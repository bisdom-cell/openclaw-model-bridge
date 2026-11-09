#!/usr/bin/env python3
"""V37.9.56 Sub-Stage 4c — test_top_alignment_picker.py.

Test layers:
  1. parse_alignment_from_content — 6-field LLM output 解析正确性
  2. _fallback_extract_star_count — alignment scorer 缺失时 fallback
  3. scan_source_results — llm_results.jsonl 读取 + FAIL-OPEN
  4. collect_all_picks — 10 source 聚合 + min_stars 阈值过滤 (V37.9.112: +ai_leaders_bsky)
  5. rank_picks — 排序契约 (stars desc + priority asc + title len desc)
  6. format_top_picks_block — markdown 段格式 + 标题截断
  7. pick_top_aligned — 端到端 orchestrator + FAIL-OPEN
  8. CLI / source-level guards (V37.9.56 marker + 反向验证守卫)
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import top_alignment_picker as tap

# 让 import 路径绝对
REPO_ROOT = os.path.dirname(os.path.abspath(__file__))


class TestParseAlignmentFromContent(unittest.TestCase):
    """parse_alignment_from_content 单测 (V37.9.56 核心 parser)."""

    def test_full_6field_content(self):
        content = """📌 标题: Self-Refining Reasoning Models
🔑 核心贡献:
- 新 RLHF 方法
💡 关键方法: chain-of-thought
🎯 实践启发: agent runtime
⭐⭐⭐⭐⭐ 评级: AI infra
🎚️ 项目对齐度: ⭐⭐⭐⭐⭐ / 直接相关 agent reliability
"""
        parsed = tap.parse_alignment_from_content(content)
        self.assertEqual(parsed["cn_title"], "Self-Refining Reasoning Models")
        self.assertEqual(parsed["alignment_stars"], 5)
        self.assertEqual(parsed["rating_stars"], 5)
        self.assertIn("直接相关", parsed["alignment_reason"])

    def test_alignment_4_stars(self):
        content = """📌 标题: Paper X
🎚️ 项目对齐度: ⭐⭐⭐⭐ / 间接相关 KB RAG
"""
        parsed = tap.parse_alignment_from_content(content)
        self.assertEqual(parsed["alignment_stars"], 4)
        self.assertIn("间接相关", parsed["alignment_reason"])

    def test_alignment_3_below_threshold(self):
        content = """📌 标题: Paper Low
🎚️ 项目对齐度: ⭐⭐⭐ / 一般 AI 趋势
"""
        parsed = tap.parse_alignment_from_content(content)
        # 3 stars 仍能解析 (阈值过滤在上层)
        self.assertEqual(parsed["alignment_stars"], 3)

    def test_alignment_fallback_emoji_no_variation_selector(self):
        """V37.9.51 fallback: 🎚 (无 variation selector U+FE0F)."""
        content = """📌 标题: Test
🎚 项目对齐度: ⭐⭐⭐⭐ / 关于 ontology
"""
        parsed = tap.parse_alignment_from_content(content)
        self.assertEqual(parsed["alignment_stars"], 4)

    def test_chinese_title_extraction(self):
        content = """📌 中文标题: 大模型推理优化
🎚️ 项目对齐度: ⭐⭐⭐⭐ / agent runtime
"""
        parsed = tap.parse_alignment_from_content(content)
        self.assertEqual(parsed["cn_title"], "大模型推理优化")

    def test_no_alignment_field(self):
        """5 字段输出 (V37.9.39-44 时期), 无 🎚️ → alignment_stars=0."""
        content = """📌 标题: Old Format
🔑 要点: foo
⭐⭐⭐⭐ 评级: x
"""
        parsed = tap.parse_alignment_from_content(content)
        self.assertEqual(parsed["alignment_stars"], 0)
        self.assertEqual(parsed["cn_title"], "Old Format")

    def test_empty_content(self):
        parsed = tap.parse_alignment_from_content("")
        self.assertEqual(parsed["cn_title"], "")
        self.assertEqual(parsed["alignment_stars"], 0)

    def test_non_string_content(self):
        parsed = tap.parse_alignment_from_content(None)
        self.assertEqual(parsed["alignment_stars"], 0)
        parsed = tap.parse_alignment_from_content(123)
        self.assertEqual(parsed["alignment_stars"], 0)

    def test_alignment_stars_capped_at_5(self):
        """偶发 LLM 输出 6+ ⭐ → 截断到 5."""
        content = "🎚️ 项目对齐度: ⭐⭐⭐⭐⭐⭐⭐ / 极相关\n"
        parsed = tap.parse_alignment_from_content(content)
        self.assertEqual(parsed["alignment_stars"], 5)

    def test_rating_vs_alignment_separate(self):
        """rating ⭐ 字段不污染 alignment ⭐ 字段."""
        content = """📌 标题: Test
