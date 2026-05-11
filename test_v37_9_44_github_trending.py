#!/usr/bin/env python3
"""test_v37_9_44_github_trending.py — V37.9.44 github_trending 5 字段深度迁移守卫

V37.9.44 把 V37.9.36-37 / V37.9.39 / V37.9.40 / V37.9.41 / V37.9.43 fail-fast +
5 字段模式横向迁移到:
  - jobs/github_trending/run_github_trending.sh (GitHub Trending ML/AI 仓库, 每天 14:00 HKT)

血案防御 (V37.9.36 反模式硬规则保留):
  - V37.8 老 3 字段 fallback 占位符 (亮点：AI/ML相关项目 / 推荐：⭐⭐⭐) 必须清除
  - 用 [LLM_DEGRADED] 标记 + GitHub repo description 兜底替代

github_trending-specific 适配:
  - URL = html_url (https://github.com/{owner}/{repo}, 保留)
  - MAX_REPOS = 10 (保留)
  - GitHub API 返回完整 description (LLM_DEGRADED fallback 用 description[:300])
  - 5 字段 prompt 适配项目场景: 📌 中文项目名 / 🔑 核心功能 / 💡 技术亮点 /
    🎯 实践启发 / ⭐ 评级
  - 按评级动态调长度 (⭐⭐⭐→100-150 / ⭐⭐⭐⭐→250-400 / ⭐⭐⭐⭐⭐→500-800)
  - Discord 频道: DISCORD_CH_TECH (vs S2/arxiv 用 DISCORD_CH_PAPERS)
  - 仓库 metadata 显示: ⭐ stars / 主语言 / 创建日期 / topics

audit 视角对齐 (V37.9.38 INV-LLMCRON-AUDIT-001):
  - ALIGNED_SCRIPTS 字典追加 'jobs/github_trending/run_github_trending.sh': 'V37.9.44'
  - placeholder_findings 必须为 0
  - SYSTEM_ALERT / source_notify / send_alert / status:llm_failed 标志 ✓
"""
import importlib.util
import os
import re
import sys
import unittest


REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
GH_SCRIPT = os.path.join(REPO_ROOT, "jobs", "github_trending", "run_github_trending.sh")


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


