#!/usr/bin/env python3
"""
test_ontology_engine.py — ontology_engine.py 单测

覆盖：加载/工具白名单/Schema生成/参数别名/自定义工具/策略查询/验证/一致性检查/CLI
"""

import json
import os
import sys
import unittest

# 确保能导入 ontology 包和父项目模块
_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_ONTOLOGY_DIR = os.path.dirname(_TESTS_DIR)
_PROJECT_ROOT = os.path.dirname(_ONTOLOGY_DIR)
for p in [_ONTOLOGY_DIR, _PROJECT_ROOT]:
    if p not in sys.path:
        sys.path.insert(0, p)

from engine import ToolOntology, get_ontology


class TestToolOntologyLoading(unittest.TestCase):
    """加载和初始化测试。"""

    def test_load_default_file(self):
        onto = ToolOntology()
        self.assertIsNotNone(onto)

    def test_load_from_data(self):
        data = {
            "tools": {"builtin": {"test_tool": {
                "category": "test",
                "parameters": {"arg1": {"type": "string", "required": True}}
            }}},
            "policies": {},
            "metadata": {"version": "test"}
        }
        onto = ToolOntology(data=data)
        self.assertIn("test_tool", onto.allowed_tools)

    def test_summary(self):
        onto = ToolOntology()
        summary = onto.summary()
        self.assertIn("Tool Ontology", summary)
        self.assertIn("Builtin tools", summary)

    def test_reload(self):
        onto = ToolOntology()
        old_count = len(onto.allowed_tools)
        onto.reload()
        self.assertEqual(len(onto.allowed_tools), old_count)


class TestAllowedTools(unittest.TestCase):
    """工具白名单测试。"""

    def setUp(self):
        self.onto = ToolOntology()

    def test_builtin_tools_present(self):
        expected = {"web_search", "web_fetch", "read", "write", "edit", "exec",
                    "memory_search", "memory_get", "cron", "message", "tts", "image",
                    "sessions_spawn", "sessions_send", "sessions_history", "agents_list"}
        self.assertEqual(self.onto.allowed_tools, expected)

    def test_prefix_matched(self):
        self.assertEqual(self.onto.allowed_prefixes, ["browser"])

    def test_custom_tools(self):
        self.assertIn("data_clean", self.onto.custom_tool_names)
        self.assertIn("search_kb", self.onto.custom_tool_names)

    def test_no_overlap_builtin_custom(self):
        """内置工具和自定义工具不应该有重叠。"""
        overlap = self.onto.allowed_tools & self.onto.custom_tool_names
        self.assertEqual(overlap, set())


class TestSchemaGeneration(unittest.TestCase):
    """Schema 生成测试。"""

    def setUp(self):
        self.onto = ToolOntology()

    def test_web_search_schema(self):
        schema = self.onto.clean_schemas["web_search"]
        self.assertEqual(schema["type"], "object")
        self.assertIn("query", schema["properties"])
        self.assertEqual(schema["required"], ["query"])
        self.assertFalse(schema["additionalProperties"])

    def test_write_schema_multiple_required(self):
        schema = self.onto.clean_schemas["write"]
        self.assertIn("path", schema["required"])
        self.assertIn("content", schema["required"])

    def test_cron_schema_optional_params(self):
        schema = self.onto.clean_schemas["cron"]
        self.assertEqual(schema["required"], ["action"])
        # 其他参数应该在 properties 中但不在 required 中
        self.assertIn("name", schema["properties"])
        self.assertNotIn("name", schema.get("required", []))

    def test_agents_list_empty_schema(self):
        schema = self.onto.clean_schemas["agents_list"]
        self.assertEqual(schema["properties"], {})
        self.assertFalse(schema["additionalProperties"])

    def test_image_no_schema(self):
        """image 工具在白名单中但不应该有 schema。"""
        self.assertNotIn("image", self.onto.clean_schemas)

    def test_edit_three_required(self):
        schema = self.onto.clean_schemas["edit"]
        self.assertEqual(sorted(schema["required"]), ["new_text", "old_text", "path"])

    def test_data_clean_enum(self):
        """自定义工具的 enum 约束。"""
        # 通过 custom_tools 列表检查
        for tool in self.onto.custom_tools:
            if tool["function"]["name"] == "data_clean":
                action_prop = tool["function"]["parameters"]["properties"]["action"]
                self.assertIn("enum", action_prop)
                self.assertEqual(action_prop["enum"], ["profile", "execute", "list_ops"])
                break
        else:
            self.fail("data_clean not found in custom_tools")


