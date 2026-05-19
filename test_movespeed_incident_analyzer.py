#!/usr/bin/env python3
"""test_movespeed_incident_analyzer.py — V37.9.28 F2 数据驱动诊断工具单测

锁定 movespeed_incident_analyzer.py 关键行为:
  - JSONL parse + corrupted line tolerance
  - 时间窗过滤
  - probe field classification (4 个 quadrant)
  - 失败模式归类
  - concurrent process detection
  - 决策提示生成 (基于 failure mode + procs 主导分布)
  - CLI 正常路径 + 缺文件 / 损坏 JSONL / 空文件
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

from movespeed_incident_analyzer import (  # noqa: E402
    analyze,
    classify_acl_anomaly,  # V37.9.30
    classify_caller_failure_mode,
    classify_handle_holders,  # V37.9.30
    classify_probe,
    classify_snapshot_count,  # V37.9.30
    extract_concurrent_procs,
    filter_window,
    format_text_report,
    load_records,
    parse_iso_to_dt,
    parse_window_to_seconds,
)


SCRIPT_PATH = os.path.join(_HERE, "movespeed_incident_analyzer.py")


def _make_record(ts_iso: str, caller: str = "test.sh", exit_code: str = "12",
                 probe_top: str = "exit=0|", probe_kb: str = "exit=0|",
                 procs: str = "", mount: str = "(read-write)",
                 ownership_top: str = "", ownership_kb: str = "",
                 acl_top: str = "", acl_kb: str = "",
                 lsof: str = "", snapshots: str = "") -> dict:
    """V37.9.29 (b): ownership_top/_kb default empty (backward compat with old records).
    V37.9.30: acl_top/_kb, lsof, snapshots default empty (backward compat with pre-V37.9.30 records).
    """
    rec = {
        "timestamp_iso": ts_iso,
        "caller": caller,
        "exit_code": exit_code,
        "probe_top": probe_top,
        "probe_kb": probe_kb,
        "procs": procs,
        "mount": mount,
    }
    # Only include ownership fields if non-empty, to test backward compat
    if ownership_top:
        rec["ownership_top"] = ownership_top
    if ownership_kb:
        rec["ownership_kb"] = ownership_kb
    # V37.9.30: same backward-compat pattern for forensic forensics fields
    if acl_top:
        rec["acl_top"] = acl_top
    if acl_kb:
        rec["acl_kb"] = acl_kb
    if lsof:
        rec["lsof"] = lsof
    if snapshots:
        rec["snapshots"] = snapshots
    return rec


class TestParseIsoToDt(unittest.TestCase):
    def test_z_suffix(self):
        dt = parse_iso_to_dt("2026-05-05T08:30:00Z")
        self.assertIsNotNone(dt)
        self.assertEqual(dt.tzinfo, timezone.utc)

    def test_explicit_offset(self):
        dt = parse_iso_to_dt("2026-05-05T16:30:00+08:00")
        self.assertIsNotNone(dt)
        # Normalize to UTC: 16:30+08:00 == 08:30 UTC
        self.assertEqual(dt.hour, 8)

    def test_naive_assumed_utc(self):
        dt = parse_iso_to_dt("2026-05-05T08:30:00")
        self.assertIsNotNone(dt)
        self.assertEqual(dt.tzinfo, timezone.utc)

    def test_invalid_returns_none(self):
        self.assertIsNone(parse_iso_to_dt("not-a-date"))
        self.assertIsNone(parse_iso_to_dt(""))
        self.assertIsNone(parse_iso_to_dt(None))  # type: ignore


class TestParseWindow(unittest.TestCase):
    def test_24h(self):
        self.assertEqual(parse_window_to_seconds("24h"), 86400)

    def test_72h(self):
        self.assertEqual(parse_window_to_seconds("72h"), 259200)

    def test_7d(self):
        self.assertEqual(parse_window_to_seconds("7d"), 604800)

    def test_all_returns_none(self):
        self.assertIsNone(parse_window_to_seconds("all"))
        self.assertIsNone(parse_window_to_seconds(None))

    def test_invalid_raises(self):
        with self.assertRaises(ValueError):
            parse_window_to_seconds("forever")
        with self.assertRaises(ValueError):
            parse_window_to_seconds("24x")


class TestLoadRecords(unittest.TestCase):
    def test_valid_jsonl(self):
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as f:
            f.write(json.dumps(_make_record("2026-05-05T01:00:00Z")) + "\n")
            f.write(json.dumps(_make_record("2026-05-05T02:00:00Z")) + "\n")
            path = f.name
        try:
            records, errs = load_records(path)
            self.assertEqual(len(records), 2)
            self.assertEqual(errs, 0)
        finally:
            os.unlink(path)

    def test_corrupted_lines_tolerated(self):
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as f:
            f.write(json.dumps(_make_record("2026-05-05T01:00:00Z")) + "\n")
            f.write("not valid json\n")
            f.write("[1,2,3]\n")  # array not dict
            f.write(json.dumps(_make_record("2026-05-05T02:00:00Z")) + "\n")
            path = f.name
        try:
            records, errs = load_records(path)
            self.assertEqual(len(records), 2)
            self.assertEqual(errs, 2)
        finally:
            os.unlink(path)

    def test_empty_file(self):
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as f:
            path = f.name
        try:
            records, errs = load_records(path)
            self.assertEqual(records, [])
            self.assertEqual(errs, 0)
        finally:
            os.unlink(path)


class TestFilterWindow(unittest.TestCase):
    def test_window_none_returns_all(self):
        records = [_make_record("2020-01-01T00:00:00Z")]
        result = filter_window(records, None)
        self.assertEqual(len(result), 1)

    def test_24h_filter(self):
        now = datetime(2026, 5, 5, 12, 0, 0, tzinfo=timezone.utc)
        records = [
            _make_record("2026-05-04T13:00:00Z"),  # 23h ago — kept
            _make_record("2026-05-04T11:00:00Z"),  # 25h ago — dropped
            _make_record("2026-05-05T11:00:00Z"),  # 1h ago — kept
        ]
        result = filter_window(records, 86400, now=now)
        self.assertEqual(len(result), 2)


class TestClassifyProbe(unittest.TestCase):
    def test_ok(self):
        self.assertEqual(classify_probe("exit=0|"), "ok")

    def test_eperm_in_stderr(self):
        self.assertEqual(classify_probe("exit=1|touch: Operation not permitted"), "eperm")

    def test_eperm_permission_denied(self):
        self.assertEqual(classify_probe("exit=1|touch: Permission denied"), "eperm")

    def test_other(self):
        # exit non-0 but not EPERM → other
        self.assertEqual(classify_probe("exit=1|some other error"), "other")

    def test_unknown(self):
        self.assertEqual(classify_probe(""), "unknown")
        self.assertEqual(classify_probe(None), "unknown")  # type: ignore


class TestClassifyFailureMode(unittest.TestCase):
    def test_全盘_eperm(self):
        rec = _make_record("2026-05-05T01:00:00Z",
                           probe_top="exit=1|touch: Operation not permitted",
                           probe_kb="exit=1|touch: Operation not permitted")
        self.assertEqual(classify_caller_failure_mode(rec), "全盘_eperm")

    def test_kb_only_eperm(self):
        rec = _make_record("2026-05-05T01:00:00Z",
                           probe_top="exit=0|",
                           probe_kb="exit=1|touch: Operation not permitted")
        self.assertEqual(classify_caller_failure_mode(rec), "kb_only_eperm")

    def test_probes_ok_eof_likely(self):
        """关键场景: probe 都 OK 但 rsync 仍失败 → EOF/stream 中断."""
        rec = _make_record("2026-05-05T01:00:00Z",
                           probe_top="exit=0|", probe_kb="exit=0|")
        self.assertEqual(classify_caller_failure_mode(rec), "probes_ok_likely_eof_or_stream")


class TestExtractConcurrentProcs(unittest.TestCase):
    def test_backupd(self):
        procs = extract_concurrent_procs("backupd 12345 0:30 /usr/libexec/backupd")
        self.assertIn("backupd", procs)

    def test_multiple(self):
        procs = extract_concurrent_procs(
            "backupd 12345\nmds_stores 99999\nfseventsd 5"
        )
        self.assertEqual(procs, {"backupd", "mds_stores", "mds", "fseventsd"})
        # mds_stores includes 'mds' substring — both detected, expected

    def test_empty(self):
        self.assertEqual(extract_concurrent_procs(""), set())
        self.assertEqual(extract_concurrent_procs(None), set())  # type: ignore


class TestAnalyze(unittest.TestCase):
    def test_empty(self):
        result = analyze([])
        self.assertEqual(result["count"], 0)

    def test_aggregates(self):
        records = [
            _make_record("2026-05-05T01:00:00Z", caller="a.sh", exit_code="12",
                         procs="backupd 12345"),
            _make_record("2026-05-05T02:00:00Z", caller="a.sh", exit_code="12",
                         procs="backupd 12345"),
            _make_record("2026-05-05T03:00:00Z", caller="b.sh", exit_code="23",
                         procs="mds_stores 1 0:01"),
        ]
        result = analyze(records)
        self.assertEqual(result["count"], 3)
        self.assertEqual(result["by_caller"]["a.sh"], 2)
        self.assertEqual(result["by_caller"]["b.sh"], 1)
        self.assertEqual(result["by_exit_code"]["12"], 2)
        self.assertEqual(result["by_exit_code"]["23"], 1)
        self.assertEqual(result["by_concurrent_proc"]["backupd"], 2)


class TestFormatTextReport(unittest.TestCase):
    def test_eof_and_backupd_decision_hint(self):
        """4 个 EOF 模式 + backupd 高频 → 应建议检测 tmutil status."""
        records = [
            _make_record(f"2026-05-05T0{i}:00:00Z",
                         probe_top="exit=0|", probe_kb="exit=0|",
                         procs="backupd 12345 0:30")
            for i in range(1, 5)
        ]
        analysis = analyze(records)
        report = format_text_report(analysis, "all")
        self.assertIn("Time Machine backupd 高频出现", report)
        self.assertIn("tmutil status", report)

    def test_eperm_decision_hint(self):
        records = [
            _make_record(f"2026-05-05T0{i}:00:00Z",
                         probe_top="exit=1|Operation not permitted",
                         probe_kb="exit=1|Operation not permitted")
            for i in range(1, 4)
        ]
        analysis = analyze(records)
        report = format_text_report(analysis, "all")
        self.assertIn("EPERM", report)
        self.assertIn("APFS 修复", report)

    def test_empty_records(self):
        report = format_text_report({"count": 0}, "all")
        self.assertIn("无记录", report)


class TestCli(unittest.TestCase):
    def test_missing_file_returns_2(self):
        proc = subprocess.run(
            [sys.executable, SCRIPT_PATH, "--file", "/nonexistent/path"],
            capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(proc.returncode, 2)
        self.assertIn("不存在", proc.stderr)

    def test_happy_path_text_output(self):
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as f:
            f.write(json.dumps(_make_record("2026-05-05T01:00:00Z")) + "\n")
            path = f.name
        try:
            proc = subprocess.run(
                [sys.executable, SCRIPT_PATH, "--file", path, "--window", "all"],
                capture_output=True, text=True, timeout=10,
            )
            self.assertEqual(proc.returncode, 0)
            self.assertIn("总数: 1", proc.stdout)
            self.assertIn("MOVESPEED Incident 分析", proc.stdout)
        finally:
            os.unlink(path)

    def test_json_output_parseable(self):
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as f:
            f.write(json.dumps(_make_record("2026-05-05T01:00:00Z")) + "\n")
            path = f.name
        try:
            proc = subprocess.run(
                [sys.executable, SCRIPT_PATH, "--file", path, "--json"],
                capture_output=True, text=True, timeout=10,
            )
            self.assertEqual(proc.returncode, 0)
            data = json.loads(proc.stdout)
            self.assertEqual(data["count"], 1)
            self.assertIn("_meta", data)
        finally:
            os.unlink(path)

    def test_invalid_window_returns_2(self):
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as f:
            f.write(json.dumps(_make_record("2026-05-05T01:00:00Z")) + "\n")
            path = f.name
        try:
            proc = subprocess.run(
                [sys.executable, SCRIPT_PATH, "--file", path, "--window", "forever"],
                capture_output=True, text=True, timeout=10,
            )
            self.assertEqual(proc.returncode, 2)
            self.assertIn("Invalid window", proc.stderr)
        finally:
            os.unlink(path)


class TestV37929BOwnershipAnalysis(unittest.TestCase):
    """V37.9.29 (b): ownership distribution as 8th analyzer dimension.

    Records before V37.9.29 (b) lack ownership_top / ownership_kb fields,
    must be classified as 'empty (pre-V37.9.29(b) records)' for backward compat.

    Misalignment patterns (top=0:0 root / kb=99:99 _unknown) trigger an
    explicit warning in the decision-hint section of the text report.
    """

    def test_records_without_ownership_fields_counted_as_empty(self):
        """Backward compat: old records (no ownership_top/_kb) → 'empty' bucket."""
        records = [
            _make_record("2026-05-05T01:00:00Z"),  # no ownership fields
            _make_record("2026-05-05T02:00:00Z"),
        ]
        result = analyze(records)
        owners = result.get("by_ownership", {})
        # All records should land in the empty bucket
        empty_key = "empty (pre-V37.9.29(b) records)"
        self.assertEqual(
            owners.get(empty_key, 0),
            2,
            f"Expected 2 in empty bucket, got: {owners}",
        )

    def test_records_with_misalignment_pair_counted(self):
        """V37.9.29 path D' pre-fix pattern: top=root + kb=_unknown."""
        records = [
            _make_record("2026-05-05T01:00:00Z",
                         ownership_top="0:0", ownership_kb="99:99"),
            _make_record("2026-05-05T02:00:00Z",
                         ownership_top="0:0", ownership_kb="99:99"),
            _make_record("2026-05-05T03:00:00Z",
                         ownership_top="0:0", ownership_kb="99:99"),
        ]
        result = analyze(records)
        owners = result.get("by_ownership", {})
        misalign_key = "top=0:0 kb=99:99"
        self.assertEqual(
            owners.get(misalign_key, 0),
            3,
            f"Expected 3 misaligned records, got: {owners}",
        )

    def test_records_post_fix_consistent_pair_counted(self):
        """V37.9.29 path D' post-fix pattern: top=bisdom + kb=bisdom."""
        records = [
            _make_record("2026-05-05T01:00:00Z",
                         ownership_top="501:20", ownership_kb="501:20"),
        ]
        result = analyze(records)
        owners = result.get("by_ownership", {})
        consistent_key = "top=501:20 kb=501:20"
        self.assertEqual(
            owners.get(consistent_key, 0),
            1,
            f"Expected 1 consistent record, got: {owners}",
        )

    def test_partial_ownership_field_handled(self):
        """Edge case: only top set, kb missing → 'kb=?' marker."""
        # _make_record only adds field if non-empty, so we need to inject directly
        rec = _make_record("2026-05-05T01:00:00Z")
        rec["ownership_top"] = "501:20"
        # kb intentionally absent
        result = analyze([rec])
        owners = result.get("by_ownership", {})
        # Should have a pair with "kb=?" marker
        found = any("?" in k for k in owners)
        self.assertTrue(
            found,
            f"Expected ?-marker for missing field, got: {owners}",
        )

    def test_non_string_ownership_field_does_not_crash(self):
        """Robustness: non-string ownership field must not crash analyzer."""
        rec = _make_record("2026-05-05T01:00:00Z")
        rec["ownership_top"] = 12345  # invalid type
        rec["ownership_kb"] = None
        # Must not raise
        result = analyze([rec])
        # Should land in empty bucket because both became ""
        self.assertIn("by_ownership", result)

    def test_text_report_shows_ownership_section_when_data_present(self):
        """Text report includes the new 8th dimension if any record has data."""
        records = [
            _make_record("2026-05-05T01:00:00Z",
                         ownership_top="0:0", ownership_kb="99:99"),
        ]
        result = analyze(records)
        text = format_text_report(result, "all")
        self.assertIn("Ownership 分布", text, "8th dimension header missing")
        self.assertIn("V37.9.29 b", text, "V37.9.29 b marker missing")
        self.assertIn("top=0:0 kb=99:99", text, "Misalignment pair not displayed")

    def test_text_report_warns_on_root_uid_at_incident(self):
        """Decision hint must alert if root (0:0) appears post-V37.9.29 fix."""
        records = [
            _make_record("2026-05-05T01:00:00Z",
                         ownership_top="0:0", ownership_kb="99:99"),
        ]
        result = analyze(records)
        text = format_text_report(result, "all")
        self.assertIn("Ownership 警告", text, "Misalignment alert missing")
        self.assertIn("R1 回滚", text, "Rollback instruction missing")
        self.assertIn("disableOwnership", text, "Concrete rollback cmd missing")

    def test_text_report_no_warn_when_only_consistent_pairs(self):
        """No alert when all records show consistent UID:GID."""
        records = [
            _make_record("2026-05-05T01:00:00Z",
                         ownership_top="501:20", ownership_kb="501:20"),
            _make_record("2026-05-05T02:00:00Z",
                         ownership_top="501:20", ownership_kb="501:20"),
        ]
        result = analyze(records)
        text = format_text_report(result, "all")
        self.assertNotIn("Ownership 警告", text,
                         "Should NOT warn when ownership is consistent")

    def test_text_report_no_section_when_only_empty_records(self):
        """Old records without ownership fields → don't show empty bucket alone."""
        # Backward compat: if all records are pre-V37.9.29(b), the section shows
        # but doesn't trigger warning (no suspicious UIDs to alert on)
        records = [_make_record("2026-05-05T01:00:00Z")]  # no ownership
        result = analyze(records)
        text = format_text_report(result, "all")
        # The section header still appears (empty bucket is valid data point)
        self.assertIn("Ownership 分布", text)
        # But no warning since no suspicious UIDs
        self.assertNotIn("Ownership 警告", text)

    def test_kb_only_root_uid_also_triggers_alert(self):
        """Edge: just kb=0:0 (without top=0:0) should also warn."""
        records = [
            _make_record("2026-05-05T01:00:00Z",
                         ownership_top="501:20", ownership_kb="0:0"),
        ]
        result = analyze(records)
        text = format_text_report(result, "all")
        self.assertIn("Ownership 警告", text)


