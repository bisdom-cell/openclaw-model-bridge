#!/usr/bin/env python3
"""test_v37_9_50_semantic_scholar.py — V37.9.50 Sub-Stage 4b PoC 验证

V37.9.50 = Sub-Stage 4b 1 脚本 PoC 模板验证 (semantic_scholar V37.9.39 5 字段
→ V37.9.50 6 字段 + rule_check, V37.9.45 hf_papers 同款模板横向迁移)

测试类:
  TestV37950ShellGuards (15)              — semantic_scholar 源码级守卫
  TestV37950LlmDegradedFallback (3)       — LLM_DEGRADED 路径不污染高对齐统计
  TestV37950InAuditAligned (3)            — ontology/llm_cron_audit.py ALIGNED_SCRIPTS 集成
"""

import os
import sys
import re
import unittest

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


# ── Test 1: TestV37950ShellGuards (15) ─────────────────────────────
class TestV37950ShellGuards(unittest.TestCase):
    """semantic_scholar V37.9.50 6 字段 PoC 源码守卫."""

    @classmethod
    def setUpClass(cls):
        cls.SRC = _read(os.path.join(
            REPO_ROOT, "jobs/semantic_scholar/run_semantic_scholar.sh"))

    def test_v37_9_50_marker_present(self):
        """V37.9.50 标记存在 (Sub-Stage 4b 模板验证)."""
        self.assertIn("V37.9.50", self.SRC)

    def test_prompt_has_alignment_field(self):
        """prompt 必含 🎚️ 项目对齐度 字段定义."""
        self.assertIn("🎚️ 项目对齐度", self.SRC)
        self.assertIn("一句话原因", self.SRC)
        self.assertIn("≤ 30 字", self.SRC)

    def test_prompt_has_alignment_scoring_guide(self):
        """prompt 必含 OpenClaw 项目方向 5 档评分指南."""
        self.assertIn("OpenClaw 项目方向", self.SRC)
        self.assertIn("control plane", self.SRC)
        self.assertIn("agent runtime", self.SRC)
        self.assertIn("ontology", self.SRC)
        self.assertIn("memory plane", self.SRC)
        # 5 档评分都必含 (字面量带空格对齐, 用 substring 检查)
        self.assertIn("⭐⭐⭐⭐⭐ = 直接相关", self.SRC)
        self.assertIn("间接相关", self.SRC)
        self.assertIn("一般 AI/ML 趋势", self.SRC)
        self.assertIn("无明显关联", self.SRC)
        self.assertIn("完全无关", self.SRC)

    def test_prompt_has_alignment_anti_hallucination(self):
        """prompt 必含 alignment 反幻觉守卫."""
        self.assertIn("项目对齐度评分必须基于", self.SRC)
        # MR-8 单一真理源 — V37.9.45 hf_papers 同款的"非泛泛 AI 相关"短语
        # 用 source-level grep 守卫确保不被未来重构稀释
        self.assertIn("而非泛泛 AI 相关", self.SRC)

    def test_prompt_uses_6field_not_5field(self):
        """prompt 必须声明 '6 字段' 而非 '5 字段'."""
        # 输出格式段必须说 6 字段
        self.assertIn("严格按此 6 字段", self.SRC)
        # 顶部 prompt 必须说 6 字段中文分析
        self.assertIn("6 字段中文分析", self.SRC)
        # 老 5 字段声明必须清除
        self.assertNotIn("5 字段中文分析", self.SRC)
        self.assertNotIn("严格按此 5 字段", self.SRC)

    def test_parse_function_renamed_to_6field(self):
        """parse_5field_output → parse_6field_output 重命名."""
        self.assertIn("def parse_6field_output", self.SRC)
        # 老函数名必须清除 (定义和调用都不能残留)
        self.assertNotIn("def parse_5field_output", self.SRC)

    def test_parse_function_has_alignment_field(self):
        """parse_6field_output 内必须含 'alignment' 字段定义."""
        # 字段 dict 必须含 alignment key
        self.assertIn("'alignment': ''", self.SRC)
        # 字段头识别必须含 🎚️
        self.assertIn("🎚️", self.SRC)
        # current_field = 'alignment' 设置点必须存在
        self.assertIn("current_field = 'alignment'", self.SRC)

    def test_parse_function_has_no_variation_selector_fallback(self):
        """🎚 (no variation selector U+FE0F) fallback 必须存在."""
        # V37.9.50 同 V37.9.45 hf_papers 模式
        self.assertIn("🎚", self.SRC)
        self.assertIn("🎚️", self.SRC)

    def test_emit_calls_parse_6field(self):
        """emit 必须调用 parse_6field_output (不是老 parse_5field)."""
        self.assertIn("parse_6field_output(result.get", self.SRC)
        # 老调用必须清除
        self.assertNotIn("parse_5field_output(result.get", self.SRC)

    def test_emit_displays_alignment(self):
        """emit 必须输出 🎚️ 项目对齐度 行."""
        # msg_lines.append 含 🎚️ 字面量
        self.assertIn("🎚️ 项目对齐度", self.SRC)
        # fields['alignment'] 访问点
        self.assertIn("fields['alignment']", self.SRC)

    def test_emit_has_high_alignment_count(self):
        """emit 必须维护 high_alignment_count 统计."""
        self.assertIn("high_alignment_count", self.SRC)
        # 末尾汇总必须含 '高对齐论文' 字样
        self.assertIn("高对齐论文", self.SRC)
        self.assertIn("⭐≥4", self.SRC)

    def test_lazy_imports_project_alignment_scorer(self):
        """V37.9.50 必须 lazy import project_alignment_scorer (FAIL-OPEN)."""
        # try/except 包装 import (不阻塞 cron)
        self.assertIn("from project_alignment_scorer import", self.SRC)
        self.assertIn("load_project_concepts", self.SRC)
        self.assertIn("validate_alignment_score", self.SRC)
        self.assertIn("extract_star_count", self.SRC)
        self.assertIn("format_validation_marker", self.SRC)

    def test_v37_9_50_hotfix_emit_heredoc_imports_os(self):
        """V37.9.50-hotfix: emit heredoc 顶部必须 import os (lazy import 用 os.environ/os.path)."""
        # 找 emit heredoc 起点 + 检查顶部 import 行包含 os
        # Pattern: 'python3 - "$PAPERS_FILE" "$RESULTS_FILE" "$DAY" "$MSG_FILE" << '\''PYEOF'\''
        marker = 'python3 - "$PAPERS_FILE" "$RESULTS_FILE" "$DAY" "$MSG_FILE" << \'PYEOF\''
        idx = self.SRC.find(marker)
        self.assertGreater(idx, 0, "emit heredoc 起点未找到")
        # 取后续 100 字符内必须含 'import' + 'os'
        ctx = self.SRC[idx:idx + 200]
        # 必须有 import 行包含 os (V37.9.50 之前 emit heredoc 缺 os 导致 lazy import name not defined)
        # alternatives: 'import os' 单独 / 'import sys, json, re, os' / 'import os, sys, ...' 等
        import_lines = re.findall(r"^import\s+[\w,\s]+", ctx, re.MULTILINE)
        os_imported = any("os" in line.replace(" ", "").split(",") or "os" in re.split(r"[,\s]+", line)
                          for line in import_lines)
        self.assertTrue(os_imported,
                        f"emit heredoc 顶部 import 行必须含 os (V37.9.50-hotfix), 找到: {import_lines!r}")

    def test_rule_check_call_structure(self):
        """rule_check 必须 (a) 检查 4 个 helper 都加载 (b) try/except 包装."""
        # 4 个 helper 加载守卫
        self.assertIn("if _validate_alignment_score and _concepts", self.SRC)
        # FAIL-OPEN try/except
        self.assertIn("V37.9.50 rule_check 失败", self.SRC)
        # rule_content 拼接 title + tldr/abstract
        self.assertIn("rule_content = paper.get", self.SRC)

    def test_rule_check_uses_marker_for_invalid(self):
        """validation invalid → format_validation_marker → ⚠️ 标记加入推送."""
        self.assertIn("_format_validation_marker(validation)", self.SRC)
        # marker 不为空时加入 msg_lines
        self.assertIn("if marker:", self.SRC)

    def test_no_v37_9_36_placeholder_fallback(self):
        """严禁 V37.9.36 占位符反模式回归 (line-by-line 跳过注释)."""
        # 明确的占位符反模式字串
        forbidden = [
            "贡献：AI领域相关研究",
            "价值：⭐⭐⭐",  # 不带 LLM 输出含义的占位字串
            "要点：技术深度文章",
        ]
        for line in self.SRC.split("\n"):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue  # skip comments
            for pat in forbidden:
                self.assertNotIn(pat, stripped,
                                 f"V37.9.36 占位符反模式回归: {pat!r} in line {stripped!r}")


