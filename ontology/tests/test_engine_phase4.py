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


class TestContextEvaluatorQuietHours(unittest.TestCase):
    """V37.9.13 Phase 4 P2 — temporal policy `quiet-hours-00-07` context evaluator.

    规则: hour_of_day ∈ [0, 7) → applicable=True
    """

    @unittest.skipUnless(HAS_YAML, "PyYAML not available")
    def test_hour_in_quiet_window(self):
        for h in (0, 3, 6):
            r = evaluate_policy("quiet-hours-00-07", context={"hour": h})
            self.assertTrue(r["applicable"], f"h={h} should be quiet")
            self.assertIsNone(r["reason"])

    @unittest.skipUnless(HAS_YAML, "PyYAML not available")
    def test_hour_outside_quiet_window(self):
        for h in (7, 12, 23):
            r = evaluate_policy("quiet-hours-00-07", context={"hour": h})
            self.assertFalse(r["applicable"], f"h={h} should NOT be quiet")

    @unittest.skipUnless(HAS_YAML, "PyYAML not available")
    def test_datetime_now_parsed(self):
        import datetime as _dt
        r = evaluate_policy(
            "quiet-hours-00-07",
            context={"now": _dt.datetime(2026, 4, 23, 5, 30)}
        )
        self.assertTrue(r["applicable"])

    @unittest.skipUnless(HAS_YAML, "PyYAML not available")
    def test_no_context_returns_none_with_specific_reason(self):
        r = evaluate_policy("quiet-hours-00-07")
        self.assertIsNone(r["applicable"])
        self.assertEqual(r["reason"], "needs_context_evaluator")

    @unittest.skipUnless(HAS_YAML, "PyYAML not available")
    def test_context_without_hour_reports_missing(self):
        r = evaluate_policy("quiet-hours-00-07", context={"actor": "pa"})
        self.assertIsNone(r["applicable"])
        self.assertEqual(r["reason"], "context_missing_hour")

    @unittest.skipUnless(HAS_YAML, "PyYAML not available")
    def test_invalid_hour_type(self):
        r = evaluate_policy("quiet-hours-00-07", context={"hour": "five"})
        self.assertIsNone(r["applicable"])
        self.assertEqual(r["reason"], "context_hour_invalid_type")

    @unittest.skipUnless(HAS_YAML, "PyYAML not available")
    def test_out_of_range_hour(self):
        r = evaluate_policy("quiet-hours-00-07", context={"hour": 25})
        self.assertIsNone(r["applicable"])
        self.assertEqual(r["reason"], "context_hour_out_of_range")

    @unittest.skipUnless(HAS_YAML, "PyYAML not available")
    def test_boundary_7_is_not_quiet(self):
        """半开区间 [0, 7) 验证: 7 点整不是静默期。"""
        r = evaluate_policy("quiet-hours-00-07", context={"hour": 7})
        self.assertFalse(r["applicable"])

    @unittest.skipUnless(HAS_YAML, "PyYAML not available")
    def test_static_policy_ignores_context(self):
        """static policy 给任意 context 都应 applicable=True (context 不影响结论)。"""
        r = evaluate_policy("max-tools-per-agent", context={"hour": 12})
        self.assertTrue(r["applicable"])


class TestContextEvaluatorAlertIsolation(unittest.TestCase):
    """contextual: alert-context-isolation — messages 含 [SYSTEM_ALERT] 触发。"""

    @unittest.skipUnless(HAS_YAML, "PyYAML not available")
    def test_str_content_with_alert_marker(self):
        msgs = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "[SYSTEM_ALERT] cron failure"},
        ]
        r = evaluate_policy("alert-context-isolation", context={"messages": msgs})
        self.assertTrue(r["applicable"])

    @unittest.skipUnless(HAS_YAML, "PyYAML not available")
    def test_str_content_without_alert(self):
        msgs = [{"role": "user", "content": "hello"}]
        r = evaluate_policy("alert-context-isolation", context={"messages": msgs})
        self.assertFalse(r["applicable"])

    @unittest.skipUnless(HAS_YAML, "PyYAML not available")
    def test_openai_content_blocks(self):
        msgs = [
            {"role": "user", "content": [
                {"type": "text", "text": "[SYSTEM_ALERT] test"}
            ]}
        ]
        r = evaluate_policy("alert-context-isolation", context={"messages": msgs})
        self.assertTrue(r["applicable"])

    @unittest.skipUnless(HAS_YAML, "PyYAML not available")
    def test_messages_missing(self):
        r = evaluate_policy("alert-context-isolation", context={})
        self.assertIsNone(r["applicable"])
        self.assertEqual(r["reason"], "context_missing_messages")

    @unittest.skipUnless(HAS_YAML, "PyYAML not available")
    def test_messages_not_list(self):
        r = evaluate_policy("alert-context-isolation", context={"messages": "oops"})
        self.assertIsNone(r["applicable"])
        self.assertEqual(r["reason"], "context_messages_must_be_list")


