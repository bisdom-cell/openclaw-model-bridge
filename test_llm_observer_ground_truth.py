#!/usr/bin/env python3
"""test_llm_observer_ground_truth.py — schema guard for the LLM-Observer ground-truth set.

研究攻关 #1 Stage 1 (V37.9.193). docs/llm_observer_ground_truth.yaml 是 Observer
fail-plausible 检测器的带标注验证集; 它的标签 load-bearing (驱动 Stage 2+ 的 sabotage
测试)。一个未验证的 ground-truth 表 = 一个可能静默漂移的表 (typo'd enum / 缺字段 /
expected_signal 引用了不存在的信号 / summary 计数与实际不符)。本守卫保证它 well-formed,
并把 schema 契约 (design doc §4.2/§4.4) 机器化。

依赖边界: 解析 YAML 需 PyYAML (ontology 层依赖, V37.9.144)。parse-dependent 测试在
缺 PyYAML 时 skip (镜像 test_ontology_packaging); 文本级守卫恒跑。

反向验证 (手动 sabotage, 收工已确认真有效):
  - 把某 golden seed 的 fail_plausible 改成 "no" → test_golden_seeds_are_fail_plausible FAIL
  - expected_signal 加一个未定义信号 "S9_foo" → test_expected_signals_are_defined FAIL
  - summary.total_labeled 改错 → test_summary_counts_match_cases FAIL
"""
import os
import re
import unittest

_REPO = os.path.dirname(os.path.abspath(__file__))
_GT_PATH = os.path.join(_REPO, "docs", "llm_observer_ground_truth.yaml")
_DESIGN_PATH = os.path.join(_REPO, "docs", "llm_observer_design.md")

try:
    import yaml  # noqa: F401
    _HAS_YAML = True
except ImportError:  # pragma: no cover - exercised only in yaml-less envs
    _HAS_YAML = False

_REQUIRED_CASE_FIELDS = (
    "id", "file", "title", "taxonomy_class", "fail_plausible", "llm_fabrication",
    "observable_artifact", "discovery_channel", "observer_in_scope", "in_scope_reason",
    "expected_signal", "category", "golden_seed", "paper_class_ref", "paper_canonical",
    "silence_span", "notes",
)
_TAXONOMY_CLASSES = {"A", "B", "C", "D", "E"}
_TRISTATE = {"yes", "no", "partial"}
_CATEGORIES = {"A", "B", None}
_DISCOVERY_CHANNELS = {
    "user-view", "check", "log-forensics", "self-observation", "target-env",
}


def _load():
    import yaml
    with open(_GT_PATH) as f:
        return yaml.safe_load(f)


