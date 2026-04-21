#!/usr/bin/env python3
"""
test_audit_perf_dimensions.py — regression test for V37.9.3 audit-of-audit
multi-dimensional performance regression detection (MRD-AUDIT-PERF-001).

Background
----------
V37.9 introduced single-dimension wall_time monitoring for audit-of-audit
(MRD-AUDIT-PERF-001). V37.9.3 expands to 4 dimensions, each with its own
independent threshold:

  1. wall_time_ms   : relative 1.3x + absolute 300ms (V37.9, preserved)
  2. peak_memory_mb : relative 1.5x + absolute 10MB (V37.9.3 new)
  3. bootstrap_ms   : relative 2.0x + absolute 500ms (V37.9.3 new)
  4. skip_rate_pct  : absolute +20pct jump (V37.9.3 new)

Any single dimension regression triggers warn. Missing fields in old history
are gracefully skipped (backward compatibility).

This file locks the threshold semantics of each dimension so future edits
cannot accidentally weaken the detector.
"""

import json
import os
import sys
import tempfile
import unittest
from unittest import mock

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_ONTOLOGY_DIR = os.path.dirname(_TESTS_DIR)
_PROJECT_ROOT = os.path.dirname(_ONTOLOGY_DIR)
for p in [_ONTOLOGY_DIR, _PROJECT_ROOT]:
    if p not in sys.path:
        sys.path.insert(0, p)

import governance_checker  # noqa: E402


def _write_history(path, entries):
    with open(path, "w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")


def _baseline_history(n=5, **overrides):
    """5 healthy baseline entries. Caller can override any field."""
    base = {
        "timestamp": "2026-04-20T07:30:00",
        "wall_time_ms": 2000,
        "total_invariants": 55,
        "total_checks_executed": 270,
        "total_checks_skipped": 10,
        "pass_count": 55,
        "fail_count": 0,
        "error_count": 0,
        "discovery_count": 14,
        "peak_memory_mb": 30.0,
        "bootstrap_ms": 150,
        "skip_rate_pct": 3.7,
    }
    base.update(overrides)
    return [dict(base) for _ in range(n)]