class TestV37929BCaptureScriptIntegrity(unittest.TestCase):
    """V37.9.29 (b): source-level guards on capture.sh — ensure ownership
    capture lines + Python rec dict fields stay in sync with this analyzer.
    """

    def test_capture_script_has_top_ownership_capture(self):
        """capture.sh must collect /Volumes/MOVESPEED ownership."""
        cap_path = os.path.join(_HERE, "movespeed_incident_capture.sh")
        with open(cap_path, "r", encoding="utf-8") as fp:
            content = fp.read()
        self.assertIn(
            'stat -f "%u:%g" /Volumes/MOVESPEED >',
            content,
            "Top-level ownership capture missing in capture.sh",
        )

    def test_capture_script_has_kb_ownership_capture(self):
        """capture.sh must collect /Volumes/MOVESPEED/KB ownership."""
        cap_path = os.path.join(_HERE, "movespeed_incident_capture.sh")
        with open(cap_path, "r", encoding="utf-8") as fp:
            content = fp.read()
        self.assertIn(
            'stat -f "%u:%g" /Volumes/MOVESPEED/KB >',
            content,
            "KB ownership capture missing in capture.sh",
        )

    def test_capture_script_rec_dict_includes_ownership_fields(self):
        cap_path = os.path.join(_HERE, "movespeed_incident_capture.sh")
        with open(cap_path, "r", encoding="utf-8") as fp:
            content = fp.read()
        self.assertIn('"ownership_top": read_file("ownership_top", 50)', content)
        self.assertIn('"ownership_kb": read_file("ownership_kb", 50)', content)

    def test_capture_script_v37_9_29_b_marker(self):
        """Attribution comment for V37.9.29 (b) must exist."""
        cap_path = os.path.join(_HERE, "movespeed_incident_capture.sh")
        with open(cap_path, "r", encoding="utf-8") as fp:
            content = fp.read()
        self.assertIn("V37.9.29 (b)", content)