# ── Test 2: TestV37950LlmDegradedFallback (3) ──────────────────────
class TestV37950LlmDegradedFallback(unittest.TestCase):
    """LLM_DEGRADED 路径不污染高对齐统计 + V37.9.43 兜底保留."""

    @classmethod
    def setUpClass(cls):
        cls.SRC = _read(os.path.join(
            REPO_ROOT, "jobs/semantic_scholar/run_semantic_scholar.sh"))

    def test_llm_degraded_marker_preserved(self):
        """[LLM_DEGRADED] 标记保留 (V37.9.43 fail-fast 契约)."""
        self.assertIn("[LLM_DEGRADED]", self.SRC)

    def test_llm_degraded_uses_tldr_fallback(self):
        """LLM_DEGRADED 路径用 tldr 优先 abstract 兜底 (V37.9.39 同款)."""
        self.assertIn("paper.get('tldr')", self.SRC)
        self.assertIn("paper.get('abstract'", self.SRC)

    def test_llm_degraded_does_not_count_high_alignment(self):
        """LLM_DEGRADED 分支不应触及 high_alignment_count (源码顺序锁)."""
        # LLM_DEGRADED 块必须在 high_alignment_count += 1 之前
        # 测试 Python source 顺序: degraded 写入 msg_lines 块在 alignment block 之前
        idx_degraded = self.SRC.find("[LLM_DEGRADED] 深度分析失败")
        idx_high_count = self.SRC.find("high_alignment_count += 1")
        # idx_degraded 块结束于 else: 后面才进入 alignment 处理
        self.assertGreater(idx_high_count, idx_degraded,
                           "high_alignment_count += 1 必须在 LLM_DEGRADED 块之后")
        # 同时确保 high_alignment_count += 1 在 if llm_stars >= 4 守卫内
        # (不是 LLM_DEGRADED 路径累计)
        ctx_window = self.SRC[max(0, idx_high_count - 200):idx_high_count]
        self.assertIn("if llm_stars >= 4:", ctx_window,
                      "high_alignment_count += 1 必须在 if llm_stars >= 4 守卫之后")


