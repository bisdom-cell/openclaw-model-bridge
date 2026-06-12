#!/usr/bin/env python3
"""test_minimal_runtime.py — V37.9.144 P1(b) examples/minimal_runtime 守卫.

外部评审2 "10 分钟最小例子" 入口的契约:
  1. demo 真 subprocess 跑通 (exit 0) + 4 步 marker 全出现
  2. golden trace 自校验 MATCH (确定性决策与提交参考一致)
  3. 无 PyYAML 环境优雅降级 (policy 步 SKIP + 安装提示, 仍 exit 0) — 依赖边界活演示
  4. golden_trace.json 已提交且 schema 完整
  5. 反向验证: 篡改 golden 的确定性字段 → demo exit 1 (自校验真有效, 非装饰)
  6. 主 README 双入口链接存在 (minimal_runtime + minimal_consumer)
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.abspath(__file__))
DEMO_DIR = os.path.join(REPO, "examples", "minimal_runtime")
DEMO = os.path.join(DEMO_DIR, "minimal_runtime.py")
GOLDEN = os.path.join(DEMO_DIR, "golden_trace.json")


def _run_demo(*args, env=None, timeout=120):
    return subprocess.run([sys.executable, DEMO, *args],
                          capture_output=True, text=True,
                          env=env or dict(os.environ), timeout=timeout)


class TestDemoEndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.r = _run_demo()

    def test_exit_zero(self):
        self.assertEqual(self.r.returncode, 0,
                         f"demo 应 exit 0, stderr={self.r.stderr[-500:]}")

    def test_all_four_step_markers(self):
        for marker in ("[1/4] provider registry", "[2/4] tool governance",
                       "[3/4] policy (ontology layer)", "[4/4] SLO mini-stats"):
            self.assertIn(marker, self.r.stdout, f"缺步骤 marker: {marker}")

    def test_golden_match_line(self):
        self.assertIn("golden trace: MATCH", self.r.stdout)

    def test_tool_governance_three_categories_reported(self):
        # 语义守卫: 白名单拒绝 / 硬截断 / 自定义注入 三类必须分开展示
        # (V37.9.144 开发期发现 browser_navigate 被误标 rejected 的教训)
        self.assertIn("whitelist rejected", self.r.stdout)
        self.assertIn("hard-cap truncated", self.r.stdout)
        self.assertIn("custom injected", self.r.stdout)

    def test_json_flag_outputs_trace(self):
        r = _run_demo("--json")
        self.assertEqual(r.returncode, 0)
        self.assertIn('"deterministic"', r.stdout)


class TestGoldenTraceCommitted(unittest.TestCase):
    def setUp(self):
        self.assertTrue(os.path.isfile(GOLDEN), "golden_trace.json 必须已提交")
        self.golden = json.load(open(GOLDEN, encoding="utf-8"))

    def test_schema_deterministic_fields(self):
        det = self.golden["deterministic"]
        for key in ("tool_input_count", "tool_output_count", "final_tool_names",
                    "whitelist_rejected_count", "cap_truncated", "injected_custom",
                    "fallback_chain", "best_for_text_prefer_reasoning", "policy"):
            self.assertIn(key, det, f"golden deterministic 缺字段 {key}")

    def test_hard_cap_holds(self):
        det = self.golden["deterministic"]
        self.assertEqual(det["tool_input_count"], 24)
        self.assertLessEqual(det["tool_output_count"], 12,
                             "工具治理硬截断 ≤12 (INV-TOOL-001 同款契约)")

    def test_policy_limit_when_present(self):
        pol = self.golden["deterministic"]["policy"]
        if "limit" in pol:
            self.assertEqual(pol["limit"], 12)
            self.assertEqual(pol["policy_id"], "max-tools-per-agent")

    def test_timings_not_in_deterministic(self):
        # 时延天然不确定 — 必须只存在于 non_deterministic, 不参与 golden 比对
        self.assertNotIn("step_timings_ms", self.golden["deterministic"])
        self.assertIn("step_timings_ms", self.golden["non_deterministic"])


class TestNoPyyamlGracefulDegrade(unittest.TestCase):
    """模拟评审者无 PyYAML 环境: policy 步 SKIP + 提示, demo 仍 exit 0."""

    def setUp(self):
        self.blocker = tempfile.mkdtemp()
        with open(os.path.join(self.blocker, "yaml.py"), "w") as f:
            f.write("raise ImportError('simulated missing PyYAML')\n")
        self.env = dict(os.environ)
        self.env["PYTHONPATH"] = self.blocker

    def test_exit_zero_without_yaml(self):
        r = _run_demo(env=self.env)
        self.assertEqual(r.returncode, 0,
                         f"无 yaml 应优雅降级 exit 0, stdout={r.stdout[-500:]}")
        self.assertIn("SKIP", r.stdout)
        self.assertIn("pyyaml", r.stdout.lower())

    def test_golden_check_skips_policy_comparison(self):
        r = _run_demo(env=self.env)
        self.assertIn("golden trace: MATCH", r.stdout)
        self.assertIn("policy 比对跳过", r.stdout)


class TestReverseValidationGoldenIsReal(unittest.TestCase):
    """sabotage golden 确定性字段 → demo 必须 exit 1 (自校验真有效)."""

    def test_tampered_golden_fails_loud(self):
        orig = open(GOLDEN, encoding="utf-8").read()
        data = json.loads(orig)
        data["deterministic"]["fallback_chain"] = ["SABOTAGED_PROVIDER"]
        try:
            with open(GOLDEN, "w", encoding="utf-8") as f:
                json.dump(data, f)
            r = _run_demo()
            self.assertEqual(r.returncode, 1, "golden 被篡改时 demo 必须 exit 1")
            self.assertIn("MISMATCH", r.stdout)
        finally:
            with open(GOLDEN, "w", encoding="utf-8") as f:
                f.write(orig)


class TestReadmeWiring(unittest.TestCase):
    def test_example_readme_exists(self):
        self.assertTrue(os.path.isfile(os.path.join(DEMO_DIR, "README.md")))

    def test_main_readme_links_both_entry_points(self):
        readme = open(os.path.join(REPO, "README.md"), encoding="utf-8").read()
        self.assertIn("examples/minimal_runtime/", readme,
                      "主 README 必须链接 10 分钟最小入口")
        self.assertIn("examples/minimal_consumer/", readme,
                      "主 README 必须链接 governance 消费方入口")

    def test_demo_forces_ontology_mode_off_for_determinism(self):
        src = open(DEMO, encoding="utf-8").read()
        self.assertIn('os.environ["ONTOLOGY_MODE"] = "off"', src,
                      "core 步骤必须锁 ONTOLOGY_MODE=off 保证跨环境确定性")


if __name__ == "__main__":
    unittest.main(verbosity=1)