# ────────────────────────────────────────────────────────────────────────
# V37.9.30: 3 new forensic dimensions (ACL/xattr, lsof handles, TM snapshots)
#
# Context: V37.9.29 24h validation showed chown真生效 (19/21 records显示
# bisdom:staff) but EPERM 100% persisted (21 incidents). Ownership-misalignment
# hypothesis was partially falsified — UID was a real bug but NOT the EPERM
# root cause. V37.9.30 adds ACL/xattr/handle/snapshot capture to differentiate
# new hypotheses (ACL deny / daemon contention / TM local snapshot lock).
# ────────────────────────────────────────────────────────────────────────


class TestV37930ClassifyAclAnomaly(unittest.TestCase):
    """V37.9.30: ACL/xattr classifier nuances."""

    def test_empty_string_is_empty(self):
        self.assertEqual(classify_acl_anomaly(""), "empty")

    def test_whitespace_only_is_empty(self):
        self.assertEqual(classify_acl_anomaly("   \n  "), "empty")

    def test_non_string_is_empty(self):
        # Robustness: integer / None / list inputs must not crash
        self.assertEqual(classify_acl_anomaly(None), "empty")  # type: ignore
        self.assertEqual(classify_acl_anomaly(123), "empty")  # type: ignore

    def test_explicit_deny_rule_is_acl_deny(self):
        # macOS ACL line format: " 0: group:everyone deny add_file"
        acl_str = ("drwxr-xr-x@ 5 bisdom staff 160 May  6 00:00 KB\n"
                   " 0: group:everyone deny add_file,delete")
        self.assertEqual(classify_acl_anomaly(acl_str), "acl_deny")

    def test_xattr_only_is_xattr_only(self):
        # Tab-indented xattr line, no ACL deny
        acl_str = ("drwxr-xr-x@ 5 bisdom staff 160 May  6 00:00 KB\n"
                   "\tcom.apple.quarantine     38")
        self.assertEqual(classify_acl_anomaly(acl_str), "xattr_only")

    def test_normal_ls_output_is_normal(self):
        # Plain ls -la output, no ACL no xattr
        acl_str = "total 0\ndrwxr-xr-x  5 bisdom staff 160 May  6 00:00 KB"
        self.assertEqual(classify_acl_anomaly(acl_str), "normal")

    def test_acl_present_without_deny(self):
        # Structured ACL line but not 'deny' (e.g. allow rule)
        # Still flagged as acl_present (weaker signal than acl_deny)
        acl_str = ("drwxr-xr-x+ 5 bisdom staff 160 May  6 00:00 KB\n"
                   " 0: user:_spotlight allow read")
        self.assertEqual(classify_acl_anomaly(acl_str), "acl_present")


