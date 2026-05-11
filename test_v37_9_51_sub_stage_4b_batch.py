#!/usr/bin/env python3
"""test_v37_9_51_sub_stage_4b_batch.py — V37.9.51 Sub-Stage 4b 6 脚本批量验证

V37.9.51 = Sub-Stage 4b 续 batch 6 个论文/repo/tweet 类脚本机械迁移
(V37.9.45 hf_papers / V37.9.50 semantic_scholar 同款 6 字段 + rule_check 模板横向应用):
  1/6 rss_blogs        — 博客类, rule_content = title + description
  2/6 dblp             — 论文类 (无 abstract), rule_content = title + venue
  3/6 arxiv_monitor    — 论文类, rule_content = title + abstract
  4/6 github_trending  — 仓库类, rule_content = full_name + description + topics
  5/6 ai_leaders_x     — tweet 类, rule_content = author + text
  6/6 hn (run_hn_fixed) — HN 帖子类, rule_content = title + desc (清 HTML)

每个脚本应有:
  - V37.9.51 marker
  - prompt 6 字段 (📌 🔑 💡 🎯 ⭐ 🎚️) + OpenClaw 5 档评分指南
  - parse_6field_output 函数 + alignment 字段
  - emit heredoc imports os (V37.9.50-hotfix 同款防 NameError)
  - lazy import project_alignment_scorer (4 helper, FAIL-OPEN)
  - rule_check 调用 + high_alignment_count 统计
  - 末尾"高对齐 ⭐≥4: N/M" 行
  - 禁止 V37.9.36 占位符反模式

测试类:
  TestV37951SharedGuards          — 6 个脚本共用守卫 (V37.9.51 marker / 6 字段 / parser / lazy import)
  TestV37951PerScriptRuleContent  — 每个脚本特有 rule_content 拼接
  TestV37951AlignedScriptsAudit   — ontology/llm_cron_audit.py ALIGNED_SCRIPTS 集成
"""

import os
import re
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


# 6 个目标脚本路径 + 显示名 (用于断言失败时定位)
SUB_STAGE_4B_SCRIPTS = [
    ("rss_blogs", "jobs/rss_blogs/run_rss_blogs.sh"),
    ("dblp", "jobs/dblp/run_dblp.sh"),
    ("arxiv_monitor", "jobs/arxiv_monitor/run_arxiv.sh"),
    ("github_trending", "jobs/github_trending/run_github_trending.sh"),
    ("ai_leaders_x", "jobs/ai_leaders_x/run_ai_leaders_x.sh"),
    ("hn", "run_hn_fixed.sh"),
]


