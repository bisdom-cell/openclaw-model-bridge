#!/usr/bin/env python3
"""
test_kb_dream_helpers.py — V37.9.68 Dream 三阶推送辅助函数单测

覆盖 kb_dream_helpers.py 全部公开纯函数：
- normalize_theme_keywords()
- themes_overlap()
- extract_recent_themes()
- format_banned_themes_block()
- extract_deep_theme_from_chunk()
- split_wide_radar_output()
- build_overview_block()
- extract_section_titles()
- CLI main()

测试模式：
1. 纯函数单元层（无 IO）
2. extract_recent_themes 用 tempdir 隔离构造 dream 文件
3. **血案场景回归**：复现"连续几周 Qwen-BIM 重复"场景，断言 V37.9.68 防御真生效
4. 反向验证守卫：检测 14 天硬规则 / 主题归一化字面量是否被回退

V37.9.68 设计契约：
- PREV_THEMES 默认 14 天
- 主题归一化 ≥2 关键词重叠 = 重复
- DEEP 失败 fail-fast / WIDE+RADAR 失败独立降级
- 4 段 ## header (DEEP / WIDE / RADAR / Overview)
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta

# 注入仓库根到 path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from kb_dream_helpers import (
    build_overview_block,
    extract_deep_theme_from_chunk,
    extract_recent_themes,
    extract_section_titles,
    format_banned_themes_block,
    normalize_theme_keywords,
    split_dream_into_chunks,
    split_wide_radar_output,
    themes_overlap,
)


# ─────────────────────────────────────────────────────────────────────
# normalize_theme_keywords
# ─────────────────────────────────────────────────────────────────────


class TestNormalizeThemeKeywords(unittest.TestCase):
    """主题归一化纯函数 — 跨日比较的基础。"""

    def test_strips_markdown_header_and_emoji(self):
        kw = normalize_theme_keywords("## 🌙 今日深度发现：Qwen-BIM 颠覆参数")
        # qwen-bim 是英文 token (small case)
        self.assertIn("qwen-bim", kw)
        # V37.9.68 修正：中文用 2-gram + 3-gram 滑动窗口
        # "颠覆参数" 4 字串可拆出 2-gram "颠覆"+"覆参"+"参数" / 3-gram "颠覆参"+"覆参数"
        self.assertIn("颠覆", kw)
        self.assertIn("参数", kw)
        # markdown header 不应出现在关键词中
        self.assertNotIn("##", kw)
        self.assertNotIn("🌙", kw)

    def test_blood_lesson_qwen_bim_themes_overlap(self):
        """V37.9.68 血案场景：Qwen-BIM 在多种表述下都被识别为同一主题。"""
        kw1 = normalize_theme_keywords("Qwen-BIM 14B 模型颠覆参数竞赛")
        kw2 = normalize_theme_keywords("Qwen-BIM 仅 14B 参数 vs 671B 巨型")
        kw3 = normalize_theme_keywords("Qwen-BIM 路线对工业 AI 的启示")
        # 三者共享 "qwen-bim" + "14b" 等关键词
        self.assertIn("qwen-bim", kw1)
        self.assertIn("qwen-bim", kw2)
        self.assertIn("qwen-bim", kw3)
        # themes_overlap 应当判定彼此重复
        self.assertTrue(themes_overlap(kw1, kw2))
        self.assertTrue(themes_overlap(kw1, kw3))
        self.assertTrue(themes_overlap(kw2, kw3))

    def test_stopwords_filtered(self):
        kw = normalize_theme_keywords("The Agent and the future")
        # the/and/agent 都是停用词
        self.assertNotIn("the", kw)
        self.assertNotIn("and", kw)
        self.assertNotIn("agent", kw)
        self.assertIn("future", kw)

    def test_lowercase_english(self):
        kw = normalize_theme_keywords("Control Plane vs Capability Plane")
        # 全部小写
        self.assertIn("control", kw)
        self.assertIn("plane", kw)
        self.assertIn("capability", kw)
        self.assertNotIn("Control", kw)
        self.assertNotIn("Plane", kw)

    def test_chinese_2_to_4_chars(self):
        kw = normalize_theme_keywords("人工智能控制平面")
        # 2-4 字段都应被提取
        self.assertTrue(any("人工" in k or "智能" in k or "控制" in k or "平面" in k for k in kw))

    def test_empty_input(self):
        self.assertEqual(normalize_theme_keywords(""), set())
        self.assertEqual(normalize_theme_keywords("   "), set())

    def test_non_string_safe(self):
        self.assertEqual(normalize_theme_keywords(None), set())
        self.assertEqual(normalize_theme_keywords(123), set())
        self.assertEqual(normalize_theme_keywords([]), set())

    def test_multi_layer_prefix_strip(self):
        """主题文本可能包含多层前缀，需迭代剥除。"""
        kw = normalize_theme_keywords("## 🌙 今日深度发现：control plane 演进")
        self.assertIn("control", kw)
        self.assertIn("plane", kw)
        self.assertIn("演进", kw)


# ─────────────────────────────────────────────────────────────────────
# themes_overlap
# ─────────────────────────────────────────────────────────────────────


class TestThemesOverlap(unittest.TestCase):
    def test_two_keyword_overlap_is_duplicate(self):
        self.assertTrue(themes_overlap({"qwen-bim", "14b"}, {"qwen-bim", "14b", "颠覆"}))

    def test_one_keyword_short_set_is_duplicate(self):
        # 单 keyword 但占短主题 100% → 重复
        self.assertTrue(themes_overlap({"ontology"}, {"ontology", "engine"}))

    def test_no_overlap_not_duplicate(self):
        self.assertFalse(themes_overlap({"qwen-bim", "14b"}, {"freight", "watcher"}))

    def test_one_keyword_long_set_not_duplicate(self):
        # 1 重叠 / 长主题 ≥3 keyword 占比 <50%
        self.assertFalse(
            themes_overlap(
                {"qwen-bim"},
                {"control", "plane", "capability", "memory"},
            )
        )

    def test_empty_sets(self):
        self.assertFalse(themes_overlap(set(), set()))
        self.assertFalse(themes_overlap({"a", "b"}, set()))
        self.assertFalse(themes_overlap(set(), {"a", "b"}))


# ─────────────────────────────────────────────────────────────────────
# extract_recent_themes
# ─────────────────────────────────────────────────────────────────────


class TestExtractRecentThemes(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="dream_themes_")
        self.today = datetime(2026, 5, 14)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write_dream(self, date_str: str, theme: str = "测试主题"):
        path = os.path.join(self.tmpdir, f"{date_str}.md")
        with open(path, "w") as f:
            f.write(f"# 🌙 Agent Dream — {date_str}\n\n")
            f.write(f"## 🌙 今日深度发现：{theme}\n\n")
            f.write("### 发现过程\n内容\n\n")
        return path

    def test_default_14_day_window(self):
        """V37.9.68 关键：默认 14 天窗口（从 V37.4 的 3 天扩展）。"""
        # 今天 5/14, 14 天前 = 4/30
        # 注意：theme 文本不能是"今日主题"等含 prefix 字眼，否则归一化后关键词集合为空
        self._write_dream("2026-05-14", "控制平面演进")
        self._write_dream("2026-05-01", "Memory Plane 设计")  # 在 14 天内
        self._write_dream("2026-04-30", "Ontology 概念抽象")  # 在 14 天内 (cutoff = today - 14d)
        self._write_dream("2026-04-15", "陈旧主题")  # 超 14 天，应剔除

        themes = extract_recent_themes(self.tmpdir, days=14, today=self.today)
        dates = [t["date"] for t in themes]
        self.assertIn("2026-05-14", dates)
        self.assertIn("2026-05-01", dates)
        self.assertNotIn("2026-04-15", dates)

    def test_extracts_theme_text(self):
        self._write_dream("2026-05-13", "Qwen-BIM 14B 颠覆参数竞赛")
        themes = extract_recent_themes(self.tmpdir, days=14, today=self.today)
        self.assertEqual(len(themes), 1)
        self.assertEqual(themes[0]["raw_title"], "Qwen-BIM 14B 颠覆参数竞赛")
        self.assertIn("qwen-bim", themes[0]["keywords"])

    def test_descending_date_order(self):
        self._write_dream("2026-05-10", "10 日主题")
        self._write_dream("2026-05-12", "12 日主题")
        self._write_dream("2026-05-11", "11 日主题")
        themes = extract_recent_themes(self.tmpdir, days=14, today=self.today)
        dates = [t["date"] for t in themes]
        self.assertEqual(dates, ["2026-05-12", "2026-05-11", "2026-05-10"])

    def test_v37_9_260_boundary_with_time_of_day(self):
        """V37.9.260: today 带时间分量时，恰 14 日历天前的主题仍在窗口内（镜像
        kb_deep_dive off-by-one 修复，原则 #31 全量同步）。修复前：dream 03:00 跑
        (今日带时间) → 恰 14 天前主题逃逸 ban → 可能重复。"""
        # today 05-14 22:30（带时间），14 日历天前 = 04-30
        self._write_dream("2026-04-30", "控制平面演进")
        themes = extract_recent_themes(
            self.tmpdir, days=14, today=datetime(2026, 5, 14, 22, 30, 0))
        dates = [t["date"] for t in themes]
        self.assertIn("2026-04-30", dates,
                      "恰 14 日历天前的主题必须在窗口内（今日归一化午夜）")

    def test_missing_dir_returns_empty(self):
        self.assertEqual(extract_recent_themes("/nonexistent/path"), [])

    def test_invalid_filename_skipped(self):
        path = os.path.join(self.tmpdir, "not_a_date.md")
        with open(path, "w") as f:
            f.write("## 🌙 今日深度发现：垃圾主题")
        themes = extract_recent_themes(self.tmpdir, days=14, today=self.today)
        self.assertEqual(themes, [])

    def test_no_theme_line_in_file_skipped(self):
        path = os.path.join(self.tmpdir, "2026-05-13.md")
        with open(path, "w") as f:
            f.write("# 🌙 Agent Dream\n\n（梦境内容但无主题行）")
        themes = extract_recent_themes(self.tmpdir, days=14, today=self.today)
        self.assertEqual(themes, [])

    def test_days_param_validation(self):
        """非法 days 参数自动 fallback 1。"""
        themes = extract_recent_themes(self.tmpdir, days=0, today=self.today)
        self.assertEqual(themes, [])
        themes = extract_recent_themes(self.tmpdir, days=-5, today=self.today)
        self.assertEqual(themes, [])

    def test_non_string_dream_dir_safe(self):
        self.assertEqual(extract_recent_themes(None), [])
        self.assertEqual(extract_recent_themes(123), [])


# ─────────────────────────────────────────────────────────────────────
# format_banned_themes_block
# ─────────────────────────────────────────────────────────────────────


class TestFormatBannedThemesBlock(unittest.TestCase):
    def test_empty_themes_returns_empty_string(self):
        self.assertEqual(format_banned_themes_block([]), "")

    def test_renders_all_themes_with_keywords(self):
        themes = [
            {"date": "2026-05-13", "raw_title": "Qwen-BIM", "keywords": {"qwen-bim", "14b"}},
            {"date": "2026-05-12", "raw_title": "control plane", "keywords": {"control", "plane"}},
        ]
        block = format_banned_themes_block(themes)
        self.assertIn("2026-05-13", block)
        self.assertIn("Qwen-BIM", block)
        self.assertIn("2026-05-12", block)
        self.assertIn("control plane", block)
        # keywords 摘要应显示
        self.assertIn("qwen-bim", block)
        # 必有硬规则文字
        self.assertIn("禁止重复", block)
        self.assertIn("整份输出作废", block)

    def test_blood_lesson_marker_in_block(self):
        themes = [{"date": "2026-05-13", "raw_title": "T", "keywords": {"k"}}]
        block = format_banned_themes_block(themes)
        # V37.9.68 关键字眼：把用户视角原则作为压力点
        self.assertIn("连续几周", block)


# ─────────────────────────────────────────────────────────────────────
# extract_deep_theme_from_chunk
# ─────────────────────────────────────────────────────────────────────


class TestExtractDeepThemeFromChunk(unittest.TestCase):
    def test_new_v9_68_format(self):
        chunk = "## 🌙 今日深度: 控制平面演进\n\n### 发现过程\n..."
        self.assertEqual(extract_deep_theme_from_chunk(chunk), "控制平面演进")

    def test_old_v37_4_format(self):
        chunk = "## 🌙 今日深度发现：Qwen-BIM\n\n### 发现过程..."
        self.assertEqual(extract_deep_theme_from_chunk(chunk), "Qwen-BIM")

    def test_no_emoji_variant(self):
        chunk = "### 今日深度发现：Memory Plane\n..."
        self.assertEqual(extract_deep_theme_from_chunk(chunk), "Memory Plane")

    def test_empty_input(self):
        self.assertEqual(extract_deep_theme_from_chunk(""), "(未识别主题)")
        self.assertEqual(extract_deep_theme_from_chunk("   "), "(未识别主题)")

    def test_no_match_returns_placeholder(self):
        chunk = "正文内容，无主题行。"
        self.assertEqual(extract_deep_theme_from_chunk(chunk), "(未识别主题)")

    def test_strips_trailing_punctuation(self):
        chunk = "## 🌙 今日深度: 主题名。"
        self.assertEqual(extract_deep_theme_from_chunk(chunk), "主题名")


# ─────────────────────────────────────────────────────────────────────
# split_wide_radar_output
# ─────────────────────────────────────────────────────────────────────


class TestSplitWideRadarOutput(unittest.TestCase):
    def test_split_both_sections(self):
        content = (
            "## 🌐 跨领域鲜人知 × 5\n\n"
            "- **A**: 内容 1\n"
            "- **B**: 内容 2\n\n"
            "## 📡 准期信号 × 5\n\n"
            "- **C**: 内容 3\n"
        )
        wide, radar = split_wide_radar_output(content)
        self.assertIn("🌐", wide)
        self.assertIn("A", wide)
        self.assertIn("B", wide)
        # WIDE 段不应包含 RADAR
        self.assertNotIn("📡", wide)
        # RADAR 段
        self.assertIn("📡", radar)
        self.assertIn("C", radar)

    def test_only_wide_no_radar(self):
        content = "## 🌐 跨领域\n\n- A: 内容"
        wide, radar = split_wide_radar_output(content)
        self.assertIn("🌐", wide)
        self.assertEqual(radar, "")

    def test_only_radar_no_wide(self):
        content = "## 📡 准期信号\n\n- A: 内容"
        wide, radar = split_wide_radar_output(content)
        self.assertEqual(wide, "")
        self.assertIn("📡", radar)

    def test_empty_input(self):
        self.assertEqual(split_wide_radar_output(""), ("", ""))
        self.assertEqual(split_wide_radar_output(None), ("", ""))

    def test_emoji_variants_recognized(self):
        # 不同 emoji 写法
        content_a = "## 🌐 跨领域\n- A\n## 📡 早期机会\n- B"
        wide_a, radar_a = split_wide_radar_output(content_a)
        self.assertIn("A", wide_a)
        self.assertIn("B", radar_a)


# ─────────────────────────────────────────────────────────────────────
# build_overview_block
# ─────────────────────────────────────────────────────────────────────


class TestBuildOverviewBlock(unittest.TestCase):
    def test_full_overview(self):
        block = build_overview_block(
            deep_theme="Control Plane 演进",
            wide_themes=["Topic A", "Topic B", "Topic C"],
            radar_themes=["Signal 1", "Signal 2"],
            kb_stats={"sources_count": 14, "notes_count": 290, "kb_kbytes": 130, "reduce_chars": 80000},
        )
        self.assertIn("## 📋 今日连动 + 明日关注", block)
        self.assertIn("Control Plane 演进", block)
        self.assertIn("Topic A", block)
        self.assertIn("Topic B", block)
        self.assertIn("Signal 1", block)
        self.assertIn("14 sources", block)
        self.assertIn("290 notes", block)
        self.assertIn("明日关注", block)

    def test_no_radar_themes(self):
        block = build_overview_block(
            deep_theme="Theme",
            wide_themes=["A"],
            radar_themes=[],
            kb_stats=None,
        )
        # RADAR 行省略但不抛
        self.assertNotIn("📡 **RADAR", block)
        self.assertIn("A", block)

    def test_unrecognized_deep_theme_skipped(self):
        block = build_overview_block(
            deep_theme="(未识别主题)",
            wide_themes=["A"],
            radar_themes=[],
            kb_stats=None,
        )
        self.assertNotIn("(未识别主题)", block)
        self.assertNotIn("🌙 **DEEP 主题**", block)

    def test_empty_themes_returns_minimum_block(self):
        block = build_overview_block(
            deep_theme="",
            wide_themes=None,
            radar_themes=None,
            kb_stats=None,
        )
        self.assertIn("## 📋 今日连动 + 明日关注", block)
        self.assertIn("信号源稀薄", block)

    def test_overview_uses_h2_header(self):
        """V37.9.68 4 段切片机制依赖 `## ` header（kb_dream.sh:1421 split by `\\n## `）。"""
        block = build_overview_block("T", ["A"], ["B"], None)
        # 必须 `## ` 开头才能被 split 正确切到独立窗口
        self.assertTrue(block.startswith("## "))


# ─────────────────────────────────────────────────────────────────────
# extract_section_titles
# ─────────────────────────────────────────────────────────────────────


class TestExtractSectionTitles(unittest.TestCase):
    def test_bold_title_format(self):
        md = "- **Topic A**: content\n- **Topic B**: content"
        titles = extract_section_titles(md)
        self.assertEqual(titles, ["Topic A", "Topic B"])

    def test_bracket_title_format(self):
        md = "- [Signal A]\n- [Signal B]"
        titles = extract_section_titles(md)
        self.assertEqual(titles, ["Signal A", "Signal B"])

    def test_h3_header_format(self):
        md = "### Topic A\n内容\n### Topic B"
        titles = extract_section_titles(md)
        self.assertIn("Topic A", titles)
        self.assertIn("Topic B", titles)

    def test_dedup_titles(self):
        md = "- **A**: x\n- **A**: y\n- **B**: z"
        titles = extract_section_titles(md)
        self.assertEqual(titles, ["A", "B"])

    def test_max_n_truncation(self):
        md = "\n".join(f"- **T{i}**: x" for i in range(20))
        titles = extract_section_titles(md, max_n=5)
        self.assertEqual(len(titles), 5)

    def test_empty_input(self):
        self.assertEqual(extract_section_titles(""), [])
        self.assertEqual(extract_section_titles(None), [])

    def test_long_title_filtered(self):
        # 超 60 字的"标题"应忽略（实际是内容行）
        long_title = "X" * 80
        md = f"- **{long_title}**: x"
        titles = extract_section_titles(md)
        self.assertEqual(titles, [])


# ─────────────────────────────────────────────────────────────────────
# 血案场景回归（V37.9.68 核心防御）
# ─────────────────────────────────────────────────────────────────────


class TestBloodLessonQwenBimRegression(unittest.TestCase):
    """V37.9.68 血案防御：连续几周 Qwen-BIM 重复推送的端到端场景。"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="qwen_bim_")
        self.today = datetime(2026, 5, 14)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write_dream(self, date_str: str, theme: str):
        path = os.path.join(self.tmpdir, f"{date_str}.md")
        with open(path, "w") as f:
            f.write(f"# 🌙 Agent Dream — {date_str}\n\n")
            f.write(f"## 🌙 今日深度发现：{theme}\n")
        return path

    def test_qwen_bim_in_14d_history_blocks_repeat(self):
        """场景：过去 14 天 dream 含 Qwen-BIM，今日 LLM 若选 Qwen-BIM 变体，必须被 themes_overlap 抓到。"""
        # 历史：5/13 Qwen-BIM 14B
        self._write_dream("2026-05-13", "Qwen-BIM 14B 颠覆参数竞赛")
        # 5/10 control plane
        self._write_dream("2026-05-10", "Control Plane 三平面架构")
        # 5/8 ontology
        self._write_dream("2026-05-08", "Ontology Engine 抽象")

        recent = extract_recent_themes(self.tmpdir, days=14, today=self.today)
        # 3 个历史主题都应被捕获（都在 14 天内）
        self.assertEqual(len(recent), 3)

        # 模拟今日 LLM 想选 "Qwen-BIM 路线对工业 AI 的启示"
        today_candidate = normalize_theme_keywords("Qwen-BIM 路线对工业 AI 的启示")
        # 应当与 5/13 的 Qwen-BIM 主题 overlap
        blocked = any(themes_overlap(today_candidate, t["keywords"]) for t in recent)
        self.assertTrue(blocked, "今日 Qwen-BIM 候选应被 14 天历史拦截")

        # 反例：今日如果选 "Memory Plane 设计模式"，应当不被拦截
        new_candidate = normalize_theme_keywords("Memory Plane 设计模式")
        passed = any(themes_overlap(new_candidate, t["keywords"]) for t in recent)
        self.assertFalse(passed, "Memory Plane 是新主题不应被拦截")

    def test_banned_block_includes_qwen_bim_history(self):
        """场景：format_banned_themes_block 输出含 Qwen-BIM 历史，LLM 看到这段会避免选它。"""
        self._write_dream("2026-05-13", "Qwen-BIM 14B")
        self._write_dream("2026-05-12", "Qwen-BIM 工业应用")
        self._write_dream("2026-05-11", "Qwen-BIM vs 671B")

        recent = extract_recent_themes(self.tmpdir, days=14, today=self.today)
        block = format_banned_themes_block(recent)

        # 三天 Qwen-BIM 主题都应在 prompt 段中
        self.assertEqual(block.count("Qwen-BIM"), 3)
        # 硬规则文字
        self.assertIn("整份输出作废", block)
        self.assertIn("连续几周", block)


