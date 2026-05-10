#!/usr/bin/env python3
"""test_project_alignment_scorer.py — V37.9.47 Stage 2 PoC 31 单测

测试类分布 (设计文档 docs/opportunity_radar_design.md 4.5 节):
  TestProjectConceptsYaml      (5)  — 文件存在 / schema / weight 范围 / keywords / FAIL-OPEN
  TestKeywordHitCounting       (6)  — 多关键词 / 大小写 / 中英混合 / excluded 降权 / dedup / 空
  TestExpectedScoreRange       (4)  — 0/5/边界/负数 命中 → 评分区间
  TestValidateAlignmentScore   (4)  — LLM 评 5 但 0 命中 / 一致 / 偏低 / invalid score
  TestPromptInjection          (3)  — hf_papers.sh 6 字段 prompt 含 🎚️ + 评分指南 + 原因要求
  TestParse6FieldOutput        (5)  — 完整 6 字段 / 缺字段 / 字段顺序错乱 / 空 / 与 hf_papers inline 行为一致 (MR-8)
  TestSourceLevelGuards        (4)  — V37.9.47 marker / FAIL-OPEN / score range / log stderr

V37.9.47 反向验证守卫 (V37.9.43-hotfix 同款):
  sed 注入反模式 (如 _SCORE_RANGES 修改让边界错位) → 单测立即 fail
"""

import os
import re
import sys
import tempfile
import unittest

import project_alignment_scorer as pas

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))


# ── Test 1: TestProjectConceptsYaml (5) ─────────────────────────────
class TestProjectConceptsYaml(unittest.TestCase):
    """V37.9.47: project_concepts.yaml 加载 + schema 完整性"""

    def test_file_exists_and_loads(self):
        """project_concepts.yaml 文件存在 + 可解析."""
        path = os.path.join(REPO_ROOT, "project_concepts.yaml")
        self.assertTrue(os.path.isfile(path), "project_concepts.yaml must exist")
        concepts = pas.load_project_concepts(path)
        self.assertIsInstance(concepts, dict)

    def test_schema_has_three_top_sections(self):
        """schema 必含 core_planes / active_research_directions / excluded_topics."""
        concepts = pas.load_project_concepts()
        self.assertIn("core_planes", concepts)
        self.assertIn("active_research_directions", concepts)
        self.assertIn("excluded_topics", concepts)
        # 各 section 都非空 dict
        self.assertGreater(len(concepts["core_planes"]), 0)
        self.assertGreater(len(concepts["active_research_directions"]), 0)
        self.assertGreater(len(concepts["excluded_topics"]), 0)

    def test_weight_in_valid_range(self):
        """所有 weight 必须在合理范围 ([-5, 5])."""
        concepts = pas.load_project_concepts()
        for section_key in ("core_planes", "active_research_directions"):
            for name, d in concepts[section_key].items():
                w = d.get("weight", 0)
                self.assertIsInstance(w, int, f"{section_key}.{name}.weight not int")
                self.assertTrue(1 <= w <= 5,
                                f"{section_key}.{name}.weight={w} not in [1,5]")
        # excluded weight 必须 <= 0 (降权)
        for name, d in concepts["excluded_topics"].items():
            w = d.get("weight", 0)
            self.assertLess(w, 0, f"excluded_topics.{name}.weight={w} not negative")

    def test_keywords_non_empty(self):
        """每个 plane / direction / excluded 必须有非空 keywords."""
        concepts = pas.load_project_concepts()
        for section_key in ("core_planes", "active_research_directions", "excluded_topics"):
            for name, d in concepts[section_key].items():
                kws = d.get("keywords", [])
                self.assertGreater(len(kws), 0,
                                   f"{section_key}.{name}.keywords is empty")
                for kw in kws:
                    self.assertIsInstance(kw, str)
                    self.assertGreater(len(kw), 0)

    def test_load_fail_open_on_missing_file(self):
        """缺文件 → 返回最小默认 dict 不抛异 (FAIL-OPEN 契约)."""
        with tempfile.TemporaryDirectory() as tmp:
            missing = os.path.join(tmp, "nonexistent.yaml")
            concepts = pas.load_project_concepts(missing)
        # FAIL-OPEN: 返回 dict 而非 raise
        self.assertIsInstance(concepts, dict)
        self.assertEqual(concepts["core_planes"], {})
        self.assertEqual(concepts["excluded_topics"], {})


