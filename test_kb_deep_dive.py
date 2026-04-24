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
        self.assertLessEqual(len(wa), 1450)

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
        self.assertIn("set -euo pipefail", self.content)

    def test_script_writes_status_file(self):
        self.assertIn("last_run_deep_dive.json", self.content)

    def test_script_uses_deep_dive_topic(self):
        self.assertIn("--topic deep_dive", self.content)

    def test_script_has_rsync_with_forensics(self):
        """V37.9.14 INV-BACKUP-001 check 4: rsync+MOVESPEED 必须调 incident capture"""
        self.assertIn("rsync", self.content)
        self.assertIn("movespeed_incident_capture.sh", self.content)

    def test_script_fail_fast_on_llm_failed(self):
        """llm_failed 分支必须 exit 1（fail-fast 契约）"""
        # 简单 heuristic: llm_failed 块内容里必须 exit 1
        idx = self.content.find("llm_failed")
        self.assertGreater(idx, 0)
        # 从该处到后面 500 字符内必须有 exit 1
        region = self.content[idx : idx + 800]
        self.assertIn("exit 1", region)


if __name__ == "__main__":
    unittest.main()