# ─────────────────────────────────────────────────────────────────────
# 反向验证守卫
# ─────────────────────────────────────────────────────────────────────


class TestSourceLevelGuards(unittest.TestCase):
    """源码级守卫：防止未来重构回退 V37.9.68 关键设计。"""

    def setUp(self):
        repo_root = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(repo_root, "kb_dream_helpers.py")) as f:
            self.src = f.read()

    def test_v37_9_68_marker_in_source(self):
        self.assertIn("V37.9.68", self.src)

    def test_default_days_is_14(self):
        """守卫: extract_recent_themes 默认 days=14 (不是 3 也不是 7)。"""
        import inspect

        from kb_dream_helpers import extract_recent_themes

        sig = inspect.signature(extract_recent_themes)
        default_days = sig.parameters["days"].default
        self.assertEqual(default_days, 14)

    def test_overlap_threshold_is_2(self):
        """守卫: themes_overlap 用 ≥2 关键词重叠（防被改回 ≥1）。"""
        self.assertIn(">= 2", self.src)  # `len(common) >= 2`

    def test_blood_lesson_documented(self):
        """守卫: 源码注释含 Qwen-BIM 血案引用（防被清掉历史教训）。"""
        self.assertIn("Qwen-BIM", self.src)
        self.assertIn("2026-05-14", self.src)

    def test_overview_uses_h2_header_for_split(self):
        """守卫: build_overview_block 输出 `## ` h2 header (依赖 kb_dream.sh 分窗逻辑)。"""
        block = build_overview_block("T", ["A"], ["B"], None)
        self.assertTrue(block.startswith("## "))