⭐⭐⭐ 评级: 推荐场景 x
🎚️ 项目对齐度: ⭐⭐⭐⭐⭐ / 直接相关
"""
        parsed = tap.parse_alignment_from_content(content)
        self.assertEqual(parsed["rating_stars"], 3)
        self.assertEqual(parsed["alignment_stars"], 5)

    def test_alignment_reason_truncation(self):
        long_reason = "x" * 200
        content = f"🎚️ 项目对齐度: ⭐⭐⭐⭐ / {long_reason}\n"
        parsed = tap.parse_alignment_from_content(content)
        # alignment_reason 截断到 60 字
        self.assertLessEqual(len(parsed["alignment_reason"]), 60)


class TestFallbackExtractStarCount(unittest.TestCase):
    """_fallback_extract_star_count 单测 (project_alignment_scorer 缺失场景)."""

    def test_basic_stars(self):
        self.assertEqual(tap._fallback_extract_star_count("⭐⭐⭐⭐"), 4)
        self.assertEqual(tap._fallback_extract_star_count("⭐⭐"), 2)

    def test_max_consecutive(self):
        """多段 ⭐ → 取最长段."""
        self.assertEqual(tap._fallback_extract_star_count("⭐ rating ⭐⭐⭐⭐⭐ alignment"), 5)

    def test_no_stars(self):
        self.assertEqual(tap._fallback_extract_star_count("no stars here"), 0)

    def test_empty_or_none(self):
        self.assertEqual(tap._fallback_extract_star_count(""), 0)
        self.assertEqual(tap._fallback_extract_star_count(None), 0)
        self.assertEqual(tap._fallback_extract_star_count(123), 0)

    def test_clamped_to_5(self):
        self.assertEqual(tap._fallback_extract_star_count("⭐" * 10), 5)


class TestScanSourceResults(unittest.TestCase):
    """scan_source_results 单测 (llm_results.jsonl 读取 + FAIL-OPEN)."""

    def test_missing_cache_dir(self):
        result = tap.scan_source_results("/nonexistent/cache/dir")
        self.assertEqual(result, [])

    def test_missing_results_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = tap.scan_source_results(tmp)
            self.assertEqual(result, [])

    def test_normal_jsonl(self):
        with tempfile.TemporaryDirectory() as tmp:
            jsonl_path = os.path.join(tmp, "llm_results.jsonl")
            with open(jsonl_path, "w", encoding="utf-8") as f:
                f.write(json.dumps({"idx": 0, "content": "🎚️ 项目对齐度: ⭐⭐⭐⭐⭐", "failed": False}) + "\n")
                f.write(json.dumps({"idx": 1, "content": "🎚️ 项目对齐度: ⭐⭐⭐", "failed": False}) + "\n")
            result = tap.scan_source_results(tmp)
            self.assertEqual(len(result), 2)
            self.assertEqual(result[0]["idx"], 0)

    def test_skip_failed_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            jsonl_path = os.path.join(tmp, "llm_results.jsonl")
            with open(jsonl_path, "w", encoding="utf-8") as f:
                f.write(json.dumps({"idx": 0, "content": "ok", "failed": False}) + "\n")
                f.write(json.dumps({"idx": 1, "content": "", "failed": True, "fail_reason": "timeout"}) + "\n")
            result = tap.scan_source_results(tmp)
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0]["idx"], 0)

    def test_corrupted_jsonl_skips_line(self):
        """V37.9.46 同款契约: 单行损坏不阻塞."""
        with tempfile.TemporaryDirectory() as tmp:
            jsonl_path = os.path.join(tmp, "llm_results.jsonl")
            with open(jsonl_path, "w", encoding="utf-8") as f:
                f.write(json.dumps({"idx": 0, "content": "ok1", "failed": False}) + "\n")
                f.write("{ broken json !\n")
                f.write(json.dumps({"idx": 2, "content": "ok2", "failed": False}) + "\n")
            result = tap.scan_source_results(tmp)
            self.assertEqual(len(result), 2)

    def test_empty_content_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            jsonl_path = os.path.join(tmp, "llm_results.jsonl")
            with open(jsonl_path, "w", encoding="utf-8") as f:
                f.write(json.dumps({"idx": 0, "content": "", "failed": False}) + "\n")
                f.write(json.dumps({"idx": 1, "content": "   ", "failed": False}) + "\n")
            result = tap.scan_source_results(tmp)
            self.assertEqual(len(result), 0)


class TestCollectAllPicks(unittest.TestCase):
    """collect_all_picks 单测 (聚合 + 阈值过滤)."""

    def test_no_sources_in_dev(self):
        """Dev 环境无 cache → 空 list 不抛."""
        with tempfile.TemporaryDirectory() as tmp:
            picks = tap.collect_all_picks(repo_root=tmp, min_stars=4)
            self.assertEqual(picks, [])

    def test_filter_by_min_stars(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = os.path.join(tmp, "jobs/hf_papers/cache")
            os.makedirs(cache_dir)
            with open(os.path.join(cache_dir, "llm_results.jsonl"), "w", encoding="utf-8") as f:
                f.write(json.dumps({"idx": 0, "content": "📌 标题: A\n🎚️ 项目对齐度: ⭐⭐⭐⭐⭐ / good", "failed": False}) + "\n")
                f.write(json.dumps({"idx": 1, "content": "📌 标题: B\n🎚️ 项目对齐度: ⭐⭐⭐ / weak", "failed": False}) + "\n")
                f.write(json.dumps({"idx": 2, "content": "📌 标题: C\n🎚️ 项目对齐度: ⭐⭐⭐⭐ / mid", "failed": False}) + "\n")
            picks = tap.collect_all_picks(repo_root=tmp, min_stars=4)
            self.assertEqual(len(picks), 2)
            titles = {p["cn_title"] for p in picks}
            self.assertEqual(titles, {"A", "C"})
            # source_id / source_display / source_priority 注入
            self.assertEqual(picks[0]["source_id"], "hf_papers")
            self.assertEqual(picks[0]["source_display"], "HF精选")
            self.assertEqual(picks[0]["source_priority"], 1)

    def test_min_stars_5(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = os.path.join(tmp, "jobs/semantic_scholar/cache")
            os.makedirs(cache_dir)
            with open(os.path.join(cache_dir, "llm_results.jsonl"), "w", encoding="utf-8") as f:
                f.write(json.dumps({"idx": 0, "content": "🎚️ 项目对齐度: ⭐⭐⭐⭐", "failed": False}) + "\n")
                f.write(json.dumps({"idx": 1, "content": "🎚️ 项目对齐度: ⭐⭐⭐⭐⭐", "failed": False}) + "\n")
            picks = tap.collect_all_picks(repo_root=tmp, min_stars=5)
            self.assertEqual(len(picks), 1)


class TestRankPicks(unittest.TestCase):
    """rank_picks 单测 (排序契约: stars desc + priority asc + title len desc)."""

    def test_sort_by_stars_desc(self):
        picks = [
            {"alignment_stars": 4, "source_priority": 1, "cn_title": "a", "source_display": "x"},
            {"alignment_stars": 5, "source_priority": 7, "cn_title": "b", "source_display": "y"},
        ]
        ranked = tap.rank_picks(picks)
        self.assertEqual(ranked[0]["alignment_stars"], 5)
        self.assertEqual(ranked[1]["alignment_stars"], 4)

    def test_tie_break_by_source_priority(self):
        """⭐ 同时, source priority asc (论文类 > tweet 类)."""
        picks = [
            {"alignment_stars": 5, "source_priority": 7, "cn_title": "hn item", "source_display": "HN"},
            {"alignment_stars": 5, "source_priority": 1, "cn_title": "hf paper", "source_display": "HF"},
        ]
        ranked = tap.rank_picks(picks)
        self.assertEqual(ranked[0]["source_display"], "HF")

    def test_tie_break_by_title_length_desc(self):
        """⭐ + priority 同时, title 长度 desc (信息密度代理)."""
        picks = [
            {"alignment_stars": 5, "source_priority": 1, "cn_title": "x", "source_display": "A"},
            {"alignment_stars": 5, "source_priority": 1, "cn_title": "much longer title here", "source_display": "A"},
        ]
        ranked = tap.rank_picks(picks)
        self.assertEqual(ranked[0]["cn_title"], "much longer title here")

    def test_top_n_truncation(self):
        picks = [
            {"alignment_stars": 5, "source_priority": i, "cn_title": f"p{i}", "source_display": "S"}
            for i in range(1, 11)
        ]
        ranked = tap.rank_picks(picks, top_n=5)
        self.assertEqual(len(ranked), 5)
        self.assertEqual(ranked[0]["cn_title"], "p1")

    def test_empty_input(self):
        self.assertEqual(tap.rank_picks([]), [])

    def test_missing_fields_default_safely(self):
        """缺字段 entry 用默认值排序不崩."""
        picks = [
            {"source_display": "X"},  # 完全缺 stars/priority/title
            {"alignment_stars": 5, "source_priority": 1, "cn_title": "good", "source_display": "Y"},
        ]
        ranked = tap.rank_picks(picks)
        self.assertEqual(ranked[0]["source_display"], "Y")


class TestFormatTopPicksBlock(unittest.TestCase):
    """format_top_picks_block 单测."""

    def test_empty_returns_empty_string(self):
        self.assertEqual(tap.format_top_picks_block([]), "")

    def test_no_header_no_picks(self):
        self.assertEqual(tap.format_top_picks_block([]), "")

    def test_block_format_one_line_per_pick(self):
        picks = [
            {"alignment_stars": 5, "source_display": "HF", "cn_title": "Paper A", "alignment_reason": "agent reliability"},
            {"alignment_stars": 4, "source_display": "S2", "cn_title": "Paper B", "alignment_reason": "kb rag"},
        ]
        block = tap.format_top_picks_block(picks)
        lines = block.split("\n")
        self.assertEqual(len(lines), 2)
        self.assertIn("⭐⭐⭐⭐⭐", lines[0])
        self.assertIn("[HF]", lines[0])
        self.assertIn("Paper A", lines[0])
        self.assertIn("agent reliability", lines[0])

    def test_title_truncation_40_chars(self):
        long_title = "x" * 100
        picks = [{"alignment_stars": 4, "source_display": "S", "cn_title": long_title, "alignment_reason": "r"}]
        block = tap.format_top_picks_block(picks)
        # 40 字 + … = 41 chars 在 [<source>] 后
        self.assertIn("…", block)

    def test_no_reason_omits_slash(self):
        picks = [{"alignment_stars": 4, "source_display": "S", "cn_title": "T", "alignment_reason": ""}]
        block = tap.format_top_picks_block(picks)
        # 无 reason 时不应有 " / " 分隔符
        self.assertNotIn(" / ", block)

    def test_stars_clamped(self):
        """⭐ count 超 5 / 负数 / 非 int 仍 emit 合理结果."""
        picks = [{"alignment_stars": 10, "source_display": "S", "cn_title": "T", "alignment_reason": ""}]
        block = tap.format_top_picks_block(picks)
        # 10 ⭐ 截断到 5
        self.assertEqual(block.count("⭐"), 5)


class TestPickTopAlignedOrchestrator(unittest.TestCase):
    """pick_top_aligned 端到端 orchestrator 单测."""

    def test_no_picks_dev_environment(self):
        """Dev 无 cache → status=no_picks block=空 不抛."""
        with tempfile.TemporaryDirectory() as tmp:
            result = tap.pick_top_aligned(repo_root=tmp)
            self.assertEqual(result["status"], "no_picks")
            self.assertEqual(result["picks_total"], 0)
            self.assertEqual(result["picks_top"], [])
            self.assertEqual(result["block"], "")

    def test_ok_path_with_picks(self):
        with tempfile.TemporaryDirectory() as tmp:
            hf_cache = os.path.join(tmp, "jobs/hf_papers/cache")
            os.makedirs(hf_cache)
            with open(os.path.join(hf_cache, "llm_results.jsonl"), "w", encoding="utf-8") as f:
                f.write(json.dumps({"idx": 0, "content": "📌 标题: Top Paper\n🎚️ 项目对齐度: ⭐⭐⭐⭐⭐ / agent runtime", "failed": False}) + "\n")
            result = tap.pick_top_aligned(repo_root=tmp)
            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["picks_total"], 1)
            self.assertIn("Top Paper", result["block"])

    def test_picks_from_multiple_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            for src_id, title, stars in [
                ("hf_papers", "HF Top", 5),
                ("rss_blogs", "Blog Top", 4),
                ("hn", "HN Top", 5),
            ]:
                # V37.9.57: use cache_paths[0] (dev mode, relative to repo_root)
                src_meta = next(s for s in tap.ALIGNED_SOURCES if s["id"] == src_id)
                cache_dir = os.path.join(tmp, src_meta["cache_paths"][0])
                os.makedirs(cache_dir)
                star_emoji = "⭐" * stars
                with open(os.path.join(cache_dir, "llm_results.jsonl"), "w", encoding="utf-8") as f:
                    f.write(json.dumps({"idx": 0, "content": f"📌 标题: {title}\n🎚️ 项目对齐度: {star_emoji} / reason", "failed": False}) + "\n")
            result = tap.pick_top_aligned(repo_root=tmp)
            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["picks_total"], 3)
            # HF (5⭐ priority 1) 排第一, HN (5⭐ priority 7) 第二, Blog (4⭐) 第三
            self.assertEqual(result["picks_top"][0]["source_id"], "hf_papers")
            self.assertEqual(result["picks_top"][1]["source_id"], "hn")
            self.assertEqual(result["picks_top"][2]["source_id"], "rss_blogs")


class TestCliInterface(unittest.TestCase):
    """CLI 单测 (--json / --block-only / argparse)."""

    def test_cli_no_picks_exit_0(self):
        """Dev 环境跑 CLI → exit 0 即使 no_picks."""
        # subprocess 用 tempdir 当 repo_root, 防止读真实 cache
        with tempfile.TemporaryDirectory() as tmp:
            r = subprocess.run(
                [sys.executable, os.path.join(REPO_ROOT, "top_alignment_picker.py"),
                 "--repo-root", tmp],
                capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(r.returncode, 0)
            self.assertIn("no picks", r.stdout)

    def test_cli_json_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = subprocess.run(
                [sys.executable, os.path.join(REPO_ROOT, "top_alignment_picker.py"),
                 "--repo-root", tmp, "--json"],
                capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(r.returncode, 0)
            data = json.loads(r.stdout)
            self.assertEqual(data["status"], "no_picks")
            self.assertIn("picks_top", data)

    def test_cli_block_only_mode(self):
        """--block-only 输出只有 block 字面量 (适合 BLOCK=$(...))."""
        with tempfile.TemporaryDirectory() as tmp:
            hf_cache = os.path.join(tmp, "jobs/hf_papers/cache")
            os.makedirs(hf_cache)
            with open(os.path.join(hf_cache, "llm_results.jsonl"), "w", encoding="utf-8") as f:
                f.write(json.dumps({"idx": 0, "content": "📌 标题: X\n🎚️ 项目对齐度: ⭐⭐⭐⭐⭐", "failed": False}) + "\n")
            r = subprocess.run(
                [sys.executable, os.path.join(REPO_ROOT, "top_alignment_picker.py"),
                 "--repo-root", tmp, "--block-only"],
                capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(r.returncode, 0)
            # --block-only mode: stdout starts with markdown list, no "no picks" prefix
            self.assertIn("⭐⭐⭐⭐⭐", r.stdout)
            self.assertNotIn("no picks", r.stdout)
            self.assertNotIn("Top", r.stdout)  # 无 "Top 5:" 标识


class TestSourceLevelGuards(unittest.TestCase):
    """源码级守卫 (V37.9.46 同款反向验证模式)."""

    def setUp(self):
        with open(os.path.join(REPO_ROOT, "top_alignment_picker.py"), encoding="utf-8") as f:
            self.src = f.read()

    def test_v37_9_56_marker_present(self):
        self.assertIn("V37.9.56", self.src)
        self.assertIn("Sub-Stage 4c", self.src)

    def test_9_aligned_sources_registered(self):
        """ALIGNED_SOURCES 必须含全部 9 个 source id (V37.9.112: +ai_leaders_bsky; V37.9.383: dblp 退役)."""
        for src_id in ["hf_papers", "semantic_scholar", "arxiv",
                       "github_trending", "rss_blogs", "ai_leaders_blogs",
                       "ai_leaders_x", "ai_leaders_bsky", "hn"]:
            self.assertIn(f'"id": "{src_id}"', self.src,
                          f"ALIGNED_SOURCES 缺 {src_id}")
        # V37.9.383: dblp 已退役。抓取失败时提前退出不清空 llm_results.jsonl，
        # 而 picker 不看文件新旧 → 残留会把退役前的旧论文当今日 Top 5 推出去
        import top_alignment_picker as tap
        self.assertNotIn("dblp", [s["id"] for s in tap.ALIGNED_SOURCES])

    def test_ai_leaders_blogs_is_blog_tier_priority_5(self):
        """V37.9.108: ai_leaders_blogs (长文学者观点) = 博客档 priority 5 (同 rss_blogs, > tweet 类 6)."""
        import top_alignment_picker as tap
        src = next((s for s in tap.ALIGNED_SOURCES if s["id"] == "ai_leaders_blogs"), None)
        self.assertIsNotNone(src, "ALIGNED_SOURCES 应含 ai_leaders_blogs")
        self.assertEqual(src["priority"], 5, "博客档 priority 5")
        self.assertIn("ai_leaders_blogs/cache", str(src["cache_paths"]))
        # Mac Mini layout (~/.openclaw/jobs/) 候选必须在 (V37.9.57-hotfix 教训)
        self.assertTrue(any("~/.openclaw/jobs/ai_leaders_blogs" in p for p in src["cache_paths"]))

    def test_ai_leaders_bsky_is_social_tier_priority_6(self):
        """V37.9.112: ai_leaders_bsky (实时短帖) = 社媒档 priority 6 (同 ai_leaders_x tweet 类, > 博客 5)."""
        import top_alignment_picker as tap
        src = next((s for s in tap.ALIGNED_SOURCES if s["id"] == "ai_leaders_bsky"), None)
        self.assertIsNotNone(src, "ALIGNED_SOURCES 应含 ai_leaders_bsky")
        self.assertEqual(src["priority"], 6, "社媒档 priority 6 (同 ai_leaders_x)")
        self.assertIn("ai_leaders_bsky/cache", str(src["cache_paths"]))
        # Mac Mini layout (~/.openclaw/jobs/) 候选必须在 (V37.9.57-hotfix 教训)
        self.assertTrue(any("~/.openclaw/jobs/ai_leaders_bsky" in p for p in src["cache_paths"]))

    def test_default_min_stars_is_4(self):
        """V37.9.51 5 档锁定: ⭐≥4 才入选."""
        self.assertIn("DEFAULT_MIN_STARS = 4", self.src)

    def test_default_top_n_is_5(self):
        """V37.9.51 收工承诺 Top 5."""
        self.assertIn("DEFAULT_TOP_N = 5", self.src)

    def test_log_writes_to_stderr_mr11(self):
        """MR-11 兑现: log() 写 stderr 防 $(...) 命令替换污染."""
        self.assertIn("print(msg, file=sys.stderr)", self.src)

    def test_fail_open_contract_documented(self):
        self.assertIn("FAIL-OPEN", self.src)

    def test_alignment_field_emoji_supports_fallback(self):
        """V37.9.51 fallback: 既要识别 🎚️ (U+FE0F variation selector) 也要识别 🎚."""
        self.assertIn('"🎚️"', self.src)
        self.assertIn('"🎚"', self.src)

    def test_priority_ordering_papers_first(self):
        """source priority: 论文类 1-3 < repo 类 4 < blog 5 < tweet 6-7."""
        # hf_papers priority=1 (最高优先级)
        self.assertRegex(self.src, r'"id":\s*"hf_papers".*?"priority":\s*1')
        # hn priority=7 (最低)
        self.assertRegex(self.src, r'"id":\s*"hn".*?"priority":\s*7')

    def test_v37_9_56_blood_lesson_threshold_lockdown(self):
        """V37.9.51 验证守卫: 反向修改 DEFAULT_MIN_STARS=4 → 单测立即 fail."""
        # 这个测试本身就是反向验证守卫 — 如果有人改成 DEFAULT_MIN_STARS=3 / 2,
        # test_default_min_stars_is_4 立即 fail
        self.assertEqual(tap.DEFAULT_MIN_STARS, 4)
        self.assertEqual(tap.DEFAULT_TOP_N, 5)

    def test_no_module_top_level_external_imports(self):
        """V37.9.46 同款: 模块顶部禁导入重依赖 (lazy import only)."""
        # project_alignment_scorer 必须在函数内 lazy import, 不在顶部
        # 检查方式: 顶部 60 行内不应出现 "import project_alignment_scorer"
        first_60_lines = "\n".join(self.src.split("\n")[:60])
        self.assertNotIn("import project_alignment_scorer", first_60_lines)


class TestV9_57MacMiniLayout(unittest.TestCase):
    """V37.9.57 hotfix: Mac Mini production layout (~/.openclaw/jobs/X/cache).

    Mac Mini auto_deploy FILE_MAP 实际部署:
      - paper/repo/tweet jobs: ~/.openclaw/jobs/X/run_X.sh + cache 同目录
      - HN 例外: ~/.openclaw/jobs/hn_watcher/cache (子目录名 hn_watcher 而非 hn)
      - top_alignment_picker.py: ~/top_alignment_picker.py (顶层)

    V37.9.56 picker 默认 repo_root=$HOME, cache_dir_rel="jobs/X/cache" → 找 ~/jobs/X/cache
    完全错配, Mac Mini 0 picks 而 dev 单测全过. V37.9.57 cache_paths list 修复双 layout.
    """

    def test_resolve_cache_dir_dev_layout(self):
        """Dev layout: cache_paths[0] 相对 repo_root 存在 → 用它."""
        with tempfile.TemporaryDirectory() as tmp:
            cache = os.path.join(tmp, "jobs/hf_papers/cache")
            os.makedirs(cache)
            src = next(s for s in tap.ALIGNED_SOURCES if s["id"] == "hf_papers")
            resolved = tap._resolve_cache_dir(tmp, src)
            self.assertEqual(resolved, cache)

    def test_resolve_cache_dir_mac_mini_layout(self):
        """Mac Mini layout: cache_paths[0] 不存在 → fallback 到 cache_paths[1] (~/.openclaw/jobs/X/cache)."""
        import shutil
        original_home = os.environ.get("HOME", "")
        fake_home = tempfile.mkdtemp()
        try:
            os.environ["HOME"] = fake_home
            mac_cache = os.path.expanduser("~/.openclaw/jobs/hf_papers/cache")
            os.makedirs(mac_cache)
            with tempfile.TemporaryDirectory() as tmp:
                # tmp 下没建 jobs/hf_papers/cache → 应自动 fallback Mac Mini path
                src = next(s for s in tap.ALIGNED_SOURCES if s["id"] == "hf_papers")
                resolved = tap._resolve_cache_dir(tmp, src)
                self.assertEqual(resolved, mac_cache)
        finally:
            os.environ["HOME"] = original_home
            shutil.rmtree(fake_home, ignore_errors=True)

    def test_resolve_cache_dir_hn_subdir_hn_watcher(self):
        """HN 例外: Mac Mini cache 在 ~/.openclaw/jobs/hn_watcher/cache (子目录名差异)."""
        import shutil
        original_home = os.environ.get("HOME", "")
        fake_home = tempfile.mkdtemp()
        try:
            os.environ["HOME"] = fake_home
            mac_hn_cache = os.path.expanduser("~/.openclaw/jobs/hn_watcher/cache")
            os.makedirs(mac_hn_cache)
            with tempfile.TemporaryDirectory() as tmp:
                src = next(s for s in tap.ALIGNED_SOURCES if s["id"] == "hn")
                resolved = tap._resolve_cache_dir(tmp, src)
                self.assertEqual(resolved, mac_hn_cache)
                # 验证不是 ~/cache (那是 dev layout)
                self.assertNotEqual(resolved, os.path.expanduser("~/cache"))
        finally:
            os.environ["HOME"] = original_home
            shutil.rmtree(fake_home, ignore_errors=True)

    def test_resolve_cache_dir_fail_open_all_missing(self):
        """FAIL-OPEN: 所有 candidates 都不存在 → 返回首个候选 (展开后) 让 scan 返回空 list."""
        with tempfile.TemporaryDirectory() as tmp:
            src = next(s for s in tap.ALIGNED_SOURCES if s["id"] == "hf_papers")
            resolved = tap._resolve_cache_dir(tmp, src)
            # 首个候选 = "jobs/hf_papers/cache" 相对 tmp
            self.assertEqual(resolved, os.path.join(tmp, "jobs/hf_papers/cache"))
            self.assertFalse(os.path.isdir(resolved))

    def test_resolve_cache_dir_empty_paths(self):
        """cache_paths 缺失/空 → 返回 "" 不抛 (FAIL-OPEN)."""
        with tempfile.TemporaryDirectory() as tmp:
            # 模拟 schema 缺失的边缘 case
            empty_src = {"id": "test", "display": "Test", "priority": 99, "cache_paths": []}
            self.assertEqual(tap._resolve_cache_dir(tmp, empty_src), "")
            no_paths_src = {"id": "test", "display": "Test", "priority": 99}
            self.assertEqual(tap._resolve_cache_dir(tmp, no_paths_src), "")

    def test_end_to_end_mac_mini_layout_picks_collected(self):
        """V37.9.57 端到端: 在 Mac Mini layout 下 picker 真能找到 picks."""
        import shutil
        original_home = os.environ.get("HOME", "")
        fake_home = tempfile.mkdtemp()
        try:
            os.environ["HOME"] = fake_home
            # 构造 Mac Mini layout cache + 1 个 ⭐⭐⭐⭐⭐ alignment
            mac_cache = os.path.expanduser("~/.openclaw/jobs/hf_papers/cache")
            os.makedirs(mac_cache)
            with open(os.path.join(mac_cache, "llm_results.jsonl"), "w", encoding="utf-8") as f:
                f.write(json.dumps({
                    "idx": 0,
                    "content": "📌 标题: Mac Mini Test Paper\n🎚️ 项目对齐度: ⭐⭐⭐⭐⭐ / V37.9.57 hotfix verified",
                    "failed": False,
                }) + "\n")
            # repo_root 用空 tmpdir, picker 应自动 fallback Mac Mini path
            with tempfile.TemporaryDirectory() as tmp:
                result = tap.pick_top_aligned(repo_root=tmp)
                self.assertEqual(result["status"], "ok")
                self.assertEqual(result["picks_total"], 1)
                self.assertIn("Mac Mini Test Paper", result["block"])
        finally:
            os.environ["HOME"] = original_home
            shutil.rmtree(fake_home, ignore_errors=True)


class TestV9_57SchemaGuards(unittest.TestCase):
    """V37.9.57 schema 守卫: cache_paths list 锁定 + Mac Mini path 字面量存在."""

    def setUp(self):
        with open(os.path.join(REPO_ROOT, "top_alignment_picker.py"), encoding="utf-8") as f:
            self.src = f.read()

    def test_aligned_sources_use_cache_paths_not_cache_dir_rel(self):
        """V37.9.57 schema: 所有 source 必须用 cache_paths list, 禁止 cache_dir_rel 单值字段."""
        for src in tap.ALIGNED_SOURCES:
            self.assertIn("cache_paths", src,
                f'source {src["id"]} 缺 cache_paths 字段')
            self.assertIsInstance(src["cache_paths"], list,
                f'source {src["id"]}.cache_paths 必须是 list')
            self.assertGreaterEqual(len(src["cache_paths"]), 2,
                f'source {src["id"]}.cache_paths 必须 >= 2 候选 (dev + Mac Mini)')
            self.assertNotIn("cache_dir_rel", src,
                f'source {src["id"]} 还保留 cache_dir_rel 反模式 (应已重命名为 cache_paths)')

    def test_mac_mini_path_pattern_present(self):
        """V37.9.57: ALIGNED_SOURCES 每个 source 第二个候选必须是 ~/.openclaw/jobs/...path."""
        for src in tap.ALIGNED_SOURCES:
            mac_paths = [p for p in src["cache_paths"] if p.startswith("~/.openclaw/jobs/")]
            self.assertEqual(len(mac_paths), 1,
                f'source {src["id"]} 缺 ~/.openclaw/jobs/ Mac Mini 路径')

    def test_hn_uses_hn_watcher_subdir_on_mac_mini(self):
        """V37.9.57 HN 例外: Mac Mini 路径必须是 hn_watcher 子目录 (FILE_MAP 部署位置)."""
        hn = next(s for s in tap.ALIGNED_SOURCES if s["id"] == "hn")
        mac_paths = [p for p in hn["cache_paths"] if p.startswith("~/.openclaw/jobs/")]
        self.assertEqual(len(mac_paths), 1)
        self.assertIn("hn_watcher", mac_paths[0],
            f'HN Mac Mini 路径必须含 hn_watcher 子目录, got {mac_paths[0]}')

    def test_resolve_cache_dir_function_defined(self):
        """V37.9.57 helper 函数定义必须存在."""
        self.assertIn("def _resolve_cache_dir(", self.src)
        self.assertTrue(callable(tap._resolve_cache_dir))

    def test_collect_all_picks_uses_resolve_cache_dir(self):
        """collect_all_picks 必须调 _resolve_cache_dir, 不直接 os.path.join(repo_root, cache_dir_rel)."""
        self.assertIn("_resolve_cache_dir(repo_root, src)", self.src)
        # 反模式守卫: 不应残留 src["cache_dir_rel"] 直接索引
        self.assertNotIn('src["cache_dir_rel"]', self.src)
        self.assertNotIn("src['cache_dir_rel']", self.src)

    def test_v37_9_57_marker_present(self):
        self.assertIn("V37.9.57", self.src)


class TestV9_56IntegrationContracts(unittest.TestCase):
    """V37.9.56 集成契约守卫 (kb_dream / kb_evening 集成路径)."""

    def test_picks_top_serializable_json(self):
        """picks_top 必须可 JSON 序列化, 供 shell bash 解析."""
        with tempfile.TemporaryDirectory() as tmp:
            hf_cache = os.path.join(tmp, "jobs/hf_papers/cache")
            os.makedirs(hf_cache)
            with open(os.path.join(hf_cache, "llm_results.jsonl"), "w", encoding="utf-8") as f:
                f.write(json.dumps({"idx": 0, "content": "📌 标题: X\n🎚️ 项目对齐度: ⭐⭐⭐⭐⭐ / reason", "failed": False}) + "\n")
            result = tap.pick_top_aligned(repo_root=tmp)
            # 必须可 JSON dump 不抛
            serialized = json.dumps(result, ensure_ascii=False)
            # 验证 JSON 字段含 status / picks_total / picks_top / block 四 key
            parsed_back = json.loads(serialized)
            self.assertEqual(parsed_back["status"], "ok")
            self.assertEqual(parsed_back["picks_total"], 1)
            self.assertEqual(len(parsed_back["picks_top"]), 1)
            self.assertIn("block", parsed_back)

    def test_block_safe_for_bash_injection(self):
        """block 内容不应含 bash 元字符破坏 \"$(... )\" 命令替换."""
        picks = [
            {"alignment_stars": 5, "source_display": "HF", "cn_title": "Paper $TITLE `evil`",
             "alignment_reason": "test"},
        ]
        block = tap.format_top_picks_block(picks)
        # block 应可安全包在 "..."" 字符串里
        # 注意: title 含 $ / ` 我们没主动 escape, 但调用方应 heredoc 或单引号传递
        # 此 test 仅文档化契约 — block 是 markdown 不 sanitize, 调用方负责安全传递
        self.assertIn("$TITLE", block)  # 不修改原内容