# ── Test 2: TestKeywordHitCounting (6) ──────────────────────────────
class TestKeywordHitCounting(unittest.TestCase):
    """V37.9.47: count_keyword_hits — 多关键词 / 大小写 / excluded / dedup"""

    def setUp(self):
        # Build minimal fake concepts
        self.concepts = {
            "core_planes": {
                "control_plane": {
                    "keywords": ["governance", "audit", "fail-fast"],
                    "weight": 5,
                },
            },
            "active_research_directions": {
                "ontology": {
                    "keywords": ["ontology", "BFO", "policy engine"],
                    "weight": 5,
                },
            },
            "excluded_topics": {
                "pure_finetuning": {
                    "keywords": ["LoRA optimization", "distillation tricks"],
                    "weight": -2,
                },
            },
        }

    def test_multiple_keywords_hits(self):
        """多个关键词命中, positive_hits 准确计数."""
        content = "This paper proposes governance + audit + ontology framework."
        hits = pas.count_keyword_hits(content, self.concepts)
        self.assertEqual(hits["positive_hits"], 3)  # governance / audit / ontology
        self.assertEqual(hits["negative_hits"], 0)
        self.assertEqual(hits["total_score"], 3)

    def test_case_insensitive_matching(self):
        """大小写不敏感命中."""
        content = "GOVERNANCE Policy Engine BFO ontology."
        hits = pas.count_keyword_hits(content, self.concepts)
        # governance / policy engine / BFO / ontology = 4 (但 BFO 是 capital, 仍命中)
        # ontology 已被 BFO 之前的 ontology 字符串 trigger? 实际 ontology 关键词在 BFO 之后
        # but content 含两次 ontology pattern - 只计 1 次 (set semantics)
        self.assertGreaterEqual(hits["positive_hits"], 3,
                                f"matched: {hits['matched_keywords']}")

    def test_chinese_english_mixed_content(self):
        """中英文混合内容 (substring 匹配)."""
        content = "本论文研究 governance 和审计 audit, 实现 fail-fast 机制和 BFO 本体推理"
        hits = pas.count_keyword_hits(content, self.concepts)
        # governance / audit / fail-fast / BFO → 4
        self.assertGreaterEqual(hits["positive_hits"], 4)

    def test_excluded_topics_strong_downweight(self):
        """excluded_topics 命中 → negative_hits + total_score 强降权 (× -2)."""
        content = "We use LoRA optimization with distillation tricks for fine-tuning."
        hits = pas.count_keyword_hits(content, self.concepts)
        self.assertEqual(hits["positive_hits"], 0)
        self.assertEqual(hits["negative_hits"], 2)  # LoRA + distillation
        self.assertEqual(hits["total_score"], -4)  # 0 - 2*2 = -4

    def test_duplicate_keywords_not_double_counted(self):
        """同一关键词出现多次 → 只计 1 次 (set semantics)."""
        content = "governance governance governance audit."
        hits = pas.count_keyword_hits(content, self.concepts)
        self.assertEqual(hits["positive_hits"], 2)  # governance + audit, not 4

    def test_empty_content_returns_zero(self):
        """空内容 / None → 全 0 不抛异."""
        for empty in ("", None):
            hits = pas.count_keyword_hits(empty, self.concepts)
            self.assertEqual(hits["positive_hits"], 0)
            self.assertEqual(hits["negative_hits"], 0)
            self.assertEqual(hits["total_score"], 0)
            self.assertEqual(hits["matched_keywords"], [])