class TestGhTrendingV9_44ShellGuards(unittest.TestCase):
    """V37.9.44 github_trending 脚本 source-level grep 守卫"""

    @classmethod
    def setUpClass(cls):
        cls.src = _read(GH_SCRIPT)

    def test_v37_9_44_marker_present(self):
        self.assertIn("V37.9.44", self.src)

    def test_source_notify_sh_at_top(self):
        self.assertIn("NOTIFY_SH=", self.src)
        self.assertTrue(re.search(r'source\s+"\$NOTIFY_SH"', self.src))

    def test_send_alert_helper_with_system_alert(self):
        self.assertIn("send_alert()", self.src)
        m = re.search(r"send_alert\(\)\s*\{[^}]*\[SYSTEM_ALERT\]", self.src, re.DOTALL)
        self.assertIsNotNone(m)
        self.assertIn("[SYSTEM_ALERT] github_trending", self.src)

    def test_llm_three_layer_detection(self):
        self.assertIn("__LLM_HTTP_ERROR__", self.src)
        self.assertIn("__LLM_PARSE_FAIL__", self.src)

    def test_call_llm_single_with_retry_helper(self):
        self.assertIn("call_llm_single_with_retry()", self.src)
        self.assertIn("backoffs=(5 10 20)", self.src)
        self.assertTrue(re.search(r"for\s+attempt\s+in\s+0\s+1\s+2", self.src))

    def test_main_loop_per_repo(self):
        self.assertTrue(re.search(r"for\s+\(\(\s*i\s*=\s*0\s*;\s*i\s*<\s*TOTAL_NEW", self.src))

    def test_three_status_levels(self):
        self.assertIn('"status":"llm_failed"', self.src)
        self.assertIn('"status":"partial_degraded"', self.src)
        self.assertIn("all_failed_", self.src)

    def test_llm_failed_branch_exit_1_lock(self):
        """V37.9.44 顺序锁: status:llm_failed 写入后必须 500 字符内 exit 1 (fail-fast 契约)"""
        idx = self.src.find('"status":"llm_failed"')
        self.assertGreater(idx, 0)
        exit_idx = self.src.find("exit 1", idx)
        self.assertGreater(exit_idx, 0)
        gap = exit_idx - idx
        self.assertLess(gap, 500)

    def test_llm_degraded_marker(self):
        self.assertIn("[LLM_DEGRADED]", self.src)
        self.assertIn("[LLM_DEGRADED] 深度分析失败", self.src)

    def test_5_field_emoji_set(self):
        for emoji in ("📌", "🔑", "💡", "🎯", "⭐"):
            self.assertIn(emoji, self.src)

    def test_anti_hallucination_guard(self):
        self.assertIn("严禁虚构", self.src)

    def test_rating_dynamic_length(self):
        """V37.9.44 prompt 必须含按评级动态长度"""
        self.assertIn("⭐⭐⭐⭐⭐", self.src)
        self.assertTrue(re.search(r"500\s*-\s*800", self.src))

    def test_multi_window_pattern(self):
        """V37.9.21 多窗口: ≤8000 单段直发, >8000 切片 + sleep 1s + [i/N] 续段"""
        self.assertTrue(re.search(r"TOTAL_LEN.*-le\s+8000", self.src))
        self.assertIn("MAX_CHUNK = 4000", self.src)
        self.assertTrue(re.search(r"sleep\s+1\s*#.*乱序", self.src))
        self.assertIn("(续)", self.src)
        # 多窗口 header 含 [1/N]
        self.assertTrue(re.search(r"GitHub 热门 AI/ML 仓库 \[1/", self.src))

    def test_no_placeholder_fallback_text(self):
        """V37.9.36 反模式: 老 3 字段占位符 (亮点：AI/ML相关项目 / 推荐：⭐⭐⭐) 必须清除"""
        for line_no, line in enumerate(self.src.splitlines(), start=1):
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue
            # 老 fallback: highlight = '亮点：AI/ML相关项目'
            if "亮点：AI/ML相关项目" in line:
                self.fail(
                    f"L{line_no} V37.9.36 占位符 '亮点：AI/ML相关项目' 必须已清除: {line.strip()!r}"
                )
            # 老 fallback: rec_stars = '推荐：⭐⭐⭐'
            if re.search(r"""推荐：⭐⭐⭐(?:["']|$)""", line):
                self.fail(
                    f"L{line_no} V37.9.36 占位符 '推荐：⭐⭐⭐' 必须已清除: {line.strip()!r}"
                )

    def test_no_legacy_3_field_emit(self):
        """V37.8 老 3 字段 emit (cn_name/亮点/推荐) 已替换为 5 字段"""
        # 老 emit 模式: msg_lines.append(rec_stars) 配合 highlight + cn_name 严格 5 行 block
        self.assertNotIn(
            "msg_lines.append(rec_stars)", self.src,
            msg="V37.8 老 emit 'msg_lines.append(rec_stars)' 必须已清除"
        )

    def test_discord_target_is_tech(self):
        """github_trending Discord 频道是 TECH 不是 PAPERS (技术 vs 论文区分)"""
        # 推送区使用 DISCORD_CH_TECH
        self.assertIn("DISCORD_CH_TECH", self.src)
        # 不能在推送时用 DISCORD_CH_PAPERS (那是论文场景)
        for line_no, line in enumerate(self.src.splitlines(), start=1):
            if "DISCORD_CH_PAPERS" in line and "discord" in line.lower():
                self.fail(
                    f"L{line_no}: github_trending 不应使用 DISCORD_CH_PAPERS (应用 TECH): {line.strip()!r}"
                )