class TestContextEvaluatorMultimodal(unittest.TestCase):
    """contextual: multimodal-routing — has_image flag 或 image blocks 触发。"""

    @unittest.skipUnless(HAS_YAML, "PyYAML not available")
    def test_explicit_has_image_true(self):
        r = evaluate_policy("multimodal-routing", context={"has_image": True})
        self.assertTrue(r["applicable"])

    @unittest.skipUnless(HAS_YAML, "PyYAML not available")
    def test_explicit_has_image_false(self):
        r = evaluate_policy("multimodal-routing", context={"has_image": False})
        self.assertFalse(r["applicable"])

    @unittest.skipUnless(HAS_YAML, "PyYAML not available")
    def test_image_block_in_messages(self):
        msgs = [{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;..."}}
        ]}]
        r = evaluate_policy("multimodal-routing", context={"messages": msgs})
        self.assertTrue(r["applicable"])

    @unittest.skipUnless(HAS_YAML, "PyYAML not available")
    def test_text_only_messages(self):
        msgs = [{"role": "user", "content": "plain text"}]
        r = evaluate_policy("multimodal-routing", context={"messages": msgs})
        self.assertFalse(r["applicable"])

    @unittest.skipUnless(HAS_YAML, "PyYAML not available")
    def test_no_image_clue(self):
        r = evaluate_policy("multimodal-routing", context={"actor": "pa"})
        self.assertIsNone(r["applicable"])
        self.assertEqual(r["reason"], "context_missing_has_image_or_messages")


class TestContextEvaluatorDreamBudget(unittest.TestCase):
    """temporal: dream-map-budget — task=='kb_dream' 触发。"""

    @unittest.skipUnless(HAS_YAML, "PyYAML not available")
    def test_task_is_kb_dream(self):
        r = evaluate_policy("dream-map-budget", context={"task": "kb_dream"})
        self.assertTrue(r["applicable"])

    @unittest.skipUnless(HAS_YAML, "PyYAML not available")
    def test_task_is_other(self):
        r = evaluate_policy("dream-map-budget", context={"task": "kb_evening"})
        self.assertFalse(r["applicable"])

    @unittest.skipUnless(HAS_YAML, "PyYAML not available")
    def test_missing_task(self):
        r = evaluate_policy("dream-map-budget", context={})
        self.assertIsNone(r["applicable"])
        self.assertEqual(r["reason"], "context_missing_task")


class TestContextEvaluatorDataCleanKeywords(unittest.TestCase):
    """contextual: data-clean-tool-injection — 关键词匹配。"""

    @unittest.skipUnless(HAS_YAML, "PyYAML not available")
    def test_chinese_keyword(self):
        r = evaluate_policy(
            "data-clean-tool-injection",
            context={"user_text": "请帮我做一下数据清洗"},
        )
        self.assertTrue(r["applicable"])

    @unittest.skipUnless(HAS_YAML, "PyYAML not available")
    def test_english_keyword_case_insensitive(self):
        r = evaluate_policy(
            "data-clean-tool-injection",
            context={"user_text": "Please clean DATA in this file"},
        )
        self.assertTrue(r["applicable"])

    @unittest.skipUnless(HAS_YAML, "PyYAML not available")
    def test_no_keyword(self):
        r = evaluate_policy(
            "data-clean-tool-injection",
            context={"user_text": "write me a haiku"},
        )
        self.assertFalse(r["applicable"])

    @unittest.skipUnless(HAS_YAML, "PyYAML not available")
    def test_non_string_user_text(self):
        r = evaluate_policy(
            "data-clean-tool-injection",
            context={"user_text": 42},
        )
        self.assertIsNone(r["applicable"])
        self.assertEqual(r["reason"], "context_user_text_must_be_str")


class TestContextEvaluatorFallbackChain(unittest.TestCase):
    """contextual: fallback-chain-capability — need_fallback flag 控制。"""

    @unittest.skipUnless(HAS_YAML, "PyYAML not available")
    def test_need_fallback_true(self):
        r = evaluate_policy(
            "fallback-chain-capability",
            context={"need_fallback": True},
        )
        self.assertTrue(r["applicable"])

    @unittest.skipUnless(HAS_YAML, "PyYAML not available")
    def test_need_fallback_false(self):
        r = evaluate_policy(
            "fallback-chain-capability",
            context={"need_fallback": False},
        )
        self.assertFalse(r["applicable"])

    @unittest.skipUnless(HAS_YAML, "PyYAML not available")
    def test_missing_flag(self):
        r = evaluate_policy("fallback-chain-capability", context={})
        self.assertIsNone(r["applicable"])
        self.assertEqual(r["reason"], "context_missing_need_fallback")


