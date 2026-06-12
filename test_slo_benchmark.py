#!/usr/bin/env python3
"""
Unit tests for slo_benchmark.py (V35 + V37.9.99 三态样本门槛).
Run: python3 -m unittest test_slo_benchmark -v

V37.9.99 (外部评审 P0): build_report 新增 OBSERVING 第三态 — 样本不足 (< min_sample_count,
默认 200) 时既不报 PASS 也不报 FAIL, 而报 OBSERVING (观察中). 修 V35 golden trace
samples=1 标 ALL PASS 的统计无意义问题. make_stats 默认样本调到 ≥200 让 PASS/FAIL
测试有效, 新增 TestObservingThreshold 覆盖三态门槛.
"""
import json
import os
import tempfile
import unittest

from slo_benchmark import build_report, format_markdown, read_stats, _verdict, MIN_SAMPLE_THRESHOLD


def make_stats(total=250, errors=2, p50=5000, p95=12000, p99=25000,
               max_lat=28000, samples=250, tool_total=250, tool_success=245,
               fallback=1, timeout=1, recovery=250, streaks=250,
               prompt_tokens=50000, total_tokens=80000):
    """Helper: build a proxy_stats dict for testing.

    V37.9.99: 默认样本基数 ≥200 (samples/total/tool_total/streaks=250) 让
    PASS/FAIL 测试越过 OBSERVING 门槛. 测 OBSERVING 时显式传小样本.
    """
    total_errors_by_type = {"timeout": timeout, "context_overflow": 0, "backend": 0, "other": errors - timeout}
    deg_pct = round(fallback / total * 100, 2) if total > 0 else 0
    timeout_pct = round(timeout / total * 100, 2) if total > 0 else 0
    tool_pct = round(tool_success / tool_total * 100, 2) if tool_total > 0 else 100.0
    rec_pct = round(recovery / streaks * 100, 2) if streaks > 0 else 100.0
    return {
        "total_requests": total,
        "total_errors": errors,
        "prompt_tokens": prompt_tokens,
        "total_tokens": total_tokens,
        "slo": {
            "latency": {"p50": p50, "p95": p95, "p99": p99, "max": max_lat, "count": samples},
            "errors_by_type": total_errors_by_type,
            "tool_calls_total": tool_total,
            "tool_calls_success": tool_success,
            "tool_success_rate_pct": tool_pct,
            "degradation_rate_pct": deg_pct,
            "fallback_count": fallback,
            "timeout_rate_pct": timeout_pct,
            "auto_recovery_rate_pct": rec_pct,
            "recovery_total": recovery,
            "failure_streaks": streaks,
        },
    }


def make_config(min_sample_count=None):
    """Helper: minimal config matching config.yaml defaults."""
    slo = {
        "latency_p95_ms": 30000,
        "tool_success_rate_pct": 95.0,
        "degradation_rate_pct": 5.0,
        "timeout_rate_pct": 3.0,
        "auto_recovery_rate_pct": 90.0,
    }
    if min_sample_count is not None:
        slo["min_sample_count"] = min_sample_count
    return {"slo": slo}


class TestBuildReport(unittest.TestCase):

    def test_all_pass_healthy_system(self):
        """Healthy system with sufficient samples should produce ALL PASS."""
        stats = make_stats()
        report = build_report(stats, make_config())
        self.assertEqual(report["overall_verdict"], "ALL PASS")
        self.assertEqual(report["pass_count"], 5)
        self.assertEqual(report["total_checks"], 5)

    def test_latency_violation(self):
        """p95 > 30s with sufficient samples should fail latency check."""
        stats = make_stats(p95=35000, samples=250)
        report = build_report(stats, make_config())
        self.assertEqual(report["latency"]["verdict"], "FAIL")
        self.assertEqual(report["overall_verdict"], "VIOLATIONS DETECTED")

    def test_tool_success_violation(self):
        """Tool success < 95% with sufficient samples should fail."""
        stats = make_stats(tool_total=250, tool_success=225)  # 90%
        report = build_report(stats, make_config())
        self.assertEqual(report["tools"]["verdict"], "FAIL")

    def test_degradation_violation(self):
        """Degradation > 5% with sufficient samples should fail."""
        stats = make_stats(total=250, fallback=25)  # 10%
        report = build_report(stats, make_config())
        self.assertEqual(report["degradation"]["verdict"], "FAIL")

    def test_timeout_violation(self):
        """Timeout > 3% with sufficient samples should fail."""
        stats = make_stats(total=250, timeout=13, errors=13)  # 5.2%
        report = build_report(stats, make_config())
        self.assertEqual(report["errors"]["verdict"], "FAIL")

    def test_recovery_violation(self):
        """Recovery < 90% with sufficient streaks should fail."""
        stats = make_stats(recovery=20, streaks=250)  # 8%
        report = build_report(stats, make_config())
        self.assertEqual(report["recovery"]["verdict"], "FAIL")

    def test_zero_requests(self):
        """Zero requests should not crash."""
        stats = make_stats(total=0, errors=0, samples=0, tool_total=0,
                          fallback=0, timeout=0, recovery=0, streaks=0)
        report = build_report(stats, make_config())
        self.assertEqual(report["observation_window"]["success_rate_pct"], 0)

    def test_token_included(self):
        """Token usage should be in report."""
        stats = make_stats(prompt_tokens=12345, total_tokens=23456)
        report = build_report(stats, make_config())
        self.assertEqual(report["tokens"]["prompt_tokens"], 12345)
        self.assertEqual(report["tokens"]["total_tokens"], 23456)