# ══════════════════════════════════════════════════════════════════════
# V37.9.169 [5](c) — emit 端 per-pick 确定性可信度 tier
# ══════════════════════════════════════════════════════════════════════
#
# Background (status.json unfinished [5](c)):
#   source_credibility 此前只在 prompt 级注入 (format_credibility_block 让
#   LLM 自己加 [可信度:X] 标签, 依赖 LLM 合规). [5](c) 改为 emit 端确定性
#   per-pick tier — format_top_picks_block 给每个带 source_id 的 pick 加
#   source_credibility tier emoji (不依赖 LLM). 关键修复: picker 用 "arxiv"
#   但 source_credibility 用 "arxiv_monitor", 不映射则 FAIL-OPEN 误判社媒.

class TestV37_9_169_CredibilityTier(unittest.TestCase):
    """[5](c) 确定性 per-pick 可信度 tier emoji."""

    @staticmethod
    def _pick(sid, stars=5, title="标题", reason="原因"):
        return {"source_id": sid, "source_display": sid,
                "alignment_stars": stars, "cn_title": title,
                "alignment_reason": reason}

    def test_arxiv_maps_to_arxiv_monitor(self):
        # 核心修复: arxiv → arxiv_monitor → 🥈 会议预印本 (非 FAIL-OPEN 社媒💬)
        import source_credibility as sc
        expected = sc.get_credibility("arxiv_monitor")["emoji"]
        block = tap.format_top_picks_block([self._pick("arxiv")])
        self.assertIn(expected, block)
        self.assertEqual(expected, "🥈")  # 会议预印本

    def test_each_real_source_tier(self):
        import source_credibility as sc
        for sid in ("hf_papers", "github_trending", "ai_leaders_blogs",
                    "ai_leaders_x"):
            cred_id = tap._PICKER_TO_CRED_ID.get(sid, sid)
            expected = sc.get_credibility(cred_id)["emoji"]
            block = tap.format_top_picks_block([self._pick(sid)])
            self.assertIn(f"[{sid} {expected}]", block,
                          f"{sid} expected tier {expected}")

    def test_unknown_source_fail_open_social(self):
        # hn 不在 source_credibility → FAIL-OPEN 社媒💬 (对 HN 本就正确)
        block = tap.format_top_picks_block([self._pick("hn")])
        self.assertIn("💬", block)

    def test_no_source_id_no_emoji_backward_compat(self):
        # 边界 pick 无 source_id → 无 emoji, 现有格式保留 (向后兼容)
        block = tap.format_top_picks_block(
            [{"alignment_stars": 5, "source_display": "HF",
              "cn_title": "Paper A", "alignment_reason": "r"}])
        self.assertEqual(block, "- ⭐⭐⭐⭐⭐ [HF] Paper A / r")

    def test_helper_fail_open_module_none(self):
        self.assertEqual(tap._credibility_emoji(None, "arxiv"), "")

    def test_helper_fail_open_empty_source_id(self):
        self.assertEqual(tap._credibility_emoji(object(), ""), "")

    def test_block_format_full_line(self):
        block = tap.format_top_picks_block([self._pick("hf_papers")])
        # 格式: - ⭐⭐⭐⭐⭐ [hf_papers 🥈] 标题 / 原因
        self.assertTrue(block.startswith("- ⭐⭐⭐⭐⭐ [hf_papers 🥈] 标题"))


