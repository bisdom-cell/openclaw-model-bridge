#!/usr/bin/env python3
"""
test_governance_load_timeout.py — V37.9.285 executor-level load-timeout classification

Background
----------
2026-07-03/05 the 07:00 governance cron 💥'd repeatedly: runtime checks that
spawn subprocesses (kb_review.sh collector / a FULL inner governance audit)
were CPU-starved under the cron storm, subprocess.run(timeout=...) raised
subprocess.TimeoutExpired, and _exec_python_assert's generic `except Exception`
classified it as status="error" → 💥 SYSTEM_ALERT for an environment condition,
not an invariant violation. V37.9.214 (INV-REVIEW-001) and V37.9.247
(INV-CONVERGENCE-INTEGRATION-001) each hardened one check *inside its yaml
code* — two copies of the same try/except pattern — while the systematic survey
(2026-08-03, unfinished "治理 runtime check 对负载敏感 待系统性评估") found ~51
more subprocess-spawning checks (44 python_assert with unhandled timeout= +
command_succeeds/env_var_exists executors) with the same latent exposure.

V37.9.285 closes the family at the single point every check flows through:
the three subprocess-capable executors (_exec_python_assert,
_exec_command_succeeds, _exec_env_var_exists) classify subprocess.TimeoutExpired
as status="skip" with a visible [LOAD-TIMEOUT] message (observable per MR-7,
counted as ⏭ in the summary; a load storm now surfaces as a skip_rate_pct
spike in the audit self-metrics instead of a false 💥) instead of "error".
Real regressions still manifest fast when the subprocess COMPLETES (assertions
run on its output); true hangs still surface via job_watchdog last_run
staleness and via full_regression running the same suites directly without
these budgets.

One mechanism instead of 44 more yaml copies (MR-8 copy-paste-is-a-bug-class /
一物一形). The two existing in-yaml handlers remain valid: they catch inside
the check code, return normally, and never reach the executor branch.
"""

import inspect
import os
import re
import subprocess as sp
import sys
import unittest
from unittest import mock

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_ONTOLOGY_DIR = os.path.dirname(_TESTS_DIR)
_PROJECT_ROOT = os.path.dirname(_ONTOLOGY_DIR)
for p in [_ONTOLOGY_DIR, _PROJECT_ROOT]:
    if p not in sys.path:
        sys.path.insert(0, p)

import governance_checker as gc  # noqa: E402


class TestPythonAssertLoadTimeout(unittest.TestCase):
    """Behavior: TimeoutExpired escaping a python_assert → skip, never error."""

    def test_timeout_expired_classified_skip(self):
        check = {
            "name": "synthetic-load-timeout",
            "check_type": "python_assert",
            "code": (
                "import subprocess\n"
                "raise subprocess.TimeoutExpired(cmd='python3 -m unittest test_x', timeout=60)"
            ),
        }
        status, msg = gc._exec_python_assert(check)
        self.assertEqual(status, "skip",
                         "V37.9.285: load-timeout must classify as skip, not error/💥")
        self.assertIn("[LOAD-TIMEOUT]", msg)

    def test_real_subprocess_timeout_classified_skip(self):
        # End-to-end without mocks: a real subprocess exceeding its budget,
        # exactly the shape of the 51 unhardened runtime checks.
        check = {
            "name": "real-subprocess-timeout",
            "check_type": "python_assert",
            "code": (
                "import subprocess\n"
                "subprocess.run(['sleep', '5'], timeout=0.2)"
            ),
        }
        status, msg = gc._exec_python_assert(check)
        self.assertEqual(status, "skip")
        self.assertIn("[LOAD-TIMEOUT]", msg)

    def test_assertion_error_still_fail(self):
        check = {"name": "x", "check_type": "python_assert",
                 "code": "assert False, 'real violation'"}
        status, msg = gc._exec_python_assert(check)
        self.assertEqual(status, "fail")
        self.assertIn("real violation", msg)

    def test_generic_exception_still_error(self):
        # INV-GOV-001 semantics preserved: real execution errors stay 💥.
        check = {"name": "x", "check_type": "python_assert",
                 "code": "raise RuntimeError('boom')"}
        status, msg = gc._exec_python_assert(check)
        self.assertEqual(status, "error")
        self.assertIn("RuntimeError", msg)

    def test_completed_subprocess_bad_output_still_fails(self):
        # The other half of the V37.9.214 tradeoff: when the subprocess
        # COMPLETES, assertions run and a real regression manifests as fail —
        # the timeout classification must not blunt detection on this path.
        check = {
            "name": "x",
            "check_type": "python_assert",
            "code": (
                "import subprocess\n"
                "r = subprocess.run(['echo', 'FAILED'], capture_output=True, text=True, timeout=30)\n"
                "assert 'OK' in r.stdout, 'suite regressed: ' + r.stdout.strip()"
            ),
        }
        status, msg = gc._exec_python_assert(check)
        self.assertEqual(status, "fail")
        self.assertIn("suite regressed", msg)


