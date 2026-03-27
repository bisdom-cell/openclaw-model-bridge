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


class TestFormatDetection(unittest.TestCase):
    """格式检测测试"""

    def test_detect_csv(self):
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            f.write(b"a,b\n1,2\n")
            self.addCleanup(os.unlink, f.name)
            self.assertEqual(dc.detect_format(f.name), "csv")

    def test_detect_tsv(self):
        with tempfile.NamedTemporaryFile(suffix=".tsv", delete=False) as f:
            f.write(b"a\tb\n1\t2\n")
            self.addCleanup(os.unlink, f.name)
            self.assertEqual(dc.detect_format(f.name), "tsv")

    def test_detect_json(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
            json.dump([{"a": 1}], f)
            self.addCleanup(os.unlink, f.name)
            self.assertEqual(dc.detect_format(f.name), "json")

    def test_detect_jsonl(self):
        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False, mode="w") as f:
            f.write('{"a":1}\n{"a":2}\n')
            self.addCleanup(os.unlink, f.name)
            self.assertEqual(dc.detect_format(f.name), "jsonl")

    def test_detect_xlsx(self):
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as f:
            self.addCleanup(os.unlink, f.name)
            self.assertEqual(dc.detect_format(f.name), "xlsx")

    def test_detect_tab_as_tsv(self):
        with tempfile.NamedTemporaryFile(suffix=".tab", delete=False) as f:
            f.write(b"a\tb\n")
            self.addCleanup(os.unlink, f.name)
            self.assertEqual(dc.detect_format(f.name), "tsv")


class TestStringify(unittest.TestCase):
    """值转字符串测试"""

    def test_none(self):
        self.assertEqual(dc._stringify(None), "")

    def test_bool(self):
        self.assertEqual(dc._stringify(True), "true")
        self.assertEqual(dc._stringify(False), "false")

    def test_int(self):
        self.assertEqual(dc._stringify(42), "42")

    def test_float(self):
        self.assertEqual(dc._stringify(3.14), "3.14")

    def test_dict(self):
        result = dc._stringify({"key": "val"})
        self.assertIn("key", result)

    def test_list(self):
        result = dc._stringify([1, 2, 3])
        self.assertIn("1", result)

    def test_string_passthrough(self):
        self.assertEqual(dc._stringify("hello"), "hello")


class TestTSV(unittest.TestCase):
    """TSV 读写测试"""

    def test_read_tsv(self):
        with tempfile.NamedTemporaryFile(suffix=".tsv", delete=False, mode="w") as f:
            f.write("id\tname\tdate\n1\tAlice\t2026-01-01\n2\tBob\t2026-02-01\n")
            self.addCleanup(os.unlink, f.name)

        headers, rows = dc.read_data(f.name)
        self.assertEqual(headers, ["id", "name", "date"])
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["name"], "Alice")

    def test_write_tsv(self):
        path = tempfile.mktemp(suffix=".tsv")
        self.addCleanup(lambda: os.unlink(path) if os.path.exists(path) else None)

        dc.write_data(path, ["a", "b"], [{"a": "1", "b": "2"}], "tsv")
        headers, rows = dc.read_data(path)
        self.assertEqual(headers, ["a", "b"])
        self.assertEqual(rows[0]["a"], "1")

    def test_tsv_roundtrip(self):
        path = tempfile.mktemp(suffix=".tsv")
        self.addCleanup(lambda: os.unlink(path) if os.path.exists(path) else None)

        original = [{"id": "1", "name": "Alice"}, {"id": "2", "name": "Bob"}]
        dc.write_data(path, ["id", "name"], original, "tsv")
        headers, rows = dc.read_data(path)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1]["name"], "Bob")


