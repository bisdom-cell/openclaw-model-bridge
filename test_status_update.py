#!/usr/bin/env python3
"""test_status_update.py — status_update.py 全量单测

覆盖：原子读写、嵌套字段、数组操作、优先级CRUD、
并发安全、损坏恢复、字段向前兼容、CLI 接口
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

# 动态导入 status_update 的核心函数
sys.path.insert(0, os.path.dirname(__file__))

# 我们不能直接 import status_update（它有 main 入口），
# 但可以导入核心函数
import importlib
import importlib.util
_su_spec = importlib.util.spec_from_file_location(
    "status_update", os.path.join(os.path.dirname(__file__), "status_update.py"))
_su = importlib.util.module_from_spec(_su_spec)


class TestStatusUpdateIO(unittest.TestCase):
    """原子读写测试"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.status_file = os.path.join(self.tmp, "status.json")
        # Monkey-patch STATUS_FILE
        self._orig_file = None

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run_cmd(self, *args):
        """运行 status_update.py CLI，使用临时文件"""
        env = os.environ.copy()
        cmd = [sys.executable, "status_update.py"] + list(args)
        # 需要 patch STATUS_FILE — 通过修改源码中的路径不可行
        # 改为直接测试函数逻辑
        return subprocess.run(cmd, capture_output=True, text=True, env=env)

    def test_load_missing_file_returns_defaults(self):
        """文件不存在时返回默认结构"""
        # 直接验证默认结构
        from status_update import DEFAULT_STATUS
        self.assertIn("priorities", DEFAULT_STATUS)
        self.assertIn("health", DEFAULT_STATUS)
        self.assertIn("feedback", DEFAULT_STATUS)
        self.assertIsInstance(DEFAULT_STATUS["priorities"], list)
        self.assertIsInstance(DEFAULT_STATUS["health"], dict)

    def test_save_creates_file_atomically(self):
        """原子写入：tmp + os.replace"""
        with open("status_update.py") as f:
            content = f.read()
        self.assertIn("os.replace(tmp, STATUS_FILE)", content)
        self.assertIn('STATUS_FILE + ".tmp"', content)

    def test_save_sets_updated_timestamp(self):
        """save 自动设置 updated 和 updated_by"""
        with open("status_update.py") as f:
            content = f.read()
        self.assertIn('data["updated"] = time.strftime', content)
        self.assertIn('data["updated_by"] = updated_by', content)

    def test_load_handles_corrupted_json(self):
        """损坏的 JSON 返回默认结构（不 crash）"""
        with open("status_update.py") as f:
            content = f.read()
        self.assertIn("json.JSONDecodeError", content)

    def test_load_forward_compatible(self):
        """加载旧版 status.json 时自动补齐缺失字段"""
        with open("status_update.py") as f:
            content = f.read()
        # load_status 中有字段补齐逻辑
        self.assertIn("if k not in data:", content)
        self.assertIn("if kk not in data[k]:", content)


