#!/usr/bin/env python3
"""test_config_slo.py — config_loader + slo_checker 单测（V32）"""
import json
import os
import sys
import tempfile
import unittest

# 确保 config.yaml 能找到
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


class TestConfigLoader(unittest.TestCase):
    """config_loader.py 单测"""

    def test_load_config_returns_dict(self):
        from config_loader import load_config
        cfg = load_config(force_reload=True)
        self.assertIsInstance(cfg, dict)

    def test_required_sections_exist(self):
        from config_loader import load_config
        cfg = load_config()
        for section in ("slo", "proxy", "tokens", "alerts", "routing", "truncation", "watchdog", "incidents"):
            self.assertIn(section, cfg, f"Missing section: {section}")

    def test_slo_values(self):
        from config_loader import load_config
        cfg = load_config()
        slo = cfg["slo"]
        self.assertEqual(slo["latency_p95_ms"], 30000)
        self.assertEqual(slo["tool_success_rate_pct"], 95.0)
        self.assertEqual(slo["degradation_rate_pct"], 5.0)
        self.assertEqual(slo["timeout_rate_pct"], 3.0)
        self.assertEqual(slo["auto_recovery_rate_pct"], 90.0)

    def test_proxy_values(self):
        from config_loader import load_config
        cfg = load_config()
        p = cfg["proxy"]
        self.assertEqual(p["max_request_bytes"], 200000)
        self.assertEqual(p["max_tools"], 12)
        self.assertEqual(p["backend_timeout_seconds"], 300)

    def test_token_values(self):
        from config_loader import load_config
        cfg = load_config()
        t = cfg["tokens"]
        self.assertEqual(t["context_limit"], 260000)
        self.assertEqual(t["warn_threshold_pct"], 75)
        self.assertEqual(t["critical_threshold_pct"], 90)

    def test_get_helper(self):
        from config_loader import get
        self.assertEqual(get("proxy", "max_request_bytes"), 200000)
        self.assertEqual(get("nonexistent", "key", "default"), "default")

    def test_module_constants(self):
        import config_loader
        self.assertEqual(config_loader.MAX_REQUEST_BYTES, 200000)
        self.assertEqual(config_loader.CONTEXT_LIMIT, 260000)
        self.assertEqual(config_loader.TOKEN_WARN_THRESHOLD, 195000)
        self.assertEqual(config_loader.TOKEN_CRITICAL_THRESHOLD, 234000)
        self.assertEqual(config_loader.CONSECUTIVE_ERROR_ALERT, 3)


