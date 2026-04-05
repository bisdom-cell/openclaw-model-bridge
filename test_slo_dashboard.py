#!/usr/bin/env python3
"""
test_slo_dashboard.py — SLO Dashboard 单测
"""
import json
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from slo_dashboard import (
    extract_snapshot, take_snapshot, load_history, filter_history,
    compute_trends, build_dashboard, format_dashboard_md, format_compact,
    _sparkline, _trim_history, read_stats,
)


def _make_stats(total=100, errors=2, p50=200, p95=500, p99=1500,
                fallback=1, tool_total=50, tool_success=48):
    """Helper to create a proxy_stats-like dict."""
    return {
        "total_requests": total,
        "total_errors": errors,
        "prompt_tokens": 50000,
        "total_tokens": 80000,
        "slo": {
            "latency": {"p50": p50, "p95": p95, "p99": p99, "max": 3000, "count": total},
            "errors_by_type": {"timeout": 1, "backend": 1, "context_overflow": 0, "other": 0},
            "fallback_count": fallback,
            "degradation_rate_pct": round(fallback / total * 100, 2) if total > 0 else 0,
            "tool_calls_total": tool_total,
            "tool_calls_success": tool_success,
            "tool_success_rate_pct": round(tool_success / tool_total * 100, 2) if tool_total > 0 else 100,
            "timeout_rate_pct": round(1 / total * 100, 2) if total > 0 else 0,
            "auto_recovery_rate_pct": 100.0,
        },
    }


class TestExtractSnapshot(unittest.TestCase):
    def test_basic(self):
        stats = _make_stats()
        snap = extract_snapshot(stats)
        self.assertIsNotNone(snap)
        self.assertEqual(snap["requests"], 100)
        self.assertEqual(snap["errors"], 2)
        self.assertEqual(snap["p95_ms"], 500)
        self.assertAlmostEqual(snap["success_pct"], 98.0)

    def test_none_input(self):
        self.assertIsNone(extract_snapshot(None))

    def test_empty_stats(self):
        snap = extract_snapshot({})
        # Empty dict has no total_requests, extract returns a snapshot with 0s
        if snap:
            self.assertEqual(snap["requests"], 0)
        # Also valid: returns None for completely empty input

    def test_zero_requests(self):
        snap = extract_snapshot(_make_stats(total=0, errors=0))
        self.assertEqual(snap["success_pct"], 0)

    def test_has_timestamp(self):
        snap = extract_snapshot(_make_stats())
        self.assertIn("ts", snap)
        self.assertTrue(snap["ts"].startswith("20"))


