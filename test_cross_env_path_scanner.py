#!/usr/bin/env python3
"""
test_cross_env_path_scanner.py — V37.9.94 INV-CROSS-ENV-PATH-001 单测

Tests the MR-15 deployment-layout scanner that prevents 5th occurrence
of `_resolve_*_path` functions missing Mac Mini canonical candidate.

Scope:
  - is_config_path: only flag .yaml/.yml/.json/.md, skip hidden/data dirs
  - has_script_adjacent_pattern: detect resolver signature
  - scan_function_body: violation detection logic
  - scan_file: AST-level function walking
  - scan_repo: full repo scan + count
  - CLI: exit codes (FAIL-CLOSE), --file option
  - Reverse validation: sabotage canonical, scanner catches
  - Source-level guards for scanner self
"""
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

# Module under test
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cross_env_path_scanner as scanner

SCANNER_PATH = Path(__file__).resolve().parent / "cross_env_path_scanner.py"


class TestIsConfigPath(unittest.TestCase):
    """V37.9.94: is_config_path filters out non-config paths."""

    def test_yaml_is_config(self):
        self.assertTrue(scanner.is_config_path("jobs_registry.yaml"))

    def test_yml_is_config(self):
        self.assertTrue(scanner.is_config_path("nested/config.yml"))

    def test_json_is_config(self):
        self.assertTrue(scanner.is_config_path("status.json"))

    def test_md_is_config(self):
        self.assertTrue(scanner.is_config_path("CLAUDE.md"))

    def test_log_is_not_config(self):
        self.assertFalse(scanner.is_config_path("daily_observer.log"))

    def test_py_is_not_config(self):
        self.assertFalse(scanner.is_config_path("script.py"))

    def test_sh_is_not_config(self):
        self.assertFalse(scanner.is_config_path("notify.sh"))

    def test_hidden_dir_skipped(self):
        """~/.kb/, ~/.openclaw/ are runtime data, not git-managed configs."""
        self.assertFalse(scanner.is_config_path(".kb/status.json"))
        self.assertFalse(scanner.is_config_path(".openclaw/jobs"))

    def test_canonical_itself_skipped(self):
        """`~/openclaw-model-bridge/X` is the CANONICAL — not a candidate."""
        self.assertFalse(
            scanner.is_config_path("openclaw-model-bridge/jobs_registry.yaml"))


class TestHasScriptAdjacentPattern(unittest.TestCase):
    """V37.9.94: detect script-adjacent fallback signature."""

    def test_typical_pattern_detected(self):
        body = (
            "os.path.join(os.path.dirname(os.path.abspath(__file__)), 'x.yaml')"
        )
        self.assertTrue(scanner.has_script_adjacent_pattern(body))

    def test_partial_dirname_no_file_attr(self):
        body = "os.path.dirname('/some/path')"
        self.assertFalse(scanner.has_script_adjacent_pattern(body))

    def test_file_attr_no_dirname(self):
        body = "x = __file__"
        self.assertFalse(scanner.has_script_adjacent_pattern(body))

    def test_no_resolver_signal(self):
        body = "x = 1\ny = 2"
        self.assertFalse(scanner.has_script_adjacent_pattern(body))