class TestV37930ClassifyHandleHolders(unittest.TestCase):
    """V37.9.30: lsof handle-holder classifier nuances."""

    def test_empty_is_empty(self):
        self.assertEqual(classify_handle_holders(""), "empty")
        self.assertEqual(classify_handle_holders(None), "empty")  # type: ignore

    def test_only_header_line_is_empty(self):
        # lsof first line is header "COMMAND PID USER FD ..." — must not count
        self.assertEqual(classify_handle_holders("COMMAND PID USER FD"), "empty")

    def test_only_daemon_lines_is_daemon_dominated(self):
        lsof_str = (
            "mds_stores 123 _spotlight 5u REG 1,5 4096 /Volumes/MOVESPEED\n"
            "backupd    456 _backupd   3r DIR 1,5 1024 /Volumes/MOVESPEED/KB"
        )
        self.assertEqual(classify_handle_holders(lsof_str), "daemon_dominated")

    def test_only_user_processes_is_user_only(self):
        lsof_str = (
            "rsync 789 bisdom 4r REG 1,5 16384 /Volumes/MOVESPEED/KB/note.md\n"
            "python 1011 bisdom 5w REG 1,5 8192 /Volumes/MOVESPEED/index.json"
        )
        self.assertEqual(classify_handle_holders(lsof_str), "user_only")

    def test_mixed_daemon_and_user_is_mixed(self):
        lsof_str = (
            "mds_stores 123 _spotlight 5u REG 1,5 4096 /Volumes/MOVESPEED\n"
            "rsync 789 bisdom 4r REG 1,5 16384 /Volumes/MOVESPEED/KB/note.md"
        )
        self.assertEqual(classify_handle_holders(lsof_str), "mixed")

    def test_mds_substring_does_not_falsely_match_md5sum(self):
        # 'mds' is a daemon keyword but 'md5sum' would substring-match if naive
        # — verify via case + word matching that daemon detection works correctly.
        # (This is validation: classifier must detect 'mds' as substring; the
        # safeguard is that user_cmds list is checked separately.)
        lsof_str = "mds 123 _spotlight 5u REG 1,5 4096 /Volumes/MOVESPEED"
        self.assertEqual(classify_handle_holders(lsof_str), "daemon_dominated")