class TestContextEvaluatorUnregistered(unittest.TestCase):
    """V37.9.13 P2 契约: 未注册 evaluator 的 policy 返回 applicable=None + 明确 reason。

    P2 阶段性承诺: 不"全部做完"，而是"做一条路径，可扩展"。未登记的 policy 走
    declaration-only fallback，未来可逐条 wire。
    """

    @unittest.skipUnless(HAS_YAML, "PyYAML not available")
    def test_unregistered_contextual_policy_with_context(self):
        """注入一个 P2 evaluator 覆盖范围之外的 contextual policy。"""
        injected = {
            "policies": [{
                "id": "future-unwired-policy", "type": "contextual",
                "scope": [], "rule": "some future rule",
                "rationale": "x", "enforcement_site": "y",
            }]
        }
        r = evaluate_policy("future-unwired-policy", context={"anything": 1},
                            policy_data=injected)
        self.assertTrue(r["found"])
        self.assertIsNone(r["applicable"])
        self.assertEqual(r["reason"], "no_context_evaluator_registered")


class TestContextEvaluatorExceptionSafe(unittest.TestCase):
    """V37.9.13 P2 契约: evaluator 抛异常不得冒泡到调用方。"""

    @unittest.skipUnless(HAS_YAML, "PyYAML not available")
    def test_evaluator_exception_captured(self):
        import engine as _eng
        orig = _eng._CONTEXT_EVALUATORS.get("quiet-hours-00-07")
        def _boom(policy, context):
            raise RuntimeError("synthetic")
        _eng._CONTEXT_EVALUATORS["quiet-hours-00-07"] = _boom
        try:
            r = evaluate_policy("quiet-hours-00-07", context={"hour": 3})
            self.assertTrue(r["found"])
            self.assertIsNone(r["applicable"])
            self.assertTrue(r["reason"].startswith("evaluator_error:"))
            self.assertIn("RuntimeError", r["reason"])
        finally:
            if orig is not None:
                _eng._CONTEXT_EVALUATORS["quiet-hours-00-07"] = orig


class TestMaxToolCallsPolicyWiring(unittest.TestCase):
    """V37.9.13 Phase 4 P2 — 第二条 policy 切换契约 (max-tool-calls-per-task)。

    镜像 TestMaxToolsPolicyWiring，验证 V37.9.12 wiring 模式可扩展性。
    """

    @unittest.skipUnless(HAS_YAML, "PyYAML not available")
    def test_policy_exists_and_static(self):
        r = evaluate_policy("max-tool-calls-per-task")
        self.assertTrue(r["found"])
        self.assertEqual(r["type"], "static")

    @unittest.skipUnless(HAS_YAML, "PyYAML not available")
    def test_limit_is_2(self):
        r = evaluate_policy("max-tool-calls-per-task")
        self.assertEqual(r["limit"], 2)

    @unittest.skipUnless(HAS_YAML, "PyYAML not available")
    def test_limit_matches_config_loader(self):
        """横向一致性: ontology.limit == config_loader.MAX_TOOL_CALLS_PER_TASK。"""
        from config_loader import MAX_TOOL_CALLS_PER_TASK
        r = evaluate_policy("max-tool-calls-per-task")
        self.assertEqual(
            r["limit"], MAX_TOOL_CALLS_PER_TASK,
            f"ontology limit ({r['limit']}) != config_loader "
            f"({MAX_TOOL_CALLS_PER_TASK}) — Phase 4 P2 wiring 双源一致性契约"
        )

    @unittest.skipUnless(HAS_YAML, "PyYAML not available")
    def test_hard_limit_true(self):
        r = evaluate_policy("max-tool-calls-per-task")
        self.assertTrue(r["hard_limit"])

    @unittest.skipUnless(HAS_YAML, "PyYAML not available")
    def test_governance_invariant_is_inv_tool_002(self):
        r = evaluate_policy("max-tool-calls-per-task")
        self.assertEqual(r["governance_invariant"], "INV-TOOL-002")

    @unittest.skipUnless(HAS_YAML, "PyYAML not available")
    def test_enforcement_site_references_tool_proxy(self):
        r = evaluate_policy("max-tool-calls-per-task")
        # policy_ontology.yaml declares "tool_proxy.py main request loop"
        self.assertIn("tool_proxy.py", (r["enforcement_site"] or ""))


if __name__ == "__main__":
    unittest.main()
