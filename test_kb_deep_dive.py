#!/usr/bin/env python3
"""test_kb_deep_dive.py — V37.9.16 每日深度分析单测

覆盖维度：
  1. Tier 分类（一档/二档/三档）
  2. Entry 解析（从 H2 section 抽 title/link/stars/abstract）
  3. 评分 + 排序（⭐≥4 门槛 + 主题加权 + 摘要长度 tie-breaker）
  4. PDF / HTML fetcher（lazy import + degrade）
  5. Prompt builder（full_text vs abstract-only，grounding 约束）
  6. Output builder（markdown / WA / Discord 三格式）
  7. run() orchestrator（各种路径：ok / no_candidates / llm_failed）
  8. Shell 脚本守卫（反模式防御）
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import kb_deep_dive as m


# ══════════════════════════════════════════════════════════════════════
# 1. Tier 分类
# ══════════════════════════════════════════════════════════════════════
class TestClassifyTier(unittest.TestCase):
    def test_tier1_arxiv(self):
        self.assertEqual(m.classify_tier("arxiv_monitor"), 1)

    def test_tier1_acl(self):
        self.assertEqual(m.classify_tier("acl_anthology"), 1)

    def test_tier1_hf(self):
        self.assertEqual(m.classify_tier("hf_papers"), 1)

    def test_tier2_rss(self):
        self.assertEqual(m.classify_tier("rss_blogs"), 2)

    def test_tier2_ontology(self):
        self.assertEqual(m.classify_tier("ontology_sources"), 2)

    def test_tier3_hn_default(self):
        self.assertEqual(m.classify_tier("run_hn_fixed"), 3)

    def test_tier3_unknown(self):
        self.assertEqual(m.classify_tier("nonexistent_source"), 3)


# ══════════════════════════════════════════════════════════════════════
# 2. Entry 解析器
# ══════════════════════════════════════════════════════════════════════
class TestParseEntriesFromSection(unittest.TestCase):
    def test_single_entry(self):
        section = """
*Attention Is All You Need*
作者：Vaswani 等 | 日期：2017
链接：https://arxiv.org/abs/1706.03762
贡献：提出 Transformer 架构
价值：⭐⭐⭐⭐⭐
"""
        entries = m.parse_entries_from_section(section, "arxiv_monitor", "📄 ArXiv")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["title"], "Attention Is All You Need")
        self.assertEqual(entries[0]["link"], "https://arxiv.org/abs/1706.03762")
        self.assertEqual(entries[0]["stars"], 5)
        self.assertEqual(entries[0]["source_id"], "arxiv_monitor")

    def test_multiple_entries(self):
        section = """
*论文A*
链接：https://arxiv.org/abs/1111.1111
价值：⭐⭐⭐

*论文B*
链接：https://arxiv.org/abs/2222.2222
价值：⭐⭐⭐⭐⭐

*论文C*
价值：⭐⭐
"""
        entries = m.parse_entries_from_section(section, "arxiv_monitor", "📄 ArXiv")
        self.assertEqual(len(entries), 3)
        self.assertEqual(entries[0]["stars"], 3)
        self.assertEqual(entries[1]["stars"], 5)
        self.assertEqual(entries[2]["stars"], 2)

    def test_empty_section(self):
        self.assertEqual(m.parse_entries_from_section("", "x", "X"), [])

    def test_no_stars_defaults_zero(self):
        section = "*No stars here*\n链接：https://example.com"
        entries = m.parse_entries_from_section(section, "x", "X")
        self.assertEqual(entries[0]["stars"], 0)

    def test_fallback_url_without_link_label(self):
        """链接行不以'链接：'开头时也能抓到 URL。"""
        section = "*Paper*\nhttps://aclanthology.org/2024.acl-long.1/\n价值：⭐⭐⭐⭐"
        entries = m.parse_entries_from_section(section, "acl_anthology", "📝 ACL")
        self.assertTrue(entries[0]["link"].startswith("https://aclanthology.org/"))

    def test_abstract_accumulates(self):
        section = """
