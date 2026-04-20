#!/usr/bin/env python3
"""test_conv_quality.py — conv_quality.py 单测"""
import unittest, tempfile, os, json
from unittest.mock import patch

# Patch log paths before import
_tmpdir = tempfile.mkdtemp()
_proxy_log = os.path.join(_tmpdir, "tool_proxy.log")
_adapter_log = os.path.join(_tmpdir, "adapter.log")
_report_json = os.path.join(_tmpdir, "conv_quality.json")

import conv_quality
conv_quality.PROXY_LOG = _proxy_log
conv_quality.ADAPTER_LOG = _adapter_log
conv_quality.REPORT_JSON = _report_json

DATE = "2026-03-24"


def write_proxy_log(lines):
    with open(_proxy_log, "w") as f:
        for line in lines:
            f.write(line + "\n")


def write_adapter_log(lines):
    with open(_adapter_log, "w") as f:
        for line in lines:
            f.write(line + "\n")


class TestParseLogBasic(unittest.TestCase):
    """Basic log parsing tests."""

    def test_empty_logs(self):
        """No log files → zero metrics."""
        for p in (_proxy_log, _adapter_log):
            if os.path.exists(p):
                os.remove(p)
        data = conv_quality.parse_logs(DATE)
        self.assertEqual(data["total_requests"], 0)
        self.assertEqual(data["success_rate"], 0)

    def test_success_request(self):
        """Single successful request parsed correctly."""
        write_proxy_log([
            f"[proxy] {DATE} 10:00:00 [abc12345] Backend: 200 1500b 350ms stream=True",
            f"[proxy] {DATE} 10:00:00 [abc12345] TEXT: 500 chars",
            f"[proxy] {DATE} 10:00:00 [abc12345] TOKENS: prompt=50,000 total=51,000 (19% of 260K)",
        ])
        data = conv_quality.parse_logs(DATE)
        self.assertEqual(data["total_requests"], 1)
        self.assertEqual(data["success_count"], 1)
        self.assertEqual(data["success_rate"], 100.0)
        self.assertEqual(data["latency"]["avg"], 350)
        self.assertEqual(data["text_responses"], 1)
        self.assertEqual(data["token_stats"]["avg_prompt"], 50000)
        self.assertEqual(data["token_stats"]["max_prompt"], 50000)

    def test_error_request(self):
        """Backend error parsed as failure."""
        write_proxy_log([
            f"[proxy] {DATE} 10:00:00 [err00001] Backend error (1200ms): HTTP Error 502: Bad Gateway",
        ])
        data = conv_quality.parse_logs(DATE)
        self.assertEqual(data["total_requests"], 1)
        self.assertEqual(data["error_count"], 1)
        self.assertEqual(data["success_rate"], 0)
        self.assertIn("backend_error", data["error_types"])

    def test_403_error(self):
        """403 classified as auth/context_overflow."""
        write_proxy_log([
            f"[proxy] {DATE} 10:00:00 [err00002] Backend error (500ms): HTTP Error 403: Forbidden",
        ])
        data = conv_quality.parse_logs(DATE)
        self.assertIn("auth/context_overflow", data["error_types"])

    def test_tool_calls(self):
        """Tool call counting and top tools."""
        write_proxy_log([
            f"[proxy] {DATE} 10:00:00 [rid00001] Backend: 200 1000b 300ms stream=False",
            f"[proxy] {DATE} 10:00:00 [rid00001] CALL: web_search (50 bytes)",
            f"[proxy] {DATE} 10:00:01 [rid00002] Backend: 200 2000b 400ms stream=False",
            f"[proxy] {DATE} 10:00:01 [rid00002] CALL: web_search (60 bytes)",
            f"[proxy] {DATE} 10:00:01 [rid00002] CALL: read (30 bytes)",
            f"[proxy] {DATE} 10:00:02 [rid00003] Backend: 200 500b 200ms stream=False",
            f"[proxy] {DATE} 10:00:02 [rid00003] CALL: exec (80 bytes)",
        ])
        data = conv_quality.parse_logs(DATE)
        self.assertEqual(data["tool_calls_total"], 4)
        self.assertEqual(data["tool_responses"], 3)  # 3 unique rids with tool calls
        # web_search should be top tool
        top = dict(data["top_tools"])
        self.assertEqual(top["web_search"], 2)
        self.assertEqual(top["read"], 1)
        self.assertEqual(top["exec"], 1)

    def test_truncation(self):
        """Message truncation events counted."""
        write_proxy_log([
            f"[proxy] {DATE} 10:00:00 [rid00001] WARN: Truncated 5 old messages (20 -> 15 msgs)",
            f"[proxy] {DATE} 10:01:00 [rid00002] WARN: Truncated 3 old messages (18 -> 15 msgs)",
        ])
        data = conv_quality.parse_logs(DATE)
        self.assertEqual(data["truncation_count"], 2)

    def test_context_pressure(self):
        """Token > 75% of 260K flagged as context pressure."""
        write_proxy_log([
            f"[proxy] {DATE} 10:00:00 [rid00001] Backend: 200 1000b 300ms stream=False",
            f"[proxy] {DATE} 10:00:00 [rid00001] TOKENS: prompt=200,000 total=201,000 (76% of 260K)",
            f"[proxy] {DATE} 10:01:00 [rid00002] Backend: 200 1000b 300ms stream=False",
            f"[proxy] {DATE} 10:01:00 [rid00002] TOKENS: prompt=50,000 total=51,000 (19% of 260K)",
        ])
        data = conv_quality.parse_logs(DATE)
        self.assertEqual(data["token_stats"]["context_pressure_count"], 1)

    def test_date_filtering(self):
        """Only target date lines are included."""
        write_proxy_log([
            f"[proxy] 2026-03-23 10:00:00 [old00001] Backend: 200 1000b 300ms stream=False",
            f"[proxy] {DATE} 10:00:00 [new00001] Backend: 200 2000b 500ms stream=False",
        ])
        data = conv_quality.parse_logs(DATE)
        self.assertEqual(data["total_requests"], 1)
        self.assertEqual(data["latency"]["avg"], 500)