class TestToolParams(unittest.TestCase):
    """合法参数集测试。"""

    def setUp(self):
        self.onto = ToolOntology()

    def test_read_params(self):
        self.assertEqual(self.onto.tool_params["read"], {"path"})

    def test_write_params(self):
        self.assertEqual(self.onto.tool_params["write"], {"path", "content"})

    def test_exec_params(self):
        self.assertEqual(self.onto.tool_params["exec"], {"command"})

    def test_browser_navigate_params(self):
        self.assertIn("url", self.onto.tool_params["browser_navigate"])
        self.assertIn("profile", self.onto.tool_params["browser_navigate"])

    def test_cron_params(self):
        expected = {"action", "name", "schedule", "sessionTarget", "payload", "id", "command", "job"}
        self.assertEqual(self.onto.tool_params["cron"], expected)


class TestAliases(unittest.TestCase):
    """参数别名测试。"""

    def setUp(self):
        self.onto = ToolOntology()

    def test_read_alias_file_path(self):
        args = {"file_path": "/tmp/test.txt"}
        resolved, changed = self.onto.resolve_alias("read", args)
        self.assertTrue(changed)
        self.assertEqual(resolved["path"], "/tmp/test.txt")
        self.assertNotIn("file_path", resolved)

    def test_read_alias_file(self):
        args = {"file": "/tmp/test.txt"}
        resolved, changed = self.onto.resolve_alias("read", args)
        self.assertTrue(changed)
        self.assertEqual(resolved["path"], "/tmp/test.txt")

    def test_exec_alias_cmd(self):
        args = {"cmd": "ls -la"}
        resolved, changed = self.onto.resolve_alias("exec", args)
        self.assertTrue(changed)
        self.assertEqual(resolved["command"], "ls -la")

    def test_write_alias_text(self):
        args = {"path": "/tmp/f.txt", "text": "hello"}
        resolved, changed = self.onto.resolve_alias("write", args)
        self.assertTrue(changed)
        self.assertEqual(resolved["content"], "hello")

    def test_web_search_alias_q(self):
        args = {"q": "test query"}
        resolved, changed = self.onto.resolve_alias("web_search", args)
        self.assertTrue(changed)
        self.assertEqual(resolved["query"], "test query")

    def test_no_alias_when_canonical_present(self):
        """正式参数名存在时不触发别名替换。"""
        args = {"path": "/tmp/test.txt", "file_path": "/tmp/other.txt"}
        resolved, changed = self.onto.resolve_alias("read", args)
        self.assertFalse(changed)
        self.assertEqual(resolved["path"], "/tmp/test.txt")

    def test_no_alias_for_unknown_tool(self):
        args = {"foo": "bar"}
        resolved, changed = self.onto.resolve_alias("unknown_tool", args)
        self.assertFalse(changed)

    def test_alias_mapping_count(self):
        """确保所有别名都已从 proxy_filters 迁移。"""
        # read: 4 aliases, exec: 4, write: 4, web_search: 4
        total = sum(len(alts) for alts_dict in self.onto.aliases.values() for alts in alts_dict.values())
        self.assertEqual(total, 16)  # 4 tools × 4 aliases each


