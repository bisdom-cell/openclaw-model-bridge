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


if __name__ == "__main__":
    unittest.main(verbosity=2)