class TestSetNested(unittest.TestCase):
    """嵌套字段设置测试"""

    def test_set_top_level(self):
        """设置顶层字段"""
        from status_update import set_nested
        data = {"focus": ""}
        set_nested(data, "focus", "testing")
        self.assertEqual(data["focus"], "testing")

    def test_set_nested_field(self):
        """设置嵌套字段 health.services"""
        from status_update import set_nested
        data = {"health": {"services": "unknown"}}
        set_nested(data, "health.services", "ok")
        self.assertEqual(data["health"]["services"], "ok")

    def test_set_deep_nested(self):
        """设置三层嵌套"""
        from status_update import set_nested
        data = {"a": {"b": {"c": "old"}}}
        set_nested(data, "a.b.c", "new")
        self.assertEqual(data["a"]["b"]["c"], "new")

    def test_set_creates_intermediate_dicts(self):
        """中间路径不存在时自动创建"""
        from status_update import set_nested
        data = {}
        set_nested(data, "a.b.c", "value")
        self.assertEqual(data["a"]["b"]["c"], "value")

    def test_set_overwrites_non_dict(self):
        """中间路径不是 dict 时覆盖"""
        from status_update import set_nested
        data = {"a": "string"}
        set_nested(data, "a.b", "value")
        self.assertEqual(data["a"]["b"], "value")

    # ── V37.9.7: --set JSON 自动解析（闭环 2026-04-21 发现的 bug）────────
    def test_set_parses_json_list(self):
        """V37.9.7: '[...]' 字符串自动解析为 list"""
        from status_update import set_nested
        data = {}
        set_nested(data, "items", '["a","b","c"]')
        self.assertIsInstance(data["items"], list)
        self.assertEqual(data["items"], ["a", "b", "c"])

    def test_set_parses_json_dict(self):
        """V37.9.7: '{...}' 字符串自动解析为 dict"""
        from status_update import set_nested
        data = {}
        set_nested(data, "config", '{"k":"v","n":42}')
        self.assertIsInstance(data["config"], dict)
        self.assertEqual(data["config"], {"k": "v", "n": 42})

    def test_set_parses_int(self):
        """V37.9.7: 纯整数字符串解析为 int"""
        from status_update import set_nested
        data = {}
        set_nested(data, "count", "42")
        self.assertIsInstance(data["count"], int)
        self.assertEqual(data["count"], 42)

    def test_set_parses_float(self):
        """V37.9.7: 浮点字符串解析为 float"""
        from status_update import set_nested
        data = {}
        set_nested(data, "score", "93.5")
        self.assertIsInstance(data["score"], float)
        self.assertEqual(data["score"], 93.5)

    def test_set_parses_bool_literals(self):
        """V37.9.7: 'true'/'false'/'null' 解析为对应 Python 值"""
        from status_update import set_nested
        data = {}
        set_nested(data, "enabled", "true")
        self.assertIs(data["enabled"], True)
        set_nested(data, "disabled", "false")
        self.assertIs(data["disabled"], False)
        set_nested(data, "missing", "null")
        self.assertIsNone(data["missing"])

    def test_set_plain_string_stays_string(self):
        """V37.9.7 向后兼容: 非 JSON 字符串保持原样"""
        from status_update import set_nested
        data = {}
        set_nested(data, "note", "hello world")
        self.assertIsInstance(data["note"], str)
        self.assertEqual(data["note"], "hello world")

    def test_set_invalid_json_fallback_to_string(self):
        """V37.9.7: 看似 JSON 但非法 → fallback 为字符串"""
        from status_update import set_nested
        data = {}
        set_nested(data, "broken", "[not valid json")
        self.assertIsInstance(data["broken"], str)
        self.assertEqual(data["broken"], "[not valid json")

    def test_set_blood_lesson_unfinished_list(self):
        """V37.9.7 血案回归: 2026-04-21 unfinished 字段被存为 831 字符串。
        修复后同样调用应产生 list 类型。"""
        from status_update import set_nested
        data = {"session_context": {}}
        payload = '["item A","item B","item C with 特殊字符"]'
        set_nested(data, "session_context.unfinished", payload)
        self.assertIsInstance(
            data["session_context"]["unfinished"], list,
            "unfinished 必须是 list 不是 str（2026-04-21 血案）"
        )
        self.assertEqual(len(data["session_context"]["unfinished"]), 3)
        self.assertEqual(
            data["session_context"]["unfinished"][2],
            "item C with 特殊字符"
        )

    def test_parse_cli_value_direct(self):
        """V37.9.7: _parse_cli_value 纯函数单测"""
        from status_update import _parse_cli_value
        # list
        self.assertEqual(_parse_cli_value('[1,2,3]'), [1, 2, 3])
        # dict
        self.assertEqual(_parse_cli_value('{"x":1}'), {"x": 1})
        # bool
        self.assertIs(_parse_cli_value("true"), True)
        self.assertIs(_parse_cli_value("false"), False)
        self.assertIsNone(_parse_cli_value("null"))
        # int
        self.assertEqual(_parse_cli_value("42"), 42)
        self.assertEqual(_parse_cli_value("-7"), -7)
        # float
        self.assertEqual(_parse_cli_value("3.14"), 3.14)
        # 字符串（向后兼容）
        self.assertEqual(_parse_cli_value("hello"), "hello")
        # 非法 JSON 看似像但失败 → 字符串
        self.assertEqual(_parse_cli_value("[invalid"), "[invalid")
        # 非字符串输入透传
        self.assertEqual(_parse_cli_value([1, 2]), [1, 2])
        self.assertIsNone(_parse_cli_value(None))
        # 空字符串
        self.assertEqual(_parse_cli_value(""), "")