class TestCustomTools(unittest.TestCase):
    """自定义工具生成测试。"""

    def setUp(self):
        self.onto = ToolOntology()

    def test_custom_tools_format(self):
        for tool in self.onto.custom_tools:
            self.assertEqual(tool["type"], "function")
            self.assertIn("name", tool["function"])
            self.assertIn("description", tool["function"])
            self.assertIn("parameters", tool["function"])

    def test_custom_tools_count(self):
        self.assertEqual(len(self.onto.custom_tools), 2)

    def test_data_clean_custom_tool(self):
        found = False
        for tool in self.onto.custom_tools:
            if tool["function"]["name"] == "data_clean":
                found = True
                params = tool["function"]["parameters"]
                self.assertIn("action", params["properties"])
                self.assertIn("file", params["properties"])
                break
        self.assertTrue(found)

    def test_search_kb_custom_tool(self):
        found = False
        for tool in self.onto.custom_tools:
            if tool["function"]["name"] == "search_kb":
                found = True
                params = tool["function"]["parameters"]
                self.assertIn("query", params["properties"])
                self.assertIn("source", params["properties"])
                # source 应该有 enum
                self.assertIn("enum", params["properties"]["source"])
                break
        self.assertTrue(found)


class TestPolicies(unittest.TestCase):
    """策略查询测试。"""

    def setUp(self):
        self.onto = ToolOntology()

    def test_get_policy_category(self):
        policy = self.onto.get_policy("tool_admission")
        self.assertIsNotNone(policy)
        self.assertIn("rules", policy)

    def test_get_policy_rule(self):
        rule = self.onto.get_policy("tool_admission", "max_tools")
        self.assertIsNotNone(rule)
        self.assertEqual(rule["value"], 12)

    def test_get_policy_value(self):
        val = self.onto.get_policy_value("request_limits", "max_request_bytes")
        self.assertEqual(val, 200000)

    def test_get_policy_value_default(self):
        val = self.onto.get_policy_value("nonexistent", "nope", default=42)
        self.assertEqual(val, 42)

    def test_policy_has_rationale(self):
        """每条策略规则都应该有 rationale（设计原因）。"""
        for cat_name, policy in self.onto._policies.items():
            for rule in policy.get("rules", []):
                self.assertIn("rationale", rule,
                              f"Policy {cat_name}.{rule.get('name')} missing rationale")


class TestBrowserConstraints(unittest.TestCase):
    """浏览器约束测试。"""

    def setUp(self):
        self.onto = ToolOntology()

    def test_valid_profiles(self):
        self.assertEqual(self.onto.valid_browser_profiles, {"openclaw", "chrome"})

    def test_default_profile(self):
        self.assertEqual(self.onto.default_browser_profile, "openclaw")


class TestToolMetadata(unittest.TestCase):
    """工具元数据查询测试。"""

    def setUp(self):
        self.onto = ToolOntology()

    def test_builtin_metadata(self):
        meta = self.onto.get_tool_metadata("read")
        self.assertEqual(meta["type"], "builtin")
        self.assertEqual(meta["category"], "file_operation")
        self.assertFalse(meta["side_effects"])

    def test_custom_metadata(self):
        meta = self.onto.get_tool_metadata("data_clean")
        self.assertEqual(meta["type"], "custom")
        self.assertEqual(meta["category"], "data_processing")
        self.assertTrue(meta["side_effects"])
        self.assertEqual(meta["executor"], "data_clean.py")

    def test_unknown_tool_metadata(self):
        meta = self.onto.get_tool_metadata("nonexistent")
        self.assertEqual(meta, {})

    def test_side_effects_read_vs_write(self):
        self.assertFalse(self.onto.has_side_effects("read"))
        self.assertTrue(self.onto.has_side_effects("write"))
        self.assertTrue(self.onto.has_side_effects("exec"))

    def test_category_query(self):
        file_tools = self.onto.get_tools_by_category("file_operation")
        self.assertIn("read", file_tools)
        self.assertIn("write", file_tools)
        self.assertIn("edit", file_tools)

    def test_all_tools_have_category(self):
        """所有工具都应该有 category。"""
        for name in self.onto.allowed_tools:
            meta = self.onto.get_tool_metadata(name)
            self.assertIn("category", meta, f"Tool {name} missing category")