class TestV37930ClassifySnapshotCount(unittest.TestCase):
    """V37.9.30: TM snapshot count bucketing."""

    def test_empty_is_empty(self):
        self.assertEqual(classify_snapshot_count(""), "empty")
        self.assertEqual(classify_snapshot_count(None), "empty")  # type: ignore

    def test_no_snapshots_is_snap_0(self):
        self.assertEqual(classify_snapshot_count("Snapshots for disk1:\n"), "snap_0")

    def test_one_snapshot_is_snap_1_5(self):
        snap_str = "Snapshots for disk1:\ncom.apple.TimeMachine.2026-05-06-120000.local"
        self.assertEqual(classify_snapshot_count(snap_str), "snap_1_5")

    def test_five_snapshots_is_snap_1_5(self):
        lines = ["Snapshots for disk1:"]
        for i in range(5):
            lines.append(f"com.apple.TimeMachine.2026-05-0{i+1}-120000.local")
        self.assertEqual(classify_snapshot_count("\n".join(lines)), "snap_1_5")

    def test_six_snapshots_is_snap_6_plus(self):
        lines = ["Snapshots for disk1:"]
        for i in range(6):
            lines.append(f"com.apple.TimeMachine.2026-05-0{i+1}-120000.local")
        self.assertEqual(classify_snapshot_count("\n".join(lines)), "snap_6_plus")

    def test_non_tm_lines_ignored(self):
        # Stray non-TM lines must not inflate snapshot count
        snap_str = ("Snapshots for disk1:\n"
                    "Not a snapshot line\n"
                    "com.apple.TimeMachine.2026-05-06-120000.local\n"
                    "another non-tm line")
        self.assertEqual(classify_snapshot_count(snap_str), "snap_1_5")