*Paper X*
贡献：核心贡献句1
要点：核心贡献句2
"""
        entries = m.parse_entries_from_section(section, "x", "X")
        self.assertIn("核心贡献句1", entries[0]["abstract"])
        self.assertIn("核心贡献句2", entries[0]["abstract"])


# ══════════════════════════════════════════════════════════════════════
# 3. 评分 + 排序
# ══════════════════════════════════════════════════════════════════════
class TestScoreEntry(unittest.TestCase):
    def test_low_stars_returns_negative(self):
        """⭐<MIN_STARS 必须返回负数以被排除。"""
        e = {"stars": 3, "title": "X", "abstract": ""}
        self.assertLess(m.score_entry(e), 0)

    def test_high_stars_base_score(self):
        e = {"stars": 5, "title": "plain", "abstract": ""}
        # 5*10 + 0 + 0 = 50
        self.assertEqual(m.score_entry(e), 50)

    def test_topic_keyword_boost(self):
        e = {"stars": 4, "title": "ontology engine", "abstract": ""}
        # 4*10 + 10 (ontology) = 50
        self.assertEqual(m.score_entry(e), 50)

    def test_multiple_topic_boosts(self):
        e = {
            "stars": 4,
            "title": "agent runtime with tool calling and ontology",
            "abstract": "",
        }
        # 4*10 + 10 (agent runtime) + 8 (tool calling) + 10 (ontology) = 68
        self.assertEqual(m.score_entry(e), 68)

    def test_abstract_length_tiebreaker(self):
        e_short = {"stars": 5, "title": "x", "abstract": "y" * 50}
        e_long = {"stars": 5, "title": "x", "abstract": "y" * 1000}
        self.assertLess(m.score_entry(e_short), m.score_entry(e_long))

    def test_tie_breaker_bonus_capped(self):
        """摘要长度奖励封顶 MAX_ABSTRACT_BONUS=10。"""
        e_capped = {"stars": 5, "title": "x", "abstract": "y" * 2000}
        # 5*10 + 0 + min(2000//100, 10) = 50 + 10 = 60
        self.assertEqual(m.score_entry(e_capped), 60)


# ══════════════════════════════════════════════════════════════════════
# 4. PDF URL 派生
# ══════════════════════════════════════════════════════════════════════
class TestPickTopTierFallback(unittest.TestCase):
    """V37.9.17 方案 C: tier-aware fallback — TIER 1/2 优先，TIER 3 fallback。

    背景：V37.9.16 首跑选中 ai_leaders_x ⭐5 走 abstract_only，用户感知质量低于
    预期。方案 C 保证 TIER 1/2 候选永远优先于 TIER 3，即使 score 较低。
    """

    def _entry(self, source_id, stars, title="x"):
        return {
            "title": title,
            "link": "https://example.com",
            "stars": stars,
            "abstract": "",
            "source_id": source_id,
            "source_label": source_id,
        }

    def test_empty_returns_none(self):
        self.assertIsNone(m.pick_top([]))

    def test_tier1_wins_over_higher_score_tier3(self):
        """关键场景：arxiv ⭐4 必须胜过 X tweet ⭐5（tier-aware）。"""
        tier3 = self._entry("ai_leaders_x", 5, "X tweet")
        tier1 = self._entry("arxiv_monitor", 4, "Arxiv paper")
        # candidates 已按 score 排序时 tier3 在前（V37.9.16 行为）
        result = m.pick_top([tier3, tier1])
        self.assertEqual(result["source_id"], "arxiv_monitor")
        self.assertEqual(result["title"], "Arxiv paper")

    def test_tier2_wins_over_tier3(self):
        tier3 = self._entry("run_hn_fixed", 5, "HN")
        tier2 = self._entry("rss_blogs", 4, "Blog post")
        result = m.pick_top([tier3, tier2])
        self.assertEqual(result["source_id"], "rss_blogs")

    def test_tier1_beats_tier2_when_both_present(self):
        """TIER 1+2 同桶内按 score 排序（不区分 1 vs 2）— 输入顺序即决定。"""
        tier1 = self._entry("arxiv_monitor", 4, "Arxiv")
        tier2 = self._entry("rss_blogs", 5, "Blog")
        # 输入按 score 排序：tier2 score 高在前 → 同桶内 tier2 胜出
        result = m.pick_top([tier2, tier1])
        self.assertEqual(result["source_id"], "rss_blogs")
        # 反过来 tier1 在前则 tier1 胜出
        result2 = m.pick_top([tier1, tier2])
        self.assertEqual(result2["source_id"], "arxiv_monitor")

    def test_only_tier3_fallback(self):
        """TIER 1+2 全空 → 回退 TIER 3（保留 V37.9.16 行为，没新候选时仍推送）。"""
        tier3a = self._entry("ai_leaders_x", 5, "X tweet A")
        tier3b = self._entry("run_hn_fixed", 4, "HN B")
        result = m.pick_top([tier3a, tier3b])
        self.assertEqual(result["source_id"], "ai_leaders_x")
        self.assertEqual(result["title"], "X tweet A")

    def test_v37_9_16_blood_lesson_scenario(self):
        """直接复现 V37.9.16 首跑场景：tier3 X tweet ⭐5 + tier1 论文 ⭐4
        必须选 tier1 论文（方案 C 兑现）。"""
        x_tweet = self._entry("ai_leaders_x", 5, "国家AI动员")
        arxiv = self._entry("arxiv_monitor", 4, "Some Paper")
        hf = self._entry("hf_papers", 4, "HF Paper")
        # 模拟 collect_today_candidates 已排序输出（X tweet score 最高在前）
        result = m.pick_top([x_tweet, arxiv, hf])
        self.assertIn(result["source_id"], ("arxiv_monitor", "hf_papers"))
        self.assertNotEqual(result["source_id"], "ai_leaders_x")


class TestPdfUrlDerivation(unittest.TestCase):
    def test_arxiv_abs_to_pdf(self):
        self.assertEqual(
            m.arxiv_url_to_pdf("https://arxiv.org/abs/2401.12345"),
            "https://arxiv.org/pdf/2401.12345.pdf",
        )

    def test_arxiv_abs_trailing_slash(self):
        self.assertEqual(
            m.arxiv_url_to_pdf("https://arxiv.org/abs/2401.12345/"),
            "https://arxiv.org/pdf/2401.12345.pdf",
        )

    def test_arxiv_already_pdf(self):
        url = "https://arxiv.org/pdf/2401.12345.pdf"
        self.assertEqual(m.arxiv_url_to_pdf(url), url)

    def test_acl_url_to_pdf(self):
        self.assertEqual(
            m.acl_url_to_pdf("https://aclanthology.org/2024.acl-long.123/"),
            "https://aclanthology.org/2024.acl-long.123.pdf",
        )

    def test_non_arxiv_returns_none(self):
        self.assertIsNone(m.arxiv_url_to_pdf("https://example.com/paper.html"))


# ══════════════════════════════════════════════════════════════════════
# 4b. OA 全文解析（V37.9.183）— DOI/S2-id/HF-id → S2 → arxiv/OA PDF
#     77% abstract_only 结构性 gap：picker 选高对齐论文(dblp DOI/S2 页/HF 页)
#     无法直接派生 PDF；本解析复用 S2 把确定性标识符解析为全文 PDF。
# ══════════════════════════════════════════════════════════════════════
class TestOaResolutionV183(unittest.TestCase):
    # --- resolve_oa_pdf_url 路由（确定性，标识符在 URL 里）---
    def test_hf_page_rewritten_to_arxiv(self):
        # HF Daily Papers 按 arxiv id 索引 → 确定性改写，无网络
        self.assertEqual(
            m.resolve_oa_pdf_url("https://huggingface.co/papers/2604.22085"),
            "https://arxiv.org/pdf/2604.22085",
        )

    def test_hf_page_with_version(self):
        self.assertEqual(
            m.resolve_oa_pdf_url("https://huggingface.co/papers/2604.22085v2"),
            "https://arxiv.org/pdf/2604.22085v2",
        )

    def test_s2_page_extracts_paper_id(self):
        pid = "c3d330be0c52d70290c545372718994bd995dabb"
        with patch.object(m, "_s2_lookup_oa_pdf", return_value="https://arxiv.org/pdf/2501.1") as mk:
            r = m.resolve_oa_pdf_url("https://www.semanticscholar.org/paper/" + pid)
        self.assertEqual(r, "https://arxiv.org/pdf/2501.1")
        mk.assert_called_once_with(pid)

    def test_s2_page_with_slug(self):
        pid = "9ecfdb33b93c71e96c107645a4514eea6d8bb10d"
        with patch.object(m, "_s2_lookup_oa_pdf", return_value="x") as mk:
            m.resolve_oa_pdf_url("https://www.semanticscholar.org/paper/Some-Title/" + pid)
        mk.assert_called_once_with(pid)

    def test_doi_routed_to_s2_by_doi(self):
        with patch.object(m, "_s2_lookup_oa_pdf", return_value="https://www.mdpi.com/x/pdf") as mk:
            r = m.resolve_oa_pdf_url("https://doi.org/10.3390/SYSTEMS14020154")
        self.assertEqual(r, "https://www.mdpi.com/x/pdf")
        mk.assert_called_once_with("DOI:10.3390/SYSTEMS14020154")

    def test_acm_doi_routed(self):
        with patch.object(m, "_s2_lookup_oa_pdf", return_value="x") as mk:
            m.resolve_oa_pdf_url("https://doi.org/10.1145/3774904.3792985")
        mk.assert_called_once_with("DOI:10.1145/3774904.3792985")

    def test_sciencedirect_pii_no_identifier_returns_none(self):
        # ScienceDirect PII URL 无 DOI/S2-id/HF-id → None（登记 follow-up，不选错论文）
        self.assertIsNone(m.resolve_oa_pdf_url(
            "https://www.sciencedirect.com/science/article/pii/S0950705126012177"))

    def test_empty_url_returns_none(self):
        self.assertIsNone(m.resolve_oa_pdf_url(""))

    # --- _s2_lookup_oa_pdf 解析（mock _s2_fetch）---
    def test_s2_lookup_open_access_pdf(self):
        with patch.object(m, "_s2_fetch", return_value={"openAccessPdf": {"url": "https://x.org/p.pdf"}}):
            self.assertEqual(m._s2_lookup_oa_pdf("DOI:10.1/2"), "https://x.org/p.pdf")

    def test_s2_lookup_oa_arxiv_abs_normalized(self):
        # openAccessPdf.url 是 arxiv abs → 归一化为 pdf
        with patch.object(m, "_s2_fetch", return_value={"openAccessPdf": {"url": "https://arxiv.org/abs/2501.123"}}):
            self.assertEqual(m._s2_lookup_oa_pdf("DOI:10.1/2"), "https://arxiv.org/pdf/2501.123.pdf")

    def test_s2_lookup_external_arxiv_id(self):
        with patch.object(m, "_s2_fetch", return_value={"externalIds": {"ArXiv": "2501.12345"}}):
            self.assertEqual(m._s2_lookup_oa_pdf("DOI:10.1/2"), "https://arxiv.org/pdf/2501.12345")

    def test_s2_lookup_oa_preferred_over_external(self):
        data = {"openAccessPdf": {"url": "https://oa.org/p.pdf"}, "externalIds": {"ArXiv": "2501.1"}}
        with patch.object(m, "_s2_fetch", return_value=data):
            self.assertEqual(m._s2_lookup_oa_pdf("DOI:10.1/2"), "https://oa.org/p.pdf")

    def test_s2_lookup_no_oa_no_arxiv_returns_none(self):
        with patch.object(m, "_s2_fetch", return_value={"externalIds": {"DOI": "10.1/2"}}):
            self.assertIsNone(m._s2_lookup_oa_pdf("DOI:10.1/2"))

    def test_s2_lookup_fetch_failed_fail_open(self):
        with patch.object(m, "_s2_fetch", return_value=None):
            self.assertIsNone(m._s2_lookup_oa_pdf("DOI:10.1/2"))

    def test_s2_lookup_malformed_oa_not_dict(self):
        with patch.object(m, "_s2_fetch", return_value={"openAccessPdf": "garbage"}):
            self.assertIsNone(m._s2_lookup_oa_pdf("DOI:10.1/2"))

    # --- _s2_fetch FAIL-OPEN（网络层）---
    def test_s2_fetch_network_error_fail_open(self):
        with patch("kb_deep_dive.urllib.request.urlopen",
                   side_effect=m.urllib.error.URLError("boom")):
            self.assertIsNone(m._s2_fetch("DOI:10.1/2"))

    # --- fetch_pdf_text 集成（OA 解析 wire 进 fetch）---
    def test_fetch_pdf_text_uses_oa_resolution_when_direct_fails(self):
        # DOI URL 直接派生失败 → 调 resolve → 拿到 PDF url → 进 pdfplumber（dev 无库）
        with patch.object(m, "resolve_oa_pdf_url", return_value="https://x.org/p.pdf") as mk:
            ok, text, reason = m.fetch_pdf_text("https://doi.org/10.1145/3774904.3792985")
        mk.assert_called_once()
        self.assertFalse(ok)
        # 关键：不是 "no PDF URL derivable" — 证明 OA url 被采用并进入抓取
        self.assertNotIn("no PDF URL derivable", reason)
        self.assertIn("pdfplumber not installed", reason)

    def test_fetch_pdf_text_oa_lookup_failed_degrades(self):
        with patch.object(m, "resolve_oa_pdf_url", return_value=None):
            ok, text, reason = m.fetch_pdf_text("https://doi.org/10.1145/3774904.3792985")
        self.assertFalse(ok)
        self.assertIn("no PDF URL derivable (incl. OA lookup)", reason)

    def test_fetch_pdf_text_arxiv_skips_oa_resolution(self):
        # arxiv 能直接派生 → 不应调 OA 解析
        with patch.object(m, "resolve_oa_pdf_url") as mk:
            m.fetch_pdf_text("https://arxiv.org/abs/2501.12345")
        mk.assert_not_called()

    # --- 源码守卫 ---
    def test_v37_9_183_marker_and_wiring(self):
        with open(m.__file__, encoding="utf-8") as f:
            src = f.read()
        self.assertIn("V37.9.183", src)
        # resolve_oa_pdf_url 必须 wire 进 fetch_pdf_text
        fpt = src[src.index("def fetch_pdf_text"):src.index("def preprocess_pdf_text")]
        self.assertIn("resolve_oa_pdf_url(url)", fpt,
                      "OA 解析必须 wire 进 fetch_pdf_text 直接派生失败分支")


# ══════════════════════════════════════════════════════════════════════
# 5. PDF 预处理（切 References + 去图表噪声 + 截断）
# ══════════════════════════════════════════════════════════════════════
class TestPreprocessPdfText(unittest.TestCase):
    def test_cuts_references(self):
        raw = "Body content here.\n\nReferences\n[1] Smith et al. 2024\n[2] Doe 2023\n"
        out = m.preprocess_pdf_text(raw)
        self.assertIn("Body content", out)
        self.assertNotIn("Smith et al", out)

    def test_cuts_acknowledgments(self):
        raw = "Core text.\n\nAcknowledgments\nWe thank...\n"
        out = m.preprocess_pdf_text(raw)
        self.assertIn("Core text", out)
        self.assertNotIn("We thank", out)

    def test_cuts_chinese_references(self):
        raw = "正文内容.\n\n参考文献\n[1] 某某 2024"
        out = m.preprocess_pdf_text(raw)
        self.assertIn("正文", out)
        self.assertNotIn("某某 2024", out)

    def test_truncates_to_max_chars(self):
        raw = "x" * 50000
        out = m.preprocess_pdf_text(raw, max_chars=1000)
        self.assertLessEqual(len(out), 1100)  # +" ...[truncated]"
        self.assertIn("[truncated]", out)

    def test_removes_figure_captions(self):
        raw = "Main text.\nFigure 1: A diagram showing something important.\nMore main text."
        out = m.preprocess_pdf_text(raw)
        self.assertIn("Main text", out)
        self.assertIn("More main text", out)


# ══════════════════════════════════════════════════════════════════════
# 6. Fetch 降级路径（lazy import 失败 → degrade）
# ══════════════════════════════════════════════════════════════════════
class TestFetchDegrade(unittest.TestCase):
    def test_tier3_source_not_fetched(self):
        entry = {
            "source_id": "run_hn_fixed",
            "title": "x",
            "abstract": "this is an HN discussion",
            "link": "https://news.ycombinator.com/item?id=1",
            "stars": 5,
        }
        mode, text, reason = m.fetch_full_text(entry)
        self.assertEqual(mode, "abstract_only")
        self.assertIn("tier3", reason)
        self.assertEqual(text, "this is an HN discussion")

    def test_no_link_degrades(self):
        entry = {
            "source_id": "arxiv_monitor",
            "title": "x",
            "abstract": "abstract only",
            "link": "",
            "stars": 5,
        }
        mode, text, reason = m.fetch_full_text(entry)
        self.assertEqual(mode, "abstract_only")

    def test_pdf_fetch_lazy_import_or_http_fail_degrades(self):
        """即使 pdfplumber 未装或 HTTP 失败，必须 degrade 不 raise。"""
        entry = {
            "source_id": "arxiv_monitor",
            "title": "x",
            "abstract": "fallback abstract",
            "link": "https://arxiv.org/abs/invalid.12345",
            "stars": 5,
        }
        # 不走真 HTTP — 用 patch 模拟 urlopen 抛错保证路径被覆盖
        with patch("kb_deep_dive._urlopen") as mock_open:
            mock_open.side_effect = m.urllib.error.URLError("mocked fail")
            mode, text, reason = m.fetch_full_text(entry)
        self.assertEqual(mode, "abstract_only")
        self.assertEqual(text, "fallback abstract")


# ══════════════════════════════════════════════════════════════════════
# 7. Prompt builder
# ══════════════════════════════════════════════════════════════════════
class TestPromptBuilders(unittest.TestCase):
    def _entry(self):
        return {
            "title": "Test Paper",
            "source_label": "📄 ArXiv",
            "link": "https://arxiv.org/abs/2401.12345",
            "stars": 5,
            "source_id": "arxiv_monitor",
        }

    def test_full_text_prompt_includes_structured_sections(self):
        p = m.build_full_text_prompt(self._entry(), "full body content")
        self.assertIn("核心论点", p)
        self.assertIn("论证链", p)
        self.assertIn("实验/证据", p)
        self.assertIn("局限性", p)
        self.assertIn("full body content", p)

    def test_abstract_prompt_forbids_speculation(self):
        p = m.build_abstract_only_prompt(self._entry(), "just the abstract")
        self.assertIn("严禁推测方法细节", p)
        self.assertIn("基于摘要", p)
        self.assertIn("just the abstract", p)

    def test_both_prompts_include_grounding(self):
        e = self._entry()
        self.assertIn("严格约束", m.build_full_text_prompt(e, "x"))
        self.assertIn("严格约束", m.build_abstract_only_prompt(e, "x"))

    def test_build_prompt_for_entry_dispatches(self):
        e = self._entry()
        full = m.build_prompt_for_entry(e, "full_text", "body")
        abs_p = m.build_prompt_for_entry(e, "abstract_only", "body")
        self.assertIn("完整", full)
        self.assertIn("摘要", abs_p)


# ══════════════════════════════════════════════════════════════════════
# 8. Output builder
# ══════════════════════════════════════════════════════════════════════
class TestOutputBuilders(unittest.TestCase):
    def _entry(self):
        return {
            "title": "Paper X",
            "source_label": "📄 ArXiv",
            "link": "https://arxiv.org/abs/2401.12345",
            "stars": 5,
            "source_id": "arxiv_monitor",
        }

    def test_markdown_has_frontmatter(self):
        md = m.build_deep_dive_markdown(
            self._entry(), "full_text", "LLM analysis content here", "", "2026-04-24"
        )
        self.assertTrue(md.startswith("---\n"))
        self.assertIn("date: 2026-04-24", md)
        self.assertIn("type: deep_dive", md)
        self.assertIn("LLM analysis content here", md)

    def test_markdown_includes_degrade_notice(self):
        md = m.build_deep_dive_markdown(
            self._entry(),
            "abstract_only",
            "analysis",
            "PDF fetch failed: HTTP 404",
            "2026-04-24",
        )
        self.assertIn("抓取降级原因", md)
        self.assertIn("HTTP 404", md)

    def test_wa_message_bounded(self):
        long_content = "a" * 5000
        wa = m.build_deep_dive_wa(self._entry(), "full_text", long_content, "2026-04-24")
        # V37.9.35: budget bumped 1400→4000 (WhatsApp client folding confirmed); +50 buffer
        self.assertLessEqual(len(wa), 4050)

    def test_discord_has_markdown_formatting(self):
        disc = m.build_deep_dive_discord(
            self._entry(), "full_text", "content", "2026-04-24"
        )
        self.assertIn("**", disc)
        self.assertIn("Paper X", disc)

    def test_abstract_mode_tag_in_push(self):
        wa = m.build_deep_dive_wa(
            self._entry(), "abstract_only", "content", "2026-04-24"
        )
        self.assertIn("摘要级", wa)
        disc = m.build_deep_dive_discord(
            self._entry(), "abstract_only", "content", "2026-04-24"
        )
        self.assertIn("摘要级", disc)


# ══════════════════════════════════════════════════════════════════════
# 8b. V37.9.21 — Multi-part WA splitting (Dream-style)
# ══════════════════════════════════════════════════════════════════════
class TestSplitTextIntoChunks(unittest.TestCase):
    """V37.9.21: chunk splitter for long LLM content."""

    def test_short_text_returns_single_chunk(self):
        chunks = m._split_text_into_chunks("hello world", 1000)
        self.assertEqual(chunks, ["hello world"])

    def test_empty_text_returns_empty_list(self):
        self.assertEqual(m._split_text_into_chunks("", 1000), [])

    def test_long_text_splits_at_paragraph_boundary(self):
        # First half + paragraph break + second half — splitter prefers \n\n
        text = ("a" * 800) + "\n\n" + ("b" * 800)
        chunks = m._split_text_into_chunks(text, 1000)
        self.assertEqual(len(chunks), 2)
        # First chunk should end at the paragraph (no b's)
        self.assertNotIn("b", chunks[0])
        # Second chunk should be the b's
        self.assertNotIn("a", chunks[1])

    def test_long_text_splits_at_line_boundary_when_no_paragraph(self):
        text = "\n".join(["line " + ("x" * 100) for _ in range(20)])
        chunks = m._split_text_into_chunks(text, 1000)
        self.assertGreater(len(chunks), 1)
        # Each chunk should be at most max_chunk + overhead from boundary search
        for chunk in chunks:
            self.assertLessEqual(len(chunk), 1100)

    def test_no_boundary_falls_back_to_hard_cut(self):
        # Pure 'a' string with no separators
        text = "a" * 3000
        chunks = m._split_text_into_chunks(text, 1000)
        self.assertEqual(len(chunks), 3)
        # Each chunk is exactly 1000 chars (hard cut)
        for chunk in chunks:
            self.assertEqual(len(chunk), 1000)

    def test_preserves_full_content(self):
        text = "Section 1\n\n" + ("a" * 500) + "\n\nSection 2\n\n" + ("b" * 500)
        chunks = m._split_text_into_chunks(text, 600)
        # All a's and b's must appear somewhere across chunks
        joined = "".join(chunks)
        self.assertEqual(joined.count("a"), 500)
        self.assertEqual(joined.count("b"), 500)

    def test_chinese_punctuation_boundary(self):
        # No paragraph or line breaks, but Chinese sentence punctuation
        text = ("内容" * 100) + "。" + ("更多" * 100) + "。" + ("结尾" * 100)
        chunks = m._split_text_into_chunks(text, 300)
        self.assertGreater(len(chunks), 1)
        # First chunk should end at one of the 。 boundaries
        self.assertTrue(chunks[0].endswith("。") or len(chunks[0]) == 300)


class TestBuildDeepDiveWaParts(unittest.TestCase):
    """V37.9.21: multi-part WA builder."""

    def _entry(self):
        return {
            "title": "Test Paper Title",
            "source_label": "📄 ArXiv",
            "link": "https://arxiv.org/abs/2401.12345",
            "stars": 5,
            "source_id": "arxiv_monitor",
        }

    def test_short_content_single_part(self):
        parts = m.build_deep_dive_wa_parts(
            self._entry(), "full_text", "短分析。", "2026-04-27"
        )
        self.assertEqual(len(parts), 1)
        # Single-part: NO [i/N] indicator
        self.assertNotIn("[1/", parts[0])
        # Has header
        self.assertIn("Test Paper Title", parts[0])
        self.assertIn("⭐", parts[0])
        # Has link
        self.assertIn("arxiv.org", parts[0])

    def test_long_content_multi_part(self):
        # V37.9.35: bumped budget 1400→4000, need longer content to trigger split.
        # Each "段落内容。" is 5 chars, 1500 × 5 = 7500 chars > 3800 body budget.
        long_content = "段落内容。" * 1500  # ~7500 chars
        parts = m.build_deep_dive_wa_parts(
            self._entry(), "full_text", long_content, "2026-04-27"
        )
        self.assertGreater(len(parts), 1, "Long content should split into multiple parts")

    def test_each_part_has_indexed_header(self):
        long_content = "x" * 5000
        parts = m.build_deep_dive_wa_parts(
            self._entry(), "full_text", long_content, "2026-04-27"
        )
        total = len(parts)
        self.assertGreater(total, 1)
        for idx, part in enumerate(parts, start=1):
            # Each part must have [i/N] indicator
            self.assertIn(f"[{idx}/{total}]", part,
                f"Part {idx} missing [{idx}/{total}] indicator")
            # Each part has title for context
            self.assertIn("Test Paper Title", part)

    def test_link_only_on_first_part(self):
        long_content = "y" * 5000
        parts = m.build_deep_dive_wa_parts(
            self._entry(), "full_text", long_content, "2026-04-27"
        )
        self.assertGreater(len(parts), 1)
        # Part 1 has link
        self.assertIn("arxiv.org/abs/2401.12345", parts[0])
        # Parts 2+ don't repeat link (saves chars)
        for part in parts[1:]:
            self.assertNotIn("arxiv.org/abs/2401.12345", part)

    def test_each_part_within_budget(self):
        long_content = "z" * 8000
        parts = m.build_deep_dive_wa_parts(
            self._entry(), "full_text", long_content, "2026-04-27"
        )
        for idx, part in enumerate(parts, start=1):
            self.assertLessEqual(len(part), m._WA_BUDGET_PER_PART + 50,
                f"Part {idx} exceeds budget: {len(part)} > {m._WA_BUDGET_PER_PART}")

    def test_abstract_mode_tag_on_each_part(self):
        # V37.9.35: longer content for new 4000 budget
        long_content = "段落。" * 1500  # ~4500 chars
        parts = m.build_deep_dive_wa_parts(
            self._entry(), "abstract_only", long_content, "2026-04-27"
        )
        self.assertGreater(len(parts), 1)
        for part in parts:
            self.assertIn("摘要级", part,
                "All parts must show abstract_only mode tag for context")

    def test_empty_llm_content_returns_empty_list(self):
        parts = m.build_deep_dive_wa_parts(
            self._entry(), "full_text", "", "2026-04-27"
        )
        self.assertEqual(parts, [])

    def test_full_content_preserved_across_parts(self):
        """Critical: splitter must NOT lose content (no truncation)."""
        # Use distinctive markers so we can verify full preservation
        markers = [f"MARKER_{i:04d}" for i in range(50)]
        long_content = "\n".join(
            f"{m_str}: {'x' * 60}" for m_str in markers
        )
        parts = m.build_deep_dive_wa_parts(
            self._entry(), "full_text", long_content, "2026-04-27"
        )
        # Concatenate body across all parts and check every marker is present
        joined = "\n".join(parts)
        for marker in markers:
            self.assertIn(marker, joined,
                f"Content lost: {marker} not in any part")

    def test_backward_compat_build_deep_dive_wa_returns_first_part(self):
        """build_deep_dive_wa() (legacy) returns parts[0] for backward compat."""
        long_content = "abc " * 1000
        wa = m.build_deep_dive_wa(self._entry(), "full_text", long_content, "2026-04-27")
        parts = m.build_deep_dive_wa_parts(
            self._entry(), "full_text", long_content, "2026-04-27"
        )
        self.assertEqual(wa, parts[0])


# ══════════════════════════════════════════════════════════════════════
# 9. run() orchestrator — 端到端路径
# ══════════════════════════════════════════════════════════════════════
class TestRunOrchestrator(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.kb_dir = self.tmp.name
        os.makedirs(os.path.join(self.kb_dir, "sources"), exist_ok=True)

        # Fake registry
        self.registry_path = os.path.join(self.tmp.name, "jobs_registry.yaml")
        with open(self.registry_path, "w", encoding="utf-8") as f:
            f.write(
                "jobs:\n"
                "  - id: arxiv_monitor\n"
                "    enabled: true\n"
                "    kb_source_file: arxiv_daily.md\n"
                "    kb_source_label: ArXiv\n"
            )
        self.today = datetime(2026, 4, 24)
        self.today_str = "2026-04-24"

    def tearDown(self):
        self.tmp.cleanup()

    def _write_arxiv_source(self, entries_text):
        path = os.path.join(self.kb_dir, "sources", "arxiv_daily.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"## {self.today_str}\n\n{entries_text}\n")

    def test_no_candidates_status(self):
        """无合格候选 → status=no_candidates。"""
        self._write_arxiv_source("*Low paper*\n链接：x\n价值：⭐⭐")
        out = m.run(
            self.kb_dir, self.registry_path, today=self.today,
            llm_caller=lambda p: (True, "content", ""),
            fetcher=lambda e: ("full_text", "body", ""),
        )
        self.assertEqual(out["status"], "no_candidates")
        self.assertEqual(out["candidates_count"], 0)

    def test_ok_path_full_text(self):
        self._write_arxiv_source(
            "*Great paper on ontology engines*\n"
            "链接：https://arxiv.org/abs/2401.12345\n"
            "贡献：本体工程新框架\n"
            "价值：⭐⭐⭐⭐⭐"
        )
        out = m.run(
            self.kb_dir, self.registry_path, today=self.today,
            llm_caller=lambda p: (True, "深度分析结果", ""),
            fetcher=lambda e: ("full_text", "FULL BODY", ""),
        )
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["mode"], "full_text")
        self.assertIn("深度分析结果", out["markdown"])
        self.assertIn("Great paper on ontology engines", out["pick"]["title"])
        self.assertGreaterEqual(out["candidates_count"], 1)

    def test_llm_failed_does_not_produce_artifacts(self):
        self._write_arxiv_source(
            "*Paper*\n链接：https://arxiv.org/abs/x\n价值：⭐⭐⭐⭐⭐"
        )
        out = m.run(
            self.kb_dir, self.registry_path, today=self.today,
            llm_caller=lambda p: (False, "", "timeout after 120s"),
            fetcher=lambda e: ("full_text", "BODY", ""),
        )
        self.assertEqual(out["status"], "llm_failed")
        self.assertIn("timeout", out["reason"])
        # Must NOT produce success-only artifacts
        self.assertNotIn("markdown", out)
        self.assertNotIn("wa_message", out)

    def test_degrade_path_abstract_only(self):
        self._write_arxiv_source(
            "*Paper with fetch fail*\n链接：https://arxiv.org/abs/bad\n贡献：摘要内容\n价值：⭐⭐⭐⭐⭐"
        )
        out = m.run(
            self.kb_dir, self.registry_path, today=self.today,
            llm_caller=lambda p: (True, "摘要级分析", ""),
            fetcher=lambda e: ("abstract_only", e.get("abstract", ""), "PDF fetch failed: HTTP 404"),
        )
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["mode"], "abstract_only")
        self.assertIn("HTTP 404", out["degrade_reason"])
        self.assertIn("摘要级", out["discord_message"])

    def test_collector_failed_on_missing_registry(self):
        out = m.run(
            self.kb_dir, "/nonexistent/registry.yaml", today=self.today,
        )
        self.assertEqual(out["status"], "collector_failed")

    def test_ok_path_returns_wa_parts_list(self):
        """V37.9.21: run() must return wa_parts as list[str] for multi-part send.
        V37.9.35: bumped content from ~2400 → ~7000 chars after budget 1400→4000."""
        self._write_arxiv_source(
            "*Long paper analysis*\n"
            "链接：https://arxiv.org/abs/2401.99999\n"
            "贡献：详细贡献\n"
            "价值：⭐⭐⭐⭐⭐"
        )
        long_llm = "深度分析段落。\n\n" * 600  # ~7200 chars guaranteed multi-part at new budget
        out = m.run(
            self.kb_dir, self.registry_path, today=self.today,
            llm_caller=lambda p: (True, long_llm, ""),
            fetcher=lambda e: ("full_text", "BODY", ""),
        )
        self.assertEqual(out["status"], "ok")
        self.assertIn("wa_parts", out)
        self.assertIsInstance(out["wa_parts"], list)
        self.assertGreater(len(out["wa_parts"]), 1,
            "Long LLM content should split into multiple WA parts")
        # Backward-compat: wa_message still equals parts[0]
        self.assertEqual(out["wa_message"], out["wa_parts"][0])


# ══════════════════════════════════════════════════════════════════════
# 10. Shell 脚本守卫
# ══════════════════════════════════════════════════════════════════════
class TestKbDeepDiveShellGuards(unittest.TestCase):
    def setUp(self):
        self.script_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "kb_deep_dive.sh"
        )
        with open(self.script_path, "r", encoding="utf-8") as f:
            self.content = f.read()

    def test_script_exists(self):
        self.assertTrue(os.path.isfile(self.script_path))

    def test_date_uses_hkt_tz(self):
        """V37.9.241 (V37.9.213 ⑨ TZ 一物一形): DATE 必须 HKT——system-local 在
        TZ 漂移时会让 observer 按日期读 deep_dives/{DATE}.md 错位。"""
        # V37.9.249 (B1/D3): TZ config 化 → ${SYSTEM_TZ:-Asia/Hong_Kong}（默认仍 HKT, 可移植覆盖）
        self.assertIn("DATE=$(TZ=${SYSTEM_TZ:-Asia/Hong_Kong} date +%Y-%m-%d)", self.content)
        self.assertNotIn("DATE=$(date +%Y-%m-%d)", self.content,
                         "DATE 回退 system-local（TZ 一物一形被破坏）")

    def test_script_has_system_alert_prefix(self):
        self.assertIn("[SYSTEM_ALERT]", self.content)

    def test_script_uses_env_var_heredoc_pattern(self):
        """V37.5.1 反模式防御：禁止 `echo ... | python3 - << 'PYEOF'`
        因 pipe+heredoc 会让 stdin 被 heredoc 覆盖。"""
        # 除注释外不得有 `| python3 -`
        lines = [l for l in self.content.split("\n") if not l.strip().startswith("#")]
        for ln in lines:
            self.assertNotIn("| python3 -\n", ln + "\n")

    def test_script_sources_notify_sh(self):
        self.assertIn("notify.sh", self.content)

    def test_script_has_set_euo_pipefail(self):
        # V37.9.61 升级到 set -eEuo pipefail (加 -E errtrace, V37.9.58-hotfix4 教训)
        # alternation 接受 set -euo / set -eEuo 两种形式 (向后兼容)
        has_set_e = any(marker in self.content for marker in
                        ["set -euo pipefail", "set -eEuo pipefail"])
        self.assertTrue(has_set_e,
            "kb_deep_dive.sh 必须含 set -euo pipefail 或 set -eEuo pipefail (V37.9.61 升级)")

    def test_script_writes_status_file(self):
        self.assertIn("last_run_deep_dive.json", self.content)

    def test_script_uses_deep_dive_topic(self):
        self.assertIn("--topic deep_dive", self.content)

    def test_script_has_rsync_with_forensics(self):
        """V37.9.14 INV-BACKUP-001 check 4: rsync+MOVESPEED 必须调 incident capture
        V37.9.27 升级: site 用 movespeed_rsync_helper.sh, helper 内部接管 capture"""
        # V37.9.14 旧契约 OR V37.9.27 新契约 任一满足即合规
        has_legacy = "movespeed_incident_capture.sh" in self.content
        has_v37_9_27_helper = "movespeed_rsync_helper.sh" in self.content
        self.assertTrue(has_legacy or has_v37_9_27_helper,
            "V37.9.14: site 需调 movespeed_incident_capture.sh OR "
            "V37.9.27: 通过 movespeed_rsync_helper.sh 间接调用 (helper 内部接管)")

    def test_script_fail_fast_on_llm_failed(self):
        """llm_failed 分支必须 exit 1（fail-fast 契约）"""
        # 简单 heuristic: llm_failed 块内容里必须 exit 1
        idx = self.content.find("llm_failed")
        self.assertGreater(idx, 0)
        # 从该处到后面 500 字符内必须有 exit 1
        region = self.content[idx : idx + 800]
        self.assertIn("exit 1", region)

    # ── V37.9.21 multi-part WA shell guards ──────────────────────────────

    def test_v37_9_21_marker_in_script(self):
        self.assertIn("V37.9.21", self.content,
            "kb_deep_dive.sh must mark V37.9.21 multi-part section")

    def test_script_extracts_wa_parts_via_env_var_heredoc(self):
        """V37.5.1 反模式防御：必须用 env-var heredoc, 不能 echo | python3 -."""
        self.assertIn('COLLECTOR_OUTPUT="$COLLECTOR_OUTPUT"', self.content)
        self.assertIn("wa_parts", self.content,
            "Script must consume wa_parts (V37.9.21 list)")

    def test_script_writes_chunks_to_tempdir(self):
        """V37.9.21: chunks written to mktemp dir, cleaned up on EXIT."""
        self.assertIn("mktemp -d", self.content)
        self.assertIn("WA_CHUNK_DIR", self.content)
        # trap EXIT for cleanup
        self.assertIn("trap", self.content)
        self.assertIn('rm -rf "$WA_CHUNK_DIR"', self.content)

    def test_script_loops_over_chunk_files_with_sleep(self):
        """V37.9.21: send loop iterates *.txt with 1s sleep between (Dream pattern)."""
        # Loop body
        self.assertIn('for chunk_file in "$WA_CHUNK_DIR"/*.txt', self.content)
        # Sleep between segments to avoid WhatsApp out-of-order
        self.assertIn("sleep 1", self.content)
        # WA_PART_IDX counter
        self.assertIn("WA_PART_IDX", self.content)
        # Total parts variable
        self.assertIn("WA_PARTS_TOTAL", self.content)

    def test_script_has_separate_wa_and_discord_send(self):
        """Discord stays single-send (per V37.9.21 design decision 1: only WA/微信 splits)."""
        # V37.9.182: openclaw-weixin → whatsapp 回退（V37.9.179 回退默认通道时漏改本行；
        # 微信仅 48h 客服窗口内可投递、窗口外静默丢失，不适合无人值守 cron；WhatsApp 已恢复）。
        # 强制单通道避免 default 多通道把 discord 重复发（discord 由 DISCORD_MSG 单发）。
        self.assertIn("--channel whatsapp --topic deep_dive", self.content)
        self.assertNotIn("--channel openclaw-weixin", self.content)  # V37.9.182: 不得再指向微信
        self.assertIn("--channel discord --topic deep_dive", self.content)
        # Discord NOT in WA chunk loop — separate notify call
        # Check that DISCORD_MSG is sent ONCE (not in loop)
        # heuristic: count occurrences of DISCORD_MSG sends
        discord_send_count = self.content.count('"$DISCORD_MSG"')
        self.assertGreaterEqual(discord_send_count, 1)

    def test_script_logs_part_count_on_success(self):
        """ops visibility: log message includes 'X/N 段' so failures are visible."""
        self.assertIn("$WA_SEND_OK/$WA_PARTS_TOTAL", self.content)

    # ── V37.9.22: deployment inconsistency window detection ────────────────

    def test_v37_9_22_marker_in_script(self):
        self.assertIn("V37.9.22", self.content,
            "kb_deep_dive.sh must mark V37.9.22 wa_parts missing detection section")

    def test_v37_9_22_warns_when_wa_parts_field_missing(self):
        """Source-level guard: heredoc must check 'wa_parts' not in data and emit WARN to stderr.
        Detects the V37.9.21 deployment-inconsistency-window silent fail
        (new shell + old py temporarily mixed during 2-min auto_deploy polling)."""
        # The check itself
        self.assertIn('"wa_parts" not in data', self.content,
            "Must explicitly check key existence (not None/empty) to distinguish 'old py' from 'picker returned empty'")
        # Must write to stderr (so $(...) capture doesn't swallow it)
        self.assertIn("file=sys.stderr", self.content,
            "WARN must go to stderr to avoid contaminating $(...) command substitution capture")
        # Specific marker mentioning the deployment inconsistency cause
        self.assertIn("部署不一致", self.content,
            "WARN message must explain root cause for ops visibility")


class TestV37_9_22_WaPartsMissingWarn(unittest.TestCase):
    """Behavior-level: extract heredoc and run it to verify stderr WARN behavior.
    Mirrors V37.9.13 test_restart_launchd subprocess-runtime pattern."""

    @classmethod
    def setUpClass(cls):
        cls.script_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "kb_deep_dive.sh"
        )
        with open(cls.script_path, "r", encoding="utf-8") as f:
            cls.content = f.read()

    def _extract_heredoc_python(self):
        """Extract the wa_parts heredoc (between PYEOF markers right after WA_PARTS_TOTAL=)."""
        marker = "WA_PARTS_TOTAL=$(COLLECTOR_OUTPUT="
        start = self.content.find(marker)
        self.assertGreater(start, 0, "WA_PARTS_TOTAL heredoc not found")
        # Find the heredoc body between << 'PYEOF' and PYEOF
        heredoc_open = self.content.find("<< 'PYEOF'", start)
        body_start = self.content.find("\n", heredoc_open) + 1
        body_end = self.content.find("\nPYEOF", body_start)
        return self.content[body_start:body_end]

    def _run_heredoc(self, collector_json):
        import subprocess, tempfile
        body = self._extract_heredoc_python()
        with tempfile.TemporaryDirectory() as td:
            env = {"COLLECTOR_OUTPUT": collector_json, "CHUNK_DIR": td, "PATH": os.environ.get("PATH", "")}
            proc = subprocess.run(
                ["python3", "-c", body],
                env=env, capture_output=True, text=True, timeout=10,
            )
            return proc

    def test_warn_emitted_when_wa_parts_field_missing(self):
        """Old py simulation: collector JSON without wa_parts field → stderr WARN."""
        old_py_output = json.dumps({
            "status": "ok",
            "wa_message": "single-segment fallback",
            "discord_message": "discord version",
        })
        proc = self._run_heredoc(old_py_output)
        self.assertEqual(proc.returncode, 0, f"heredoc failed: {proc.stderr}")
        self.assertIn("WARN", proc.stderr)
        self.assertIn("wa_parts", proc.stderr)
        self.assertIn("部署不一致", proc.stderr)
        # stdout still produces parts count for $(...) capture
        self.assertEqual(proc.stdout.strip(), "1")

    def test_no_warn_when_wa_parts_field_present(self):
        """New py: collector JSON with wa_parts → no WARN."""
        new_py_output = json.dumps({
            "status": "ok",
            "wa_parts": ["part 1 of 2", "part 2 of 2"],
            "wa_message": "part 1 of 2",
            "discord_message": "discord version",
        })
        proc = self._run_heredoc(new_py_output)
        self.assertEqual(proc.returncode, 0)
        self.assertNotIn("WARN", proc.stderr)
        self.assertEqual(proc.stdout.strip(), "2")

    def test_no_warn_when_wa_parts_present_but_empty(self):
        """Edge case: wa_parts=[] should NOT trigger WARN (key exists, just empty).
        Empty list is a valid 'no candidates' state already handled upstream."""
        new_py_empty = json.dumps({
            "status": "ok",
            "wa_parts": [],
            "wa_message": "fallback message",
            "discord_message": "discord",
        })
        proc = self._run_heredoc(new_py_empty)
        self.assertEqual(proc.returncode, 0)
        self.assertNotIn("WARN", proc.stderr,
            "Empty wa_parts is valid (key exists), only missing key should warn")
        # Falls back to wa_message (existing behavior)
        self.assertEqual(proc.stdout.strip(), "1")

    def test_warn_does_not_pollute_stdout_capture(self):
        """Critical contract: WARN must go to stderr, not stdout.
        If WARN went to stdout, WA_PARTS_TOTAL=$(...) would capture 'WARN: ...\\n1'
        and downstream arithmetic would break."""
        old_py_output = json.dumps({"status": "ok", "wa_message": "x"})
        proc = self._run_heredoc(old_py_output)
        # stdout must be ONLY the integer count
        self.assertEqual(proc.stdout.strip(), "1")
        self.assertNotIn("WARN", proc.stdout)


class TestV37960Hotfix3SilentAbortFix(unittest.TestCase):
    """V37.9.60-hotfix3 反向验证守卫:
    send_wa_parts_via_notify / send_wa_parts_via_openclaw 函数末尾必须 `return 0`,
    防 V37.9.21 引入的 bash quirk: `[ X -lt Y ] && sleep 1` 单段时短路返回 1,
    函数 implicit return 1 → set -e 杀 caller → write_status 不跑 → last_run 停滞.

    5/8-5/12 5 天血案: kb_deep_dive cron 每天 LLM ok → markdown 写盘 → 推送成功,
    但 last_run.json 停在 5/7 22:30 status:llm_failed (前一次失败) 因为
    write_status "ok" 在 send_wa_parts 后, 被 silent abort 杀.
    """

    def setUp(self):
        self.script_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "kb_deep_dive.sh"
        )
        with open(self.script_path, "r", encoding="utf-8") as f:
            self.content = f.read()

    def test_send_wa_parts_via_notify_ends_with_return_0(self):
        """送 notify 函数末尾必须显式 `return 0` 防 bash quirk."""
        # 找函数定义到下一个 `}` 之间的内容
        import re
        m = re.search(
            r'send_wa_parts_via_notify\(\)\s*\{(.+?)^\}',
            self.content, re.DOTALL | re.MULTILINE
        )
        self.assertIsNotNone(m, "找不到 send_wa_parts_via_notify 函数定义")
        body = m.group(1)
        self.assertIn("return 0", body,
            "V37.9.60-hotfix3: send_wa_parts_via_notify 末尾必须 `return 0` 防 bash quirk 单段时 "
            "`[ X -lt Y ] && sleep 1` 短路返回 1 → set -e 杀 caller. "
            "血案 5/8-5/12 5 天 kb_deep_dive last_run 不更新.")

    def test_send_wa_parts_via_openclaw_ends_with_return_0(self):
        """送 openclaw 函数末尾同款保护."""
        import re
        m = re.search(
            r'send_wa_parts_via_openclaw\(\)\s*\{(.+?)^\}',
            self.content, re.DOTALL | re.MULTILINE
        )
        self.assertIsNotNone(m, "找不到 send_wa_parts_via_openclaw 函数定义")
        body = m.group(1)
        self.assertIn("return 0", body,
            "V37.9.60-hotfix3: send_wa_parts_via_openclaw 末尾必须 `return 0` 防 bash quirk")

    def test_v37_9_60_hotfix3_marker_present(self):
        """V37.9.60-hotfix3 marker 必须在源码中可追溯."""
        self.assertIn("V37.9.60-hotfix3", self.content,
            "V37.9.60-hotfix3 marker 必须在 kb_deep_dive.sh 注释中追溯血案")

    def test_blood_lesson_references_silent_abort(self):
        """注释必须引用 5/8-5/12 silent 血案 + V37.9.21 quirk 来源."""
        self.assertIn("bash quirk", self.content,
            "V37.9.60-hotfix3: 必须引用 bash quirk 作为根因")
        # 引用 silent + last_run 或 5/8-5/12 之一作为血案证据
        has_blood_marker = any(
            keyword in self.content for keyword in
            ["silent", "5/8-5/12", "set -e 杀 caller", "last_run 停滞", "implicit return"]
        )
        self.assertTrue(has_blood_marker,
            "V37.9.60-hotfix3: 必须含血案锚点 (silent / 5/8-5/12 / set -e 杀 / etc)")


# ══════════════════════════════════════════════════════════════════════
# TIER 源 ↔ registry 一致性守卫（V37.9.186 日落法）
# ══════════════════════════════════════════════════════════════════════
# 退役 pwc 死路径的回归守卫：TIER_1/TIER_2 是 deep_dive "可抓全文/HTML" 的源清单，
# 每个 source_id 必须对应 registry 里 enabled 且声明 kb_source_file 的 job —— 停用的
# job（如 pwc）绝不该残留在活跃抓取档里（否则 classify_tier 把死源当一档 = 误导）。
# 镜像 V37.9.88 血案：daily_observer 的硬编码 JOBS_SUBDIRS 残留 pwc → 读 stale last_run
# → 2 月误告警。本守卫把 TIER 清单绑定到 registry enabled 状态（MR-8 复用生产 loader
# rc.load_sources_from_registry，无 yaml 依赖），未来任何 "停用 job 残留在 TIER" 立即被抓。
class TestV37_9_186_TierRegistryConsistency(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import kb_review_collect as rc
        repo_root = os.path.dirname(os.path.abspath(__file__))
        registry = os.path.join(repo_root, "jobs_registry.yaml")
        srcs = rc.load_sources_from_registry(registry)
        # load_sources_from_registry 只返回 enabled=true 且有 kb_source_file 的 job
        cls.enabled_kb_ids = {d["id"] for d in srcs}
        cls.tier_sources = m.TIER_1_SOURCES | m.TIER_2_SOURCES

    def test_all_tier_sources_are_enabled_registry_jobs(self):
        """每个 TIER_1/TIER_2 源必须是 registry enabled 且有 kb_source_file 的 job。

        停用 job 残留在 TIER = 死路径（V37.9.88 同款 stale-引用 bug）。
        """
        stale = sorted(self.tier_sources - self.enabled_kb_ids)
        self.assertEqual(
            stale, [],
            "TIER_1/TIER_2 含非 enabled-registry 源 = 死路径残留 (V37.9.88 同款): "
            "{}。停用 job 必须从 TIER 移除。".format(stale),
        )

    def test_pwc_retired_from_tier1(self):
        """V37.9.186 回归守卫：pwc（停用 V31 + 脚本删 V37.8.13）不得在 TIER_1。"""
        self.assertNotIn(
            "pwc", m.TIER_1_SOURCES,
            "pwc job 已停用（registry enabled=false）+ 脚本已删，不得残留在 TIER_1_SOURCES "
            "（死路径，镜像 V37.9.88 daily_observer JOBS_SUBDIRS 残留 pwc 血案）",
        )

    def test_pwc_premise_still_holds(self):
        """守卫前提自检：pwc 仍是 registry 里的 disabled job（若被重新启用本守卫需复审）。"""
        self.assertNotIn(
            "pwc", self.enabled_kb_ids,
            "前提变化：pwc 重新出现在 enabled-registry 集合 —— 若 pwc 真复活需重审 TIER 归属",
        )

    def test_tier_sources_nonempty(self):
        """sanity：TIER 清单非空（防 import 失败导致守卫 vacuous）。"""
        self.assertTrue(self.tier_sources, "TIER_1 ∪ TIER_2 不应为空")
        self.assertTrue(self.enabled_kb_ids, "registry enabled 源集不应为空")


class TestV37_9_230_PushFailLoud(unittest.TestCase):
    """V37.9.230 (审计 finding H): push 全失败不再伪装 ok。

    此前 WA 段失败/Discord 失败都只 log WARN, status 恒写 "ok" → 用户什么都没
    收到但 watchdog 静默 (MR-4 家族, 镜像 V37.9.227 cron 状态 fail-loud)。
    修复: WA 0 段 且 Discord 失败 → write_status "push_failed" (watchdog
    catch-all "异常状态" 告警); 任一通道送达 → 保持 ok。
    """

    @classmethod
    def setUpClass(cls):
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "kb_deep_dive.sh")
        with open(path, encoding="utf-8") as f:
            cls.src = f.read()

    def test_push_failed_status_present(self):
        self.assertIn('write_status "push_failed"', self.src)

    def test_all_fail_condition(self):
        """全失败判定 = WA 0 段 AND Discord 失败（任一送达即 ok, 不过度告警）"""
        self.assertIn('[ "$WA_SEND_OK" -eq 0 ] && [ "$DISCORD_SEND_OK" -eq 0 ]', self.src)

    def test_discord_tracked_in_both_branches(self):
        """notify 分支 + openclaw fallback 分支都必须跟踪 Discord 结果（原则 #31）"""
        self.assertEqual(self.src.count("DISCORD_SEND_OK=1"), 2,
                         "两个推送分支各有一处 DISCORD_SEND_OK=1")

    def test_unconditional_ok_retired(self):
        """旧形态（推送后无条件 write_status ok）退役 — ok 只在非全失败分支写一次"""
        self.assertEqual(self.src.count('write_status "ok" "" "$MODE"'), 1,
                         "write_status ok 应只剩 else 分支一处（原两分支各一处无条件写）")

    def test_no_exit_in_push_failed_path(self):
        """push_failed 不 exit 1 — 产出已归档, rsync 取证备份必须继续跑
        (INV-BACKUP-001; 镜像 V37.9.227 不打断后续步骤的取舍)"""
        idx = self.src.find('write_status "push_failed"')
        self.assertNotEqual(idx, -1)
        # push_failed 到 rsync 备份之间不得有 exit
        rsync_idx = self.src.find("movespeed_rsync_helper.sh", idx)
        self.assertNotEqual(rsync_idx, -1, "push_failed 之后必须还有 rsync 备份步骤")
        between = self.src[idx:rsync_idx]
        self.assertNotIn("exit 1", between)

    def test_v37_9_230_marker(self):
        self.assertIn("V37.9.230", self.src)