class TestValidation(unittest.TestCase):
    """工具参数验证测试。"""

    def setUp(self):
        self.onto = ToolOntology()

    def test_valid_web_search(self):
        valid, errors = self.onto.validate_tool_args("web_search", {"query": "test"})
        self.assertTrue(valid)
        self.assertEqual(errors, [])

    def test_missing_required(self):
        valid, errors = self.onto.validate_tool_args("web_search", {})
        self.assertFalse(valid)
        self.assertTrue(any("query" in e for e in errors))

    def test_invalid_enum(self):
        valid, errors = self.onto.validate_tool_args("data_clean", {"action": "invalid"})
        self.assertFalse(valid)
        self.assertTrue(any("enum" in e or "Must be one of" in e for e in errors))

    def test_extra_params(self):
        valid, errors = self.onto.validate_tool_args("web_search", {"query": "test", "extra": "bad"})
        self.assertFalse(valid)
        self.assertTrue(any("Unexpected" in e for e in errors))

    def test_valid_with_optional(self):
        valid, errors = self.onto.validate_tool_args("search_kb", {"query": "AI", "source": "arxiv"})
        self.assertTrue(valid)

    def test_unknown_tool_passes(self):
        """未知工具不做验证（宽容策略）。"""
        valid, errors = self.onto.validate_tool_args("unknown", {"anything": "goes"})
        self.assertTrue(valid)


class TestConsistencyCheck(unittest.TestCase):
    """与 proxy_filters.py 一致性检查测试。"""

    def test_consistency_with_proxy_filters(self):
        """ontology 必须与 proxy_filters.py 硬编码完全一致。"""
        from proxy_filters import ALLOWED_TOOLS, CLEAN_SCHEMAS, TOOL_PARAMS
        onto = ToolOntology()
        issues = onto.check_consistency(ALLOWED_TOOLS, CLEAN_SCHEMAS, TOOL_PARAMS)
        self.assertEqual(issues, [], f"Consistency issues: {issues}")

    def test_detect_intentional_mismatch(self):
        """故意制造不一致，确认检查能发现。"""
        onto = ToolOntology()
        fake_allowed = {"web_search", "FAKE_TOOL"}
        issues = onto.check_consistency(fake_allowed, {}, {})
        self.assertTrue(len(issues) > 0)