class TestJSON(unittest.TestCase):
    """JSON 读写测试"""

    def test_read_json_array(self):
        path = tempfile.mktemp(suffix=".json")
        self.addCleanup(lambda: os.unlink(path) if os.path.exists(path) else None)
        with open(path, "w") as f:
            json.dump([{"id": 1, "name": "Alice"}, {"id": 2, "name": "Bob"}], f)

        headers, rows = dc.read_data(path)
        self.assertEqual(headers, ["id", "name"])
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["id"], "1")  # 转为字符串

    def test_read_json_wrapped(self):
        """支持 {"data": [...]} 格式"""
        path = tempfile.mktemp(suffix=".json")
        self.addCleanup(lambda: os.unlink(path) if os.path.exists(path) else None)
        with open(path, "w") as f:
            json.dump({"data": [{"id": 1}, {"id": 2}]}, f)

        headers, rows = dc.read_data(path)
        self.assertEqual(len(rows), 2)

    def test_read_json_records_key(self):
        """支持 {"records": [...]} 格式"""
        path = tempfile.mktemp(suffix=".json")
        self.addCleanup(lambda: os.unlink(path) if os.path.exists(path) else None)
        with open(path, "w") as f:
            json.dump({"records": [{"a": 1}]}, f)

        headers, rows = dc.read_data(path)
        self.assertEqual(len(rows), 1)

    def test_read_json_single_object(self):
        """单个对象当作一行"""
        path = tempfile.mktemp(suffix=".json")
        self.addCleanup(lambda: os.unlink(path) if os.path.exists(path) else None)
        with open(path, "w") as f:
            json.dump({"id": 1, "name": "Alice"}, f)

        headers, rows = dc.read_data(path)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["name"], "Alice")

    def test_json_null_to_empty(self):
        """JSON null → 空字符串"""
        path = tempfile.mktemp(suffix=".json")
        self.addCleanup(lambda: os.unlink(path) if os.path.exists(path) else None)
        with open(path, "w") as f:
            json.dump([{"id": 1, "name": None}], f)

        _, rows = dc.read_data(path)
        self.assertEqual(rows[0]["name"], "")

    def test_json_bool_to_string(self):
        """JSON bool → "true"/"false" """
        path = tempfile.mktemp(suffix=".json")
        self.addCleanup(lambda: os.unlink(path) if os.path.exists(path) else None)
        with open(path, "w") as f:
            json.dump([{"active": True, "deleted": False}], f)

        _, rows = dc.read_data(path)
        self.assertEqual(rows[0]["active"], "true")
        self.assertEqual(rows[0]["deleted"], "false")

    def test_json_heterogeneous_keys(self):
        """不同行有不同 key"""
        path = tempfile.mktemp(suffix=".json")
        self.addCleanup(lambda: os.unlink(path) if os.path.exists(path) else None)
        with open(path, "w") as f:
            json.dump([{"a": 1}, {"a": 2, "b": 3}], f)

        headers, rows = dc.read_data(path)
        self.assertIn("b", headers)
        self.assertEqual(rows[0]["b"], "")  # 第一行缺少 b

    def test_write_json(self):
        path = tempfile.mktemp(suffix=".json")
        self.addCleanup(lambda: os.unlink(path) if os.path.exists(path) else None)

        dc.write_data(path, ["id", "name"], [{"id": "1", "name": "Alice"}], "json")
        with open(path) as f:
            data = json.load(f)
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["name"], "Alice")


class TestJSONL(unittest.TestCase):
    """JSONL 读写测试"""

    def test_read_jsonl(self):
        path = tempfile.mktemp(suffix=".jsonl")
        self.addCleanup(lambda: os.unlink(path) if os.path.exists(path) else None)
        with open(path, "w") as f:
            f.write('{"id":1,"name":"Alice"}\n{"id":2,"name":"Bob"}\n')

        headers, rows = dc.read_data(path)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1]["name"], "Bob")

    def test_read_jsonl_with_blank_lines(self):
        path = tempfile.mktemp(suffix=".jsonl")
        self.addCleanup(lambda: os.unlink(path) if os.path.exists(path) else None)
        with open(path, "w") as f:
            f.write('{"a":1}\n\n{"a":2}\n\n')

        _, rows = dc.read_data(path)
        self.assertEqual(len(rows), 2)

    def test_read_jsonl_with_bad_lines(self):
        """跳过无法解析的行"""
        path = tempfile.mktemp(suffix=".jsonl")
        self.addCleanup(lambda: os.unlink(path) if os.path.exists(path) else None)
        with open(path, "w") as f:
            f.write('{"a":1}\nINVALID\n{"a":2}\n')

        _, rows = dc.read_data(path)
        self.assertEqual(len(rows), 2)

    def test_write_jsonl(self):
        path = tempfile.mktemp(suffix=".jsonl")
        self.addCleanup(lambda: os.unlink(path) if os.path.exists(path) else None)

        dc.write_data(path, ["x"], [{"x": "1"}, {"x": "2"}], "jsonl")
        _, rows = dc.read_data(path)
        self.assertEqual(len(rows), 2)


