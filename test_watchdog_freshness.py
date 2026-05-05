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

    def test_v37_9_28_f3_hn_threshold_widened(self):
        """V37.9.28 F3: HN threshold 25200 (7h) → 50400 (14h) 修正 schedule drift.
        jobs_registry 实际 schedule '45 8,14,20 * * *' 最大 gap 12h, 7h 阈值
        导致 overnight 20:45→08:45 必报警. 14h = 12h max gap + 2h slack."""
        # 新阈值必须存在
        self.assertIn("|50400|HN热帖抓取", self.source,
                      "HN run_hn_fixed threshold must be 50400 (14h) per V37.9.28 F3")
        # 旧阈值必须移除
        self.assertNotIn("|25200|HN热帖抓取", self.source,
                         "Old HN 7h threshold (25200) must not remain")
        # 解释注释存在
        self.assertIn("V37.9.28 F3", self.source,
                      "job_watchdog.sh must mark V37.9.28 F3 schedule-threshold alignment")

    def test_acl_threshold_widened_to_28d(self):
        """V37.9.8: ACL Anthology threshold must be 28 days (2419200s).
        Trajectory: 8d (V37.9.6 original) → 14d (V37.9.6 first widen) →
                    28d (V37.9.8 after root cause: cron 4/15 miss + year cycle)."""
        self.assertIn("2419200", self.source,
                      "ACL acl_anthology threshold must be 28d (2419200s) per V37.9.8")
        # Old values must not remain
        self.assertNotIn("acl_anthology|" + "$" + "HOME/.openclaw/jobs/acl_anthology"
                         "/cache/last_run.json|691200", self.source,
                         "Old 8d ACL threshold (691200) must not remain")
        self.assertNotIn("acl_anthology|" + "$" + "HOME/.openclaw/jobs/acl_anthology"
                         "/cache/last_run.json|1209600", self.source,
                         "Intermediate 14d ACL threshold (1209600) must not remain")


def _run_alert_format(recent_window):
    """Run the V37.9.28 F1 alert-format logic on a recent_window string.

    Returns the formatted alert line that scan_logs() would push to ALERTS,
    or empty string if no errors. Mirrors the bash code in job_watchdog.sh
    (drift caught by TestF1AlertFormatShellGuards source-level checks).
    """
    err_pattern = (
        '推送失败|send_failed|fetch_failed|FAIL(ED)?:|ERROR[: ]|Traceback|'
        'HTTP[/ ](4[0-9]{2}|5[0-9]{2})'
    )
    bash_code = r'''
        err_pattern="$1"
        recent_window=$(cat)
        recent_fails=$(echo "$recent_window" | grep -ciE "$err_pattern" || true)
        if [ "$recent_fails" -gt 0 ]; then
            last_err=$(echo "$recent_window" | grep -iE "$err_pattern" | tail -1 | head -c 120)
            err_ts=$(echo "$recent_window" | grep -iE "$err_pattern" | \
                     grep -oE '[0-9]{4}-[0-9]{2}-[0-9]{2}[T ][0-9]{2}:[0-9]{2}' | sort -u)
            if [ -n "$err_ts" ]; then
                oldest=$(echo "$err_ts" | head -1)
                newest=$(echo "$err_ts" | tail -1)
                if [ "$oldest" = "$newest" ]; then
                    ts_info=" (@ $oldest)"
                else
                    ts_info=" (最早 $oldest, 最新 $newest)"
                fi
            else
                ts_info=" (时间戳缺失)"
            fi
            echo "test_job 日志: ${recent_fails}条错误${ts_info} → $last_err"
        fi
    '''
    proc = subprocess.run(
        ["bash", "-c", bash_code, "_", err_pattern],
        input=recent_window, capture_output=True, text=True, timeout=10,
    )
    assert proc.returncode == 0, f"bash failed: {proc.stderr}"
    return proc.stdout.strip()