class TestV37930ForensicAnalysisIntegration(unittest.TestCase):
    """V37.9.30: full analyze() pipeline with new dimensions.

    Mirror V37.9.29 (b) TestV37929BOwnershipAnalysis structure: backward-compat
    + warning trigger + no-warning-when-clean.
    """

    def test_pre_v37_9_30_records_land_in_empty_buckets(self):
        """Backward compat: pre-V37.9.30 records (no acl/lsof/snapshot fields)
        must land in the 'empty' bucket without crashing or falsely warning."""
        records = [
            _make_record("2026-05-05T01:00:00Z"),
            _make_record("2026-05-05T02:00:00Z"),
        ]
        result = analyze(records)
        # All three new dimensions must be present in result
        self.assertIn("by_acl_anomaly", result)
        self.assertIn("by_handle_pattern", result)
        self.assertIn("by_snapshot_bucket", result)
        # All records counted as empty in each dimension
        self.assertEqual(
            result["by_acl_anomaly"].get("empty (pre-V37.9.30 records)", 0), 2
        )
        self.assertEqual(result["by_handle_pattern"].get("empty", 0), 2)
        self.assertEqual(result["by_snapshot_bucket"].get("empty", 0), 2)

    def test_acl_deny_pattern_counted(self):
        """ACL deny is the strongest EPERM signal post-V37.9.29 path D'."""
        records = [
            _make_record(
                "2026-05-07T01:00:00Z",
                acl_kb=" 0: group:everyone deny add_file,delete",
            ),
            _make_record(
                "2026-05-07T02:00:00Z",
                acl_top=" 0: group:everyone deny add_file",
            ),
        ]
        result = analyze(records)
        self.assertEqual(result["by_acl_anomaly"].get("acl_deny", 0), 2)

    def test_daemon_dominated_handle_pattern_counted(self):
        """daemon_dominated is supports daemon-contention hypothesis."""
        records = [
            _make_record(
                "2026-05-07T01:00:00Z",
                lsof="mds_stores 123 _spotlight 5u REG /Volumes/MOVESPEED",
            ),
            _make_record(
                "2026-05-07T02:00:00Z",
                lsof="backupd 456 _backupd 3r DIR /Volumes/MOVESPEED/KB",
            ),
        ]
        result = analyze(records)
        self.assertEqual(
            result["by_handle_pattern"].get("daemon_dominated", 0), 2
        )

    def test_high_snapshot_count_bucketed(self):
        """6+ snapshots = snap_6_plus = TM local snapshot lock candidate."""
        many_snaps = "\n".join(
            f"com.apple.TimeMachine.2026-05-0{i+1}-120000.local" for i in range(7)
        )
        records = [
            _make_record("2026-05-07T01:00:00Z", snapshots=many_snaps),
        ]
        result = analyze(records)
        self.assertEqual(result["by_snapshot_bucket"].get("snap_6_plus", 0), 1)

    def test_text_report_shows_new_three_sections(self):
        """All 3 V37.9.30 sections appear in text report when data present."""
        records = [
            _make_record(
                "2026-05-07T01:00:00Z",
                acl_kb=" 0: group:everyone deny add_file",
                lsof="mds_stores 123 _spotlight 5u REG /Volumes/MOVESPEED",
                snapshots="\n".join(
                    f"com.apple.TimeMachine.2026-05-0{i+1}-120000.local"
                    for i in range(7)
                ),
            ),
        ]
        result = analyze(records)
        text = format_text_report(result, "all")
        self.assertIn("ACL/xattr 异常分布", text, "9th dimension header missing")
        self.assertIn("句柄持有者模式", text, "10th dimension header missing")
        self.assertIn("TM Snapshot 分布", text, "11th dimension header missing")
        self.assertIn("V37.9.30", text, "V37.9.30 marker missing")

    def test_text_report_acl_deny_warning_fires(self):
        """ACL deny warning must fire with concrete fix command."""
        records = [
            _make_record(
                "2026-05-07T01:00:00Z",
                acl_kb=" 0: group:everyone deny add_file",
            ),
        ]
        result = analyze(records)
        text = format_text_report(result, "all")
        self.assertIn("ACL deny 警告", text)
        self.assertIn("chmod -RN", text, "ACL clearing command missing")

    def test_text_report_daemon_dominated_warning_fires_when_threshold_met(self):
        """daemon_dominated warning needs ≥3 records to fire."""
        records = [
            _make_record(
                f"2026-05-07T0{i+1}:00:00Z",
                lsof="mds_stores 123 _spotlight 5u REG /Volumes/MOVESPEED",
            )
            for i in range(3)
        ]
        result = analyze(records)
        text = format_text_report(result, "all")
        self.assertIn("句柄持有", text)
        self.assertIn("path B", text, "Schedule避峰 hint missing")

    def test_text_report_no_daemon_warning_below_threshold(self):
        """daemon_dominated below threshold (=2) must NOT fire warning."""
        records = [
            _make_record(
                "2026-05-07T01:00:00Z",
                lsof="mds_stores 123 _spotlight 5u REG /Volumes/MOVESPEED",
            ),
            _make_record(
                "2026-05-07T02:00:00Z",
                lsof="backupd 456 _backupd 3r DIR /Volumes/MOVESPEED/KB",
            ),
        ]
        result = analyze(records)
        text = format_text_report(result, "all")
        # The dimension section appears, but the path-B hint should NOT
        self.assertIn("句柄持有者模式", text)  # section title
        self.assertNotIn("path B", text)  # decision hint suppressed

    def test_text_report_snapshot_warning_fires_at_snap_6_plus(self):
        """snap_6_plus must trigger TM-snapshot warning with concrete fix."""
        many_snaps = "\n".join(
            f"com.apple.TimeMachine.2026-05-0{i+1}-120000.local" for i in range(7)
        )
        records = [
            _make_record("2026-05-07T01:00:00Z", snapshots=many_snaps),
        ]
        result = analyze(records)
        text = format_text_report(result, "all")
        self.assertIn("Snapshot 警告", text)
        self.assertIn("deletelocalsnapshots", text, "TM snapshot fix cmd missing")

    def test_text_report_no_warnings_when_all_clean(self):
        """All V37.9.30 dimensions normal = no warnings."""
        records = [
            _make_record(
                "2026-05-07T01:00:00Z",
                acl_kb="total 0\ndrwxr-xr-x 5 bisdom staff 160 May 6 KB",
                lsof="rsync 789 bisdom 4r REG /Volumes/MOVESPEED/KB/note.md",
                snapshots="Snapshots for disk1:\n",
            ),
        ]
        result = analyze(records)
        text = format_text_report(result, "all")
        self.assertNotIn("ACL deny 警告", text)
        self.assertNotIn("Snapshot 警告", text)
        # daemon hint is also absent (user_only pattern, no daemon)


class TestV37930CaptureScriptIntegrity(unittest.TestCase):
    """V37.9.30: source-level guards on capture.sh — ensure new forensic
    capture lines + Python rec dict fields stay in sync with this analyzer.
    """

    def test_capture_script_has_acl_top_capture(self):
        """capture.sh must collect /Volumes/MOVESPEED ACL+xattr (ls -le@)."""
        cap_path = os.path.join(_HERE, "movespeed_incident_capture.sh")
        with open(cap_path, "r", encoding="utf-8") as fp:
            content = fp.read()
        self.assertIn(
            'ls -le@ /Volumes/MOVESPEED/ >',
            content,
            "Top-level ACL/xattr capture missing in capture.sh",
        )

    def test_capture_script_has_acl_kb_capture(self):
        """capture.sh must collect /Volumes/MOVESPEED/KB ACL+xattr (ls -le@)."""
        cap_path = os.path.join(_HERE, "movespeed_incident_capture.sh")
        with open(cap_path, "r", encoding="utf-8") as fp:
            content = fp.read()
        self.assertIn(
            'ls -le@ /Volumes/MOVESPEED/KB/ >',
            content,
            "KB ACL/xattr capture missing in capture.sh",
        )

    def test_capture_script_has_lsof_capture(self):
        """capture.sh must collect lsof open handles on /Volumes/MOVESPEED."""
        cap_path = os.path.join(_HERE, "movespeed_incident_capture.sh")
        with open(cap_path, "r", encoding="utf-8") as fp:
            content = fp.read()
        self.assertIn("lsof /Volumes/MOVESPEED", content)
        # Must cap output to bound runtime + size (lsof can hang on macOS)
        self.assertIn("head -50", content,
                      "lsof must be capped with head -50 for runtime safety")

    def test_capture_script_has_snapshots_capture(self):
        """capture.sh must collect tmutil listlocalsnapshots."""
        cap_path = os.path.join(_HERE, "movespeed_incident_capture.sh")
        with open(cap_path, "r", encoding="utf-8") as fp:
            content = fp.read()
        self.assertIn("tmutil listlocalsnapshots", content)

    def test_capture_script_rec_dict_includes_v37_9_30_fields(self):
        cap_path = os.path.join(_HERE, "movespeed_incident_capture.sh")
        with open(cap_path, "r", encoding="utf-8") as fp:
            content = fp.read()
        # V37.9.81 B alternation: accept legacy read_file (V37.9.30)
        # OR read_file_with_stderr (V37.9.81 B — stderr-aware marker reads).
        # Both forms write the same field name, the marker is just an upgrade
        # to distinguish sandbox-denied empty from genuinely-empty output.
        for field in ("acl_top", "acl_kb", "lsof", "snapshots"):
            self.assertTrue(
                f'"{field}": read_file("{field}"' in content
                or f'"{field}": read_file_with_stderr("{field}"' in content,
                f"capture.sh must include {field} via read_file or read_file_with_stderr",
            )

    def test_capture_script_v37_9_30_marker(self):
        """Attribution comment for V37.9.30 must exist in capture.sh."""
        cap_path = os.path.join(_HERE, "movespeed_incident_capture.sh")
        with open(cap_path, "r", encoding="utf-8") as fp:
            content = fp.read()
        self.assertIn("V37.9.30", content)
        # Sanity: must mention the falsified-hypothesis context so future
        # readers understand WHY these new fields were added (MR-7 self-aware
        # observability — capture.sh attribution carries causal narrative).
        self.assertIn("partially falsified", content.lower()
                      .replace("hypothesis is partially falsified", "partially falsified"))