class TestParseAdapterLog(unittest.TestCase):
    """Adapter log fallback parsing."""

    def test_fallback_events(self):
        write_proxy_log([])  # empty proxy log
        write_adapter_log([
            f"[adapter:qwen] {DATE} 10:00:00 [rid00001] PRIMARY FAILED (5000ms): timeout",
            f"[adapter:qwen] {DATE} 10:00:05 [rid00001] FALLBACK -> gemini (gemini-2.5-flash)",
            f"[adapter:qwen] {DATE} 10:00:08 [rid00001] FALLBACK OK: 200 (3000 bytes) 8000ms",
            f"[adapter:qwen] {DATE} 10:01:00 [rid00002] PRIMARY FAILED (5000ms): 502",
            f"[adapter:qwen] {DATE} 10:01:05 [rid00002] FALLBACK ALSO FAILED (10000ms): 503",
        ])
        data = conv_quality.parse_logs(DATE)
        self.assertEqual(data["fallback"]["triggered"], 2)
        self.assertEqual(data["fallback"]["success"], 1)
        self.assertEqual(data["fallback"]["failed"], 1)


class TestLatencyPercentiles(unittest.TestCase):
    """Latency percentile calculations."""

    def test_multiple_requests(self):
        lines = []
        for i in range(20):
            rid = f"perf{i:04d}"
            ms = 200 + i * 100  # 200, 300, ..., 2100
            lines.append(f"[proxy] {DATE} 10:{i:02d}:00 [{rid}] Backend: 200 1000b {ms}ms stream=False")
        write_proxy_log(lines)
        data = conv_quality.parse_logs(DATE)
        self.assertEqual(data["total_requests"], 20)
        self.assertEqual(data["latency"]["min"], 200)
        self.assertEqual(data["latency"]["max"], 2100)
        # avg should be 1150 = (200+2100)/2
        self.assertEqual(data["latency"]["avg"], 1150)
        # P95 = index 19 (95% of 20 = 19)
        self.assertEqual(data["latency"]["p95"], 2100)

    def test_single_request_percentiles(self):
        """Single request: all percentiles equal."""
        write_proxy_log([
            f"[proxy] {DATE} 10:00:00 [solo0001] Backend: 200 500b 999ms stream=False",
        ])
        data = conv_quality.parse_logs(DATE)
        self.assertEqual(data["latency"]["avg"], 999)
        self.assertEqual(data["latency"]["p50"], 999)
        self.assertEqual(data["latency"]["p95"], 999)


class TestFormatReport(unittest.TestCase):
    """Report formatting."""

    def test_zero_requests(self):
        data = conv_quality.parse_logs("1999-01-01")
        report = conv_quality.format_report(data)
        self.assertIn("无请求记录", report)

    def test_full_report(self):
        write_proxy_log([
            f"[proxy] {DATE} 10:00:00 [rid00001] Backend: 200 1000b 300ms stream=False",
            f"[proxy] {DATE} 10:00:00 [rid00001] CALL: web_search (50 bytes)",
            f"[proxy] {DATE} 10:00:00 [rid00001] TOKENS: prompt=50,000 total=51,000 (19% of 260K)",
            f"[proxy] {DATE} 10:00:00 [rid00001] WARN: Truncated 2 old messages (10 -> 8 msgs)",
        ])
        data = conv_quality.parse_logs(DATE)
        report = conv_quality.format_report(data)
        self.assertIn("100.0%", report)
        self.assertIn("web_search", report)
        self.assertIn("截断", report)
        self.assertIn("日报完毕", report)


