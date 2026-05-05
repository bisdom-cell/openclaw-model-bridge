#!/usr/bin/env python3
"""test_slo_checker.py — V37.9.28 F4 方向显示修复回归

锁定 slo_checker.py format_alert 的方向显示语义:
  - 5 个 metric 的 direction 显式声明在 check_slo 写入的 result dict 中
  - format_alert 直接读 v["direction"], 不再用纠缠 if-else 推断
  - 旧 bug: 3/5 metric 方向反 (latency_p95 / tool_success / auto_recovery 颠倒)

血案场景 (用户 5/5 周一观察): "🔴 latency_p95: 54040ms (目标: >30000ms)"
  ↑ ">30000ms 是目标" 暗示越大越好, 实际应 "<30000ms 是目标" (越小越好)
"""

from __future__ import annotations

import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

from slo_checker import check_slo, format_alert  # noqa: E402


class TestDirectionField(unittest.TestCase):
    """V37.9.28 F4: 每个 metric 显式声明 direction."""

    def setUp(self):
        # 构造一个 stats 让所有 metric 都违规, 这样 result dict 全部走完
        self.bad_stats = {
            "total_requests": 100,
            "slo": {
                "latency": {"p95": 54040, "count": 100},
                "tool_success_rate_pct": 50.0,  # below 95
                "tool_calls_total": 100,
                "degradation_rate_pct": 50.0,   # above 5
                "timeout_rate_pct": 50.0,       # above 3
                "auto_recovery_rate_pct": 0.0,  # below 90
            },
        }
        self.config = {
            "slo": {
                "latency_p95_ms": 30000,
                "tool_success_rate_pct": 95.0,
                "degradation_rate_pct": 5.0,
                "timeout_rate_pct": 3.0,
                "auto_recovery_rate_pct": 90.0,
            }
        }
        self.results, self.all_ok = check_slo(self.bad_stats, self.config)
        self.by_name = {r["name"]: r for r in self.results}

    def test_all_metrics_have_direction(self):
        """每个 result dict 都有 direction 字段."""
        for r in self.results:
            self.assertIn("direction", r,
                          f"{r['name']} missing direction field")
            self.assertIn(r["direction"], ("<", ">"),
                          f"{r['name']} direction must be '<' or '>'")

    def test_latency_p95_direction_lt(self):
        """V37.9.28 F4 核心修复: latency_p95 direction 应是 '<'."""
        self.assertEqual(self.by_name["latency_p95"]["direction"], "<")

    def test_tool_success_direction_gt(self):
        """V37.9.28 F4: tool_success_rate direction 应是 '>'."""
        self.assertEqual(self.by_name["tool_success_rate"]["direction"], ">")

    def test_degradation_direction_lt(self):
        """degradation_rate (越小越好) direction 应是 '<'."""
        self.assertEqual(self.by_name["degradation_rate"]["direction"], "<")

    def test_timeout_direction_lt(self):
        """timeout_rate (越小越好) direction 应是 '<'."""
        self.assertEqual(self.by_name["timeout_rate"]["direction"], "<")

    def test_auto_recovery_direction_gt(self):
        """V37.9.28 F4: auto_recovery_rate direction 应是 '>' (越大越好)."""
        self.assertEqual(self.by_name["auto_recovery_rate"]["direction"], ">")