class TestMemoryDimension(unittest.TestCase):
    """V37.9.3 peak_memory_mb: 1.5x + 10MB abs threshold."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.metrics_path = os.path.join(self.tmp.name, ".audit_metrics.jsonl")
        self._patcher_root = mock.patch.object(
            governance_checker, "_PROJECT_ROOT", self.tmp.name
        )
        self._patcher_root.start()
        # Put history in ontology/.audit_metrics.jsonl relative to project root
        os.makedirs(os.path.join(self.tmp.name, "ontology"), exist_ok=True)
        self.metrics_path = os.path.join(
            self.tmp.name, "ontology", ".audit_metrics.jsonl"
        )

    def tearDown(self):
        self._patcher_root.stop()
        self.tmp.cleanup()

    def _run_mrd(self, current_memory):
        """Call MRD with mocked current memory."""
        with mock.patch.object(
            governance_checker, "_get_peak_memory_mb", return_value=current_memory
        ):
            # Also mock time.time so wall_time / bootstrap don't spuriously fire
            with mock.patch.object(
                governance_checker, "_AUDIT_SESSION_START",
                governance_checker.time.time() - 2.0,
            ):
                return governance_checker._discover_audit_performance_regression(
                    "medium"
                )

    def test_memory_1_5x_with_abs_10mb_triggers_warn(self):
        # baseline median=30MB, current=46MB = 1.53x + 16MB abs → both triggers
        _write_history(self.metrics_path, _baseline_history(peak_memory_mb=30.0))
        result = self._run_mrd(current_memory=46.0)
        self.assertEqual(result["status"], "warn")
        self.assertIn("memory", result["message"])

    def test_memory_relative_below_1_5x_no_warn(self):
        # 30 → 40 = 1.33x (below 1.5x) → no memory warn
        _write_history(self.metrics_path, _baseline_history(peak_memory_mb=30.0))
        result = self._run_mrd(current_memory=40.0)
        # Should be pass (no dimension triggered)
        if result["status"] == "warn":
            self.assertNotIn("memory", result["message"])

    def test_memory_abs_below_10mb_no_warn(self):
        # 5MB → 9MB = 1.8x but only +4MB abs → below 10MB threshold → no warn
        _write_history(self.metrics_path, _baseline_history(peak_memory_mb=5.0))
        result = self._run_mrd(current_memory=9.0)
        if result["status"] == "warn":
            self.assertNotIn("memory", result["message"])

    def test_memory_missing_from_old_history_skipped(self):
        # Old history without peak_memory_mb field → median=0 → dimension skipped
        baseline = _baseline_history()
        for b in baseline:
            del b["peak_memory_mb"]
        _write_history(self.metrics_path, baseline)
        result = self._run_mrd(current_memory=100.0)  # Large current mem
        # Should NOT warn on memory (field missing → median 0 → skip)
        if result["status"] == "warn":
            self.assertNotIn("memory", result["message"])


class TestBootstrapDimension(unittest.TestCase):
    """V37.9.3 bootstrap_ms: 2.0x + 500ms abs threshold."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.makedirs(os.path.join(self.tmp.name, "ontology"), exist_ok=True)
        self.metrics_path = os.path.join(
            self.tmp.name, "ontology", ".audit_metrics.jsonl"
        )
        self._patcher_root = mock.patch.object(
            governance_checker, "_PROJECT_ROOT", self.tmp.name
        )
        self._patcher_root.start()

    def tearDown(self):
        self._patcher_root.stop()
        self.tmp.cleanup()

    def _run_mrd(self, current_bootstrap):
        # Pin session start so bootstrap = current_bootstrap ms
        fake_now = 1_000_000.0
        fake_first = fake_now + current_bootstrap / 1000.0
        with mock.patch.object(
            governance_checker, "_AUDIT_SESSION_START", fake_now
        ), mock.patch.object(
            governance_checker, "_AUDIT_FIRST_CHECK_TIME", fake_first
        ), mock.patch.object(
            governance_checker.time, "time", return_value=fake_first + 0.1
        ):
            return governance_checker._discover_audit_performance_regression(
                "medium"
            )

    def test_bootstrap_2x_with_abs_500ms_triggers_warn(self):
        # median=150ms, current=1700ms = 11.3x + 1550ms abs → both triggers
        _write_history(self.metrics_path, _baseline_history(bootstrap_ms=150))
        result = self._run_mrd(current_bootstrap=1700)
        self.assertEqual(result["status"], "warn")
        self.assertIn("bootstrap", result["message"])

    def test_bootstrap_relative_below_2x_no_warn(self):
        # 1000ms → 1800ms = 1.8x (below 2.0x) → no bootstrap warn
        _write_history(self.metrics_path, _baseline_history(bootstrap_ms=1000))
        result = self._run_mrd(current_bootstrap=1800)
        if result["status"] == "warn":
            self.assertNotIn("bootstrap", result["message"])

    def test_bootstrap_abs_below_500ms_no_warn(self):
        # 100ms → 400ms = 4x but only +300ms abs → below 500ms threshold
        _write_history(self.metrics_path, _baseline_history(bootstrap_ms=100))
        result = self._run_mrd(current_bootstrap=400)
        if result["status"] == "warn":
            self.assertNotIn("bootstrap", result["message"])


class TestSkipRateDimension(unittest.TestCase):
    """V37.9.3 skip_rate_pct: absolute +20pct jump from median."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.makedirs(os.path.join(self.tmp.name, "ontology"), exist_ok=True)
        self.metrics_path = os.path.join(
            self.tmp.name, "ontology", ".audit_metrics.jsonl"
        )
        self._patcher_root = mock.patch.object(
            governance_checker, "_PROJECT_ROOT", self.tmp.name
        )
        self._patcher_root.start()

    def tearDown(self):
        self._patcher_root.stop()
        self.tmp.cleanup()

    def _run_mrd(self):
        fake_now = 1_000_000.0
        with mock.patch.object(
            governance_checker, "_AUDIT_SESSION_START", fake_now
        ), mock.patch.object(
            governance_checker, "_AUDIT_FIRST_CHECK_TIME", fake_now + 0.1
        ), mock.patch.object(
            governance_checker.time, "time", return_value=fake_now + 0.5
        ), mock.patch.object(
            governance_checker, "_get_peak_memory_mb", return_value=30.0
        ):
            return governance_checker._discover_audit_performance_regression(
                "medium"
            )

    def test_skip_rate_plus_20pct_triggers_warn(self):
        # Baseline 4% median, last entry shows 30% → +26pct jump → trigger
        hist = _baseline_history(skip_rate_pct=4.0)
        # Last entry: skip jumped
        hist[-1]["skip_rate_pct"] = 30.0
        _write_history(self.metrics_path, hist)
        result = self._run_mrd()
        self.assertEqual(result["status"], "warn")
        self.assertIn("skip_rate", result["message"])

    def test_skip_rate_plus_10pct_no_warn(self):
        hist = _baseline_history(skip_rate_pct=4.0)
        hist[-1]["skip_rate_pct"] = 14.0  # +10pct (below 20pct)
        _write_history(self.metrics_path, hist)
        result = self._run_mrd()
        if result["status"] == "warn":
            self.assertNotIn("skip_rate", result["message"])


class TestBackwardCompatibility(unittest.TestCase):
    """V37.9.3: New dimensions must not break old history files."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.makedirs(os.path.join(self.tmp.name, "ontology"), exist_ok=True)
        self.metrics_path = os.path.join(
            self.tmp.name, "ontology", ".audit_metrics.jsonl"
        )
        self._patcher_root = mock.patch.object(
            governance_checker, "_PROJECT_ROOT", self.tmp.name
        )
        self._patcher_root.start()

    def tearDown(self):
        self._patcher_root.stop()
        self.tmp.cleanup()

    def test_old_history_without_new_fields_still_passes(self):
        # Old V37.9 history with ONLY wall_time_ms (no mem/bootstrap/skip)
        old_entries = []
        for _ in range(5):
            old_entries.append({
                "timestamp": "2026-04-18T07:30:00",
                "wall_time_ms": 2000,
                "total_invariants": 55,
                "total_checks_executed": 270,
                "total_checks_skipped": 10,
                "pass_count": 55,
                "fail_count": 0,
                "error_count": 0,
                "discovery_count": 14,
            })
        _write_history(self.metrics_path, old_entries)
        fake_now = 1_000_000.0
        with mock.patch.object(
            governance_checker, "_AUDIT_SESSION_START", fake_now
        ), mock.patch.object(
            governance_checker, "_AUDIT_FIRST_CHECK_TIME", fake_now + 0.15
        ), mock.patch.object(
            governance_checker.time, "time", return_value=fake_now + 2.0
        ), mock.patch.object(
            governance_checker, "_get_peak_memory_mb", return_value=35.0
        ):
            result = governance_checker._discover_audit_performance_regression(
                "medium"
            )
        # Must NOT crash; must NOT warn on missing dimensions
        self.assertIn(result["status"], ("pass", "warn"))
        if result["status"] == "warn":
            # Only allowed warns are wall_time or check_count, never mem/boot/skip
            self.assertNotIn("memory", result["message"])
            self.assertNotIn("bootstrap", result["message"])
            self.assertNotIn("skip_rate", result["message"])