class TestWriteJson(unittest.TestCase):
    """JSON output."""

    def test_json_output(self):
        write_proxy_log([
            f"[proxy] {DATE} 10:00:00 [rid00001] Backend: 200 1000b 300ms stream=False",
            f"[proxy] {DATE} 10:00:00 [rid00001] TOKENS: prompt=50,000 total=51,000 (19% of 260K)",
        ])
        data = conv_quality.parse_logs(DATE)
        conv_quality.write_json(data)
        self.assertTrue(os.path.exists(_report_json))
        with open(_report_json) as f:
            output = json.load(f)
        self.assertEqual(output["total_requests"], 1)
        self.assertIn("generated_at", output)


class TestParseInt(unittest.TestCase):
    def test_comma_format(self):
        self.assertEqual(conv_quality.parse_int("12,345"), 12345)
        self.assertEqual(conv_quality.parse_int("260,000"), 260000)
        self.assertEqual(conv_quality.parse_int("500"), 500)


class TestMixedDay(unittest.TestCase):
    """Real-world scenario: mixed success/failure/tools across a day."""

    def test_realistic_day(self):
        write_proxy_log([
            # 10 successful requests
            f"[proxy] {DATE} 08:00:00 [r0000001] Backend: 200 1500b 400ms stream=True",
            f"[proxy] {DATE} 08:00:00 [r0000001] TEXT: 300 chars",
            f"[proxy] {DATE} 08:00:00 [r0000001] TOKENS: prompt=10,000 total=10,500 (3% of 260K)",
            f"[proxy] {DATE} 09:00:00 [r0000002] Backend: 200 3000b 800ms stream=True",
            f"[proxy] {DATE} 09:00:00 [r0000002] CALL: web_search (50 bytes)",
            f"[proxy] {DATE} 09:00:00 [r0000002] CALL: read (30 bytes)",
            f"[proxy] {DATE} 09:00:00 [r0000002] TOKENS: prompt=30,000 total=31,000 (11% of 260K)",
            f"[proxy] {DATE} 10:00:00 [r0000003] Backend: 200 2000b 600ms stream=False",
            f"[proxy] {DATE} 10:00:00 [r0000003] CALL: exec (100 bytes)",
            f"[proxy] {DATE} 10:00:00 [r0000003] TOKENS: prompt=25,000 total=26,000 (9% of 260K)",
            # 1 error
            f"[proxy] {DATE} 11:00:00 [r0000004] Backend error (5000ms): HTTP Error 502: Bad Gateway",
            # 1 context overflow
            f"[proxy] {DATE} 12:00:00 [r0000005] Backend: 200 500b 1500ms stream=True",
            f"[proxy] {DATE} 12:00:00 [r0000005] TOKENS: prompt=200,000 total=202,000 (76% of 260K)",
            # truncation
            f"[proxy] {DATE} 12:00:00 [r0000005] WARN: Truncated 4 old messages (20 -> 16 msgs)",
        ])
        write_adapter_log([
            f"[adapter:qwen] {DATE} 11:00:00 [r0000004] PRIMARY FAILED (5000ms): 502",
            f"[adapter:qwen] {DATE} 11:00:05 [r0000004] FALLBACK OK: 200 (1000 bytes) 8000ms",
        ])

        data = conv_quality.parse_logs(DATE)
        self.assertEqual(data["total_requests"], 5)
        self.assertEqual(data["success_count"], 4)
        self.assertEqual(data["error_count"], 1)
        self.assertEqual(data["success_rate"], 80.0)
        self.assertEqual(data["tool_calls_total"], 3)
        self.assertEqual(data["text_responses"], 1)
        self.assertEqual(data["truncation_count"], 1)
        self.assertEqual(data["token_stats"]["context_pressure_count"], 1)
        self.assertEqual(data["fallback"]["triggered"], 1)
        self.assertEqual(data["fallback"]["success"], 1)

        # Report should be readable
        report = conv_quality.format_report(data)
        self.assertIn("80.0%", report)
        self.assertIn("Fallback", report)


if __name__ == "__main__":
    unittest.main()