# ── Test 1: TestV37951SharedGuards ─────────────────────────────────
class TestV37951SharedGuards(unittest.TestCase):
    """6 个脚本共用的 V37.9.51 6 字段 + rule_check 守卫.

    每条守卫调用 `_assert_for_each` 对 6 脚本逐一断言, 失败时显示具体哪个脚本不达标.
    """

    def _for_each(self, predicate, msg):
        """对 6 脚本依次跑 predicate(name, src) -> bool, 失败时报告具体脚本."""
        for name, rel_path in SUB_STAGE_4B_SCRIPTS:
            with self.subTest(script=name):
                src = _read(os.path.join(REPO_ROOT, rel_path))
                self.assertTrue(predicate(name, src), f"{name}: {msg}")

    def test_v37_9_51_marker_present(self):
        """所有 6 脚本必须含 V37.9.51 标记."""
        self._for_each(lambda n, s: "V37.9.51" in s, "缺 V37.9.51 标记")

    def test_prompt_has_alignment_field(self):
        """prompt 必含 🎚️ 项目对齐度 字段定义."""
        self._for_each(
            lambda n, s: ("🎚️ 项目对齐度" in s) and ("一句话原因" in s) and ("≤ 30 字" in s),
            "prompt 缺 🎚️ 项目对齐度 字段定义"
        )

    def test_prompt_has_alignment_scoring_guide(self):
        """prompt 必含 OpenClaw 项目方向 5 档评分指南."""
        keywords = ["OpenClaw 项目方向", "control plane", "agent runtime", "ontology",
                    "memory plane", "⭐⭐⭐⭐⭐ = 直接相关", "间接相关",
                    "一般 AI/ML 趋势", "无明显关联", "完全无关"]
        for kw in keywords:
            with self.subTest(keyword=kw):
                self._for_each(lambda n, s, k=kw: k in s, f"prompt 缺 '{kw}'")

    def test_prompt_has_alignment_anti_hallucination(self):
        """prompt 必含 alignment 反幻觉守卫 (MR-8 单一真理源)."""
        self._for_each(
            lambda n, s: ("项目对齐度评分必须基于" in s) and ("而非泛泛 AI 相关" in s),
            "prompt 缺反幻觉短语"
        )

    def test_prompt_uses_6field_not_5field(self):
        """prompt 必须声明 '6 字段' 而非 '5 字段' (输出格式段)."""
        # 输出格式段必须说 6 字段
        self._for_each(lambda n, s: "严格按此 6 字段" in s, "缺 '严格按此 6 字段'")
        # 老 5 字段声明必须清除
        self._for_each(lambda n, s: "严格按此 5 字段" not in s, "残留 '严格按此 5 字段'")

    def test_parse_function_renamed_to_6field(self):
        """parse_5field_output → parse_6field_output 重命名."""
        self._for_each(lambda n, s: "def parse_6field_output" in s,
                       "缺 def parse_6field_output")
        # 老函数定义必须清除
        self._for_each(lambda n, s: "def parse_5field_output" not in s,
                       "残留 def parse_5field_output")

    def test_parse_function_has_alignment_field(self):
        """parse_6field_output 内必须含 'alignment' 字段定义."""
        self._for_each(lambda n, s: "'alignment': ''" in s,
                       "parser 缺 'alignment': '' 字段")
        self._for_each(lambda n, s: "current_field = 'alignment'" in s,
                       "parser 缺 current_field = 'alignment' 切换")

    def test_parse_function_has_variation_selector_fallback(self):
        """🎚 (no variation selector) fallback 必须存在 (V37.9.50 同款模式)."""
        # 既要有带 variation selector 的 🎚️ 也要有不带的 🎚 (作 fallback)
        self._for_each(lambda n, s: "🎚️" in s and "🎚" in s,
                       "parser 缺 🎚 fallback (no variation selector U+FE0F)")

    def test_emit_calls_parse_6field(self):
        """emit 必须调用 parse_6field_output (不是老 parse_5field)."""
        # 函数调用 parse_6field_output(result.get(
        self._for_each(lambda n, s: "parse_6field_output(result.get" in s,
                       "emit 缺 parse_6field_output 调用")
        # 老调用必须清除
        self._for_each(lambda n, s: "parse_5field_output(result.get" not in s,
                       "emit 残留 parse_5field_output 调用")

    def test_emit_displays_alignment(self):
        """emit 必须输出 🎚️ 项目对齐度 行 + 访问 fields['alignment']."""
        self._for_each(lambda n, s: "🎚️ 项目对齐度" in s,
                       "emit 缺 🎚️ 项目对齐度 字面量")
        self._for_each(lambda n, s: "fields['alignment']" in s,
                       "emit 缺 fields['alignment'] 访问")

    def test_emit_has_high_alignment_count(self):
        """emit 必须维护 high_alignment_count 统计 + 末尾汇总行."""
        self._for_each(lambda n, s: "high_alignment_count" in s,
                       "emit 缺 high_alignment_count 计数器")
        # 末尾汇总必须含 '⭐≥4' (中文星级 + ASCII >=)
        self._for_each(lambda n, s: "⭐≥4" in s,
                       "emit 缺 ⭐≥4 高对齐汇总行")
        # 汇总必须明确指出是"项目对齐度"
        self._for_each(lambda n, s: "(项目对齐度 ⭐≥4)" in s,
                       "emit 汇总不含 '(项目对齐度 ⭐≥4)' 字面量")

    def test_lazy_imports_project_alignment_scorer(self):
        """V37.9.51 必须 lazy import project_alignment_scorer (FAIL-OPEN)."""
        # 4 个 helper 都要 import
        for fn in ("load_project_concepts", "validate_alignment_score",
                   "extract_star_count", "format_validation_marker"):
            with self.subTest(helper=fn):
                self._for_each(lambda n, s, f=fn: f in s,
                               f"缺 lazy import helper '{fn}'")
        # 必须有 from project_alignment_scorer import
        self._for_each(lambda n, s: "from project_alignment_scorer import" in s,
                       "缺 from project_alignment_scorer import 语句")

    def test_v37_9_50_hotfix_emit_heredoc_imports_os(self):
        """V37.9.50-hotfix: emit heredoc 顶部必须 import os (lazy import 用 os.environ/os.path).

        V37.9.50-hotfix 教训: 缺 os import 会让 lazy import 抛 NameError → rule_check 静默跳过.
        每个脚本的 emit heredoc (写 MSG_FILE 的那个) 必须含 V37.9.51 注释 + os import.

        注意: 其他 heredoc (HTML 解析 / single prompt 构建 / chunk 切片) 不需要 os, 不在本测试范围.
        """
        for name, rel_path in SUB_STAGE_4B_SCRIPTS:
            with self.subTest(script=name):
                src = _read(os.path.join(REPO_ROOT, rel_path))
                # V37.9.51 标准模式: emit heredoc 顶部含独有 marker
                # 'import sys, json, re, os  # V37.9.51: os 用于 lazy import project_alignment_scorer'
                # 用这个独有 marker 字面量精确锁定 emit heredoc 的 import 行
                expected_pattern = "import sys, json, re, os  # V37.9.51: os 用于 lazy import project_alignment_scorer"
                self.assertIn(expected_pattern, src,
                              f"{name}: emit heredoc 顶部缺 'import sys, json, re, os # V37.9.51' "
                              f"(V37.9.50-hotfix 防 NameError 模式)")

    def test_rule_check_call_structure(self):
        """rule_check 必须 (a) 检查 4 个 helper 都加载 (b) try/except 包装."""
        # 4 个 helper 加载守卫
        self._for_each(
            lambda n, s: "if _validate_alignment_score and _concepts" in s,
            "缺 4 helper 加载守卫 (V37.9.51)"
        )
        # FAIL-OPEN try/except 关键字 (每个脚本独立 log prefix)
        self._for_each(
            lambda n, s: "V37.9.51 rule_check 失败" in s,
            "缺 V37.9.51 rule_check FAIL-OPEN log 字面量"
        )

    def test_rule_check_uses_marker_for_invalid(self):
        """validation invalid → format_validation_marker → ⚠️ 标记加入推送."""
        self._for_each(
            lambda n, s: "_format_validation_marker(validation)" in s,
            "缺 _format_validation_marker(validation) 调用"
        )
        self._for_each(lambda n, s: "if marker:" in s, "缺 if marker: 守卫")

    def test_high_alignment_threshold_is_4(self):
        """high_alignment_count 累加门槛必须是 llm_stars >= 4 (不能改成 3)."""
        self._for_each(
            lambda n, s: "if llm_stars >= 4:" in s,
            "缺 llm_stars >= 4 门槛守卫"
        )

    def test_no_v37_9_36_placeholder_fallback(self):
        """严禁 V37.9.36 占位符反模式回归 (line-by-line 跳过注释)."""
        forbidden = [
            "贡献：AI领域相关研究",
            "要点：技术深度文章",
        ]
        for name, rel_path in SUB_STAGE_4B_SCRIPTS:
            with self.subTest(script=name):
                src = _read(os.path.join(REPO_ROOT, rel_path))
                for line in src.split("\n"):
                    stripped = line.strip()
                    if stripped.startswith("#"):
                        continue  # skip comments
                    for pat in forbidden:
                        self.assertNotIn(pat, stripped,
                                         f"{name}: V37.9.36 占位符反模式回归: {pat!r}")


