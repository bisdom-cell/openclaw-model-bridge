#!/usr/bin/env python3
"""
test_engine_phase4.py — V37.9.12 Phase 4 P1 wiring 契约守卫

锁定 engine.py 三个新纯函数的 API 行为：
  - load_domain_ontology(path=None) -> dict
  - find_by_domain(domain_name, ontology=None, path=None) -> list
  - evaluate_policy(policy_id, context=None, policy_data=None, path=None) -> dict

以及首条被实际切换的 policy (max-tools-per-agent) 的契约：
  - limit=12, hard_limit=True, type=static, governance_invariant=INV-TOOL-001
  - proxy_filters.py 查询此 policy 得到的值必须与 config MAX_TOOLS 一致（Phase 4 P1
    只替换数据源，不改阈值）

配合 test_phase4_ontology_skeleton.py（锁 YAML 声明结构） + test_tool_proxy.py 的
TestPolicyDrivenMaxTools（锁 wiring 后向后兼容）形成三层防线。
"""

import os
import sys
import tempfile
import unittest

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_ONTOLOGY_DIR = os.path.dirname(_TESTS_DIR)
_PROJECT_ROOT = os.path.dirname(_ONTOLOGY_DIR)
for p in [_ONTOLOGY_DIR, _PROJECT_ROOT]:
    if p not in sys.path:
        sys.path.insert(0, p)

try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False

from engine import (
    load_domain_ontology,
    load_policy_ontology,
    find_by_domain,
    evaluate_policy,
    _parse_limit_from_rule,
)


# ===========================================================================
# load_domain_ontology / load_policy_ontology
# ===========================================================================
class TestLoadDomainOntology(unittest.TestCase):
    """load_domain_ontology() 纯函数加载契约。"""

    @unittest.skipUnless(HAS_YAML, "PyYAML not available")
    def test_returns_dict_with_domains_key(self):
        data = load_domain_ontology()
        self.assertIsInstance(data, dict)
        self.assertIn("domains", data)

    @unittest.skipUnless(HAS_YAML, "PyYAML not available")
    def test_has_all_six_domains(self):
        data = load_domain_ontology()
        expected = {"Actor", "Tool", "Resource", "Task", "Provider", "Memory"}
        self.assertEqual(set(data["domains"].keys()), expected)

    @unittest.skipUnless(HAS_YAML, "PyYAML not available")
    def test_accepts_alternate_path(self):
        # 写一个最小 YAML 到 tmp，确认 path 参数优先
        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False, encoding="utf-8"
        )
        tmp.write("domains:\n  Actor:\n    description: test-only\n    instances: []\n")
        tmp.close()
        try:
            data = load_domain_ontology(path=tmp.name)
            self.assertEqual(set(data["domains"].keys()), {"Actor"})
            self.assertEqual(data["domains"]["Actor"]["description"], "test-only")
        finally:
            os.unlink(tmp.name)

    def test_missing_file_raises(self):
        with self.assertRaises((IOError, OSError, FileNotFoundError)):
            load_domain_ontology(path="/nonexistent/path/domain.yaml")


class TestLoadPolicyOntology(unittest.TestCase):
    """load_policy_ontology() 加载契约。"""

    @unittest.skipUnless(HAS_YAML, "PyYAML not available")
    def test_returns_dict_with_policies_list(self):
        data = load_policy_ontology()
        self.assertIsInstance(data, dict)
        self.assertIn("policies", data)
        self.assertIsInstance(data["policies"], list)
        self.assertGreaterEqual(len(data["policies"]), 5)


