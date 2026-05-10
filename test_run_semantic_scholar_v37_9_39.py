#!/usr/bin/env python3
"""test_run_semantic_scholar_v37_9_39.py — V37.9.39 S2 fail-fast + 5 字段深度迁移守卫单测

V37.9.39 把 V37.9.36-37 rss_blogs 完整模式横向迁移到 semantic_scholar:
  - source notify.sh + send_alert helper (统一 [SYSTEM_ALERT] 通道)
  - LLM 三层检测 (HTTP error / JSON parse fail / empty content)
  - call_llm_single_with_retry helper (5/10/20s 指数退避 × 3)
  - 5 字段深度: 📌中文标题 / 🔑核心贡献 / 💡关键方法 / 🎯实践启发 / ⭐评级
  - 按评级动态调长度: ⭐⭐⭐→100-150 / ⭐⭐⭐⭐→250-400 / ⭐⭐⭐⭐⭐→500-800 字
  - 三档失败语义: ok / partial_degraded (LLM_DEGRADED + 摘要 fallback) / llm_failed (exit 1)
  - 多窗口切片 (>8000 字, [i/N] 标识 + sleep 1s 防乱序)

血案防御:
  V37.9.36 反模式硬规则保留 — 占位符 fallback (`贡献：AI领域相关研究` /
  `价值：⭐⭐⭐`) 严禁回归。LLM_DEGRADED 标记 + S2 摘要 (tldr 优先, 否则 abstract)
  作为部分失败的最小用户保障, 但绝不伪造完整 LLM 输出。

用户视角原则 #13 第 10 次正向兑现:
  V37.9.38 audit 工具识别 S2 为 P1 (3 findings) → 用户 5/8 看到今日 S2 推送
  深度不足 → V37.9.39 立即应用 V37.9.36-37 rss_blogs 模式。
"""
import importlib.util
import os
import re
import sys
import unittest


REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
S2_SCRIPT = os.path.join(REPO_ROOT, "jobs/semantic_scholar/run_semantic_scholar.sh")
RSS_SCRIPT = os.path.join(REPO_ROOT, "jobs/rss_blogs/run_rss_blogs.sh")