class TestExcel(unittest.TestCase):
    """Excel (.xlsx) 读写测试"""

    @classmethod
    def setUpClass(cls):
        try:
            import openpyxl
            cls.has_openpyxl = True
        except ImportError:
            cls.has_openpyxl = False

    def _create_xlsx(self, path, headers, data_rows):
        import openpyxl
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(headers)
        for row in data_rows:
            ws.append(row)
        wb.save(path)
        wb.close()

    def test_read_xlsx(self):
        if not self.has_openpyxl:
            self.skipTest("openpyxl not installed")
        path = tempfile.mktemp(suffix=".xlsx")
        self.addCleanup(lambda: os.unlink(path) if os.path.exists(path) else None)
        self._create_xlsx(path, ["id", "name"], [[1, "Alice"], [2, "Bob"]])

        headers, rows = dc.read_data(path)
        self.assertEqual(headers, ["id", "name"])
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["id"], "1")
        self.assertEqual(rows[1]["name"], "Bob")

    def test_write_xlsx(self):
        if not self.has_openpyxl:
            self.skipTest("openpyxl not installed")
        path = tempfile.mktemp(suffix=".xlsx")
        self.addCleanup(lambda: os.unlink(path) if os.path.exists(path) else None)

        dc.write_data(path, ["a", "b"], [{"a": "1", "b": "2"}], "xlsx")
        headers, rows = dc.read_data(path)
        self.assertEqual(rows[0]["a"], "1")

    def test_xlsx_with_none(self):
        """Excel 空单元格 → 空字符串"""
        if not self.has_openpyxl:
            self.skipTest("openpyxl not installed")
        path = tempfile.mktemp(suffix=".xlsx")
        self.addCleanup(lambda: os.unlink(path) if os.path.exists(path) else None)
        self._create_xlsx(path, ["id", "name"], [[1, None], [2, "Bob"]])

        _, rows = dc.read_data(path)
        self.assertEqual(rows[0]["name"], "")

    def test_xlsx_roundtrip(self):
        if not self.has_openpyxl:
            self.skipTest("openpyxl not installed")
        path = tempfile.mktemp(suffix=".xlsx")
        self.addCleanup(lambda: os.unlink(path) if os.path.exists(path) else None)

        original = [{"id": "1", "name": "Alice"}, {"id": "2", "name": "Bob"}]
        dc.write_data(path, ["id", "name"], original, "xlsx")
        _, rows = dc.read_data(path)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["name"], "Alice")


class TestMultiFormatEndToEnd(unittest.TestCase):
    """多格式端到端测试"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.orig_workspace = dc.WORKSPACE
        self.orig_version_dir = dc.VERSION_DIR
        self.orig_log_file = dc.LOG_FILE
        dc.WORKSPACE = self.tmpdir
        dc.VERSION_DIR = os.path.join(self.tmpdir, "versions")
        dc.LOG_FILE = os.path.join(self.tmpdir, "audit.jsonl")
        os.makedirs(dc.VERSION_DIR, exist_ok=True)

    def tearDown(self):
        dc.WORKSPACE = self.orig_workspace
        dc.VERSION_DIR = self.orig_version_dir
        dc.LOG_FILE = self.orig_log_file
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_tsv_execute(self):
        """TSV 清洗：输出也是 .tsv"""
        path = os.path.join(self.tmpdir, "data.tsv")
        with open(path, "w") as f:
            f.write("id\tname\n1\t  Alice \n2\tBob\n1\t  Alice \n")

        rc = dc.cmd_execute(path, ["trim", "dedup"], {})
        self.assertEqual(rc, 0)
        output = os.path.join(self.tmpdir, "data_cleaned.tsv")
        self.assertTrue(os.path.exists(output))
        _, rows = dc.read_data(output)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["name"], "Alice")

    def test_json_execute(self):
        """JSON 清洗：输出也是 .json"""
        path = os.path.join(self.tmpdir, "data.json")
        with open(path, "w") as f:
            json.dump([
                {"id": 1, "name": "  Alice "},
                {"id": 2, "name": "Bob"},
                {"id": 1, "name": "  Alice "},
            ], f)

        rc = dc.cmd_execute(path, ["trim", "dedup"], {})
        self.assertEqual(rc, 0)
        output = os.path.join(self.tmpdir, "data_cleaned.json")
        self.assertTrue(os.path.exists(output))
        _, rows = dc.read_data(output)
        self.assertEqual(len(rows), 2)

    def test_jsonl_execute(self):
        """JSONL 清洗：输出也是 .jsonl"""
        path = os.path.join(self.tmpdir, "data.jsonl")
        with open(path, "w") as f:
            f.write('{"id":"1","name":"Active"}\n')
            f.write('{"id":"2","name":"active"}\n')

        rc = dc.cmd_execute(path, ["fix_case"], {"fix_case": ["name"]})
        self.assertEqual(rc, 0)
        output = os.path.join(self.tmpdir, "data_cleaned.jsonl")
        self.assertTrue(os.path.exists(output))
        _, rows = dc.read_data(output)
        self.assertEqual(rows[0]["name"], "active")
        self.assertEqual(rows[1]["name"], "active")

    def test_xlsx_execute(self):
        """Excel 清洗：输出也是 .xlsx"""
        try:
            import openpyxl
        except ImportError:
            self.skipTest("openpyxl not installed")

        path = os.path.join(self.tmpdir, "data.xlsx")
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["id", "name", "date"])
        ws.append([1, "  Alice ", "2026-01-01"])
        ws.append([2, "Bob", "15/03/2026"])
        ws.append([1, "  Alice ", "2026-01-01"])
        wb.save(path)
        wb.close()

        rc = dc.cmd_execute(path, ["trim", "dedup", "fix_dates"], {})
        self.assertEqual(rc, 0)
        output = os.path.join(self.tmpdir, "data_cleaned.xlsx")
        self.assertTrue(os.path.exists(output))
        _, rows = dc.read_data(output)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1]["date"], "2026-03-15")


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
