#!/usr/bin/env python3
"""test_v37_9_49_radar_integration.py — V37.9.49 Sub-Stage 4a 集成单测

Stage 4a 范围:
  - kb_evening_collect.py 集成 #3 trend_acceleration → build_evening_prompt 加 trend_signals 参数
  - kb_dream.sh 集成 #1 cross_source + #3 trend_acceleration → Phase 1.5 信号采集 + REDUCE_DATA 注入

不涉及:
  - 11 脚本批量改 6 字段 (Sub-Stage 4b)
  - Top 5 高对齐推送段 (Sub-Stage 4c)

测试类:
  TestEveningCollectTrendIntegration (4) — collect_trend_signals_for_evening + build_evening_prompt
  TestKbDreamPhase15ShellGuards (5)      — kb_dream.sh Phase 1.5 源码级守卫
"""

import os
import sys
import json
import tempfile
import unittest

import kb_evening_collect as ke

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))


# ── Test 1: TestEveningCollectTrendIntegration (4) ──────────────────
class TestEveningCollectTrendIntegration(unittest.TestCase):
    """V37.9.49 Sub-Stage 4a: kb_evening_collect.py 集成 #3 trend acceleration"""

    def test_missing_radar_dir_returns_empty(self):
        """radar_dir 不存在 → 返回空字符串 (FAIL-OPEN, 不阻塞 evening)."""
        with tempfile.TemporaryDirectory() as tmp:
            result = ke.collect_trend_signals_for_evening(
                radar_dir=os.path.join(tmp, "nonexistent")
            )
            self.assertEqual(result, "")

    def test_no_weekly_trends_files_returns_empty(self):
        """radar_dir 存在但无 weekly_trends_*.json → 返回空字符串."""
        with tempfile.TemporaryDirectory() as tmp:
            result = ke.collect_trend_signals_for_evening(radar_dir=tmp)
            self.assertEqual(result, "")

    def test_real_weekly_trends_data_formats_correctly(self):
        """有真实 weekly_trends.json → 格式化为 加速主题/减速主题 段."""
        with tempfile.TemporaryDirectory() as tmp:
            data = {
                "week": "2026-W19",
                "signals": [
                    {"keyword": "agent runtime",
                     "classification": "🚀 strong_acceleration", "accel_1w": 2.3},
                    {"keyword": "cot pruning",
                     "classification": "🚀 strong_acceleration", "accel_1w": 1.8},
                    {"keyword": "static prompt",
                     "classification": "⚰️ obsolescence", "accel_1w": 0.3},
                ],
            }
            with open(os.path.join(tmp, "weekly_trends_2026-W19.json"), "w") as f:
                json.dump(data, f)
            result = ke.collect_trend_signals_for_evening(radar_dir=tmp)
            # 必含两段 + 关键词
            self.assertIn("agent runtime", result)
            self.assertIn("static prompt", result)
            self.assertIn("加速主题", result)
            self.assertIn("减速主题", result)

    def test_build_evening_prompt_with_trend_signals(self):
        """build_evening_prompt 接收 trend_signals 参数 → prompt 注入加速度段."""
        # 含 trend_signals
        prompt_with = ke.build_evening_prompt(
            "note text", "source text", 1, 100, 50, 5, "tech",
            trend_signals="测试 trend 数据 (mock)",
        )
        self.assertIn("测试 trend 数据 (mock)", prompt_with)
        self.assertIn("本周趋势加速度", prompt_with)

        # 缺 trend_signals (向后兼容 V37.6/7 行为)
        prompt_without = ke.build_evening_prompt(
            "note text", "source text", 1, 100, 50, 5, "tech",
        )
        self.assertNotIn("本周趋势加速度", prompt_without)


# ── Test 2: TestKbDreamPhase15ShellGuards (5) ───────────────────────
class TestKbDreamPhase15ShellGuards(unittest.TestCase):
    """V37.9.49 Sub-Stage 4a: kb_dream.sh Phase 1.5 源码级守卫."""

    @classmethod
    def setUpClass(cls):
        path = os.path.join(REPO_ROOT, "kb_dream.sh")
        with open(path, "r", encoding="utf-8") as f:
            cls.SRC = f.read()

    def test_phase_1_5_block_exists(self):
        """kb_dream.sh 必含 V37.9.49 Sub-Stage 4a Phase 1.5 块标记."""
        self.assertIn("V37.9.49 Sub-Stage 4a", self.SRC)
        self.assertIn("Phase 1.5 (Opportunity Radar)", self.SRC)

    def test_calls_cross_source_aggregator(self):
        """Phase 1.5 必调 cross_source_signal_aggregator (V37.9.46 #1)."""
        self.assertIn("cross_source_signal_aggregator", self.SRC)
        self.assertIn("RADAR_SCORER", self.SRC)
        # 注入 REDUCE_DATA
        self.assertIn("RADAR_SIGNALS_BLOCK", self.SRC)

    def test_calls_kb_trend_acceleration(self):
        """Phase 1.5 必调 kb_trend_acceleration (V37.9.48 #3)."""
        self.assertIn("kb_trend_acceleration", self.SRC)
        self.assertIn("TREND_SCORER", self.SRC)
        self.assertIn("TREND_SIGNALS_BLOCK", self.SRC)

    def test_reduce_data_injection_uses_section_headers(self):
        """REDUCE_DATA 注入必含双段 section header (Reduce LLM 可识别)."""
        self.assertIn("Opportunity Radar #1", self.SRC)
        self.assertIn("Opportunity Radar #3", self.SRC)
        # Reduce LLM 注意提示 (防 Radar 信号被无中生有展开)
        self.assertIn("早期机会点", self.SRC)

    def test_fail_open_contract(self):
        """Phase 1.5 必有 FAIL-OPEN 契约 (任意脚本失败不阻塞 Reduce)."""
        # FAIL-OPEN comment 标记
        self.assertIn("FAIL-OPEN", self.SRC)
        # 调用前检查 scorer 文件存在 ([ -f "$RADAR_SCORER" ])
        self.assertIn('[ -f "$RADAR_SCORER" ]', self.SRC)
        self.assertIn('[ -f "$TREND_SCORER" ]', self.SRC)


if __name__ == "__main__":
    unittest.main(verbosity=2)