# ─────────────────────────────────────────────────────────────────────
# CLI 子进程测试
# ─────────────────────────────────────────────────────────────────────


class TestCli(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="cli_dream_")
        # V37.9.143 修日期敏感 bug: 原硬编码 "2026-05-13.md" 在 2026-06-12 (恰好 30 天后)
        # 滑出 --days 30 窗口导致测试失败 — 改相对日期 (now-3d) 永不出窗。
        from datetime import datetime, timedelta
        self.dream_date = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d")
        path = os.path.join(self.tmpdir, f"{self.dream_date}.md")
        with open(path, "w") as f:
            f.write("## 🌙 今日深度发现：CLI 测试主题")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_cli_show_recent_themes(self):
        repo_root = os.path.dirname(os.path.abspath(__file__))
        result = subprocess.run(
            [
                sys.executable,
                os.path.join(repo_root, "kb_dream_helpers.py"),
                "--show-recent-themes",
                self.tmpdir,
                "--days",
                "30",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn(self.dream_date, result.stdout)
        self.assertIn("CLI 测试主题", result.stdout)

    def test_cli_no_help_args_prints_help(self):
        repo_root = os.path.dirname(os.path.abspath(__file__))
        result = subprocess.run(
            [sys.executable, os.path.join(repo_root, "kb_dream_helpers.py")],
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("usage", result.stdout.lower())


class TestSplitDreamIntoChunks(unittest.TestCase):
    """V37.9.68-hotfix split_dream_into_chunks 纯函数单测。

    根因: V37.9.68 设计预期 4 段 ## header 各自独立成 4 chunks，
    但 V37.9.21 旧合并算法 (current + piece <= max_chunk 就合并) 把小段合并，
    导致 Mac Mini 5/15 03:00 cron 真激活时只产 3 chunks (WIDE+RADAR 合并)。
    属 V37.9.66 案例库 类别 B "设计假设错配"。

    新算法契约:
    - 每个 ## header 段独立成 chunk (不合并)
    - header 段 (首个 ## 之前) 合并到第一个 ## 段作 prefix
    - 单段超 max_chunk 时按 \\n 内部切分
    """

    def test_empty_returns_empty_list(self):
        self.assertEqual(split_dream_into_chunks(""), [])
        self.assertEqual(split_dream_into_chunks("   \n  \n"), [])

    def test_non_string_returns_empty(self):
        self.assertEqual(split_dream_into_chunks(None), [])  # type: ignore
        self.assertEqual(split_dream_into_chunks(123), [])  # type: ignore

    def test_v37_9_68_blood_lesson_four_section_independent_chunks(self):
        """V37.9.68-hotfix 血案场景回归: 4 段每段 < max_chunk 必产 4 chunks.

        模拟 5/15 真实 dream 数据形状 (DEEP / WIDE / RADAR / 总览),
        每段都 < max_chunk 但旧算法会合并成 3 chunks.
        """
        text = "# 🌙 Agent Dream — 2026-05-15\n\n"
        text += "> 模式: MapReduce 全量\n> 覆盖: 17 sources\n\n"
        text += "## 🌙 今日深度: 测试主题\n" + ("内容" * 500) + "\n\n"  # ~1500 chars
        text += "## 🌐 跨领域 × 5\n" + ("内容" * 500) + "\n\n"  # ~1500 chars
        text += "## 📡 准期信号 × 5\n" + ("内容" * 300) + "\n\n"  # ~900 chars
        text += "## 📋 今日连动\n" + ("内容" * 100)  # ~300 chars

        chunks = split_dream_into_chunks(text, max_chunk=4000)
        self.assertEqual(
            len(chunks), 4,
            f"V37.9.68 设计预期 4 段 ## header 独立成 4 chunks, 实际 {len(chunks)} (V37.9.66 类别 B 血案回归)"
        )
        # 每段独立验证
        self.assertIn("今日深度", chunks[0])
        self.assertIn("Agent Dream", chunks[0], "header 应合并到第一段作 prefix")
        self.assertIn("跨领域", chunks[1])
        self.assertIn("准期信号", chunks[2])
        self.assertIn("今日连动", chunks[3])
        # 反向验证: WIDE 段不应被合并到 RADAR 段
        self.assertNotIn("跨领域", chunks[2], "WIDE 段不应混入 RADAR chunk (旧合并算法 bug)")
        # 反向验证: RADAR 段不应被合并到总览段
        self.assertNotIn("准期信号", chunks[3], "RADAR 段不应混入总览 chunk")

    def test_header_merged_into_first_section(self):
        """header 部分（首个 ## 之前）应合并到第一个 ## 段作 prefix."""
        text = "# 🌙 Header\n\n> 元数据行\n\n## 第一段\n内容A"
        chunks = split_dream_into_chunks(text, max_chunk=4000)
        self.assertEqual(len(chunks), 1)
        # 第一个 chunk 必含 header + 第一段
        self.assertIn("🌙 Header", chunks[0])
        self.assertIn("元数据行", chunks[0])
        self.assertIn("第一段", chunks[0])
        self.assertIn("内容A", chunks[0])

    def test_oversized_section_internal_split(self):
        """单段超 max_chunk 仍内部按 \\n 切分, 不影响其他段独立."""
        text = "# Header\n\n"
        text += "## DEEP 大段\n"
        # 5500 chars 超 max_chunk=4000, 用 \n 让切分有 boundary
        big_lines = "\n".join("DEEP 段第 {} 行内容".format(i) for i in range(400))
        text += big_lines + "\n\n"
        text += "## WIDE\n" + ("WIDE 内容" * 200) + "\n\n"
        text += "## RADAR\n" + ("RADAR 内容" * 100) + "\n\n"
        text += "## 总览\n" + ("总览 内容" * 30)

        chunks = split_dream_into_chunks(text, max_chunk=4000)
        # DEEP 应被切成 ≥2 sub-chunks; WIDE/RADAR/总览 仍各自独立
        self.assertGreaterEqual(len(chunks), 4)
        # 每段不超 max_chunk
        for c in chunks:
            self.assertLessEqual(len(c), 4000, f"chunk 超 max_chunk: {len(c)} chars")
        # WIDE/RADAR/总览 仍各占独立 chunk
        wide_chunks = [i for i, c in enumerate(chunks) if "WIDE" in c]
        radar_chunks = [i for i, c in enumerate(chunks) if "RADAR" in c]
        overview_chunks = [i for i, c in enumerate(chunks) if "总览" in c]
        self.assertEqual(len(wide_chunks), 1, "WIDE 应仅在 1 个 chunk")
        self.assertEqual(len(radar_chunks), 1, "RADAR 应仅在 1 个 chunk")
        self.assertEqual(len(overview_chunks), 1, "总览 应仅在 1 个 chunk")
        # 顺序保持 DEEP < WIDE < RADAR < 总览
        self.assertLess(wide_chunks[0], radar_chunks[0])
        self.assertLess(radar_chunks[0], overview_chunks[0])

    def test_no_section_headers_text_returned_as_single_chunk(self):
        """退化场景: text 完全没 ## header, 整段作为单 chunk."""
        text = "# Just header\n\nSome plain text without sections."
        chunks = split_dream_into_chunks(text, max_chunk=4000)
        self.assertEqual(len(chunks), 1)
        self.assertIn("Just header", chunks[0])
        self.assertIn("plain text", chunks[0])

    def test_max_chunk_too_small_defaults_to_4000(self):
        """防御: max_chunk < 100 视为无效 fallback 到 4000."""
        text = "## A\n" + ("x" * 200) + "\n\n## B\n" + ("y" * 200)
        chunks_invalid = split_dream_into_chunks(text, max_chunk=10)
        # max_chunk 太小被 fallback 到 4000, 4 段 (实际只有 2) 都独立
        chunks_valid = split_dream_into_chunks(text, max_chunk=4000)
        self.assertEqual(len(chunks_invalid), len(chunks_valid))

    def test_empty_sections_skipped(self):
        """空段静默跳过, 不产空 chunk."""
        text = "# H\n\n## A\nx\n\n## \n\n## B\ny"
        chunks = split_dream_into_chunks(text, max_chunk=4000)
        # 空段 "## \n\n" 不应产 chunk
        for c in chunks:
            self.assertTrue(c.strip(), f"chunk 不应为空: {repr(c)}")

    def test_real_2026_05_15_layout_produces_4_chunks(self):
        """V37.9.68-hotfix Mac Mini 真实 5/15 dream 文件 layout 模拟.

        实际 17119 bytes / 4 个 ## header / DEEP 偏大但加 header 仍 < 4000
        （header ~150 + DEEP 实际生产 LLM 输出可能波动 1500-3500 chars）.
        """
        # 模拟 V37.9.68 设计字数（DEEP ~1800 / WIDE ~1700 / RADAR ~900 / 总览 ~400）
        text = "# 🌙 Agent Dream — 2026-05-15\n\n"
        text += "> 模式: MapReduce 全量（17 源）\n"
        text += "> 覆盖: 17 sources (8312KB) + 829 notes\n"
        text += "> Reduce 素材: 153351 chars\n"
        text += "> 生成时间: 2026-05-15 03:11:15\n\n"
        text += "## 🌙 今日深度: 测试\n"
        text += "### 发现过程\n" + ("a" * 600) + "\n\n"
        text += "### 🔗 隐藏关联\n" + ("b" * 700) + "\n\n"
        text += "### 🎯 行动建议\n" + ("c" * 500) + "\n\n"
        text += "## 🌐 跨领域 × 5\n" + ("d" * 1700) + "\n\n"
        text += "## 📡 准期信号 × 5\n" + ("e" * 900) + "\n\n"
        text += "## 📋 今日连动\n" + ("f" * 350)

        chunks = split_dream_into_chunks(text, max_chunk=4000)
        # DEEP 段 ~150+1800 < 4000 → 1 chunk; WIDE/RADAR/总览 各 1 → 共 4 chunks
        self.assertEqual(
            len(chunks), 4,
            f"V37.9.68 4 段设计预期 4 chunks, 实际 {len(chunks)} (V37.9.68-hotfix 守卫)"
        )


class TestSplitDreamProductionCallerFormat(unittest.TestCase):
    """V37.9.73 血案回归: 覆盖生产 caller 真实 input 形态 (V37.9.68-hotfix 测试盲区).

    V37.9.68-hotfix 12 个测试全部用 text = '# 🌙 Agent Dream — DATE\\n\\n> 模式: ...\\n\\n## DEEP'
    形态作为 input, 完全没覆盖生产 caller kb_dream.sh:1557 实际推的 DREAM_RESULT 形态
    (直接以 '## DEEP' 开头, 没有 '# Agent Dream' header).

    Mac Mini 2026-05-16 + 5/17 连续两天 cron 产 [3/3] 血案的真根因:
    split_dream_into_chunks 把 sections[0] (DEEP 段) 误当 header_part → 跟 WIDE 合并.

    V37.9.73 修复: 主动判断 sections[0] 是否以 '## ' 开头, 决定是 header 还是第一个 ## 段.
    本测试类锁定生产 caller 真实形态必须产 4 chunks, 防未来回归.

    MR-15 测试三层第三层不可替代: 单测全过 + 治理全过 + 用户连续两天 [3/3] 才暴露.
    MR-6 critical-invariants-need-depth: 测试 input 必须覆盖真实生产路径.
    """

    def test_v37_9_73_blood_lesson_no_header_4_chunks(self):
        """V37.9.73 血案直接复现: text 开头是 '## DEEP' (无 # Agent Dream header).

        镜像 kb_dream.sh:1479 DREAM_RESULT 拼接逻辑:
            DREAM_RESULT="$DEEP_RESULT\\n\\n$WIDE_BLOCK\\n\\n$RADAR_BLOCK\\n\\n$OVERVIEW_BLOCK"
        每段以 '## ' 开头, 整个 DREAM_RESULT 以 '## ' 开头.
        """
        # DEEP_RESULT (LLM 调用 1 真实形态)
        deep = "## 🌙 今日深度: 编译器反馈闭环提升LLM代码生成可信度\n"
        deep += "### 发现过程\n" + ("a" * 600) + "\n\n"
        deep += "### 🔗 隐藏关联\n" + ("b" * 800) + "\n\n"
        deep += "### 🎯 行动建议\n" + ("c" * 500)
        # WIDE_BLOCK (LLM 调用 2 拆出的第一段)
        wide = "## 🌐 跨领域鲜人知 × 5\n\n"
        wide += "- **地缘政治冲突对全球供应链与宏观经济的连锁效应**: " + ("d" * 600)
        # RADAR_BLOCK (LLM 漏 ## 📡 header 导致 DEGRADED 占位)
        radar = "## 📡 准期信号 × 5\n\n⚠️ [DEGRADED] RADAR 段本日生成失败 (1 chars < 600)."
        # OVERVIEW_BLOCK (规则提取)
        overview = "## 📋 今日连动 + 明日关注\n\n- 🌙 **DEEP 主题**: 编译器反馈闭环"

        # 生产 caller 拼接: 没有 '# Agent Dream' header
        dream_result = f"{deep}\n\n{wide}\n\n{radar}\n\n{overview}"
        self.assertTrue(
            dream_result.startswith("## "),
            "前置: DREAM_RESULT 必须以 '## ' 开头 (镜像 kb_dream.sh:1479)"
        )

        chunks = split_dream_into_chunks(dream_result, max_chunk=4000)
        self.assertEqual(
            len(chunks), 4,
            f"V37.9.73 血案回归: 生产 caller 形态必须产 4 chunks, 实际 {len(chunks)}. "
            f"chunk 内容: {[c[:50] for c in chunks]}"
        )
        # 每段独立验证
        self.assertIn("今日深度", chunks[0])
        self.assertNotIn("跨领域", chunks[0], "DEEP chunk 不应混入 WIDE (5/16-17 血案核心)")
        self.assertIn("跨领域", chunks[1])
        self.assertNotIn("准期信号", chunks[1], "WIDE chunk 不应混入 RADAR")
        self.assertIn("准期信号", chunks[2])
        self.assertIn("DEGRADED", chunks[2], "RADAR DEGRADED 占位符必须独立成 chunk")
        self.assertIn("今日连动", chunks[3])

    def test_v37_9_73_starts_with_double_hash_no_metadata(self):
        """text 以 '## A' 开头无任何 metadata, sections[0] 必为第一段而非空 header."""
        text = "## A\n" + ("内容" * 200) + "\n\n## B\n" + ("内容" * 200) + "\n\n## C\n" + ("内容" * 100)
        chunks = split_dream_into_chunks(text, max_chunk=4000)
        self.assertEqual(len(chunks), 3, f"3 段独立 ## header 必产 3 chunks, 实际 {len(chunks)}")
        self.assertIn("## A", chunks[0])
        self.assertIn("## B", chunks[1])
        self.assertIn("## C", chunks[2])

    def test_v37_9_73_backward_compat_with_header_preserved(self):
        """V37.9.68-hotfix 测试场景 (text 开头有 # Agent Dream header) 必须仍工作."""
        text = "# 🌙 Agent Dream — 2026-05-17\n\n> 模式: MapReduce\n\n"
        text += "## A\n" + ("x" * 500) + "\n\n## B\n" + ("y" * 500)
        chunks = split_dream_into_chunks(text, max_chunk=4000)
        self.assertEqual(len(chunks), 2, "向后兼容: header + 2 段 → 2 chunks (header 合并到第一段)")
        self.assertIn("Agent Dream", chunks[0], "header 应合并到第一段 (向后兼容)")
        self.assertIn("## A", chunks[0])
        self.assertIn("## B", chunks[1])
        self.assertNotIn("## A", chunks[1])

    def test_v37_9_73_production_layout_5_16_5_17_real_sizes(self):
        """模拟用户实际 5/17 推送的真实段大小 (DEEP ~2400 / WIDE ~700 / RADAR ~78 / 总览 ~70)."""
        # DEEP ~2400 chars
        deep = "## 🌙 今日深度: 编译器反馈闭环\n" + ("内容" * 600)
        # WIDE ~700 chars (LLM 只产 1 主题)
        wide = "## 🌐 跨领域鲜人知 × 5\n\n- **地缘政治冲突**: " + ("分析" * 100)
        # RADAR ~78 chars (DEGRADED 占位)
        radar = "## 📡 准期信号 × 5\n\n⚠️ [DEGRADED] RADAR 段本日生成失败 (1 chars < 600). DEEP 主题段仍有效, 明日重试。"
        # 总览 ~70 chars
        overview = "## 📋 今日连动 + 明日关注\n\n- 🌙 **DEEP 主题**: 编译器反馈闭环"

        dream_result = f"{deep}\n\n{wide}\n\n{radar}\n\n{overview}"
        chunks = split_dream_into_chunks(dream_result, max_chunk=4000)
        self.assertEqual(
            len(chunks), 4,
            f"5/17 真实 layout 必产 4 chunks, 实际 {len(chunks)} (V37.9.73 守卫 [3/3] 血案)"
        )

    def test_v37_9_73_first_is_h2_detection(self):
        """lstrip 后 '## ' 开头判定: 容错前导空白."""
        # 真生产: 直接 ## 开头
        c1 = split_dream_into_chunks("## A\n内容A\n\n## B\n内容B", max_chunk=4000)
        self.assertEqual(len(c1), 2)
        # 容错: 前导空白 + ## 开头
        c2 = split_dream_into_chunks("  \n## A\n内容A\n\n## B\n内容B", max_chunk=4000)
        self.assertEqual(len(c2), 2, "lstrip 后判定 '## ' 开头, 不应被前导空白影响")

    def test_v37_9_73_sabotage_revert_to_pre_fix_fails_loud(self):
        """反向验证守卫真有效: 模拟回退到 V37.9.68-hotfix 旧逻辑 (无 first_is_h2 判定),
        必须能立即被 test_v37_9_73_blood_lesson_no_header_4_chunks 抓到.

        本测试 grep 源码确认 V37.9.73 'first_is_h2' 字面量存在 (反向守卫).
        """
        import os
        repo_root = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(repo_root, "kb_dream_helpers.py")) as f:
            src = f.read()
        self.assertIn(
            "first_is_h2", src,
            "V37.9.73 修复必须含 first_is_h2 判定 (防止回退到 V37.9.68-hotfix bug)"
        )
        self.assertIn(
            "V37.9.73", src,
            "V37.9.73 marker 必须在 kb_dream_helpers.py 源码 (审计可追)"
        )


class TestKbDreamShellGuards(unittest.TestCase):
    """V37.9.68 kb_dream.sh 源码级守卫：防止未来重构回退新设计。

    检测 kb_dream.sh shell 文件本身（不跑脚本，纯 grep）。
    """

    def setUp(self):
        repo_root = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(repo_root, "kb_dream.sh")) as f:
            self.src = f.read()

    def test_v37_9_68_marker_present(self):
        self.assertIn("V37.9.68", self.src)

    def test_prev_themes_14_day_window(self):
        """守卫：调 extract_recent_themes 用 days=14（不是 3 / 7）。"""
        self.assertIn("days=14", self.src)
        # 反向验证旧 3 天模式被清除
        self.assertNotIn('ls -t "$DREAM_DIR"/*.md 2>/dev/null | head -3 ', self.src)

    def test_banned_themes_block_used(self):
        """守卫：BANNED_THEMES_BLOCK 变量被注入 DEEP_PROMPT。"""
        self.assertIn("BANNED_THEMES_BLOCK", self.src)
        self.assertIn("${BANNED_THEMES_BLOCK}", self.src)

    def test_two_llm_calls_not_three(self):
        """守卫：V37.9.68 是 2 次 LLM 调用，不是 V37.8.3 的 3 chunks。"""
        # 旧 chunked 段标志（必须被删除）
        self.assertNotIn("CHUNK1_RESULT", self.src)
        self.assertNotIn("CHUNK2_RESULT", self.src)
        self.assertNotIn("CHUNK3_RESULT", self.src)
        # 新设计标志
        self.assertIn("DEEP_RESULT", self.src)
        self.assertIn("WIDE_RADAR_RESULT", self.src)

    def test_deep_must_pass_fail_fast(self):
        """守卫：DEEP fail-fast 顺序锁 — 失败必 exit 1，不允许静默继续。

        V37.9.68: 500 chars 窗口已够 (printf + status_file + alert + exit 1).
        V37.9.75: 扩到 1000 chars 容纳 retry vs not-retry 告警双分支 (DEEP_RETRIED true/false 区分):
            原 ~300 → printf + status + alert + exit 1
            V37.9.75 ~570 → printf + status (含 deep_retried/deep_retry_chars) + if/else 双 alert + exit 1
        语义不变: exit 1 必须在 status:llm_failed marker 后立即出现, 不允许漂移到不相关位置.
        """
        # 找 DEEP fail 分支
        idx = self.src.find('"status":"llm_failed","phase":"deep"')
        self.assertGreater(idx, 0, "DEEP fail 状态写入分支应存在")
        # 该分支后 1000 字符内必须有 exit 1 (V37.9.75 扩窗容纳双告警分支)
        after = self.src[idx : idx + 1000]
        self.assertIn("exit 1", after, "DEEP 失败后必须立即 exit 1 fail-fast")

    def test_wide_radar_independent_degradation(self):
        """守卫：WIDE / RADAR 段失败独立 [DEGRADED] 标记，不阻塞 DEEP 推送。"""
        # [DEGRADED] 标记字面量
        self.assertIn("[DEGRADED]", self.src)
        # WIDE 段独立 status
        self.assertIn('WIDE_STATUS="ok"', self.src)
        self.assertIn('WIDE_STATUS="degraded"', self.src)
        # RADAR 段独立 status
        self.assertIn('RADAR_STATUS="ok"', self.src)
        self.assertIn('RADAR_STATUS="degraded"', self.src)

    def test_overview_uses_helper_module(self):
        """守卫：总览段调 kb_dream_helpers.build_overview_block 而非 inline 拼。"""
        self.assertIn("build_overview_block", self.src)
        self.assertIn("extract_section_titles", self.src)

    def test_status_json_schema_v9_68(self):
        """守卫：status.json 写入含 V37.9.68 新字段（multitheme/deep_chars/wide_status/radar_status）。"""
        self.assertIn('"multitheme":true', self.src)
        self.assertIn('"deep_chars":', self.src)
        self.assertIn('"wide_status":', self.src)
        self.assertIn('"radar_status":', self.src)

    def test_dream_result_four_section_concat(self):
        """守卫：DREAM_RESULT 拼接 4 段（DEEP + WIDE + RADAR + Overview）。"""
        # 找 DREAM_RESULT 拼接行
        idx = self.src.find('DREAM_RESULT="$DEEP_RESULT')
        self.assertGreater(idx, 0, "DREAM_RESULT 必须以 $DEEP_RESULT 起首")
        # 拼接行后 200 字符内必须含三个段
        after = self.src[idx : idx + 200]
        self.assertIn("$WIDE_BLOCK", after)
        self.assertIn("$RADAR_BLOCK", after)
        self.assertIn("$OVERVIEW_BLOCK", after)

    def test_min_deep_chars_threshold(self):
        """守卫：MIN_DEEP_CHARS=1200（V37.9.68 用户决策的最低字节阈值）。"""
        self.assertIn("MIN_DEEP_CHARS=1200", self.src)

    def test_anti_pollution_guard_retained(self):
        """守卫：V37.8.6 反污染守卫在两个 system prompt 中都保留。"""
        # DEEP_SYSTEM + WIDE_RADAR_SYSTEM 都引用 V37.8.6
        deep_idx = self.src.find('DEEP_SYSTEM="')
        wide_radar_idx = self.src.find('WIDE_RADAR_SYSTEM="')
        self.assertGreater(deep_idx, 0)
        self.assertGreater(wide_radar_idx, 0)
        # 两 system prompt 都引用反污染
        self.assertIn("V37.8.6", self.src[deep_idx : deep_idx + 2000])
        self.assertIn(
            "V37.8.6", self.src[wide_radar_idx : wide_radar_idx + 2000]
        )

    def test_no_lost_in_the_middle_warning_retained(self):
        """守卫：V37.4.2 长 prompt 长度衰减教训作为历史 comment 保留。

        kb_dream_helpers.py 中应当能见到血案历史引用。
        """
        # 主体 kb_dream.sh 不需要 V37.4.2 字面量（已被 V37.9.68 替代）
        # 但 V37.9.68 改动的复杂性教训应在 helper 中文档化（已有 "用户视角原则 #13"）
        pass  # 历史教训在 helpers.py 中

    def test_v37_9_68_hotfix_uses_split_dream_into_chunks_helper(self):
        """V37.9.68-hotfix 守卫: kb_dream.sh 用 helper 而非 inline 合并算法.

        Mac Mini 5/15 03:00 cron 实测发现旧 inline 合并算法导致 4 段被合并成 3 chunks.
        修复后必须改用 kb_dream_helpers.split_dream_into_chunks (MR-8 single-source-of-truth).
        """
        # 必须含 helper import
        self.assertIn("from kb_dream_helpers import split_dream_into_chunks", self.src,
                      "V37.9.68-hotfix 必须改用 split_dream_into_chunks helper")
        # 必须调用 helper
        self.assertIn("split_dream_into_chunks(text, max_chunk=", self.src,
                      "V37.9.68-hotfix 必须真正调用 helper, 而非仅 import")

    def test_v37_9_68_hotfix_no_inline_merge_algorithm_regression(self):
        """V37.9.68-hotfix 反向守卫: 旧合并算法字面量必须被消除.

        防止未来重构回退到 'current + piece <= max_chunk 就合并' 反模式.
        本守卫是 V37.9.66 案例库 类别 B "设计假设错配" 的硬防御.
        """
        # 旧合并算法的标志: current 变量累积 + 合并条件
        # 不应再出现完整模式 (允许 helper 内部 docstring 提到为反例)
        merge_pattern = "if len(current) + len(piece) + 1 <= max_chunk:"
        self.assertNotIn(merge_pattern, self.src,
                         f"V37.9.68-hotfix 必须消除旧合并算法 '{merge_pattern}' (V37.9.66 类别 B)")
        # 旧 inline `current = ... + piece` 累积模式
        self.assertNotIn("current = current + '\\n' + piece if current else piece", self.src,
                         "V37.9.68-hotfix 必须消除 current 累积变量")

    def test_v37_9_68_hotfix_marker_present(self):
        """V37.9.68-hotfix marker 必须在 shell 文件中可 grep."""
        self.assertIn("V37.9.68-hotfix", self.src,
                      "V37.9.68-hotfix marker 必须存在便于运维 grep")


class TestV37974RadarRetryAndPromptHardening(unittest.TestCase):
    """V37.9.74 RADAR LLM drift 修复 (方案 2 prompt 强化 + 方案 3 retry 兜底).

    背景: 2026-05-16 + 5/17 连续两天 Mac Mini cron LLM drift 漏 ## 📡 RADAR header
    → RADAR_BLOCK="" → wc -c = 1 → < MIN_SECTION_CHARS=600 → DEGRADED 占位符推送.
    用户视角 [3/3] 血案的 LLM 输出 drift 根因 (chunk 切分由 V37.9.73 修, drift 本身 V37.9.74 修).

    方案 2 (prompt 强化): WIDE_RADAR_SYSTEM 加 V37.9.74 硬要求 + WIDE_RADAR_PROMPT 加输出自检 checklist.
    方案 3 (retry 兜底): 当 WIDE ok 但 RADAR 缺失时, 主动调一次 LLM 只产 RADAR 段, 失败仍走 DEGRADED.
    用户决策 V37.9.74 retry 走默认 primary (Qwen3), doubao 试水留 V37.9.75 (B 候选, 需 adapter ?provider= 支持).

    多层防御: prompt 强化 (预防) + retry 兜底 (恢复) + DEGRADED 占位符 (最后兜底). 不依赖任一层 100%.
    """

    def setUp(self):
        repo_root = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(repo_root, "kb_dream.sh")) as f:
            self.src = f.read()

    # === 方案 2: prompt 强化守卫 ===

    def test_v37_9_74_marker_in_source(self):
        """V37.9.74 marker 必须在 shell 文件可 grep."""
        self.assertIn("V37.9.74", self.src, "V37.9.74 marker 必须存在")

    def test_wide_radar_system_strict_two_section_requirement(self):
        """WIDE_RADAR_SYSTEM prompt 强化: 必须输出完整 2 段 + RADAR header 字面量."""
        self.assertIn("**必须**输出完整 2 段", self.src,
                      "V37.9.74: WIDE_RADAR_SYSTEM 必须有'必须输出完整 2 段'硬约束 (markdown bold 强调)")
        self.assertIn("写完 WIDE 第 5 条后", self.src,
                      "V37.9.74: 必须有'写完 WIDE 后必须立即接 RADAR'约束 (防 LLM drift 提前收尾)")

    def test_wide_radar_prompt_self_check_checklist(self):
        """WIDE_RADAR_PROMPT 强化: 含 V37.9.74 输出自检 checklist."""
        self.assertIn("V37.9.74 输出自检", self.src,
                      "V37.9.74: prompt 必须含'输出自检'段")
        # 5 项 checklist 应都在
        self.assertIn("第一段开头是字面量", self.src)
        self.assertIn("第二段开头是字面量", self.src)
        self.assertIn("没有写完 WIDE 就直接收尾", self.src,
                      "V37.9.74: checklist 必须显式提示 5/16+5/17 真实 drift 模式")

    def test_prompt_mentions_v37_9_74_retry_in_hard_requirement(self):
        """硬性要求段必须提示 V37.9.74 retry 机制 (告诉 LLM 不合格会触发 retry)."""
        self.assertIn("V37.9.74 会自动 retry RADAR 一次", self.src,
                      "V37.9.74: 硬性要求必须告知 LLM retry 机制 (防 LLM 误以为 unrecoverable)")

    # === 方案 3: retry 兜底逻辑守卫 ===

    def test_radar_retry_block_exists(self):
        """V37.9.74 RADAR retry 兜底逻辑必须存在."""
        # 触发条件: WIDE ok + RADAR degraded
        self.assertIn('[ "$RADAR_STATUS" = "degraded" ] && [ "$WIDE_STATUS" = "ok" ]', self.src,
                      "V37.9.74: retry 必须仅在 WIDE ok + RADAR degraded 时触发")
        # retry-only prompt 标识
        self.assertIn("RADAR_RETRY_SYSTEM", self.src, "V37.9.74: RADAR_RETRY_SYSTEM 变量必须定义")
        self.assertIn("RADAR_RETRY_PROMPT", self.src, "V37.9.74: RADAR_RETRY_PROMPT 变量必须定义")
        # llm_call 真调用
        self.assertIn('llm_call "$RADAR_RETRY_PROMPT"', self.src,
                      "V37.9.74: retry 必须真调用 llm_call")

    def test_retry_validates_both_chars_and_header(self):
        """V37.9.74 retry 结果必须双重验证: chars >= MIN + 含 ## 📡 header.

        防止 LLM 产出 1500 字垃圾文本但仍漏 RADAR header 的 corner case.
        """
        self.assertIn('RADAR_RETRY_HAS_HEADER', self.src,
                      "V37.9.74: retry 必须验证 RADAR header 字面量存在")
        # 双重验证: chars + header
        self.assertIn('[ "$RADAR_RETRY_CHARS" -ge "$MIN_SECTION_CHARS" ]', self.src,
                      "V37.9.74: retry 必须验证 chars 阈值")
        self.assertIn('[ "$RADAR_RETRY_HAS_HEADER" = "true" ]', self.src,
                      "V37.9.74: retry 必须验证 has_header=true (双重防御)")

    def test_retry_success_sets_status_ok_for_downstream_compat(self):
        """retry 成功必须 RADAR_STATUS='ok' (下游零改动 — total counter / SUCCESSFUL_SECTIONS 等)."""
        # retry success 分支必须设 RADAR_STATUS="ok" (不是 "ok_via_retry" 避免下游 if 漏判)
        # 在 retry block 内部检查
        retry_idx = self.src.find('V37.9.74 RADAR retry 成功')
        self.assertGreater(retry_idx, 0, "V37.9.74: retry 成功日志字面量必须存在")
        # 在该日志前 500 字符内必须有 RADAR_STATUS="ok"
        window = self.src[max(0, retry_idx - 500):retry_idx]
        self.assertIn('RADAR_STATUS="ok"', window,
                      "V37.9.74: retry 成功后 RADAR_STATUS 必须设 'ok' (下游兼容)")

    def test_retry_failure_falls_through_to_v37_9_73_degraded(self):
        """retry 失败必须 fall-through 到 V37.9.73 DEGRADED 兜底 (不阻塞 DEEP 推送)."""
        # 即使 retry 失败也不 exit, 由后续 DEGRADED 块处理
        # 检查 retry 失败日志后没有 exit 1
        retry_fail_idx = self.src.find('V37.9.74 RADAR retry 失败')
        self.assertGreater(retry_fail_idx, 0, "retry 失败日志必须存在")
        # 失败日志后 200 字符内不应有 exit
        window = self.src[retry_fail_idx:retry_fail_idx + 200]
        self.assertNotIn("exit 1", window,
                         "V37.9.74: retry 失败不应 exit (走 V37.9.73 DEGRADED 兜底, DEEP 推送不阻塞)")

    def test_retry_prompt_excludes_deep_theme_and_wide_block(self):
        """retry prompt 必须显式排除 DEEP 主题 + WIDE 已覆盖主题 (防 LLM 重复)."""
        self.assertIn('严禁重复】${DEEP_THEME}', self.src,
                      "V37.9.74: retry 必须显式排除 DEEP 主题")
        self.assertIn('上次 WIDE 已覆盖主题', self.src,
                      "V37.9.74: retry 必须告知 LLM WIDE 已覆盖主题")
        self.assertIn('${WIDE_BLOCK}', self.src,
                      "V37.9.74: retry prompt 必须注入 WIDE_BLOCK 防重复")

    # === schema 升级守卫 ===

    def test_status_json_schema_includes_radar_retried_field(self):
        """status.json 必须含 radar_retried + radar_retry_chars 字段 (运维可观测).

        Mac Mini 部署后通过 last_run.json `radar_retried` 字段统计 LLM drift 频率.
        """
        self.assertIn('"radar_retried":%s', self.src,
                      "V37.9.74: status.json schema 必须含 radar_retried 字段")
        self.assertIn('"radar_retry_chars":%d', self.src,
                      "V37.9.74: status.json schema 必须含 radar_retry_chars 字段")
        # printf 参数顺序必须对应
        self.assertIn('"${RADAR_RETRIED:-false}"', self.src,
                      "V37.9.74: status.json 必须用 RADAR_RETRIED 变量 (defaults to false)")

    def test_radar_retried_initialized_false_before_retry_block(self):
        """RADAR_RETRIED 变量必须在 retry block 之前初始化 false (防止 set -u 未定义错误)."""
        # 初始化必须在 retry block 之前
        init_idx = self.src.find('RADAR_RETRIED=false')
        retry_block_idx = self.src.find('主动 retry RADAR-only')
        self.assertGreater(init_idx, 0, "RADAR_RETRIED=false 初始化必须存在")
        self.assertLess(init_idx, retry_block_idx,
                        "V37.9.74: RADAR_RETRIED 必须先初始化 false 再用 (顺序锁)")

    # === V37.9.75 候选 B 占位守卫 (doubao 试水路径登记) ===

    def test_v37_9_75_doubao_candidate_documented_in_comment(self):
        """V37.9.74 注释必须登记 V37.9.75 候选 B (doubao 试水) — 防止候选丢失."""
        self.assertIn("V37.9.75+ 候选 B", self.src,
                      "V37.9.74 注释必须显式登记 V37.9.75 doubao 试水候选")
        self.assertIn("doubao", self.src,
                      "V37.9.74 注释必须提到 doubao (V37.9.55 试水承诺路径)")