class TestRunInvariantIntegration(unittest.TestCase):
    """skip keeps the invariant 成立 but stays observable (MR-7)."""

    def test_load_timeout_keeps_invariant_pass_and_observable(self):
        inv = {"id": "INV-SYNTH", "checks": [
            {"name": "lt", "check_type": "python_assert",
             "code": "import subprocess\nraise subprocess.TimeoutExpired(cmd='x', timeout=60)"},
            {"name": "ok", "check_type": "python_assert", "code": "assert True"},
        ]}
        worst, results = gc.run_invariant(inv)
        self.assertEqual(worst, "pass",
                         "skip must not degrade the invariant (dev-mode skip precedent)")
        self.assertEqual(results[0]["status"], "skip")
        self.assertIn("[LOAD-TIMEOUT]", results[0]["message"])
        self.assertEqual(results[1]["status"], "pass")

    def test_error_still_degrades_invariant(self):
        inv = {"id": "INV-SYNTH", "checks": [
            {"name": "err", "check_type": "python_assert", "code": "raise ValueError('x')"},
        ]}
        worst, _ = gc.run_invariant(inv)
        self.assertEqual(worst, "error")


class TestCommandSucceedsLoadTimeout(unittest.TestCase):
    """_exec_command_succeeds has a fixed timeout=30 — same family, same fix."""

    def test_command_timeout_classified_skip(self):
        def _raise(*a, **kw):
            raise sp.TimeoutExpired(cmd="slow-cmd", timeout=30)
        with mock.patch.object(gc.subprocess, "run", side_effect=_raise):
            status, msg = gc._exec_command_succeeds({"name": "x", "command": "slow-cmd"})
        self.assertEqual(status, "skip")
        self.assertIn("[LOAD-TIMEOUT]", msg)

    def test_command_nonzero_still_fail(self):
        status, msg = gc._exec_command_succeeds({"name": "x", "command": "false"})
        self.assertEqual(status, "fail")

    def test_command_zero_still_pass(self):
        status, msg = gc._exec_command_succeeds({"name": "x", "command": "true"})
        self.assertEqual(status, "pass")


class TestEnvVarExistsLoadTimeout(unittest.TestCase):
    def test_env_probe_timeout_classified_skip(self):
        old = gc.FULL_MODE
        gc.FULL_MODE = True
        try:
            def _raise(*a, **kw):
                raise sp.TimeoutExpired(cmd="bash -lc", timeout=5)
            with mock.patch.object(gc.subprocess, "run", side_effect=_raise):
                status, msg = gc._exec_env_var_exists({"name": "x", "var": "HOME"})
        finally:
            gc.FULL_MODE = old
        self.assertEqual(status, "skip")
        self.assertIn("[LOAD-TIMEOUT]", msg)


class TestSourceGuards(unittest.TestCase):
    """Pin the single-point mechanism so a refactor can't silently drop it."""

    _EXECUTORS = None

    @classmethod
    def setUpClass(cls):
        cls._EXECUTORS = (
            gc._exec_python_assert,
            gc._exec_command_succeeds,
            gc._exec_env_var_exists,
        )

    # Handler starts located line-anchored: comment lines begin with '#' after
    # indentation, so prose that mentions handler syntax can never match
    # ^\s*except... (V37.9.178 lesson: guards must not be bitten by their own
    # prose — this suite's first draft was bitten twice, by "try/except" and
    # "except Exception" appearing inside the handler's comment).
    _RE_TE = re.compile(r"(?m)^\s*except subprocess\.TimeoutExpired\b")
    _RE_GE = re.compile(r"(?m)^\s*except Exception\b")

    def test_all_three_executors_catch_timeout_before_generic(self):
        for fn in self._EXECUTORS:
            src = inspect.getsource(fn)
            m_te = self._RE_TE.search(src)
            m_ge = self._RE_GE.search(src)
            self.assertIsNotNone(m_te,
                                 f"{fn.__name__}: V37.9.285 TimeoutExpired handler missing")
            self.assertIsNotNone(m_ge,
                                 f"{fn.__name__}: generic Exception handler missing (INV-GOV-001)")
            self.assertLess(m_te.start(), m_ge.start(),
                            f"{fn.__name__}: TimeoutExpired must be caught before generic Exception")

    def test_timeout_branch_returns_skip_not_error(self):
        # Slice the real handler block: from the line-anchored TimeoutExpired
        # handler to the line-anchored generic handler.
        for fn in self._EXECUTORS:
            src = inspect.getsource(fn)
            block = src[self._RE_TE.search(src).start():self._RE_GE.search(src).start()]
            self.assertIn('"skip"', block,
                          f"{fn.__name__}: LOAD-TIMEOUT must classify as skip")
            self.assertIn("LOAD-TIMEOUT", block)

    def test_marker_present(self):
        with open(os.path.join(_ONTOLOGY_DIR, "governance_checker.py"),
                  encoding="utf-8") as f:
            src = f.read()
        self.assertIn("V37.9.285", src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