# ===========================================================================
# find_by_domain()
# ===========================================================================
class TestFindByDomain(unittest.TestCase):
    """find_by_domain() 归一化返回契约。"""

    @unittest.skipUnless(HAS_YAML, "PyYAML not available")
    def test_actor_returns_instance_list(self):
        actors = find_by_domain("Actor")
        self.assertIsInstance(actors, list)
        self.assertGreater(len(actors), 0)
        ids = {a["id"] for a in actors}
        # Actor 必须至少含这三个核心实例（与 test_phase4_ontology_skeleton 对齐）
        self.assertTrue({"user", "pa", "cron_job"}.issubset(ids))

    @unittest.skipUnless(HAS_YAML, "PyYAML not available")
    def test_resource_returns_categories(self):
        resources = find_by_domain("Resource")
        ids = {r["id"] for r in resources}
        self.assertIn("file", ids)
        self.assertIn("kb_note", ids)
        self.assertIn("status", ids)

    @unittest.skipUnless(HAS_YAML, "PyYAML not available")
    def test_task_returns_taxonomy(self):
        tasks = find_by_domain("Task")
        ids = {t["id"] for t in tasks}
        self.assertIn("qa", ids)
        self.assertIn("monitoring", ids)
        self.assertIn("dev", ids)

    @unittest.skipUnless(HAS_YAML, "PyYAML not available")
    def test_memory_returns_four_layers(self):
        layers = find_by_domain("Memory")
        ids = {L["id"] for L in layers}
        self.assertEqual(ids, {"kb_semantic", "multimodal", "preferences", "state"})

    @unittest.skipUnless(HAS_YAML, "PyYAML not available")
    def test_provider_types_are_normalized_from_strings(self):
        """Provider.types 是字符串列表，find_by_domain 必须包装成 {"id": ...} dict。"""
        providers = find_by_domain("Provider")
        self.assertIsInstance(providers, list)
        for p in providers:
            self.assertIsInstance(p, dict)
            self.assertIn("id", p)
        ids = {p["id"] for p in providers}
        self.assertIn("llm", ids)
        self.assertIn("vl_model", ids)

    @unittest.skipUnless(HAS_YAML, "PyYAML not available")
    def test_tool_returns_empty_because_source_of_truth_elsewhere(self):
        """Tool 域的 source_of_truth 是 tool_ontology.yaml；find_by_domain 主动返回 []
        防止调用方误用（应改用 ToolOntology.query_tools()）。"""
        self.assertEqual(find_by_domain("Tool"), [])

    def test_unknown_domain_returns_empty_list_not_raise(self):
        self.assertEqual(find_by_domain("NonexistentDomain"), [])

    def test_accepts_pre_loaded_ontology_dict(self):
        """ontology 参数注入（测试/缓存用），不触碰文件系统。"""
        injected = {
            "domains": {
                "Actor": {
                    "description": "injected",
                    "instances": [{"id": "fake_actor", "kind": "test"}],
                }
            }
        }
        result = find_by_domain("Actor", ontology=injected)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["id"], "fake_actor")

    def test_pure_function_no_input_mutation(self):
        """纯函数契约：调用不得修改输入 dict。"""
        injected = {
            "domains": {
                "Memory": {
                    "description": "x",
                    "layers": [{"id": "a"}, {"id": "b"}],
                }
            }
        }
        import copy
        before = copy.deepcopy(injected)
        _ = find_by_domain("Memory", ontology=injected)
        self.assertEqual(injected, before)


