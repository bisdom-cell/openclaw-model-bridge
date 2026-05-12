#!/usr/bin/env python3
"""V37.9.56 Sub-Stage 4c — test_top_alignment_picker.py.

Test layers:
  1. parse_alignment_from_content — 6-field LLM output 解析正确性
  2. _fallback_extract_star_count — alignment scorer 缺失时 fallback
  3. scan_source_results — llm_results.jsonl 读取 + FAIL-OPEN
  4. collect_all_picks — 8 source 聚合 + min_stars 阈值过滤
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
                # find cache_dir_rel
                src_meta = next(s for s in tap.ALIGNED_SOURCES if s["id"] == src_id)
                cache_dir = os.path.join(tmp, src_meta["cache_dir_rel"])
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

    def test_8_aligned_sources_registered(self):
        """ALIGNED_SOURCES 必须含全部 8 个 source id."""
        for src_id in ["hf_papers", "semantic_scholar", "arxiv", "dblp",
                       "github_trending", "rss_blogs", "ai_leaders_x", "hn"]:
            self.assertIn(f'"id": "{src_id}"', self.src,
                          f"ALIGNED_SOURCES 缺 {src_id}")

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


if __name__ == "__main__":
    unittest.main()