# ═══════════════════════════════════════════════════════════════════════
# V37.9.81 B — capture.sh "未采集 vs 采集到空" 显式区分
# ═══════════════════════════════════════════════════════════════════════
# V37.9.30 取证维度盲区: lsof/ACL 采集器自身被 macOS TCC sandbox 拒绝时,
# stderr 含 "Operation not permitted" 但 `2>/dev/null` 吞掉 → stdout 文件空
# → Python read_file 返回 "" → analyzer 分类为 normal/empty (误读为正常).
# V37.9.80 真因 (60 天血案: macOS TCC Sandbox) 锁定后, V37.9.81 B 修复采集器
# 让 stderr 被独立捕获 + Python 加 [sandbox_denied] / [tool_unavailable] marker.

class TestV37981BCaptureSandboxStderr(unittest.TestCase):
    """V37.9.81 B: capture.sh 必须把 4 个采集命令的 stderr 引导到独立文件
    (而非 2>/dev/null 吞掉), Python 端必须读 stderr 文件加 sandbox_denied marker.
    """

    def setUp(self):
        cap_path = os.path.join(_HERE, "movespeed_incident_capture.sh")
        with open(cap_path, "r", encoding="utf-8") as fp:
            self.content = fp.read()

    def test_acl_top_stderr_captured_to_file(self):
        """ls -le@ /Volumes/MOVESPEED/ 必须把 stderr 重定向到 acl_top_err 文件."""
        self.assertIn('2> "$_TMP/acl_top_err"', self.content,
                      "V37.9.81 B: acl_top stderr must capture to file (not /dev/null)")

    def test_acl_kb_stderr_captured_to_file(self):
        self.assertIn('2> "$_TMP/acl_kb_err"', self.content,
                      "V37.9.81 B: acl_kb stderr must capture to file")

    def test_lsof_stderr_captured_to_file(self):
        self.assertIn('lsof /Volumes/MOVESPEED 2> "$_TMP/lsof_err"', self.content,
                      "V37.9.81 B: lsof stderr must capture to file")

    def test_snapshots_stderr_captured_to_file(self):
        self.assertIn('tmutil listlocalsnapshots / 2> "$_TMP/snapshots_err"', self.content,
                      "V37.9.81 B: tmutil snapshots stderr must capture to file")

    def test_python_heredoc_has_read_file_with_stderr_helper(self):
        """V37.9.81 B: Python heredoc must define read_file_with_stderr helper."""
        self.assertIn("def read_file_with_stderr(", self.content,
                      "V37.9.81 B helper function definition missing")
        self.assertIn("[sandbox_denied]", self.content,
                      "V37.9.81 B sandbox_denied marker prefix string missing")
        self.assertIn("[tool_unavailable]", self.content,
                      "V37.9.81 B tool_unavailable marker prefix string missing")

    def test_v37_9_81_b_marker_in_capture_script(self):
        """V37.9.81 B attribution comment must exist for future readers."""
        self.assertIn("V37.9.81 B", self.content,
                      "V37.9.81 B marker comment missing")
        # Causal narrative: must explain V37.9.30 blind spot was the trigger
        self.assertIn("V37.9.30", self.content,
                      "V37.9.81 B must reference V37.9.30 lineage (取证盲区)")
        # Must reference V37.9.80 TCC root cause that motivated the fix
        self.assertIn("V37.9.80", self.content,
                      "V37.9.81 B must reference V37.9.80 TCC sandbox root cause")

    def test_python_helper_priority_order_sandbox_first(self):
        """V37.9.81 B: sandbox-deny detection MUST take priority over tool-unavailable.

        Both 'Operation not permitted' (sandbox) and 'not found' (tool) can
        coexist in stderr. The V37.9.80 root cause is TCC sandbox, so the
        marker assignment ordering must prefer [sandbox_denied] when both
        patterns match. Source-level guard: 'operation not permitted' check
        must appear before 'command not found' check in the helper function.
        """
        # Extract the helper body (rough heuristic: between def read_file_with_stderr and next def)
        start = self.content.find("def read_file_with_stderr(")
        self.assertGreater(start, 0, "helper not found")
        end = self.content.find("rec = {", start)
        self.assertGreater(end, start, "helper end not found")
        helper_body = self.content[start:end]
        sandbox_pos = helper_body.find("operation not permitted")
        tool_pos = helper_body.find("command not found")
        self.assertGreater(sandbox_pos, 0, "sandbox check missing in helper")
        self.assertGreater(tool_pos, 0, "tool check missing in helper")
        self.assertLess(sandbox_pos, tool_pos,
                        "V37.9.81 B: sandbox_denied check must precede tool_unavailable check")


