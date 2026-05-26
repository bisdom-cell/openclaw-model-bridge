#!/usr/bin/env python3
"""test_v37_9_41_hn.py — V37.9.41 HN 5 字段深度迁移守卫

V37.9.41 把 V37.9.36-37 / V37.9.39 / V37.9.40 fail-fast + 5 字段模式横向迁移到:
  - run_hn_fixed.sh (HN 头版精选, 适配 HN posts 上下文 - 摘要常缺失类似 DBLP)

血案防御 (V37.9.36 反模式硬规则保留):
  - HN 老占位符 stars='⭐⭐⭐' / point=title[:40] silent fallback 必须清除
  - 用 [LLM_DEGRADED] 标记 + HN description 兜底替代

HN-specific 适配:
  - 摘要常较短或缺失 → 5 字段 prompt 加 "(基于标题与摘要推断)" caveat (类似 DBLP)
  - HN URL = https://news.ycombinator.com/item?id=N (保留)
  - max 5 items per run (HN-specific 限制)
  - Twitter <think>/ANSI 清理保留 (HN 早期 Qwen3 输出兼容)
"""
import importlib.util
import os
import re
import sys
import unittest


REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
HN_SCRIPT = os.path.join(REPO_ROOT, "jobs", "hn_watcher", "run_hn_fixed.sh")


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


class TestHnV9_41ShellGuards(unittest.TestCase):
    """V37.9.41 HN 脚本 source-level grep 守卫"""

    @classmethod
    def setUpClass(cls):
        cls.src = _read(HN_SCRIPT)

    def test_v37_9_41_marker_present(self):
        self.assertIn("V37.9.41", self.src)

    def test_source_notify_sh_at_top(self):
        self.assertIn("NOTIFY_SH=", self.src)
        self.assertTrue(re.search(r'source\s+"\$NOTIFY_SH"', self.src))

    def test_send_alert_helper_with_system_alert(self):
        self.assertIn("send_alert()", self.src)
        m = re.search(r"send_alert\(\)\s*\{[^}]*\[SYSTEM_ALERT\]", self.src, re.DOTALL)
        self.assertIsNotNone(m)

    def test_llm_three_layer_detection(self):
        self.assertIn("__LLM_HTTP_ERROR__", self.src)
        self.assertIn("__LLM_PARSE_FAIL__", self.src)

    def test_call_llm_single_with_retry_helper(self):
        self.assertIn("call_llm_single_with_retry()", self.src)
        self.assertIn("backoffs=(5 10 20)", self.src)
        self.assertTrue(re.search(r"for\s+attempt\s+in\s+0\s+1\s+2", self.src))

    def test_main_loop_per_item(self):
        self.assertTrue(re.search(r"for\s+\(\(\s*i\s*=\s*0\s*;\s*i\s*<\s*TOTAL_NEW", self.src))

    def test_three_status_levels(self):
        self.assertIn('"status":"llm_failed"', self.src)
        self.assertIn('"status":"partial_degraded"', self.src)
        self.assertIn("all_failed_", self.src)

    def test_llm_failed_branch_exit_1_lock(self):
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

    def test_hn_specific_caveat_inferred_from_title(self):
        """HN 适配: 摘要常缺失, prompt 必须含 (基于标题与摘要推断) caveat"""
        self.assertIn("基于标题与摘要推断", self.src)

    def test_rating_dynamic_length(self):
        self.assertIn("⭐⭐⭐⭐⭐→500-800", self.src)

    def test_multi_window_pattern(self):
        self.assertTrue(re.search(r"TOTAL_LEN.*-le\s+8000", self.src))
        self.assertIn("MAX_CHUNK = 4000", self.src)
        self.assertTrue(re.search(r"sleep\s+1\s*#.*乱序", self.src))
        self.assertIn("(续)", self.src)
        self.assertTrue(re.search(r"💻 HN 头版精选 \[1/", self.src))

    def test_no_v37_9_36_placeholder_pattern(self):
        """V37.9.36 反模式: HN 老 stars='⭐⭐⭐' silent fallback 不得回归"""
        for line_no, line in enumerate(self.src.splitlines(), start=1):
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue
            # 老 fallback 模式 stars = '⭐⭐⭐' 或 stars = "⭐⭐⭐"
            if re.search(r"""stars\s*=\s*['"]⭐⭐⭐['"]""", line):
                self.fail(
                    f"L{line_no} HN 老占位符 stars='⭐⭐⭐' 必须已清除: {line.strip()!r}"
                )
            # 老 fallback 模式 point = title[:40]
            if re.search(r"""point\s*=\s*[^=]*title.*\[:40\]""", line):
                self.fail(
                    f"L{line_no} HN 老占位符 point=title[:40] 必须已清除: {line.strip()!r}"
                )

    def test_no_legacy_4_field_emit(self):
        """V37.8 老 4 行 emit (zh_title/链接/要点/价值) 已替换为 5 字段"""
        # 老 emit: f.write(f"{zh_title}\n链接：{hn_url}\n要点：{point}\n价值：{stars}\n\n")
        # V37.9.41 emit 用 *{title_display}* + 链接行 + 5 emoji 字段
        self.assertNotIn(r"f.write(f\"{zh_title}\n链接：", self.src)