class TestSloChecker(unittest.TestCase):
    """slo_checker.py 单测"""

    def _make_stats(self, **overrides):
        base = {
            "total_requests": 100,
            "total_errors": 2,
            "slo": {
                "latency": {"p50": 1000, "p95": 5000, "p99": 8000, "max": 12000, "count": 100},
                "errors_by_type": {"timeout": 1, "context_overflow": 0, "backend": 1, "other": 0},
                "tool_success_rate_pct": 98.0,
                "degradation_rate_pct": 1.0,
                "timeout_rate_pct": 1.0,
                "auto_recovery_rate_pct": 100.0,
            }
        }
        if overrides:
            for k, v in overrides.items():
                if isinstance(v, dict) and k in base:
                    base[k].update(v)
                else:
                    base[k] = v
        return base

    def test_all_ok(self):
        from slo_checker import check_slo
        from config_loader import load_config
        stats = self._make_stats()
        results, all_ok = check_slo(stats, load_config())
        self.assertTrue(all_ok)
        self.assertEqual(len(results), 5)
        for r in results:
            self.assertTrue(r["ok"], f"SLO {r['name']} should be ok")

    def test_latency_violation(self):
        from slo_checker import check_slo
        from config_loader import load_config
        stats = self._make_stats(slo={
            "latency": {"p50": 5000, "p95": 35000, "p99": 40000, "max": 50000, "count": 100},
            "errors_by_type": {"timeout": 0, "context_overflow": 0, "backend": 0, "other": 0},
            "tool_success_rate_pct": 100.0,
            "degradation_rate_pct": 0.0,
            "timeout_rate_pct": 0.0,
            "auto_recovery_rate_pct": 100.0,
        })
        results, all_ok = check_slo(stats, load_config())
        self.assertFalse(all_ok)
        latency = [r for r in results if r["name"] == "latency_p95"][0]
        self.assertFalse(latency["ok"])
        self.assertEqual(latency["value"], 35000)

    def test_low_sample_count_skips_latency(self):
        from slo_checker import check_slo
        from config_loader import load_config
        stats = self._make_stats(slo={
            "latency": {"p50": 50000, "p95": 50000, "p99": 50000, "max": 50000, "count": 3},
            "errors_by_type": {"timeout": 0, "context_overflow": 0, "backend": 0, "other": 0},
            "tool_success_rate_pct": 100.0,
            "degradation_rate_pct": 0.0,
            "timeout_rate_pct": 0.0,
            "auto_recovery_rate_pct": 100.0,
        })
        results, all_ok = check_slo(stats, load_config())
        latency = [r for r in results if r["name"] == "latency_p95"][0]
        self.assertTrue(latency["ok"], "Should skip alert when < 5 samples")

    def test_zero_tool_calls_not_violation(self):
        """零工具调用时不应报 tool_success_rate 违规"""
        from slo_checker import check_slo
        from config_loader import load_config
        stats = self._make_stats(slo={
            "latency": {"p50": 0, "p95": 0, "p99": 0, "max": 0, "count": 0},
            "errors_by_type": {"timeout": 0, "context_overflow": 0, "backend": 0, "other": 0},
            "tool_calls_total": 0,
            "tool_success_rate_pct": 0.0,
            "degradation_rate_pct": 0.0,
            "timeout_rate_pct": 0.0,
            "auto_recovery_rate_pct": 100.0,
        })
        results, all_ok = check_slo(stats, load_config())
        tool = [r for r in results if r["name"] == "tool_success_rate"][0]
        self.assertTrue(tool["ok"], "Should not alert when tool_calls_total=0")
        self.assertTrue(all_ok)

    def test_tool_success_rate_violation(self):
        from slo_checker import check_slo
        from config_loader import load_config
        stats = self._make_stats(slo={
            "latency": {"p50": 1000, "p95": 5000, "p99": 8000, "max": 12000, "count": 50},
            "errors_by_type": {"timeout": 0, "context_overflow": 0, "backend": 0, "other": 0},
            "tool_calls_total": 100,
            "tool_success_rate_pct": 80.0,
            "degradation_rate_pct": 0.0,
            "timeout_rate_pct": 0.0,
            "auto_recovery_rate_pct": 100.0,
        })
        results, all_ok = check_slo(stats, load_config())
        self.assertFalse(all_ok)

    def test_timeout_rate_violation(self):
        from slo_checker import check_slo
        from config_loader import load_config
        stats = self._make_stats(slo={
            "latency": {"p50": 1000, "p95": 5000, "p99": 8000, "max": 12000, "count": 50},
            "errors_by_type": {"timeout": 5, "context_overflow": 0, "backend": 0, "other": 0},
            "tool_success_rate_pct": 100.0,
            "degradation_rate_pct": 0.0,
            "timeout_rate_pct": 5.0,
            "auto_recovery_rate_pct": 100.0,
        })
        results, all_ok = check_slo(stats, load_config())
        self.assertFalse(all_ok)
        timeout = [r for r in results if r["name"] == "timeout_rate"][0]
        self.assertFalse(timeout["ok"])

    def test_format_alert_no_violations(self):
        from slo_checker import format_alert
        results = [{"name": "test", "ok": True, "value": 1, "target": 10, "unit": "ms"}]
        self.assertEqual(format_alert(results), "")

    def test_format_alert_with_violations(self):
        from slo_checker import format_alert
        results = [{"name": "latency_p95", "ok": False, "value": 35000, "target": 30000, "unit": "ms"}]
        alert = format_alert(results)
        self.assertIn("SLO", alert)
        self.assertIn("latency_p95", alert)


class TestIncidentSnapshot(unittest.TestCase):
    """incident_snapshot.py 基础单测"""

    def test_import(self):
        import incident_snapshot
        self.assertTrue(hasattr(incident_snapshot, "create_snapshot"))
        self.assertTrue(hasattr(incident_snapshot, "list_snapshots"))

    def test_create_snapshot(self):
        import incident_snapshot
        with tempfile.TemporaryDirectory() as tmpdir:
            old_dir = incident_snapshot.SNAPSHOT_DIR
            incident_snapshot.SNAPSHOT_DIR = tmpdir
            try:
                path = incident_snapshot.create_snapshot("test", "unit test snapshot")
                self.assertIsNotNone(path)
                self.assertTrue(os.path.exists(path))
                with open(path) as f:
                    data = json.load(f)
                self.assertEqual(data["trigger"], "test")
                self.assertIn("logs", data)
                self.assertIn("services", data)
            finally:
                incident_snapshot.SNAPSHOT_DIR = old_dir

    def test_cleanup_respects_max(self):
        import incident_snapshot
        with tempfile.TemporaryDirectory() as tmpdir:
            old_dir = incident_snapshot.SNAPSHOT_DIR
            old_max = incident_snapshot.MAX_SNAPSHOTS
            incident_snapshot.SNAPSHOT_DIR = tmpdir
            incident_snapshot.MAX_SNAPSHOTS = 3
            try:
                for i in range(5):
                    with open(os.path.join(tmpdir, f"snap_{i:03d}.json"), "w") as f:
                        json.dump({"i": i}, f)
                incident_snapshot._cleanup_old_snapshots()
                remaining = os.listdir(tmpdir)
                self.assertLessEqual(len(remaining), 3)
            finally:
                incident_snapshot.SNAPSHOT_DIR = old_dir
                incident_snapshot.MAX_SNAPSHOTS = old_max