# ── Test 2: TestV37951PerScriptRuleContent ─────────────────────────
class TestV37951PerScriptRuleContent(unittest.TestCase):
    """每个脚本独立的 rule_content 拼接 (适配各 domain)."""

    def test_rss_blogs_uses_title_plus_description(self):
        """rss_blogs: rule_content = title + description (blog 无 abstract 用 description)."""
        src = _read(os.path.join(REPO_ROOT, "jobs/rss_blogs/run_rss_blogs.sh"))
        # rule_content 拼接 article.get('title', '') + article.get('description')
        self.assertIn("article.get('title', '') + ' ' + (article.get('description')", src)

    def test_dblp_uses_title_plus_venue(self):
        """dblp: rule_content = title + venue (DBLP 无 abstract 用 venue 元数据)."""
        src = _read(os.path.join(REPO_ROOT, "jobs/dblp/run_dblp.sh"))
        self.assertIn("paper.get('title', '') + ' ' + paper.get('venue', '')", src)

    def test_arxiv_uses_title_plus_abstract(self):
        """arxiv_monitor: rule_content = title + abstract (V37.9.43 同款 fallback 数据源)."""
        src = _read(os.path.join(REPO_ROOT, "jobs/arxiv_monitor/run_arxiv.sh"))
        self.assertIn("paper.get('title', '') + ' ' + paper.get('abstract', '')", src)

    def test_github_uses_full_name_plus_description_plus_topics(self):
        """github_trending: rule_content = full_name + description + topics (repo 元数据)."""
        src = _read(os.path.join(REPO_ROOT, "jobs/github_trending/run_github_trending.sh"))
        # full_name 是 keyword, description optional, topics is list
        self.assertIn("repo.get('full_name', '')", src)
        self.assertIn("repo.get('description')", src)
        self.assertIn("repo.get('topics', [])", src)

    def test_ai_leaders_uses_author_plus_text(self):
        """ai_leaders_x: rule_content = author + text (tweet 上下文)."""
        src = _read(os.path.join(REPO_ROOT, "jobs/ai_leaders_x/run_ai_leaders_x.sh"))
        self.assertIn("tweet.get('author', '') + ' ' + tweet.get('text', '')", src)

    def test_hn_uses_title_plus_desc_cleaned(self):
        """hn: rule_content = title + desc (HTML 清理后)."""
        src = _read(os.path.join(REPO_ROOT, "run_hn_fixed.sh"))
        # HN desc 含 HTML, 必须 re.sub 清理
        self.assertIn("item.get('title', '')", src)
        # HTML 清理 (V37.9.41 模式)
        self.assertIn("re.sub(r'<[^>]+>'", src)

    def test_all_scripts_use_total_for_alignment_summary(self):
        """每个脚本末尾用 total_<domain> 变量做高对齐汇总分母."""
        # 不同脚本用不同变量名 (total_articles / total_papers / total_repos / total_tweets / total_items)
        expected = {
            "jobs/rss_blogs/run_rss_blogs.sh": "total_articles",
            "jobs/dblp/run_dblp.sh": "total_papers",
            "jobs/arxiv_monitor/run_arxiv.sh": "total_papers",
            "jobs/github_trending/run_github_trending.sh": "total_repos",
            "jobs/ai_leaders_x/run_ai_leaders_x.sh": "total_tweets",
            "run_hn_fixed.sh": "total_items",
        }
        for rel_path, var in expected.items():
            with self.subTest(script=rel_path, var=var):
                src = _read(os.path.join(REPO_ROOT, rel_path))
                self.assertIn(f"{var} = ", src,
                              f"{rel_path}: 缺 {var} 变量定义")