class TestObservingThreshold(unittest.TestCase):
    """V37.9.99 外部评审 P0: 样本门槛三态 (OBSERVING) 覆盖."""

    def test_verdict_helper_three_states(self):
        """_verdict 三态: 样本不足→OBSERVING, 达标→PASS, 否则→FAIL."""
        self.assertEqual(_verdict(True, 1, 200), "OBSERVING")    # 样本不足, 即使达标也不判 PASS
        self.assertEqual(_verdict(False, 1, 200), "OBSERVING")   # 样本不足, 即使不达标也不判 FAIL
        self.assertEqual(_verdict(True, 200, 200), "PASS")       # 样本够 + 达标
        self.assertEqual(_verdict(False, 250, 200), "FAIL")      # 样本够 + 不达标

    def test_one_sample_does_not_pass(self):
        """血案修复: V35 golden trace samples=1 不应标 PASS (应 OBSERVING)."""
        stats = make_stats(total=1, samples=1, tool_total=1, streaks=1)
        report = build_report(stats, make_config())
        self.assertEqual(report["latency"]["verdict"], "OBSERVING")
        self.assertIn("OBSERVING", report["overall_verdict"])
        self.assertNotEqual(report["overall_verdict"], "ALL PASS")

    def test_low_samples_latency_observing(self):
        """p95 超标但样本不足 → OBSERVING (不报 FAIL 也不报 PASS)."""
        stats = make_stats(p95=50000, samples=3)
        report = build_report(stats, make_config())
        self.assertEqual(report["latency"]["verdict"], "OBSERVING")

    def test_zero_tool_calls_na(self):
        """V37.9.143 四态: 0 tool calls → N_A_NO_TOOL_CALLS (无流量不可评判,
        区别于 OBSERVING 有流量但样本不足; 升级自 V37.9.99 OBSERVING 语义)."""
        stats = make_stats(tool_total=0, tool_success=0)
        report = build_report(stats, make_config())
        self.assertEqual(report["tools"]["verdict"], "N_A_NO_TOOL_CALLS")

    def test_fail_takes_precedence_over_observing(self):
        """FAIL > OBSERVING: 一个 check 够样本且超标 FAIL → overall VIOLATIONS."""
        # latency 够样本超标 FAIL; tools 样本不足 OBSERVING
        stats = make_stats(p95=35000, samples=250, tool_total=5, tool_success=2)
        report = build_report(stats, make_config())
        self.assertEqual(report["latency"]["verdict"], "FAIL")
        self.assertEqual(report["tools"]["verdict"], "OBSERVING")
        self.assertEqual(report["overall_verdict"], "VIOLATIONS DETECTED")

    def test_observing_overall_when_no_fail(self):
        """无 FAIL 但有 OBSERVING → overall OBSERVING (不是 ALL PASS)."""
        stats = make_stats(total=250, samples=250, tool_total=5, tool_success=5)
        report = build_report(stats, make_config())
        self.assertEqual(report["tools"]["verdict"], "OBSERVING")
        self.assertIn("OBSERVING", report["overall_verdict"])

    def test_observing_count_in_report(self):
        """report 含 observing_count / fail_count 计数."""
        stats = make_stats(total=1, samples=1, tool_total=1, streaks=1)
        report = build_report(stats, make_config())
        self.assertIn("observing_count", report)
        self.assertEqual(report["observing_count"], 5)
        self.assertEqual(report["fail_count"], 0)

    def test_min_sample_threshold_configurable(self):
        """config slo.min_sample_count 可调低门槛 (低流量个人系统)."""
        # 默认 200 时 samples=50 → OBSERVING
        stats = make_stats(samples=50, total=50, tool_total=50, streaks=50)
        report_default = build_report(stats, make_config())
        self.assertEqual(report_default["latency"]["verdict"], "OBSERVING")
        # 调低到 30 时 samples=50 → PASS (达标)
        report_low = build_report(stats, make_config(min_sample_count=30))
        self.assertEqual(report_low["latency"]["verdict"], "PASS")
        self.assertEqual(report_low["min_sample_threshold"], 30)

    def test_default_threshold_is_200(self):
        """默认门槛 = 200 (外部评审建议 = 延迟 rolling buffer 满)."""
        self.assertEqual(MIN_SAMPLE_THRESHOLD, 200)
        report = build_report(make_stats(), make_config())
        self.assertEqual(report["min_sample_threshold"], 200)


