#!/usr/bin/env python3
"""
test_watchdog_freshness.py — V37.9.6 INV-WATCHDOG-FRESHNESS-001 regression

Locks the awk-based line-level timestamp filter in job_watchdog.sh:scan_logs():
  - Errors with timestamps within 24h MUST be reported
  - Errors with timestamps older than 24h MUST be filtered out
  - Untimestamped lines (e.g. Python Traceback continuations) follow the
    in_recent state of the previous timestamped line

Background
----------
Before V37.9.6, watchdog scan_logs() only filtered by file mtime (24h).
But `tail -50` could include lines from many days ago in actively-updated
log files, causing watchdog to repeatedly report 13-day-old errors:

  • kb_evening 4/14-15 errors (already closed by V37.8.10)
  • openclaw_discussions 4/8 errors
  • etc.

12:30 daily watchdog alert showed 6/12 alerts were stale ghosts. Alert
fatigue is real cost. V37.9.6 adds line-level timestamp filtering via
awk: parses [YYYY-MM-DD ...] prefix, keeps only lines within 24h window.
"""

import os
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import date, timedelta

_HERE = os.path.dirname(os.path.abspath(__file__))


def _run_awk_filter(log_content, today_str=None, cutoff_str=None):
    """Run the same awk filter as job_watchdog.sh scan_logs() in a subprocess.

    Returns the filtered lines (only those within [cutoff, today] window
    plus untimestamped continuation lines that follow recent ones).
    """
    if today_str is None:
        today_str = date.today().isoformat()
    if cutoff_str is None:
        cutoff_str = (date.today() - timedelta(days=1)).isoformat()

    awk_script = '''
        /\\[([0-9]{4}-[0-9]{2}-[0-9]{2})/ {
            if (match($0, /[0-9]{4}-[0-9]{2}-[0-9]{2}/)) {
                ts_date = substr($0, RSTART, RLENGTH)
                if (ts_date >= cutoff && ts_date <= today) {
                    print
                    in_recent = 1
                } else {
                    in_recent = 0
                }
                next
            }
        }
        in_recent { print }
    '''

    proc = subprocess.run(
        ["awk", "-v", f"cutoff={cutoff_str}", "-v", f"today={today_str}",
         awk_script],
        input=log_content, capture_output=True, text=True, timeout=10,
    )
    assert proc.returncode == 0, f"awk failed: {proc.stderr}"
    return proc.stdout.strip()


class TestTimestampFilter(unittest.TestCase):
    """V37.9.6: line-level timestamp filtering core semantics."""

    def setUp(self):
        self.today = date.today().isoformat()
        self.yesterday = (date.today() - timedelta(days=1)).isoformat()
        self.old_13d = (date.today() - timedelta(days=13)).isoformat()
        self.old_7d = (date.today() - timedelta(days=7)).isoformat()

    def test_recent_error_kept(self):
        log = f"[{self.today} 08:00:00] ERROR: today's error\n"
        result = _run_awk_filter(log, self.today, self.yesterday)
        self.assertIn("today's error", result)

    def test_yesterday_error_kept(self):
        log = f"[{self.yesterday} 11:00:00] ERROR: yesterday's error\n"
        result = _run_awk_filter(log, self.today, self.yesterday)
        self.assertIn("yesterday's error", result)

    def test_old_error_filtered(self):
        log = f"[{self.old_13d} 22:00:00] ERROR: 13d ago should be filtered\n"
        result = _run_awk_filter(log, self.today, self.yesterday)
        self.assertNotIn("13d ago", result)

    def test_old_traceback_multiline_filtered(self):
        """Python Traceback (无时间戳多行) following old timestamped error → filtered."""
        log = (
            f"[{self.old_7d} 22:00:00] ERROR: old root error\n"
            "Traceback (most recent call last):\n"
            "  File 'foo.py', line 42, in bar\n"
            "    raise ValueError('boom')\n"
            "ValueError: boom\n"
        )
        result = _run_awk_filter(log, self.today, self.yesterday)
        self.assertNotIn("old root error", result)
        self.assertNotIn("Traceback", result, "Old Traceback continuation must be filtered")
        self.assertNotIn("ValueError", result)

    def test_recent_traceback_multiline_kept(self):
        """Python Traceback following recent timestamped error → kept."""
        log = (
            f"[{self.today} 12:00:00] ERROR: fresh root error\n"
            "Traceback (most recent call last):\n"
            "  File 'foo.py', line 99\n"
            "ValueError: fresh boom\n"
        )
        result = _run_awk_filter(log, self.today, self.yesterday)
        self.assertIn("fresh root error", result)
        self.assertIn("Traceback", result, "Recent Traceback continuation must be kept")
        self.assertIn("fresh boom", result)

    def test_mixed_old_and_new_only_keeps_new(self):
        """The exact scenario watchdog 12:30 alert reported."""
        log = (
            f"[{self.old_13d} 22:00:00] ERROR: kb_evening LLM 502 (V37.8.10 closed)\n"
            f"[2026-04-08 08:15:00] ERROR: openclaw_discussions push fail\n"
            f"[{self.today} 08:00:00] ERROR: real today error\n"
            f"[{self.today} 12:00:00] HTTP Error 500: today\n"
        )
        result = _run_awk_filter(log, self.today, self.yesterday)
        self.assertNotIn("V37.8.10 closed", result, "Stale 13d kb_evening error must NOT report")
        self.assertNotIn("4/8 ERROR", result.replace("2026-04-08", "4/8 ERROR")
                         if "2026-04-08" in result else "")
        self.assertNotIn("openclaw_discussions push fail", result,
                         "Stale 4/8 discussions error must NOT report")
        self.assertIn("real today error", result)
        self.assertIn("HTTP Error 500", result)

    def test_state_resets_between_old_and_new(self):
        """If an old timestamped line follows a recent one, in_recent goes to 0
        and old continuations are dropped."""
        log = (
            f"[{self.today} 08:00:00] OK: today success\n"
            f"[{self.old_13d} 22:00:00] ERROR: stale\n"
            "  stale continuation\n"
            f"[{self.today} 12:00:00] ERROR: today fail\n"
            "  today continuation\n"
        )
        result = _run_awk_filter(log, self.today, self.yesterday)
        self.assertIn("today success", result)
        self.assertIn("today fail", result)
        self.assertIn("today continuation", result)
        self.assertNotIn("stale continuation", result,
                         "Old continuation must be filtered")
        self.assertNotIn("ERROR: stale", result)