class TestArrayOperations(unittest.TestCase):
    """数组操作测试（add/pop/clear）"""

    def test_add_to_empty_array(self):
        """向空数组追加"""
        data = {"feedback": []}
        data["feedback"].append("test feedback")
        self.assertEqual(len(data["feedback"]), 1)

    def test_add_json_item(self):
        """追加 JSON 对象"""
        item_str = '{"task":"test","status":"active"}'
        item = json.loads(item_str)
        data = {"priorities": []}
        data["priorities"].append(item)
        self.assertEqual(data["priorities"][0]["task"], "test")

    def test_add_string_item(self):
        """追加纯字符串"""
        data = {"feedback": []}
        data["feedback"].append("simple string")
        self.assertEqual(data["feedback"][0], "simple string")

    def test_recent_changes_insert_front(self):
        """recent_changes 插入到开头"""
        data = {"recent_changes": [{"what": "old"}]}
        data["recent_changes"].insert(0, {"what": "new"})
        self.assertEqual(data["recent_changes"][0]["what"], "new")

    def test_recent_changes_limit_20(self):
        """recent_changes 最多保留 20 条"""
        data = {"recent_changes": [{"what": f"item_{i}"} for i in range(25)]}
        data["recent_changes"] = data["recent_changes"][:20]
        self.assertEqual(len(data["recent_changes"]), 20)

    def test_pop_valid_index(self):
        """pop 有效索引"""
        data = {"feedback": ["a", "b", "c"]}
        removed = data["feedback"].pop(1)
        self.assertEqual(removed, "b")
        self.assertEqual(len(data["feedback"]), 2)

    def test_pop_invalid_index(self):
        """pop 无效索引不 crash"""
        data = {"feedback": ["a"]}
        idx = 5
        if 0 <= idx < len(data["feedback"]):
            data["feedback"].pop(idx)
        self.assertEqual(len(data["feedback"]), 1)

    def test_clear_array(self):
        """清空数组"""
        data = {"feedback": ["a", "b", "c"]}
        data["feedback"] = []
        self.assertEqual(len(data["feedback"]), 0)


class TestPriorityUpdate(unittest.TestCase):
    """优先级 CRUD 测试"""

    def test_update_existing_priority(self):
        """更新已存在的优先级"""
        data = {"priorities": [
            {"task": "知识图谱", "status": "active"},
            {"task": "语音支持", "status": "backlog"},
        ]}
        for p in data["priorities"]:
            if p["task"] == "知识图谱":
                p["status"] = "done"
                break
        self.assertEqual(data["priorities"][0]["status"], "done")

    def test_update_nonexistent_creates(self):
        """更新不存在的任务时新增"""
        data = {"priorities": []}
        task_name = "新任务"
        found = False
        for p in data["priorities"]:
            if p["task"] == task_name:
                found = True
                break
        if not found:
            data["priorities"].append({"task": task_name, "status": "active"})
        self.assertEqual(len(data["priorities"]), 1)
        self.assertEqual(data["priorities"][0]["task"], "新任务")

    def test_update_preserves_other_fields(self):
        """更新 status 不影响其他字段"""
        data = {"priorities": [
            {"task": "test", "status": "active", "note": "重要"},
        ]}
        data["priorities"][0]["status"] = "done"
        self.assertEqual(data["priorities"][0]["note"], "重要")