# ── Test 3: TestExpectedScoreRange (4) ──────────────────────────────
class TestExpectedScoreRange(unittest.TestCase):
    """V37.9.47: compute_expected_range — 设计文档 4.2 锁定档位"""

    def test_zero_hits_low_stars(self):
        """0 命中 → ⭐1-2."""
        self.assertEqual(pas.compute_expected_range(0), (1, 2))

    def test_high_hits_high_stars(self):
        """5+ 命中 → ⭐4-5."""
        self.assertEqual(pas.compute_expected_range(5), (4, 5))
        self.assertEqual(pas.compute_expected_range(10), (4, 5))

    def test_boundary_values(self):
        """边界值: 1/2/3/4 命中各自档位."""
        self.assertEqual(pas.compute_expected_range(1), (2, 3))
        self.assertEqual(pas.compute_expected_range(2), (2, 3))
        self.assertEqual(pas.compute_expected_range(3), (3, 4))
        self.assertEqual(pas.compute_expected_range(4), (3, 4))

    def test_negative_score_low_stars(self):
        """negative score (excluded > positive) → ⭐1-2 强降权."""
        self.assertEqual(pas.compute_expected_range(-1), (1, 2))
        self.assertEqual(pas.compute_expected_range(-10), (1, 2))


# ── Test 4: TestValidateAlignmentScore (4) ──────────────────────────
class TestValidateAlignmentScore(unittest.TestCase):
    """V37.9.47: validate_alignment_score — LLM vs rule 一致性"""

    def setUp(self):
        self.concepts = {
            "core_planes": {
                "control_plane": {
                    "keywords": ["governance", "audit", "ontology", "policy engine",
                                 "control plane"],
                    "weight": 5,
                },
            },
            "active_research_directions": {},
            "excluded_topics": {
                "pure_hw": {"keywords": ["CUDA kernel"], "weight": -2},
            },
        }

    def test_llm_high_but_zero_keyword_hits_not_validated(self):
        """V37.9.47 关键场景: LLM 评 ⭐5 但 0 命中 → not validated + reason 解释."""
        content = "This paper studies image classification with ResNet on ImageNet."
        result = pas.validate_alignment_score(content, 5, self.concepts)
        self.assertFalse(result["validated"])
        self.assertEqual(result["llm_score"], 5)
        self.assertEqual(result["positive_hits"], 0)
        self.assertEqual(result["rule_range"], (1, 2))
        self.assertIn("LLM 评 ⭐5 偏高", result["reason"])
        self.assertIn("仅命中 0 关键词", result["reason"])

    def test_llm_score_in_range_validated(self):
        """LLM 评分在 rule range 内 → validated=True."""
        content = ("Agent runtime governance with policy engine, audit log, "
                   "and ontology-driven control plane.")
        result = pas.validate_alignment_score(content, 5, self.concepts)
        self.assertTrue(result["validated"])
        self.assertGreaterEqual(result["positive_hits"], 4)

    def test_llm_too_low_when_keywords_high(self):
        """LLM 评 ⭐2 但命中 5+ 关键词 → not validated (LLM 偏低)."""
        content = ("governance audit ontology policy engine control plane "
                   "framework for agent runtime.")
        result = pas.validate_alignment_score(content, 2, self.concepts)
        self.assertFalse(result["validated"])
        self.assertIn("LLM 评 ⭐2 偏低", result["reason"])

    def test_invalid_llm_score(self):
        """非 1-5 整数 LLM score → validated=False + reason 解释."""
        for bad in (0, 6, 99, -1, "5", None, 3.5):
            result = pas.validate_alignment_score("any content", bad, self.concepts)
            self.assertFalse(result["validated"], f"bad llm_score={bad!r}")
            self.assertIn("invalid llm_score", result["reason"])