class TestGhTrendingLlmDegradedFallback(unittest.TestCase):
    """V37.9.44 github_trending LLM_DEGRADED 兜底逻辑"""

    @classmethod
    def setUpClass(cls):
        cls.src = _read(GH_SCRIPT)

    def test_degraded_uses_github_description(self):
        """github_trending LLM_DEGRADED 必须用 description 兜底 (而非占位符)"""
        self.assertIn("⚠️ [LLM_DEGRADED] 深度分析失败, 仓库描述供参考:", self.src)
        idx = self.src.find("[LLM_DEGRADED] 深度分析失败")
        self.assertGreater(idx, 0)
        chunk = self.src[idx:idx+800]
        # description 提取在兜底里
        self.assertIn("repo.get('description'", chunk)

    def test_degraded_explicit_no_data_message(self):
        """github 无 description 时显式说明 (引导用户看 README)"""
        self.assertIn("(GitHub 无描述数据, 请直接点链接阅读 README)", self.src)

    def test_repo_url_format_preserved(self):
        """GitHub URL 格式必须保留 html_url (https://github.com/{owner}/{repo})"""
        self.assertIn("html_url", self.src)
        # emit 中用 html_url 不能用硬编码 URL — assert 至少有一处从 repo dict 取 html_url
        # 接受 repo['html_url'] / repo.get('html_url') / 直接 html_url 变量赋值
        self.assertTrue(
            re.search(r"repo\['html_url'\]|repo\.get\('html_url'", self.src),
            msg="emit 必须从 repo dict 提取 html_url, 不能硬编码 URL"
        )

    def test_repo_metadata_in_emit(self):
        """LLM_DEGRADED + 正常 emit 都必须显示 repo metadata (stars/lang/created)"""
        # badge_parts 含 stars + lang + created
        self.assertIn("badge_parts = [f", self.src)
        self.assertIn("'language'", self.src)
        self.assertIn("'created'", self.src)


class TestGhTrendingInAuditAligned(unittest.TestCase):
    """V37.9.44 github_trending 必须被 audit 识别为 aligned"""

    def setUp(self):
        spec = importlib.util.spec_from_file_location(
            "_au_v9_44", os.path.join(REPO_ROOT, "ontology", "llm_cron_audit.py"))
        self.au = importlib.util.module_from_spec(spec)
        sys.modules["_au_v9_44"] = self.au
        spec.loader.exec_module(self.au)

    def test_github_trending_in_aligned_with_v37_9_44_or_later(self):
        """github_trending 必须在 ALIGNED_SCRIPTS, V37.9.44 (原) 或 V37.9.51 (Sub-Stage 4b 升级)"""
        self.assertIn("jobs/github_trending/run_github_trending.sh", self.au.ALIGNED_SCRIPTS)
        version = self.au.ALIGNED_SCRIPTS["jobs/github_trending/run_github_trending.sh"]
        self.assertIn(version, ("V37.9.44", "V37.9.51"),
                      f"github_trending 应映射 V37.9.44 或 V37.9.51, 实际 {version!r}")

    def test_aligned_scripts_count_at_least_10(self):
        """V37.9.44 后 ALIGNED_SCRIPTS ≥10 (V37.9.43 9 + github_trending)"""
        self.assertGreaterEqual(len(self.au.ALIGNED_SCRIPTS), 10)

    def test_audit_github_trending_aligned_True(self):
        rep = self.au.audit_script(GH_SCRIPT)
        self.assertTrue(rep.exists)
        self.assertTrue(
            rep.aligned, msg=f"github_trending 应识别为 aligned, score {rep.compliance_score}"
        )
        # V37.9.51 兼容: github_trending 从 V37.9.44 升级到 V37.9.51 (Sub-Stage 4b)
        self.assertIn(rep.aligned_version, ("V37.9.44", "V37.9.51"),
                      f"aligned_version 应为 V37.9.44 或 V37.9.51, 实际 {rep.aligned_version!r}")
        self.assertEqual(len(rep.placeholder_findings), 0)


if __name__ == "__main__":
    unittest.main()