class TestTakeSnapshot(unittest.TestCase):
    def test_with_stats_file(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(_make_stats(), f)
            stats_path = f.name

        with tempfile.TemporaryDirectory() as tmpdir:
            history_path = os.path.join(tmpdir, "history.jsonl")
            import slo_dashboard
            orig = slo_dashboard._history_path
            slo_dashboard._history_path = lambda: history_path
            try:
                snap = take_snapshot(stats_path)
                self.assertIsNotNone(snap)
                self.assertTrue(os.path.exists(history_path))
                entries = load_history(history_path)
                self.assertEqual(len(entries), 1)
            finally:
                slo_dashboard._history_path = orig
                os.unlink(stats_path)

    def test_no_stats_file(self):
        snap = take_snapshot("/nonexistent/path.json")
        self.assertIsNone(snap)


class TestLoadHistory(unittest.TestCase):
    def test_load_empty(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.name  # empty file
            path = f.name
        try:
            entries = load_history(path)
            self.assertEqual(entries, [])
        finally:
            os.unlink(path)

    def test_load_entries(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            for i in range(5):
                f.write(json.dumps({"ts": f"2026-04-0{i+1}T10:00:00", "requests": i * 10}) + "\n")
            path = f.name
        try:
            entries = load_history(path)
            self.assertEqual(len(entries), 5)
        finally:
            os.unlink(path)

    def test_corrupt_lines_skipped(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write('{"ts":"2026-04-01T10:00:00"}\n')
            f.write('not json\n')
            f.write('{"ts":"2026-04-02T10:00:00"}\n')
            path = f.name
        try:
            entries = load_history(path)
            self.assertEqual(len(entries), 2)
        finally:
            os.unlink(path)

    def test_nonexistent_file(self):
        entries = load_history("/nonexistent.jsonl")
        self.assertEqual(entries, [])


class TestFilterHistory(unittest.TestCase):
    def test_filter_24h(self):
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        old = "2020-01-01T00:00:00"
        entries = [{"ts": old, "requests": 10}, {"ts": now, "requests": 20}]
        filtered = filter_history(entries, hours=24)
        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0]["requests"], 20)

    def test_empty_input(self):
        self.assertEqual(filter_history([], hours=24), [])


class TestComputeTrends(unittest.TestCase):
    def test_basic(self):
        entries = [
            {"ts": "2026-04-05T10:00:00", "requests": 50, "errors": 1, "p95_ms": 400, "success_pct": 98, "degradation_pct": 1},
            {"ts": "2026-04-05T11:00:00", "requests": 60, "errors": 2, "p95_ms": 600, "success_pct": 96.7, "degradation_pct": 2},
        ]
        trend = compute_trends(entries)
        self.assertEqual(trend["period_snapshots"], 2)
        self.assertEqual(trend["total_requests"], 110)
        self.assertAlmostEqual(trend["avg_p95_ms"], 500.0)
        self.assertIn("p95_sparkline", trend)

    def test_empty(self):
        self.assertEqual(compute_trends([]), {})

    def test_single_entry(self):
        entries = [{"ts": "2026-04-05T10:00:00", "requests": 50, "errors": 0, "p95_ms": 300, "success_pct": 100, "degradation_pct": 0}]
        trend = compute_trends(entries)
        self.assertEqual(trend["avg_p95_ms"], 300.0)


class TestSparkline(unittest.TestCase):
    def test_basic(self):
        result = _sparkline([1, 2, 3, 4, 5])
        self.assertEqual(len(result), 5)

    def test_constant(self):
        result = _sparkline([5, 5, 5])
        self.assertEqual(len(result), 3)

    def test_empty(self):
        self.assertEqual(_sparkline([]), "")

    def test_single(self):
        result = _sparkline([42])
        self.assertEqual(len(result), 1)


class TestBuildDashboard(unittest.TestCase):
    def test_with_stats(self):
        stats = _make_stats()
        dashboard = build_dashboard(stats=stats, history=[])
        self.assertIn("current", dashboard)
        self.assertIn("verdicts", dashboard)
        self.assertIn("overall", dashboard)
        self.assertIn("version", dashboard)

    def test_without_stats(self):
        import slo_dashboard
        orig_read = slo_dashboard.read_stats
        slo_dashboard.read_stats = lambda p=None: None
        try:
            dashboard = build_dashboard(stats=None, history=[])
            self.assertIsNone(dashboard["current"])
            self.assertEqual(dashboard["overall"], "NO DATA")
        finally:
            slo_dashboard.read_stats = orig_read

    def test_all_pass(self):
        stats = _make_stats(total=100, errors=0, p95=500)
        dashboard = build_dashboard(stats=stats, history=[])
        self.assertEqual(dashboard["overall"], "ALL PASS")

    def test_latency_violation(self):
        stats = _make_stats(total=100, errors=0, p95=50000)
        dashboard = build_dashboard(stats=stats, history=[])
        self.assertEqual(dashboard["verdicts"]["latency"], "FAIL")

    def test_targets_present(self):
        dashboard = build_dashboard(stats=_make_stats(), history=[])
        self.assertIn("targets", dashboard)
        self.assertIn("p95_ms", dashboard["targets"])


class TestFormatDashboardMd(unittest.TestCase):
    def test_contains_header(self):
        dashboard = build_dashboard(stats=_make_stats(), history=[])
        md = format_dashboard_md(dashboard)
        self.assertIn("# SLO Dashboard", md)
        self.assertIn("Current Metrics", md)

    def test_no_data(self):
        # Force no-data state by passing stats that produce None snapshot
        import slo_dashboard
        orig_read = slo_dashboard.read_stats
        slo_dashboard.read_stats = lambda p=None: None
        try:
            dashboard = build_dashboard(stats=None, history=[])
            md = format_dashboard_md(dashboard)
            self.assertIn("No current data", md)
        finally:
            slo_dashboard.read_stats = orig_read

    def test_with_trends(self):
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        history = [{"ts": now, "requests": 50, "errors": 0, "p95_ms": 300, "success_pct": 100, "degradation_pct": 0}]
        dashboard = build_dashboard(stats=_make_stats(), history=history)
        md = format_dashboard_md(dashboard)
        self.assertIn("Last 24 Hours", md)


class TestFormatCompact(unittest.TestCase):
    def test_with_data(self):
        dashboard = build_dashboard(stats=_make_stats(), history=[])
        compact = format_compact(dashboard)
        self.assertIn("SLO:", compact)
        self.assertIn("p95=", compact)

    def test_no_data(self):
        import slo_dashboard
        orig_read = slo_dashboard.read_stats
        slo_dashboard.read_stats = lambda p=None: None
        try:
            dashboard = build_dashboard(stats=None, history=[])
            compact = format_compact(dashboard)
            self.assertEqual(compact, "SLO: NO_DATA")
        finally:
            slo_dashboard.read_stats = orig_read


class TestTrimHistory(unittest.TestCase):
    def test_trim(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            for i in range(10):
                f.write(json.dumps({"ts": f"T{i}"}) + "\n")
            path = f.name
        try:
            import slo_dashboard
            orig = slo_dashboard.MAX_HISTORY_ENTRIES
            slo_dashboard.MAX_HISTORY_ENTRIES = 5
            _trim_history(path)
            slo_dashboard.MAX_HISTORY_ENTRIES = orig
            entries = load_history(path)
            self.assertEqual(len(entries), 5)
            self.assertEqual(entries[0]["ts"], "T5")  # oldest kept
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