# ===========================================================================
# evaluate_policy()
# ===========================================================================
class TestEvaluatePolicyContract(unittest.TestCase):
    """evaluate_policy() 返回结构稳定性。"""

    REQUIRED_KEYS = {
        "policy_id", "found", "type", "hard_limit", "limit",
        "applicable", "rule", "rationale", "enforcement_site",
        "governance_invariant", "scope", "reason",
    }

    @unittest.skipUnless(HAS_YAML, "PyYAML not available")
    def test_unknown_policy_returns_found_false_with_stable_keys(self):
        r = evaluate_policy("totally-fake-policy-id")
        self.assertEqual(set(r.keys()), self.REQUIRED_KEYS)
        self.assertFalse(r["found"])
        self.assertEqual(r["reason"], "policy_id_not_found")
        self.assertIsNone(r["limit"])
        self.assertIsNone(r["type"])
        self.assertFalse(r["hard_limit"])

    @unittest.skipUnless(HAS_YAML, "PyYAML not available")
    def test_static_policy_returns_applicable_true(self):
        r = evaluate_policy("max-tools-per-agent")
        self.assertEqual(set(r.keys()), self.REQUIRED_KEYS)
        self.assertTrue(r["found"])
        self.assertEqual(r["type"], "static")
        self.assertTrue(r["applicable"])
        self.assertIsNone(r["reason"])

    @unittest.skipUnless(HAS_YAML, "PyYAML not available")
    def test_contextual_policy_returns_applicable_none_pending_p2(self):
        """Phase 4 P1 只覆盖 static; contextual 留 applicable=None + reason=needs_context_evaluator。"""
        r = evaluate_policy("multimodal-routing")
        self.assertTrue(r["found"])
        self.assertEqual(r["type"], "contextual")
        self.assertIsNone(r["applicable"])
        self.assertEqual(r["reason"], "needs_context_evaluator")

    @unittest.skipUnless(HAS_YAML, "PyYAML not available")
    def test_temporal_policy_returns_applicable_none_pending_p2(self):
        r = evaluate_policy("quiet-hours-00-07")
        self.assertTrue(r["found"])
        self.assertEqual(r["type"], "temporal")
        self.assertIsNone(r["applicable"])

    def test_load_failure_returns_structured_result_not_raise(self):
        """文件不存在场景：evaluate_policy 不得抛异常，必须返回 found=False 结果。"""
        r = evaluate_policy("max-tools-per-agent", path="/nonexistent/policy.yaml")
        self.assertEqual(set(r.keys()), self.REQUIRED_KEYS)
        self.assertFalse(r["found"])
        self.assertIsNotNone(r["reason"])
        self.assertTrue(r["reason"].startswith("load_failed:"))

    def test_accepts_pre_loaded_policy_data(self):
        """policy_data 注入绕过文件读（测试用）。"""
        injected = {
            "policies": [
                {
                    "id": "test-policy",
                    "type": "static",
                    "scope": ["Tool"],
                    "rule": "test ≤ 5",
                    "limit": 5,
                    "hard_limit": True,
                    "rationale": "test",
                    "enforcement_site": "test.py",
                }
            ]
        }
        r = evaluate_policy("test-policy", policy_data=injected)
        self.assertTrue(r["found"])
        self.assertEqual(r["limit"], 5)
        self.assertTrue(r["hard_limit"])

    def test_context_param_accepted_even_if_ignored_in_p1(self):
        """context 参数签名兼容 P2；P1 不使用但不得拒绝。"""
        r = evaluate_policy("max-tools-per-agent", context={"actor": "pa"})
        self.assertTrue(r["found"])


class TestMaxToolsPolicyWiring(unittest.TestCase):
    """被 proxy_filters.py 首个切换的 policy — max-tools-per-agent 的硬契约。

    若这些 assertion 失败意味着 wiring 契约破坏（hardcoded MAX_TOOLS 和 ontology 声明漂移）。
    """

    @unittest.skipUnless(HAS_YAML, "PyYAML not available")
    def test_policy_exists_and_type_static(self):
        r = evaluate_policy("max-tools-per-agent")
        self.assertTrue(r["found"])
        self.assertEqual(r["type"], "static")

    @unittest.skipUnless(HAS_YAML, "PyYAML not available")
    def test_limit_is_12(self):
        """Phase 4 P1 wiring 必须保证 ontology 声明的阈值 = 硬编码 MAX_TOOLS (12)。
        改这个数字需要同步 config_loader + 跑完整 E2E 验证。"""
        r = evaluate_policy("max-tools-per-agent")
        self.assertEqual(r["limit"], 12)

    @unittest.skipUnless(HAS_YAML, "PyYAML not available")
    def test_limit_matches_config_loader_max_tools(self):
        """横向一致性: ontology.limit 必须 == config_loader.MAX_TOOLS。
        防止两个数据源漂移（导师指出的"可迁移性"差距）。"""
        from config_loader import MAX_TOOLS
        r = evaluate_policy("max-tools-per-agent")
        self.assertEqual(
            r["limit"], MAX_TOOLS,
            f"ontology limit ({r['limit']}) != config_loader.MAX_TOOLS ({MAX_TOOLS}) — "
            "Phase 4 wiring 安全网要求二者同步，不同步意味着切换后行为将改变"
        )

    @unittest.skipUnless(HAS_YAML, "PyYAML not available")
    def test_hard_limit_true(self):
        r = evaluate_policy("max-tools-per-agent")
        self.assertTrue(r["hard_limit"])

    @unittest.skipUnless(HAS_YAML, "PyYAML not available")
    def test_governance_invariant_is_inv_tool_001(self):
        r = evaluate_policy("max-tools-per-agent")
        self.assertEqual(r["governance_invariant"], "INV-TOOL-001")

    @unittest.skipUnless(HAS_YAML, "PyYAML not available")
    def test_enforcement_site_points_to_proxy_filters(self):
        """enforcement_site 必须指向实际切换代码所在文件，防止声明和实现漂移。"""
        r = evaluate_policy("max-tools-per-agent")
        self.assertIn("proxy_filters.py", (r["enforcement_site"] or ""))


