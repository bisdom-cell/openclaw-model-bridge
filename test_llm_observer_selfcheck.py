#!/usr/bin/env python3
"""test_llm_observer_selfcheck.py — Observer 自验证 harness 守卫 (研究攻关 #1 Stage 4).

验证 llm_observer_selfcheck.py 的 scorecard 计算 + sabotage suite + CORPUS 绑回
ground_truth 单一真理源 + CLI。

关键守卫:
  - Category A defense rate = 100% (golden case 全被 Layer 1 抓), FP rate = 0% (clean)
  - sabotage: 每个 A case 关掉 only_signal detector → 必漏检 (证 load-bearing 非 tautology)
  - CORPUS A 条目绑回 docs/llm_observer_ground_truth.yaml (gt_id 是 golden_seed)
  - Category B FN 高是【诚实预期】(held-out recall, 非失败) → 不当回归门禁

反向验证 (sabotage 真有效): 给某 A case 故意配一个【不守它】的 detector → load_bearing=False
被守卫抓出。
"""
import os
import subprocess
import sys
import unittest
from unittest import mock

import llm_observer as obs
import llm_observer_selfcheck as sc

_REPO = os.path.dirname(os.path.abspath(__file__))
_GT_PATH = os.path.join(_REPO, "docs", "llm_observer_ground_truth.yaml")

try:
    import yaml  # noqa: F401
    _HAS_YAML = True
except ImportError:  # pragma: no cover
    _HAS_YAML = False


class TestCorpusSchema(unittest.TestCase):
    def test_corpus_nonempty_and_categories(self):
        corpus = sc.get_corpus()
        self.assertTrue(corpus)
        cats = {c["category"] for c in corpus}
        self.assertEqual(cats, {"A", "clean", "B"})

    def test_every_entry_has_required_fields(self):
        for c in sc.get_corpus():
            for k in ("id", "category", "source", "text", "expect_flag"):
                self.assertIn(k, c, f"{c.get('id')}: missing {k}")
            self.assertIsInstance(c["expect_flag"], bool)

    def test_a_entries_have_only_signal(self):
        # Category A 每条标 only_signal (sabotage 用) 且指向真 detector
        for c in sc.get_corpus():
            if c["category"] == "A":
                self.assertIn("only_signal", c, f"{c['id']}: A case needs only_signal")
                self.assertTrue(hasattr(obs, c["only_signal"]),
                                f"{c['id']}: only_signal {c['only_signal']} not a detector")

    def test_a_expect_flag_true_clean_false(self):
        for c in sc.get_corpus():
            if c["category"] == "A":
                self.assertTrue(c["expect_flag"], f"{c['id']}: A must expect_flag=True")
            if c["category"] == "clean":
                self.assertFalse(c["expect_flag"], f"{c['id']}: clean must expect_flag=False")

    def test_get_corpus_returns_copy(self):
        c1 = sc.get_corpus()
        c1[0]["id"] = "MUTATED"
        c2 = sc.get_corpus()
        self.assertNotEqual(c2[0]["id"], "MUTATED")


class TestEvaluate(unittest.TestCase):
    def test_evaluate_entry_correct_flag(self):
        a = next(c for c in sc.get_corpus() if c["category"] == "A")
        r = sc.evaluate_entry(a)
        self.assertTrue(r["flagged"])
        self.assertTrue(r["correct"])

    def test_evaluate_clean_not_flagged(self):
        cl = next(c for c in sc.get_corpus() if c["category"] == "clean")
        r = sc.evaluate_entry(cl)
        self.assertFalse(r["flagged"])
        self.assertTrue(r["correct"])

    def test_evaluate_corpus_full(self):
        results = sc.evaluate_corpus()
        self.assertEqual(len(results), len(sc.get_corpus()))


class TestScorecard(unittest.TestCase):
    def setUp(self):
        self.full = sc.build_scorecard()
        self.sc = self.full["scorecard"]

    def test_defense_rate_100(self):
        # Category A golden 全被 Layer 1 确定性抓到
        self.assertEqual(self.sc["defense_rate"], 1.0,
                         f"defense rate must be 100%, got {self.sc}")
        self.assertEqual(self.sc["a_caught"], self.sc["a_total"])

    def test_fp_rate_0(self):
        self.assertEqual(self.sc["fp_rate"], 0.0, "clean fixtures must not be flagged")

    def test_category_b_fn_is_honest_not_gate(self):
        # Category B FN 高是诚实预期 (held-out recall), 不当回归门禁
        self.assertIsNotNone(self.sc["fn_rate_B"])
        self.assertGreaterEqual(self.sc["b_total"], 1)

    def test_offline_metrics_marked_na(self):
        # detection_latency / calibration 离线不可测, 诚实标 None
        self.assertIsNone(self.sc["detection_latency"])
        self.assertIsNone(self.sc["confidence_calibration"])

    def test_empty_category_rate_none(self):
        empty = sc.compute_scorecard([])
        self.assertIsNone(empty["defense_rate"])
        self.assertIsNone(empty["fp_rate"])