def _read_script(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


class TestS2ShellGuardsV9_39(unittest.TestCase):
    """V37.9.39 S2 脚本 source-level grep 守卫 — 防未来重构反向回退"""

    @classmethod
    def setUpClass(cls):
        cls.src = _read_script(S2_SCRIPT)

    # ── V37.9.39 marker + helper 定义 ────────────────────────────────
    def test_v37_9_39_marker_present(self):
        self.assertIn("V37.9.39", self.src)

    def test_source_notify_sh_at_top(self):
        """V37.9.36 同款: source notify.sh 让 send_alert 走统一通道"""
        self.assertIn("NOTIFY_SH=", self.src)
        self.assertTrue(re.search(r"source\s+\"\$NOTIFY_SH\"", self.src),
                        msg="必须 source notify.sh")

    def test_send_alert_helper_defined(self):
        self.assertIn("send_alert()", self.src)

    def test_system_alert_prefix_in_send_alert(self):
        """V37.4.3 PA 上下文隔离: [SYSTEM_ALERT] 前缀"""
        m = re.search(r"send_alert\(\)\s*\{[^}]*\[SYSTEM_ALERT\]", self.src, re.DOTALL)
        self.assertIsNotNone(m, msg="send_alert 内必须含 [SYSTEM_ALERT] 前缀")

    # ── LLM 三层检测 markers ────────────────────────────────────────
    def test_llm_http_error_marker(self):
        self.assertIn("__LLM_HTTP_ERROR__", self.src)

    def test_llm_parse_fail_marker(self):
        self.assertIn("__LLM_PARSE_FAIL__", self.src)

    # ── retry helper 契约 ───────────────────────────────────────────
    def test_call_llm_single_with_retry_helper(self):
        """V37.9.37 同款: per-paper retry helper"""
        self.assertIn("call_llm_single_with_retry()", self.src)

    def test_retry_backoffs_5_10_20(self):
        """退避序列必须严格 5/10/20s (V37.9.37 契约)"""
        self.assertIn("backoffs=(5 10 20)", self.src)

    def test_retry_loop_3_attempts(self):
        """主循环 3 次 attempt (0 1 2)"""
        self.assertTrue(re.search(r"for\s+attempt\s+in\s+0\s+1\s+2", self.src),
                        msg="必须用 for attempt in 0 1 2 (V37.9.37 契约)")

    def test_main_loop_per_paper_iteration(self):
        """主循环 for ((i=0; i<TOTAL_NEW; i++)) 每篇独立调 LLM"""
        self.assertTrue(re.search(r"for\s+\(\(\s*i\s*=\s*0\s*;\s*i\s*<\s*TOTAL_NEW", self.src),
                        msg="主循环必须 per-paper iteration")

    # ── 失败语义三档 + status_file ──────────────────────────────────
    def test_status_llm_failed_full_failure(self):
        self.assertIn('"status":"llm_failed"', self.src)

    def test_status_partial_degraded(self):
        self.assertIn('"status":"partial_degraded"', self.src)

    def test_all_failed_prefix(self):
        """全部失败 reason 含 all_failed_ 前缀"""
        self.assertIn("all_failed_", self.src)

    def test_full_failure_branch_exits_1(self):
        """全部失败分支 500 字符内必须 exit 1 (fail-fast 顺序锁)"""
        idx = self.src.find('"status":"llm_failed"')
        self.assertGreater(idx, 0)
        exit_idx = self.src.find("exit 1", idx)
        self.assertGreater(exit_idx, 0, msg="llm_failed 分支后未找到 exit 1")
        gap = exit_idx - idx
        self.assertLess(gap, 500,
                        msg=f"llm_failed 分支必须立即 fail-fast exit 1, 距离 {gap} 字符")

    # ── LLM_DEGRADED 标记 (替代 V37.9.36 占位符) ────────────────────
    def test_llm_degraded_marker(self):
        self.assertIn("[LLM_DEGRADED]", self.src)

    def test_llm_degraded_with_explicit_fallback_text(self):
        """LLM_DEGRADED 上下文必须显式说明用 S2 摘要兜底"""
        self.assertIn("[LLM_DEGRADED] 深度分析失败", self.src)

    # ── 5 字段 prompt 完整性 (📌🔑💡🎯⭐) ───────────────────────────
    def test_field_marker_title(self):
        self.assertIn("📌", self.src)

    def test_field_marker_contribution(self):
        self.assertIn("🔑", self.src)

    def test_field_marker_method(self):
        self.assertIn("💡", self.src)

    def test_field_marker_practice(self):
        self.assertIn("🎯", self.src)

    def test_field_marker_rating(self):
        self.assertIn("⭐", self.src)

    def test_anti_hallucination_guard(self):
        """V37.8.6 同款: 严禁虚构 anti-hallucination 守卫"""
        self.assertIn("严禁虚构", self.src)

    def test_rating_dynamic_length(self):
        """评级动态长度规则 (⭐⭐⭐⭐⭐→500-800)"""
        self.assertIn("⭐⭐⭐⭐⭐→500-800", self.src)

    # ── 多窗口切片 (>8000 字触发) ──────────────────────────────────
    def test_multi_window_threshold_8000(self):
        """单段直发阈值 ≤8000, 超过触发多窗口切片"""
        self.assertTrue(re.search(r"TOTAL_LEN.*-le\s+8000", self.src),
                        msg="单段直发阈值必须 ≤8000")

    def test_max_chunk_4000(self):
        """每段切片上限 4000 字 (V37.9.21 契约)"""
        self.assertIn("MAX_CHUNK = 4000", self.src)

    def test_multi_window_sleep_1s(self):
        """多窗口段间 sleep 1 防 WhatsApp 消息乱序"""
        self.assertTrue(re.search(r"sleep\s+1\s*#.*乱序", self.src),
                        msg="多窗口段间必须 sleep 1 防乱序")

    def test_multi_window_part_indicator(self):
        """[i/N] 标识让用户知道是连续段"""
        self.assertIn("[1/", self.src)
        self.assertTrue(re.search(r"\[\{i\+1\}/\{total_parts\}\]", self.src),
                        msg="多窗口必须含 [i/N] 标识")

    def test_multi_window_continued_marker(self):
        """续段必须含 (续) 标识"""
        self.assertIn("(续)", self.src)

    # ── V37.9.36 反模式硬规则 (源码级守卫) ─────────────────────────
    def test_no_placeholder_fallback_text(self):
        """V37.9.36 反模式禁止: 占位符字面量 不得在执行代码中出现 (跳过 # 注释)"""
        forbidden = ["贡献：AI领域相关研究", "价值：⭐⭐⭐"]
        for line_no, line in enumerate(self.src.splitlines(), start=1):
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue
            for s in forbidden:
                if s in line:
                    # 唯一允许: 在 LLM prompt template 内 (识别 prompt context)
                    # V37.9.39 prompt 不再含这些字面量, 任何出现都是反模式
                    self.fail(
                        f"L{line_no} 含 V37.9.36 反模式占位符 '{s}': {line.strip()!r}"
                    )

    def test_no_legacy_three_field_prompt_strings(self):
        """V37.8 老 3 字段 prompt 残留不应再出现"""
        self.assertNotIn("第一行：中文标题", self.src,
                         msg="V37.8 老 3 字段 prompt 应已被 V37.9.39 替换")
        self.assertNotIn("严格输出三行", self.src,
                         msg="V37.8 老 3 字段 prompt 应已被 V37.9.39 替换")


class TestEmit5FieldParser(unittest.TestCase):
    """V37.9.39 5 字段 emit parser 行为测试 — 从 shell heredoc 提取 Python 函数测试"""

    @classmethod
    def setUpClass(cls):
        cls.src = _read_script(S2_SCRIPT)

    def test_parse_5field_output_function_defined(self):
        """parse_5field_output 或 parse_6field_output 函数定义存在 (V37.9.50 升级支持 6 字段)."""
        # V37.9.50 alternation: 接受老 5 字段或新 6 字段实现
        has_5 = "def parse_5field_output(content):" in self.src
        has_6 = "def parse_6field_output(content):" in self.src
        self.assertTrue(has_5 or has_6,
                        "parse_5field_output 或 parse_6field_output 必须定义其一")

    def test_parse_returns_dict_with_all_5_keys(self):
        """parse_*field_output 返回 dict 必须含 5 字段 key (V37.9.50 加 alignment 第 6 个)."""
        # V37.9.50 alternation: 同时接受 5/6 字段函数定义
        pattern = re.compile(
            r"def parse_[56]field_output\(content\):.*?return fields",
            re.DOTALL,
        )
        m = pattern.search(self.src)
        self.assertIsNotNone(m, msg="parse_5field_output 或 parse_6field_output 函数体未找到")
        # 验证 fields dict 含原 5 个 key (V37.9.50 不删字段, 只加 alignment)
        body = m.group(0)
        for key in ("'cn_title'", "'highlights'", "'insight'", "'practice'", "'rating'"):
            self.assertIn(key, body, msg=f"parse 返回 dict 缺少 {key}")


class TestActualBloodLessonScenarioRegression(unittest.TestCase):
    """V37.9.36 血案场景源码级反向验证 — 删除任何关键守卫立即失败"""

    @classmethod
    def setUpClass(cls):
        cls.src = _read_script(S2_SCRIPT)

    def test_blood_lesson_v37_9_36_pattern_fully_removed(self):
        """V37.9.36 血案模式 (silent placeholder fallback) 已完全清除"""
        # 反模式 1: contrib = "贡献：AI领域相关研究"
        self.assertFalse(
            re.search(r"contrib\s*=\s*[\"']贡献：AI领域相关研究", self.src),
            msg="V37.9.36 反模式 'contrib = 贡献：AI领域相关研究' 必须已清除"
        )
        # 反模式 2: stars = "价值：⭐⭐⭐"
        self.assertFalse(
            re.search(r"stars\s*=\s*[\"']价值：⭐⭐⭐", self.src),
            msg="V37.9.36 反模式 'stars = 价值：⭐⭐⭐' 必须已清除"
        )
        # 反模式 3: pending_contrib or '贡献：AI领域相关研究'
        self.assertFalse(
            re.search(r"or\s+'贡献：AI领域相关研究'", self.src),
            msg="V37.9.36 反模式 fallback expression 必须已清除"
        )

    def test_v37_9_39_fallback_uses_explicit_degraded_marker(self):
        """V37.9.39 失败 fallback 必须是 [LLM_DEGRADED] + S2 摘要而非占位符"""
        self.assertIn("[LLM_DEGRADED] 深度分析失败", self.src)
        # 必须有 tldr 或 abstract 兜底逻辑
        self.assertIn("paper.get('tldr')", self.src)
        self.assertIn("paper.get('abstract'", self.src)


class TestS2InAuditAlignedScripts(unittest.TestCase):
    """V37.9.39 S2 必须被 ontology/llm_cron_audit.py 识别为已对齐"""

    def setUp(self):
        spec = importlib.util.spec_from_file_location(
            "_au_v9_39",
            os.path.join(REPO_ROOT, "ontology", "llm_cron_audit.py"),
        )
        self.au = importlib.util.module_from_spec(spec)
        sys.modules["_au_v9_39"] = self.au
        spec.loader.exec_module(self.au)

    def test_s2_is_in_aligned_scripts_constant(self):
        """ALIGNED_SCRIPTS 必须含 S2 锚点 (V37.9.50 升级后接受 V37.9.39 或 V37.9.50)."""
        self.assertIn(
            "jobs/semantic_scholar/run_semantic_scholar.sh",
            self.au.ALIGNED_SCRIPTS,
        )
        # V37.9.50 alternation: 接受 V37.9.39 (老 5 字段) 或 V37.9.50 (升级 6 字段)
        version = self.au.ALIGNED_SCRIPTS["jobs/semantic_scholar/run_semantic_scholar.sh"]
        self.assertIn(version, ("V37.9.39", "V37.9.50"),
                      msg=f"S2 version 期望 V37.9.39 或 V37.9.50, 实际 {version}")

    def test_aligned_scripts_count_5(self):
        """V37.9.39 后 ALIGNED_SCRIPTS 必须从 4 升到 5"""
        self.assertGreaterEqual(len(self.au.ALIGNED_SCRIPTS), 5,
                                msg="V37.9.39 后 ALIGNED_SCRIPTS 应 ≥5")

    def test_audit_script_recognizes_s2_aligned(self):
        """audit_script(S2 path) 必须返回 aligned=True + version V37.9.39 或 V37.9.50."""
        rep = self.au.audit_script(S2_SCRIPT)
        self.assertTrue(rep.exists)
        self.assertTrue(rep.aligned, msg=f"S2 应识别为 aligned, 但 score={rep.compliance_score}, findings={len(rep.placeholder_findings)}")
        # V37.9.50 alternation
        self.assertIn(rep.aligned_version, ("V37.9.39", "V37.9.50"),
                      msg=f"S2 version 期望 V37.9.39 或 V37.9.50, 实际 {rep.aligned_version}")
        # 占位符 finding 必须为 0
        self.assertEqual(len(rep.placeholder_findings), 0,
                         msg=f"S2 placeholder findings 应为 0, 实际: {[f.matched for f in rep.placeholder_findings]}")


if __name__ == "__main__":
    unittest.main()