class TestV37_9_233_AnalyzedBanList(unittest.TestCase):
    """V37.9.233: 已分析论文 14 天 ban-list（镜像 dream V37.9.68）。

    血案 (2026-07-03 用户 Mac Mini 降级原因明细): 同一 ACM 付费墙 DOI
    (doi.org/10.1145/3774904.3792985) 被 picker 连选 ~20 天 — 每天霸占深度
    分析位且必然 abstract_only (30 天 77% 降级率的主体), 用户连收近似重复分析。
    """

    _ACM = "https://doi.org/10.1145/3774904.3792985"

    def _write_dive(self, d, date_str, link):
        with open(os.path.join(d, f"{date_str}.md"), "w", encoding="utf-8") as f:
            f.write(f"---\ndate: {date_str}\ntype: deep_dive\nmode: abstract_only\n"
                    f"source_id: dblp\nsource_label: DBLP\nstars: 4\n"
                    f"link: {link}\n---\n\n# 分析\n")

    def test_recent_file_link_loaded(self):
        import tempfile
        from datetime import datetime
        with tempfile.TemporaryDirectory() as td:
            today = datetime(2026, 7, 3)
            self._write_dive(td, "2026-07-01", self._ACM + "/")
            links = m.load_recent_analyzed_links(td, today=today)
            self.assertIn(self._ACM, links, "尾斜杠须归一化后入集合")

    def test_old_file_outside_window_excluded(self):
        import tempfile
        from datetime import datetime
        with tempfile.TemporaryDirectory() as td:
            today = datetime(2026, 7, 3)
            self._write_dive(td, "2026-06-01", self._ACM)
            links = m.load_recent_analyzed_links(td, today=today)
            self.assertEqual(links, set(), ">14 天的旧分析不进 ban-list")

    def test_missing_dir_fail_open(self):
        links = m.load_recent_analyzed_links("/nonexistent/deep_dives")
        self.assertEqual(links, set())

    def test_non_date_filename_skipped(self):
        import tempfile
        from datetime import datetime
        with tempfile.TemporaryDirectory() as td:
            with open(os.path.join(td, "notes.md"), "w") as f:
                f.write("link: https://x.example/paper\n")
            links = m.load_recent_analyzed_links(
                td, today=datetime(2026, 7, 3))
            self.assertEqual(links, set())

    def test_filter_bans_matching_link(self):
        cands = [
            {"title": "ACM 重复论文", "link": self._ACM, "stars": 5, "source_id": "dblp"},
            {"title": "新论文", "link": "https://arxiv.org/abs/2607.00001", "stars": 4,
             "source_id": "arxiv_monitor"},
        ]
        kept, banned = m.filter_recently_analyzed(cands, {self._ACM})
        self.assertEqual(banned, 1)
        self.assertEqual([c["title"] for c in kept], ["新论文"])

    def test_filter_empty_recent_unchanged(self):
        cands = [{"title": "a", "link": "https://x", "stars": 4, "source_id": "dblp"}]
        kept, banned = m.filter_recently_analyzed(cands, set())
        self.assertEqual((kept, banned), (cands, 0))

    def test_empty_link_candidate_never_banned(self):
        cands = [{"title": "无链接候选", "link": "", "stars": 4, "source_id": "hn"}]
        kept, banned = m.filter_recently_analyzed(cands, {self._ACM})
        self.assertEqual(banned, 0)
        self.assertEqual(len(kept), 1)

    def test_blood_lesson_repeat_pick_broken(self):
        """血案端到端: 昨天分析过的 ACM DOI 今天再入候选 → 被 ban, picker 选新论文
        (修复前: ACM 高分每天胜出 → 20 天重复)"""
        import tempfile
        from datetime import datetime
        with tempfile.TemporaryDirectory() as td:
            today = datetime(2026, 7, 3)
            self._write_dive(td, "2026-07-02", self._ACM)
            recent = m.load_recent_analyzed_links(td, today=today)
            cands = [
                {"title": "ACM 重复论文", "link": self._ACM, "stars": 5, "source_id": "dblp"},
                {"title": "新论文", "link": "https://arxiv.org/abs/2607.00001", "stars": 4,
                 "source_id": "arxiv_monitor"},
            ]
            kept, banned = m.filter_recently_analyzed(cands, recent)
            pick = m.pick_top(kept)
            self.assertEqual(banned, 1)
            self.assertEqual(pick["title"], "新论文")

    def test_run_wiring_before_pick_top(self):
        """源码守卫: run() 里 filter_recently_analyzed 在 pick_top 之前"""
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "kb_deep_dive.py"), encoding="utf-8") as f:
            s = f.read()
        i_run = s.index("def run(")
        i_filter = s.index("filter_recently_analyzed(candidates", i_run)
        i_pick = s.index("pick = pick_top(candidates)", i_run)
        self.assertLess(i_filter, i_pick)

    def test_dedup_window_locked_14(self):
        """设计锁定: 与 dream V37.9.68 主题 ban-list 同 14 天窗口"""
        self.assertEqual(m.DEDUP_WINDOW_DAYS, 14)

    def test_v37_9_233_marker(self):
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "kb_deep_dive.py"), encoding="utf-8") as f:
            self.assertIn("V37.9.233", f.read())