class TestV37_9_169_Guards(unittest.TestCase):
    """V37.9.169 源码级守卫 + 反向验证."""

    @classmethod
    def setUpClass(cls):
        here = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(here, "top_alignment_picker.py"),
                  "r", encoding="utf-8") as f:
            cls.src = f.read()

    def test_marker(self):
        self.assertIn("V37.9.169", self.src)

    def test_picker_to_cred_id_locked(self):
        # 一物一形: 只登记真差异 (arxiv→arxiv_monitor)
        self.assertEqual(tap._PICKER_TO_CRED_ID, {"arxiv": "arxiv_monitor"})

    def test_helper_exists(self):
        self.assertTrue(hasattr(tap, "_credibility_emoji"))

    def test_lazy_import_fail_open_specific(self):
        # FAIL-OPEN: except ImportError (specific, 不触发 MRD-SILENT-EXCEPT)
        self.assertIn("except ImportError", self.src)

    def test_mr8_emoji_sourced_from_credibility(self):
        """MR-8: emoji 来自 source_credibility, 非 picker 硬编码.
        若 source_credibility tier emoji 改变, picker 输出自动跟随."""
        import source_credibility as sc
        cred_emoji = sc.get_credibility("arxiv_monitor")["emoji"]
        block = tap.format_top_picks_block(
            [{"source_id": "arxiv", "source_display": "ArXiv",
              "alignment_stars": 4, "cn_title": "T", "alignment_reason": ""}])
        self.assertIn(cred_emoji, block)

    def test_reverse_id_map_matters_non_tautology(self):
        """反向验证: id 映射真起作用 — arxiv (映射后) 得 🥈, 而未映射当
        未知源 FAIL-OPEN 得 💬. 两者不同 = 映射非装饰."""
        import source_credibility as sc
        mapped = sc.get_credibility(
            tap._PICKER_TO_CRED_ID.get("arxiv", "arxiv"))["emoji"]
        unmapped = sc.get_credibility("arxiv")["emoji"]  # 未映射 FAIL-OPEN
        self.assertEqual(mapped, "🥈")
        self.assertEqual(unmapped, "💬")
        self.assertNotEqual(mapped, unmapped)