# ── Test 5: TestPromptInjection (3) ─────────────────────────────────
class TestPromptInjection(unittest.TestCase):
    """V37.9.47: 检验 hf_papers.sh 6 字段 prompt 注入正确 (V37.9.45 已落地, 此处守卫)."""

    @classmethod
    def setUpClass(cls):
        path = os.path.join(REPO_ROOT, "jobs", "hf_papers", "run_hf_papers.sh")
        with open(path, "r", encoding="utf-8") as f:
            cls.SRC = f.read()

    def test_prompt_contains_alignment_field(self):
        """hf_papers.sh prompt 必含 🎚️ 项目对齐度 字段定义."""
        self.assertIn("🎚️ 项目对齐度", self.SRC)

    def test_prompt_contains_scoring_guide(self):
        """hf_papers.sh prompt 必含 5 档评分指南 (⭐⭐⭐⭐⭐ 直接相关 ... ⭐ 完全无关)."""
        # V37.9.45 prompt 文档化 5 档
        self.assertIn("⭐⭐⭐⭐⭐", self.SRC)
        self.assertIn("control plane", self.SRC)  # core direction keyword
        self.assertIn("ontology", self.SRC)

    def test_prompt_contains_reason_requirement(self):
        """prompt 必要求 LLM 给"一句话原因 (≤30 字)" 防止只给数字."""
        # V37.9.45 hf_papers.sh prompt 严格规定原因长度
        self.assertIn("一句话原因", self.SRC)
        # 长度限制 (允许 "30" 或 "≤ 30 字" 等变体)
        self.assertTrue(re.search(r"30\s*字", self.SRC),
                        "prompt should require <=30 字 reason")


# ── Test 6: TestParse6FieldOutput (5) ───────────────────────────────
class TestParse6FieldOutput(unittest.TestCase):
    """V37.9.47: parse_6field_output (MR-8 与 hf_papers.sh inline 行为一致)"""

    def test_full_six_fields_parsed(self):
        """完整 6 字段输入全部解析 (hf_papers LLM 真实多行格式).

        注意 (MR-8): parser 与 hf_papers.sh inline 行为一致 — 仅 📌 字段做 inline
        内容提取 (因为 cn_title 通常是 LLM 一行写完). 其他字段头后 LLM 必须换行
        写内容. 实测 V37.9.45 hf_papers cron 推送 LLM 总是多行格式, 兼容 OK.
        """
        text = """📌 中文标题: Speculative Tool Calls
🔑 核心贡献:
speculative execution for agent tools
💡 关键方法:
cache + verify pattern
🎯 实践启发:
可借鉴 Open Agent Runtime
⭐ 评级: ⭐⭐⭐⭐
🎚️ 项目对齐度: ⭐⭐⭐⭐⭐ / 直接对应控制平面工具优化"""
        fields = pas.parse_6field_output(text)
        self.assertEqual(fields["cn_title"], "Speculative Tool Calls")
        self.assertIn("speculative", fields["highlights"])
        self.assertIn("cache", fields["insight"])
        self.assertIn("Open Agent Runtime", fields["practice"])
        self.assertIn("⭐⭐⭐⭐", fields["rating"])
        self.assertIn("⭐⭐⭐⭐⭐", fields["alignment"])
        self.assertIn("直接对应控制平面", fields["alignment"])

    def test_missing_alignment_field_backward_compat(self):
        """缺 🎚️ 字段 → alignment='' (V37.9.45 之前 5 字段格式向后兼容)."""
        text = """📌 标题
🔑 核心贡献
💡 关键方法
🎯 实践启发
⭐ 评级: ⭐⭐⭐"""
        fields = pas.parse_6field_output(text)
        self.assertEqual(fields["alignment"], "")  # 缺失 → 空字符串
        self.assertIn("⭐⭐⭐", fields["rating"])

    def test_field_order_scrambled(self):
        """字段顺序乱序 → 仍正确解析 (key-based).

        MR-8 注意: parser 与 hf_papers.sh inline 一致 — ⭐ 检测会被 alignment 状态
        排除 (避免 ⭐ 评级行被 alignment 字段吞), 因此真实 LLM 输出必须保持 ⭐ 在 🎚️
        之前的固定顺序 (V37.9.45 hf_papers prompt 已锁定此顺序). 本测试验证其他
        字段 (📌/🔑/💡/🎯) scramble 时仍正确解析, 但 ⭐ → 🎚️ 顺序保持.
        """
        text = """🎯 实践启发:
practice content
📌 标题: scrambled order paper
💡 关键方法:
method content
🔑 核心贡献:
contribution content
⭐ 评级: ⭐⭐⭐⭐⭐
🎚️ 项目对齐度: ⭐⭐⭐⭐"""
        fields = pas.parse_6field_output(text)
        self.assertEqual(fields["cn_title"], "scrambled order paper")
        self.assertIn("contribution", fields["highlights"])
        self.assertIn("method", fields["insight"])
        self.assertIn("practice", fields["practice"])
        self.assertIn("⭐⭐⭐⭐⭐", fields["rating"])
        self.assertIn("⭐⭐⭐⭐", fields["alignment"])

    def test_empty_input_returns_empty_fields(self):
        """空输入 / None → 全空字段不抛异."""
        for empty in ("", None):
            fields = pas.parse_6field_output(empty)
            self.assertEqual(fields["cn_title"], "")
            self.assertEqual(fields["alignment"], "")
            # 6 字段全部存在
            self.assertEqual(set(fields.keys()),
                             {"cn_title", "highlights", "insight", "practice",
                              "rating", "alignment"})

    def test_module_parser_matches_hf_papers_inline_behavior(self):
        """V37.9.47 MR-8 守卫: 模块 parse_6field_output 必须与 hf_papers.sh
        inline 实现行为一致 (防漂移). 通过 grep hf_papers.sh 验证 inline parser
        签名 + 关键 regex 与模块版本一致.
        """
        path = os.path.join(REPO_ROOT, "jobs", "hf_papers", "run_hf_papers.sh")
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        # inline parser 必含相同函数签名
        self.assertIn("def parse_6field_output(content):", src)
        # inline 必含 6 字段 keys 完整集
        for key in ("'cn_title'", "'highlights'", "'insight'",
                    "'practice'", "'rating'", "'alignment'"):
            self.assertIn(key, src, f"inline parser missing {key}")
        # inline 必含相同 emoji 字段头检测
        for emoji in ("📌", "🔑", "💡", "🎯", "⭐", "🎚️"):
            self.assertIn(emoji, src, f"inline parser missing {emoji} detector")


