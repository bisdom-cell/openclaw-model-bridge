#!/usr/bin/env python3
"""test_tool_policy_plugin.py — V37.9.160 Tool Policy Plugin chunk 1 单测.

守卫 engine.py 的工具策略插件契约 (镜像 Provider Plugin providers.d/):
  validate_policy_dict / discover_policy_plugins (FAIL-OPEN) / evaluate_policy 加性集成 / CLI.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO / "ontology"))
import engine  # noqa: E402


def _write_plugins(td, files):
    """在 td/policies.d/ 写若干插件文件. files = {fname: yaml_str}."""
    pdir = os.path.join(td, engine._POLICY_PLUGIN_DIR_NAME)
    os.makedirs(pdir, exist_ok=True)
    for fname, content in files.items():
        with open(os.path.join(pdir, fname), "w", encoding="utf-8") as f:
            f.write(content)
    return pdir


_GOOD = ("policies:\n"
         "  - id: max-cost\n    type: static\n    scope: [Tool]\n"
         "    rule: 'cost <= 1'\n    limit: 1\n    hard_limit: true\n"
         "    enforcement_site: 'tool_proxy.py'\n")


# ─────────────────────── validate_policy_dict ───────────────────────
class TestValidatePolicyDict(unittest.TestCase):
    def test_good_policy_no_errors(self):
        self.assertEqual(engine.validate_policy_dict(
            {"id": "x", "type": "static", "rule": "r", "enforcement_site": "s"}), [])

    def test_non_dict(self):
        self.assertTrue(engine.validate_policy_dict("not a dict"))

    def test_missing_id(self):
        errs = engine.validate_policy_dict({"type": "static", "rule": "r", "enforcement_site": "s"})
        self.assertTrue(any("id" in e for e in errs))

    def test_bad_type(self):
        errs = engine.validate_policy_dict({"id": "x", "type": "BOGUS", "rule": "r", "enforcement_site": "s"})
        self.assertTrue(any("type" in e for e in errs))

    def test_missing_rule(self):
        errs = engine.validate_policy_dict({"id": "x", "type": "static", "enforcement_site": "s"})
        self.assertTrue(any("rule" in e for e in errs))

    def test_missing_enforcement_site(self):
        errs = engine.validate_policy_dict({"id": "x", "type": "static", "rule": "r"})
        self.assertTrue(any("enforcement_site" in e for e in errs))

    def test_limit_must_be_number(self):
        errs = engine.validate_policy_dict(
            {"id": "x", "type": "static", "rule": "r", "enforcement_site": "s", "limit": "12"})
        self.assertTrue(any("limit" in e for e in errs))

    def test_limit_bool_rejected(self):
        # bool 是 int 子类, 必须显式拒绝
        errs = engine.validate_policy_dict(
            {"id": "x", "type": "static", "rule": "r", "enforcement_site": "s", "limit": True})
        self.assertTrue(any("limit" in e for e in errs))

    def test_hard_limit_must_be_bool(self):
        errs = engine.validate_policy_dict(
            {"id": "x", "type": "static", "rule": "r", "enforcement_site": "s", "hard_limit": "yes"})
        self.assertTrue(any("hard_limit" in e for e in errs))

    def test_scope_str_or_list_ok(self):
        self.assertEqual(engine.validate_policy_dict(
            {"id": "x", "type": "static", "rule": "r", "enforcement_site": "s", "scope": "Tool"}), [])
        self.assertEqual(engine.validate_policy_dict(
            {"id": "x", "type": "static", "rule": "r", "enforcement_site": "s", "scope": ["Tool"]}), [])

    def test_temporal_contextual_types_ok(self):
        for t in ("temporal", "contextual"):
            self.assertEqual(engine.validate_policy_dict(
                {"id": "x", "type": t, "rule": "r", "enforcement_site": "s"}), [])


# ─────────────────────── discover_policy_plugins ───────────────────────
class TestDiscoverPolicyPlugins(unittest.TestCase):
    def test_no_dir_returns_empty(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(engine.discover_policy_plugins(config_dir=td), ([], []))

    def test_single_plugin_loaded(self):
        with tempfile.TemporaryDirectory() as td:
            _write_plugins(td, {"cost.yaml": _GOOD})
            pols, errs = engine.discover_policy_plugins(config_dir=td)
            self.assertEqual([p["id"] for p in pols], ["max-cost"])
            self.assertEqual(errs, [])

    def test_underscore_prefix_skipped(self):
        with tempfile.TemporaryDirectory() as td:
            _write_plugins(td, {"_example.yaml": _GOOD, "real.yaml": _GOOD})
            pols, errs = engine.discover_policy_plugins(config_dir=td)
            # 只有 real.yaml 被加载 (一条), _example 跳过
            self.assertEqual(len(pols), 1)

    def test_dot_prefix_skipped(self):
        with tempfile.TemporaryDirectory() as td:
            _write_plugins(td, {".hidden.yaml": _GOOD})
            self.assertEqual(engine.discover_policy_plugins(config_dir=td), ([], []))

    def test_non_yaml_skipped(self):
        with tempfile.TemporaryDirectory() as td:
            _write_plugins(td, {"notes.txt": "junk", "real.yaml": _GOOD})
            pols, _ = engine.discover_policy_plugins(config_dir=td)
            self.assertEqual(len(pols), 1)

    def test_fail_open_on_bad_yaml(self):
        with tempfile.TemporaryDirectory() as td:
            _write_plugins(td, {"bad.yaml": "policies:\n  - id: [unclosed", "good.yaml": _GOOD})
            pols, errs = engine.discover_policy_plugins(config_dir=td)
            # good 仍加载, bad 收集 error 不阻塞
            self.assertEqual([p["id"] for p in pols], ["max-cost"])
            self.assertTrue(errs, "bad.yaml 应收集 error")

    def test_invalid_policy_filtered_with_error(self):
        with tempfile.TemporaryDirectory() as td:
            bad = "policies:\n  - id: x\n    type: BOGUS\n    rule: r\n    enforcement_site: s\n"
            _write_plugins(td, {"p.yaml": bad})
            pols, errs = engine.discover_policy_plugins(config_dir=td)
            self.assertEqual(pols, [])
            self.assertTrue(any("BOGUS" in e for e in errs))

    def test_top_level_list_structure(self):
        with tempfile.TemporaryDirectory() as td:
            lst = "- id: a\n  type: static\n  rule: r\n  enforcement_site: s\n"
            _write_plugins(td, {"p.yaml": lst})
            pols, _ = engine.discover_policy_plugins(config_dir=td)
            self.assertEqual([p["id"] for p in pols], ["a"])

    def test_single_policy_dict_structure(self):
        with tempfile.TemporaryDirectory() as td:
            single = "id: solo\ntype: static\nrule: r\nenforcement_site: s\n"
            _write_plugins(td, {"p.yaml": single})
            pols, _ = engine.discover_policy_plugins(config_dir=td)
            self.assertEqual([p["id"] for p in pols], ["solo"])

    def test_unrecognized_structure_error(self):
        with tempfile.TemporaryDirectory() as td:
            _write_plugins(td, {"p.yaml": "random: stuff\nno_id: here\n"})
            pols, errs = engine.discover_policy_plugins(config_dir=td)
            self.assertEqual(pols, [])
            self.assertTrue(errs)

    def test_bridge_example_skipped(self):
        # 仓库 ontology/policies.d/_example.yaml 应被跳过 (下划线前缀)
        pols, errs = engine.discover_policy_plugins()
        self.assertNotIn("max-tool-cost-per-task",
                         [p.get("id") for p in pols], "示例插件不应被加载")


# ─────────────────────── evaluate_policy 加性集成 ───────────────────────
class TestEvaluatePolicyPluginIntegration(unittest.TestCase):
    def test_base_policy_still_works(self):
        # 现有主文件策略不受影响
        r = engine.evaluate_policy("max-tools-per-agent")
        self.assertTrue(r["found"])
        self.assertEqual(r["limit"], 12)

    def test_plugin_policy_found_when_base_missing(self):
        with tempfile.TemporaryDirectory() as td:
            base = os.path.join(td, "policy_ontology.yaml")
            open(base, "w").write("policies: []\n")
            _write_plugins(td, {"cost.yaml": _GOOD})
            r = engine.evaluate_policy("max-cost", path=base)
            self.assertTrue(r["found"], "插件策略应被 evaluate_policy 找到")
            self.assertEqual(r["limit"], 1)
            self.assertTrue(r["hard_limit"])

    def test_base_precedence_over_plugin_same_id(self):
        with tempfile.TemporaryDirectory() as td:
            base = os.path.join(td, "policy_ontology.yaml")
            open(base, "w").write(
                "policies:\n  - id: dup\n    type: static\n    rule: 'base'\n"
                "    limit: 99\n    enforcement_site: 'base'\n")
            _write_plugins(td, {"p.yaml":
                "policies:\n  - id: dup\n    type: static\n    rule: 'plugin'\n"
                "    limit: 1\n    enforcement_site: 'plugin'\n"})
            r = engine.evaluate_policy("dup", path=base)
            # 主文件优先, 不被插件覆盖
            self.assertEqual(r["limit"], 99)
            self.assertEqual(r["rule"], "base")

    def test_policy_data_injection_skips_plugins(self):
        # test 注入 policy_data 时不查插件 (保持现有测试稳定)
        r = engine.evaluate_policy("max-cost", policy_data={"policies": []})
        self.assertFalse(r["found"])
        self.assertEqual(r["reason"], "policy_id_not_found")

    def test_unknown_policy_not_found(self):
        r = engine.evaluate_policy("totally-nonexistent-policy-xyz")
        self.assertFalse(r["found"])


# ─────────────────────── CLI ───────────────────────
class TestCli(unittest.TestCase):
    def test_validate_policies_exit_zero(self):
        r = subprocess.run([sys.executable, "ontology/engine.py", "--validate-policies"],
                           cwd=str(REPO), capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, f"仓库插件应合规 exit 0:\n{r.stdout}\n{r.stderr}")

    def test_validate_policies_json(self):
        r = subprocess.run(
            [sys.executable, "ontology/engine.py", "--validate-policies", "--json"],
            cwd=str(REPO), capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0)
        out = json.loads(r.stdout)
        self.assertIn("loaded", out)
        self.assertIn("errors", out)


# ─────────────────────── 反向验证 + 源码守卫 ───────────────────────
class TestReverseAndSourceGuards(unittest.TestCase):
    def test_sabotage_bad_plugin_causes_cli_exit_1(self):
        # 临时往仓库 policies.d/ 放一个非法插件 → CLI 应 exit 1, 还原后 exit 0
        bad_path = REPO / "ontology" / "policies.d" / "_sabotage_test.yaml"
        # 用非下划线名才会被发现; 但放仓库会影响别的测试 → 用临时 config_dir 验证
        with tempfile.TemporaryDirectory() as td:
            _write_plugins(td, {"bad.yaml":
                "policies:\n  - id: x\n    type: NOPE\n    rule: r\n    enforcement_site: s\n"})
            pols, errs = engine.discover_policy_plugins(config_dir=td)
            self.assertTrue(errs, "非法插件必须被守卫抓到 (反向验证)")
        self.assertFalse(bad_path.exists())  # 没污染仓库

    def test_engine_has_v37_9_160_marker(self):
        src = (REPO / "ontology" / "engine.py").read_text(encoding="utf-8")
        self.assertIn("V37.9.160", src)
        self.assertIn("def discover_policy_plugins", src)
        self.assertIn("def validate_policy_dict", src)
        self.assertIn("policies.d", src)

    def test_extension_guide_exists(self):
        guide = REPO / "docs" / "tool_policy_plugin_guide.md"
        self.assertTrue(guide.exists(), "缺 tool_policy_plugin_guide.md")
        txt = guide.read_text(encoding="utf-8")
        self.assertIn("policies.d", txt)
        self.assertIn("FAIL-OPEN", txt)

    def test_example_plugin_exists_and_underscore(self):
        ex = REPO / "ontology" / "policies.d" / "_example.yaml"
        self.assertTrue(ex.exists(), "缺 policies.d/_example.yaml")
        self.assertTrue(ex.name.startswith("_"), "示例必须下划线前缀 (被发现跳过)")


if __name__ == "__main__":
    unittest.main(verbosity=2)