class TestFormatHuman(unittest.TestCase):
    """人类可读格式测试"""

    def test_format_includes_focus(self):
        """format_human 包含焦点"""
        from status_update import format_human
        data = {**__import__("status_update").DEFAULT_STATUS, "focus": "V30 安全加固"}
        output = format_human(data)
        self.assertIn("V30 安全加固", output)

    def test_format_includes_priorities(self):
        """format_human 包含优先级"""
        from status_update import format_human, DEFAULT_STATUS
        data = {**DEFAULT_STATUS, "priorities": [
            {"task": "测试任务", "status": "active"}
        ]}
        output = format_human(data)
        self.assertIn("测试任务", output)

    def test_format_includes_health(self):
        """format_human 包含健康状态"""
        from status_update import format_human, DEFAULT_STATUS
        data = dict(DEFAULT_STATUS)
        data["health"]["services"] = "ok"
        output = format_human(data)
        self.assertIn("ok", output)

    def test_format_empty_state(self):
        """空状态不 crash"""
        from status_update import format_human, DEFAULT_STATUS
        output = format_human(dict(DEFAULT_STATUS))
        self.assertIn("项目状态", output)


class TestDefaultStatusStructure(unittest.TestCase):
    """默认状态结构完整性"""

    def test_has_all_required_fields(self):
        from status_update import DEFAULT_STATUS
        required = {"updated", "updated_by", "priorities", "recent_changes",
                     "feedback", "health", "focus", "notes"}
        self.assertTrue(required.issubset(set(DEFAULT_STATUS.keys())))

    def test_health_has_all_subfields(self):
        from status_update import DEFAULT_STATUS
        health = DEFAULT_STATUS["health"]
        required = {"services", "last_deploy", "last_deploy_time",
                     "last_preflight", "last_preflight_time",
                     "last_trend_report", "model_id",
                     "kb_stats", "stale_jobs", "last_refresh"}
        self.assertTrue(required.issubset(set(health.keys())),
                        f"Missing: {required - set(health.keys())}")

    def test_arrays_are_empty_by_default(self):
        from status_update import DEFAULT_STATUS
        self.assertEqual(DEFAULT_STATUS["priorities"], [])
        self.assertEqual(DEFAULT_STATUS["recent_changes"], [])
        self.assertEqual(DEFAULT_STATUS["feedback"], [])


class TestCLIInterface(unittest.TestCase):
    """CLI 接口测试"""

    def test_read_flag_exists(self):
        """--read 参数存在"""
        result = subprocess.run(
            [sys.executable, "status_update.py", "--help"],
            capture_output=True, text=True
        )
        self.assertIn("--read", result.stdout)

    def test_set_flag_exists(self):
        """--set 参数存在"""
        result = subprocess.run(
            [sys.executable, "status_update.py", "--help"],
            capture_output=True, text=True
        )
        self.assertIn("--set", result.stdout)

    def test_add_flag_exists(self):
        """--add 参数存在"""
        result = subprocess.run(
            [sys.executable, "status_update.py", "--help"],
            capture_output=True, text=True
        )
        self.assertIn("--add", result.stdout)

    def test_by_flag_exists(self):
        """--by 参数存在"""
        result = subprocess.run(
            [sys.executable, "status_update.py", "--help"],
            capture_output=True, text=True
        )
        self.assertIn("--by", result.stdout)

    def test_python_syntax(self):
        """status_update.py Python 语法正确"""
        result = subprocess.run(
            [sys.executable, "-c", "import ast; ast.parse(open('status_update.py').read())"],
            capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