class TestMetricWriteIntegration(unittest.TestCase):
    """Integration: _write_audit_metrics must include all 3 new fields."""

    def test_write_includes_three_new_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "ontology"), exist_ok=True)
            metrics_path = os.path.join(tmp, "ontology", ".audit_metrics.jsonl")
            with mock.patch.object(
                governance_checker, "_PROJECT_ROOT", tmp
            ), mock.patch.object(
                governance_checker, "_AUDIT_FIRST_CHECK_TIME",
                governance_checker.time.time() + 0.1,
            ):
                results = [{
                    "status": "pass",
                    "total_checks": 10,
                    "passed_checks": 9,
                }]
                governance_checker._write_audit_metrics(results, [])
            self.assertTrue(os.path.exists(metrics_path))
            with open(metrics_path) as f:
                lines = f.readlines()
            self.assertTrue(lines)
            entry = json.loads(lines[-1])
            # All three new fields must exist
            self.assertIn("peak_memory_mb", entry)
            self.assertIn("bootstrap_ms", entry)
            self.assertIn("skip_rate_pct", entry)
            # skip_rate derived correctly: (10-9)/10 * 100 = 10.0%
            self.assertEqual(entry["skip_rate_pct"], 10.0)
            # memory and bootstrap should be non-negative numbers
            self.assertGreaterEqual(entry["peak_memory_mb"], 0)
            self.assertGreaterEqual(entry["bootstrap_ms"], 0)


class TestGetPeakMemoryCrossplatform(unittest.TestCase):
    """V37.9.3 _get_peak_memory_mb handles macOS (bytes) vs Linux (KB) correctly."""

    def test_returns_float_or_zero(self):
        val = governance_checker._get_peak_memory_mb()
        self.assertIsInstance(val, (int, float))
        self.assertGreaterEqual(val, 0)

    def test_macos_bytes_conversion(self):
        with mock.patch.object(governance_checker.sys, "platform", "darwin"):
            # Mock resource.getrusage to return 50MB in bytes
            class FakeRu:
                ru_maxrss = 50 * 1024 * 1024
            with mock.patch("resource.getrusage", return_value=FakeRu()):
                val = governance_checker._get_peak_memory_mb()
            self.assertAlmostEqual(val, 50.0, places=1)

    def test_linux_kb_conversion(self):
        with mock.patch.object(governance_checker.sys, "platform", "linux"):
            class FakeRu:
                ru_maxrss = 50 * 1024  # 50 MB in KB
            with mock.patch("resource.getrusage", return_value=FakeRu()):
                val = governance_checker._get_peak_memory_mb()
            self.assertAlmostEqual(val, 50.0, places=1)


if __name__ == "__main__":
    unittest.main()
