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
                fallback=1, tool_total=50, tool_success=48, updated=None):
    """Helper to create a proxy_stats-like dict.

    V37.9.303: fixture 忠实于 producer schema (proxy_filters._write_stats) —
    token 键是 last_prompt_tokens/max_prompt_tokens_today + updated 时间戳;
    顶层 prompt_tokens/total_tokens 从不被 producer 写出 (V37.9.299 fake-faithful 教训)。
    """
    return {
        "updated": updated if updated is not None else time.strftime("%Y-%m-%d %H:%M:%S"),
        "total_requests": total,
        "total_errors": errors,
        "last_prompt_tokens": 50000,
        "last_total_tokens": 80000,
        "max_prompt_tokens_today": 60000,
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
        """V37.9.303 (审计 F1): requests/errors 是日累计 gauge, 窗口总量 = 相邻快照增量和
        (50→60 = 10 新请求), 不是 sum(快照) = 110 (旧口径把同一请求重复计数 ~12×/天)."""
        entries = [
            {"ts": "2026-04-05T10:00:00", "requests": 50, "errors": 1, "p95_ms": 400, "success_pct": 98, "degradation_pct": 1},
            {"ts": "2026-04-05T11:00:00", "requests": 60, "errors": 2, "p95_ms": 600, "success_pct": 96.7, "degradation_pct": 2},
        ]
        trend = compute_trends(entries)
        self.assertEqual(trend["period_snapshots"], 2)
        self.assertEqual(trend["total_requests"], 10)
        self.assertEqual(trend["total_errors"], 1)
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
        # V37.9.79: 阈值调整 30000 → 50000ms, 输入需 > 50000ms 才触发 FAIL
        # 用 60000ms (Mac Mini 实测 p99=53339ms 量级, 是真实 violation 场景)
        stats = _make_stats(total=100, errors=0, p95=60000)
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


class TestV379303DashboardHonesty(unittest.TestCase):
    """V37.9.303 SLO 读侧口径审计守卫 (F1 计数器增量 / F2 token key / F4 幽灵行+staleness / F5 零数据 N/A)."""

    def test_fixture_is_producer_faithful(self):
        """守卫的守卫: fixture 不得回退到 producer 从不写的顶层 prompt_tokens/total_tokens."""
        stats = _make_stats()
        self.assertNotIn("prompt_tokens", stats)
        self.assertNotIn("total_tokens", stats)
        self.assertIn("last_prompt_tokens", stats)
        self.assertIn("updated", stats)

    def test_snapshot_carries_real_token_keys_and_stats_updated(self):
        """审计 F2/F4: 快照读 producer 真实 token key + 携带数据时刻 stats_updated."""
        snap = extract_snapshot(_make_stats(updated="2026-08-14 10:00:00"))
        self.assertEqual(snap["last_prompt_tokens"], 50000)
        self.assertEqual(snap["max_prompt_tokens_today"], 60000)
        self.assertEqual(snap["stats_updated"], "2026-08-14 10:00:00")
        self.assertNotIn("prompt_tokens", snap)
        self.assertNotIn("total_tokens", snap)

    def test_counter_delta_across_daily_reset(self):
        """审计 F1: 跨日重置 (cur < prev) → 计 cur (重置后新增量), 不产生负数/虚报.
        24(昨日累计) → 3(今晨重置后) → 8: 窗口增量 = 3 + 5 = 8."""
        entries = [
            {"ts": "2026-08-13T23:00:00", "requests": 24, "errors": 2, "p95_ms": 400, "success_pct": 98, "degradation_pct": 0},
            {"ts": "2026-08-14T00:05:00", "requests": 3, "errors": 0, "p95_ms": 400, "success_pct": 100, "degradation_pct": 0},
            {"ts": "2026-08-14T01:05:00", "requests": 8, "errors": 1, "p95_ms": 400, "success_pct": 95, "degradation_pct": 0},
        ]
        trend = compute_trends(entries)
        self.assertEqual(trend["total_requests"], 8)
        self.assertEqual(trend["total_errors"], 1)

    def test_counter_delta_single_entry_is_baseline_only(self):
        """单条快照仅作 baseline (增量不可观测) → 0, 诚实欠计优于 12× 虚报."""
        entries = [{"ts": "2026-08-14T10:00:00", "requests": 50, "errors": 5, "p95_ms": 300, "success_pct": 90, "degradation_pct": 0}]
        trend = compute_trends(entries)
        self.assertEqual(trend["total_requests"], 0)
        self.assertEqual(trend["total_errors"], 0)

    def test_degradation_avg_excludes_zero_traffic_snapshots(self):
        """审计 F1 次级: 零流量快照 (degradation 恒 0) 不稀释降级率均值."""
        entries = [
            {"ts": "2026-08-14T10:00:00", "requests": 10, "errors": 0, "p95_ms": 300, "success_pct": 100, "degradation_pct": 8.0},
            {"ts": "2026-08-14T11:00:00", "requests": 0, "errors": 0, "p95_ms": 0, "success_pct": 0, "degradation_pct": 0.0},
        ]
        trend = compute_trends(entries)
        self.assertAlmostEqual(trend["avg_degradation_pct"], 8.0)

    def test_ghost_snapshot_dedup(self):
        """审计 F4: producer updated 未变 (proxy 挂/零流量) → 不追加幽灵快照行."""
        import slo_dashboard
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(_make_stats(updated="2026-08-14 09:00:00"), f)
            stats_path = f.name
        with tempfile.TemporaryDirectory() as tmpdir:
            history_path = os.path.join(tmpdir, "history.jsonl")
            orig = slo_dashboard._history_path
            slo_dashboard._history_path = lambda: history_path
            try:
                snap1 = take_snapshot(stats_path)
                self.assertIsNotNone(snap1)
                snap2 = take_snapshot(stats_path)  # 同一 updated → 幽灵行, 应跳过
                self.assertIsNone(snap2)
                self.assertEqual(len(load_history(history_path)), 1)
            finally:
                slo_dashboard._history_path = orig
                os.unlink(stats_path)

    def test_zero_data_verdicts_na_not_all_pass(self):
        """审计 F5: 空 stats (p95==0/requests==0) → verdicts N/A + overall N/A,
        零信息不再渲染 ALL PASS (V37.9.288 B-F2 '[] 双关语义' 家族)."""
        dashboard = build_dashboard(stats={"total_requests": 0, "total_errors": 0, "slo": {}},
                                    history=[], config={})
        v = dashboard["verdicts"]
        self.assertEqual(v["latency"], "N/A")
        self.assertEqual(v["success"], "N/A")
        self.assertEqual(v["degradation"], "N/A")
        self.assertEqual(dashboard["overall"], "N/A")

    def test_stale_stats_override_overall(self):
        """审计 F4: updated 超龄 (> watchdog.stats_stale_hours 默认 4h) → overall=STALE_DATA
        + md 渲染陈旧警示, 冻结数据不当当前健康."""
        from datetime import datetime, timedelta
        old = (datetime.now() - timedelta(hours=6)).strftime("%Y-%m-%d %H:%M:%S")
        dashboard = build_dashboard(stats=_make_stats(updated=old), history=[], config={})
        self.assertTrue(dashboard["stats_stale"])
        self.assertEqual(dashboard["overall"], "STALE_DATA")
        md = format_dashboard_md(dashboard)
        self.assertIn("数据陈旧", md)

    def test_fresh_stats_normal_verdict(self):
        """新鲜 updated → 正常判定不受 staleness 影响."""
        dashboard = build_dashboard(stats=_make_stats(), history=[], config={})
        self.assertFalse(dashboard["stats_stale"])
        self.assertNotEqual(dashboard["overall"], "STALE_DATA")

    def test_missing_updated_fail_open(self):
        """updated 缺失 (旧 schema) → 不判 stale (FAIL-OPEN)."""
        stats = _make_stats()
        del stats["updated"]
        dashboard = build_dashboard(stats=stats, history=[], config={})
        self.assertFalse(dashboard["stats_stale"])
        self.assertIsNone(dashboard["data_age_min"])


if __name__ == "__main__":
    unittest.main()