# ═══════════════════════════════════════════════════════════════════════════
# V37.9.384 结果文件新鲜度闸门
# ═══════════════════════════════════════════════════════════════════════════
import contextlib
import io
import re
import time
from unittest import mock

_STALE_DAYS = 51  # DBLP 2026-09-19 → 2026-11-09 的失败天数, 血案形态 (dev 复现用合成源)
# 2026-11-09 Mac Mini 取证实测: ai_leaders_x 文件停在 11-07 09:02 = 55h (两次 09:00 运行
# 均未重写), 是 DBLP 退役 (V37.9.383) 后仍在 ALIGNED_SOURCES 里的真实陈旧源
_PROD_STALE_HOURS = 55


def _write_results(cache_dir, title, stars, mtime=None):
    os.makedirs(cache_dir, exist_ok=True)
    path = os.path.join(cache_dir, "llm_results.jsonl")
    content = f"📌 标题: {title}\n⭐ 评级: ⭐⭐⭐\n🎚️ 项目对齐度: {'⭐' * stars} / 直接相关"
    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps({"idx": 0, "content": content, "failed": False}, ensure_ascii=False) + "\n")
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def _src(src_id, cache_dir, display=None, cadence=24, priority=3):
    return {"id": src_id, "display": display or src_id, "priority": priority,
            "cache_paths": [cache_dir, cache_dir], "cadence_hours": cadence}