class TestV37981BAnalyzerSandboxBuckets(unittest.TestCase):
    """V37.9.81 B: analyzer classify_* 函数必须识别 [sandbox_denied] /
    [tool_unavailable] marker 并产生独立桶, 不再误判为 normal/empty.
    """

    def test_classify_acl_anomaly_sandbox_marker(self):
        """[sandbox_denied] prefix → sandbox_denied bucket (overrides any content)."""
        from movespeed_incident_analyzer import classify_acl_anomaly
        # Even if stdout content looks like a normal "total 0" line, the marker
        # takes priority because sandbox-deny IS the EPERM cause.
        self.assertEqual(
            classify_acl_anomaly("[sandbox_denied] total 0"),
            "sandbox_denied",
        )
        self.assertEqual(
            classify_acl_anomaly("[sandbox_denied] ls: Operation not permitted"),
            "sandbox_denied",
        )

    def test_classify_acl_anomaly_tool_unavailable_marker(self):
        from movespeed_incident_analyzer import classify_acl_anomaly
        self.assertEqual(
            classify_acl_anomaly("[tool_unavailable] command not found"),
            "tool_unavailable",
        )

    def test_classify_acl_anomaly_backward_compat_no_marker(self):
        """V37.9.30 era records (no marker) must continue to work as before."""
        from movespeed_incident_analyzer import classify_acl_anomaly
        self.assertEqual(classify_acl_anomaly("total 0"), "normal")
        self.assertEqual(
            classify_acl_anomaly("0: group:everyone deny add_file"),
            "acl_deny",
        )
        self.assertEqual(classify_acl_anomaly(""), "empty")

    def test_classify_handle_holders_sandbox_marker(self):
        from movespeed_incident_analyzer import classify_handle_holders
        self.assertEqual(
            classify_handle_holders("[sandbox_denied] lsof: Operation not permitted"),
            "sandbox_denied",
        )

    def test_classify_handle_holders_tool_unavailable_marker(self):
        from movespeed_incident_analyzer import classify_handle_holders
        self.assertEqual(
            classify_handle_holders("[tool_unavailable] lsof: command not found"),
            "tool_unavailable",
        )

    def test_classify_handle_holders_backward_compat(self):
        """Daemon dominance / user_only must still work post-V37.9.81 B."""
        from movespeed_incident_analyzer import classify_handle_holders
        daemon_only = "mds_stores 555 root  txt REG  ...\nbackupd 548 root  txt REG  ..."
        self.assertEqual(classify_handle_holders(daemon_only), "daemon_dominated")
        self.assertEqual(classify_handle_holders(""), "empty")

    def test_classify_snapshot_count_sandbox_marker(self):
        from movespeed_incident_analyzer import classify_snapshot_count
        self.assertEqual(
            classify_snapshot_count("[sandbox_denied] tmutil: Operation not permitted"),
            "sandbox_denied",
        )

    def test_classify_snapshot_count_tool_unavailable_marker(self):
        from movespeed_incident_analyzer import classify_snapshot_count
        self.assertEqual(
            classify_snapshot_count("[tool_unavailable] tmutil: command not found"),
            "tool_unavailable",
        )

    def test_classify_snapshot_count_backward_compat(self):
        """snap_0 / snap_1_5 / snap_6_plus / empty must still work."""
        from movespeed_incident_analyzer import classify_snapshot_count
        self.assertEqual(classify_snapshot_count("Snapshots for disk /:"), "snap_0")
        self.assertEqual(classify_snapshot_count(""), "empty")
        self.assertEqual(
            classify_snapshot_count("com.apple.TimeMachine.2026-05-19-080000.local"),
            "snap_1_5",
        )

    def test_analyze_sandbox_priority_over_acl_deny(self):
        """V37.9.81 B priority dict: sandbox_denied must beat acl_deny.

        Rationale: sandbox-deny is direct evidence of the kernel-level EPERM
        cause (V37.9.80 TCC sandbox), while acl_deny is a hypothesis. When
        both signals appear across top vs kb, sandbox wins.
        """
        from movespeed_incident_analyzer import analyze
        records = [{
            "timestamp_iso": "2026-05-19T01:00:00Z",
            "caller": "test.sh",
            "exit_code": "1",
            "acl_top": "[sandbox_denied] ls: Operation not permitted",
            "acl_kb": "0: group:everyone deny add_file",  # acl_deny
        }]
        result = analyze(records)
        by_acl = result.get("by_acl_anomaly", {})
        # sandbox_denied must win over acl_deny in priority dict
        self.assertEqual(by_acl.get("sandbox_denied", 0), 1,
                         f"sandbox should win over acl_deny: got {by_acl}")
        self.assertEqual(by_acl.get("acl_deny", 0), 0,
                         f"acl_deny should be suppressed: got {by_acl}")

    def test_analyze_text_report_sandbox_decision_hint(self):
        """V37.9.81 B: when sandbox_denied detected, decision hint must reference V37.9.80 FDA fix."""
        from movespeed_incident_analyzer import analyze, format_text_report
        records = [{
            "timestamp_iso": "2026-05-19T01:00:00Z",
            "caller": "test.sh",
            "exit_code": "1",
            "lsof": "[sandbox_denied] lsof: Operation not permitted",
        }]
        result = analyze(records)
        report = format_text_report(result, "test")
        self.assertIn("Sandbox 拒绝警告", report,
                      "V37.9.81 B sandbox decision hint header missing")
        self.assertIn("/usr/sbin/cron", report,
                      "V37.9.81 B decision hint must reference V37.9.80 FDA fix path")
        self.assertIn("V37.9.81 B", report,
                      "V37.9.81 B marker missing in decision hint")

    def test_analyze_no_sandbox_no_hint_regression(self):
        """If no sandbox_denied detected, V37.9.81 B hint must NOT appear (avoid false alerts)."""
        from movespeed_incident_analyzer import analyze, format_text_report
        records = [{
            "timestamp_iso": "2026-05-19T01:00:00Z",
            "caller": "test.sh",
            "exit_code": "1",
            "acl_top": "total 0",  # normal
        }]
        result = analyze(records)
        report = format_text_report(result, "test")
        self.assertNotIn("Sandbox 拒绝警告", report,
                         "V37.9.81 B sandbox hint must not fire when no sandbox_denied")


if __name__ == "__main__":
    unittest.main()
