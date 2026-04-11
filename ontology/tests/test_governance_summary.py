#!/usr/bin/env python3
"""
test_governance_summary.py — regression test for the governance silent error bug

Background
----------
On 2026-04-11 a three-layer nested bug in the governance stack was uncovered:

  1. INV-CRON-003/004 used naive substring matching; broken by Map-Reduce split
     scheduling of kb_dream.sh. → fixed in 2937198 via endswith + word-boundary.

  2. The fix introduced an `exec()` scope trap: a `def` helper inside exec'd
     code is invisible to generator expressions (their own scope can only see
     module globals and enclosing function locals, not exec locals).
     → fixed in bf454e1 via plain for-loop.

  3. `governance_checker.print_results()` only counted `status=="fail"` toward
     `failed_invs`, silently ignoring `status=="error"`. So the audit reported
     "✅ 所有不变式成立" while three 💥 icons hid in the body. This bug had
     existed for months in the checker; bug #2 was the first time a check
     actually raised an exception in production, which finally made bug #3
     observable to a human.
     → fixed in bf454e1 by treating both "fail" and "error" as not-passing.

Bug #3 is the observer's self-blindness: the governance system had never
applied MR-4 (silent-failure-is-a-bug) to itself. This file is the regression
test for bug #3 — the first enforcement of new meta-rule MR-7
(governance-execution-is-self-observable).

See: ontology/docs/cases/governance_silent_error_case.md
"""

import contextlib
import io
import os
import sys
import unittest

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_ONTOLOGY_DIR = os.path.dirname(_TESTS_DIR)
_PROJECT_ROOT = os.path.dirname(_ONTOLOGY_DIR)
for p in [_ONTOLOGY_DIR, _PROJECT_ROOT]:
    if p not in sys.path:
        sys.path.insert(0, p)

import governance_checker  # noqa: E402


def _synthetic_result(status, check_status=None):
    """Build a minimal governance result with an injected top-level status."""
    return {
        "id": "INV-FAKE",
        "name": "synthetic-probe",
        "status": status,
        "severity": "critical",
        "meta_rule": "MR-7",
        "declaration": "injected for silent-error regression",
        "checks": [
            {
                "name": "synthetic",
                "status": check_status or status,
                "message": "NameError: synthetic" if status == "error" else "",
            }
        ],
    }


def _run_print_results(results):
    """Call print_results with JSON_MODE disabled and capture stdout + exit code."""
    saved_json_mode = governance_checker.JSON_MODE
    governance_checker.JSON_MODE = False
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            exit_code = governance_checker.print_results(results)
    finally:
        governance_checker.JSON_MODE = saved_json_mode
    return buf.getvalue(), exit_code


class TestSummaryDoesNotSwallowError(unittest.TestCase):
    """The summary line must never say '✅ 所有不变式成立' when the results
    include a check that errored. Bug #3 did exactly that."""

    def test_error_status_surfaces_in_summary_text(self):
        """The literal ✅ 所有不变式成立 must not appear when any invariant errored."""
        results = [_synthetic_result("error")]
        out, _ = _run_print_results(results)
        self.assertNotIn(
            "所有不变式成立",
            out,
            "silent-error regression: summary said '所有不变式成立' "
            "while an invariant was in 'error' state",
        )

    def test_error_status_surfaces_as_nonzero_exit(self):
        """print_results must return a non-zero value when error is present."""
        results = [_synthetic_result("error")]
        _, exit_code = _run_print_results(results)
        self.assertNotEqual(
            exit_code,
            0,
            f"silent-error regression: print_results returned {exit_code} "
            "despite an 'error' invariant",
        )

    def test_error_summary_uses_bomb_icon(self):
        """Bug fix must distinguish fail from error in the summary line."""
        results = [_synthetic_result("error")]
        out, _ = _run_print_results(results)
        self.assertIn(
            "💥",
            out,
            "summary must surface 💥 when an invariant execution errored",
        )

    def test_pure_fail_still_uses_cross_icon(self):
        """Sanity: hard failures (fail, not error) still use ❌."""
        results = [_synthetic_result("fail")]
        out, _ = _run_print_results(results)
        self.assertIn(
            "❌",
            out,
            "summary must still use ❌ for hard failures",
        )
        # And must NOT use the bomb for pure fail
        last_two_lines = out.rstrip().splitlines()[-3:]
        self.assertFalse(
            any("💥" in l and "出错" in l for l in last_two_lines),
            "pure fail must not be labeled as '出错' in the summary",
        )

    def test_all_pass_still_says_all_invariants_hold(self):
        """Sanity: the happy path still produces the ✅ summary line."""
        results = [
            {
                "id": "INV-OK",
                "name": "happy-path",
                "status": "pass",
                "severity": "critical",
                "meta_rule": "MR-1",
                "declaration": "synthetic all-pass",
                "checks": [{"name": "ok", "status": "pass", "message": ""}],
            }
        ]
        out, exit_code = _run_print_results(results)
        self.assertIn("所有不变式成立", out)
        self.assertEqual(exit_code, 0)

    def test_mixed_fail_and_error_are_both_counted(self):
        """When both fail and error are present, summary must count both."""
        results = [
            _synthetic_result("fail"),
            _synthetic_result("error"),
        ]
        out, exit_code = _run_print_results(results)
        self.assertNotIn("所有不变式成立", out)
        # Bug fix summary format: "❌ N 违反, 💥 M 出错"
        self.assertIn("违反", out)
        self.assertIn("出错", out)
        # failed_invs now counts both, so exit code must reflect 2
        self.assertEqual(
            exit_code,
            2,
            f"expected exit_code=2 (1 fail + 1 error), got {exit_code}",
        )


class TestFailedInvsCountsBothFailAndError(unittest.TestCase):
    """Source-level guard: the condition in print_results must accept both
    'fail' and 'error'. This catches the regression at the grep level even
    if the runtime test above is somehow bypassed."""

    def test_print_results_source_treats_error_as_not_passing(self):
        path = os.path.join(_PROJECT_ROOT, "ontology", "governance_checker.py")
        with open(path) as f:
            content = f.read()
        # The fixed form must appear
        self.assertIn(
            'r["status"] in ("fail", "error")',
            content,
            "governance_checker.print_results no longer treats 'error' as "
            "not-passing — silent error bug has regressed",
        )
        # The broken form must NOT appear (strict equality-only check)
        self.assertNotIn(
            'if r["status"] == "fail":\n            failed_invs += 1',
            content,
            "governance_checker.print_results still uses the pre-fix pattern "
            "that silently ignored 'error' status",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