class TestProxyStatsSLO(unittest.TestCase):
    """ProxyStats SLO 指标追踪单测"""

    def _make_stats(self):
        from proxy_filters import ProxyStats
        return ProxyStats()

    def test_record_success_with_latency(self):
        ps = self._make_stats()
        ps.record_success({"prompt_tokens": 1000, "total_tokens": 1500}, latency_ms=500)
        self.assertEqual(ps.total_requests, 1)
        self.assertEqual(len(ps._latencies), 1)
        self.assertEqual(ps._latencies[0], 500)

    def test_record_error_classifies_timeout(self):
        ps = self._make_stats()
        ps.record_error(504, "Connection timed out", latency_ms=30000)
        self.assertEqual(ps.errors_by_type["timeout"], 1)

    def test_record_error_classifies_context(self):
        ps = self._make_stats()
        ps.record_error(403, "context length exceeded")
        self.assertEqual(ps.errors_by_type["context_overflow"], 1)

    def test_record_error_classifies_backend(self):
        ps = self._make_stats()
        ps.record_error(502, "bad gateway")
        self.assertEqual(ps.errors_by_type["backend"], 1)

    def test_latency_percentiles(self):
        ps = self._make_stats()
        for ms in [100, 200, 300, 400, 500, 1000, 2000, 3000, 5000, 10000]:
            ps.record_success({}, latency_ms=ms)
        lp = ps.get_latency_percentiles()
        self.assertGreater(lp["p95"], 0)
        self.assertGreaterEqual(lp["max"], 10000)
        self.assertEqual(lp["count"], 10)

    def test_tool_call_tracking(self):
        ps = self._make_stats()
        ps.record_tool_call(success=True)
        ps.record_tool_call(success=True)
        ps.record_tool_call(success=False)
        self.assertEqual(ps.tool_calls_total, 3)
        self.assertEqual(ps.tool_calls_success, 2)

    def test_recovery_tracking(self):
        ps = self._make_stats()
        ps.record_error(502, "error 1")
        ps.record_error(502, "error 2")
        ps.record_error(502, "error 3")
        # 此时 _failure_streaks = 1
        self.assertEqual(ps._failure_streaks, 1)
        # 恢复
        ps.record_success({})
        self.assertEqual(ps._recovery_total, 1)
        self.assertEqual(ps.consecutive_errors, 0)

    def test_get_slo_status(self):
        ps = self._make_stats()
        ps.record_success({"prompt_tokens": 1000}, latency_ms=500)
        ps.record_tool_call(success=True)
        status = ps.get_slo_status()
        self.assertIn("latency_p95_ms", status)
        self.assertIn("tool_success_rate_pct", status)
        self.assertIn("timeout_rate_pct", status)
        self.assertIn("auto_recovery_rate_pct", status)
        self.assertEqual(status["tool_success_rate_pct"], 100.0)

    def test_day_reset_clears_slo(self):
        ps = self._make_stats()
        ps.record_error(502, "error", latency_ms=1000)
        ps.record_tool_call(success=False)
        # Simulate day change
        ps._today = "1999-01-01"
        ps._check_day_reset()
        self.assertEqual(ps.errors_by_type["backend"], 0)
        self.assertEqual(ps.tool_calls_total, 0)
        self.assertEqual(ps.fallback_count, 0)

    def test_stats_dict_includes_slo(self):
        ps = self._make_stats()
        ps.record_success({}, latency_ms=1000)
        d = ps.get_stats_dict()
        self.assertIn("slo", d)
        self.assertIn("latency", d["slo"])
        self.assertIn("errors_by_type", d["slo"])

    def test_fallback_recording(self):
        ps = self._make_stats()
        ps.record_fallback()
        ps.record_fallback()
        self.assertEqual(ps.fallback_count, 2)


if __name__ == "__main__":
    unittest.main()
