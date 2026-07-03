#!/usr/bin/env python3
"""test_v37_9_237_atomic_tmp.py — 审计 finding C 安全半边守卫

背景（V37.9.237）：status.json / ~/.kb/index.json 的原子写此前用**固定** tmp 名
（`TARGET + ".tmp"`）。两个并发写者（~40 cron 进程 / kb_dedup vs kb_autotag）写
同一个固定 tmp 路径 → 字节交错 → os.replace 发布损坏文件 → 读者 JSONDecodeError →
fallback DEFAULT_STATUS / {"entries": []}（系统失忆 / KB 索引失忆）。

修复：tmp 名带 pid 后缀（`"{}.tmp.{}".format(TARGET, os.getpid())`）。并发写者（不同
进程 = 不同 pid）写各自 tmp 路径，绝不共用 → 消除交错损坏。os.replace 仍保证 atomic
publish，并发下最坏是 last-writer-wins（良性丢更新）。

**范围诚实**：本修复只做 finding C 的**损坏安全半边**，不做 lost-update 跨写者锁
（需谨慎锁设计防死锁，保持登记）。故本测试断言"无损坏 / valid JSON"，绝不断言
"并发写全部保留"（那是未实现的 lost-update 安全）。
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

REPO = os.path.dirname(os.path.abspath(__file__))


def _load(modname, path):
    spec = importlib.util.spec_from_file_location(modname, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# ---------------------------------------------------------------------------
# status.json — 最关键共享状态（三方意识锚点，~40 并发写者，损坏=系统失忆）
# ---------------------------------------------------------------------------
class TestStatusTmpUniqueness(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.status = os.path.join(self.tmpdir, "status.json")
        self.su = _load("status_update", os.path.join(REPO, "status_update.py"))
        self.su.STATUS_FILE = self.status

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _capture_tmp_path(self, pid):
        """在给定 pid 下调用 save_status，返回它写入并发布的 tmp 路径。"""
        captured = {}
        real_replace = os.replace

        def spy_replace(src, dst):
            captured["src"] = src
            return real_replace(src, dst)

        with mock.patch("os.getpid", return_value=pid), \
             mock.patch("os.replace", side_effect=spy_replace):
            self.su.save_status({"priorities": [pid]}, updated_by="test")
        return captured["src"]

    def test_tmp_path_contains_pid(self):
        p = self._capture_tmp_path(4242)
        self.assertTrue(p.endswith(".tmp.4242"), p)
        # 发布后 tmp 已被 os.replace 重命名，不残留
        self.assertFalse(os.path.exists(p))

    def test_two_pids_distinct_tmp_paths(self):
        """核心属性：两个并发写者（不同 pid）用不同 tmp 路径 → 不可能交错损坏。"""
        p1 = self._capture_tmp_path(111)
        p2 = self._capture_tmp_path(222)
        self.assertNotEqual(p1, p2)
        self.assertTrue(p1.endswith(".tmp.111"))
        self.assertTrue(p2.endswith(".tmp.222"))

    def test_published_file_valid_json(self):
        self.su.save_status({"priorities": ["x"]}, updated_by="test")
        with open(self.status) as f:
            data = json.load(f)  # 绝不 JSONDecodeError
        self.assertEqual(data["priorities"], ["x"])
        self.assertEqual(data["updated_by"], "test")

    def test_write_exception_cleans_up_tmp(self):
        """写入异常 → finally 清理 tmp，不留 orphan（唯一 tmp 的 orphan 防累积）。"""
        with mock.patch.object(self.su.json, "dump", side_effect=ValueError("boom")):
            with self.assertRaises(ValueError):
                self.su.save_status({"priorities": []}, updated_by="test")
        leftovers = [f for f in os.listdir(self.tmpdir) if ".tmp." in f]
        self.assertEqual(leftovers, [], "写入异常后未清理 tmp orphan: %s" % leftovers)

    def test_real_subprocess_concurrency_no_corruption(self):
        """真跨进程并发 sanity：N 个 status_update.py 子进程并发 --add，
        最终文件必须始终 valid JSON（never JSONDecodeError）。

        诚实：这证明"无损坏 + 无 crash"，不证明"所有 insert 保留"（last-writer-wins
        丢更新是登记未修的 lost-update 半边）。故只断言 valid JSON + 是 dict，不断言 count。
        """
        home = os.path.join(self.tmpdir, "home")
        kb = os.path.join(home, ".kb")
        os.makedirs(kb, exist_ok=True)
        # 预置一个合法 status.json，让子进程解析到 ~/.kb/status.json
        with open(os.path.join(kb, "status.json"), "w") as f:
            json.dump({"recent_changes": []}, f)
        env = os.environ.copy()
        env["HOME"] = home
        procs = []
        for i in range(8):
            item = json.dumps({"date": "2026-07-03", "what": "c%d" % i, "by": "test"})
            procs.append(subprocess.Popen(
                [sys.executable, os.path.join(REPO, "status_update.py"),
                 "--add", "recent_changes", item, "--by", "test"],
                env=env, cwd=REPO,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        for p in procs:
            p.wait()
        # 最终文件必须 valid JSON（并发写从未损坏它）
        with open(os.path.join(kb, "status.json")) as f:
            data = json.load(f)  # 不抛 JSONDecodeError = 无损坏
        self.assertIsInstance(data, dict)
        self.assertIsInstance(data.get("recent_changes"), list)
        # 无 tmp orphan 累积（子进程正常退出）
        orphans = [f for f in os.listdir(kb) if f.startswith("status.json.tmp.")]
        self.assertEqual(orphans, [], "子进程留下 tmp orphan: %s" % orphans)


# ---------------------------------------------------------------------------
# ~/.kb/index.json — kb_dedup / kb_autotag 无锁写者（finding C "kb_write vs kb_dedup"）
# ---------------------------------------------------------------------------
class TestKbIndexTmpUniqueness(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.index = os.path.join(self.tmpdir, "index.json")
        self.dedup = _load("kb_dedup", os.path.join(REPO, "kb_dedup.py"))
        self.dedup.INDEX_FILE = self.index

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _capture_tmp(self, pid):
        captured = {}
        real_replace = os.replace

        def spy_replace(src, dst):
            captured["src"] = src
            return real_replace(src, dst)

        with mock.patch("os.getpid", return_value=pid), \
             mock.patch("os.replace", side_effect=spy_replace):
            self.dedup.save_index({"entries": [pid]})
        return captured["src"]

    def test_index_tmp_contains_pid(self):
        p = self._capture_tmp(7777)
        self.assertTrue(p.endswith(".tmp.7777"), p)

    def test_index_two_pids_distinct(self):
        self.assertNotEqual(self._capture_tmp(1), self._capture_tmp(2))

    def test_index_valid_json_after_write(self):
        self.dedup.save_index({"entries": [{"k": "v"}]})
        with open(self.index) as f:
            data = json.load(f)
        self.assertEqual(data["entries"], [{"k": "v"}])


# ---------------------------------------------------------------------------
# 源码守卫（反向验证：回退到固定 tmp 名 → 守卫立即 fail）
# ---------------------------------------------------------------------------
class TestSourceGuards(unittest.TestCase):
    def _read(self, name):
        with open(os.path.join(REPO, name)) as f:
            return f.read()

    def test_status_update_pid_tmp(self):
        c = self._read("status_update.py")
        self.assertIn('"{}.tmp.{}".format(STATUS_FILE, os.getpid())', c)
        self.assertNotIn('tmp = STATUS_FILE + ".tmp"', c)  # 固定名反模式已退役

    def test_kb_dedup_pid_tmp(self):
        c = self._read("kb_dedup.py")
        self.assertIn('"{}.tmp.{}".format(INDEX_FILE, os.getpid())', c)
        self.assertNotIn('tmp = INDEX_FILE + ".tmp"', c)

    def test_kb_autotag_pid_tmp(self):
        c = self._read("kb_autotag.py")
        self.assertIn('"{}.tmp.{}".format(INDEX_FILE, os.getpid())', c)
        self.assertNotIn('tmp = INDEX_FILE + ".tmp"', c)

    def test_finally_cleanup_present(self):
        # 三处都必须有 finally 清理未发布 tmp（防 orphan 累积）
        for name in ("status_update.py", "kb_dedup.py", "kb_autotag.py"):
            c = self._read(name)
            self.assertIn("os.remove(tmp)", c, "%s 缺 finally tmp 清理" % name)

    def test_honest_docstring_not_fail_plausible(self):
        # docstring 不得再声称"支持并发安全"（fail-plausible）；须诚实标注 non-lost-update
        c = self._read("status_update.py")
        self.assertNotIn("支持并发安全", c)
        self.assertIn("last-writer-wins", c)

    def test_v37_9_237_marker(self):
        for name in ("status_update.py", "kb_dedup.py", "kb_autotag.py"):
            self.assertIn("V37.9.237", self._read(name))


if __name__ == "__main__":
    unittest.main(verbosity=2)