class TestV379143FourStateAndTrends(unittest.TestCase):
    """V37.9.143 (外部评审2 P0(a)): 四态 verdict + 24h/7d 双窗口 + 阈值原因正文段."""

    def test_na_does_not_block_all_pass(self):
        """N_A 不参与汇总判定: 其余全 PASS + tool N_A → overall ALL PASS."""
        stats = make_stats(tool_total=0, tool_success=0)
        report = build_report(stats, make_config())
        self.assertEqual(report["tools"]["verdict"], "N_A_NO_TOOL_CALLS")
        self.assertEqual(report["overall_verdict"], "ALL PASS")
        self.assertEqual(report["na_count"], 1)

    def test_na_does_not_mask_fail(self):
        """N_A 跳过但 FAIL 仍优先: latency FAIL + tool N_A → VIOLATIONS."""
        stats = make_stats(p95=60000, samples=250, tool_total=0, tool_success=0)
        report = build_report(stats, make_config())
        self.assertEqual(report["latency"]["verdict"], "FAIL")
        self.assertEqual(report["overall_verdict"], "VIOLATIONS DETECTED")

    def test_one_tool_call_is_observing_not_na(self):
        """四态边界: tool_total=1 (有流量但样本不足) → OBSERVING 不是 N_A."""
        stats = make_stats(tool_total=1, tool_success=1)
        report = build_report(stats, make_config())
        self.assertEqual(report["tools"]["verdict"], "OBSERVING")

    def test_report_has_trend_windows_key(self):
        """report 必含 trend_windows 键 (无历史时为 None, 不缺键)."""
        report = build_report(make_stats(), make_config())
        self.assertIn("trend_windows", report)

    def test_markdown_trend_windows_section(self):
        """Markdown 必含 Trend Windows 段 (无历史显示提示行)."""
        report = build_report(make_stats(), make_config())
        report["trend_windows"] = None  # 模拟 dev 无历史
        md = format_markdown(report)
        self.assertIn("## Trend Windows (24h / 7d)", md)
        self.assertIn("暂无历史快照", md)

    def test_markdown_trend_windows_with_history(self):
        """有历史时渲染 24h/7d 双行表."""
        report = build_report(make_stats(), make_config())
        report["trend_windows"] = {
            "history_total_snapshots": 168,
            "trend_24h": {"period_snapshots": 24, "total_requests": 100, "total_errors": 2,
                          "avg_success_pct": 98.0, "avg_p95_ms": 35000.0, "max_p95_ms": 48000,
                          "avg_degradation_pct": 1.0},
            "trend_7d": {"period_snapshots": 168, "total_requests": 700, "total_errors": 10,
                         "avg_success_pct": 98.5, "avg_p95_ms": 36000.0, "max_p95_ms": 52000,
                         "avg_degradation_pct": 1.2},
        }
        md = format_markdown(report)
        self.assertIn("| Last 24h | 24 |", md)
        self.assertIn("| Last 7d | 168 |", md)
        self.assertIn("168 snapshots", md)

    def test_markdown_threshold_rationale_section(self):
        """阈值原因正文段: target>30000 时写明 V37.9.79 调整原因 + 恢复条件."""
        config = make_config()
        config["slo"]["latency_p95_ms"] = 50000
        report = build_report(make_stats(), config)
        md = format_markdown(report)
        self.assertIn("## Threshold Rationale", md)
        self.assertIn("V37.9.79", md)
        self.assertIn("不是掩盖问题", md)
        self.assertIn("恢复 30000ms 的条件", md)

    def test_markdown_rationale_adapts_when_restored(self):
        """阈值恢复 30000 后 rationale 自动切换文案 (不留谎言)."""
        report = build_report(make_stats(), make_config())  # make_config 默认 30000
        md = format_markdown(report)
        self.assertIn("## Threshold Rationale", md)
        self.assertNotIn("恢复 30000ms 的条件", md)
        self.assertIn("已恢复 V36 原始目标", md)

    def test_methodology_thresholds_dynamic(self):
        """Methodology 阈值行动态读 config (修 V37.9.79 后硬编码 ≤30s 漂移)."""
        config = make_config()
        config["slo"]["latency_p95_ms"] = 50000
        report = build_report(make_stats(), config)
        md = format_markdown(report)
        self.assertIn("latency p95 ≤50s", md)
        self.assertNotIn("latency p95 ≤30s", md)

    def test_methodology_mentions_four_states(self):
        report = build_report(make_stats(), make_config())
        md = format_markdown(report)
        self.assertIn("N_A_NO_TOOL_CALLS", md)

    def test_build_trend_windows_no_history_returns_none(self):
        """dev 无 slo_history.jsonl → build_trend_windows 返回 None 不抛异."""
        import slo_benchmark, slo_dashboard
        from unittest.mock import patch
        with patch.object(slo_dashboard, "load_history", return_value=[]):
            self.assertIsNone(slo_benchmark.build_trend_windows())

    def test_build_trend_windows_with_history(self):
        """有历史 → 返回 24h/7d 双窗口 dict (复用 slo_dashboard, MR-8)."""
        import slo_benchmark, slo_dashboard
        from unittest.mock import patch
        from datetime import datetime
        now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        fake = [{"ts": now, "p95_ms": 30000, "success_pct": 99.0, "requests": 10,
                 "errors": 0, "degradation_pct": 0.5}]
        with patch.object(slo_dashboard, "load_history", return_value=fake):
            tw = slo_benchmark.build_trend_windows()
        self.assertIsNotNone(tw)
        self.assertEqual(tw["history_total_snapshots"], 1)
        self.assertIn("trend_24h", tw)
        self.assertIn("trend_7d", tw)
        self.assertEqual(tw["trend_24h"]["period_snapshots"], 1)

    def test_source_guard_v37_9_143(self):
        """源码守卫: 四态字面量 + Methodology 不得回退硬编码 30s."""
        import os
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "slo_benchmark.py"), encoding="utf-8") as f:
            src = f.read()
        self.assertIn("N_A_NO_TOOL_CALLS", src)
        self.assertIn("V37.9.143", src)
        self.assertIn("build_trend_windows", src)
        self.assertNotIn("latency p95 ≤30s, tool success ≥95%", src,
                         "Methodology 硬编码阈值行不得回退 (V37.9.79 漂移 bug)")