class TestSabotageSuite(unittest.TestCase):
    """证每个 A case 的 only_signal detector load-bearing (关掉即漏检)。"""

    def setUp(self):
        self.sab = sc.run_sabotage_suite()

    def test_covers_all_a_cases(self):
        a_ids = {c["id"] for c in sc.get_corpus() if c["category"] == "A"}
        sab_cases = {s["case"] for s in self.sab}
        self.assertEqual(a_ids, sab_cases)

    def test_all_detectors_load_bearing(self):
        for s in self.sab:
            self.assertTrue(s["baseline_flagged"], f"{s['case']}: baseline must flag")
            self.assertFalse(s["flagged_when_disabled"],
                             f"{s['case']}: disabling {s['detector']} must leak it")
            self.assertTrue(s["load_bearing"], f"{s['case']}/{s['detector']} not load-bearing")

    def test_sabotage_detects_wrong_detector(self):
        # 反向验证: 给一个 A case 配【不守它】的 detector → load_bearing=False 被抓
        # dream_quota 靠 S5 (coherence), 故意标 S1 (pollution, 不守它) → 关 S1 仍 flagged
        fake_corpus = [{
            "id": "wrong_det", "category": "A", "source": "hn",
            "only_signal": "detect_pollution_signal", "expect_flag": True,
            "text": ("## HN 头版精选\n"
                     + "\n".join(f"{i}. T\n   要点：技术内容，详见原文" for i in range(1, 6))),
        }]
        res = sc.run_sabotage_suite(fake_corpus)
        self.assertEqual(len(res), 1)
        # S5 仍在守, 关 S1 不影响 → still flagged → not load-bearing (S1 对此 case 是空检测)
        self.assertFalse(res[0]["load_bearing"],
                         "配错 detector 应被 load_bearing=False 抓出")


@unittest.skipUnless(_HAS_YAML, "PyYAML required for ground-truth tie")
class TestCorpusBoundToGroundTruth(unittest.TestCase):
    """CORPUS Category A 绑回 ground_truth 单一真理源。"""

    @classmethod
    def setUpClass(cls):
        with open(_GT_PATH) as f:
            cls.gt = yaml.safe_load(f)
        cls.by_id = {c["id"]: c for c in cls.gt["cases"]}

    def test_a_gt_ids_are_golden_seeds(self):
        for c in sc.get_corpus():
            if c["category"] == "A" and c.get("gt_id"):
                gtc = self.by_id.get(c["gt_id"])
                self.assertIsNotNone(gtc, f"{c['id']}: gt_id {c['gt_id']} not in ground_truth")
                self.assertTrue(gtc["golden_seed"],
                                f"{c['id']}: gt_id {c['gt_id']} must be golden_seed")

    def test_a_only_signal_in_expected_deterministic(self):
        # only_signal 标的 detector 必须对应该 case ground_truth expected_signal 的确定性子集
        det_to_sig = {
            "detect_pollution_signal": "S1_pollution_signal",
            "detect_credibility_mismatch": "S2_credibility_mismatch",
            "detect_fabrication_phrase": "S3_fabrication_phrase",
            "detect_provenance_gap": "S4_provenance_gap",
            "detect_coherence_structural": "S5_coherence_structural",
        }
        for c in sc.get_corpus():
            if c["category"] == "A" and c.get("gt_id"):
                gtc = self.by_id[c["gt_id"]]
                expected_sig = det_to_sig[c["only_signal"]]
                self.assertIn(expected_sig, gtc["expected_signal"],
                              f"{c['id']}: only_signal {c['only_signal']} → {expected_sig} "
                              f"not in ground_truth expected {gtc['expected_signal']}")


class TestScorecardMarkdown(unittest.TestCase):
    def test_markdown_has_key_sections(self):
        full = sc.build_scorecard()
        md = sc.build_scorecard_markdown(full["scorecard"], full["sabotage"], full["results"])
        for token in ("defense rate", "false-positive", "Category B", "sabotage 验证",
                      "audit-as-regression"):
            self.assertIn(token, md)

    def test_saved_scorecard_exists(self):
        # docs/llm_observer_scorecard.md 应已生成并含核心指标
        path = os.path.join(_REPO, "docs", "llm_observer_scorecard.md")
        self.assertTrue(os.path.exists(path), "scorecard artifact must exist (run --save)")
        with open(path, encoding="utf-8") as f:
            content = f.read()
        self.assertIn("defense rate", content)
        self.assertIn("100%", content)   # defense 100


class TestStage4SourceGuards(unittest.TestCase):
    def test_stage4_marker(self):
        with open(os.path.join(_REPO, "llm_observer_selfcheck.py"), encoding="utf-8") as f:
            src = f.read()
        self.assertIn("Stage 4", src)
        self.assertIn("audit-as-regression", src)
        self.assertIn("held-out recall", src)

    def test_reuses_observer_not_reimplements(self):
        with open(os.path.join(_REPO, "llm_observer_selfcheck.py"), encoding="utf-8") as f:
            src = f.read()
        self.assertIn("import llm_observer", src)
        self.assertIn("run_prefilter", src)
        # 不是另起新 harness: 不 import adversarial_chaos_audit (复用概念非代码)
        self.assertNotIn("import adversarial_chaos_audit", src)

    def test_category_b_fn_not_a_regression_gate(self):
        # 诚实纪律: Category B 高 FN 不能让 main exit 1 (它是预期盲区非失败)
        with open(os.path.join(_REPO, "llm_observer_selfcheck.py"), encoding="utf-8") as f:
            src = f.read()
        self.assertIn("Category B FN 不算失败", src)


class TestCli(unittest.TestCase):
    def test_cli_default_scorecard(self):
        r = subprocess.run([sys.executable, os.path.join(_REPO, "llm_observer_selfcheck.py")],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0)   # defense 100 + FP 0 + load-bearing
        self.assertIn("defense rate", r.stdout)

    def test_cli_json(self):
        r = subprocess.run([sys.executable, os.path.join(_REPO, "llm_observer_selfcheck.py"),
                            "--json"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0)
        import json
        data = json.loads(r.stdout)
        self.assertEqual(data["scorecard"]["defense_rate"], 1.0)

    def test_cli_sabotage(self):
        r = subprocess.run([sys.executable, os.path.join(_REPO, "llm_observer_selfcheck.py"),
                            "--sabotage"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0)
        self.assertIn("✅", r.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