class TestCLI(unittest.TestCase):
    """CLI 输出测试。"""

    def test_cli_summary(self):
        import subprocess
        result = subprocess.run([sys.executable, os.path.join(_ONTOLOGY_DIR, "engine.py"), "--summary"],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0)
        self.assertIn("Tool Ontology", result.stdout)

    def test_cli_tools(self):
        import subprocess
        result = subprocess.run([sys.executable, os.path.join(_ONTOLOGY_DIR, "engine.py"), "--tools"],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0)
        self.assertIn("web_search", result.stdout)

    def test_cli_categories(self):
        import subprocess
        result = subprocess.run([sys.executable, os.path.join(_ONTOLOGY_DIR, "engine.py"), "--categories"],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0)
        self.assertIn("file_operation", result.stdout)

    def test_cli_policies(self):
        import subprocess
        result = subprocess.run([sys.executable, os.path.join(_ONTOLOGY_DIR, "engine.py"), "--policies"],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0)
        self.assertIn("tool_admission", result.stdout)

    def test_cli_check(self):
        import subprocess
        result = subprocess.run([sys.executable, os.path.join(_ONTOLOGY_DIR, "engine.py"), "--check"],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0)
        self.assertIn("consistent", result.stdout)

    def test_cli_validate_valid(self):
        import subprocess
        result = subprocess.run([sys.executable, os.path.join(_ONTOLOGY_DIR, "engine.py"), "--validate",
                                 "web_search", '{"query":"test"}'],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0)
        self.assertIn("valid", result.stdout)

    def test_cli_validate_invalid(self):
        import subprocess
        result = subprocess.run([sys.executable, os.path.join(_ONTOLOGY_DIR, "engine.py"), "--validate",
                                 "web_search", '{}'],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn("Missing required", result.stdout)

    def test_cli_tools_json(self):
        import subprocess
        result = subprocess.run([sys.executable, os.path.join(_ONTOLOGY_DIR, "engine.py"), "--tools", "--json"],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0)
        data = json.loads(result.stdout)
        self.assertIsInstance(data, list)
        names = [t["name"] for t in data]
        self.assertIn("web_search", names)


class TestConstitution(unittest.TestCase):
    """宪法四条强制执行测试。

    这些测试直接对应 docs/ontology/CONSTITUTION.md 的四条不可违反原则。
    任何一条失败 = 违宪。
    """

    def test_constitution_1_non_destructive(self):
        """宪法第一条：非破坏性引入 — proxy_filters 不依赖 ontology_engine 即可工作。"""
        # 验证 proxy_filters 的核心数据结构独立存在
        from proxy_filters import ALLOWED_TOOLS, CLEAN_SCHEMAS, TOOL_PARAMS, CUSTOM_TOOLS
        self.assertGreater(len(ALLOWED_TOOLS), 0, "ALLOWED_TOOLS 必须独立定义")
        self.assertGreater(len(CLEAN_SCHEMAS), 0, "CLEAN_SCHEMAS 必须独立定义")
        self.assertGreater(len(TOOL_PARAMS), 0, "TOOL_PARAMS 必须独立定义")
        self.assertGreater(len(CUSTOM_TOOLS), 0, "CUSTOM_TOOLS 必须独立定义")

    def test_constitution_2_consistency(self):
        """宪法第二条：一致性安全网 — ontology 与 hardcoded 100% 一致。"""
        from proxy_filters import ALLOWED_TOOLS, CLEAN_SCHEMAS, TOOL_PARAMS
        onto = ToolOntology()
        issues = onto.check_consistency(ALLOWED_TOOLS, CLEAN_SCHEMAS, TOOL_PARAMS)
        self.assertEqual(issues, [],
                         f"宪法第二条违规 — ontology 与 hardcoded 不一致:\n" +
                         "\n".join(f"  - {i}" for i in issues))

    def test_constitution_3_rationale(self):
        """宪法第三条：每条规则有 rationale — 无例外。"""
        onto = ToolOntology()
        missing = []
        for cat_name, policy in onto._policies.items():
            for rule in policy.get("rules", []):
                name = rule.get("name", "?")
                if not rule.get("rationale", "").strip():
                    missing.append(f"{cat_name}.{name}")
        self.assertEqual(missing, [],
                         f"宪法第三条违规 — 以下规则缺少 rationale:\n" +
                         "\n".join(f"  - {m}" for m in missing))

    def test_constitution_4_diff_all_green(self):
        """宪法第四条：差异对比全绿 — 81 项 100% 一致。"""
        from diff import run_diff
        items = run_diff()
        non_match = [i for i in items if i.status != "match"]
        self.assertEqual(len(non_match), 0,
                         f"宪法第四条违规 — {len(non_match)} 项不一致:\n" +
                         "\n".join(f"  - [{i.dimension}] {i.item}: {i.icon} {i.detail}"
                                  for i in non_match))

    def test_constitution_4_all_dimensions_covered(self):
        """宪法第四条补充：diff 必须覆盖所有 9 个维度。"""
        from diff import run_diff
        items = run_diff()
        dimensions = {i.dimension for i in items}
        expected_dims = {
            "工具白名单", "前缀匹配", "Schema", "参数集合",
            "参数别名", "自定义工具", "浏览器约束", "策略值", "Rationale覆盖"
        }
        self.assertEqual(dimensions, expected_dims,
                         f"diff 维度不完整: 缺少 {expected_dims - dimensions}")


if __name__ == "__main__":
    unittest.main()