# ── Test 7: TestSourceLevelGuards (4) ───────────────────────────────
class TestSourceLevelGuards(unittest.TestCase):
    """V37.9.47: 源码级 grep 守卫 — 防未来重构丢字面量"""

    @classmethod
    def setUpClass(cls):
        path = os.path.join(REPO_ROOT, "project_alignment_scorer.py")
        with open(path, "r", encoding="utf-8") as f:
            cls.SRC = f.read()

    def test_v37_9_47_marker_present(self):
        """V37.9.47 版本标记必须出现在源码."""
        self.assertIn("V37.9.47", self.SRC)
        self.assertIn("Opportunity Radar Stage 2", self.SRC)

    def test_design_locked_score_ranges(self):
        """_SCORE_RANGES 设计文档 4.2 锁定档位必须保持."""
        self.assertIn("_SCORE_RANGES", self.SRC)
        # 必含 5 档定义
        self.assertIn("(0, 0, 1, 2)", self.SRC)         # 0 命中 → ⭐1-2
        self.assertIn("(5, 999, 4, 5)", self.SRC)       # 5+ 命中 → ⭐4-5
        # 负数档 (excluded > positive)
        self.assertIn("(-999, -1, 1, 2)", self.SRC)

    def test_fail_open_contract_documented(self):
        """FAIL-OPEN 契约必须有源码注释."""
        self.assertIn("FAIL-OPEN", self.SRC)

    def test_log_writes_to_stderr_mr11(self):
        """log() 必须 file=sys.stderr (MR-11 防 $(...) 命令替换污染)."""
        self.assertIn("def log(msg)", self.SRC)
        idx = self.SRC.find("def log(msg)")
        next_def = self.SRC.find("\ndef ", idx + 1)
        log_body = self.SRC[idx:next_def]
        self.assertIn("file=sys.stderr", log_body,
                      "MR-11: log() must write to stderr")


if __name__ == "__main__":
    unittest.main(verbosity=2)