class TestScanFunctionBodyViolations(unittest.TestCase):
    """V37.9.94: violation detection in single function bodies."""

    def test_compliant_resolver_no_violation(self):
        """Function with home + canonical + script-adj is compliant."""
        body = textwrap.dedent("""
        def _resolve_path():
            candidates = [
                os.path.expanduser("~/jobs_registry.yaml"),
                os.path.expanduser("~/openclaw-model-bridge/jobs_registry.yaml"),
                os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "jobs_registry.yaml"),
            ]
            return candidates[0]
        """)
        violations = scanner.scan_function_body(body, "_resolve_path", "x.py")
        self.assertEqual(violations, [],
                         "compliant resolver must produce no violations")

    def test_missing_canonical_flagged(self):
        """Function with ~/X + script-adj but NO canonical → flagged."""
        body = textwrap.dedent("""
        def _resolve_path():
            candidates = [
                os.path.expanduser("~/jobs_registry.yaml"),
                os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "jobs_registry.yaml"),
            ]
            return candidates[0]
        """)
        violations = scanner.scan_function_body(body, "_resolve_path", "x.py")
        self.assertEqual(len(violations), 1,
                         "must flag missing canonical")
        self.assertIn("jobs_registry.yaml", violations[0])
        self.assertIn("openclaw-model-bridge", violations[0])
        self.assertIn("MR-15", violations[0])

    def test_no_script_adj_skipped(self):
        """Function with ~/X but no script-adj is NOT a resolver — skip."""
        body = textwrap.dedent("""
        def consume_config():
            path = os.path.expanduser("~/jobs_registry.yaml")
            with open(path) as f:
                return f.read()
        """)
        violations = scanner.scan_function_body(
            body, "consume_config", "x.py")
        self.assertEqual(violations, [],
                         "non-resolver pattern must not be flagged")

    def test_no_config_files_skipped(self):
        """Function with only non-config paths (.log) skipped."""
        body = textwrap.dedent("""
        def _resolve_log_path():
            candidates = [
                os.path.expanduser("~/observer.log"),
                os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "observer.log"),
            ]
            return candidates[0]
        """)
        violations = scanner.scan_function_body(
            body, "_resolve_log_path", "x.py")
        self.assertEqual(violations, [],
                         "log-file resolver must not be flagged")

    def test_hidden_dir_paths_skipped(self):
        """Paths like ~/.kb/ are runtime data, not config — skip."""
        body = textwrap.dedent("""
        def _resolve_kb_path():
            candidates = [
                os.path.expanduser("~/.kb/status.json"),
                os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             ".kb/status.json"),
            ]
            return candidates[0]
        """)
        violations = scanner.scan_function_body(
            body, "_resolve_kb_path", "x.py")
        self.assertEqual(violations, [],
                         "~/.kb/ paths are runtime data, skip")

    def test_multiple_config_files_each_checked(self):
        """A function resolving 2 config files must have BOTH canonical."""
        body = textwrap.dedent("""
        def _resolve_multi():
            cfg = os.path.expanduser("~/cfg1.yaml")
            policy = os.path.expanduser("~/cfg2.yaml")
            adj = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "x.yaml")
            return [cfg, policy, adj]
        """)
        violations = scanner.scan_function_body(body, "_resolve_multi", "x.py")
        self.assertEqual(len(violations), 2,
                         "both files must each be flagged")

    def test_canonical_only_for_one_partial_flag(self):
        """If canonical exists for cfg1 but not cfg2, only cfg2 flagged."""
        body = textwrap.dedent("""
        def _resolve_partial():
            cfg1 = os.path.expanduser("~/cfg1.yaml")
            cfg1_canon = os.path.expanduser("~/openclaw-model-bridge/cfg1.yaml")
            cfg2 = os.path.expanduser("~/cfg2.yaml")
            adj = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "x.yaml")
            return [cfg1, cfg1_canon, cfg2, adj]
        """)
        violations = scanner.scan_function_body(
            body, "_resolve_partial", "x.py")
        self.assertEqual(len(violations), 1,
                         "only cfg2 should be flagged")
        self.assertIn("cfg2.yaml", violations[0])