# ── Test 3: TestV37950InAuditAligned (3) ───────────────────────────
class TestV37950InAuditAligned(unittest.TestCase):
    """ontology/llm_cron_audit.py ALIGNED_SCRIPTS 含 semantic_scholar V37.9.50."""

    @classmethod
    def setUpClass(cls):
        cls.AUDIT_SRC = _read(os.path.join(
            REPO_ROOT, "ontology/llm_cron_audit.py"))

    def test_aligned_scripts_has_v37_9_50_anchor(self):
        """ALIGNED_SCRIPTS 含 V37.9.50 锚点 (防未来误删 entry)."""
        # entry 字面量
        self.assertIn('"jobs/semantic_scholar/run_semantic_scholar.sh": "V37.9.50"',
                      self.AUDIT_SRC)

    def test_aligned_scripts_count_at_least_11(self):
        """ALIGNED_SCRIPTS 必有 ≥11 项 (V37.9.45 起 11 项不退化)."""
        # 数 ALIGNED_SCRIPTS dict 中 entry 行数
        in_dict = False
        count = 0
        for line in self.AUDIT_SRC.split("\n"):
            if "ALIGNED_SCRIPTS = {" in line:
                in_dict = True
                continue
            if in_dict:
                if line.strip() == "}":
                    break
                if re.match(r'\s+"[^"]+\.sh":\s*"V', line):
                    count += 1
        self.assertGreaterEqual(count, 11, f"ALIGNED_SCRIPTS 含 {count} 项 (期望 ≥11)")

    def test_audit_recognizes_semantic_scholar_aligned(self):
        """audit_script(semantic_scholar) 返回 aligned=True+version=V37.9.50+findings=0 (端到端)."""
        # 通过 import 模块跑 audit_script
        sys.path.insert(0, REPO_ROOT)
        from ontology import llm_cron_audit
        sh = os.path.join(REPO_ROOT, "jobs/semantic_scholar/run_semantic_scholar.sh")
        report = llm_cron_audit.audit_script(sh)
        self.assertTrue(report.aligned, f"audit failed: aligned={report.aligned}, version={report.aligned_version}")
        self.assertEqual(report.aligned_version, "V37.9.50")
        # placeholder findings 期望为 0 (V37.9.45 同款迁移完整度)
        self.assertEqual(len(report.placeholder_findings), 0,
                         f"V37.9.50 PoC 期望 0 placeholder findings, got: {report.placeholder_findings}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
