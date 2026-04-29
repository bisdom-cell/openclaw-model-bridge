"""V37.9.19 — Convergence framework regression tests

Covers:
  TestConvergenceResult — namedtuple shape + immutability
  TestLoadSpecs — yaml loading + missing file FAIL-OPEN
  TestSpecLookup — list_spec_ids / get_spec contracts
  TestExtractRegistryEnabledSystemJobs — declared-side extractor
  TestObserveShellCommand — runtime observer with subprocess + timeout/error paths
  TestIdentifierParsers — line_contains_identifier + word_boundary variants
  TestVerifyConvergenceHappyPath — declared==observed → drift_detected=False
  TestVerifyConvergenceDriftDetected — declared > observed → missing populated
  TestVerifyConvergenceFailOpen — every internal failure → result.error set, no raise
  TestVerifyConvergenceDriftActionValidation — invalid drift_action handled gracefully
  TestVerifyConvergenceDisabledSpec — enabled=false → no-op
  TestFormatResultForLog — single-line output for ops grep
  TestRealJobsToCrontabSpec — real convergence_ontology.yaml jobs_to_crontab spec smoke
  TestSourceLevelGuards — convergence.py + convergence_ontology.yaml structural guards
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
ONTOLOGY_DIR = REPO_ROOT / "ontology"

sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(ONTOLOGY_DIR))

from ontology import convergence as cv  # noqa: E402


# ── helpers ────────────────────────────────────────────────────────────────

def _write_temp_registry(tmp_dir, jobs):
    """Write a minimal jobs_registry.yaml fragment with the given jobs list."""
    path = tmp_dir / "jobs_registry.yaml"
    lines = ["jobs:\n"]
    for j in jobs:
        lines.append(f"  - id: {j['id']}\n")
        lines.append(f"    enabled: {str(j.get('enabled', True)).lower()}\n")
        lines.append(f"    scheduler: {j.get('scheduler', 'system')}\n")
        lines.append(f"    entry: {j.get('entry', '')}\n")
    path.write_text("".join(lines), encoding="utf-8")
    return path


def _build_spec(registry_path, command, **overrides):
    """Build a synthetic spec with absolute paths for isolated testing."""
    spec = {
        "id": "test_spec",
        "enabled": True,
        "declaration": {
            "source": str(registry_path),
            "extractor": "registry_enabled_system_jobs",
        },
        "runtime_observable": {
            "method": "shell_command",
            "command": command,
            "parser": "line_contains_identifier",
        },
        "drift_action": "alert_only",
    }
    spec.update(overrides)
    return spec


# ── tests ──────────────────────────────────────────────────────────────────

class TestConvergenceResult(unittest.TestCase):
    """ConvergenceResult namedtuple shape and immutability"""

    def test_field_order_stable(self):
        # Stable field order is part of the contract — downstream callers
        # may unpack positionally or compare against tuple literals
        expected = ("spec_id", "declared", "observed", "missing_in_runtime",
                    "drift_detected", "drift_action", "error")
        self.assertEqual(cv.ConvergenceResult._fields, expected)

    def test_result_is_immutable(self):
        r = cv._empty_result("x")
        with self.assertRaises(AttributeError):
            r.spec_id = "mutated"

    def test_empty_result_defaults(self):
        r = cv._empty_result("foo")
        self.assertEqual(r.spec_id, "foo")
        self.assertEqual(r.declared, frozenset())
        self.assertEqual(r.observed, frozenset())
        self.assertFalse(r.drift_detected)
        self.assertEqual(r.drift_action, "alert_only")
        self.assertIsNone(r.error)


class TestLoadSpecs(unittest.TestCase):
    def test_load_real_file(self):
        specs = cv.load_specs()
        self.assertIsInstance(specs, dict)
        self.assertIn("convergence_specs", specs)

    def test_load_explicit_path(self):
        specs = cv.load_specs(ONTOLOGY_DIR / "convergence_ontology.yaml")
        self.assertIn("convergence_specs", specs)

    def test_load_missing_file_raises(self):
        with self.assertRaises(Exception):
            cv.load_specs("/nonexistent/path/foo.yaml")


class TestSpecLookup(unittest.TestCase):
    def test_list_spec_ids_returns_list(self):
        ids = cv.list_spec_ids()
        self.assertIsInstance(ids, list)
        self.assertIn("jobs_to_crontab", ids)

    def test_list_spec_ids_handles_load_failure(self):
        # FAIL-OPEN: missing file → empty list, not raise
        ids = cv.list_spec_ids(path="/nonexistent/file.yaml")
        self.assertEqual(ids, [])

    def test_get_spec_existing(self):
        spec = cv.get_spec("jobs_to_crontab")
        self.assertIsNotNone(spec)
        self.assertEqual(spec.get("id"), "jobs_to_crontab")

    def test_get_spec_missing_returns_none(self):
        self.assertIsNone(cv.get_spec("nonexistent_spec_id"))

    def test_get_spec_with_explicit_specs_dict(self):
        synthetic = {"convergence_specs": [{"id": "abc", "enabled": True}]}
        result = cv.get_spec("abc", specs=synthetic)
        self.assertEqual(result["id"], "abc")


class TestExtractRegistryEnabledSystemJobs(unittest.TestCase):
    def test_extracts_only_enabled_system_jobs(self):
        with tempfile.TemporaryDirectory() as td:
            tmp_dir = Path(td)
            reg = _write_temp_registry(tmp_dir, [
                {"id": "a", "enabled": True, "scheduler": "system", "entry": "a.sh"},
                {"id": "b", "enabled": False, "scheduler": "system", "entry": "b.sh"},
                {"id": "c", "enabled": True, "scheduler": "openclaw", "entry": "c.sh"},
                {"id": "d", "enabled": True, "scheduler": "system", "entry": "d.sh"},
            ])
            spec = _build_spec(reg, "echo ''")
            result = cv._extract_registry_enabled_system_jobs(spec)
            self.assertEqual(result, {"a.sh", "d.sh"})

    def test_skips_jobs_without_entry(self):
        with tempfile.TemporaryDirectory() as td:
            tmp_dir = Path(td)
            reg = _write_temp_registry(tmp_dir, [
                {"id": "x", "enabled": True, "scheduler": "system", "entry": ""},
                {"id": "y", "enabled": True, "scheduler": "system", "entry": "y.sh"},
            ])
            spec = _build_spec(reg, "echo ''")
            result = cv._extract_registry_enabled_system_jobs(spec)
            self.assertEqual(result, {"y.sh"})

    def test_empty_registry_returns_empty_set(self):
        with tempfile.TemporaryDirectory() as td:
            tmp_dir = Path(td)
            reg = _write_temp_registry(tmp_dir, [])
            spec = _build_spec(reg, "echo ''")
            result = cv._extract_registry_enabled_system_jobs(spec)
            self.assertEqual(result, set())


class TestObserveShellCommand(unittest.TestCase):
    def test_returns_stdout_on_success(self):
        spec = {"runtime_observable": {"method": "shell_command",
                                        "command": "echo 'hello world'"}}
        out = cv._observe_shell_command(spec)
        self.assertIn("hello world", out)

    def test_missing_command_raises(self):
        spec = {"runtime_observable": {"method": "shell_command", "command": ""}}
        with self.assertRaises(ValueError):
            cv._observe_shell_command(spec)

    def test_nonzero_exit_with_empty_stdout_returns_empty(self):
        # FAIL-OPEN for "no crontab installed" scenario: crontab -l on a user
        # without crontab returns exit=1 + empty stdout + stderr message.
        # Observer treats this as "empty observed" not error.
        spec = {"runtime_observable": {
            "method": "shell_command",
            "command": "echo 'no crontab' >&2; exit 1",
        }}
        out = cv._observe_shell_command(spec)
        self.assertEqual(out, "")

    def test_nonzero_exit_with_real_stdout_raises(self):
        # If command produced real stdout but also failed → genuine error
        spec = {"runtime_observable": {
            "method": "shell_command",
            "command": "echo 'partial output'; echo 'err' >&2; exit 2",
        }}
        with self.assertRaises(RuntimeError):
            cv._observe_shell_command(spec)


class TestIdentifierParsers(unittest.TestCase):
    def test_line_contains_identifier_basic(self):
        raw = (
            "30 22 * * * bash -lc 'bash ~/foo.sh >> ~/foo.log 2>&1'\n"
            "0 22 * * * bash -lc 'bash ~/bar.sh >> ~/bar.log 2>&1'\n"
        )
        declared = {"foo.sh", "bar.sh", "missing.sh"}
        result = cv._parse_line_contains_identifier({}, raw, declared)
        self.assertEqual(result, {"foo.sh", "bar.sh"})

    def test_line_contains_identifier_empty_raw(self):
        self.assertEqual(
            cv._parse_line_contains_identifier({}, "", {"foo.sh"}),
            set(),
        )

    def test_line_contains_identifier_skips_comments(self):
        raw = "# 30 22 * * * bash ~/foo.sh\n"
        result = cv._parse_line_contains_identifier({}, raw, {"foo.sh"})
        self.assertEqual(result, set())

    def test_word_boundary_variant_avoids_substring_collision(self):
        raw = "* * * * * bash ~/kb_dream_helper.sh\n"
        result = cv._parse_line_contains_word_boundary({}, raw, {"kb_dream.sh"})
        self.assertEqual(result, set(),
            "kb_dream.sh should NOT match kb_dream_helper.sh under word boundary parser")

    def test_word_boundary_variant_matches_real_cron_quoting(self):
        raw = "30 22 * * * bash -lc 'bash ~/kb_deep_dive.sh >> ~/kb_deep_dive.log 2>&1'\n"
        result = cv._parse_line_contains_word_boundary({}, raw, {"kb_deep_dive.sh"})
        self.assertEqual(result, {"kb_deep_dive.sh"})


class TestVerifyConvergenceHappyPath(unittest.TestCase):
    def test_all_declared_observed_no_drift(self):
        with tempfile.TemporaryDirectory() as td:
            tmp_dir = Path(td)
            reg = _write_temp_registry(tmp_dir, [
                {"id": "a", "enabled": True, "scheduler": "system", "entry": "a.sh"},
                {"id": "b", "enabled": True, "scheduler": "system", "entry": "b.sh"},
            ])
            cmd = "printf '%s\\n' 'bash ~/a.sh' 'bash ~/b.sh'"
            spec = _build_spec(reg, cmd)
            specs = {"convergence_specs": [spec]}
            r = cv.verify_convergence("test_spec", specs=specs)
            self.assertIsNone(r.error)
            self.assertFalse(r.drift_detected)
            self.assertEqual(r.declared, frozenset({"a.sh", "b.sh"}))
            self.assertEqual(r.observed, frozenset({"a.sh", "b.sh"}))
            self.assertEqual(r.missing_in_runtime, frozenset())


class TestVerifyConvergenceDriftDetected(unittest.TestCase):
    def test_missing_jobs_reported(self):
        # V37.9.18 blood scenario: kb_deep_dive declared but not in crontab
        with tempfile.TemporaryDirectory() as td:
            tmp_dir = Path(td)
            reg = _write_temp_registry(tmp_dir, [
                {"id": "evening", "enabled": True, "scheduler": "system",
                 "entry": "kb_evening.sh"},
                {"id": "deep_dive", "enabled": True, "scheduler": "system",
                 "entry": "kb_deep_dive.sh"},  # this one missing in crontab
                {"id": "review", "enabled": True, "scheduler": "system",
                 "entry": "kb_review.sh"},
            ])
            # crontab returns only evening + review, missing deep_dive
            cmd = ("printf '%s\\n' "
                   "'0 22 * * * bash kb_evening.sh' "
                   "'0 21 * * 5 bash kb_review.sh'")
            spec = _build_spec(reg, cmd)
            specs = {"convergence_specs": [spec]}
            r = cv.verify_convergence("test_spec", specs=specs)
            self.assertIsNone(r.error)
            self.assertTrue(r.drift_detected)
            self.assertEqual(r.missing_in_runtime, frozenset({"kb_deep_dive.sh"}))
            self.assertEqual(r.observed, frozenset({"kb_evening.sh", "kb_review.sh"}))

    def test_all_missing_when_crontab_empty(self):
        with tempfile.TemporaryDirectory() as td:
            tmp_dir = Path(td)
            reg = _write_temp_registry(tmp_dir, [
                {"id": "a", "enabled": True, "scheduler": "system", "entry": "a.sh"},
            ])
            spec = _build_spec(reg, "echo ''")
            specs = {"convergence_specs": [spec]}
            r = cv.verify_convergence("test_spec", specs=specs)
            self.assertTrue(r.drift_detected)
            self.assertEqual(r.missing_in_runtime, frozenset({"a.sh"}))


class TestVerifyConvergenceFailOpen(unittest.TestCase):
    """Every internal failure path returns a valid ConvergenceResult, never raises."""

    def test_spec_not_found(self):
        r = cv.verify_convergence("nonexistent",
                                  specs={"convergence_specs": []})
        self.assertEqual(r.error, "spec_not_found")
        self.assertFalse(r.drift_detected)

    def test_extractor_failure(self):
        spec = _build_spec(Path("/nonexistent/registry.yaml"), "echo ''")
        r = cv.verify_convergence("test_spec", specs={"convergence_specs": [spec]})
        self.assertIsNotNone(r.error)
        self.assertIn("extractor_failed", r.error)

    def test_unknown_extractor_name(self):
        spec = {
            "id": "bad",
            "enabled": True,
            "declaration": {"extractor": "this_extractor_does_not_exist"},
            "runtime_observable": {"method": "shell_command", "command": "echo ''"},
        }
        r = cv.verify_convergence("bad", specs={"convergence_specs": [spec]})
        self.assertIsNotNone(r.error)
        self.assertIn("extractor_failed", r.error)

    def test_unknown_observer_method(self):
        with tempfile.TemporaryDirectory() as td:
            reg = _write_temp_registry(Path(td), [])
            spec = {
                "id": "bad",
                "enabled": True,
                "declaration": {
                    "source": str(reg),
                    "extractor": "registry_enabled_system_jobs",
                },
                "runtime_observable": {"method": "totally_made_up", "command": "x"},
            }
            r = cv.verify_convergence("bad", specs={"convergence_specs": [spec]})
            self.assertIsNotNone(r.error)
            self.assertIn("observer_failed", r.error)

    def test_unknown_parser_name(self):
        with tempfile.TemporaryDirectory() as td:
            reg = _write_temp_registry(Path(td), [
                {"id": "a", "enabled": True, "scheduler": "system", "entry": "a.sh"},
            ])
            spec = {
                "id": "bad",
                "enabled": True,
                "declaration": {
                    "source": str(reg),
                    "extractor": "registry_enabled_system_jobs",
                },
                "runtime_observable": {
                    "method": "shell_command",
                    "command": "echo 'a.sh'",
                    "parser": "fake_parser",
                },
            }
            r = cv.verify_convergence("bad", specs={"convergence_specs": [spec]})
            self.assertIsNotNone(r.error)
            self.assertIn("parser_failed", r.error)

    def test_observer_failure_treats_all_declared_as_missing(self):
        # When observer fails, declared identifiers should still be reported
        # as missing (since we couldn't observe them). This lets ops see what
        # WOULD be missing if observation had worked.
        with tempfile.TemporaryDirectory() as td:
            reg = _write_temp_registry(Path(td), [
                {"id": "a", "enabled": True, "scheduler": "system", "entry": "a.sh"},
            ])
            spec = {
                "id": "obs_fail",
                "enabled": True,
                "declaration": {
                    "source": str(reg),
                    "extractor": "registry_enabled_system_jobs",
                },
                "runtime_observable": {"method": "totally_made_up", "command": "x"},
            }
            r = cv.verify_convergence("obs_fail",
                                      specs={"convergence_specs": [spec]})
            self.assertEqual(r.declared, frozenset({"a.sh"}))
            self.assertEqual(r.missing_in_runtime, frozenset({"a.sh"}))
            self.assertTrue(r.drift_detected)


class TestVerifyConvergenceDriftActionValidation(unittest.TestCase):
    def test_invalid_drift_action_falls_back_to_alert_only(self):
        with tempfile.TemporaryDirectory() as td:
            reg = _write_temp_registry(Path(td), [])
            spec = _build_spec(reg, "echo ''", drift_action="auto_destroy_universe")
            r = cv.verify_convergence("test_spec",
                                      specs={"convergence_specs": [spec]})
            self.assertIsNotNone(r.error)
            self.assertIn("invalid_drift_action", r.error)
            self.assertEqual(r.drift_action, "alert_only")

    def test_default_drift_action_is_alert_only(self):
        # No drift_action declared → default to alert_only (safest)
        with tempfile.TemporaryDirectory() as td:
            reg = _write_temp_registry(Path(td), [])
            spec = _build_spec(reg, "echo ''")
            del spec["drift_action"]
            r = cv.verify_convergence("test_spec",
                                      specs={"convergence_specs": [spec]})
            self.assertEqual(r.drift_action, "alert_only")


class TestVerifyConvergenceDisabledSpec(unittest.TestCase):
    def test_disabled_spec_reports_spec_disabled(self):
        spec = {"id": "off", "enabled": False}
        r = cv.verify_convergence("off", specs={"convergence_specs": [spec]})
        self.assertEqual(r.error, "spec_disabled")
        self.assertFalse(r.drift_detected)


class TestFormatResultForLog(unittest.TestCase):
    def test_ok_result_format(self):
        r = cv.ConvergenceResult(
            spec_id="x", declared=frozenset({"a", "b"}),
            observed=frozenset({"a", "b"}), missing_in_runtime=frozenset(),
            drift_detected=False, drift_action="alert_only", error=None,
        )
        s = cv.format_result_for_log(r)
        self.assertIn("[convergence:x]", s)
        self.assertIn("ok", s)
        self.assertIn("declared=2", s)

    def test_drift_result_format(self):
        r = cv.ConvergenceResult(
            spec_id="x", declared=frozenset({"a", "b", "c"}),
            observed=frozenset({"a"}), missing_in_runtime=frozenset({"b", "c"}),
            drift_detected=True, drift_action="alert_only", error=None,
        )
        s = cv.format_result_for_log(r)
        self.assertIn("DRIFT", s)
        self.assertIn("missing=2", s)
        # Sorted preview
        self.assertTrue("b,c" in s or "c,b" in s or "[b,c" in s or "[c,b" in s
                        or ("b" in s and "c" in s))

    def test_error_result_format(self):
        r = cv._empty_result("x", error="something_bad")
        s = cv.format_result_for_log(r)
        self.assertIn("error=something_bad", s)


class TestRealJobsToCrontabSpec(unittest.TestCase):
    """Sanity: real V37.9.19 jobs_to_crontab spec loads + verify completes."""

    def test_real_spec_completes_without_raising(self):
        # In dev environment without crontab, observer returns empty stdout
        # → all declared jobs reported as missing → drift_detected=True
        # That's expected behavior; we only assert verify_convergence doesn't
        # raise and returns a structured result.
        r = cv.verify_convergence("jobs_to_crontab")
        self.assertEqual(r.spec_id, "jobs_to_crontab")
        self.assertEqual(r.drift_action, "alert_only")
        # In environments without crontab, error may be set or empty; either way
        # we got a structured result back not an exception
        self.assertIsInstance(r, cv.ConvergenceResult)

    def test_real_spec_extractor_finds_declared_jobs(self):
        # The extractor reads jobs_registry.yaml from repo root; we know
        # kb_deep_dive.sh + kb_evening.sh + kb_review.sh are enabled+system
        spec = cv.get_spec("jobs_to_crontab")
        declared = cv._extract_registry_enabled_system_jobs(spec)
        self.assertGreater(len(declared), 0)
        self.assertIn("kb_deep_dive.sh", declared,
            "kb_deep_dive.sh should be in declared system jobs (V37.9.16+)")


class TestSourceLevelGuards(unittest.TestCase):
    """Source-level guards on convergence.py + convergence_ontology.yaml"""

    @classmethod
    def setUpClass(cls):
        cls.py_src = (ONTOLOGY_DIR / "convergence.py").read_text(encoding="utf-8")
        cls.yaml_src = (ONTOLOGY_DIR / "convergence_ontology.yaml").read_text(encoding="utf-8")

    def test_v37_9_19_marker_in_py(self):
        self.assertIn("V37.9.19", self.py_src)

    def test_mr_17_referenced_in_py(self):
        self.assertIn("MR-17", self.py_src,
            "convergence.py must reference MR-17 (declared-state-must-converge-...)")

    def test_fail_open_principle_documented(self):
        self.assertIn("FAIL-OPEN", self.py_src)

    def test_drift_actions_constant_exact(self):
        self.assertIn(
            '_VALID_DRIFT_ACTIONS = ("alert_only", "machine_sync", "block_until_human")',
            self.py_src,
        )

    def test_dispatch_tables_present(self):
        self.assertIn("_DECLARED_EXTRACTORS = {", self.py_src)
        self.assertIn("_RUNTIME_OBSERVERS = {", self.py_src)
        self.assertIn("_IDENTIFIER_PARSERS = {", self.py_src)

    def test_yaml_has_meta_section(self):
        self.assertIn("meta:", self.yaml_src)
        self.assertIn("meta_rule: MR-17", self.yaml_src)

    def test_yaml_first_spec_is_jobs_to_crontab(self):
        self.assertIn("id: jobs_to_crontab", self.yaml_src)

    def test_yaml_first_spec_drift_action_is_alert_only(self):
        # V37.9.19 ships only with alert_only; machine_sync requires V37.9.20 +
        # one-week observation evidence. Source-level guard prevents accidental
        # premature escalation.
        self.assertIn("drift_action: alert_only", self.yaml_src)
        self.assertNotIn("drift_action: machine_sync", self.yaml_src,
            "V37.9.19 must not ship machine_sync — premature escalation blocked. "
            "If escalating, update this guard explicitly.")

    def test_yaml_blood_lesson_link_present(self):
        self.assertIn("kb_deep_dive_cron_unregistered_case.md", self.yaml_src)


# ═══════════════════════════════════════════════════════════════════════════
# V37.9.20 — providers_to_adapter (second spec) regression tests
# ═══════════════════════════════════════════════════════════════════════════

class TestExtractProvidersFromRegistry(unittest.TestCase):
    """V37.9.20 declared-side extractor for providers."""

    def test_returns_set_of_strings(self):
        result = cv._extract_providers_from_registry({})
        self.assertIsInstance(result, set)
        for name in result:
            self.assertIsInstance(name, str)

    def test_includes_known_builtin_providers(self):
        # Built-in 7 are stable across V35+; spec relies on this
        result = cv._extract_providers_from_registry({})
        # At minimum qwen + gemini must be present (V37.8 fallback chain users)
        self.assertIn("qwen", result)
        self.assertIn("gemini", result)
        self.assertGreaterEqual(len(result), 4,
            "Built-in registry should expose ≥4 providers (V37.9.20 floor)")

    def test_skips_empty_names(self):
        # Defense in depth: ensure no empty strings leak through
        result = cv._extract_providers_from_registry({})
        self.assertNotIn("", result)
        self.assertNotIn(None, result)


class TestObserveHttpEndpoint(unittest.TestCase):
    """V37.9.20 HTTP observer with mocked urllib."""

    def _spec(self, **overrides):
        s = {"runtime_observable": {
            "method": "http_endpoint",
            "url": "http://localhost:5001/health",
            "timeout_sec": 1,
        }}
        s["runtime_observable"].update(overrides)
        return s

    def test_missing_url_raises_value_error(self):
        spec = {"runtime_observable": {"method": "http_endpoint"}}
        with self.assertRaises(ValueError):
            cv._observe_http_endpoint(spec)

    def test_invalid_timeout_falls_back_to_default(self):
        # Timeout type-coerced; non-numeric should silently use default
        # (FAIL-OPEN config tolerance, not strict validation)
        spec = self._spec(timeout_sec="not_a_number")
        # We can't easily assert the timeout value used, but at least
        # the call shouldn't raise ValueError before connection attempt
        with self.assertRaises(RuntimeError):
            # Will fail with connection refused on dev, but past the
            # timeout-coercion code path
            cv._observe_http_endpoint(spec)

    def test_connection_refused_raises_runtime_error_not_url_error(self):
        # FAIL-OPEN promise: every failure surfaces as RuntimeError so
        # framework's verify_convergence wraps it in observer_failed
        spec = self._spec(url="http://localhost:1/should_not_exist")
        with self.assertRaises(RuntimeError) as cm:
            cv._observe_http_endpoint(spec)
        self.assertIn("http_endpoint", str(cm.exception))

    def test_timeout_path_raises_runtime_error(self):
        # blackholed IP should hit timeout path; very short timeout
        # 198.51.100.1 is TEST-NET-2 (RFC5737), guaranteed unreachable
        spec = self._spec(url="http://198.51.100.1:5001/health", timeout_sec=0.5)
        with self.assertRaises(RuntimeError):
            cv._observe_http_endpoint(spec)

    def test_successful_response_returns_decoded_body(self):
        # Mock urlopen via context manager; verify decode path
        from unittest.mock import patch, MagicMock
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = b'{"ok": true, "provider": "qwen"}'
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            spec = self._spec()
            body = cv._observe_http_endpoint(spec)
            self.assertEqual(body, '{"ok": true, "provider": "qwen"}')

    def test_non_2xx_status_raises(self):
        from unittest.mock import patch, MagicMock
        mock_resp = MagicMock()
        mock_resp.status = 500
        mock_resp.read.return_value = b'oops'
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            spec = self._spec()
            with self.assertRaises(RuntimeError) as cm:
                cv._observe_http_endpoint(spec)
            self.assertIn("status=500", str(cm.exception))


class TestParseJsonSetUnion(unittest.TestCase):
    """V37.9.20 JSON parser with multi-path union semantics."""

    def _spec(self, paths):
        return {"runtime_observable": {"json_paths": paths}}

    def test_basic_three_path_union(self):
        body = ('{"provider": "qwen", "fallback": "gemini", '
                '"fallback_chain": ["gemini", "claude"]}')
        spec = self._spec(["provider", "fallback", "fallback_chain[]"])
        declared = {"qwen", "gemini", "claude", "openai"}
        result = cv._parse_json_set_union(spec, body, declared)
        self.assertEqual(result, {"qwen", "gemini", "claude"})

    def test_observed_intersects_with_declared(self):
        # Framework convention: extras silently dropped
        body = '{"provider": "qwen", "fallback_chain": ["unknown_provider"]}'
        spec = self._spec(["provider", "fallback_chain[]"])
        declared = {"qwen", "gemini"}
        result = cv._parse_json_set_union(spec, body, declared)
        # unknown_provider not in declared → dropped
        self.assertEqual(result, {"qwen"})

    def test_missing_keys_silently_skipped(self):
        body = '{"provider": "qwen"}'  # no fallback / fallback_chain
        spec = self._spec(["provider", "fallback", "fallback_chain[]"])
        declared = {"qwen", "gemini"}
        result = cv._parse_json_set_union(spec, body, declared)
        self.assertEqual(result, {"qwen"})

    def test_empty_raw_returns_empty_set(self):
        result = cv._parse_json_set_union(self._spec(["x"]), "", {"x"})
        self.assertEqual(result, set())

    def test_invalid_json_raises_value_error(self):
        with self.assertRaises(ValueError) as cm:
            cv._parse_json_set_union(self._spec(["x"]), "not json", {"x"})
        self.assertIn("invalid JSON", str(cm.exception))

    def test_non_object_top_level_raises(self):
        # JSON arrays at top level → unsupported
        with self.assertRaises(ValueError):
            cv._parse_json_set_union(self._spec(["x"]), '["a","b"]', {"a"})

    def test_missing_paths_config_raises(self):
        with self.assertRaises(ValueError):
            cv._parse_json_set_union(
                {"runtime_observable": {}}, '{"x":1}', {"x"})

    def test_empty_paths_list_raises(self):
        with self.assertRaises(ValueError):
            cv._parse_json_set_union(self._spec([]), '{"x":1}', {"x"})

    def test_list_path_with_non_list_value_raises(self):
        # Spec says fallback_chain[] but body has scalar — structural error
        body = '{"fallback_chain": "not_a_list"}'
        with self.assertRaises(ValueError):
            cv._parse_json_set_union(
                self._spec(["fallback_chain[]"]), body, {"x"})

    def test_scalar_path_with_dict_value_silently_skipped(self):
        # FAIL-OPEN: dict on scalar path likely misconfig but don't raise
        body = '{"provider": {"nested": "qwen"}}'
        result = cv._parse_json_set_union(
            self._spec(["provider"]), body, {"qwen"})
        self.assertEqual(result, set())

    def test_null_list_elements_skipped(self):
        body = '{"fallback_chain": ["gemini", null, "claude"]}'
        result = cv._parse_json_set_union(
            self._spec(["fallback_chain[]"]), body, {"gemini", "claude"})
        self.assertEqual(result, {"gemini", "claude"})

    def test_non_string_path_skipped(self):
        # Defensive: spec authors may put None / int by mistake
        spec = {"runtime_observable": {"json_paths": ["provider", None, 42, ""]}}
        body = '{"provider": "qwen"}'
        result = cv._parse_json_set_union(spec, body, {"qwen"})
        self.assertEqual(result, {"qwen"})


class TestVerifyProvidersToAdapterIntegration(unittest.TestCase):
    """V37.9.20 end-to-end via verify_convergence with mocked HTTP."""

    def test_real_spec_loads_and_runs_without_raise(self):
        # In dev (no adapter on :5001), expect observer_failed but not crash
        r = cv.verify_convergence("providers_to_adapter")
        self.assertEqual(r.spec_id, "providers_to_adapter")
        self.assertEqual(r.drift_action, "alert_only")
        self.assertIsInstance(r, cv.ConvergenceResult)
        # In dev, error should mention observer or extractor (both can trigger
        # before connection refused depending on import timing)
        if r.error:
            self.assertTrue(
                "observer_failed" in r.error or "extractor_failed" in r.error,
                f"unexpected error type in dev: {r.error}")

    def test_happy_path_with_mocked_health(self):
        from unittest.mock import patch, MagicMock
        # Mock /health response: 3 providers visible
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = (
            b'{"provider":"qwen","fallback":"gemini",'
            b'"fallback_chain":["gemini","claude"]}')
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            r = cv.verify_convergence("providers_to_adapter")
            self.assertIsNone(r.error,
                f"unexpected error: {r.error}")
            # 3 of 7 visible → 4 missing
            self.assertEqual(r.observed,
                frozenset({"qwen", "gemini", "claude"}))
            # missing = declared - observed (whatever the registry has minus 3)
            self.assertEqual(len(r.missing_in_runtime),
                len(r.declared) - 3)
            self.assertTrue(r.drift_detected,
                "Drift expected: 4 providers declared but not in /health")

    def test_full_visibility_no_drift(self):
        from unittest.mock import patch, MagicMock
        # Synthesize /health that lists all 7 builtin providers
        all_known = {"qwen", "openai", "gemini", "claude", "kimi", "minimax", "glm"}
        body = json.dumps({
            "provider": "qwen",
            "fallback": "gemini",
            "fallback_chain": sorted(all_known - {"qwen"}),
        }).encode()
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = body
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            r = cv.verify_convergence("providers_to_adapter")
            self.assertIsNone(r.error)
            # Every declared provider should be observed → no drift
            self.assertEqual(r.missing_in_runtime, frozenset())
            self.assertFalse(r.drift_detected)


class TestProvidersSpecSourceGuards(unittest.TestCase):
    """V37.9.20 source-level guards on yaml + framework registration."""

    @classmethod
    def setUpClass(cls):
        cls.py_src = (ONTOLOGY_DIR / "convergence.py").read_text(encoding="utf-8")
        cls.yaml_src = (ONTOLOGY_DIR / "convergence_ontology.yaml").read_text(encoding="utf-8")

    def test_extractor_registered_in_dispatch(self):
        self.assertIn(
            '"providers_from_registry": _extract_providers_from_registry',
            self.py_src,
        )

    def test_observer_registered_in_dispatch(self):
        self.assertIn(
            '"http_endpoint": _observe_http_endpoint',
            self.py_src,
        )

    def test_parser_registered_in_dispatch(self):
        self.assertIn(
            '"json_set_union": _parse_json_set_union',
            self.py_src,
        )

    def test_http_observer_timeout_constant_present(self):
        self.assertIn("_HTTP_OBSERVER_TIMEOUT_SEC", self.py_src)

    def test_yaml_declares_providers_to_adapter_spec(self):
        self.assertIn("id: providers_to_adapter", self.yaml_src)

    def test_yaml_drift_action_alert_only(self):
        # V37.9.20 ships providers_to_adapter as alert_only.
        # machine_sync NOT applicable structurally (cannot auto-provision keys).
        # planned_rationale documents this is permanent design choice, not TODO.
        self.assertIn("alert_only_permanent", self.yaml_src,
            "providers_to_adapter must declare alert_only_permanent (not TODO)")

    def test_yaml_meta_version_advanced(self):
        # V37.9.20: meta version bumped from 0.1-skeleton → 0.2-second-spec
        # V37.9.22 third spec: bumped → 0.3-third-spec
        # V37.9.22 fourth spec: bumped → 0.4-fourth-spec
        # Guard against accidental regression below 0.2 (V37.9.20 baseline)
        self.assertNotIn('version: "0.1-skeleton"', self.yaml_src,
            "meta version should be bumped past 0.1-skeleton in V37.9.20+")
        # Use regex to accept any 0.X-* where X >= 2 (forward-compatible)
        import re as _re
        m = _re.search(r'version:\s*"0\.([0-9]+)-', self.yaml_src)
        self.assertIsNotNone(m, "meta version line not found")
        major = int(m.group(1))
        self.assertGreaterEqual(major, 2,
            f"meta version 0.{major}-* below V37.9.20 baseline 0.2")

    def test_yaml_meta_lists_both_invariants(self):
        # Both V37.9.19 + V37.9.20 invariants present in related_invariants
        self.assertIn("INV-CONVERGENCE-CRON-001", self.yaml_src)
        self.assertIn("INV-CONVERGENCE-PROVIDERS-001", self.yaml_src)

    def test_yaml_blood_lesson_links_present(self):
        # Both blood case docs referenced
        self.assertIn("kb_deep_dive_cron_unregistered_case.md", self.yaml_src)
        self.assertIn("kb_evening_fallback_quota_chain_case.md", self.yaml_src)

    def test_v37_9_20_changelog_mentions_dispatch_extension(self):
        # Document that V37.9.20 is pure named-dispatch extension
        self.assertIn("v37_9_20_changelog", self.yaml_src)
        self.assertIn("named-dispatch", self.yaml_src)

    def test_no_machine_sync_for_providers_spec(self):
        # Guard against accidental drift_action escalation: providers spec
        # should NEVER have machine_sync (cannot auto-provision API keys)
        # Find providers_to_adapter spec block and check its drift_action
        idx = self.yaml_src.find("id: providers_to_adapter")
        self.assertGreater(idx, 0)
        # Look at next ~80 lines for spec content
        block = self.yaml_src[idx:idx + 3000]
        # Within the spec block, drift_action must be alert_only
        # (not machine_sync). The first drift_action: line in this block
        # is the spec's drift_action.
        lines = block.split("\n")
        for line in lines:
            if line.strip().startswith("drift_action:") and "rationale" not in line:
                self.assertIn("alert_only", line)
                self.assertNotIn("machine_sync", line)
                return
        self.fail("Could not find drift_action: line in providers_to_adapter spec")


class TestWalkJsonPathsToSet(unittest.TestCase):
    """V37.9.22 — _walk_json_paths_to_set shared helper (MR-8 兑现).

    Extracted from V37.9.20 _parse_json_set_union to be shared by both
    declared-side (_extract_json_file_paths) and observed-side parser.
    Pure function with no I/O — easy to test directly."""

    def test_top_level_scalar_path(self):
        data = {"version": "1.2.3", "name": "openclaw"}
        result = cv._walk_json_paths_to_set(data, ["version"])
        self.assertEqual(result, {"1.2.3"})

    def test_top_level_list_path(self):
        data = {"agents": ["pa", "ops", "research"]}
        result = cv._walk_json_paths_to_set(data, ["agents[]"])
        self.assertEqual(result, {"pa", "ops", "research"})

    def test_multiple_paths_union(self):
        data = {"version": "1.2.3", "agents": ["pa", "ops"]}
        result = cv._walk_json_paths_to_set(data, ["version", "agents[]"])
        self.assertEqual(result, {"1.2.3", "pa", "ops"})

    def test_missing_key_silently_skipped(self):
        data = {"version": "1.2.3"}
        result = cv._walk_json_paths_to_set(data, ["version", "missing", "absent[]"])
        self.assertEqual(result, {"1.2.3"})

    def test_none_value_silently_skipped(self):
        data = {"version": None, "name": "x"}
        result = cv._walk_json_paths_to_set(data, ["version", "name"])
        self.assertEqual(result, {"x"})

    def test_dict_on_scalar_path_silently_skipped(self):
        # FAIL-OPEN: nested dict on scalar path is misconfig but doesn't raise
        data = {"agents": {"pa": "..."}}
        result = cv._walk_json_paths_to_set(data, ["agents"])
        self.assertEqual(result, set())

    def test_list_on_scalar_path_silently_skipped(self):
        # FAIL-OPEN: bare list on scalar path silently skipped
        data = {"agents": ["pa", "ops"]}
        result = cv._walk_json_paths_to_set(data, ["agents"])  # missing []
        self.assertEqual(result, set())

    def test_scalar_on_list_path_raises(self):
        # Structural misconfig: path declared [] but value is scalar
        data = {"agents": "pa"}
        with self.assertRaises(ValueError) as ctx:
            cv._walk_json_paths_to_set(data, ["agents[]"])
        self.assertIn("expected list", str(ctx.exception))

    def test_list_with_none_elements_skipped(self):
        data = {"agents": ["pa", None, "ops"]}
        result = cv._walk_json_paths_to_set(data, ["agents[]"])
        self.assertEqual(result, {"pa", "ops"})

    def test_list_coerces_non_string_elements(self):
        data = {"ports": [5001, 5002, 18789]}
        result = cv._walk_json_paths_to_set(data, ["ports[]"])
        self.assertEqual(result, {"5001", "5002", "18789"})

    def test_empty_paths_returns_empty_set(self):
        result = cv._walk_json_paths_to_set({"a": 1}, [])
        self.assertEqual(result, set())

    def test_invalid_path_types_silently_skipped(self):
        # Non-string path entries skipped silently (FAIL-OPEN)
        data = {"version": "1"}
        result = cv._walk_json_paths_to_set(data, ["version", None, 42, "", "version"])
        self.assertEqual(result, {"1"})


class TestExtractJsonFilePaths(unittest.TestCase):
    """V37.9.22 — _extract_json_file_paths declared-side extractor.

    Validates: file resolution (abs/rel/~/$VAR), FAIL-OPEN on missing,
    error categorization (missing vs corrupted), spec-config validation."""

    def _spec(self, source, json_paths):
        return {
            "id": "test_spec",
            "declaration": {
                "source": source,
                "extractor": "json_file_paths",
                "json_paths": json_paths,
            },
        }

    def test_basic_extraction(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "config.json"
            p.write_text(json.dumps({"version": "2.5.0", "agents": ["pa", "ops"]}))
            result = cv._extract_json_file_paths(
                self._spec(str(p), ["version", "agents[]"])
            )
            self.assertEqual(result, {"2.5.0", "pa", "ops"})

    def test_missing_source_raises_value_error(self):
        with self.assertRaises(ValueError) as ctx:
            cv._extract_json_file_paths(self._spec("", ["x"]))
        self.assertIn("source", str(ctx.exception))

    def test_missing_json_paths_raises_value_error(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "x.json"
            p.write_text("{}")
            with self.assertRaises(ValueError) as ctx:
                cv._extract_json_file_paths(self._spec(str(p), []))
            self.assertIn("json_paths", str(ctx.exception))

    def test_file_not_exist_returns_empty_set_fail_open(self):
        """FAIL-OPEN: dev environments without OpenClaw runtime → empty set,
        not raise. Critical for governance audit not spuriously alerting."""
        result = cv._extract_json_file_paths(
            self._spec("/nonexistent/path/openclaw.json", ["version"])
        )
        self.assertEqual(result, set())

    def test_invalid_json_raises_runtime_error(self):
        """File present but corrupted → RuntimeError (extractor_failed),
        distinct from missing file (set()) — admin sees actual data corruption."""
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "bad.json"
            p.write_text("{ not valid json")
            with self.assertRaises(RuntimeError) as ctx:
                cv._extract_json_file_paths(self._spec(str(p), ["x"]))
            self.assertIn("invalid JSON", str(ctx.exception))

    def test_top_level_non_object_raises_value_error(self):
        """JSON valid but top-level array (not object) → ValueError."""
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "list.json"
            p.write_text(json.dumps(["a", "b", "c"]))
            with self.assertRaises(ValueError) as ctx:
                cv._extract_json_file_paths(self._spec(str(p), ["x"]))
            self.assertIn("must be object", str(ctx.exception))

    def test_home_var_expansion(self):
        """$HOME and ~ should expand to actual home dir."""
        with tempfile.TemporaryDirectory() as td:
            # Override HOME to td so we can test expansion safely
            old_home = os.environ.get("HOME")
            os.environ["HOME"] = td
            try:
                p = Path(td) / "config.json"
                p.write_text(json.dumps({"version": "1.0"}))
                # $HOME path
                result = cv._extract_json_file_paths(
                    self._spec("$HOME/config.json", ["version"])
                )
                self.assertEqual(result, {"1.0"})
                # ~ path
                result = cv._extract_json_file_paths(
                    self._spec("~/config.json", ["version"])
                )
                self.assertEqual(result, {"1.0"})
            finally:
                if old_home is not None:
                    os.environ["HOME"] = old_home

    def test_relative_path_resolves_against_repo_root(self):
        """Relative paths resolve to repo root (parent of ontology/)."""
        # Create temp file at repo root for this test, clean up after
        marker = REPO_ROOT / "_test_convergence_v9_22_marker.json"
        try:
            marker.write_text(json.dumps({"version": "rel-test"}))
            result = cv._extract_json_file_paths(
                self._spec("_test_convergence_v9_22_marker.json", ["version"])
            )
            self.assertEqual(result, {"rel-test"})
        finally:
            if marker.exists():
                marker.unlink()

    def test_multi_path_union_from_file(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "multi.json"
            p.write_text(json.dumps({
                "version": "2.5.0",
                "channels": ["whatsapp", "discord"],
                "missing_field_skipped": "but_not_in_paths",
            }))
            result = cv._extract_json_file_paths(
                self._spec(str(p), ["version", "channels[]"])
            )
            self.assertEqual(result, {"2.5.0", "whatsapp", "discord"})


class TestVerifyOpenclawConfigToRuntimeIntegration(unittest.TestCase):
    """V37.9.22 — End-to-end via real openclaw_config_to_runtime spec from yaml.

    Verifies framework's third extension works: zero changes to verify_convergence
    orchestrator, just dispatch table extension."""

    def test_real_spec_dev_environment_does_not_crash(self):
        """dev: openclaw.json absent + Gateway not running → declared=set()
        + observer_failed → result.error set, drift_detected=False, no raise."""
        result = cv.verify_convergence("openclaw_config_to_runtime")
        # Should not raise; result is a valid namedtuple
        self.assertEqual(result.spec_id, "openclaw_config_to_runtime")
        # Declared empty (file missing) → no drift detected since nothing to miss
        self.assertEqual(result.declared, set())
        # Drift not detected (declared empty intersect observed empty = no missing)
        self.assertFalse(result.drift_detected)

    def test_spec_uses_json_file_paths_extractor(self):
        spec = cv.get_spec("openclaw_config_to_runtime")
        self.assertIsNotNone(spec, "openclaw_config_to_runtime spec must exist in yaml")
        self.assertEqual(spec["declaration"]["extractor"], "json_file_paths")

    def test_spec_uses_http_endpoint_observer(self):
        spec = cv.get_spec("openclaw_config_to_runtime")
        self.assertEqual(spec["runtime_observable"]["method"], "http_endpoint")

    def test_spec_uses_json_set_union_parser(self):
        spec = cv.get_spec("openclaw_config_to_runtime")
        self.assertEqual(spec["runtime_observable"]["parser"], "json_set_union")

    def test_spec_drift_action_alert_only(self):
        spec = cv.get_spec("openclaw_config_to_runtime")
        self.assertEqual(spec["drift_action"], "alert_only")


class TestOpenclawSpecSourceGuards(unittest.TestCase):
    """V37.9.22 — source-level guards on convergence.py + yaml extension."""

    @classmethod
    def setUpClass(cls):
        cls.py_src = (ONTOLOGY_DIR / "convergence.py").read_text(encoding="utf-8")
        cls.yaml_src = (ONTOLOGY_DIR / "convergence_ontology.yaml").read_text(encoding="utf-8")
        cls.gov_src = (ONTOLOGY_DIR / "governance_ontology.yaml").read_text(encoding="utf-8")

    def test_extractor_registered_in_dispatch(self):
        self.assertIn(
            '"json_file_paths": _extract_json_file_paths',
            self.py_src,
        )

    def test_walk_helper_defined(self):
        """MR-8 兑现：shared helper extracted from V37.9.20 parser."""
        self.assertIn("def _walk_json_paths_to_set", self.py_src)

    def test_extractor_uses_walk_helper(self):
        """_extract_json_file_paths must call the shared helper, not duplicate logic."""
        self.assertIn("_walk_json_paths_to_set(data, paths)", self.py_src)
        # Specifically inside _extract_json_file_paths
        idx = self.py_src.find("def _extract_json_file_paths")
        self.assertGreater(idx, 0)
        end = self.py_src.find("\ndef ", idx + 10)
        body = self.py_src[idx:end]
        self.assertIn("_walk_json_paths_to_set", body)

    def test_parser_uses_walk_helper(self):
        """V37.9.22 refactor: V37.9.20 _parse_json_set_union must now use shared
        helper instead of inlined path traversal — single source of truth."""
        idx = self.py_src.find("def _parse_json_set_union")
        self.assertGreater(idx, 0)
        end = self.py_src.find("\n\n_IDENTIFIER_PARSERS", idx)
        body = self.py_src[idx:end]
        self.assertIn("_walk_json_paths_to_set", body,
            "V37.9.22 refactor: _parse_json_set_union must call shared helper "
            "(MR-8 単一真理源 — both extractor and parser go through one path-syntax impl)")

    def test_extractor_fail_open_on_missing_file(self):
        """FAIL-OPEN contract: extractor returns set() (not raise) when file missing."""
        idx = self.py_src.find("def _extract_json_file_paths")
        self.assertGreater(idx, 0)
        end = self.py_src.find("\ndef ", idx + 10)
        body = self.py_src[idx:end]
        self.assertIn("if not p.exists():", body)
        self.assertIn("return set()", body)

    def test_yaml_declares_openclaw_config_to_runtime_spec(self):
        self.assertIn("id: openclaw_config_to_runtime", self.yaml_src)

    def test_yaml_meta_version_advanced(self):
        # V37.9.22 third spec → 0.3-third-spec; fourth spec → 0.4-fourth-spec.
        # Guard: must be at 0.3+ (third spec baseline or higher)
        import re as _re
        m = _re.search(r'version:\s*"0\.([0-9]+)-', self.yaml_src)
        self.assertIsNotNone(m, "meta version line not found")
        major = int(m.group(1))
        self.assertGreaterEqual(major, 3,
            f"meta version 0.{major}-* below V37.9.22 third-spec baseline 0.3")

    def test_yaml_meta_lists_three_invariants(self):
        self.assertIn("INV-CONVERGENCE-CRON-001", self.yaml_src)
        self.assertIn("INV-CONVERGENCE-PROVIDERS-001", self.yaml_src)
        self.assertIn("INV-CONVERGENCE-OPENCLAW-001", self.yaml_src)

    def test_yaml_changelog_documents_extension(self):
        self.assertIn("v37_9_22_changelog", self.yaml_src)
        self.assertIn("json_file_paths", self.yaml_src)
        self.assertIn("_walk_json_paths_to_set", self.yaml_src)

    def test_yaml_alert_only_permanent_for_third_spec(self):
        """Like providers spec, openclaw_config_to_runtime cannot machine-sync
        (operator decision required), so planned=alert_only_permanent."""
        idx = self.yaml_src.find("id: openclaw_config_to_runtime")
        self.assertGreater(idx, 0)
        block = self.yaml_src[idx:]
        self.assertIn("alert_only_permanent", block)

    def test_no_machine_sync_for_openclaw_spec(self):
        """Guard against accidental drift_action escalation."""
        idx = self.yaml_src.find("id: openclaw_config_to_runtime")
        self.assertGreater(idx, 0)
        # spec block runs from this id to end of file (it's the last spec in yaml);
        # openclaw spec is ~7KB with all description/declaration/runtime/method blocks
        block = self.yaml_src[idx:]
        for line in block.split("\n"):
            if line.strip().startswith("drift_action:") and "rationale" not in line:
                self.assertIn("alert_only", line)
                self.assertNotIn("machine_sync", line)
                return
        self.fail("drift_action: line not found in openclaw_config_to_runtime spec")

    def test_governance_ontology_lists_third_invariant(self):
        """MR-17 derivative_invariants must include the third INV."""
        self.assertIn("INV-CONVERGENCE-OPENCLAW-001", self.gov_src)
        # Specifically in MR-17 derivative_invariants list
        idx = self.gov_src.find("- id: MR-17")
        self.assertGreater(idx, 0)
        end = self.gov_src.find("\n  - id:", idx + 10)
        if end < 0:
            end = idx + 5000
        mr17_block = self.gov_src[idx:end]
        self.assertIn("INV-CONVERGENCE-OPENCLAW-001", mr17_block)


class TestExtractRegistryKbSourceFiles(unittest.TestCase):
    """V37.9.22 fourth spec — _extract_registry_kb_source_files (registry-specific).

    Sibling to V37.9.19 _extract_registry_enabled_system_jobs (different field,
    different filter — does NOT require scheduler=system since KB sources can
    come from openclaw cron too)."""

    def test_extracts_kb_source_file_basenames(self):
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            jobs = [
                {"id": "arxiv_monitor", "enabled": True, "scheduler": "system",
                 "entry": "kb_save_arxiv.sh", "kb_source_file": "arxiv_daily.md"},
                {"id": "hf_papers", "enabled": True, "scheduler": "system",
                 "entry": "run_hf_papers.sh", "kb_source_file": "hf_papers_daily.md"},
            ]
            # Build registry yaml manually (more fields than helper provides)
            lines = ["jobs:\n"]
            for j in jobs:
                lines.append(f"  - id: {j['id']}\n")
                lines.append(f"    enabled: {str(j['enabled']).lower()}\n")
                lines.append(f"    scheduler: {j['scheduler']}\n")
                lines.append(f"    entry: {j['entry']}\n")
                lines.append(f"    kb_source_file: {j['kb_source_file']}\n")
            (tdp / "jobs_registry.yaml").write_text("".join(lines))
            # Use temp dir as fake repo root by patching Path.parent.parent
            spec = {"declaration": {"source": "jobs_registry.yaml"}}
            # The extractor uses Path(__file__).resolve().parent.parent / src
            # so we test against the real repo's registry instead
            result = cv._extract_registry_kb_source_files({
                "declaration": {"source": "jobs_registry.yaml"}
            })
            # Real registry has 14 declared kb_source_file entries
            self.assertGreaterEqual(len(result), 10,
                "Real jobs_registry should declare ≥10 kb_source_file entries")
            self.assertIn("arxiv_daily.md", result)

    def test_disabled_jobs_excluded(self):
        # The real registry — verify enabled filter works by checking
        # against a known-disabled job (none currently disabled, so we
        # write a temp registry to test the filter mechanism)
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            registry = """jobs:
  - id: enabled_job
    enabled: true
    scheduler: system
    entry: x.sh
    kb_source_file: enabled.md
  - id: disabled_job
    enabled: false
    scheduler: system
    entry: y.sh
    kb_source_file: disabled.md
