#!/usr/bin/env python3
"""test_audit_log.py — 审计日志单测"""
import ast
import json
import os
import sys
import tempfile
import unittest

# 动态修改审计文件路径到临时目录
import audit_log

class TestAuditBase(unittest.TestCase):
    """审计日志测试基类：每个测试使用临时文件。"""
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.audit_file = os.path.join(self.tmpdir, "audit.jsonl")
        self._orig = audit_log.AUDIT_FILE
        audit_log.AUDIT_FILE = self.audit_file

    def tearDown(self):
        audit_log.AUDIT_FILE = self._orig
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)


class TestAuditWrite(TestAuditBase):
    def test_single_write(self):
        """写入单条记录"""
        audit_log.audit("test", "set", "health", "ok")
        self.assertTrue(os.path.exists(self.audit_file))
        with open(self.audit_file) as f:
            lines = f.readlines()
        self.assertEqual(len(lines), 1)
        entry = json.loads(lines[0])
        self.assertEqual(entry["actor"], "test")
        self.assertEqual(entry["action"], "set")
        self.assertEqual(entry["target"], "health")

    def test_multiple_writes(self):
        """多次写入追加"""
        for i in range(5):
            audit_log.audit("test", "action", f"target_{i}")
        with open(self.audit_file) as f:
            lines = [l for l in f.readlines() if l.strip()]
        self.assertEqual(len(lines), 5)

    def test_has_timestamp(self):
        """记录包含时间戳"""
        audit_log.audit("test", "set", "x")
        with open(self.audit_file) as f:
            entry = json.loads(f.readline())
        self.assertIn("ts", entry)
        self.assertRegex(entry["ts"], r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")

    def test_summary_truncated(self):
        """摘要超长时截断到500字"""
        long_summary = "x" * 1000
        audit_log.audit("test", "set", "x", long_summary)
        with open(self.audit_file) as f:
            entry = json.loads(f.readline())
        self.assertLessEqual(len(entry["summary"]), 500)

    def test_first_entry_prev_is_zeros(self):
        """首条记录的 prev 为全零"""
        audit_log.audit("test", "set", "x")
        with open(self.audit_file) as f:
            entry = json.loads(f.readline())
        self.assertEqual(entry["prev"], "0" * 16)


class TestChainHash(TestAuditBase):
    def test_chain_links(self):
        """链式哈希：后一条的 prev = 前一条的 hash"""
        audit_log.audit("a", "set", "x")
        audit_log.audit("b", "set", "y")
        with open(self.audit_file) as f:
            lines = f.readlines()
        e1 = json.loads(lines[0])
        e2 = json.loads(lines[1])
        self.assertEqual(e2["prev"], e1["hash"])

    def test_hash_deterministic(self):
        """相同内容产生相同哈希"""
        h1 = audit_log._compute_hash("test string")
        h2 = audit_log._compute_hash("test string")
        self.assertEqual(h1, h2)

    def test_hash_length_16(self):
        """哈希截断为16字符"""
        h = audit_log._compute_hash("anything")
        self.assertEqual(len(h), 16)


class TestVerify(TestAuditBase):
    def test_empty_file_ok(self):
        """空文件验证通过"""
        result = audit_log.verify_chain()
        self.assertTrue(result["ok"])
        self.assertEqual(result["total"], 0)

    def test_valid_chain_ok(self):
        """正常链验证通过"""
        for i in range(10):
            audit_log.audit("test", "action", f"t{i}")
        result = audit_log.verify_chain()
        self.assertTrue(result["ok"])
        self.assertEqual(result["total"], 10)

    def test_tampered_entry_detected(self):
        """篡改记录被检测到"""
        audit_log.audit("test", "set", "a")
        audit_log.audit("test", "set", "b")
        audit_log.audit("test", "set", "c")
        # 篡改第2条
        with open(self.audit_file) as f:
            lines = f.readlines()
        entry = json.loads(lines[1])
        entry["summary"] = "TAMPERED"
        lines[1] = json.dumps(entry, ensure_ascii=False) + "\n"
        with open(self.audit_file, "w") as f:
            f.writelines(lines)
        result = audit_log.verify_chain()
        self.assertFalse(result["ok"])
        self.assertGreater(len(result["errors"]), 0)

    def test_deleted_entry_detected(self):
        """删除中间记录被检测到"""
        audit_log.audit("test", "set", "a")
        audit_log.audit("test", "set", "b")
        audit_log.audit("test", "set", "c")
        # 删除第2条
        with open(self.audit_file) as f:
            lines = f.readlines()
        del lines[1]
        with open(self.audit_file, "w") as f:
            f.writelines(lines)
        result = audit_log.verify_chain()
        self.assertFalse(result["ok"])

    def test_json_parse_error_does_not_cascade(self):
        """V37.7: JSON parse error on one line must not cascade the prev_hash
        pointer to the next line. Before V37.7, prev_hash was left untouched
        on parse error → subsequent valid entries saw stale pointer → all
        downstream entries falsely flagged as prev-mismatch errors.
        """
        audit_log.audit("test", "set", "a")
        audit_log.audit("test", "set", "b")
        audit_log.audit("test", "set", "c")
        # Corrupt line 2 to invalid JSON
        with open(self.audit_file) as f:
            lines = f.readlines()
        lines[1] = "{not valid json at all]\n"
        with open(self.audit_file, "w") as f:
            f.writelines(lines)
        result = audit_log.verify_chain()
        self.assertFalse(result["ok"])
        # Exactly 1 error (the parse error itself) — not 2+ from cascaded
        # prev-mismatch reports on lines 3+
        parse_errors = [e for e in result["errors"]
                        if "parse" in str(e.get("actual", "")).lower()
                        or "parse" in str(e.get("expected", "")).lower()]
        self.assertGreaterEqual(len(parse_errors), 1)
        # Line 3 (valid JSON with valid self-hash) must not itself trigger
        # a prev-mismatch error
        line_3_errors = [e for e in result["errors"] if e.get("line") == 3]
        # Allowed: zero line-3 errors because we skip prev check when prev_hash is None
        self.assertEqual(
            len([e for e in line_3_errors if "prev" in str(e.get("expected", ""))]),
            0,
            "line 3 must not be flagged for prev mismatch after line 2 parse error"
        )


class TestTail(TestAuditBase):
    def test_tail_empty(self):
        """空文件返回空列表"""
        self.assertEqual(audit_log.tail(), [])

    def test_tail_returns_latest(self):
        """tail 返回最新 N 条"""
        for i in range(30):
            audit_log.audit("test", "action", f"t{i}")
        result = audit_log.tail(5)
        self.assertEqual(len(result), 5)
        self.assertEqual(result[-1]["target"], "t29")


class TestStats(TestAuditBase):
    def test_stats_empty(self):
        """空文件统计"""
        s = audit_log.stats()
        self.assertEqual(s["total"], 0)

    def test_stats_counts(self):
        """统计计数正确"""
        audit_log.audit("alice", "set", "x")
        audit_log.audit("bob", "set", "y")
        audit_log.audit("alice", "add", "z")
        s = audit_log.stats()
        self.assertEqual(s["total"], 3)
        self.assertEqual(s["actors"]["alice"], 2)
        self.assertEqual(s["actors"]["bob"], 1)


class TestSyntax(unittest.TestCase):
    def test_python_syntax(self):
        """audit_log.py Python 语法正确"""
        with open("audit_log.py") as f:
            ast.parse(f.read())

    def test_cli_interface(self):
        """CLI 支持 --verify / --tail / --stats"""
        with open("audit_log.py") as f:
            content = f.read()
        self.assertIn("--verify", content)
        self.assertIn("--tail", content)
        self.assertIn("--stats", content)


class TestIntegrationWithStatusUpdate(TestAuditBase):
    def test_status_update_import(self):
        """status_update.py 引用了 audit_log"""
        with open("status_update.py") as f:
            content = f.read()
        self.assertIn("audit_log", content)
        self.assertIn("audit", content)


class TestSnapshot(TestAuditBase):
    """V37.9.10 snapshot() 原子导出单测。"""

    def test_snapshot_without_audit_file_returns_error(self):
        """审计文件不存在时应返回 ok=False 而非抛异常。"""
        dest = os.path.join(self.tmpdir, "snap.jsonl")
        result = audit_log.snapshot(dest)
        self.assertFalse(result["ok"])
        self.assertIn("error", result)
        self.assertFalse(os.path.exists(dest))

    def test_snapshot_creates_exact_copy(self):
        """snapshot 必须产生字节级一致的副本。"""
        audit_log.audit("test", "set", "health", "ok")
        audit_log.audit("test", "add", "priority", "new task")
        audit_log.audit("test", "delete", "priority", "done task")

        dest = os.path.join(self.tmpdir, "snap.jsonl")
        result = audit_log.snapshot(dest)

        self.assertTrue(result["ok"])
        self.assertEqual(result["lines"], 3)
        self.assertTrue(os.path.exists(dest))

        with open(self.audit_file, "rb") as f1, open(dest, "rb") as f2:
            self.assertEqual(f1.read(), f2.read(), "snapshot 不是字节级一致副本")

    def test_snapshot_uses_tmp_plus_replace_atomic_pattern(self):
        """确认 snapshot 实现不留下 .tmp 残余文件（atomic pattern 契约）。"""
        audit_log.audit("test", "set", "x", "y")
        dest = os.path.join(self.tmpdir, "snap.jsonl")
        audit_log.snapshot(dest)
        # .tmp 不应存在（os.replace 应该消费它）
        self.assertFalse(os.path.exists(dest + ".tmp"),
                         "snapshot 应使用 tmp+os.replace 且不留 .tmp 残余")

    def test_snapshot_creates_missing_dest_dir(self):
        """目标目录不存在应自动创建。"""
        audit_log.audit("test", "set", "x", "y")
        dest = os.path.join(self.tmpdir, "nested", "deep", "snap.jsonl")
        result = audit_log.snapshot(dest)
        self.assertTrue(result["ok"])
        self.assertTrue(os.path.exists(dest))

    def test_snapshot_overwrites_existing_dest(self):
        """目标文件已存在时应原子覆盖（os.replace 语义）。"""
        audit_log.audit("test", "set", "x", "y")
        dest = os.path.join(self.tmpdir, "snap.jsonl")
        # 先写一个不相关的占位
        with open(dest, "w") as f:
            f.write("PLACEHOLDER\n")
        result = audit_log.snapshot(dest)
        self.assertTrue(result["ok"])
        with open(dest) as f:
            content = f.read()
        self.assertNotIn("PLACEHOLDER", content)
        self.assertIn("test", content)

    def test_snapshot_source_uses_os_replace(self):
        """源码级守卫：snapshot 实现必须含 os.replace（原子契约不得被删除）。"""
        with open(os.path.abspath(audit_log.__file__)) as f:
            content = f.read()
        self.assertIn("os.replace(", content,
                      "audit_log.py 必须含 os.replace 调用（V37.9.10 原子写入契约）")
        self.assertIn("def snapshot(", content)

    def test_audit_write_calls_fsync(self):
        """源码级守卫：audit() 写入后必须 flush + fsync（持久化契约）。"""
        with open(os.path.abspath(audit_log.__file__)) as f:
            content = f.read()
        self.assertIn("os.fsync(", content,
                      "audit() 必须含 os.fsync（V37.9.10 持久化加固）")


if __name__ == "__main__":
    unittest.main()