class TestFormatAlertUsesDirection(unittest.TestCase):
    """V37.9.28 F4: format_alert 读 v[direction] 不再推断."""

    def setUp(self):
        # 5 metric 全部违规
        self.results = [
            {"name": "latency_p95", "value": 54040, "target": 30000,
             "unit": "ms", "direction": "<", "ok": False, "samples": 100},
            {"name": "tool_success_rate", "value": 50.0, "target": 95.0,
             "unit": "%", "direction": ">", "ok": False, "samples": 100},
            {"name": "degradation_rate", "value": 50.0, "target": 5.0,
             "unit": "%", "direction": "<", "ok": False},
            {"name": "timeout_rate", "value": 50.0, "target": 3.0,
             "unit": "%", "direction": "<", "ok": False},
            {"name": "auto_recovery_rate", "value": 0.0, "target": 90.0,
             "unit": "%", "direction": ">", "ok": False},
        ]
        self.alert = format_alert(self.results)

    def test_user_blood_lesson_latency_shows_lt_target(self):
        """V37.9.28 F4 血案场景: latency_p95 应显示 '目标: <30000ms' 不是 '>30000ms'."""
        self.assertIn("latency_p95: 54040ms (目标: <30000ms)", self.alert,
                      "F4: latency_p95 alert must use '<' direction")
        self.assertNotIn("latency_p95: 54040ms (目标: >30000ms)", self.alert,
                         "Old bug: '>30000ms' direction must NOT appear")

    def test_tool_success_shows_gt_target(self):
        self.assertIn("tool_success_rate: 50.0% (目标: >95.0%)", self.alert,
                      "tool_success must use '>' direction")
        self.assertNotIn("tool_success_rate: 50.0% (目标: <", self.alert,
                         "Old bug: tool_success '<' direction must NOT appear")

    def test_auto_recovery_shows_gt_target(self):
        self.assertIn("auto_recovery_rate: 0.0% (目标: >90.0%)", self.alert,
                      "auto_recovery must use '>' direction (越大越好)")
        self.assertNotIn("auto_recovery_rate: 0.0% (目标: <", self.alert,
                         "Old bug: auto_recovery '<' direction must NOT appear")

    def test_degradation_shows_lt_target(self):
        self.assertIn("degradation_rate: 50.0% (目标: <5.0%)", self.alert)

    def test_timeout_shows_lt_target(self):
        self.assertIn("timeout_rate: 50.0% (目标: <3.0%)", self.alert)

    def test_no_violations_returns_empty(self):
        ok_results = [
            {"name": "latency_p95", "value": 100, "target": 30000,
             "unit": "ms", "direction": "<", "ok": True, "samples": 100},
        ]
        self.assertEqual(format_alert(ok_results), "")

    def test_format_alert_handles_missing_direction_gracefully(self):
        """向后兼容: 如有旧代码路径 result dict 缺 direction, 默认 '<' 不崩."""
        legacy = [{"name": "test", "value": 1, "target": 0, "unit": "ms", "ok": False}]
        result = format_alert(legacy)
        self.assertIn("test: 1ms", result)


class TestSourceLevelGuards(unittest.TestCase):
    """V37.9.28 F4: 源码守卫防止旧 if-else 推断逻辑回归."""

    def setUp(self):
        with open(os.path.join(_HERE, "slo_checker.py"), "r", encoding="utf-8") as f:
            self.source = f.read()

    def test_v37_9_28_marker_present(self):
        self.assertIn("V37.9.28 F4", self.source,
                      "slo_checker.py must mark V37.9.28 F4 direction fix")

    def test_old_inference_pattern_removed(self):
        """旧 bug 模式: direction 由 if-else 推断 (unit/name 检测)."""
        # 旧 bug 三层 if-else:
        # direction = ">" if v["unit"] == "ms" else "<" if "rate" in v["name"] and ...
        self.assertNotIn(
            'direction = ">" if v["unit"] == "ms"',
            self.source,
            "Old direction inference based on unit must be removed"
        )
        self.assertNotIn(
            'if "recovery" in v["name"] or "success" in v["name"]:',
            self.source,
            "Old override `if recovery or success` must be removed"
        )

    def test_format_alert_uses_v_direction(self):
        """format_alert 必须读 v.get('direction', ...) 或 v['direction']."""
        format_alert_block = self.source[self.source.find("def format_alert"):]
        format_alert_block = format_alert_block[:format_alert_block.find("\ndef ")]
        self.assertIn('v.get("direction"', format_alert_block,
                      "format_alert must read v['direction'] field")

    def test_each_metric_declares_direction(self):
        """check_slo 中每个 results.append 必须包含 'direction':."""
        check_slo_block = self.source[self.source.find("def check_slo"):]
        check_slo_block = check_slo_block[:check_slo_block.find("\ndef ")]
        self.assertEqual(check_slo_block.count('"direction":'), 5,
                         "All 5 metrics must declare 'direction' explicitly in check_slo")


if __name__ == "__main__":
    unittest.main()