class TestFormatMarkdown(unittest.TestCase):

    def test_contains_sections(self):
        """Markdown should contain all major sections."""
        report = build_report(make_stats(), make_config())
        md = format_markdown(report)
        self.assertIn("# SLO Benchmark Report", md)
        self.assertIn("## Traffic Summary", md)
        self.assertIn("## Latency Distribution", md)
        self.assertIn("## Error Classification", md)
        self.assertIn("## SLO Compliance Matrix", md)
        self.assertIn("## Token Usage", md)
        self.assertIn("## Methodology", md)

    def test_verdict_in_output(self):
        """Overall verdict should be visible."""
        report = build_report(make_stats(), make_config())
        md = format_markdown(report)
        self.assertIn("ALL PASS", md)

    def test_violation_shown(self):
        """Violations should be visible in markdown (sufficient samples)."""
        report = build_report(make_stats(p95=35000, samples=250), make_config())
        md = format_markdown(report)
        self.assertIn("FAIL", md)
        self.assertIn("VIOLATIONS DETECTED", md)

    def test_observing_shown(self):
        """V37.9.99: OBSERVING 应在 markdown 中可见 (低样本)."""
        report = build_report(make_stats(total=1, samples=1, tool_total=1, streaks=1), make_config())
        md = format_markdown(report)
        self.assertIn("OBSERVING", md)
        self.assertIn("≥200", md)  # min samples 门槛展示

    def test_avg_tokens_per_request(self):
        """Avg tokens/request should be computed when requests > 0."""
        report = build_report(make_stats(total=10, total_tokens=10000), make_config())
        md = format_markdown(report)
        self.assertIn("1,000", md)


class TestReadStats(unittest.TestCase):

    def test_read_valid_file(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"slo": {}, "total_requests": 5}, f)
            f.flush()
            result = read_stats(f.name)
        os.unlink(f.name)
        self.assertIsNotNone(result)
        self.assertEqual(result["total_requests"], 5)

    def test_read_missing_file(self):
        result = read_stats("/tmp/nonexistent_slo_benchmark_test.json")
        self.assertIsNone(result)

    def test_read_invalid_json(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("not json")
            f.flush()
            result = read_stats(f.name)
        os.unlink(f.name)
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main(verbosity=2)
