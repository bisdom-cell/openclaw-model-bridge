#!/usr/bin/env python3
"""
Unit tests for slo_benchmark.py (V35).
Run: python3 -m unittest test_slo_benchmark -v
"""
import json
import os
import tempfile
import unittest

from slo_benchmark import build_report, format_markdown, read_stats


def make_stats(total=100, errors=2, p50=5000, p95=12000, p99=25000,
               max_lat=28000, samples=100, tool_total=50, tool_success=48,
               fallback=1, timeout=1, recovery=3, streaks=3,
               prompt_tokens=50000, total_tokens=80000):
    """Helper: build a proxy_stats dict for testing."""
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


def make_config():
    """Helper: minimal config matching config.yaml defaults."""
    return {
        "slo": {
            "latency_p95_ms": 30000,
            "tool_success_rate_pct": 95.0,
            "degradation_rate_pct": 5.0,
            "timeout_rate_pct": 3.0,
            "auto_recovery_rate_pct": 90.0,
        }
    }


class TestBuildReport(unittest.TestCase):

    def test_all_pass_healthy_system(self):
        """Healthy system should produce ALL PASS."""
        stats = make_stats()
        report = build_report(stats, make_config())
        self.assertEqual(report["overall_verdict"], "ALL PASS")
        self.assertEqual(report["pass_count"], 5)
        self.assertEqual(report["total_checks"], 5)

    def test_latency_violation(self):
        """p95 > 30s should fail latency check."""
        stats = make_stats(p95=35000, samples=10)
        report = build_report(stats, make_config())
        self.assertEqual(report["latency"]["verdict"], "FAIL")
        self.assertEqual(report["overall_verdict"], "VIOLATIONS DETECTED")

    def test_latency_pass_low_samples(self):
        """p95 > 30s but <5 samples should still pass."""
        stats = make_stats(p95=50000, samples=3)
        report = build_report(stats, make_config())
        self.assertEqual(report["latency"]["verdict"], "PASS")

    def test_tool_success_violation(self):
        """Tool success < 95% should fail."""
        stats = make_stats(tool_total=100, tool_success=90)
        report = build_report(stats, make_config())
        self.assertEqual(report["tools"]["verdict"], "FAIL")

    def test_tool_zero_calls_pass(self):
        """Zero tool calls should pass (no data = no violation)."""
        stats = make_stats(tool_total=0, tool_success=0)
        report = build_report(stats, make_config())
        self.assertEqual(report["tools"]["verdict"], "PASS")

    def test_degradation_violation(self):
        """Degradation > 5% should fail."""
        stats = make_stats(total=100, fallback=10)
        report = build_report(stats, make_config())
        self.assertEqual(report["degradation"]["verdict"], "FAIL")

    def test_timeout_violation(self):
        """Timeout > 3% should fail."""
        stats = make_stats(total=100, timeout=5, errors=5)
        report = build_report(stats, make_config())
        self.assertEqual(report["errors"]["verdict"], "FAIL")

    def test_recovery_violation(self):
        """Recovery < 90% should fail."""
        stats = make_stats(recovery=1, streaks=5)
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
        """Violations should be visible in markdown."""
        report = build_report(make_stats(p95=35000, samples=10), make_config())
        md = format_markdown(report)
        self.assertIn("FAIL", md)
        self.assertIn("VIOLATIONS DETECTED", md)

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
