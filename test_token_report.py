#!/usr/bin/env python3
"""test_token_report.py — token_report.py 单测"""
import unittest, tempfile, os, json

_tmpdir = tempfile.mkdtemp()
_proxy_log = os.path.join(_tmpdir, "tool_proxy.log")
_history_file = os.path.join(_tmpdir, "token_history.json")
_report_json = os.path.join(_tmpdir, "token_report.json")

import token_report
token_report.PROXY_LOG = _proxy_log
token_report.HISTORY_FILE = _history_file
token_report.REPORT_JSON = _report_json

DATE = "2026-03-24"


def write_log(lines):
    with open(_proxy_log, "w") as f:
        for line in lines:
            f.write(line + "\n")


def reset():
    for p in (_proxy_log, _history_file, _report_json):
        if os.path.exists(p):
            os.remove(p)


class TestParseTokens(unittest.TestCase):

    def setUp(self):
        reset()

    def test_no_log(self):
        self.assertIsNone(token_report.parse_tokens(DATE))

    def test_single_request(self):
        write_log([
            f"[proxy] {DATE} 10:05:30 [abc12345] Backend: 200 1500b 350ms stream=True",
            f"[proxy] {DATE} 10:05:30 [abc12345] TOKENS: prompt=50,000 total=52,000 (19% of 260K)",
        ])
        data = token_report.parse_tokens(DATE)
        self.assertEqual(data["request_count"], 1)
        self.assertEqual(data["total_prompt_tokens"], 50000)
        self.assertEqual(data["total_completion_tokens"], 2000)
        self.assertEqual(data["total_tokens"], 52000)
        self.assertEqual(data["avg_prompt"], 50000)
        self.assertEqual(data["peak_hour"], 10)

    def test_multiple_hours(self):
        write_log([
            f"[proxy] {DATE} 08:00:00 [r0000001] Backend: 200 1000b 300ms stream=False",
            f"[proxy] {DATE} 08:00:00 [r0000001] TOKENS: prompt=10,000 total=11,000 (3% of 260K)",
            f"[proxy] {DATE} 08:30:00 [r0000002] Backend: 200 1000b 300ms stream=False",
            f"[proxy] {DATE} 08:30:00 [r0000002] TOKENS: prompt=20,000 total=22,000 (7% of 260K)",
            f"[proxy] {DATE} 14:00:00 [r0000003] Backend: 200 1000b 300ms stream=False",
            f"[proxy] {DATE} 14:00:00 [r0000003] TOKENS: prompt=5,000 total=6,000 (1% of 260K)",
        ])
        data = token_report.parse_tokens(DATE)
        self.assertEqual(data["request_count"], 3)
        self.assertEqual(data["total_prompt_tokens"], 35000)
        # Peak hour: 08 has 33000 total tokens, 14 has 6000
        self.assertEqual(data["peak_hour"], 8)
        self.assertEqual(len(data["hourly"]), 2)  # hours 8 and 14

    def test_distribution_buckets(self):
        write_log([
            f"[proxy] {DATE} 10:00:00 [r001] Backend: 200 100b 100ms stream=False",
            f"[proxy] {DATE} 10:00:00 [r001] TOKENS: prompt=5,000 total=6,000 (1% of 260K)",
            f"[proxy] {DATE} 10:01:00 [r002] Backend: 200 100b 100ms stream=False",
            f"[proxy] {DATE} 10:01:00 [r002] TOKENS: prompt=30,000 total=31,000 (11% of 260K)",
            f"[proxy] {DATE} 10:02:00 [r003] Backend: 200 100b 100ms stream=False",
            f"[proxy] {DATE} 10:02:00 [r003] TOKENS: prompt=80,000 total=82,000 (30% of 260K)",
            f"[proxy] {DATE} 10:03:00 [r004] Backend: 200 100b 100ms stream=False",
            f"[proxy] {DATE} 10:03:00 [r004] TOKENS: prompt=150,000 total=155,000 (57% of 260K)",
        ])
        data = token_report.parse_tokens(DATE)
        self.assertEqual(data["distribution"]["<10K"], 1)
        self.assertEqual(data["distribution"]["10-50K"], 1)
        self.assertEqual(data["distribution"]["50-100K"], 1)
        self.assertEqual(data["distribution"]["100K+"], 1)

    def test_context_pressure(self):
        write_log([
            f"[proxy] {DATE} 10:00:00 [r001] Backend: 200 100b 100ms stream=False",
            f"[proxy] {DATE} 10:00:00 [r001] TOKENS: prompt=200,000 total=201,000 (76% of 260K)",
            f"[proxy] {DATE} 10:01:00 [r002] Backend: 200 100b 100ms stream=False",
            f"[proxy] {DATE} 10:01:00 [r002] TOKENS: prompt=240,000 total=241,000 (92% of 260K)",
        ])
        data = token_report.parse_tokens(DATE)
        self.assertEqual(data["context_pressure"]["warn_75pct"], 2)  # both >= 195K
        self.assertEqual(data["context_pressure"]["critical_90pct"], 1)  # only 240K >= 234K

    def test_date_filter(self):
        write_log([
            f"[proxy] 2026-03-23 10:00:00 [old001] Backend: 200 100b 100ms stream=False",
            f"[proxy] 2026-03-23 10:00:00 [old001] TOKENS: prompt=99,999 total=100,000 (38% of 260K)",
            f"[proxy] {DATE} 10:00:00 [new001] Backend: 200 100b 100ms stream=False",
            f"[proxy] {DATE} 10:00:00 [new001] TOKENS: prompt=5,000 total=6,000 (1% of 260K)",
        ])
        data = token_report.parse_tokens(DATE)
        self.assertEqual(data["request_count"], 1)
        self.assertEqual(data["total_prompt_tokens"], 5000)