class TestHnLlmDegradedFallback(unittest.TestCase):
    """V37.9.41 HN LLM_DEGRADED 兜底逻辑"""

    @classmethod
    def setUpClass(cls):
        cls.src = _read(HN_SCRIPT)

    def test_degraded_uses_hn_description(self):
        """HN LLM_DEGRADED 必须用 desc 兜底 (而非占位符)"""
        # 检查 emit 中 LLM_DEGRADED 块包含 desc fallback
        self.assertIn("⚠️ [LLM_DEGRADED] 深度分析失败, 原文摘要供参考:", self.src)
        # desc 提取 + HTML 清理在兜底里
        idx = self.src.find("[LLM_DEGRADED] 深度分析失败")
        self.assertGreater(idx, 0)
        # 80 行内必须包含 desc fallback 逻辑
        chunk = self.src[idx:idx+800]
        self.assertIn("item.get('desc'", chunk)

    def test_degraded_explicit_no_data_message(self):
        """HN 无 desc 时显式说明"""
        self.assertIn("(HN 无摘要数据, 请直接点链接阅读)", self.src)


class TestHnInAuditAligned(unittest.TestCase):
    """V37.9.41 HN 必须被 audit 识别为 aligned"""

    def setUp(self):
        spec = importlib.util.spec_from_file_location(
            "_au_v9_41", os.path.join(REPO_ROOT, "ontology", "llm_cron_audit.py"))
        self.au = importlib.util.module_from_spec(spec)
        sys.modules["_au_v9_41"] = self.au
        spec.loader.exec_module(self.au)

    def test_hn_in_aligned_with_v37_9_41_or_later(self):
        """HN 必须在 ALIGNED_SCRIPTS, V37.9.41 (原) 或 V37.9.51 (Sub-Stage 4b 升级)"""
        self.assertIn("jobs/hn_watcher/run_hn_fixed.sh", self.au.ALIGNED_SCRIPTS)
        version = self.au.ALIGNED_SCRIPTS["jobs/hn_watcher/run_hn_fixed.sh"]
        self.assertIn(version, ("V37.9.41", "V37.9.51"),
                      f"HN 应映射 V37.9.41 或 V37.9.51, 实际 {version!r}")

    def test_aligned_scripts_count_at_least_8(self):
        """V37.9.41 后 ALIGNED_SCRIPTS ≥8 (V37.9.40 7 + HN)"""
        self.assertGreaterEqual(len(self.au.ALIGNED_SCRIPTS), 8)

    def test_audit_hn_aligned_True(self):
        rep = self.au.audit_script(HN_SCRIPT)
        self.assertTrue(rep.exists)
        self.assertTrue(rep.aligned, msg=f"HN 应识别为 aligned, score {rep.compliance_score}")
        # V37.9.51 兼容: HN 从 V37.9.41 升级到 V37.9.51 (Sub-Stage 4b)
        self.assertIn(rep.aligned_version, ("V37.9.41", "V37.9.51"),
                      f"aligned_version 应为 V37.9.41 或 V37.9.51, 实际 {rep.aligned_version!r}")
        self.assertEqual(len(rep.placeholder_findings), 0)


if __name__ == "__main__":
    unittest.main()