@unittest.skipUnless(_HAS_YAML, "PyYAML required (ontology-layer dependency); install: pip install pyyaml")
class TestGroundTruthSchema(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = _load()
        cls.cases = cls.data["cases"]

    # ── top-level structure ──────────────────────────────────────────────
    def test_top_level_keys_present(self):
        for k in ("version", "schema_source", "study_cutoff", "signals",
                  "categories", "summary", "cases", "known_gaps"):
            self.assertIn(k, self.data, f"missing top-level key {k}")

    def test_schema_source_points_to_design_doc(self):
        self.assertIn("llm_observer_design.md", self.data["schema_source"])
        self.assertTrue(os.path.isfile(_DESIGN_PATH), "design doc must exist")

    def test_signals_dictionary_nonempty(self):
        sig = self.data["signals"]
        self.assertIsInstance(sig, dict)
        # the 5 deterministic S-signals + the 4 layer2 judges (design doc §3.1)
        for s in ("S1_pollution_signal", "S2_credibility_mismatch", "S3_fabrication_phrase",
                  "S4_provenance_gap", "S5_coherence_structural",
                  "layer2_grounding", "layer2_intent_alignment",
                  "layer2_pollution_evidence", "layer2_fabricated_success"):
            self.assertIn(s, sig, f"signal {s} must be defined")

    # ── per-case schema ──────────────────────────────────────────────────
    def test_every_case_has_required_fields(self):
        for c in self.cases:
            for f in _REQUIRED_CASE_FIELDS:
                self.assertIn(f, c, f"case {c.get('id', '?')} missing field {f}")

    def test_enum_fields_valid(self):
        for c in self.cases:
            cid = c["id"]
            self.assertIn(c["taxonomy_class"], _TAXONOMY_CLASSES, cid)
            self.assertIn(c["fail_plausible"], _TRISTATE, cid)
            self.assertIn(c["llm_fabrication"], _TRISTATE, cid)
            self.assertIn(c["observer_in_scope"], _TRISTATE, cid)
            self.assertIn(c["observable_artifact"]["user_facing"], _TRISTATE, cid)
            self.assertIn(c["category"], _CATEGORIES, cid)
            self.assertIn(c["discovery_channel"], _DISCOVERY_CHANNELS, cid)
            self.assertIsInstance(c["golden_seed"], bool, cid)
            self.assertIsInstance(c["paper_canonical"], bool, cid)

    def test_tristate_fields_are_strings_not_yaml_booleans(self):
        # YAML 1.1 footgun: unquoted yes/no parse as bool. Guard that the
        # data file keeps these quoted (str) so Stage 2 consumers see one type.
        for c in self.cases:
            self.assertIsInstance(c["fail_plausible"], str, c["id"])
            self.assertIsInstance(c["observer_in_scope"], str, c["id"])
            self.assertIsInstance(c["observable_artifact"]["user_facing"], str, c["id"])

    def test_case_ids_unique(self):
        ids = [c["id"] for c in self.cases]
        self.assertEqual(len(ids), len(set(ids)), "duplicate case id")

    def test_every_referenced_case_file_exists(self):
        for c in self.cases:
            path = os.path.join(_REPO, c["file"])
            self.assertTrue(os.path.isfile(path), f"{c['id']}: file not found {c['file']}")

    # ── load-bearing contracts (drift guards) ────────────────────────────
    def test_expected_signals_are_defined(self):
        # MR-8: expected_signal must only reference signals in the signals dict.
        defined = set(self.data["signals"].keys())
        for c in self.cases:
            for sig in c["expected_signal"]:
                self.assertIn(sig, defined,
                              f"{c['id']}: expected_signal '{sig}' not in signals dict")

    def test_in_scope_implies_signals_out_of_scope_implies_empty(self):
        # in-scope (yes/partial) cases must declare which signals should fire;
        # out-of-scope (no) cases must have no expected_signal (honest boundary).
        for c in self.cases:
            if c["observer_in_scope"] == "no":
                self.assertEqual(c["expected_signal"], [],
                                 f"{c['id']}: out-of-scope but has expected_signal")
            else:
                self.assertTrue(c["expected_signal"],
                                f"{c['id']}: in-scope ({c['observer_in_scope']}) but no expected_signal")

    def test_category_consistency(self):
        # out-of-scope -> category null; A/B regression/exploratory targets must
        # have a fail-plausible signal to detect (fail_plausible != "no").
        for c in self.cases:
            if c["observer_in_scope"] == "no":
                self.assertIsNone(c["category"],
                                  f"{c['id']}: out-of-scope must have category null")
            if c["category"] in ("A", "B"):
                self.assertNotEqual(c["fail_plausible"], "no",
                                    f"{c['id']}: category {c['category']} but fail_plausible=no")

    # ── golden seeds (Category A regression core) ────────────────────────
    def test_golden_seeds_exactly_five(self):
        golden = [c for c in self.cases if c["golden_seed"]]
        self.assertEqual(len(golden), 5,
                         f"design doc §4.5 specifies 5 golden seeds, found {len(golden)}")

    def test_golden_seeds_are_category_A(self):
        for c in self.cases:
            if c["golden_seed"]:
                self.assertEqual(c["category"], "A", f"{c['id']}: golden seed must be Category A")

    def test_golden_seeds_are_fail_plausible(self):
        # sabotage anchor: corrupting a golden seed's fail_plausible to "no" fails here.
        for c in self.cases:
            if c["golden_seed"]:
                self.assertIn(c["fail_plausible"], ("yes", "partial"),
                              f"{c['id']}: golden seed must be fail_plausible yes/partial")

    def test_category_A_regression_set_nonempty(self):
        cat_a = [c for c in self.cases if c["category"] == "A"]
        self.assertGreaterEqual(len(cat_a), 5, "Category A regression set must hold the golden seeds")

    def test_golden_seeds_cover_paper_D_classes(self):
        # the 5 golden seeds should reference the paper's D-class incidents (§4.4).
        refs = " ".join(c["paper_class_ref"] for c in self.cases if c["golden_seed"])
        for d in ("D1", "D2", "D3"):
            self.assertIn(d, refs, f"golden seeds should cover paper class {d}")

    # ── summary self-consistency (drift guard) ───────────────────────────
    def test_summary_counts_match_cases(self):
        s = self.data["summary"]
        self.assertEqual(s["total_labeled"], len(self.cases))
        self.assertEqual(
            s["fail_plausible_yes"],
            sum(1 for c in self.cases if c["fail_plausible"] == "yes"))
        self.assertEqual(
            s["fail_plausible_partial"],
            sum(1 for c in self.cases if c["fail_plausible"] == "partial"))
        self.assertEqual(
            s["observer_in_scope_yes"],
            sum(1 for c in self.cases if c["observer_in_scope"] == "yes"))
        self.assertEqual(
            s["golden_seed_count"],
            sum(1 for c in self.cases if c["golden_seed"]))

    def test_paper_canonical_reconciles(self):
        # 24 labeled − 2 post-cutoff == 22 paper-canonical (data_inventory.md)
        s = self.data["summary"]
        self.assertEqual(s["total_labeled"] - s["post_cutoff_additions"], s["paper_canonical"])
        actual_canon = sum(1 for c in self.cases if c["paper_canonical"])
        self.assertEqual(actual_canon, s["paper_canonical"])

    # ── known gaps (no standalone case file) ─────────────────────────────
    def test_known_gaps_well_formed(self):
        gaps = self.data["known_gaps"]
        self.assertTrue(gaps, "D4 fabricated-release gap must be registered")
        defined = set(self.data["signals"].keys())
        for g in gaps:
            for f in ("id", "desc", "ground_truth_location", "fail_plausible",
                      "observer_in_scope", "expected_signal", "category"):
                self.assertIn(f, g, f"known_gap {g.get('id', '?')} missing {f}")
            for sig in g["expected_signal"]:
                self.assertIn(sig, defined, f"known_gap {g['id']}: undefined signal {sig}")


class TestGroundTruthFilePresence(unittest.TestCase):
    """yaml-free guards (run even without PyYAML)."""

    def test_files_exist(self):
        self.assertTrue(os.path.isfile(_GT_PATH), "ground-truth yaml must exist")
        self.assertTrue(os.path.isfile(_DESIGN_PATH), "design doc must exist")

    def test_stage1_marker_present(self):
        txt = open(_GT_PATH, encoding="utf-8").read()
        self.assertIn("研究攻关 #1 Stage 1", txt)
        self.assertIn("0.1-stage1", txt)
        # honest-boundary discipline must be documented in the file
        self.assertIn("observer_in_scope", txt)
        self.assertIn("不报 κ", txt)


if __name__ == "__main__":
    unittest.main(verbosity=2)