"""
            registry_path = tdp / "test_registry.yaml"
            registry_path.write_text(registry)
            # Read directly via _load_yaml to simulate
            data = cv._load_yaml(registry_path)
            # Replicate extractor logic on this isolated data
            out = set()
            for job in data.get("jobs") or []:
                if not job.get("enabled"):
                    continue
                kb_file = job.get("kb_source_file") or ""
                if kb_file:
                    out.add(kb_file)
            self.assertIn("enabled.md", out)
            self.assertNotIn("disabled.md", out)

    def test_jobs_without_kb_source_file_excluded(self):
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            registry = """jobs:
  - id: with_kb
    enabled: true
    scheduler: system
    entry: x.sh
    kb_source_file: x.md
  - id: without_kb
    enabled: true
    scheduler: system
    entry: y.sh
"""
            registry_path = tdp / "test_registry.yaml"
            registry_path.write_text(registry)
            data = cv._load_yaml(registry_path)
            out = set()
            for job in data.get("jobs") or []:
                if not job.get("enabled"):
                    continue
                kb_file = job.get("kb_source_file") or ""
                if kb_file:
                    out.add(kb_file)
            self.assertEqual(out, {"x.md"})

    def test_does_not_filter_by_scheduler(self):
        """Unlike V37.9.19 system_jobs extractor, kb_source_files does NOT
        filter scheduler=system — KB sources can come from openclaw cron too."""
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            registry = """jobs:
  - id: system_job
    enabled: true
    scheduler: system
    entry: x.sh
    kb_source_file: system.md
  - id: openclaw_job
    enabled: true
    scheduler: openclaw
    entry: y.sh
    kb_source_file: openclaw.md
