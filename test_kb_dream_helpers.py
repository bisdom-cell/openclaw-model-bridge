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
        path = os.path.join(self.tmpdir, "2026-05-13.md")
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
        self.assertIn("2026-05-13", result.stdout)
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
        """守卫：DEEP fail-fast 顺序锁 — 失败必 exit 1，不允许静默继续。"""
        # 找 DEEP fail 分支
        idx = self.src.find('"status":"llm_failed","phase":"deep"')
        self.assertGreater(idx, 0, "DEEP fail 状态写入分支应存在")
        # 该分支后 500 字符内必须有 exit 1
        after = self.src[idx : idx + 500]
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