# ── Test 3: TestV37951AlignedScriptsAudit ──────────────────────────
class TestV37951AlignedScriptsAudit(unittest.TestCase):
    """ontology/llm_cron_audit.py ALIGNED_SCRIPTS 集成 + 端到端 audit_script 对齐."""

    @classmethod
    def setUpClass(cls):
        # Lazy import audit module (deferred to test runtime)
        sys.path.insert(0, REPO_ROOT)
        sys.path.insert(0, os.path.join(REPO_ROOT, "ontology"))
        import llm_cron_audit
        cls.audit = llm_cron_audit

    def test_aligned_scripts_contains_v37_9_51_anchor(self):
        """V37.9.51 marker 必须出现在 ALIGNED_SCRIPTS values."""
        versions = list(self.audit.ALIGNED_SCRIPTS.values())
        self.assertIn("V37.9.51", versions,
                      "ALIGNED_SCRIPTS 中找不到 V37.9.51 (Sub-Stage 4b)")

    def test_aligned_scripts_count_at_least_11(self):
        """ALIGNED_SCRIPTS 应至少 11 条 (V37.9.50 baseline)."""
        self.assertGreaterEqual(len(self.audit.ALIGNED_SCRIPTS), 11)

    def test_all_6_sub_stage_4b_scripts_aligned_v37_9_51(self):
        """6 个 Sub-Stage 4b 脚本都应在 ALIGNED_SCRIPTS 标记为 V37.9.51."""
        expected_paths = {
            "jobs/rss_blogs/run_rss_blogs.sh",
            "jobs/dblp/run_dblp.sh",
            "jobs/arxiv_monitor/run_arxiv.sh",
            "jobs/github_trending/run_github_trending.sh",
            "jobs/ai_leaders_x/run_ai_leaders_x.sh",
            "run_hn_fixed.sh",
        }
        for path in expected_paths:
            with self.subTest(script=path):
                self.assertIn(path, self.audit.ALIGNED_SCRIPTS,
                              f"{path} 未在 ALIGNED_SCRIPTS 中")
                version = self.audit.ALIGNED_SCRIPTS[path]
                self.assertEqual(version, "V37.9.51",
                                 f"{path}: 期望 V37.9.51, 实际 {version!r}")

    def test_audit_script_marks_6_scripts_as_aligned(self):
        """端到端: 跑 audit_script 6 个脚本应全部 aligned=True + findings=0."""
        scripts = [
            "jobs/rss_blogs/run_rss_blogs.sh",
            "jobs/dblp/run_dblp.sh",
            "jobs/arxiv_monitor/run_arxiv.sh",
            "jobs/github_trending/run_github_trending.sh",
            "jobs/ai_leaders_x/run_ai_leaders_x.sh",
            "run_hn_fixed.sh",
        ]
        for rel in scripts:
            full = os.path.join(REPO_ROOT, rel)
            with self.subTest(script=rel):
                rep = self.audit.audit_script(full)
                self.assertTrue(rep.aligned,
                                f"{rel}: aligned 应为 True, 实际 {rep.aligned}")
                self.assertEqual(rep.aligned_version, "V37.9.51",
                                 f"{rel}: aligned_version 应为 V37.9.51, 实际 {rep.aligned_version!r}")
                # 0 placeholder finding
                self.assertEqual(len(rep.placeholder_findings), 0,
                                 f"{rel}: 含占位符 finding: {rep.placeholder_findings}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