class TestHistory(unittest.TestCase):

    def setUp(self):
        reset()

    def test_append_creates_file(self):
        data = {"date": DATE, "request_count": 5, "total_tokens": 100000,
                "total_prompt_tokens": 80000, "total_completion_tokens": 20000,
                "avg_prompt": 16000, "max_prompt": 30000, "peak_hour": 10}
        days = token_report.append_history(data)
        self.assertEqual(len(days), 1)
        self.assertTrue(os.path.exists(_history_file))

    def test_idempotent_rerun(self):
        data = {"date": DATE, "request_count": 5, "total_tokens": 100000,
                "total_prompt_tokens": 80000, "total_completion_tokens": 20000,
                "avg_prompt": 16000, "max_prompt": 30000, "peak_hour": 10}
        token_report.append_history(data)
        token_report.append_history(data)  # re-run same date
        history = token_report.load_history()
        self.assertEqual(len(history["days"]), 1)  # not duplicated

    def test_multi_day(self):
        for i in range(3):
            d = f"2026-03-{22+i:02d}"
            data = {"date": d, "request_count": i+1, "total_tokens": (i+1)*10000,
                    "total_prompt_tokens": (i+1)*8000, "total_completion_tokens": (i+1)*2000,
                    "avg_prompt": 8000, "max_prompt": 12000, "peak_hour": 10}
            token_report.append_history(data)
        history = token_report.load_history()
        self.assertEqual(len(history["days"]), 3)
        # Sorted by date
        dates = [d["date"] for d in history["days"]]
        self.assertEqual(dates, sorted(dates))


class TestFormatReport(unittest.TestCase):

    def test_basic_report(self):
        write_log([
            f"[proxy] {DATE} 10:00:00 [r001] Backend: 200 100b 300ms stream=False",
            f"[proxy] {DATE} 10:00:00 [r001] TOKENS: prompt=50,000 total=52,000 (19% of 260K)",
        ])
        data = token_report.parse_tokens(DATE)
        report = token_report.format_report(data, None)
        self.assertIn("52,000 tokens", report)
        self.assertIn("Prompt: 50,000", report)
        self.assertIn("Token 日报完毕", report)

    def test_day_over_day(self):
        write_log([
            f"[proxy] {DATE} 10:00:00 [r001] Backend: 200 100b 300ms stream=False",
            f"[proxy] {DATE} 10:00:00 [r001] TOKENS: prompt=50,000 total=52,000 (19% of 260K)",
        ])
        data = token_report.parse_tokens(DATE)
        prev = {"total_tokens": 40000}
        report = token_report.format_report(data, prev)
        self.assertIn("+30.0%", report)
        self.assertIn("昨日 40,000", report)


if __name__ == "__main__":
    unittest.main()