class TestParseLimitFromRuleFallback(unittest.TestCase):
    """_parse_limit_from_rule() — YAML 未声明 limit 时的文本解析回退。"""

    def test_leq_unicode_symbol(self):
        self.assertEqual(_parse_limit_from_rule("|x| ≤ 12 per call"), 12)

    def test_leq_ascii(self):
        self.assertEqual(_parse_limit_from_rule("x <= 200_000 bytes"), 200000)

    def test_less_than(self):
        self.assertEqual(_parse_limit_from_rule("count < 5"), 5)

    def test_underscore_thousands_separator(self):
        self.assertEqual(_parse_limit_from_rule("body ≤ 1_000_000"), 1000000)

    def test_no_match_returns_none(self):
        self.assertIsNone(_parse_limit_from_rule("routing: if has_image then VL else Qwen3"))

    def test_non_string_returns_none(self):
        self.assertIsNone(_parse_limit_from_rule(None))
        self.assertIsNone(_parse_limit_from_rule(42))
        self.assertIsNone(_parse_limit_from_rule([]))

    def test_empty_string_returns_none(self):
        self.assertIsNone(_parse_limit_from_rule(""))


class TestEvaluatePolicyLimitFallbackChain(unittest.TestCase):
    """limit 提取优先级: explicit `limit` > `value` > regex-parse(rule)."""

    def test_explicit_limit_field_wins(self):
        injected = {
            "policies": [{
                "id": "p1", "type": "static", "scope": [], "rule": "≤ 100",
                "limit": 50, "rationale": "x", "enforcement_site": "y",
            }]
        }
        r = evaluate_policy("p1", policy_data=injected)
        self.assertEqual(r["limit"], 50)

    def test_value_field_used_when_limit_absent(self):
        injected = {
            "policies": [{
                "id": "p2", "type": "static", "scope": [], "rule": "anything",
                "value": 77, "rationale": "x", "enforcement_site": "y",
            }]
        }
        r = evaluate_policy("p2", policy_data=injected)
        self.assertEqual(r["limit"], 77)

    def test_regex_parsed_from_rule_as_last_resort(self):
        injected = {
            "policies": [{
                "id": "p3", "type": "static", "scope": [], "rule": "x ≤ 9",
                "rationale": "x", "enforcement_site": "y",
            }]
        }
        r = evaluate_policy("p3", policy_data=injected)
        self.assertEqual(r["limit"], 9)

    def test_limit_none_when_no_source(self):
        injected = {
            "policies": [{
                "id": "p4", "type": "contextual", "scope": [],
                "rule": "if image then VL else text",
                "rationale": "x", "enforcement_site": "y",
            }]
        }
        r = evaluate_policy("p4", policy_data=injected)
        self.assertIsNone(r["limit"])


if __name__ == "__main__":
    unittest.main()
