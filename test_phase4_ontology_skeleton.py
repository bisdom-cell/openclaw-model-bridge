#!/usr/bin/env python3
"""
test_phase4_ontology_skeleton.py — V37.9.9 Phase 4 骨架守卫

锁定 domain_ontology.yaml + policy_ontology.yaml 的最小结构契约，防止未来
编辑意外破坏 Phase 4 wiring 的前置条件。

当前阶段: 声明层（Phase 4 Step 0）
  - 不测引擎行为（引擎尚未实现 load_domain_ontology / evaluate_policy）
  - 只测 YAML 结构完整、六域声明齐全、交叉引用一致

下次 Phase 4 P1+ 推进时，新增:
  - test_engine_load_domain_ontology.py（引擎实际 API）
  - test_policy_engine_evaluation.py（policy 运行时判定）
"""

import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)


def _load_yaml(name):
    try:
        import yaml
    except ImportError:
        raise unittest.SkipTest("PyYAML not available")
    path = os.path.join(_HERE, "ontology", name)
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


class TestDomainOntologySkeleton(unittest.TestCase):
    """Phase 4 骨架: domain_ontology.yaml 必须声明六域。"""

    def setUp(self):
        self.onto = _load_yaml("domain_ontology.yaml")

    def test_six_domains_declared(self):
        domains = self.onto.get("domains") or {}
        expected = {"Actor", "Tool", "Resource", "Task", "Provider", "Memory"}
        actual = set(domains.keys())
        self.assertEqual(
            actual, expected,
            f"六域声明漂移 — 缺: {expected - actual} / 多: {actual - expected}"
        )

    def test_each_domain_has_description(self):
        domains = self.onto.get("domains") or {}
        for name, dom in domains.items():
            self.assertIn("description", dom, f"{name} 缺 description")
            self.assertIsInstance(dom["description"], str)
            self.assertGreater(len(dom["description"]), 10)

    def test_actor_has_required_instances(self):
        """Actor 必须声明至少 user / pa / cron_job 三种核心实例。"""
        actor = self.onto["domains"]["Actor"]
        ids = {inst["id"] for inst in actor.get("instances", [])}
        required = {"user", "pa", "cron_job"}
        self.assertTrue(
            required.issubset(ids),
            f"Actor 缺核心实例: {required - ids}"
        )

    def test_tool_domain_references_source_of_truth(self):
        """Tool 域必须引用 tool_ontology.yaml 作为权威源（防漂移）。"""
        tool = self.onto["domains"]["Tool"]
        self.assertEqual(tool.get("source_of_truth"), "tool_ontology.yaml")

    def test_provider_domain_references_source_of_truth(self):
        provider = self.onto["domains"]["Provider"]
        self.assertEqual(provider.get("source_of_truth"), "providers.d/*.yaml")

    def test_memory_layers_match_memory_plane_design(self):
        """Memory 四层必须对齐 memory_plane.py 设计。"""
        memory = self.onto["domains"]["Memory"]
        layer_ids = {L["id"] for L in memory.get("layers", [])}
        expected = {"kb_semantic", "multimodal", "preferences", "state"}
        self.assertEqual(layer_ids, expected)

    def test_has_phase4_meta(self):
        meta = self.onto.get("meta") or {}
        self.assertEqual(meta.get("status"), "declaration_only")
        self.assertIn("Phase 4", meta.get("phase", ""))


class TestPolicyOntologySkeleton(unittest.TestCase):
    """Phase 4 骨架: policy_ontology.yaml 三类策略分类 + 最小集合。"""

    def setUp(self):
        self.onto = _load_yaml("policy_ontology.yaml")

    def test_three_policy_types_declared(self):
        types = self.onto.get("policy_types") or {}
        self.assertEqual(
            set(types.keys()), {"static", "temporal", "contextual"},
            "policy_types 必须恰好三类 (static/temporal/contextual)"
        )

    def test_policies_list_non_empty(self):
        policies = self.onto.get("policies") or []
        self.assertGreaterEqual(
            len(policies), 5,
            "骨架期至少声明 5 条 policy 作为 Phase 4 P2 迁移起点"
        )

    def test_every_policy_has_required_fields(self):
        required = {"id", "type", "scope", "rule", "enforcement_site", "rationale"}
        for pol in self.onto.get("policies", []):
            actual = set(pol.keys())
            missing = required - actual
            self.assertFalse(
                missing,
                f"policy {pol.get('id', '<unknown>')} 缺字段: {missing}"
            )

    def test_policy_type_is_valid(self):
        valid = {"static", "temporal", "contextual"}
        for pol in self.onto.get("policies", []):
            self.assertIn(
                pol["type"], valid,
                f"policy {pol['id']} 的 type={pol['type']!r} 不在 {valid}"
            )

    def test_policy_ids_unique(self):
        ids = [p["id"] for p in self.onto.get("policies", [])]
        self.assertEqual(len(ids), len(set(ids)), f"policy id 重复: {ids}")

    def test_hard_limits_are_static_type(self):
        """hard_limit=true 的 policy 应该是 static 类型（不应随时间/上下文变化）。"""
        for pol in self.onto.get("policies", []):
            if pol.get("hard_limit"):
                self.assertEqual(
                    pol["type"], "static",
                    f"policy {pol['id']} hard_limit=true 但 type={pol['type']} "
                    f"(hard_limit 只适用 static policy)"
                )

    def test_alert_context_isolation_has_ordering_constraint(self):
        """INV-PA-001 的核心约束: filter_system_alerts 必须在 truncate 之前。"""
        pols = {p["id"]: p for p in self.onto.get("policies", [])}
        self.assertIn("alert-context-isolation", pols)
        pol = pols["alert-context-isolation"]
        self.assertIn(
            "ordering_constraint", pol,
            "alert-context-isolation 必须有 ordering_constraint（V37.4.3 教训）"
        )
        self.assertIn("filter_system_alerts", pol["ordering_constraint"])
        self.assertIn("truncate", pol["ordering_constraint"])


class TestCrossOntologyConsistency(unittest.TestCase):
    """domain + policy + governance 三文件交叉一致性。"""

    def test_policy_invariant_refs_exist_in_governance(self):
        """policy 引用的 governance_invariant 必须真在 governance_ontology.yaml 里。"""
        policy_onto = _load_yaml("policy_ontology.yaml")
        gov_onto = _load_yaml("governance_ontology.yaml")
        gov_ids = set()
        for inv in gov_onto.get("invariants", []):
            if inv.get("id"):
                gov_ids.add(inv["id"])

        for pol in policy_onto.get("policies", []):
            ref = pol.get("governance_invariant")
            if ref is None:
                continue
            self.assertIn(
                ref, gov_ids,
                f"policy {pol['id']} 引用的 governance_invariant={ref} "
                f"在 governance_ontology.yaml 中不存在（可能改名/删除 → 漂移）"
            )


if __name__ == "__main__":
    unittest.main()