class TestF1AlertFormatTimestampRange(unittest.TestCase):
    """V37.9.28 F1: scan_logs ALERTS 输出包含错误行时间戳分布 (最早 X, 最新 Y)。
    用户 2026-05-05 周一观察反馈: '主要是一些 health 监控推送没有严格的时间戳' """

    def test_multiple_errors_at_different_times_show_range(self):
        """Multiple errors spanning 5 hours → '(最早 ..., 最新 ...)' format."""
        log = (
            "[2026-05-05 02:14:32] arxiv_monitor: ERROR: rsync(24683): error\n"
            "[2026-05-05 03:45:12] arxiv_monitor: ERROR: rsync(24684): error\n"
            "[2026-05-05 07:32:01] arxiv_monitor: ERROR: HTTP 500 internal error\n"
        )
        result = _run_alert_format(log)
        self.assertIn("3条错误", result)
        self.assertIn("最早 2026-05-05 02:14", result)
        self.assertIn("最新 2026-05-05 07:32", result)
        self.assertNotIn("时间戳缺失", result)

    def test_single_timestamp_uses_at_marker(self):
        """All errors at same minute → '(@ X)' compact format."""
        log = "[2026-05-05 08:30:00] kb_evening: ERROR: HTTP 502 Bad Gateway\n"
        result = _run_alert_format(log)
        self.assertIn("1条错误", result)
        self.assertIn("(@ 2026-05-05 08:30)", result)
        self.assertNotIn("最早", result)
        self.assertNotIn("最新", result)

    def test_no_timestamp_marks_missing(self):
        """Only Traceback continuation lines (no timestamp) → '(时间戳缺失)'."""
        log = (
            "Traceback (most recent call last):\n"
            "  File 'foo.py', line 42, in bar\n"
            "    raise ValueError('boom')\n"
            "ValueError: boom\n"
        )
        result = _run_alert_format(log)
        self.assertIn("时间戳缺失", result)
        self.assertIn("条错误", result)

    def test_user_observation_blood_lesson_scenario(self):
        """直接复现用户 2026-05-05 08:30 看到的 WhatsApp 截图场景：
        13+ 条 rsync EOF 错误，但用户看不出何时发生 — 修复后必须能看出范围。
        本测试构造同款数据，断言修复后用户能看到 (最早 X, 最新 Y) 信息。"""
        log = (
            "[2026-05-04 22:10:01] kb_evening: ERROR: rsync(24683): error: unexpected end of file\n"
            "[2026-05-04 23:15:02] kb_evening: ERROR: rsync(24684): error: unexpected end of file\n"
            "[2026-05-05 00:30:03] kb_evening: ERROR: rsync(24685): error: unexpected end of file\n"
            "[2026-05-05 02:00:04] kb_evening: ERROR: rsync(24686): error: unexpected end of file\n"
            "[2026-05-05 05:45:05] kb_evening: ERROR: rsync(24687): error: unexpected end of file\n"
            "[2026-05-05 08:00:06] kb_evening: ERROR: rsync(24688): error: unexpected end of file\n"
        )
        result = _run_alert_format(log)
        self.assertIn("6条错误", result)
        # 用户现在能立刻看出 10 小时跨度（22:10 → 08:00），不是某瞬间爆发
        self.assertIn("最早 2026-05-04 22:10", result)
        self.assertIn("最新 2026-05-05 08:00", result)

    def test_mixed_timestamped_and_continuation_lines(self):
        """ERROR 行有时间戳 + Traceback 续行无时间戳 → 仍能从 ERROR 行提取范围。"""
        log = (
            "[2026-05-05 08:00:00] foo: ERROR: first error\n"
            "Traceback (most recent call last):\n"
            "  File 'foo.py', line 99\n"
            "ValueError: continuation\n"
            "[2026-05-05 09:30:00] foo: ERROR: second error\n"
        )
        result = _run_alert_format(log)
        # 应至少匹配 2 条 ERROR + Traceback + ValueError
        self.assertIn("最早 2026-05-05 08:00", result)
        self.assertIn("最新 2026-05-05 09:30", result)


class TestF1AlertFormatShellGuards(unittest.TestCase):
    """V37.9.28 F1: 源码层守卫防止 job_watchdog.sh 修复后回归。"""

    def setUp(self):
        with open(os.path.join(_HERE, "job_watchdog.sh"), "r", encoding="utf-8") as f:
            self.source = f.read()

    def test_v37_9_28_marker_present(self):
        self.assertIn("V37.9.28 F1", self.source,
                      "job_watchdog.sh must mark V37.9.28 F1 timestamp visibility fix")

    def test_grep_oE_timestamp_pattern_present(self):
        """The grep -oE timestamp extraction pattern must be present in scan_logs."""
        self.assertIn(
            "grep -oE '[0-9]{4}-[0-9]{2}-[0-9]{2}[T ][0-9]{2}:[0-9]{2}'",
            self.source,
            "scan_logs must use grep -oE to extract YYYY-MM-DD HH:MM timestamps")

    def test_alert_format_includes_ts_info(self):
        """The ALERTS+= line must include ts_info variable, not just last_err."""
        # Find the scan_logs ALERTS line; it must reference ts_info
        scan_logs_block = self.source[self.source.find("scan_logs()"):]
        scan_logs_block = scan_logs_block[:scan_logs_block.find("\n}\n") + 3]
        self.assertIn("${ts_info}", scan_logs_block,
                      "ALERTS+= must use ${ts_info} for timestamp range visibility")
        self.assertIn('ts_info=" (@ $oldest)"', scan_logs_block,
                      "Single-timestamp branch must use '(@ X)' format")
        self.assertIn('ts_info=" (最早 $oldest, 最新 $newest)"', scan_logs_block,
                      "Multi-timestamp branch must use '(最早 X, 最新 Y)' format")
        self.assertIn('ts_info=" (时间戳缺失)"', scan_logs_block,
                      "No-timestamp branch must use '(时间戳缺失)' marker")

    def test_old_naked_alert_format_removed(self):
        """The old format '${recent_fails}条错误 → $last_err' (no timestamp)
        must NOT appear as the only ALERTS+= pattern. There should be a
        ${ts_info} between 条错误 and →."""
        # Match the ALERTS+= line specifically
        for line in self.source.split("\n"):
            if "ALERTS+=" in line and "条错误" in line and "$last_err" in line:
                # Found the alert line; it must have ts_info between 条错误 and →
                self.assertIn("${ts_info}", line,
                              f"ALERTS line missing ts_info: {line.strip()}")


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