class TestScanFileIntegration(unittest.TestCase):
    """V37.9.94: scan_file end-to-end with temp Python files."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write_py(self, name, source):
        path = Path(self.tmpdir) / name
        path.write_text(source, encoding="utf-8")
        return path

    def test_clean_file_no_violations(self):
        source = textwrap.dedent("""
        import os
        def _resolve_path():
            candidates = [
                os.path.expanduser("~/cfg.yaml"),
                os.path.expanduser("~/openclaw-model-bridge/cfg.yaml"),
                os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "cfg.yaml"),
            ]
            return candidates[0]
        """)
        path = self._write_py("clean.py", source)
        v = scanner.scan_file(path, root=Path(self.tmpdir))
        self.assertEqual(v, [])

    def test_violating_file_caught(self):
        source = textwrap.dedent("""
        import os
        def _resolve_path():
            candidates = [
                os.path.expanduser("~/cfg.yaml"),
                os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "cfg.yaml"),
            ]
            return candidates[0]
        """)
        path = self._write_py("violating.py", source)
        v = scanner.scan_file(path, root=Path(self.tmpdir))
        self.assertEqual(len(v), 1)
        self.assertIn("cfg.yaml", v[0])

    def test_syntax_error_silently_skipped(self):
        """Unparseable file must not crash scanner."""
        path = self._write_py("bad.py", "def foo(::: invalid syntax")
        v = scanner.scan_file(path, root=Path(self.tmpdir))
        self.assertEqual(v, [])


class TestScanRepoEndToEnd(unittest.TestCase):
    """V37.9.94: full repo scan, current state must be 0 violations."""

    def test_current_repo_zero_violations(self):
        """V37.9.94 baseline: repo has been audited + cleaned, scan = 0."""
        count, violations = scanner.scan_repo()
        self.assertGreater(count, 0, "must scan at least 1 file")
        self.assertEqual(violations, [],
                         f"current repo must be MR-15 compliant; found: "
                         f"{violations}")

    def test_known_resolvers_in_scope(self):
        """Verify daily_observer.py is scanned (sanity)."""
        files = scanner.gather_python_files()
        names = {f.name for f in files}
        self.assertIn("daily_observer.py", names)
        self.assertIn("expert_escalation.py", names)

    def test_test_files_excluded(self):
        files = scanner.gather_python_files()
        test_files = [f for f in files if f.name.startswith("test_")]
        self.assertEqual(test_files, [],
                         "test_*.py files must be excluded")

    def test_scanner_excludes_self(self):
        files = scanner.gather_python_files()
        names = {f.name for f in files}
        self.assertNotIn("cross_env_path_scanner.py", names,
                         "scanner must exclude itself")


class TestCliBehavior(unittest.TestCase):
    """V37.9.94: CLI entry — exit codes + --file option."""

    def test_clean_repo_exits_zero(self):
        r = subprocess.run(
            ["python3", str(SCANNER_PATH)],
            capture_output=True, text=True, timeout=30
        )
        self.assertEqual(r.returncode, 0,
                         f"clean repo must exit 0; stderr:\n{r.stderr}")
        self.assertIn("V37.9.94", r.stdout)
        self.assertIn("Mac Mini canonical", r.stdout)

    def test_single_file_clean_exits_zero(self):
        r = subprocess.run(
            ["python3", str(SCANNER_PATH), "--file",
             str(SCANNER_PATH.parent / "daily_observer.py")],
            capture_output=True, text=True, timeout=10
        )
        self.assertEqual(r.returncode, 0)

    def test_single_file_missing_exits_two(self):
        r = subprocess.run(
            ["python3", str(SCANNER_PATH), "--file", "/nonexistent.py"],
            capture_output=True, text=True, timeout=10
        )
        self.assertEqual(r.returncode, 2)
        self.assertIn("not found", r.stderr)


class TestReverseValidation(unittest.TestCase):
    """V37.9.94: sabotage a real resolver and confirm scanner catches."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_sabotaged_resolver_caught(self):
        """Construct sabotaged version of daily_observer._resolve_registry_path
        (missing canonical) — scanner must flag."""
        sabotaged = textwrap.dedent("""
        import os

        # SABOTAGED VERSION — V37.9.92 canonical removed
        def _resolve_registry_path():
            candidates = [
                os.path.expanduser("~/jobs_registry.yaml"),
                os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "jobs_registry.yaml"),
            ]
            for c in candidates:
                if os.path.isfile(c):
                    return c
            return None
        """)
        path = Path(self.tmpdir) / "fake_daily_observer.py"
        path.write_text(sabotaged, encoding="utf-8")
        violations = scanner.scan_file(path, root=Path(self.tmpdir))
        self.assertEqual(len(violations), 1,
                         "sabotaged resolver MUST be caught")
        self.assertIn("_resolve_registry_path", violations[0])
        self.assertIn("MR-15", violations[0])

    def test_restoring_canonical_clears_violation(self):
        """Adding canonical back clears the violation."""
        good = textwrap.dedent("""
        import os
        def _resolve_registry_path():
            candidates = [
                os.path.expanduser("~/jobs_registry.yaml"),
                os.path.expanduser("~/openclaw-model-bridge/jobs_registry.yaml"),
                os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "jobs_registry.yaml"),
            ]
            return candidates[0]
        """)
        path = Path(self.tmpdir) / "good_observer.py"
        path.write_text(good, encoding="utf-8")
        violations = scanner.scan_file(path, root=Path(self.tmpdir))
        self.assertEqual(violations, [],
                         "compliant resolver must NOT be flagged")


class TestV37_9_94_SourceLevelGuards(unittest.TestCase):
    """V37.9.94 source-level guards — prevent scanner-self regression."""

    @classmethod
    def setUpClass(cls):
        cls.src = SCANNER_PATH.read_text(encoding="utf-8")

    def test_v37_9_94_marker(self):
        self.assertIn("V37.9.94", self.src)
        self.assertIn("V37_9_94_MARKER", self.src)

    def test_mr_15_referenced(self):
        self.assertIn("MR-15", self.src,
                     "scanner must reference MR-15 meta rule")
        # 4 prior occurrences for context
        for ver in ("V37.9.56", "V37.9.76", "V37.9.78", "V37.9.92"):
            self.assertIn(ver, self.src,
                         f"must document {ver} prior occurrence")

    def test_canonical_path_string_referenced(self):
        self.assertIn("openclaw-model-bridge", self.src)

    def test_fail_close_contract(self):
        """Scanner must FAIL-CLOSE (exit 1) on violations."""
        self.assertIn("FAIL-CLOSE", self.src,
                     "FAIL-CLOSE contract must be documented")
        self.assertIn("sys.exit(1)", self.src,
                     "must call sys.exit(1) on violations")

    def test_excludes_test_files(self):
        self.assertIn("test_", self.src,
                     "must mention test_ exclusion logic")

    def test_excludes_sister_scanners(self):
        self.assertIn("EXCLUDED_FILES", self.src)
        self.assertIn("cross_os_quirk_scanner.py", self.src,
                     "must exclude V37.9.67 sister scanner")

    def test_config_suffixes_define_scope(self):
        """Only config-like files (.yaml/.yml/.json/.md) are considered."""
        for suf in (".yaml", ".yml", ".json", ".md"):
            self.assertIn(suf, self.src,
                         f"{suf} must be in CONFIG_FILE_SUFFIXES")


if __name__ == "__main__":
    unittest.main()