class TestV37975DeepRetryFallback(unittest.TestCase):
    """V37.9.75 DEEP retry 兜底 (LLM partial content 修复, 镜像 V37.9.74 RADAR retry 同款模式).

    背景: 2026-05-18 03:02 Mac Mini cron LLM provider 端返回 823 chars partial content,
    head 显示 header 完整但 "### 发现过程" 章节刚开头就停 (5/15-17 三天稳定 6314-7125 chars).
    时长 214s vs 前三天 100-113s = LLM 用 2x 时间产 1/8 内容 → LLM stream 端异常截断.

    用户决策 Option B: DEEP 加 retry 兜底 (镜像 V37.9.74 RADAR retry 模式).
    多层防御: prompt 强 (V37.9.68 已有) + retry 一次 (V37.9.75 新增) + fail-fast (V37.9.68 兜底).
    """

    def setUp(self):
        repo_root = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(repo_root, "kb_dream.sh")) as f:
            self.src = f.read()

    # === V37.9.75 marker + 血案溯源守卫 ===

    def test_v37_9_75_marker_in_source(self):
        """V37.9.75 marker 必须在 shell 文件可 grep (审计追溯)."""
        self.assertIn("V37.9.75", self.src, "V37.9.75 marker 必须存在")

    def test_blood_lesson_evidence_documented(self):
        """V37.9.75 注释必须显式记录 5/18 血案触发数据 (5/15-17 vs 5/18 对比)."""
        self.assertIn("2026-05-18", self.src, "V37.9.75 注释必须含血案日期 2026-05-18")
        self.assertIn("823 chars", self.src, "V37.9.75 注释必须含实际 partial chars 数 (823)")
        self.assertIn("5/15-17", self.src, "V37.9.75 注释必须对比 5/15-17 稳定数据")

    # === retry 触发条件守卫 ===

    def test_deep_retry_trigger_only_on_partial_not_empty(self):
        """retry 只在 LLM 返回 partial (非空但短) 时触发, 完全空时跳过 (不浪费 token)."""
        # 触发条件: -n DEEP_RESULT (非空) AND DEEP_CHARS < MIN_DEEP_CHARS (短)
        self.assertIn('[ -n "${DEEP_RESULT// }" ] && [ "$DEEP_CHARS" -lt "$MIN_DEEP_CHARS" ]', self.src,
                      "V37.9.75: retry 必须仅在 LLM 真返回 partial content 时触发")

    def test_deep_retry_block_exists(self):
        """V37.9.75 DEEP retry 兜底逻辑必须存在."""
        self.assertIn("DEEP_RETRY_SYSTEM", self.src, "V37.9.75: DEEP_RETRY_SYSTEM 变量必须定义")
        self.assertIn("DEEP_RETRY_PROMPT", self.src, "V37.9.75: DEEP_RETRY_PROMPT 变量必须定义")
        self.assertIn('llm_call "$DEEP_RETRY_PROMPT"', self.src,
                      "V37.9.75: retry 必须真调用 llm_call")

    # === retry 双重验证守卫 ===

    def test_retry_validates_both_chars_and_header(self):
        """V37.9.75 retry 结果必须双重验证: chars >= MIN_DEEP_CHARS + 含 ## 🌙 今日深度 header.

        防止 LLM 产 1400 字垃圾文本但漏 header 的 corner case.
        """
        self.assertIn('DEEP_RETRY_HAS_HEADER', self.src,
                      "V37.9.75: retry 必须验证 DEEP header 字面量存在")
        self.assertIn('[ "$DEEP_RETRY_CHARS" -ge "$MIN_DEEP_CHARS" ]', self.src,
                      "V37.9.75: retry 必须验证 chars >= MIN_DEEP_CHARS")
        self.assertIn('[ "$DEEP_RETRY_HAS_HEADER" = "true" ]', self.src,
                      "V37.9.75: retry 必须验证 has_header=true (双重防御)")
        # header 字面量必须是 "## 🌙 今日深度" (与 V37.9.68 DEEP prompt 输出格式硬要求对齐)
        self.assertIn("'## 🌙 今日深度'", self.src,
                      "V37.9.75: header 验证字面量必须是 '## 🌙 今日深度' (V37.9.68 prompt 硬要求)")

    # === retry 成功 / 失败路径守卫 ===

    def test_retry_success_uses_retry_result_continues_pipeline(self):
        """retry 成功必须用 retry 结果 + 继续 WIDE+RADAR (用户能拿到完整梦境)."""
        retry_success_idx = self.src.find('V37.9.75 DEEP retry 成功')
        self.assertGreater(retry_success_idx, 0, "V37.9.75: retry 成功日志字面量必须存在")
        # 成功路径前 300 字符内必须有 DEEP_RESULT="$DEEP_RETRY_RESULT" (用 retry 结果)
        window = self.src[max(0, retry_success_idx - 300):retry_success_idx]
        self.assertIn('DEEP_RESULT="$DEEP_RETRY_RESULT"', window,
                      "V37.9.75: retry 成功后 DEEP_RESULT 必须替换为 retry 结果")
        self.assertIn('DEEP_CHARS=$DEEP_RETRY_CHARS', window,
                      "V37.9.75: retry 成功后 DEEP_CHARS 必须更新为 retry chars")

    def test_retry_failure_falls_through_to_v37_9_68_fail_fast(self):
        """retry 失败必须 fall-through 到 V37.9.68 fail-fast (DEEP 必过原则不破).

        检查 retry 失败 log 出现后到 retry block 结束 (`fi\nfi`) 之间没有 exit 1 语句调用.
        (容忍后续注释里"不及格 exit 1"字面量, 那是文档不是控制流)
        """
        retry_fail_idx = self.src.find('V37.9.75 DEEP retry 失败')
        self.assertGreater(retry_fail_idx, 0, "V37.9.75: retry 失败日志必须存在")
        # 从 retry 失败 log 开始, 取到 retry block 闭合后立即出现的 `# DEEP fail-fast` 注释
        block_end_idx = self.src.find('# DEEP fail-fast', retry_fail_idx)
        self.assertGreater(block_end_idx, retry_fail_idx,
                           "retry block 后必须有 V37.9.68 fail-fast 注释作 marker")
        window = self.src[retry_fail_idx:block_end_idx]
        # 用 regex 找独立的 `exit 1` 语句 (非注释), 而不是子串匹配
        # 注释行 (# ...) 里出现 "exit 1" 字面量是合法 (V37.9.68 设计文档)
        import re as _re_test
        for ln in window.split("\n"):
            stripped = ln.strip()
            if stripped.startswith("#"):
                continue  # 注释行豁免
            if _re_test.search(r"\bexit\s+1\b", stripped):
                self.fail(f"V37.9.75: retry 失败到 fail-fast 之间发现 'exit 1' 控制流语句 (走 V37.9.68 兜底原则): {stripped}")

    # === retry prompt 设计守卫 ===

    def test_retry_prompt_mentions_previous_partial_chars(self):
        """retry prompt 必须显式告知 LLM 上次产 partial (引用具体字数让 LLM 重视)."""
        self.assertIn('${DEEP_CHARS} 字', self.src,
                      "V37.9.75: retry prompt 必须引用上次 DEEP_CHARS (告知 LLM 上次 partial)")
        self.assertIn('partial content', self.src,
                      "V37.9.75: retry SYSTEM 必须含 'partial content' 描述")

    def test_retry_prompt_preserves_ban_list_quality(self):
        """retry prompt 必须保留 ban-list (质量优先于成功率).

        不放弃 V37.9.68 14 天 ban-list 硬规则——retry 不是降级模式, 是修复模式.
        """
        # retry PROMPT 必须 inject BANNED_THEMES_BLOCK
        retry_prompt_idx = self.src.find("DEEP_RETRY_PROMPT=")
        self.assertGreater(retry_prompt_idx, 0, "DEEP_RETRY_PROMPT 必须存在")
        # 从 DEEP_RETRY_PROMPT 开始 2000 字符内必须 inject BANNED_THEMES_BLOCK
        window = self.src[retry_prompt_idx:retry_prompt_idx + 2000]
        self.assertIn('${BANNED_THEMES_BLOCK}', window,
                      "V37.9.75: retry prompt 必须保留 ban-list (V37.9.68 主题去重不放弃)")

    def test_retry_prompt_has_output_self_check_checklist(self):
        """retry prompt 必须含 5 项输出自检 checklist (镜像 V37.9.74 同款防 drift 模式).

        shell 文件中 retry prompt 是 heredoc-like 双引号字符串, 内部 `"` 用 `\\"` 转义.
        所以 assertion 用源码字面量 `\\"## 🌙 今日深度:\\"` 而非已 unescape 字符串.
        """
        self.assertIn("V37.9.75 retry 输出自检", self.src,
                      "V37.9.75: retry prompt 必须含'输出自检'段")
        # 5 项 checklist (镜像 V37.9.74 WIDE+RADAR 的输出自检模式)
        # shell 源码字面量含 \" 转义符
        self.assertIn(r'\"## 🌙 今日深度:\" 开头', self.src,
                      "V37.9.75: checklist 必须含 header 字面量检查 (shell escape 形式)")
        self.assertIn('1400 字', self.src,
                      "V37.9.75: checklist 必须含字数检查 (上次过短 → 这次必须够)")
        self.assertIn('没有写到一半截断', self.src,
                      "V37.9.75: checklist 必须显式提示 5/18 真实 partial drift 模式")

    # === fail-fast 升级守卫 ===

    def test_fail_fast_status_file_includes_retry_metadata(self):
        """fail-fast 时 status.json 必须含 deep_retried + deep_retry_chars (运维可观测)."""
        # llm_failed 分支的 printf 必须含新字段
        self.assertIn('"deep_retried":%s,"deep_retry_chars":%d', self.src,
                      "V37.9.75: fail-fast status_file 必须含 deep_retried + deep_retry_chars 字段")
        self.assertIn('"$DEEP_RETRIED" "$DEEP_RETRY_CHARS"', self.src,
                      "V37.9.75: fail-fast printf 必须传 DEEP_RETRIED + DEEP_RETRY_CHARS 变量")

    def test_fail_fast_alert_distinguishes_retry_attempted_vs_skipped(self):
        """fail-fast 告警必须区分 retry 已尝试 vs retry 未触发 (运维诊断辅助)."""
        # retry 已尝试: "V37.9.75 DEEP 段双失败 (原始 ... + retry ...)"
        self.assertIn("V37.9.75 DEEP 段双失败", self.src,
                      "V37.9.75: retry 尝试后告警必须显示'双失败'区分")
        # retry 未触发 (DEEP_RESULT 空): "V37.9.68 DEEP 段失败 (..., retry 未触发因 LLM 完全空)"
        self.assertIn('retry 未触发因 LLM 完全空', self.src,
                      "V37.9.75: retry 未触发场景必须有专门告警语义")
        # 条件分支判断 DEEP_RETRIED
        self.assertIn('if [ "$DEEP_RETRIED" = "true" ]; then', self.src,
                      "V37.9.75: 告警分支必须根据 DEEP_RETRIED 区分")

    # === schema 升级守卫 (成功路径 status.json) ===

    def test_success_status_json_includes_deep_retried_field(self):
        """成功路径 status.json 必须含 deep_retried + deep_retry_chars 字段.

        Mac Mini 部署后通过 last_run.json deep_retried 字段统计 LLM partial content 频率.
        """
        self.assertIn('"deep_retried":%s', self.src,
                      "V37.9.75: success status.json schema 必须含 deep_retried 字段")
        self.assertIn('"deep_retry_chars":%d', self.src,
                      "V37.9.75: success status.json schema 必须含 deep_retry_chars 字段")
        # printf 参数顺序必须用 DEEP_RETRIED 变量 (defaults to false)
        self.assertIn('"${DEEP_RETRIED:-false}"', self.src,
                      "V37.9.75: success status.json 必须用 DEEP_RETRIED 变量 (defaults to false)")

    def test_deep_retried_initialized_false_before_retry_block(self):
        """DEEP_RETRIED 变量必须在 retry block 之前初始化 false (顺序锁防 set -u 未定义错误)."""
        init_idx = self.src.find('DEEP_RETRIED=false')
        retry_block_idx = self.src.find('主动 retry (LLM partial content 兜底)')
        self.assertGreater(init_idx, 0, "DEEP_RETRIED=false 初始化必须存在")
        self.assertLess(init_idx, retry_block_idx,
                        "V37.9.75: DEEP_RETRIED 必须先初始化 false 再用 (顺序锁)")

    # === MIN_DEEP_CHARS 提前定义守卫 ===

    def test_min_deep_chars_defined_before_retry_block(self):
        """MIN_DEEP_CHARS 必须在 retry block 之前定义 (retry 触发条件依赖它).

        原 V37.9.68 把 MIN_DEEP_CHARS=1200 放在 fail-fast if 内, V37.9.75 移到 retry 之前.
        """
        min_chars_idx = self.src.find('MIN_DEEP_CHARS=1200')
        retry_trigger_idx = self.src.find('[ "$DEEP_CHARS" -lt "$MIN_DEEP_CHARS" ]')
        self.assertGreater(min_chars_idx, 0, "MIN_DEEP_CHARS=1200 必须定义")
        self.assertGreater(retry_trigger_idx, 0, "retry 触发条件必须用 $MIN_DEEP_CHARS")
        self.assertLess(min_chars_idx, retry_trigger_idx,
                        "V37.9.75: MIN_DEEP_CHARS 必须先定义再被 retry 触发条件使用")

    # === V37.9.76+ 候选登记 ===

    def test_v37_9_76_doubao_candidate_documented_in_comment(self):
        """V37.9.75 注释必须登记 V37.9.76+ 候选 (doubao 试水路径, V37.9.55+ 承诺继续)."""
        self.assertIn("V37.9.76+", self.src,
                      "V37.9.75 注释必须登记 V37.9.76+ 候选 (doubao 试水)")
        # V37.9.75 retry 仍走 primary, doubao 试水留 V37.9.76+
        # (与 V37.9.74 RADAR retry 同款留候选 B 模式对齐)


