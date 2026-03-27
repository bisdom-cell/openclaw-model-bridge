#!/usr/bin/env python3
"""
test_data_clean.py — data_clean.py 单元测试
覆盖: profile / execute / validate / history / 各操作 / 边界条件
"""

import csv
import json
import os
import shutil
import sys
import tempfile
import unittest

# 确保能 import
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import data_clean as dc


class TestHelpers(unittest.TestCase):
    """工具函数测试"""

    def test_infer_date_format_iso(self):
        self.assertEqual(dc.infer_date_format("2026-03-15"), "YYYY-MM-DD")

    def test_infer_date_format_slash(self):
        self.assertEqual(dc.infer_date_format("2026/03/15"), "YYYY/MM/DD")

    def test_infer_date_format_dmy(self):
        self.assertEqual(dc.infer_date_format("15/03/2026"), "DD/MM/YYYY")

    def test_infer_date_format_invalid(self):
        self.assertIsNone(dc.infer_date_format("not-a-date"))

    def test_infer_date_format_placeholder(self):
        self.assertIsNone(dc.infer_date_format("TBD"))

    def test_infer_date_format_na(self):
        self.assertIsNone(dc.infer_date_format("N/A"))

    def test_detect_column_type_numeric(self):
        self.assertEqual(dc.detect_column_type(["1", "2.5", "3", "4.0", "5"]), "numeric")

    def test_detect_column_type_datetime(self):
        self.assertEqual(dc.detect_column_type(["2026-01-01", "2026-02-01", "2026-03-01"]), "datetime")

    def test_detect_column_type_text(self):
        self.assertEqual(dc.detect_column_type(["hello", "world", "foo"]), "text")

    def test_detect_column_type_empty(self):
        self.assertEqual(dc.detect_column_type(["", "", "N/A"]), "empty")

    def test_file_hash_deterministic(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            f.write("a,b\n1,2\n")
            path = f.name
        try:
            h1 = dc.file_hash(path)
            h2 = dc.file_hash(path)
            self.assertEqual(h1, h2)
            self.assertEqual(len(h1), 12)
        finally:
            os.unlink(path)


class TestProfileColumn(unittest.TestCase):
    """列画像测试"""

    def test_missing_detection(self):
        result = dc.profile_column("col", ["a", "", "N/A", "b", "null"])
        issues = [i for i in result["issues"] if i["type"] == "missing_values"]
        self.assertEqual(len(issues), 1)
        self.assertEqual(result["missing"], 3)

    def test_case_inconsistency(self):
        result = dc.profile_column("status", ["Active", "active", "ACTIVE", "inactive"])
        issues = [i for i in result["issues"] if i["type"] == "case_inconsistency"]
        self.assertEqual(len(issues), 1)

    def test_whitespace_detection(self):
        result = dc.profile_column("name", ["  hello ", "world", "foo"])
        issues = [i for i in result["issues"] if i["type"] == "whitespace"]
        self.assertEqual(len(issues), 1)

    def test_mixed_date_formats(self):
        result = dc.profile_column("date", ["2026-01-01", "15/03/2026", "2026/04/01"])
        issues = [i for i in result["issues"] if i["type"] == "mixed_date_formats"]
        self.assertEqual(len(issues), 1)

    def test_negative_values(self):
        result = dc.profile_column("amount", ["100", "-50", "200", "300"])
        issues = [i for i in result["issues"] if i["type"] == "negative_values"]
        self.assertEqual(len(issues), 1)

    def test_non_numeric_in_numeric_col(self):
        result = dc.profile_column("stock", ["100", "200", "thirty", "400", "500"])
        issues = [i for i in result["issues"] if i["type"] == "non_numeric_values"]
        self.assertEqual(len(issues), 1)

    def test_clean_column_no_issues(self):
        result = dc.profile_column("id", ["1", "2", "3", "4", "5"])
        self.assertEqual(len(result["issues"]), 0)


class TestDuplicates(unittest.TestCase):
    """重复检测测试"""

    def test_exact_duplicate(self):
        rows = [
            {"id": "1", "name": "Alice"},
            {"id": "1", "name": "Alice"},
            {"id": "2", "name": "Bob"},
        ]
        exact, near = dc.find_duplicates(rows, ["id", "name"])
        self.assertEqual(len(exact), 1)
        self.assertEqual(exact[0]["row"], 3)  # 0-indexed + header

    def test_near_duplicate(self):
        rows = [
            {"id": "1", "name": "Alice", "email": "a@b.com"},
            {"id": "2", "name": "Alice", "email": "a@b.com"},
            {"id": "3", "name": "Bob", "email": "b@b.com"},
        ]
        exact, near = dc.find_duplicates(rows, ["id", "name", "email"])
        self.assertEqual(len(exact), 0)
        self.assertEqual(len(near), 1)

    def test_no_duplicates(self):
        rows = [
            {"id": "1", "name": "Alice"},
            {"id": "2", "name": "Bob"},
        ]
        exact, near = dc.find_duplicates(rows, ["id", "name"])
        self.assertEqual(len(exact), 0)
        self.assertEqual(len(near), 0)


class TestOperations(unittest.TestCase):
    """清洗操作测试"""

    def setUp(self):
        """使用临时目录作为 workspace"""
        self.orig_workspace = dc.WORKSPACE
        self.orig_version_dir = dc.VERSION_DIR
        self.tmpdir = tempfile.mkdtemp()
        dc.WORKSPACE = self.tmpdir
        dc.VERSION_DIR = os.path.join(self.tmpdir, "versions")
        os.makedirs(dc.VERSION_DIR, exist_ok=True)

    def tearDown(self):
        dc.WORKSPACE = self.orig_workspace
        dc.VERSION_DIR = self.orig_version_dir
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_op_dedup(self):
        rows = [{"a": "1"}, {"a": "1"}, {"a": "2"}]
        result, info = dc.op_dedup(["a"], rows, [])
        self.assertEqual(len(result), 2)
        self.assertEqual(info["rows_removed"], 1)

    def test_op_dedup_no_dupes(self):
        rows = [{"a": "1"}, {"a": "2"}, {"a": "3"}]
        result, info = dc.op_dedup(["a"], rows, [])
        self.assertEqual(len(result), 3)
        self.assertEqual(info["rows_removed"], 0)

    def test_op_dedup_near(self):
        rows = [
            {"id": "1", "name": "Alice"},
            {"id": "2", "name": "Alice"},
            {"id": "3", "name": "Bob"},
        ]
        result, info = dc.op_dedup_near(["id", "name"], rows, [])
        self.assertEqual(len(result), 2)
        self.assertEqual(info["rows_removed"], 1)

    def test_op_trim(self):
        rows = [{"name": "  Alice  "}, {"name": "Bob"}]
        result, info = dc.op_trim(["name"], rows, [])
        self.assertEqual(result[0]["name"], "Alice")
        self.assertEqual(info["cells_trimmed"], 1)

    def test_op_trim_no_changes(self):
        rows = [{"name": "Alice"}, {"name": "Bob"}]
        result, info = dc.op_trim(["name"], rows, [])
        self.assertEqual(info["cells_trimmed"], 0)

    def test_op_fix_dates(self):
        rows = [
            {"date": "15/03/2026"},
            {"date": "2026-03-16"},
            {"date": "2026/03/17"},
        ]
        result, info = dc.op_fix_dates(["date"], rows, ["date"])
        self.assertEqual(result[0]["date"], "2026-03-15")
        self.assertEqual(result[1]["date"], "2026-03-16")  # already correct
        self.assertEqual(result[2]["date"], "2026-03-17")
        self.assertEqual(info["dates_fixed"], 2)

    def test_op_fix_dates_with_placeholder(self):
        rows = [{"date": "N/A"}, {"date": "2026-01-01"}]
        result, info = dc.op_fix_dates(["date"], rows, ["date"])
        self.assertEqual(result[0]["date"], "N/A")  # placeholder untouched
        self.assertEqual(info["dates_fixed"], 0)

    def test_op_fix_dates_unfixable(self):
        rows = [{"date": "not-a-date"}, {"date": "2026-01-01"}]
        result, info = dc.op_fix_dates(["date"], rows, ["date"])
        self.assertEqual(len(info["unfixable"]), 1)

    def test_op_fix_case(self):
        rows = [{"status": "Active"}, {"status": "ACTIVE"}, {"status": "inactive"}]
        result, info = dc.op_fix_case(["status"], rows, ["status"])
        self.assertEqual(result[0]["status"], "active")
        self.assertEqual(result[1]["status"], "active")
        self.assertEqual(info["cells_changed"], 2)

    def test_op_fix_case_no_target(self):
        rows = [{"status": "Active"}]
        result, info = dc.op_fix_case(["status"], rows, [])
        self.assertIn("skipped", info)

    def test_op_fill_missing(self):
        rows = [{"val": ""}, {"val": "N/A"}, {"val": "hello"}, {"val": "null"}]
        result, info = dc.op_fill_missing(["val"], rows, [])
        self.assertEqual(result[0]["val"], "[MISSING]")
        self.assertEqual(result[1]["val"], "[MISSING]")
        self.assertEqual(result[2]["val"], "hello")
        self.assertEqual(result[3]["val"], "[MISSING]")
        self.assertEqual(info["cells_marked"], 3)

    def test_op_remove_test(self):
        rows = [
            {"name": "Alice", "note": "valid"},
            {"name": "测试商品请忽略", "note": "test"},
            {"name": "Bob", "note": "ok"},
        ]
        result, info = dc.op_remove_test(["name", "note"], rows, [])
        self.assertEqual(len(result), 2)
        self.assertEqual(info["removed_rows"], [3])


class TestCSVReadWrite(unittest.TestCase):
    """CSV 读写测试"""

    def test_roundtrip(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["id", "name"])
            writer.writeheader()
            writer.writerow({"id": "1", "name": "Alice"})
            writer.writerow({"id": "2", "name": "Bob"})
            path = f.name

        try:
            headers, rows = dc.read_csv(path)
            self.assertEqual(headers, ["id", "name"])
            self.assertEqual(len(rows), 2)

            out_path = path + ".out"
            dc.write_csv(out_path, headers, rows)
            h2, r2 = dc.read_csv(out_path)
            self.assertEqual(h2, headers)
            self.assertEqual(len(r2), 2)
            os.unlink(out_path)
        finally:
            os.unlink(path)

    def test_utf8_bom(self):
        """UTF-8 BOM 文件应正常读取"""
        with tempfile.NamedTemporaryFile(mode="wb", suffix=".csv", delete=False) as f:
            f.write(b"\xef\xbb\xbfid,name\n1,\xe5\xbc\xa0\xe4\xb8\x89\n")
            path = f.name
        try:
            headers, rows = dc.read_csv(path)
            self.assertEqual(headers[0], "id")  # 不应有 BOM 前缀
            self.assertEqual(rows[0]["name"], "张三")
        finally:
            os.unlink(path)


class TestEndToEnd(unittest.TestCase):
    """端到端测试"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.orig_workspace = dc.WORKSPACE
        self.orig_version_dir = dc.VERSION_DIR
        self.orig_log_file = dc.LOG_FILE
        dc.WORKSPACE = self.tmpdir
        dc.VERSION_DIR = os.path.join(self.tmpdir, "versions")
        dc.LOG_FILE = os.path.join(self.tmpdir, "audit.jsonl")
        os.makedirs(dc.VERSION_DIR, exist_ok=True)

        # 创建测试 CSV
        self.test_csv = os.path.join(self.tmpdir, "test.csv")
        with open(self.test_csv, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["id", "name", "date", "status"])
            writer.writeheader()
            writer.writerow({"id": "1", "name": "  Alice ", "date": "2026-01-01", "status": "Active"})
            writer.writerow({"id": "2", "name": "Alice", "date": "01/02/2026", "status": "active"})
            writer.writerow({"id": "1", "name": "  Alice ", "date": "2026-01-01", "status": "Active"})  # exact dup
            writer.writerow({"id": "3", "name": "Bob", "date": "2026/03/15", "status": "INACTIVE"})
            writer.writerow({"id": "4", "name": "测试请忽略", "date": "2026-01-01", "status": "test"})

    def tearDown(self):
        dc.WORKSPACE = self.orig_workspace
        dc.VERSION_DIR = self.orig_version_dir
        dc.LOG_FILE = self.orig_log_file
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_full_pipeline(self):
        """profile → execute → validate 全流程"""
        # Profile
        headers, rows = dc.read_csv(self.test_csv)
        exact, near = dc.find_duplicates(rows, headers)
        self.assertEqual(len(exact), 1)

        # Execute
        result = dc.cmd_execute(
            self.test_csv,
            ["trim", "dedup", "fix_dates", "fix_case", "remove_test"],
            {"fix_case": ["status"]},
        )
        self.assertEqual(result, 0)

        # 检查输出文件
        output_file = os.path.join(self.tmpdir, "test_cleaned.csv")
        self.assertTrue(os.path.exists(output_file))
        _, cleaned = dc.read_csv(output_file)

        # 验证: 5行 → 去重1 → 去测试1 = 3行
        self.assertEqual(len(cleaned), 3)

        # 验证: 日期统一
        for r in cleaned:
            if r["date"] and r["date"] != "[MISSING]":
                self.assertRegex(r["date"], r"^\d{4}-\d{2}-\d{2}$")

        # 验证: 大小写统一
        for r in cleaned:
            self.assertEqual(r["status"], r["status"].lower())

        # Validate
        result = dc.cmd_validate(self.test_csv, output_file)
        self.assertEqual(result, 0)

    def test_version_chain(self):
        """版本快照链完整性"""
        dc.cmd_execute(self.test_csv, ["trim", "dedup"], {})
        versions = os.listdir(dc.VERSION_DIR)
        # v0_original + v1_trim + v2_dedup = 3
        self.assertEqual(len(versions), 3)

    def test_audit_log(self):
        """审计日志记录"""
        dc.cmd_execute(self.test_csv, ["trim"], {})
        self.assertTrue(os.path.exists(dc.LOG_FILE))
        with open(dc.LOG_FILE) as f:
            entries = [json.loads(line) for line in f if line.strip()]
        actions = [e["action"] for e in entries]
        self.assertIn("execute", actions)

    def test_empty_file(self):
        """空文件处理"""
        empty = os.path.join(self.tmpdir, "empty.csv")
        with open(empty, "w") as f:
            f.write("id,name\n")
        result = dc.cmd_execute(empty, ["dedup"], {})
        self.assertEqual(result, 1)  # 应返回错误

    def test_nonexistent_file(self):
        """不存在的文件"""
        result = dc.cmd_execute("/nonexistent.csv", ["dedup"], {})
        self.assertEqual(result, 1)


class TestSampleFiles(unittest.TestCase):
    """用 Phase 0 样本文件测试"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.orig_workspace = dc.WORKSPACE
        self.orig_version_dir = dc.VERSION_DIR
        self.orig_log_file = dc.LOG_FILE
        dc.WORKSPACE = self.tmpdir
        dc.VERSION_DIR = os.path.join(self.tmpdir, "versions")
        dc.LOG_FILE = os.path.join(self.tmpdir, "audit.jsonl")
        os.makedirs(dc.VERSION_DIR, exist_ok=True)
        self.sample_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data_clean_poc")

    def tearDown(self):
        dc.WORKSPACE = self.orig_workspace
        dc.VERSION_DIR = self.orig_version_dir
        dc.LOG_FILE = self.orig_log_file
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _sample(self, name):
        return os.path.join(self.sample_dir, name)

    def test_sample1_profile(self):
        """样本1: 订单数据画像"""
        path = self._sample("sample1_orders.csv")
        if not os.path.exists(path):
            self.skipTest("sample file not available")
        headers, rows = dc.read_csv(path)
        self.assertEqual(len(rows), 15)
        _, near = dc.find_duplicates(rows, headers)
        self.assertGreater(len(near), 0)  # 有近似重复

    def test_sample1_clean(self):
        """样本1: 清洗全流程"""
        path = self._sample("sample1_orders.csv")
        if not os.path.exists(path):
            self.skipTest("sample file not available")
        result = dc.cmd_execute(path, ["trim", "dedup_near", "fix_dates", "fix_case"],
                               {"fix_case": ["status", "email"]})
        self.assertEqual(result, 0)
        output = os.path.join(self.tmpdir, "sample1_orders_cleaned.csv")
        _, cleaned = dc.read_csv(output)
        self.assertEqual(len(cleaned), 14)  # 1 near-dup removed

    def test_sample2_profile(self):
        """样本2: 商品数据画像"""
        path = self._sample("sample2_products.csv")
        if not os.path.exists(path):
            self.skipTest("sample file not available")
        headers, rows = dc.read_csv(path)
        self.assertEqual(len(rows), 15)
        stock_col = dc.profile_column("stock", [r["stock"] for r in rows])
        has_non_numeric = any(i["type"] == "non_numeric_values" for i in stock_col["issues"])
        self.assertTrue(has_non_numeric)

    def test_sample3_profile(self):
        """样本3: 联系人数据画像"""
        path = self._sample("sample3_contacts.csv")
        if not os.path.exists(path):
            self.skipTest("sample file not available")
        headers, rows = dc.read_csv(path)
        self.assertEqual(len(rows), 15)
        vip_col = dc.profile_column("vip_level", [r["vip_level"] for r in rows])
        has_case_issue = any(i["type"] == "case_inconsistency" for i in vip_col["issues"])
        self.assertTrue(has_case_issue)


if __name__ == "__main__":
    unittest.main(verbosity=2)