class TestV37_9_260_BanListBoundary(unittest.TestCase):
    """V37.9.260: ban-list off-by-one 边界修复（today 归一化午夜）。

    血案 (2026-07-08 daily_observer deep_dive_repeat MED): 链接
    doi.org/10.1145/3774935.3806169 在 06-20 与 07-04 (恰 14 天) 被重复分析。
    根因: load_recent_analyzed_links 的 today=datetime.now() 带当前时间分量
    (deep_dive 22:30 HKT 跑) 而 file_date 是午夜 → 恰 14 个日历天前的论文
    (file_date 午夜 < cutoff 带时间) 逃逸 ban → 有效窗口缩到 13d+22.5h。
    """

    _LINK = "https://doi.org/10.1145/3774935.3806169"

    def _write_dive(self, d, date_str, link):
        with open(os.path.join(d, f"{date_str}.md"), "w", encoding="utf-8") as f:
            f.write(f"---\ndate: {date_str}\ntype: deep_dive\nmode: abstract_only\n"
                    f"source_id: dblp\nsource_label: DBLP\nstars: 4\n"
                    f"link: {link}\n---\n\n# 分析\n")

    def test_exact_window_boundary_banned_despite_time_of_day(self):
        """血案回归: 恰 14 天前的论文在 22:30 跑时必须仍被 ban（修复前逃逸）。"""
        import tempfile
        from datetime import datetime
        with tempfile.TemporaryDirectory() as td:
            # deep_dive 生产 22:30 HKT 跑 → today 带时间分量
            today = datetime(2026, 7, 4, 22, 30, 0)
            self._write_dive(td, "2026-06-20", self._LINK)  # 恰 14 个日历天前
            links = m.load_recent_analyzed_links(td, today=today)
            self.assertIn(m._normalize_link(self._LINK), links,
                          "恰 14 日历天前的论文必须在 ban 集内（今日归一化午夜）")

    def test_boundary_end_to_end_repeat_blocked(self):
        """端到端: 恰 14 天前分析过的链接今天 22:30 再入候选 → 被 ban。"""
        import tempfile
        from datetime import datetime
        with tempfile.TemporaryDirectory() as td:
            today = datetime(2026, 7, 4, 22, 30, 0)
            self._write_dive(td, "2026-06-20", self._LINK)
            recent = m.load_recent_analyzed_links(td, today=today)
            cands = [
                {"title": "重复论文", "link": self._LINK, "stars": 5, "source_id": "dblp"},
                {"title": "新论文", "link": "https://arxiv.org/abs/2607.99999",
                 "stars": 4, "source_id": "arxiv_monitor"},
            ]
            kept, banned = m.filter_recently_analyzed(cands, recent)
            self.assertEqual(banned, 1)
            self.assertEqual([c["title"] for c in kept], ["新论文"])

    def test_beyond_window_still_excluded_with_time_of_day(self):
        """15 天前（超窗口）即便 today 带时间仍正确排除（不误 ban）。"""
        import tempfile
        from datetime import datetime
        with tempfile.TemporaryDirectory() as td:
            today = datetime(2026, 7, 4, 22, 30, 0)
            self._write_dive(td, "2026-06-19", self._LINK)  # 15 个日历天前
            links = m.load_recent_analyzed_links(td, today=today)
            self.assertEqual(links, set(), ">14 日历天前不进 ban 集")

    def test_v37_9_260_marker(self):
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "kb_deep_dive.py"), encoding="utf-8") as f:
            self.assertIn("V37.9.260", f.read())


if __name__ == "__main__":
    unittest.main()