class TestWatchdogShellInvariants(unittest.TestCase):
    """Source-level guards on job_watchdog.sh."""

    def setUp(self):
        with open(os.path.join(_HERE, "job_watchdog.sh"), "r", encoding="utf-8") as f:
            self.source = f.read()

    def test_v37_9_6_marker_present(self):
        self.assertIn("V37.9.6", self.source,
                      "job_watchdog.sh must mark V37.9.6 freshness fix")

    def test_awk_timestamp_filter_present(self):
        """The awk-based line-level filter must be present in scan_logs()."""
        self.assertIn("awk", self.source)
        self.assertIn("cutoff_date", self.source)
        self.assertIn("in_recent", self.source)

    def test_old_naive_tail_50_grep_pattern_removed(self):
        """The old `tail -50 ... | grep -ciE` pattern (no time filter)
        should no longer appear as the primary scan path in scan_logs."""
        # The pattern remains in 'recent_fails' line but should now operate on
        # already-filtered $recent_window, not the raw tail
        self.assertIn("recent_window", self.source)
        # Specifically: scan_logs should not have raw `tail -50 "$logfile"` directly
        # piped to grep WITHOUT going through the awk filter first
        scan_logs_block = self.source[self.source.find("scan_logs()"):]
        scan_logs_block = scan_logs_block[:scan_logs_block.find("\n}\n") + 3]
        # Must reference recent_window in the grep pipeline
        self.assertIn("echo \"$recent_window\" | grep", scan_logs_block,
                      "grep must operate on filtered recent_window, not raw tail")

    def test_acl_threshold_widened_to_14d(self):
        """ACL Anthology threshold must be 14 days (1209600s), not 8 days."""
        self.assertIn("1209600", self.source,
                      "ACL acl_anthology threshold must be 14d (1209600s)")
        self.assertNotIn("acl_anthology|" + "$" + "HOME/.openclaw/jobs/acl_anthology"
                         "/cache/last_run.json|691200", self.source,
                         "Old 8d ACL threshold (691200) must not remain")


class TestRssBlogsCleanup(unittest.TestCase):
    """V37.9.6: LangChain RSS dead-link cleanup."""

    def test_langchain_feed_commented_out(self):
        path = os.path.join(_HERE, "jobs/rss_blogs/run_rss_blogs.sh")
        with open(path, "r", encoding="utf-8") as f:
            source = f.read()
        # The active line must NOT have LangChain feed
        for line in source.split("\n"):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue  # commented-out is OK
            if "blog.langchain.dev" in stripped and not stripped.startswith("#"):
                self.fail(f"LangChain RSS still active: {line}")
        # And V37.9.6 marker present
        self.assertIn("V37.9.6", source)


if __name__ == "__main__":
    unittest.main()