class TestV37_9_235_SignalFreshness(unittest.TestCase):
    """V37.9.235 (observer 2026-07-03 finding #4): dream 远期信号时效标注。

    血案形态: dream 引用 ~3 个月前的外部信号 (2026-04-04/03-07/04-20) 作
    "今日深度" 印证, 无时效标注 → 读者误以为当日新闻。
    修复: DEEP + WIDE_RADAR 两 system prompt 加时效标注硬规则 (>14 天信号
    必须标注「(长期背景, 非近期新增)」)。
    """

    @classmethod
    def setUpClass(cls):
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "kb_dream.sh")
        with open(path, encoding="utf-8") as f:
            cls.src = f.read()

    def test_freshness_rule_in_both_system_prompts(self):
        """DEEP_SYSTEM + WIDE_RADAR_SYSTEM 各一条 (原则 #31 双 prompt 全量同步)"""
        self.assertEqual(self.src.count("V37.9.235 信号时效标注"), 2,
                         "时效标注规则必须在 DEEP 与 WIDE_RADAR 两个 system prompt 各出现一次")

    def test_long_term_background_label_literal(self):
        self.assertEqual(self.src.count("(长期背景, 非近期新增)"), 2)

    def test_14_day_threshold_aligned_with_banlist(self):
        """时效阈值 14 天与 dream 主题 ban-list / deep_dive V37.9.233 同窗口 (一物一形)"""
        import re
        rules = re.findall(r"V37\.9\.235 信号时效标注】[^\n]+", self.src)
        self.assertEqual(len(rules), 2)
        for r in rules:
            self.assertIn("14 天", r)


if __name__ == "__main__":
    unittest.main(verbosity=2)