def _watchdog_err_pattern():
    with open(os.path.join(REPO_ROOT, "job_watchdog.sh"), encoding="utf-8") as f:
        src = f.read()
    m = re.search(r"local err_pattern='([^']+)'", src)
    assert m, "job_watchdog.sh err_pattern not found"
    return m.group(1)


class TestResultsFreshness(unittest.TestCase):
    """V37.9.384: 上一轮留下的 llm_results.jsonl 不再冒充今天的 Top 5."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.now = time.time()

    def tearDown(self):
        self._tmp.cleanup()

    def _collect(self, sources, now=None):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            picks = tap.collect_all_picks(repo_root=self.tmp, sources=sources,
                                          now=self.now if now is None else now)
        return picks, err.getvalue()

    def test_fresh_file_yields_picks_without_warn(self):
        d = os.path.join(self.tmp, "fresh")
        _write_results(d, "今天的论文", 5)
        picks, err = self._collect([_src("hf_papers", d)])
        self.assertEqual(len(picks), 1)
        self.assertNotIn("WARN", err)

    def test_stale_file_yields_no_picks_and_warns(self):
        d = os.path.join(self.tmp, "stale")
        _write_results(d, "七周前的论文", 5, mtime=self.now - _STALE_DAYS * 86400)
        picks, err = self._collect([_src("dblp", d, display="DBLP")])
        self.assertEqual(picks, [])
        self.assertIn("WARN", err)
        self.assertIn("DBLP", err)
        self.assertIn("不计入 Top 5", err)
        self.assertIn(f"{_STALE_DAYS * 24:.1f}h", err)

    def test_missing_file_still_empty_and_silent(self):
        d = os.path.join(self.tmp, "nothing_here")
        picks, err = self._collect([_src("hf_papers", d)])
        self.assertEqual(picks, [])
        self.assertNotIn("WARN", err)
        self.assertIsNone(tap.results_file_age_hours(d))

    def test_boundary_just_inside_and_just_outside(self):
        d = os.path.join(self.tmp, "edge")
        limit = tap.max_age_hours({"cadence_hours": 24})
        _write_results(d, "边界论文", 5, mtime=self.now - (limit - 0.1) * 3600)
        picks, _ = self._collect([_src("hf_papers", d)])
        self.assertEqual(len(picks), 1, "上限内 0.1h 的文件必须计入")
        os.utime(os.path.join(d, "llm_results.jsonl"),
                 (self.now - (limit + 0.1) * 3600,) * 2)
        picks, err = self._collect([_src("hf_papers", d)])
        self.assertEqual(picks, [], "超出上限 0.1h 的文件必须跳过")
        self.assertIn("WARN", err)

    def test_future_mtime_counts_as_fresh(self):
        d = os.path.join(self.tmp, "future")
        _write_results(d, "时钟偏差", 5, mtime=self.now + 3600)
        picks, err = self._collect([_src("hf_papers", d)])
        self.assertEqual(len(picks), 1)
        self.assertNotIn("WARN", err)

    def test_unstatable_file_fails_open(self):
        d = os.path.join(self.tmp, "nostat")
        _write_results(d, "读得到但 stat 不了", 5)
        with mock.patch.object(tap.os.path, "getmtime", side_effect=PermissionError("denied")):
            picks, err = self._collect([_src("hf_papers", d)])
        self.assertEqual(len(picks), 1, "无法判断年龄时不得吞掉结果 (FAIL-OPEN)")
        self.assertNotIn("WARN", err)

    def test_per_source_cadence_respected(self):
        d = os.path.join(self.tmp, "weekly")
        _write_results(d, "三天前的周更内容", 5, mtime=self.now - 72 * 3600)
        daily, _ = self._collect([_src("daily_src", d, cadence=24)])
        weekly, _ = self._collect([_src("weekly_src", d, cadence=168)])
        self.assertEqual(daily, [], "日更源 72h 前的文件是陈旧的")
        self.assertEqual(len(weekly), 1, "周更源 72h 前的文件仍在周期内")

    def test_missing_cadence_defaults_to_daily(self):
        self.assertEqual(tap.max_age_hours({}), tap.DEFAULT_CADENCE_HOURS + tap.FRESHNESS_GRACE_HOURS)
        self.assertEqual(tap.max_age_hours({"cadence_hours": "垃圾"}),
                         tap.DEFAULT_CADENCE_HOURS + tap.FRESHNESS_GRACE_HOURS)

    def test_blood_case_stale_source_does_not_outrank_today(self):
        """血案回归: dev 复现 51 天前的 DBLP ⭐5 压过当天 HF ⭐4 排第一; DBLP 已退役,
        这里用 Mac Mini 实测仍在线的陈旧源 ai_leaders_x (55h) 复刻同一形状."""
        x = next(s for s in tap.ALIGNED_SOURCES if s["id"] == "ai_leaders_x")
        hf = next(s for s in tap.ALIGNED_SOURCES if s["id"] == "hf_papers")
        _write_results(os.path.join(self.tmp, x["cache_paths"][0]), "两天前的推文观点", 5,
                       mtime=self.now - _PROD_STALE_HOURS * 3600)
        _write_results(os.path.join(self.tmp, hf["cache_paths"][0]), "今天的 HF 论文", 4)
        err = io.StringIO()
        with mock.patch.dict(os.environ, {"HOME": self.tmp}), contextlib.redirect_stderr(err):
            result = tap.pick_top_aligned(repo_root=self.tmp)
        self.assertEqual(result["status"], "ok")
        self.assertNotIn("两天前的推文观点", result["block"])
        self.assertIn("今天的 HF 论文", result["block"])
        self.assertEqual([s["source_id"] for s in result["stale_sources"]], ["ai_leaders_x"])
        self.assertAlmostEqual(result["stale_sources"][0]["age_hours"], _PROD_STALE_HOURS, delta=0.2)

    def test_all_sources_stale_gives_no_picks(self):
        for src in tap.ALIGNED_SOURCES:
            _write_results(os.path.join(self.tmp, src["cache_paths"][0]), f"{src['id']} 旧内容", 5,
                           mtime=self.now - 3 * 86400)
        err = io.StringIO()
        with mock.patch.dict(os.environ, {"HOME": self.tmp}), contextlib.redirect_stderr(err):
            result = tap.pick_top_aligned(repo_root=self.tmp)
        self.assertEqual(result["status"], "no_picks")
        self.assertEqual(result["block"], "")
        self.assertEqual(len(result["stale_sources"]), len(tap.ALIGNED_SOURCES))

    def test_exception_path_keeps_schema(self):
        with mock.patch.object(tap, "_collect", side_effect=RuntimeError("boom")), \
                contextlib.redirect_stderr(io.StringIO()):
            result = tap.pick_top_aligned(repo_root=self.tmp)
        self.assertEqual(result["status"], "no_picks")
        self.assertEqual(result["stale_sources"], [])

    def test_warn_goes_to_stderr_not_stdout(self):
        """MR-11: kb_dream 用 $(... --block-only) 捕获 stdout, WARN 混进去会进 prompt."""
        x = next(s for s in tap.ALIGNED_SOURCES if s["id"] == "ai_leaders_x")
        _write_results(os.path.join(self.tmp, x["cache_paths"][0]), "旧论文", 5,
                       mtime=self.now - _PROD_STALE_HOURS * 3600)
        env = dict(os.environ, HOME=self.tmp)
        proc = subprocess.run(
            [sys.executable, os.path.join(REPO_ROOT, "top_alignment_picker.py"),
             "--repo-root", self.tmp, "--block-only"],
            capture_output=True, text=True, env=env, timeout=30)
        self.assertEqual(proc.returncode, 0)
        self.assertNotIn("WARN", proc.stdout)
        self.assertNotIn("旧论文", proc.stdout)
        self.assertIn("WARN", proc.stderr)

    def test_cli_json_reports_stale_sources(self):
        x = next(s for s in tap.ALIGNED_SOURCES if s["id"] == "ai_leaders_x")
        _write_results(os.path.join(self.tmp, x["cache_paths"][0]), "旧论文", 5,
                       mtime=self.now - _PROD_STALE_HOURS * 3600)
        env = dict(os.environ, HOME=self.tmp)
        proc = subprocess.run(
            [sys.executable, os.path.join(REPO_ROOT, "top_alignment_picker.py"),
             "--repo-root", self.tmp, "--json"],
            capture_output=True, text=True, env=env, timeout=30)
        data = json.loads(proc.stdout)
        self.assertEqual([s["source_id"] for s in data["stale_sources"]], ["ai_leaders_x"])

    def test_warn_does_not_match_watchdog_err_pattern(self):
        """WARN 进 kb_evening.log / kb_radar 日志; 源自身失败已由 watchdog 按状态文件告警,
        这行若匹配 err_pattern 会每天多报一条日志错误 (V37.9.300 告警疲劳家族)."""
        d = os.path.join(self.tmp, "stale")
        _write_results(d, "旧论文", 5, mtime=self.now - _STALE_DAYS * 86400)
        _, err = self._collect([_src("dblp", d, display="DBLP")])
        warn_lines = [ln for ln in err.splitlines() if "WARN" in ln]
        self.assertEqual(len(warn_lines), 1)
        pattern = _watchdog_err_pattern()
        proc = subprocess.run(["grep", "-ciE", pattern], input=err, capture_output=True, text=True)
        self.assertEqual(proc.stdout.strip(), "0", f"WARN 行匹配了 watchdog err_pattern: {warn_lines[0]}")
        probe = subprocess.run(["grep", "-ciE", pattern], input="ERROR: fetch_failed\n",
                               capture_output=True, text=True)
        self.assertEqual(probe.stdout.strip(), "1", "反向证据: 抽出的 err_pattern 必须能匹配真错误行")


# ── registry cron → cadence 推导 (测试侧单一真理源) ────────────────────────
def _cron_field(field, lo, hi):
    vals = set()
    for piece in field.split(","):
        step = 1
        if "/" in piece:
            piece, step_s = piece.split("/", 1)
            step = int(step_s)
        if piece == "*":
            a, b = lo, hi
        elif "-" in piece:
            a_s, b_s = piece.split("-", 1)
            a, b = int(a_s), int(b_s)
        else:
            a = b = int(piece)
        vals.update(range(a, b + 1, step))
    return vals


def _week_run_minutes(cron):
    minute, hour, dom, month, dow = str(cron).split()[:5]
    if dom != "*" or month != "*":
        raise ValueError(f"unsupported cron for cadence derivation: {cron}")
    days = {d % 7 for d in _cron_field(dow, 0, 7)}
    mins = _cron_field(minute, 0, 59)
    hours = _cron_field(hour, 0, 23)
    return sorted(d * 1440 + h * 60 + m for d in days for h in hours for m in mins)


def _max_gap_hours(cron):
    runs = _week_run_minutes(cron)
    gaps = [b - a for a, b in zip(runs, runs[1:])]
    gaps.append(runs[0] + 7 * 1440 - runs[-1])
    return max(gaps) / 60.0


def _expected_cadence(cron):
    return max(24.0, _max_gap_hours(cron))


def _registry_intervals():
    out = {}
    current = None
    with open(os.path.join(REPO_ROOT, "jobs_registry.yaml"), encoding="utf-8") as f:
        for raw in f:
            stripped = raw.strip()
            if stripped.startswith("- id:"):
                current = stripped.split(":", 1)[1].strip()
                continue
            m = re.match(r'interval:\s*"([^"]+)"', stripped)
            if current and m:
                out[current] = m.group(1)
    return out


class TestCadenceDerivedFromRegistry(unittest.TestCase):
    """MR-8: cadence_hours 必须等于 jobs_registry cron 推导值, 改排程不改 picker 即红."""

    def test_cron_helper_semantics(self):
        self.assertEqual(_max_gap_hours("0 10 * * *"), 24.0)
        self.assertEqual(_max_gap_hours("0 8,20 * * *"), 12.0)
        self.assertEqual(_max_gap_hours("45 8,14,20 * * *"), 12.0)
        self.assertEqual(_max_gap_hours("30 9 * * 3"), 168.0)
        self.assertEqual(_max_gap_hours("0 9 * * 1-5"), 72.0)
        self.assertEqual(_expected_cadence("0 8,20 * * *"), 24.0, "一天多跑的源按每日下限")
        self.assertEqual(_expected_cadence("30 9 * * 3"), 168.0)

    def test_every_source_matches_registry(self):
        intervals = _registry_intervals()
        checked = 0
        for src in tap.ALIGNED_SOURCES:
            rid = src.get("registry_id")
            self.assertTrue(rid, f"{src['id']} 缺 registry_id")
            self.assertIn(rid, intervals,
                          f"{src['id']} 的 registry_id={rid} 不在 jobs_registry.yaml "
                          f"(job 已删除就把它从 ALIGNED_SOURCES 一起删掉)")
            self.assertIn("cadence_hours", src, f"{src['id']} 缺 cadence_hours")
            self.assertEqual(float(src["cadence_hours"]), _expected_cadence(intervals[rid]),
                             f"{src['id']} cadence_hours 与 registry interval "
                             f"{intervals[rid]!r} 推导值不一致")
            checked += 1
        self.assertEqual(checked, len(tap.ALIGNED_SOURCES))
        self.assertGreaterEqual(checked, 9, "防空转: 至少核对 9 个源")

    def test_reverse_evidence_guard_can_fire(self):
        """若某源改成周更, 推导值 168 ≠ 声明的 24 —— 上面的比对不是恒真式."""
        self.assertNotEqual(_expected_cadence("30 9 * * 3"), 24.0)


class TestFreshnessWindowAgainstSchedule(unittest.TestCase):
    """用 registry 真实排程验证窗口两条性质 (V37.9.384 选 26h 而非 36h 的依据).

    (a) 不误杀: 每个源正常运行时, 在 kb_dream / kb_radar / kb_evening 的任一读取
        时刻, 最近一次写出的文件都在上限内。
    (b) 不放过: 某源过去 24h 内的运行全部没写文件, 下一次 kb_dream 读取时必被跳过。
    """

    WRITE_DELAYS_MIN = (5, 60)  # job 启动到最后一次写文件的耗时区间

    @classmethod
    def setUpClass(cls):
        cls.intervals = _registry_intervals()
        cls.readers = {r: cls.intervals[r] for r in ("kb_dream", "kb_radar", "kb_evening")}

    def _runs(self, cron, weeks=3):
        base = _week_run_minutes(cron)
        return [w * 7 * 1440 + r for w in range(weeks) for r in base]

    def _reader_instants(self, cron):
        return [7 * 1440 + r for r in _week_run_minutes(cron)]

    def _fresh_age_hours(self, src_cron, reader_min, delay):
        writes = [r + delay for r in self._runs(src_cron) if r + delay <= reader_min]
        return (reader_min - writes[-1]) / 60.0

    def _stale_age_hours(self, src_cron, reader_min, delay):
        writes = [r + delay for r in self._runs(src_cron) if r < reader_min - 24 * 60]
        return (reader_min - writes[-1]) / 60.0

    def test_a_no_false_skip_at_any_reader(self):
        worst = 0.0
        for src in tap.ALIGNED_SOURCES:
            cron = self.intervals[src["registry_id"]]
            limit = tap.max_age_hours(src)
            for rname, rcron in self.readers.items():
                for t in self._reader_instants(rcron):
                    for delay in self.WRITE_DELAYS_MIN:
                        age = self._fresh_age_hours(cron, t, delay)
                        worst = max(worst, age)
                        self.assertLess(age, limit,
                                        f"{src['id']} 在 {rname} 读取时健康文件 {age:.1f}h 被误判过期")
        self.assertGreater(worst, 12.0, "防空转: 模拟必须覆盖到接近一天的健康文件")

    def test_b_missed_day_caught_by_next_dream(self):
        dream = self._reader_instants(self.readers["kb_dream"])
        for src in tap.ALIGNED_SOURCES:
            cron = self.intervals[src["registry_id"]]
            limit = tap.max_age_hours(src)
            for t in dream:
                age = min(self._stale_age_hours(cron, t, d) for d in self.WRITE_DELAYS_MIN)
                self.assertGreater(age, limit,
                                   f"{src['id']} 漏跑一天后 kb_dream 读到 {age:.1f}h 的旧文件仍被计入")

    def test_reverse_evidence_36h_window_would_miss(self):
        """反向证据: 36h 窗口在 kb_dream 时刻放过 arxiv/HN 的漏跑日 —— (b) 守的是真性质."""
        dream = self._reader_instants(self.readers["kb_dream"])
        missed = []
        for src in tap.ALIGNED_SOURCES:
            cron = self.intervals[src["registry_id"]]
            for t in dream:
                age = min(self._stale_age_hours(cron, t, d) for d in self.WRITE_DELAYS_MIN)
                if age <= 36.0:
                    missed.append(src["id"])
                    break
        self.assertIn("arxiv", missed)
        self.assertIn("hn", missed)


if __name__ == "__main__":
    unittest.main()
