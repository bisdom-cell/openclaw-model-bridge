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
    classify_caller_failure_mode,
    classify_probe,
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
                 procs: str = "", mount: str = "(read-write)") -> dict:
    return {
        "timestamp_iso": ts_iso,
        "caller": caller,
        "exit_code": exit_code,
        "probe_top": probe_top,
        "probe_kb": probe_kb,
        "procs": procs,
        "mount": mount,
    }


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


if __name__ == "__main__":
    unittest.main()