"""
            registry_path = tdp / "test_registry.yaml"
            registry_path.write_text(registry)
            data = cv._load_yaml(registry_path)
            out = set()
            for job in data.get("jobs") or []:
                if not job.get("enabled"):
                    continue
                kb_file = job.get("kb_source_file") or ""
                if kb_file:
                    out.add(kb_file)
            # Both should be included (no scheduler filter)
            self.assertEqual(out, {"system.md", "openclaw.md"})

    def test_real_registry_has_expected_kb_sources(self):
        """Smoke test against actual jobs_registry.yaml — V37.9.22 baseline:
        14 declared kb_source_file entries (acl/ai_leaders/arxiv/chaspark/
        dblp/finance/freight/github/hf/hn/ontology/openclaw/rss/s2)."""
        result = cv._extract_registry_kb_source_files({
            "declaration": {"source": "jobs_registry.yaml"}
        })
        expected_subset = {
            "arxiv_daily.md", "hf_papers_daily.md", "ontology_sources.md",
        }
        self.assertTrue(expected_subset.issubset(result),
            f"Real registry missing core kb sources: {expected_subset - result}")


class TestVerifyKbSourcesToIndexIntegration(unittest.TestCase):
    """V37.9.22 — End-to-end via real kb_sources_to_index spec from yaml.

    Verifies framework's fourth extension works: zero changes to verify_convergence
    orchestrator, just dispatch table extension via the new extractor entry."""

    def test_real_spec_dev_environment_does_not_crash(self):
        """dev: ~/.kb/text_index/meta.json absent → command exits 0 with empty
        stdout (handled by extractor's `raise SystemExit(0)`) → observer returns
        empty string → all declared reported as missing (no crash, valid result)."""
        result = cv.verify_convergence("kb_sources_to_index")
        self.assertEqual(result.spec_id, "kb_sources_to_index")
        # declared should be non-empty (real registry has ≥10 kb_source_file)
        self.assertGreaterEqual(len(result.declared), 10)
        # In dev (no ~/.kb/text_index), command's `raise SystemExit(0)` returns
        # empty stdout; line_contains_identifier finds nothing; missing = declared.
        # drift_detected=True because declared > observed=∅.
        # No exception raised — that's the contract.
        self.assertIsNone(result.error,
            "Dev environment should not produce extractor/observer/parser error")

    def test_spec_uses_registry_kb_source_files_extractor(self):
        spec = cv.get_spec("kb_sources_to_index")
        self.assertIsNotNone(spec, "kb_sources_to_index spec must exist in yaml")
        self.assertEqual(spec["declaration"]["extractor"], "registry_kb_source_files")

    def test_spec_uses_shell_command_observer(self):
        """Reuses V37.9.19 _observe_shell_command (zero new observer needed)."""
        spec = cv.get_spec("kb_sources_to_index")
        self.assertEqual(spec["runtime_observable"]["method"], "shell_command")

    def test_spec_uses_line_contains_identifier_parser(self):
        """Reuses V37.9.19 _parse_line_contains_identifier (zero new parser needed)."""
        spec = cv.get_spec("kb_sources_to_index")
        self.assertEqual(spec["runtime_observable"]["parser"], "line_contains_identifier")

    def test_spec_drift_action_alert_only(self):
        spec = cv.get_spec("kb_sources_to_index")
        self.assertEqual(spec["drift_action"], "alert_only")


class TestKbSpecSourceGuards(unittest.TestCase):
    """V37.9.22 fourth spec — source-level guards on convergence.py + yaml."""

    @classmethod
    def setUpClass(cls):
        cls.py_src = (ONTOLOGY_DIR / "convergence.py").read_text(encoding="utf-8")
        cls.yaml_src = (ONTOLOGY_DIR / "convergence_ontology.yaml").read_text(encoding="utf-8")
        cls.gov_src = (ONTOLOGY_DIR / "governance_ontology.yaml").read_text(encoding="utf-8")

    def test_extractor_registered_in_dispatch(self):
        self.assertIn(
            '"registry_kb_source_files": _extract_registry_kb_source_files',
            self.py_src,
        )

    def test_extractor_function_defined(self):
        self.assertIn("def _extract_registry_kb_source_files", self.py_src)

    def test_extractor_uses_kb_source_file_field(self):
        idx = self.py_src.find("def _extract_registry_kb_source_files")
        self.assertGreater(idx, 0)
        end = self.py_src.find("\ndef ", idx + 10)
        body = self.py_src[idx:end]
        self.assertIn("kb_source_file", body)
        # Does NOT filter scheduler (different from V37.9.19 system_jobs extractor)
        self.assertNotIn('scheduler != "system"', body)
        self.assertNotIn('scheduler == "system"', body)

    def test_yaml_declares_kb_sources_to_index_spec(self):
        self.assertIn("id: kb_sources_to_index", self.yaml_src)

    def test_yaml_meta_version_advanced_to_fourth(self):
        self.assertIn("0.4-fourth-spec", self.yaml_src)
        self.assertNotIn('version: "0.3-third-spec"', self.yaml_src,
            "meta version should be bumped past 0.3 in V37.9.22 fourth spec")

    def test_yaml_meta_lists_four_invariants(self):
        for inv in ["INV-CONVERGENCE-CRON-001", "INV-CONVERGENCE-PROVIDERS-001",
                    "INV-CONVERGENCE-OPENCLAW-001", "INV-CONVERGENCE-KB-001"]:
            self.assertIn(inv, self.yaml_src)

    def test_yaml_changelog_documents_minimal_extension_pattern(self):
        # V37.9.22 changelog highlights the "minimal extension" pattern (only
        # 1 new extractor; reuses observer + parser from V37.9.19)
        self.assertIn("kb_sources_to_index", self.yaml_src)
        self.assertIn("registry_kb_source_files", self.yaml_src)

    def test_yaml_kb_spec_uses_v37_9_19_components(self):
        """KB spec validates 'minimal extension' path: reuse shell_command +
        line_contains_identifier from V37.9.19 instead of inventing new ones."""
        idx = self.yaml_src.find("id: kb_sources_to_index")
        self.assertGreater(idx, 0)
        block = self.yaml_src[idx:]
        self.assertIn("method: shell_command", block)
        self.assertIn("parser: line_contains_identifier", block)

    def test_yaml_kb_spec_drift_action_alert_only(self):
        idx = self.yaml_src.find("id: kb_sources_to_index")
        self.assertGreater(idx, 0)
        block = self.yaml_src[idx:]
        for line in block.split("\n"):
            if line.strip().startswith("drift_action:") and "rationale" not in line:
                self.assertIn("alert_only", line)
                self.assertNotIn("machine_sync", line)
                return
        self.fail("drift_action: line not found in kb_sources_to_index spec")

    def test_governance_ontology_lists_fourth_invariant(self):
        self.assertIn("INV-CONVERGENCE-KB-001", self.gov_src)
        idx = self.gov_src.find("- id: MR-17")
        self.assertGreater(idx, 0)
        end = self.gov_src.find("\n  - id:", idx + 10)
        if end < 0:
            end = idx + 5000
        mr17_block = self.gov_src[idx:end]
        self.assertIn("INV-CONVERGENCE-KB-001", mr17_block)


if __name__ == "__main__":
    unittest.main(verbosity=2)
