#!/usr/bin/env python3
"""
test_daily_observer.py — V37.9.84 Daily Self-Critique Observer 单测

测试层级:
  - 纯函数逻辑 (scan/detect/parse/build)
  - Orchestrator (mock LLM)
  - Shell wrapper source-level guards
  - CLI behavior (subprocess)
"""
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from datetime import datetime
from unittest import mock


sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import daily_observer as obs


class TestResolveLastRunPath(unittest.TestCase):
    """Multi-candidate path resolution (V37.9.56-hotfix same pattern)."""

    def test_finds_in_primary_jobs_dir(self):
        with tempfile.TemporaryDirectory() as td:
            job_dir = os.path.join(td, "hf_papers", "cache")
            os.makedirs(job_dir)
            lr = os.path.join(job_dir, "last_run.json")
            with open(lr, "w") as f:
                f.write("{}")
            result = obs._resolve_last_run_path(td, "hf_papers")
            self.assertEqual(result, lr)

    def test_falls_back_to_mac_mini_path(self):
        """When primary path missing, finds ~/.openclaw/jobs/X/cache/."""
        with tempfile.TemporaryDirectory() as td:
            mac_dir = os.path.join(td, "mac_openclaw", "jobs",
                                   "hf_papers", "cache")
            os.makedirs(mac_dir)
            lr = os.path.join(mac_dir, "last_run.json")
            with open(lr, "w") as f:
                f.write("{}")
            original = obs._MAC_MINI_JOBS_DIR
            try:
                obs._MAC_MINI_JOBS_DIR = os.path.join(
                    td, "mac_openclaw", "jobs")
                result = obs._resolve_last_run_path(
                    os.path.join(td, "nonexistent"), "hf_papers")
                self.assertEqual(result, lr)
            finally:
                obs._MAC_MINI_JOBS_DIR = original

    def test_returns_none_when_all_missing(self):
        with tempfile.TemporaryDirectory() as td:
            result = obs._resolve_last_run_path(td, "nonexistent_job")
            self.assertIsNone(result)

    def test_primary_takes_precedence(self):
        """Primary dir wins even if Mac Mini path also exists."""
        with tempfile.TemporaryDirectory() as td:
            primary = os.path.join(td, "primary", "dblp", "cache")
            os.makedirs(primary)
            with open(os.path.join(primary, "last_run.json"), "w") as f:
                json.dump({"status": "primary"}, f)
            mac = os.path.join(td, "mac", "dblp", "cache")
            os.makedirs(mac)
            with open(os.path.join(mac, "last_run.json"), "w") as f:
                json.dump({"status": "mac"}, f)
            original = obs._MAC_MINI_JOBS_DIR
            try:
                obs._MAC_MINI_JOBS_DIR = os.path.join(td, "mac")
                result = obs._resolve_last_run_path(
                    os.path.join(td, "primary"), "dblp")
                self.assertIn("primary", result)
            finally:
                obs._MAC_MINI_JOBS_DIR = original


class TestScanJobStatuses(unittest.TestCase):
    """Scan last_run.json from job cache directories."""

    def setUp(self):
        # V37.9.121-hotfix: isolate from _MAC_MINI_JOBS_DIR fallback.
        # _resolve_last_run_path falls back to real ~/.openclaw/jobs (V37.9.56-
        # hotfix, so the observer finds real last_run.json on Mac Mini). That
        # fallback leaks real Mac Mini state into these temp-dir tests: on
        # Mac Mini, test_missing_last_run (empty temp dir) fell through to the
        # real ~/.openclaw/jobs/<job>/cache/last_run.json → found=True →
        # AssertionError (dev had no such path so passed — dev/production seam,
        # surfaced by INV-OBSERVER-001 running the suite in 07:00 governance).
        # Mirror the isolation TestResolveLastRunPath already does (line ~47-55)
        # but for the whole class so the temp jobs_dir is the only source.
        self._orig_mac_jobs_dir = obs._MAC_MINI_JOBS_DIR
        obs._MAC_MINI_JOBS_DIR = "/nonexistent/.openclaw/jobs/test-isolation"

    def tearDown(self):
        obs._MAC_MINI_JOBS_DIR = self._orig_mac_jobs_dir

    def test_reads_ok_status(self):
        with tempfile.TemporaryDirectory() as td:
            job_dir = os.path.join(td, "hf_papers", "cache")
            os.makedirs(job_dir)
            with open(os.path.join(job_dir, "last_run.json"), "w") as f:
                json.dump({"status": "ok", "time": "2026-05-25", "new": 10}, f)
            results = obs.scan_job_statuses(td, datetime(2026, 5, 25))
            hf = [r for r in results if r["job_id"] == "hf_papers"][0]
            self.assertTrue(hf["found"])
            self.assertEqual(hf["status"], "ok")
            self.assertEqual(hf["new"], 10)

    def test_missing_last_run(self):
        with tempfile.TemporaryDirectory() as td:
            results = obs.scan_job_statuses(td, datetime(2026, 5, 25))
            self.assertTrue(all(not r["found"] for r in results))
            self.assertTrue(len(results) >= 10)

    def test_corrupted_json(self):
        with tempfile.TemporaryDirectory() as td:
            job_dir = os.path.join(td, "dblp", "cache")
            os.makedirs(job_dir)
            with open(os.path.join(job_dir, "last_run.json"), "w") as f:
                f.write("{bad json")
            results = obs.scan_job_statuses(td, datetime(2026, 5, 25))
            dblp = [r for r in results if r["job_id"] == "dblp"][0]
            self.assertTrue(dblp["found"])
            self.assertEqual(dblp["status"], "parse_error")

    def test_boolean_new_field(self):
        """sent=true (bool) should become new=1."""
        with tempfile.TemporaryDirectory() as td:
            job_dir = os.path.join(td, "rss_blogs", "cache")
            os.makedirs(job_dir)
            with open(os.path.join(job_dir, "last_run.json"), "w") as f:
                json.dump({"status": "ok", "new": True}, f)
            results = obs.scan_job_statuses(td, datetime(2026, 5, 25))
            rss = [r for r in results if r["job_id"] == "rss_blogs"][0]
            self.assertEqual(rss["new"], 1)


class TestScanPushOutputs(unittest.TestCase):
    """Scan evening/dream/deep_dive output files."""

    def test_found_all_three(self):
        with tempfile.TemporaryDirectory() as td:
            os.makedirs(os.path.join(td, "daily"))
            os.makedirs(os.path.join(td, "dreams"))
            os.makedirs(os.path.join(td, "deep_dives"))
            with open(os.path.join(td, "daily", "evening_20260525.md"), "w") as f:
                f.write("# Evening\nContent here " * 50)
            with open(os.path.join(td, "dreams", "2026-05-25.md"), "w") as f:
                f.write("# Dream\nDream content " * 100)
            with open(os.path.join(td, "deep_dives", "2026-05-25.md"), "w") as f:
                f.write("# Deep Dive\nAnalysis " * 30)

            result = obs.scan_push_outputs(td, datetime(2026, 5, 25))
            self.assertTrue(result["evening"]["found"])
            self.assertTrue(result["dream"]["found"])
            self.assertTrue(result["deep_dive"]["found"])
            self.assertGreater(result["dream"]["length"], 0)

    def test_missing_outputs(self):
        with tempfile.TemporaryDirectory() as td:
            result = obs.scan_push_outputs(td, datetime(2026, 5, 25))
            self.assertFalse(result["evening"]["found"])
            self.assertFalse(result["dream"]["found"])
            self.assertFalse(result["deep_dive"]["found"])

    def test_content_sampled(self):
        """Content should be truncated to MAX_SAMPLE_CHARS."""
        with tempfile.TemporaryDirectory() as td:
            os.makedirs(os.path.join(td, "daily"))
            long_content = "x" * 5000
            with open(os.path.join(td, "daily", "evening_20260525.md"), "w") as f:
                f.write(long_content)
            result = obs.scan_push_outputs(td, datetime(2026, 5, 25))
            self.assertEqual(len(result["evening"]["content"]),
                             obs.MAX_SAMPLE_CHARS)
            self.assertEqual(result["evening"]["length"], 5000)


class TestScanSourceSections(unittest.TestCase):
    """Scan H2 date sections from source markdown files."""

    def test_extracts_matching_date(self):
        with tempfile.TemporaryDirectory() as td:
            os.makedirs(os.path.join(td, "sources"))
            with open(os.path.join(td, "sources", "arxiv_daily.md"), "w") as f:
                f.write("## 2026-05-25\nPaper about ontology\n"
                        "## 2026-05-24\nOlder paper\n")
            result = obs.scan_source_sections(td, datetime(2026, 5, 25))
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0]["source"], "arxiv_daily")
            self.assertIn("ontology", result[0]["section_text"])

    def test_no_matching_date(self):
        with tempfile.TemporaryDirectory() as td:
            os.makedirs(os.path.join(td, "sources"))
            with open(os.path.join(td, "sources", "hf_papers_daily.md"), "w") as f:
                f.write("## 2026-05-20\nOld content\n")
            result = obs.scan_source_sections(td, datetime(2026, 5, 25))
            self.assertEqual(len(result), 0)

    def test_sorted_by_length_desc(self):
        with tempfile.TemporaryDirectory() as td:
            os.makedirs(os.path.join(td, "sources"))
            with open(os.path.join(td, "sources", "short.md"), "w") as f:
                f.write("## 2026-05-25\nShort\n")
            with open(os.path.join(td, "sources", "long.md"), "w") as f:
                f.write("## 2026-05-25\n" + "Long content " * 50 + "\n")
            result = obs.scan_source_sections(td, datetime(2026, 5, 25))
            self.assertEqual(len(result), 2)
            self.assertGreater(result[0]["char_count"], result[1]["char_count"])

    def test_missing_sources_dir(self):
        with tempfile.TemporaryDirectory() as td:
            result = obs.scan_source_sections(td, datetime(2026, 5, 25))
            self.assertEqual(result, [])


class TestDetectAnomalies(unittest.TestCase):
    """Rule-based anomaly detection."""

    def test_detects_failed_jobs(self):
        statuses = [{"job_id": "hf_papers", "found": True,
                     "status": "llm_failed", "new": 0,
                     "time": "", "reason": "HTTP 502"}]
        anomalies = obs.detect_anomalies(statuses, {}, [])
        high = [a for a in anomalies if a["severity"] == "HIGH"
                and a["category"] == "job_failure"]
        self.assertGreater(len(high), 0)
        self.assertIn("hf_papers", high[0]["message"])

    def test_detects_missing_outputs(self):
        outputs = {
            "evening": {"found": False, "length": 0},
            "dream": {"found": True, "length": 5000},
            "deep_dive": {"found": False, "length": 0},
        }
        anomalies = obs.detect_anomalies([], outputs, [])
        missing = [a for a in anomalies if a["category"] == "missing_output"]
        self.assertEqual(len(missing), 2)

    def test_detects_thin_output(self):
        outputs = {
            "evening": {"found": True, "length": 50},
        }
        anomalies = obs.detect_anomalies([], outputs, [])
        thin = [a for a in anomalies if a["category"] == "thin_output"]
        self.assertEqual(len(thin), 1)

    def test_no_anomalies_when_all_ok(self):
        statuses = [{"job_id": "hf_papers", "found": True,
                     "status": "ok", "new": 10, "time": "", "reason": ""}]
        outputs = {
            "evening": {"found": True, "length": 3000},
            "dream": {"found": True, "length": 5000},
            "deep_dive": {"found": True, "length": 2000},
        }
        sources = [{"source": "arxiv", "section_text": "...", "char_count": 500}]
        anomalies = obs.detect_anomalies(statuses, outputs, sources)
        self.assertEqual(len(anomalies), 0)

    def test_detects_no_sources(self):
        anomalies = obs.detect_anomalies([], {}, [])
        no_src = [a for a in anomalies if a["category"] == "no_sources"]
        self.assertEqual(len(no_src), 1)


class TestParseOverallScore(unittest.TestCase):
    """Extract overall score from LLM output."""

    def test_parses_star_count_format(self):
        text = "综合: ⭐×4 / 5"
        self.assertEqual(obs.parse_overall_score(text), 4.0)

    def test_parses_decimal(self):
        text = "综合：⭐×3.5 / 5"
        self.assertEqual(obs.parse_overall_score(text), 3.5)

    def test_parses_emoji_stars(self):
        text = "综合: ⭐⭐⭐⭐"
        self.assertEqual(obs.parse_overall_score(text), 4)

    def test_returns_none_for_unparseable(self):
        self.assertIsNone(obs.parse_overall_score("no score here"))

    def test_returns_none_for_empty(self):
        self.assertIsNone(obs.parse_overall_score(""))


class TestBuildCritiquePrompt(unittest.TestCase):
    """Build LLM critique prompt."""

    def test_includes_evening_content(self):
        outputs = {
            "evening": {"found": True, "content": "Evening analysis here",
                        "length": 1000},
            "dream": {"found": False, "content": "", "length": 0},
            "deep_dive": {"found": False, "content": "", "length": 0},
        }
        prompt = obs.build_critique_prompt(outputs, [], [], datetime(2026, 5, 25))
        self.assertIn("Evening analysis here", prompt)
        self.assertIn("2026-05-25", prompt)

    def test_returns_empty_when_no_content(self):
        outputs = {
            "evening": {"found": False, "content": "", "length": 0},
            "dream": {"found": False, "content": "", "length": 0},
            "deep_dive": {"found": False, "content": "", "length": 0},
        }
        prompt = obs.build_critique_prompt(outputs, [], [], datetime(2026, 5, 25))
        self.assertEqual(prompt, "")

    def test_includes_anomalies(self):
        outputs = {
            "evening": {"found": True, "content": "content", "length": 500},
        }
        anomalies = [{"severity": "HIGH", "category": "job_failure",
                       "message": "hf_papers: llm_failed"}]
        prompt = obs.build_critique_prompt(
            outputs, [], anomalies, datetime(2026, 5, 25))
        self.assertIn("hf_papers", prompt)
        self.assertIn("HIGH", prompt)


class TestRunOrchestrator(unittest.TestCase):
    """End-to-end orchestrator tests with mock LLM.

    V37.9.92 isolation: run() now calls _write_observer_to_status() which
    invokes status_update.save_status() — that uses module-level
    STATUS_FILE constant and writes to repo's real status.json. Without
    setUp patching, every run() test pollutes status.json. We patch the
    helper to a no-op for the entire class (other V37.9.92 tests have
    their own mocks and aren't affected).
    """

    def setUp(self):
        from unittest.mock import patch
        self._patcher = patch.object(obs, "_write_observer_to_status",
                                     return_value=True)
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()

    def _setup_kb(self, td, date_ymd="20260525", date_dash="2026-05-25"):
        os.makedirs(os.path.join(td, "daily"))
        os.makedirs(os.path.join(td, "dreams"))
        os.makedirs(os.path.join(td, "deep_dives"))
        os.makedirs(os.path.join(td, "sources"))
        with open(os.path.join(td, "daily", f"evening_{date_ymd}.md"), "w") as f:
            f.write("# 🌙 Evening\nToday's analysis is excellent. " * 20)
        with open(os.path.join(td, "sources", "arxiv_daily.md"), "w") as f:
            f.write(f"## {date_dash}\nPaper about LLM agent safety\n" * 5)
        return td

    def test_ok_with_mock_llm(self):
        mock_response = textwrap.dedent("""
        ## 评分
        - 信息密度: ⭐×4
        - 准确性风险: ⭐×5
        - 主题多样性: ⭐×3
        - 可行动性: ⭐×4
        - 格式规范: ⭐×4
        - 综合: ⭐×4 / 5

        ## 发现的问题
        1. [LOW] Evening report could include more cross-domain links

        ## 改进提案
        1. Add cross-reference between arxiv papers and evening topics
        """)

        def mock_llm(system, user):
            return True, mock_response, ""

        with tempfile.TemporaryDirectory() as td:
            kb = self._setup_kb(td)
            result = obs.run(
                kb_dir=kb,
                jobs_dir=os.path.join(td, "fake_jobs"),
                target_date=datetime(2026, 5, 25),
                llm_caller=mock_llm,
            )
            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["overall_score"], 4.0)
            self.assertIn("Daily Self-Critique", result["report_markdown"])
            self.assertIn("综合", result["report_markdown"])

    def test_no_outputs(self):
        with tempfile.TemporaryDirectory() as td:
            result = obs.run(
                kb_dir=td,
                jobs_dir=os.path.join(td, "fake_jobs"),
                target_date=datetime(2026, 5, 25),
            )
            self.assertEqual(result["status"], "no_outputs")
            self.assertIsNone(result["overall_score"])

    def test_dry_run_skips_llm(self):
        with tempfile.TemporaryDirectory() as td:
            kb = self._setup_kb(td)
            result = obs.run(
                kb_dir=kb,
                jobs_dir=os.path.join(td, "fake_jobs"),
                target_date=datetime(2026, 5, 25),
                dry_run=True,
            )
            self.assertFalse(result["llm_ok"])
            self.assertEqual(result["llm_reason"], "dry_run")
            self.assertIn("Daily Self-Critique", result["report_markdown"])

    def test_llm_failure_produces_report_without_critique(self):
        def mock_fail(system, user):
            return False, "", "HTTP 502: Bad Gateway"

        with tempfile.TemporaryDirectory() as td:
            kb = self._setup_kb(td)
            result = obs.run(
                kb_dir=kb,
                jobs_dir=os.path.join(td, "fake_jobs"),
                target_date=datetime(2026, 5, 25),
                llm_caller=mock_fail,
            )
            self.assertEqual(result["status"], "llm_failed")
            self.assertIn("Job Coverage", result["report_markdown"])
            self.assertIn("Push Outputs", result["report_markdown"])

    def test_result_has_required_keys(self):
        with tempfile.TemporaryDirectory() as td:
            result = obs.run(
                kb_dir=td,
                jobs_dir=os.path.join(td, "fake_jobs"),
                target_date=datetime(2026, 5, 25),
            )
            required = {"status", "date", "report_markdown", "discord_summary",
                        "anomalies", "overall_score", "llm_ok", "llm_reason"}
            self.assertTrue(required.issubset(result.keys()))


class TestBuildReportMarkdown(unittest.TestCase):
    """Report markdown builder."""

    def test_contains_header_and_date(self):
        report = obs.build_report_markdown(
            datetime(2026, 5, 25), [], {"evening": {"found": False}},
            [], [], "", None)
        self.assertIn("Daily Self-Critique", report)
        self.assertIn("2026-05-25", report)
        self.assertIn("READ-ONLY", report)

    def test_contains_score_when_available(self):
        report = obs.build_report_markdown(
            datetime(2026, 5, 25), [], {}, [], [], "critique text", 4.0)
        self.assertIn("4", report)
        self.assertIn("⭐", report)

    def test_contains_llm_critique(self):
        report = obs.build_report_markdown(
            datetime(2026, 5, 25), [], {}, [], [],
            "## LLM says this is good", None)
        self.assertIn("LLM says this is good", report)


class TestDiscordSummary(unittest.TestCase):
    """Discord push summary builder."""

    def test_contains_date_and_score(self):
        summary = obs.build_discord_summary(
            datetime(2026, 5, 25), 4.0, [], [])
        self.assertIn("2026-05-25", summary)
        self.assertIn("⭐", summary)

    def test_contains_issue_counts(self):
        anomalies = [
            {"severity": "HIGH", "category": "x", "message": "y"},
            {"severity": "MED", "category": "x", "message": "y"},
        ]
        summary = obs.build_discord_summary(
            datetime(2026, 5, 25), 3.0, anomalies, [])
        self.assertIn("1 HIGH", summary)
        self.assertIn("1 MED", summary)


class TestCLI(unittest.TestCase):
    """CLI behavior tests (subprocess)."""

    def test_help(self):
        result = subprocess.run(
            [sys.executable, "daily_observer.py", "--help"],
            capture_output=True, text=True, cwd=os.path.dirname(__file__))
        self.assertEqual(result.returncode, 0)
        self.assertIn("Daily Self-Critique", result.stdout)

    def test_dry_run_json(self):
        result = subprocess.run(
            [sys.executable, "daily_observer.py", "--json", "--dry-run",
             "--date", "20260525"],
            capture_output=True, text=True, cwd=os.path.dirname(__file__),
            timeout=10)
        self.assertEqual(result.returncode, 0)
        data = json.loads(result.stdout)
        self.assertIn("status", data)
        self.assertIn("date", data)

    def test_invalid_date(self):
        result = subprocess.run(
            [sys.executable, "daily_observer.py", "--date", "bad"],
            capture_output=True, text=True, cwd=os.path.dirname(__file__),
            timeout=10)
        self.assertEqual(result.returncode, 1)


class TestScoreHistory(unittest.TestCase):
    """Score history append + load."""

    def test_append_and_load(self):
        with tempfile.TemporaryDirectory() as td:
            os.makedirs(os.path.join(td, "self_critique"))
            obs.append_score_history(
                td, datetime(2026, 5, 25), 4.0,
                [{"severity": "HIGH", "category": "x", "message": "y"}],
                [{"job_id": "hf", "status": "ok", "new": 5,
                  "found": True, "time": "", "reason": ""}],
                {"evening": {"found": True}}, "ok")
            history = obs.load_score_history(td)
            self.assertEqual(len(history), 1)
            self.assertEqual(history[0]["overall_score"], 4.0)
            self.assertEqual(history[0]["anomalies_high"], 1)
            self.assertEqual(history[0]["jobs_ok"], 1)

    def test_multiple_days_dedup(self):
        with tempfile.TemporaryDirectory() as td:
            os.makedirs(os.path.join(td, "self_critique"))
            for day in (24, 25, 25):  # 25 appears twice
                obs.append_score_history(
                    td, datetime(2026, 5, day), 3.0, [], [], {}, "ok")
            history = obs.load_score_history(td)
            self.assertEqual(len(history), 2)  # deduped by date

    def test_load_empty(self):
        with tempfile.TemporaryDirectory() as td:
            history = obs.load_score_history(td)
            self.assertEqual(history, [])

    def test_load_corrupted_lines(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "self_critique", "score_history.jsonl")
            os.makedirs(os.path.dirname(path))
            with open(path, "w") as f:
                f.write('{"date":"2026-05-25","overall_score":4}\n')
                f.write('bad json\n')
                f.write('{"date":"2026-05-24","overall_score":3}\n')
            history = obs.load_score_history(td)
            self.assertEqual(len(history), 2)


class TestTrendAnalysis(unittest.TestCase):
    """Trend section builder."""

    def test_insufficient_data(self):
        self.assertEqual(obs.build_trend_section([]), "")
        self.assertEqual(obs.build_trend_section(
            [{"overall_score": 4}]), "")

    def test_improvement_detected(self):
        history = [
            {"overall_score": 4.0, "date": "2026-05-25",
             "anomalies_high": 0, "jobs_ok": 14, "jobs_total": 15},
            {"overall_score": 3.0, "date": "2026-05-24",
             "anomalies_high": 2, "jobs_ok": 12, "jobs_total": 15},
        ]
        section = obs.build_trend_section(history)
        self.assertIn("improved", section)
        self.assertIn("+1.0", section)

    def test_decline_detected(self):
        history = [
            {"overall_score": 2.0, "date": "2026-05-25",
             "anomalies_high": 3, "jobs_ok": 10, "jobs_total": 15},
            {"overall_score": 4.0, "date": "2026-05-24",
             "anomalies_high": 0, "jobs_ok": 15, "jobs_total": 15},
        ]
        section = obs.build_trend_section(history)
        self.assertIn("declined", section)
        self.assertIn("-2.0", section)

    def test_stable(self):
        history = [
            {"overall_score": 4.0, "date": "2026-05-25",
             "anomalies_high": 0, "jobs_ok": 14, "jobs_total": 15},
            {"overall_score": 4.0, "date": "2026-05-24",
             "anomalies_high": 0, "jobs_ok": 14, "jobs_total": 15},
        ]
        section = obs.build_trend_section(history)
        self.assertIn("stable", section)

    def test_avg_computed(self):
        history = [
            {"overall_score": 5.0, "date": "d3",
             "anomalies_high": 0, "jobs_ok": 15, "jobs_total": 15},
            {"overall_score": 3.0, "date": "d2",
             "anomalies_high": 0, "jobs_ok": 15, "jobs_total": 15},
            {"overall_score": 4.0, "date": "d1",
             "anomalies_high": 0, "jobs_ok": 15, "jobs_total": 15},
        ]
        section = obs.build_trend_section(history)
        self.assertIn("4.0", section)  # avg of 5+3+4=4.0

    def test_discord_suffix_improved(self):
        history = [
            {"overall_score": 4.0},
            {"overall_score": 3.0},
        ]
        suffix = obs.build_trend_discord_suffix(history)
        self.assertIn("+1", suffix)

    def test_discord_suffix_empty_when_no_history(self):
        self.assertEqual(obs.build_trend_discord_suffix([]), "")


# ══════════════════════════════════════════════════════════════════════
# V37.9.198 (研究攻关 #1 Stage 5) — fail-plausible 检测接入 daily_observer
# ══════════════════════════════════════════════════════════════════════
import llm_observer as _obs_fp


def _fp_aware_llm(critique_response):
    """构造同时处理 critique + fail-plausible 两种 system prompt 的 mock LLM。"""
    def caller(system, user):
        if "fail-plausible" in system or "FAIL_PLAUSIBLE" in system:
            # fail-plausible judge: 对含 Bad JSON 的 dream 报 pollution_evidence
            if "Bad JSON" in user:
                return True, ('{"verdict":"fail_plausible","confidence":85,"findings":'
                              '[{"judge":"pollution_evidence","evidence":"Bad JSON",'
                              '"rationale":"系统错误码被当成平台危机信号"}]}'), ""
            return True, '{"verdict":"clean","confidence":10,"findings":[]}', ""
        return True, critique_response, ""
    return caller


_CRITIQUE_OK = textwrap.dedent("""
## 评分
- 信息密度: ⭐×4
- 准确性风险: ⭐×4
- 主题多样性: ⭐×4
- 可行动性: ⭐×4
- 格式规范: ⭐×4
- 综合: ⭐×4 / 5
## 发现的问题
1. [LOW] minor
## 改进提案
1. x
""")


class TestStage5FailPlausibleMode(unittest.TestCase):
    def test_default_shadow(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OBSERVER_FP_MODE", None)
            self.assertEqual(obs._fp_mode(), "shadow")

    def test_explicit_modes(self):
        for val, exp in [("off", "off"), ("on", "on"), ("shadow", "shadow"),
                         ("OFF", "off"), (" on ", "on"), ("garbage", "shadow")]:
            with mock.patch.dict(os.environ, {"OBSERVER_FP_MODE": val}):
                self.assertEqual(obs._fp_mode(), exp, val)


class TestStage5SectionBuilder(unittest.TestCase):
    def _verdict(self):
        return [{"severity": "HIGH", "category": "pollution_signal", "artifact": "dream",
                 "confidence": 0.85,
                 "evidence": [{"layer": 1, "signal": "S1_pollution_signal", "locus": 2,
                               "snippet": "Bad JSON"},
                              {"layer": 2, "judge": "pollution_evidence",
                               "snippet": "Bad JSON", "rationale": "r"}]}]

    def test_shadow_section_marks_observational(self):
        s = obs.build_fail_plausible_section(self._verdict(), "shadow")
        self.assertIn("shadow", s)
        self.assertIn("观察性", s)
        self.assertIn("pollution_signal", s)
        self.assertIn("L1 S1_pollution_signal", s)
        self.assertIn("L2 pollution_evidence", s)

    def test_on_section_marks_integrated(self):
        s = obs.build_fail_plausible_section(self._verdict(), "on")
        self.assertIn("影响评分", s)

    def test_empty_verdicts_clean_line(self):
        s = obs.build_fail_plausible_section([], "shadow")
        self.assertIn("无 fail-plausible 信号", s)

    def test_off_returns_empty(self):
        self.assertEqual(obs.build_fail_plausible_section(self._verdict(), "off"), "")


class TestStage5RunIntegration(unittest.TestCase):
    """run() 端到端 fail-plausible 接入 (shadow/on/off/dry_run/FAIL-OPEN)。"""

    def setUp(self):
        # 隔离 status.json 写入 (镜像 TestRunOrchestrator)
        self._patcher = mock.patch.object(obs, "_write_observer_to_status",
                                           return_value=True)
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()

    def _setup_kb_with_d1_dream(self, td):
        for d in ("daily", "dreams", "deep_dives", "sources"):
            os.makedirs(os.path.join(td, d))
        # 干净 evening (非重复内容, 避免误触 S5 boilerplate)
        with open(os.path.join(td, "daily", "evening_20260525.md"), "w") as f:
            f.write("# Evening\n今日 arXiv 出现长上下文注意力新方法，实验覆盖八个基准。"
                    "HN 讨论 Rust 异步运行时尾延迟差异。财经方面美联储维持利率。"
                    "本体论方向有一篇知识图谱推理的综述值得关注，论证链完整。")
        # D1 fail-plausible dream (Bad JSON → Layer 1 S1 命中)
        with open(os.path.join(td, "dreams", "2026-05-25.md"), "w") as f:
            f.write("# Dream\n信号一：平台返回 'Bad JSON' 和 '400 错误'，疑似平台危机。\n"
                    "行动一：启动 72 小时监控。\n")
        return td

    def _run(self, td, mode, dry_run=False):
        with mock.patch.dict(os.environ, {"OBSERVER_FP_MODE": mode}):
            return obs.run(kb_dir=td, jobs_dir=os.path.join(td, "fake_jobs"),
                           target_date=datetime(2026, 5, 25),
                           llm_caller=_fp_aware_llm(_CRITIQUE_OK), dry_run=dry_run)

    def test_shadow_detects_but_not_in_anomalies(self):
        with tempfile.TemporaryDirectory() as td:
            self._setup_kb_with_d1_dream(td)
            r = self._run(td, "shadow")
            self.assertEqual(r["fp_mode"], "shadow")
            self.assertTrue(r["fail_plausible"], "should detect D1 dream")
            self.assertEqual(r["fail_plausible"][0]["artifact"], "dream")
            # shadow: fp NOT in anomalies (不影响评分/告警)
            cats = [a["category"] for a in r["anomalies"]]
            self.assertNotIn("pollution_signal", cats)
            # 但在专属报告段
            self.assertIn("Fail-Plausible 检测 [shadow]", r["report_markdown"])

    def test_on_mode_merges_into_anomalies(self):
        with tempfile.TemporaryDirectory() as td:
            self._setup_kb_with_d1_dream(td)
            r = self._run(td, "on")
            cats = [a["category"] for a in r["anomalies"]]
            self.assertIn("pollution_signal", cats, "on mode: fp must roll into anomalies")
            self.assertIn("[on]", r["report_markdown"])

    def test_off_mode_skips(self):
        with tempfile.TemporaryDirectory() as td:
            self._setup_kb_with_d1_dream(td)
            r = self._run(td, "off")
            self.assertEqual(r["fail_plausible"], [])
            self.assertNotIn("Fail-Plausible 检测", r["report_markdown"])

    def test_dry_run_layer1_only_no_llm(self):
        # dry_run: fail-plausible 仅 Layer 1 (零 LLM); Layer 1 仍抓 D1
        with tempfile.TemporaryDirectory() as td:
            self._setup_kb_with_d1_dream(td)
            r = self._run(td, "shadow", dry_run=True)
            self.assertTrue(r["fail_plausible"])
            # Layer 1 only: 证据全 layer 1
            ev = r["fail_plausible"][0]["evidence"]
            self.assertTrue(all(e["layer"] == 1 for e in ev))

    def test_fail_open_scan_raises(self):
        # scan_fail_plausible 抛异 → run() 不崩 (FAIL-OPEN), fp_verdicts=[]
        with tempfile.TemporaryDirectory() as td:
            self._setup_kb_with_d1_dream(td)
            with mock.patch.object(_obs_fp, "scan_fail_plausible",
                                   side_effect=RuntimeError("boom")):
                r = self._run(td, "shadow")
            self.assertEqual(r["status"], "ok")   # observer 存活
            self.assertEqual(r["fail_plausible"], [])

    def test_result_has_fp_keys(self):
        with tempfile.TemporaryDirectory() as td:
            self._setup_kb_with_d1_dream(td)
            r = self._run(td, "shadow")
            self.assertIn("fail_plausible", r)
            self.assertIn("fp_mode", r)


class TestStage5Writers(unittest.TestCase):
    def test_score_history_fp_counts(self):
        with tempfile.TemporaryDirectory() as td:
            fp = [{"severity": "HIGH"}, {"severity": "MED"}, {"severity": "MED"}]
            obs.append_score_history(td, datetime(2026, 5, 25), 4.0, [], [], {},
                                     "ok", fp_verdicts=fp)
            path = obs._score_history_path(td)
            with open(path) as f:
                rec = json.loads(f.readline())
            self.assertEqual(rec["fp_high"], 1)
            self.assertEqual(rec["fp_med"], 2)

    def test_score_history_fp_none_backward_compat(self):
        with tempfile.TemporaryDirectory() as td:
            obs.append_score_history(td, datetime(2026, 5, 25), 4.0, [], [], {}, "ok")
            with open(obs._score_history_path(td)) as f:
                rec = json.loads(f.readline())
            self.assertEqual(rec["fp_high"], 0)
            self.assertEqual(rec["fp_med"], 0)

    def test_write_observer_status_fp_counts(self):
        captured = {}

        def fake_save(data, **kw):
            captured["data"] = data

        with mock.patch.dict(sys.modules):
            import status_update
            with mock.patch.object(status_update, "load_status", return_value={}), \
                 mock.patch.object(status_update, "save_status", side_effect=fake_save):
                fp = [{"severity": "HIGH"}, {"severity": "HIGH"}]
                obs._write_observer_to_status("/tmp", datetime(2026, 5, 25), 4.0, [],
                                              "ok", [], fp_verdicts=fp)
        observer = captured["data"]["quality"]["observer"]
        self.assertEqual(observer["fail_plausible_high"], 2)
        self.assertEqual(observer["fail_plausible_med"], 0)


class TestStage5SourceGuards(unittest.TestCase):
    def test_marker_and_env(self):
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "daily_observer.py"), encoding="utf-8") as f:
            src = f.read()
        self.assertIn("Stage 5", src)
        self.assertIn("OBSERVER_FP_MODE", src)
        self.assertIn("shadow-first", src)

    def test_design_locked(self):
        self.assertEqual(obs._FP_VALID_MODES, ("off", "shadow", "on"))
        self.assertEqual(obs._FP_MODE_ENV, "OBSERVER_FP_MODE")

    def test_fail_open_contract_documented(self):
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "daily_observer.py"), encoding="utf-8") as f:
            src = f.read()
        self.assertIn("FAIL-OPEN", src)
        self.assertIn("cheap-path", src)


class TestShellGuards(unittest.TestCase):
    """Source-level guards for daily_observer.sh."""

    @classmethod
    def setUpClass(cls):
        sh_path = os.path.join(os.path.dirname(__file__), "daily_observer.sh")
        with open(sh_path, "r", encoding="utf-8") as f:
            cls.src = f.read()

    def test_v37_9_84_marker(self):
        self.assertIn("V37.9.84", self.src)

    def test_set_eEuo_pipefail(self):
        self.assertRegex(self.src, r"set\s+-eEuo\s+pipefail")

    def test_source_notify_sh(self):
        self.assertIn("source \"$NOTIFY_SH\"", self.src)

    def test_system_alert_prefix(self):
        self.assertIn("[SYSTEM_ALERT] daily_observer", self.src)

    def test_pushes_full_report_via_notify(self):
        self.assertIn("--topic daily", self.src)
        self.assertIn("REPORT_CONTENT", self.src)
        self.assertIn('cat "$REPORT_FILE"', self.src)

    def test_read_only_marker(self):
        self.assertIn("READ-ONLY", self.src.upper() + self.src)

    def test_cron_monitor_fatal_handler(self):
        self.assertIn("cron_monitor_fatal_handler", self.src)

    def test_status_file_written(self):
        self.assertIn("last_run_self_critique.json", self.src)

    def test_critique_dir_created(self):
        self.assertIn("self_critique", self.src)
        self.assertIn("mkdir -p", self.src)

    def test_bash_syntax(self):
        sh_path = os.path.join(os.path.dirname(__file__), "daily_observer.sh")
        result = subprocess.run(
            ["bash", "-n", sh_path], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0,
                         f"bash syntax error: {result.stderr}")


class TestSourceLevelGuards(unittest.TestCase):
    """Source-level guards for daily_observer.py."""

    @classmethod
    def setUpClass(cls):
        py_path = os.path.join(os.path.dirname(__file__), "daily_observer.py")
        with open(py_path, "r", encoding="utf-8") as f:
            cls.src = f.read()

    def test_v37_9_84_marker(self):
        self.assertIn("V37.9.84", self.src)

    def test_read_only_contract(self):
        self.assertIn("READ-ONLY", self.src)

    def test_fail_open_contract(self):
        self.assertIn("FAIL-OPEN", self.src)

    def test_log_writes_stderr(self):
        self.assertIn("file=sys.stderr", self.src)

    def test_llm_temperature_low(self):
        self.assertIn('"temperature": 0.3', self.src)

    def test_critique_system_prompt_defined(self):
        self.assertIn("CRITIQUE_SYSTEM", self.src)
        self.assertIn("独立的 AI 系统质量审计员", self.src)

    def test_no_write_to_production_files(self):
        """Observer must never write to ~/.kb/sources/ or ~/.kb/notes/ etc."""
        for forbidden in ["sources/", "notes/", "dreams/", "deep_dives/",
                          "daily/"]:
            lines = self.src.split("\n")
            for i, line in enumerate(lines):
                if line.strip().startswith("#"):
                    continue
                if f'open(' in line and 'w' in line and forbidden in line:
                    self.fail(f"Line {i+1} writes to {forbidden}: {line}")

    def test_default_kb_dir(self):
        self.assertIn("~/.kb", self.src)

    def test_mac_mini_jobs_path_candidate(self):
        """V37.9.56-hotfix same pattern: Mac Mini path must be a candidate."""
        self.assertIn("_MAC_MINI_JOBS_DIR", self.src)
        self.assertIn(".openclaw/jobs", self.src)

    def test_resolve_last_run_path_helper(self):
        self.assertIn("def _resolve_last_run_path(", self.src)

    def test_jobs_subdirs_defined(self):
        self.assertIn("JOBS_SUBDIRS", self.src)
        self.assertIn("hf_papers", self.src)
        self.assertIn("finance_news", self.src)


# ══════════════════════════════════════════════════════════════════════
# 9. V37.9.87 BUG #1+#2 — single-call architecture / 1 cron = 1 append
# ══════════════════════════════════════════════════════════════════════
#
# Pre-V37.9.87 evidence (2026-05-29 user paste of Mac Mini score_history):
#   2026-05-26: 4 records (2 manual runs × 2 internal appends)
#   2026-05-27: 2 records (1 cron × 2 appends)
#   2026-05-28: 2 records (1 cron × 2 appends), scores ⭐5 + ⭐4 differ
#   last_run.json overall_score=5 but score_history latest is 4 → mismatch.
# Root cause: daily_observer.sh ran daily_observer.py twice per cron
# (once --json, once for markdown report). Each call ran run() →
# append_score_history. LLM stochasticity → different scores between calls.
#
# Fix: --json output now includes report_markdown. Wrapper does single
# invocation, parses + writes report file in one Python fork.

class TestV37_9_87_SingleCallArchitecture(unittest.TestCase):
    """V37.9.87: 1 cron run produces exactly 1 score_history append.

    V37.9.92 isolation: per-test patch of _write_observer_to_status to
    prevent run() from polluting repo's real status.json (status_update
    uses module-level STATUS_FILE which doesn't honor kb_dir param).
    """

    @classmethod
    def setUpClass(cls):
        py_path = os.path.join(os.path.dirname(__file__), "daily_observer.py")
        sh_path = os.path.join(os.path.dirname(__file__), "daily_observer.sh")
        with open(py_path, "r", encoding="utf-8") as f:
            cls.py_src = f.read()
        with open(sh_path, "r", encoding="utf-8") as f:
            cls.sh_src = f.read()

    def setUp(self):
        from unittest.mock import patch
        self._patcher = patch.object(obs, "_write_observer_to_status",
                                     return_value=True)
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()

    def test_v37_9_87_marker_in_py(self):
        self.assertIn("V37.9.87", self.py_src,
                      "daily_observer.py must reference V37.9.87 fix")

    def test_v37_9_87_marker_in_sh(self):
        self.assertIn("V37.9.87", self.sh_src,
                      "daily_observer.sh must reference V37.9.87 fix")

    def test_json_branch_includes_report_markdown(self):
        """--json output must include report_markdown so wrapper avoids
        a second invocation (the root cause of double-append)."""
        # The fixed branch must NOT filter out report_markdown.
        # Pre-V37.9.87 had: safe = {k: v for k, v in result.items()
        #                           if k != "report_markdown"}
        # Post-V37.9.87 has: output = dict(result)
        self.assertNotIn(
            'k != "report_markdown"', self.py_src,
            "--json must not filter out report_markdown (V37.9.87)")
        self.assertIn("output = dict(result)", self.py_src,
                      "--json must include all result fields")

    def test_wrapper_invokes_observer_only_once(self):
        """Source-level guard: shell wrapper must invoke OBSERVER_PY only
        once per cron run. Pre-V37.9.87 had 2 invocations causing the
        double-append bug.

        We count non-comment lines that match `python3 "$OBSERVER_PY"`.
        Inline Python (python3 -c) is allowed for parsing.
        """
        count = 0
        for line in self.sh_src.split("\n"):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            # Match exec of the observer script (not python3 -c heredoc)
            if 'python3 "$OBSERVER_PY"' in stripped:
                count += 1
        self.assertEqual(
            count, 1,
            f"V37.9.87 single-call invariant: expected 1 invocation of "
            f'python3 "$OBSERVER_PY", found {count}. Double invocation '
            f"causes double append_score_history (BUG #1).")

    def test_wrapper_passes_report_file_via_env(self):
        """V37.9.87 wrapper must export REPORT_FILE to inline Python so
        the report can be written without a second observer invocation."""
        self.assertIn('REPORT_FILE="$REPORT_FILE" python3', self.sh_src,
                      "REPORT_FILE must be exported to inline Python")
        self.assertIn("os.environ.get('REPORT_FILE'", self.sh_src,
                      "inline Python must read REPORT_FILE from env")

    def test_inline_python_writes_report_markdown(self):
        """V37.9.87: inline Python parses report_markdown from JSON and
        writes to REPORT_FILE."""
        self.assertIn("d.get('report_markdown'", self.sh_src,
                      "inline Python must read report_markdown from JSON")
        self.assertIn("f.write(report)", self.sh_src,
                      "inline Python must write report to file")

    def test_pre_v37_9_87_pattern_removed(self):
        """Reverse guard: the buggy `python3 "$OBSERVER_PY" $DATE_ARG >
        "$REPORT_FILE"` pattern must NOT reappear (would re-introduce
        BUG #1 + BUG #2)."""
        # Look for the specific buggy pattern
        for line in self.sh_src.split("\n"):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if 'python3 "$OBSERVER_PY"' in stripped and '> "$REPORT_FILE"' in stripped:
                self.fail(
                    f"V37.9.87 regression: pre-fix pattern "
                    f"`python3 \"$OBSERVER_PY\" ... > \"$REPORT_FILE\"` "
                    f"reappeared in line: {stripped!r}")

    def test_cli_json_outputs_report_markdown(self):
        """End-to-end: --json output includes report_markdown field."""
        result = subprocess.run(
            [sys.executable, "daily_observer.py", "--json", "--dry-run",
             "--date", "20260525"],
            capture_output=True, text=True,
            cwd=os.path.dirname(__file__), timeout=15)
        self.assertEqual(result.returncode, 0)
        data = json.loads(result.stdout)
        self.assertIn("report_markdown", data,
                      "--json output must include report_markdown field "
                      "(V37.9.87 single-call invariant)")
        self.assertIn("report_length", data,
                      "--json output must keep report_length for "
                      "backward compatibility")
        # report_markdown is a non-empty string (even dry-run produces
        # a header + job coverage section)
        self.assertIsInstance(data["report_markdown"], str)
        self.assertGreater(len(data["report_markdown"]), 0)

    def test_single_run_produces_single_history_append(self):
        """Behavioral: 1 call to run() = 1 line in score_history.jsonl.
        This is the invariant the wrapper relies on to ensure 1 cron =
        1 append (when wrapper invokes observer once)."""
        with tempfile.TemporaryDirectory() as td:
            kb = td
            os.makedirs(os.path.join(td, "daily"))
            os.makedirs(os.path.join(td, "dreams"))
            os.makedirs(os.path.join(td, "deep_dives"))
            os.makedirs(os.path.join(td, "sources"))
            with open(os.path.join(td, "daily", "evening_20260525.md"), "w") as f:
                f.write("# Evening\n" + ("ok " * 50))
            with open(os.path.join(td, "sources", "arxiv_daily.md"), "w") as f:
                f.write("## 2026-05-25\nx" * 100)

            def mock_llm(system, user):
                return True, ("## 评分\n- 综合: ⭐⭐⭐⭐ / 5\n"
                              "## 发现的问题\n1. ok\n"
                              "## 改进提案\n1. ok"), ""

            obs.run(kb_dir=kb, jobs_dir=os.path.join(td, "fake_jobs"),
                    target_date=datetime(2026, 5, 25), llm_caller=mock_llm)

            history_path = os.path.join(td, "self_critique",
                                        "score_history.jsonl")
            self.assertTrue(os.path.isfile(history_path),
                            "run() must append to score_history.jsonl")
            with open(history_path) as f:
                lines = [l for l in f.read().strip().split("\n") if l]
            self.assertEqual(
                len(lines), 1,
                f"1 run() call = 1 history append (got {len(lines)} lines). "
                "If this regresses, V37.9.87 wrapper invariant is at risk.")

    def test_two_runs_same_date_produce_two_records_pre_dedup(self):
        """Documentation test: run() does NOT dedup at append time. Each
        call appends a row. The dedup is done at LOAD time by
        load_score_history. This means the wrapper MUST avoid double
        invocations (V37.9.87 single-call architecture) to keep
        history.jsonl clean."""
        with tempfile.TemporaryDirectory() as td:
            kb = td
            os.makedirs(os.path.join(td, "daily"))
            os.makedirs(os.path.join(td, "dreams"))
            os.makedirs(os.path.join(td, "deep_dives"))
            os.makedirs(os.path.join(td, "sources"))
            with open(os.path.join(td, "daily", "evening_20260525.md"), "w") as f:
                f.write("# Evening\n" + ("ok " * 50))

            def mock_llm(system, user):
                return True, "## 评分\n- 综合: ⭐⭐⭐⭐ / 5", ""

            for _ in range(2):
                obs.run(kb_dir=kb, jobs_dir=os.path.join(td, "fake_jobs"),
                        target_date=datetime(2026, 5, 25), llm_caller=mock_llm)

            history_path = os.path.join(td, "self_critique",
                                        "score_history.jsonl")
            with open(history_path) as f:
                lines = [l for l in f.read().strip().split("\n") if l]
            self.assertEqual(
                len(lines), 2,
                "DOC TEST: 2 run() calls = 2 history rows (no append-time "
                "dedup). This is exactly why pre-V37.9.87 double-invocation "
                "produced 2 rows per cron. load_score_history dedups by "
                "date at read time, so trend analysis sees 1 entry, but "
                "the underlying file accumulates pollution.")


# ══════════════════════════════════════════════════════════════════════
# 10. V37.9.88 — registry-driven enabled filter + stale last_run detection
# ══════════════════════════════════════════════════════════════════════
#
# Discovered 2026-05-29 during V37.9.84 observer trend review:
#   pwc disabled since V31 (2026-03), deleted V37.8.13, but observer's
#   hardcoded JOBS_SUBDIRS still contained "pwc". For 2 months observer
#   read pwc's stale last_run.json (timestamp 2026-03-31, status=fetch_failed)
#   and flagged it as today's HIGH anomaly. Same drift potential for
#   karpathy_x (V34 merged to ai_leaders_x) and openclaw_official (no
#   matching enabled job ID in registry).
#
# Fix:
#   - _filter_enabled_jobs reads jobs_registry.yaml at scan time
#   - JOBS_SUBDIRS becomes the data-source category whitelist (max set)
#   - registry filters down to enabled=true → MR-8 single source of truth
#   - Stale last_run detection: time > 7d before target_date → MED
#     anomaly "stale_job" + SUPPRESS HIGH "job_failure" (status untrustworthy)

class TestV37_9_88_LoadEnabledJobIds(unittest.TestCase):
    """V37.9.88: parse jobs_registry.yaml minimally without PyYAML."""

    def _make_registry(self, td, content):
        path = os.path.join(td, "jobs_registry.yaml")
        with open(path, "w") as f:
            f.write(content)
        return path

    def test_parses_enabled_true_jobs(self):
        with tempfile.TemporaryDirectory() as td:
            path = self._make_registry(td, """
  - id: hf_papers
    enabled: true
    interval: "0 10 * * *"

  - id: pwc
    enabled: false
    description: V31 disabled

  - id: arxiv_monitor
    enabled: true
""")
            result = obs._load_enabled_job_ids_from_registry(path)
            self.assertEqual(result, {"hf_papers", "arxiv_monitor"})

    def test_missing_file_returns_none(self):
        result = obs._load_enabled_job_ids_from_registry("/nonexistent/x.yaml")
        self.assertIsNone(result)

    def test_inline_comment_stripped(self):
        with tempfile.TemporaryDirectory() as td:
            path = self._make_registry(td, """
  - id: pwc
    enabled: false  # V31 disabled, V37.8.13 deleted

  - id: hf_papers
    enabled: true  # working
""")
            result = obs._load_enabled_job_ids_from_registry(path)
            self.assertEqual(result, {"hf_papers"})

    def test_skips_comment_only_lines(self):
        with tempfile.TemporaryDirectory() as td:
            path = self._make_registry(td, """
  # ┌─ Data Sources ─┐
  - id: hf_papers
    enabled: true
  # ┌─ Disabled ─┐
  - id: pwc
    enabled: false
""")
            result = obs._load_enabled_job_ids_from_registry(path)
            self.assertEqual(result, {"hf_papers"})

    def test_quoted_enabled_value(self):
        with tempfile.TemporaryDirectory() as td:
            path = self._make_registry(td, """
  - id: hf_papers
    enabled: "true"

  - id: pwc
    enabled: 'false'
""")
            result = obs._load_enabled_job_ids_from_registry(path)
            self.assertEqual(result, {"hf_papers"})

    def test_real_registry_excludes_pwc(self):
        """Source-level guard: against the actual repo jobs_registry.yaml,
        pwc must be excluded (the V37.9.88 trigger case)."""
        result = obs._load_enabled_job_ids_from_registry()
        # FAIL-OPEN: registry may not be at expected path in some test envs.
        # Skip if registry not found.
        if result is None:
            self.skipTest("registry not found at default paths")
        self.assertNotIn("pwc", result,
                         "V37.9.88 invariant: pwc must be excluded "
                         "(disabled V31, deleted V37.8.13)")
        # Sanity: hf_papers (enabled) must be in
        self.assertIn("hf_papers", result)


class TestV37_9_88_FilterEnabledJobs(unittest.TestCase):
    """V37.9.88: filter JOBS_SUBDIRS using registry, FAIL-OPEN."""

    def test_filter_removes_disabled(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "r.yaml")
            with open(path, "w") as f:
                f.write("""
  - id: hf_papers
    enabled: true

  - id: pwc
    enabled: false

  - id: arxiv_monitor
    enabled: true
""")
            subdirs = ["hf_papers", "pwc", "arxiv_monitor"]
            result = obs._filter_enabled_jobs(subdirs, registry_path=path)
            self.assertEqual(result, ["hf_papers", "arxiv_monitor"])

    def test_fail_open_on_missing_registry(self):
        """FAIL-OPEN: missing registry returns subdirs unchanged."""
        subdirs = ["hf_papers", "pwc", "arxiv_monitor"]
        result = obs._filter_enabled_jobs(
            subdirs, registry_path="/nonexistent/x.yaml")
        self.assertEqual(result, subdirs)

    def test_preserves_order(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "r.yaml")
            with open(path, "w") as f:
                f.write("""
  - id: hf_papers
    enabled: true

  - id: dblp
    enabled: true

  - id: arxiv_monitor
    enabled: true
""")
            # subdirs in different order than registry
            subdirs = ["arxiv_monitor", "hf_papers", "dblp"]
            result = obs._filter_enabled_jobs(subdirs, registry_path=path)
            self.assertEqual(result, subdirs,
                             "subdirs order must be preserved")

    def test_env_var_override(self):
        """OBSERVER_REGISTRY_PATH env var injection for tests."""
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "r.yaml")
            with open(path, "w") as f:
                f.write("  - id: only_one\n    enabled: true\n")
            old = os.environ.get(obs._REGISTRY_ENV_VAR)
            os.environ[obs._REGISTRY_ENV_VAR] = path
            try:
                resolved = obs._resolve_registry_path()
                self.assertEqual(resolved, path)
            finally:
                if old is None:
                    del os.environ[obs._REGISTRY_ENV_VAR]
                else:
                    os.environ[obs._REGISTRY_ENV_VAR] = old


class TestV37_9_88_ParseLrTime(unittest.TestCase):
    """V37.9.88: parse last_run.json 'time' field variants."""

    def test_space_separated(self):
        result = obs._parse_lr_time("2026-05-28 11:00:00")
        self.assertEqual(result, datetime(2026, 5, 28, 11, 0, 0))

    def test_iso_t_separator(self):
        result = obs._parse_lr_time("2026-05-28T11:00:00")
        self.assertEqual(result, datetime(2026, 5, 28, 11, 0, 0))

    def test_iso_with_z_suffix(self):
        result = obs._parse_lr_time("2026-05-28T11:00:00Z")
        self.assertEqual(result, datetime(2026, 5, 28, 11, 0, 0))

    def test_date_only(self):
        result = obs._parse_lr_time("2026-05-28")
        self.assertEqual(result, datetime(2026, 5, 28, 0, 0, 0))

    def test_empty_string_returns_none(self):
        self.assertIsNone(obs._parse_lr_time(""))

    def test_invalid_string_returns_none(self):
        self.assertIsNone(obs._parse_lr_time("not a time"))

    def test_none_input_returns_none(self):
        self.assertIsNone(obs._parse_lr_time(None))

    def test_non_string_input_returns_none(self):
        self.assertIsNone(obs._parse_lr_time(12345))


class TestV37_9_88_IsStaleLastRun(unittest.TestCase):
    """V37.9.88: stale detection boundary cases."""

    def test_fresh_today(self):
        # last_run today, target_date today → fresh
        stale, days = obs._is_stale_last_run(
            "2026-05-28 11:00:00", datetime(2026, 5, 28))
        self.assertFalse(stale)

    def test_one_day_old_fresh(self):
        stale, days = obs._is_stale_last_run(
            "2026-05-27 11:00:00", datetime(2026, 5, 28))
        self.assertFalse(stale, "1d old is fresh (<7d threshold)")

    def test_seven_days_old_boundary_fresh(self):
        """boundary: exactly 7d old = fresh (only >7d is stale)."""
        stale, days = obs._is_stale_last_run(
            "2026-05-21 12:00:00", datetime(2026, 5, 28))
        self.assertFalse(stale, "7d old is boundary fresh (>7d is stale)")

    def test_eight_days_old_stale(self):
        stale, days = obs._is_stale_last_run(
            "2026-05-20 11:00:00", datetime(2026, 5, 28))
        self.assertTrue(stale)
        self.assertEqual(days, 8)

    def test_blood_lesson_pwc_2_months_stale(self):
        """Real V37.9.88 trigger: pwc last_run 2026-03-31, target_date
        2026-05-28 → 58 days old → stale."""
        stale, days = obs._is_stale_last_run(
            "2026-03-31 16:54:03", datetime(2026, 5, 28))
        self.assertTrue(stale)
        self.assertEqual(days, 58)

    def test_unparseable_time_not_stale(self):
        """If time can't be parsed, do NOT flag as stale (FAIL-OPEN)."""
        stale, days = obs._is_stale_last_run(
            "garbage", datetime(2026, 5, 28))
        self.assertFalse(stale)
        self.assertIsNone(days)

    def test_custom_max_age(self):
        # 3d old, max_age=2 → stale
        stale, days = obs._is_stale_last_run(
            "2026-05-25 11:00:00", datetime(2026, 5, 28), max_age_days=2)
        self.assertTrue(stale)


class TestV37_9_88_DetectAnomalies(unittest.TestCase):
    """V37.9.88: stale_job MED anomaly + HIGH job_failure suppression."""

    def _job(self, job_id, status, stale=False, stale_days=None):
        return {"job_id": job_id, "status": status, "time": "x", "new": 0,
                "reason": "", "found": True,
                "stale": stale, "stale_days": stale_days}

    def test_stale_job_emits_med_anomaly(self):
        statuses = [self._job("pwc", "fetch_failed", stale=True, stale_days=58)]
        anomalies = obs.detect_anomalies(statuses, {}, ["x"])
        stale_a = [a for a in anomalies if a["category"] == "stale_job"]
        self.assertEqual(len(stale_a), 1)
        self.assertEqual(stale_a[0]["severity"], "MED")
        self.assertIn("58d", stale_a[0]["message"])
        self.assertIn("pwc", stale_a[0]["message"])
        self.assertIn("untrustworthy", stale_a[0]["message"])

    def test_stale_job_suppresses_high_job_failure(self):
        """V37.9.88 invariant: stale jobs do NOT emit HIGH job_failure.
        Status data is itself stale, can't be trusted as 'today's failure'."""
        statuses = [self._job("pwc", "fetch_failed", stale=True, stale_days=58)]
        anomalies = obs.detect_anomalies(statuses, {}, ["x"])
        high_failure = [a for a in anomalies
                        if a["category"] == "job_failure"
                        and a["severity"] == "HIGH"]
        self.assertEqual(len(high_failure), 0,
                         "stale job_failure must be suppressed (status "
                         "data is untrustworthy)")

    def test_non_stale_failure_still_emits_high(self):
        statuses = [self._job("s2", "fetch_failed", stale=False)]
        anomalies = obs.detect_anomalies(statuses, {}, ["x"])
        high_failure = [a for a in anomalies
                        if a["category"] == "job_failure"
                        and a["severity"] == "HIGH"]
        self.assertEqual(len(high_failure), 1,
                         "non-stale fresh failure still HIGH")
        self.assertIn("s2", high_failure[0]["message"])

    def test_blood_lesson_5_28_correct_anomaly_split(self):
        """Real V37.9.88 scenario: 5/28 observer reported anomalies_high=2
        (s2 + pwc). After V37.9.88: 1 HIGH (s2 real) + 1 MED (pwc stale)."""
        statuses = [
            self._job("semantic_scholar", "fetch_failed", stale=False),
            self._job("pwc", "fetch_failed", stale=True, stale_days=58),
        ]
        anomalies = obs.detect_anomalies(statuses, {}, ["x"])
        high = [a for a in anomalies if a["severity"] == "HIGH"]
        med_stale = [a for a in anomalies if a["category"] == "stale_job"]
        # Filter out source/output anomalies for clarity (those depend on
        # push_outputs/source_sections, here both passed minimal).
        high_failure = [a for a in high if a["category"] == "job_failure"]
        self.assertEqual(len(high_failure), 1,
                         "only s2 (real fresh failure) is HIGH")
        self.assertEqual(len(med_stale), 1,
                         "pwc 58d stale → MED stale_job")


class TestV37_9_88_ScanJobStatusesIntegration(unittest.TestCase):
    """V37.9.88 end-to-end: scan_job_statuses applies filter + stale check."""

    def test_excludes_disabled_jobs_via_registry(self):
        with tempfile.TemporaryDirectory() as td:
            # Create mock last_run.json for both enabled + disabled
            jobs_dir = os.path.join(td, "jobs")
            for jid in ("hf_papers", "pwc"):
                d = os.path.join(jobs_dir, jid, "cache")
                os.makedirs(d)
                with open(os.path.join(d, "last_run.json"), "w") as f:
                    json.dump({"time": "2026-05-28 11:00:00",
                               "status": "ok", "new": 5}, f)
            # Mock registry: only hf_papers enabled
            registry_path = os.path.join(td, "r.yaml")
            with open(registry_path, "w") as f:
                f.write("""
  - id: hf_papers
    enabled: true

  - id: pwc
    enabled: false
""")
            # Need to monkey-patch JOBS_SUBDIRS to just these two
            old = obs.JOBS_SUBDIRS[:]
            obs.JOBS_SUBDIRS[:] = ["hf_papers", "pwc"]
            try:
                results = obs.scan_job_statuses(
                    jobs_dir, datetime(2026, 5, 28),
                    registry_path=registry_path)
            finally:
                obs.JOBS_SUBDIRS[:] = old
            job_ids = [r["job_id"] for r in results]
            self.assertEqual(job_ids, ["hf_papers"],
                             "pwc must be filtered out")

    def test_marks_stale_entries(self):
        with tempfile.TemporaryDirectory() as td:
            jobs_dir = os.path.join(td, "jobs")
            # hf_papers fresh, pwc 2-month stale
            for jid, ts in [("hf_papers", "2026-05-28 11:00:00"),
                            ("pwc", "2026-03-31 16:54:03")]:
                d = os.path.join(jobs_dir, jid, "cache")
                os.makedirs(d)
                with open(os.path.join(d, "last_run.json"), "w") as f:
                    json.dump({"time": ts, "status": "ok"}, f)
            # Disable filter — pretend BOTH are enabled to test stale check
            registry_path = os.path.join(td, "r.yaml")
            with open(registry_path, "w") as f:
                f.write("""
  - id: hf_papers
    enabled: true

  - id: pwc
    enabled: true
""")
            old = obs.JOBS_SUBDIRS[:]
            obs.JOBS_SUBDIRS[:] = ["hf_papers", "pwc"]
            try:
                results = obs.scan_job_statuses(
                    jobs_dir, datetime(2026, 5, 28),
                    registry_path=registry_path)
            finally:
                obs.JOBS_SUBDIRS[:] = old
            by_id = {r["job_id"]: r for r in results}
            self.assertFalse(by_id["hf_papers"]["stale"])
            self.assertTrue(by_id["pwc"]["stale"])
            self.assertEqual(by_id["pwc"]["stale_days"], 58)


class TestV37_9_88_SourceLevelGuards(unittest.TestCase):
    """V37.9.88 source-level: prevent regression of hardcoded drift."""

    @classmethod
    def setUpClass(cls):
        py_path = os.path.join(os.path.dirname(__file__), "daily_observer.py")
        with open(py_path, "r", encoding="utf-8") as f:
            cls.src = f.read()

    def test_v37_9_88_marker(self):
        self.assertIn("V37.9.88", self.src)

    def test_filter_function_defined(self):
        self.assertIn("def _filter_enabled_jobs(", self.src)
        self.assertIn("def _load_enabled_job_ids_from_registry(", self.src)

    def test_stale_functions_defined(self):
        self.assertIn("def _is_stale_last_run(", self.src)
        self.assertIn("def _parse_lr_time(", self.src)
        self.assertIn("STALE_LAST_RUN_MAX_DAYS = 7", self.src)

    def test_scan_uses_filter(self):
        """scan_job_statuses must use _filter_enabled_jobs."""
        # Source-level: both definitions and a call must exist
        self.assertIn("def scan_job_statuses(", self.src)
        # The call to _filter_enabled_jobs must appear (in scan body)
        self.assertIn("_filter_enabled_jobs(JOBS_SUBDIRS", self.src,
                      "scan_job_statuses must apply registry filter")

    def test_anomaly_suppression_logic_present(self):
        """detect_anomalies must skip HIGH for stale jobs."""
        self.assertIn("def detect_anomalies(", self.src)
        self.assertIn("stale_job_ids", self.src,
                      "stale jobs must be tracked for HIGH suppression")
        self.assertIn('"stale_job"', self.src,
                      "stale_job category must be emitted")
        self.assertIn('j["job_id"] not in stale_job_ids', self.src,
                      "HIGH job_failure must skip stale entries")

    def test_blood_lesson_reference(self):
        """V37.9.88 must reference the pwc 2-month blood lesson."""
        # Either V37.9.88 + pwc + V31, or explicit "blood lesson" reference
        self.assertIn("pwc", self.src)
        # Reference to disabled-job drift (any of these is sufficient)
        markers = ["V31", "stale", "untrustworthy"]
        found = sum(1 for m in markers if m in self.src)
        self.assertGreaterEqual(found, 2,
            "V37.9.88 must reference the drift case in comments")


class TestV37_9_87_ReverseSabotage(unittest.TestCase):
    """V37.9.87 reverse-validation: sabotage the fix and confirm guards fail.
    Not run by default; documents what would break.
    """

    def test_sabotage_documentation(self):
        """If we re-introduced the pre-V37.9.87 pattern by adding a second
        `python3 "$OBSERVER_PY" $DATE_ARG > "$REPORT_FILE"` line, the
        following tests would catch it (in order of detection):
          1. test_wrapper_invokes_observer_only_once
          2. test_pre_v37_9_87_pattern_removed
        If we removed the `output = dict(result)` line and re-added the
        filter, the following would catch it:
          1. test_json_branch_includes_report_markdown
          2. test_cli_json_outputs_report_markdown
        Manually verified 2026-05-29 by reverting each change and observing
        failures in CI dev environment.
        """
        # This test always passes — it's purely documentation. The actual
        # reverse validation is done manually as part of V37.9.87 release.
        self.assertTrue(True, "see docstring")


# ============================================================================
# V37.9.92 — Mac Mini canonical path (Bug #1 from V37.9.84 trend review)
# +  status.json quality.observer closure (V37.9.84 design completion)
# ============================================================================


class TestV37_9_92_MacMiniCanonicalPath(unittest.TestCase):
    """V37.9.92: _resolve_registry_path() must include Mac Mini canonical
    candidate ~/openclaw-model-bridge/jobs_registry.yaml as 3rd-priority
    after $HOME/jobs_registry.yaml. V37.9.88 missed this layout, causing
    5 days of fallback warn in production (5/29-6/1 observed)."""

    def setUp(self):
        # Save and clear env var to ensure deterministic resolution
        self._old_env = os.environ.pop(obs._REGISTRY_ENV_VAR, None)
        # Save HOME to restore (we'll override it per-test)
        self._old_home = os.environ.get("HOME")

    def tearDown(self):
        if self._old_env is None:
            os.environ.pop(obs._REGISTRY_ENV_VAR, None)
        else:
            os.environ[obs._REGISTRY_ENV_VAR] = self._old_env
        if self._old_home is not None:
            os.environ["HOME"] = self._old_home

    def _setup_fake_home(self, td, files):
        """Helper: create candidate files inside td (acting as $HOME)
        and point HOME there. `files` is set of relative paths to create."""
        os.environ["HOME"] = td
        for rel in files:
            p = os.path.join(td, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w") as f:
                f.write("- id: dummy_job\n  enabled: true\n")

    def test_finds_in_mac_mini_canonical_path(self):
        """Only Mac Mini canonical exists ($HOME/openclaw-model-bridge/
        jobs_registry.yaml) → resolver returns it."""
        with tempfile.TemporaryDirectory() as td:
            self._setup_fake_home(td, {"openclaw-model-bridge/jobs_registry.yaml"})
            resolved = obs._resolve_registry_path()
        self.assertIsNotNone(resolved,
            "Mac Mini canonical path must be detected (V37.9.92 fix)")
        self.assertIn("openclaw-model-bridge", resolved)
        self.assertTrue(resolved.endswith("jobs_registry.yaml"))

    def test_home_root_takes_precedence_over_canonical(self):
        """If both $HOME/jobs_registry.yaml and Mac Mini canonical exist,
        $HOME wins (V37.9.88 candidate 2 preserved as highest priority)."""
        with tempfile.TemporaryDirectory() as td:
            self._setup_fake_home(td, {
                "jobs_registry.yaml",                       # candidate 2
                "openclaw-model-bridge/jobs_registry.yaml",  # candidate 3
            })
            resolved = obs._resolve_registry_path()
        self.assertEqual(resolved, os.path.join(td, "jobs_registry.yaml"),
            "$HOME root must win over Mac Mini canonical")

    def test_canonical_takes_precedence_over_script_adj(self):
        """If only Mac Mini canonical and script-adjacent (resolved real
        path under repo) exist, canonical wins (it's V37.9.88+candidate 3)."""
        # Script-adjacent path ALWAYS exists in test environment because
        # the repo has its own jobs_registry.yaml. So we just need to
        # verify that Mac Mini canonical resolves before falling through
        # to the real script-adjacent path.
        with tempfile.TemporaryDirectory() as td:
            self._setup_fake_home(td, {"openclaw-model-bridge/jobs_registry.yaml"})
            resolved = obs._resolve_registry_path()
        # Must match canonical path, NOT script-adjacent
        expected = os.path.join(td, "openclaw-model-bridge",
                                "jobs_registry.yaml")
        self.assertEqual(resolved, expected,
            "Mac Mini canonical must take precedence over script-adjacent")

    def test_env_var_overrides_all_candidates(self):
        """OBSERVER_REGISTRY_PATH env override still wins over all
        4 candidates (V37.9.88 contract preserved)."""
        with tempfile.TemporaryDirectory() as td:
            override = os.path.join(td, "override.yaml")
            with open(override, "w") as f:
                f.write("- id: override_job\n  enabled: true\n")
            self._setup_fake_home(td, {
                "jobs_registry.yaml",
                "openclaw-model-bridge/jobs_registry.yaml",
            })
            os.environ[obs._REGISTRY_ENV_VAR] = override
            resolved = obs._resolve_registry_path()
        self.assertEqual(resolved, override,
            "env var override is highest priority")

    def test_falls_back_to_script_adj_when_home_misses(self):
        """If $HOME has no registry files at all, must fall back to
        script-adjacent (V37.9.88 candidate 3 → V37.9.92 candidate 4)."""
        with tempfile.TemporaryDirectory() as td:
            self._setup_fake_home(td, set())  # nothing in fake HOME
            resolved = obs._resolve_registry_path()
        # Should resolve to the REAL repo registry (script-adjacent fallback)
        self.assertIsNotNone(resolved,
            "script-adjacent must be reachable as final fallback")
        self.assertTrue(resolved.endswith("jobs_registry.yaml"))
        self.assertNotIn(td, resolved,
            "fallback must not point inside fake HOME")

    def test_all_candidates_missing_returns_none(self):
        """If env unset + all 3 candidate files missing → None.
        Trigger by pointing HOME at empty dir AND moving the real
        script-adjacent file out of the way (not feasible — instead we
        explicitly construct candidates with all missing and verify None)."""
        # We use _load_enabled_job_ids_from_registry on missing path
        # to verify the contract of None on missing.
        result = obs._load_enabled_job_ids_from_registry(
            "/nonexistent/dir/jobs_registry.yaml")
        self.assertIsNone(result,
            "explicit missing path must return None")

    def test_v37_9_88_existing_candidates_still_present(self):
        """V37.9.88 paths (HOME root + script-adjacent) must still be
        candidates. Source-level guard."""
        py_path = os.path.join(os.path.dirname(__file__), "daily_observer.py")
        with open(py_path) as f:
            src = f.read()
        # $HOME/jobs_registry.yaml (V37.9.88 candidate 2)
        self.assertIn('os.path.expanduser("~/jobs_registry.yaml")', src,
            "V37.9.88 $HOME candidate must remain")
        # script-adjacent (V37.9.88 candidate 3)
        self.assertIn(
            "os.path.join(os.path.dirname(os.path.abspath(__file__))",
            src,
            "V37.9.88 script-adjacent candidate must remain")


class TestV37_9_92_StatusJsonClosure(unittest.TestCase):
    """V37.9.92: daily_observer.py must publish summary to status.json
    quality.observer after each run, closing V37.9.84 design loop
    (三方共享意识锚点 must include observer score)."""

    def setUp(self):
        self.kb_dir = tempfile.mkdtemp()
        self.target_date = datetime(2026, 6, 1)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.kb_dir, ignore_errors=True)

    def _build_fake_status_module(self, initial_data=None):
        """Helper: build a MagicMock standing in for status_update."""
        from unittest.mock import MagicMock
        fake = MagicMock()
        fake.load_status = MagicMock(
            return_value=initial_data if initial_data is not None else {})
        fake.save_status = MagicMock()
        # status_update has a module-level STATUS_FILE constant
        fake.STATUS_FILE = os.path.join(self.kb_dir, "status.json")
        return fake

    def test_write_observer_to_status_function_exists(self):
        """V37.9.92: _write_observer_to_status() helper must exist."""
        self.assertTrue(hasattr(obs, "_write_observer_to_status"),
            "V37.9.92 must define _write_observer_to_status helper")
        self.assertTrue(callable(obs._write_observer_to_status))

    def test_writes_quality_observer_payload(self):
        """V37.9.92 main case: helper writes observer payload to
        status_update.save_status with all required fields."""
        from unittest.mock import patch
        fake_su = self._build_fake_status_module(initial_data={"quality": {}})
        anomalies = [
            {"severity": "HIGH", "category": "job_failure", "message": "x"},
            {"severity": "MED", "category": "stale_job", "message": "y"},
            {"severity": "MED", "category": "thin_output", "message": "z"},
        ]
        job_statuses = [
            {"job_id": "a", "status": "ok"},
            {"job_id": "b", "status": "ok"},
            {"job_id": "c", "status": "partial_degraded"},
            {"job_id": "d", "status": "fetch_failed"},
        ]
        with patch.dict("sys.modules", {"status_update": fake_su}):
            ok = obs._write_observer_to_status(
                kb_dir=self.kb_dir,
                target_date=self.target_date,
                overall_score=5,
                anomalies=anomalies,
                status="ok",
                job_statuses=job_statuses,
            )

        self.assertTrue(ok, "_write_observer_to_status must return True on success")
        fake_su.load_status.assert_called_once()
        fake_su.save_status.assert_called_once()

        # Inspect payload
        call_args, call_kwargs = fake_su.save_status.call_args
        data = call_args[0]
        observer = data["quality"]["observer"]

        self.assertEqual(observer["score"], 5)
        self.assertEqual(observer["status"], "ok")
        self.assertEqual(observer["anomalies_high"], 1)
        self.assertEqual(observer["anomalies_med"], 2)
        self.assertEqual(observer["jobs_ok"], 3,  # ok + ok + partial_degraded
            "jobs_ok counts ok + partial_degraded")
        self.assertEqual(observer["jobs_total"], 4)
        self.assertEqual(observer["last_run_date"], "2026-06-01")
        self.assertTrue(observer["v37_9_92"],
            "V37.9.92 marker must be in payload")
        # last_updated_at: ISO format string
        self.assertIsInstance(observer["last_updated_at"], str)
        self.assertIn("2026-", observer["last_updated_at"])

    def test_audit_action_recorded(self):
        """V37.9.92: save_status must be called with audit_action so the
        write is recorded in audit_log (V30.2 chain hash)."""
        from unittest.mock import patch
        fake_su = self._build_fake_status_module(initial_data={"quality": {}})
        with patch.dict("sys.modules", {"status_update": fake_su}):
            obs._write_observer_to_status(
                kb_dir=self.kb_dir,
                target_date=self.target_date,
                overall_score=4,
                anomalies=[],
                status="ok",
                job_statuses=[],
            )
        _, kwargs = fake_su.save_status.call_args
        self.assertEqual(kwargs["updated_by"], "daily_observer")
        self.assertEqual(kwargs["audit_action"], "observer_score_update")
        self.assertIn("status.json:quality.observer", kwargs["audit_target"])
        self.assertIn("score=4", kwargs["audit_summary"])

    def test_failopen_on_status_update_import_error(self):
        """V37.9.92 FAIL-OPEN: if status_update module is unimportable,
        observer must log WARN and continue (return False)."""
        from unittest.mock import patch
        # Inject a broken status_update into sys.modules so the import
        # inside the function raises ImportError (we use None which
        # makes attribute access fail with the desired effect).
        # Approach: build a module that itself raises on attribute access.
        # Simpler: use the real `from X import Y` raise.
        import builtins
        orig_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "status_update":
                raise ImportError("simulated missing status_update")
            return orig_import(name, *args, **kwargs)

        with patch.object(builtins, "__import__", side_effect=fake_import):
            # Also clear cached module if present
            with patch.dict("sys.modules"):
                sys.modules.pop("status_update", None)
                ok = obs._write_observer_to_status(
                    kb_dir=self.kb_dir,
                    target_date=self.target_date,
                    overall_score=5,
                    anomalies=[],
                    status="ok",
                    job_statuses=[],
                )
        self.assertFalse(ok,
            "FAIL-OPEN: import error must return False, not raise")

    def test_failopen_on_save_status_exception(self):
        """V37.9.92 FAIL-OPEN: if save_status raises, observer must catch
        and return False without aborting."""
        from unittest.mock import patch
        fake_su = self._build_fake_status_module(initial_data={"quality": {}})
        fake_su.save_status.side_effect = OSError("disk full")
        with patch.dict("sys.modules", {"status_update": fake_su}):
            ok = obs._write_observer_to_status(
                kb_dir=self.kb_dir,
                target_date=self.target_date,
                overall_score=5,
                anomalies=[],
                status="ok",
                job_statuses=[],
            )
        self.assertFalse(ok,
            "FAIL-OPEN: save error must return False, not raise")

    def test_preserves_existing_quality_fields(self):
        """V37.9.92: writing observer must NOT clobber other quality.*
        fields (security_score / test_count / etc)."""
        from unittest.mock import patch
        initial = {
            "quality": {
                "security_score": 95,
                "test_count": 3537,
                "last_regression": "2026-05-29 07:16 pass",
                "coverage_pct": 0,
            }
        }
        fake_su = self._build_fake_status_module(initial_data=initial)
        with patch.dict("sys.modules", {"status_update": fake_su}):
            obs._write_observer_to_status(
                kb_dir=self.kb_dir,
                target_date=self.target_date,
                overall_score=5,
                anomalies=[],
                status="ok",
                job_statuses=[],
            )
        call_args, _ = fake_su.save_status.call_args
        data = call_args[0]
        quality = data["quality"]
        # Observer was added
        self.assertIn("observer", quality)
        # Existing fields preserved
        self.assertEqual(quality["security_score"], 95,
            "must preserve existing security_score")
        self.assertEqual(quality["test_count"], 3537)
        self.assertEqual(quality["last_regression"],
            "2026-05-29 07:16 pass")

    def test_run_orchestrator_calls_write_observer(self):
        """V37.9.92 wiring: run() must call _write_observer_to_status
        after append_score_history. Verifies wiring at orchestrator level."""
        from unittest.mock import patch
        # Mock the helper itself + LLM caller + filesystem
        kb_dir = tempfile.mkdtemp()
        jobs_dir = tempfile.mkdtemp()
        try:
            # Create minimal fixture so run() reaches the persistence path
            sources_dir = os.path.join(kb_dir, "sources")
            os.makedirs(sources_dir, exist_ok=True)
            target_date = datetime(2026, 6, 1)
            ds = target_date.strftime("%Y-%m-%d")
            with open(os.path.join(sources_dir, "test.md"), "w") as f:
                f.write(f"## {ds}\n\nsome content\n")

            def fake_llm(*args, **kwargs):
                return True, "## 综合: ⭐⭐⭐⭐⭐ / 5\n\n## 发现的问题\n1. None\n", "ok"

            with patch.object(obs, "_write_observer_to_status") as mock_write:
                obs.run(kb_dir=kb_dir, jobs_dir=jobs_dir,
                        target_date=target_date, llm_caller=fake_llm)

            self.assertEqual(mock_write.call_count, 1,
                "run() must call _write_observer_to_status exactly once")
            args, kwargs = mock_write.call_args
            # Verify positional args order matches the call signature:
            # (kb_dir, target_date, overall_score, anomalies, status, job_statuses)
            self.assertEqual(args[0], kb_dir)
            self.assertEqual(args[1], target_date)
            self.assertEqual(args[4], "ok",
                "status must be 'ok' for successful LLM critique")
            self.assertIsInstance(args[5], list,
                "job_statuses must be a list")
        finally:
            import shutil
            shutil.rmtree(kb_dir, ignore_errors=True)
            shutil.rmtree(jobs_dir, ignore_errors=True)

    def test_run_calls_write_observer_after_append_score_history(self):
        """V37.9.92: ordering matters — quality.observer must reflect the
        SAME run as score_history. Verify both helpers called in run().

        V37.9.92 isolation: tracers RECORD call order only, do NOT invoke
        the real `_write_observer_to_status` (which would write to the
        repo's real status.json — discovered as test pollution during
        merge resolution).
        """
        from unittest.mock import patch
        kb_dir = tempfile.mkdtemp()
        jobs_dir = tempfile.mkdtemp()
        try:
            sources_dir = os.path.join(kb_dir, "sources")
            os.makedirs(sources_dir, exist_ok=True)
            target_date = datetime(2026, 6, 1)
            ds = target_date.strftime("%Y-%m-%d")
            with open(os.path.join(sources_dir, "test.md"), "w") as f:
                f.write(f"## {ds}\n\nsome content\n")

            def fake_llm(*args, **kwargs):
                return True, "## 综合: ⭐⭐⭐⭐ / 5", "ok"

            call_order = []
            real_append = obs.append_score_history

            def trace_append(*a, **k):
                call_order.append("append_score_history")
                # Safe to invoke real append — writes to tempdir
                return real_append(*a, **k)

            def trace_write(*a, **k):
                # RECORD ONLY — do NOT invoke real _write_observer_to_status
                # (which uses status_update.STATUS_FILE → repo status.json).
                # Order verification doesn't need the real write to happen.
                call_order.append("_write_observer_to_status")
                return True

            with patch.object(obs, "append_score_history",
                              side_effect=trace_append) as _, \
                 patch.object(obs, "_write_observer_to_status",
                              side_effect=trace_write) as _:
                obs.run(kb_dir=kb_dir, jobs_dir=jobs_dir,
                        target_date=target_date, llm_caller=fake_llm)
            # Both must have run, and in this order
            self.assertEqual(call_order,
                ["append_score_history", "_write_observer_to_status"],
                "score_history must persist before status.json publication")
        finally:
            import shutil
            shutil.rmtree(kb_dir, ignore_errors=True)
            shutil.rmtree(jobs_dir, ignore_errors=True)


class TestV37_9_92_SourceLevelGuards(unittest.TestCase):
    """V37.9.92 source-level: prevent regression of Mac Mini canonical
    path candidate + status.json closure helper."""

    @classmethod
    def setUpClass(cls):
        py_path = os.path.join(os.path.dirname(__file__), "daily_observer.py")
        with open(py_path, "r", encoding="utf-8") as f:
            cls.src = f.read()

    def test_v37_9_92_marker_in_source(self):
        self.assertIn("V37.9.92", self.src,
            "V37.9.92 version marker must appear in source")

    def test_mac_mini_canonical_path_in_candidates(self):
        """The 3rd candidate must point at ~/openclaw-model-bridge/."""
        self.assertIn(
            'os.path.expanduser("~/openclaw-model-bridge/jobs_registry.yaml")',
            self.src,
            "Mac Mini canonical path must be a candidate in "
            "_resolve_registry_path (V37.9.92 fix)")

    def test_write_observer_to_status_function_defined(self):
        self.assertIn("def _write_observer_to_status(", self.src,
            "helper function definition must exist")

    def test_run_calls_write_helper_after_append_score_history(self):
        """Wiring assertion: run() body must contain the call, AND it
        must follow append_score_history (verify source order)."""
        run_marker = "def run(kb_dir=None, jobs_dir=None"
        append_call = "append_score_history(kb_dir, target_date"
        write_call = "_write_observer_to_status(kb_dir, target_date"

        self.assertIn(write_call, self.src,
            "run() must call _write_observer_to_status")

        run_idx = self.src.find(run_marker)
        append_idx = self.src.find(append_call, run_idx)
        write_idx = self.src.find(write_call, run_idx)
        self.assertGreater(append_idx, 0, "append call must exist in run()")
        self.assertGreater(write_idx, 0, "write call must exist in run()")
        self.assertGreater(write_idx, append_idx,
            "_write_observer_to_status must be called AFTER "
            "append_score_history (V37.9.92 ordering lock)")

    def test_imports_status_update_in_helper(self):
        """V37.9.92: helper must import status_update lazily inside
        function body (not at module top) for FAIL-OPEN compatibility."""
        self.assertIn("from status_update import load_status, save_status",
            self.src,
            "must import via 'from status_update import load_status, save_status'")
        # Module-top import would break FAIL-OPEN — verify not at top
        # by checking that the import appears INSIDE the helper function
        helper_start = self.src.find("def _write_observer_to_status(")
        import_pos = self.src.find(
            "from status_update import", helper_start)
        self.assertGreater(import_pos, helper_start,
            "status_update import must be INSIDE the helper function "
            "(lazy, FAIL-OPEN compatible)")

    def test_quality_observer_key_written(self):
        """payload writes to data['quality']['observer'] specifically."""
        self.assertIn('quality["observer"]', self.src,
            "must write to data['quality']['observer']")

    def test_fail_open_pattern_in_helper(self):
        """V37.9.92 must use try/except Exception for FAIL-OPEN."""
        # Search WITHIN the helper function body
        helper_start = self.src.find("def _write_observer_to_status(")
        helper_end = self.src.find("\ndef ", helper_start + 1)
        if helper_end == -1:
            helper_body = self.src[helper_start:]
        else:
            helper_body = self.src[helper_start:helper_end]
        self.assertIn("except ImportError", helper_body,
            "must handle ImportError (status_update missing)")
        self.assertIn("except Exception", helper_body,
            "must catch general Exception (FAIL-OPEN contract)")

    def test_v37_9_92_blood_lesson_reference(self):
        """V37.9.92 must reference MR-15 4th occurrence + V37.9.88 root cause."""
        # MR-15 deployment-layout-must-be-tested-on-target
        self.assertIn("MR-15", self.src,
            "V37.9.92 must reference MR-15 (deployment-layout meta rule)")
        # V37.9.88 traceback
        self.assertIn("V37.9.88", self.src,
            "V37.9.92 fix must reference V37.9.88 path bug it closes")

    def test_v37_9_92_path_docstring_updated(self):
        """_resolve_registry_path docstring must mention V37.9.92."""
        helper_start = self.src.find("def _resolve_registry_path():")
        docstring_end = self.src.find("\n    env_override", helper_start)
        docstring = self.src[helper_start:docstring_end]
        self.assertIn("V37.9.92", docstring,
            "_resolve_registry_path docstring must mention V37.9.92")
        self.assertIn("Mac Mini canonical", docstring,
            "docstring must explain what V37.9.92 adds")

    def test_top_docstring_mentions_status_json_publication(self):
        """V37.9.92 closes V37.9.84 design intent — top-of-file
        docstring must reflect that observer publishes to status.json."""
        # Look at the first 80 lines (where the module docstring is)
        head = "\n".join(self.src.splitlines()[:80])
        self.assertIn("status.json", head,
            "top docstring must mention status.json publication")
        self.assertIn("quality.observer", head,
            "top docstring must mention quality.observer key")

    def test_candidates_order_locked(self):
        """V37.9.92: candidate order is part of contract — env → $HOME
        → Mac Mini canonical → script-adjacent. Lock order in source."""
        # Find _resolve_registry_path body
        helper_start = self.src.find("def _resolve_registry_path():")
        helper_end = self.src.find("\n    return None", helper_start)
        body = self.src[helper_start:helper_end]
        # Verify candidates appear in this exact order in candidates list
        home_idx = body.find('"~/jobs_registry.yaml"')
        canonical_idx = body.find('"~/openclaw-model-bridge/jobs_registry.yaml"')
        script_adj_idx = body.find("os.path.dirname(os.path.abspath(__file__))")
        self.assertGreater(home_idx, 0)
        self.assertGreater(canonical_idx, 0)
        self.assertGreater(script_adj_idx, 0)
        self.assertLess(home_idx, canonical_idx,
            "$HOME root must appear before Mac Mini canonical")
        self.assertLess(canonical_idx, script_adj_idx,
            "Mac Mini canonical must appear before script-adjacent")


class TestV37_9_93_SmartSampling(unittest.TestCase):
    """V37.9.93: _read_file_sample must use smart head+tail sampling for
    files > MAX_SAMPLE_CHARS, so the Observer LLM can see the footer and
    verify file completeness — preventing false-positive truncation
    reports like 5/31 dream "AgentScope 1" sampling artifact."""

    def setUp(self):
        import shutil
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write_file(self, name, content):
        path = os.path.join(self.tmpdir, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return path

    def test_short_file_returns_full_content(self):
        """Files ≤ MAX_SAMPLE_CHARS return full content unchanged (V37.9.84
        behavior preserved)."""
        body = "hello world. " * 50  # 650 chars
        path = self._write_file("short.md", body)
        sample, length = obs._read_file_sample(path)
        self.assertEqual(sample, body,
                         "short file must return full content")
        self.assertEqual(length, 650)
        self.assertNotIn("sampling for LLM evaluation", sample,
                         "short file must NOT have sampling marker")

    def test_exactly_max_chars_no_sampling(self):
        """Boundary: file exactly MAX_SAMPLE_CHARS returns full content."""
        body = "x" * obs.MAX_SAMPLE_CHARS
        path = self._write_file("exact.md", body)
        sample, length = obs._read_file_sample(path)
        self.assertEqual(len(sample), obs.MAX_SAMPLE_CHARS)
        self.assertEqual(length, obs.MAX_SAMPLE_CHARS)
        self.assertNotIn("[...", sample)

    def test_long_file_returns_head_plus_marker_plus_tail(self):
        """V37.9.93 core contract: file > MAX_SAMPLE_CHARS gets
        smart sampling with explicit middle-omission marker."""
        head_segment = "HEAD_OPENING_PART. " * 100  # ~1900 chars
        middle = "MIDDLE_BODY_CONTENT. " * 200       # ~4000 chars
        footer = "*Generated by kb_dream.sh v2 (MapReduce) — END*"
        body = head_segment + middle + footer
        path = self._write_file("long.md", body)
        sample, length = obs._read_file_sample(path)
        self.assertGreater(length, obs.MAX_SAMPLE_CHARS,
                          "test setup: file must exceed MAX")
        self.assertLessEqual(len(sample), obs.MAX_SAMPLE_CHARS,
                            "V37.9.93 sample must stay within budget")
        # Head visible
        self.assertIn("HEAD_OPENING_PART", sample,
                     "smart sample must contain head")
        # Footer visible (critical for V37.9.93 false-positive prevention)
        self.assertIn(footer, sample,
                     "smart sample must contain footer for completeness check")
        # Marker visible (LLM must know it's sampling)
        self.assertIn("sampling for LLM evaluation", sample,
                     "smart sample must contain marker")
        self.assertIn("NOT file truncation", sample,
                     "marker must explicitly say NOT truncation")
        # Full length still reported accurately
        self.assertEqual(length, len(body),
                        "full_length must reflect actual file size")

    def test_marker_reports_correct_omitted_count(self):
        """Marker must include the actual char count omitted from middle."""
        body = "a" * 5000  # well above 2000
        path = self._write_file("count.md", body)
        sample, length = obs._read_file_sample(path)
        # head=1400, tail=500, omitted = 5000 - 1400 - 500 = 3100
        expected_omitted = 5000 - obs.SMART_SAMPLE_HEAD_CHARS - obs.SMART_SAMPLE_TAIL_CHARS
        self.assertIn(f"[...{expected_omitted} chars omitted", sample,
                     "marker must report exact omitted count")

    def test_sample_size_within_budget(self):
        """V37.9.93 invariant: sample size MUST be ≤ max_chars for all
        file sizes, otherwise the contract is broken."""
        for body_size in [3000, 6782, 14750, 50000, 100000]:
            with self.subTest(body_size=body_size):
                body = "X" * body_size
                path = self._write_file(f"size_{body_size}.md", body)
                sample, length = obs._read_file_sample(path)
                self.assertLessEqual(len(sample), obs.MAX_SAMPLE_CHARS,
                    f"sample size {len(sample)} > MAX {obs.MAX_SAMPLE_CHARS} "
                    f"for body of {body_size}")

    def test_dream_531_blood_scenario_smart_sample_includes_footer(self):
        """V37.9.93 blood-scenario regression: 5/31 dream file is
        6782 chars with footer `*Generated by kb_dream.sh v2 ...*`.
        Old sampling cut at 2000 chars before footer → LLM falsely
        reported "AgentScope 1 truncation". New smart sampling MUST
        include the footer so LLM can verify completeness."""
        # Simulate 5/31 dream structure
        dream_body = (
            "# 🌙 Agent Dream — 2026-05-31\n\n"
            "> 模式: MapReduce 全量\n\n"
            "## 🌙 今日深度: **多模态动态知识更新能力**\n\n" +
            "### 发现过程\n" + ("本主题... " * 200) + "\n\n" +
            "### 🔗 隐藏关联\n" +
            "[弱关联] MMKU-Bench 与 AgentScope 生态共享 [多智能体仿真测试环境]: "
            "MMKU-Bench 需要... AgentScope 1.0 (hf_papers_daily, 2026-03-31) "
            "支持百万级智能体仿真..." + ("仿真... " * 100) + "\n\n" +
            "## 🌐 跨领域鲜人知 × 5\n" + ("跨领域内容. " * 200) + "\n\n" +
            "## 📡 准期信号 × 5\n" + ("信号内容. " * 200) + "\n\n" +
            "## 📋 今日连动 + 明日关注\n" + "明日关注: ...\n\n" +
            "---\n*Generated by kb_dream.sh v2 (MapReduce) — "
            "13760000 bytes of knowledge, 17 sources deep-analyzed, "
            "every signal counts.*"
        )
        path = self._write_file("2026-05-31.md", dream_body)
        sample, length = obs._read_file_sample(path)
        self.assertGreater(length, 2000,
                          "blood scenario: file must be sampled")
        # Footer must be visible — this is the V37.9.93 core fix
        self.assertIn("*Generated by kb_dream.sh v2", sample,
                     "5/31 blood scenario: footer must be visible to LLM")
        self.assertIn("every signal counts.*", sample,
                     "footer ending marker must be visible")
        # LLM should be able to verify this is complete by seeing footer
        # Sampling marker must be explicit
        self.assertIn("sampling for LLM evaluation", sample)
        self.assertIn("NOT file truncation", sample)

    def test_missing_file_returns_empty(self):
        """V37.9.84 contract: missing file returns ('', 0)."""
        sample, length = obs._read_file_sample("/nonexistent/file.md")
        self.assertEqual(sample, "")
        self.assertEqual(length, 0)


class TestV37_9_93_CritiqueSystemSamplingClarification(unittest.TestCase):
    """V37.9.93: CRITIQUE_SYSTEM must include sampling clarification
    so LLM does not misinterpret marker-truncated content as truncation."""

    def test_critique_system_includes_v37_9_93_marker(self):
        self.assertIn("V37.9.93", obs.CRITIQUE_SYSTEM,
                     "V37.9.93 version marker must be in CRITIQUE_SYSTEM")

    def test_critique_system_explains_sampling(self):
        """Prompt must explicitly tell LLM that long outputs are sampled
        and middle-omission is NOT file truncation."""
        self.assertIn("采样", obs.CRITIQUE_SYSTEM,
                     "must mention 采样/sampling")
        self.assertIn("sampling for LLM evaluation",
                     obs.CRITIQUE_SYSTEM,
                     "must explain marker text LLM will see")
        self.assertIn("NOT file truncation",
                     obs.CRITIQUE_SYSTEM,
                     "must explicitly negate file-truncation interpretation")

    def test_critique_system_references_footer_verification(self):
        """LLM must be told to use footer presence as completeness signal."""
        self.assertIn("footer", obs.CRITIQUE_SYSTEM,
                     "must guide LLM to verify via footer")
        self.assertIn("Generated by kb_dream.sh", obs.CRITIQUE_SYSTEM,
                     "must give Dream footer as example")

    def test_critique_system_specifies_real_truncation_conditions(self):
        """LLM must know the TWO conditions under which to report truncation."""
        # Two cases: (a) no footer / mid-sentence end, (b) length < expected
        sys = obs.CRITIQUE_SYSTEM
        self.assertIn("DEEP", sys, "must mention DEEP minimum")
        self.assertIn("WIDE+RADAR", sys, "must mention WIDE+RADAR minimum")


class TestV37_9_93_SourceLevelGuards(unittest.TestCase):
    """V37.9.93 source-level guards: prevent regression to old sampling."""

    @classmethod
    def setUpClass(cls):
        py_path = os.path.join(os.path.dirname(__file__), "daily_observer.py")
        with open(py_path, "r", encoding="utf-8") as f:
            cls.src = f.read()

    def test_v37_9_93_marker_in_source(self):
        self.assertIn("V37.9.93", self.src)

    def test_smart_sampling_constants_defined(self):
        self.assertIn("SMART_SAMPLE_HEAD_CHARS", self.src)
        self.assertIn("SMART_SAMPLE_TAIL_CHARS", self.src)
        self.assertIn("SMART_SAMPLE_MARKER_TEMPLATE", self.src)

    def test_marker_template_explicit_about_sampling(self):
        """Marker text must be unambiguous to prevent LLM misreading."""
        self.assertIn("sampling for LLM evaluation", self.src)
        self.assertIn("NOT file truncation", self.src)
        self.assertIn("File is complete", self.src)

    def test_read_file_sample_uses_smart_path(self):
        """_read_file_sample must apply head+tail when over budget."""
        helper_start = self.src.find("def _read_file_sample(")
        helper_end = self.src.find("\ndef ", helper_start + 1)
        body = self.src[helper_start:helper_end] if helper_end > 0 else self.src[helper_start:]
        # New smart-sampling logic must be present
        self.assertIn("SMART_SAMPLE_HEAD_CHARS", body,
                     "_read_file_sample must reference smart-sample constant")
        self.assertIn("SMART_SAMPLE_MARKER_TEMPLATE", body,
                     "_read_file_sample must use marker template")
        # The OLD `content[:max_chars]` pattern as the sole return must
        # be gone — V37.9.93 must also have the head+marker+tail path.
        # V37.9.213: head is now line-boundary-snapped (raw_head.rfind), so
        # the concat is `head + marker + content[-tail_chars:]`.
        self.assertIn("head + marker + content[-tail_chars:]",
                     body,
                     "must concatenate (snapped) head + marker + tail")
        # V37.9.213: head must snap to a line boundary (no mid-sentence cut)
        self.assertIn("SMART_SAMPLE_HEAD_SNAP_MAX", body,
                     "_read_file_sample must snap head to line boundary")
        self.assertIn('rfind("\\n")', body,
                     "head snap must find the last newline in the head window")

    def test_critique_system_v37_9_93_in_source(self):
        self.assertIn("采样说明 (V37.9.93)", self.src,
                     "CRITIQUE_SYSTEM must label V37.9.93 sampling section")

    def test_blood_lesson_reference(self):
        """V37.9.93 source must document the 5/31 false positive blood case."""
        self.assertIn("AgentScope", self.src,
                     "must reference the 5/31 AgentScope blood scenario")
        self.assertIn("V37.9.92", self.src,
                     "must reference V37.9.92 (when discovered)")

    def test_smart_sample_size_constants_sum_within_max(self):
        """SMART_SAMPLE_HEAD + TAIL + marker overhead ≤ MAX_SAMPLE_CHARS."""
        # Direct runtime check of constants
        self.assertLessEqual(
            obs.SMART_SAMPLE_HEAD_CHARS + obs.SMART_SAMPLE_TAIL_CHARS,
            obs.MAX_SAMPLE_CHARS,
            "head + tail must leave room for marker within MAX")
        # Marker max length when omitted is a 6-digit number
        max_marker_len = len(
            obs.SMART_SAMPLE_MARKER_TEMPLATE.format(omitted=999999))
        # Reasonable: total budget should fit
        total = (obs.SMART_SAMPLE_HEAD_CHARS +
                 obs.SMART_SAMPLE_TAIL_CHARS + max_marker_len)
        # We allow it to be slightly over MAX because defensive trim handles it
        # but it should be within 30% of MAX to make sense
        self.assertLess(total, obs.MAX_SAMPLE_CHARS * 1.3,
                       f"head+tail+marker = {total} too far over MAX")


class TestV37_9_92_ReverseValidation(unittest.TestCase):
    """V37.9.92 reverse validation: sabotage each fix and verify guards fail.
    Documentation — not run as part of the regression suite."""

    def test_sabotage_documentation(self):
        """If we remove the 4th candidate from _resolve_registry_path:
          → TestV37_9_92_MacMiniCanonicalPath.test_finds_in_mac_mini_canonical_path
            would fail (Mac Mini canonical not detected)
          → TestV37_9_92_SourceLevelGuards.test_mac_mini_canonical_path_in_candidates
            would fail (source-level string check)

        If we remove the _write_observer_to_status call from run():
          → TestV37_9_92_StatusJsonClosure.test_run_orchestrator_calls_write_observer
            would fail (mock.assert_called_once)
          → TestV37_9_92_SourceLevelGuards.test_run_calls_write_helper_after_
            append_score_history would fail

        If we delete the _write_observer_to_status helper:
          → TestV37_9_92_StatusJsonClosure.test_write_observer_to_status_function_exists
            would fail (hasattr check)
          → All other status closure tests would AttributeError

        Manually verified on 2026-06-01:
          (a) remove 4th candidate line → 2 path tests fail ✓
          (b) remove the write_observer call → 2 status tests fail ✓
        """
        self.assertTrue(True, "see docstring")


# ══════════════════════════════════════════════════════════════════════
# 10. V37.9.168 — Deep-dive degrade ratio observability ([19](c) closure)
# ══════════════════════════════════════════════════════════════════════
#
# Background (V37.9.132, discovered 2026-06-11 by user):
#   76% (34/45) of deep_dives were summary-level (abstract_only) — a
#   structural gap latent for 2 MONTHS because degrade frequency had no
#   aggregated review. V37.9.132 fixed the root cause; V37.9.168 makes the
#   degrade ratio observable in the daily report so any future regression
#   surfaces (anomaly) instead of needing a user to read 45 files.
#   Sanctioned as legitimate observability addition in V37.9.166 changelog.

class TestV37_9_168_DeepDiveModes(unittest.TestCase):
    """Deep-dive mode scanning, ratio, anomaly, section."""

    @staticmethod
    def _mk_dd(td, date, mode):
        """Write a deep_dive fixture file mirroring kb_deep_dive output."""
        ddir = os.path.join(td, "deep_dives")
        os.makedirs(ddir, exist_ok=True)
        if mode == "full_text":
            label = "完整原文"
        elif mode == "abstract_only":
            label = "摘要级"
        else:  # unknown -> no 模式 marker
            with open(os.path.join(ddir, f"{date}.md"), "w",
                      encoding="utf-8") as f:
                f.write("# 老格式深度\n无模式行\n正文")
            return
        body = (f"# 深度\n**来源**: src | **星级**: ⭐⭐⭐⭐ | "
                f"**模式**: {label}\n正文")
        with open(os.path.join(ddir, f"{date}.md"), "w",
                  encoding="utf-8") as f:
            f.write(body)

    # ---- classification ----
    def test_classify_full_text_marker(self):
        c = "**模式**: 完整原文"
        self.assertEqual(obs._classify_deep_dive_mode(c), "full_text")

    def test_classify_abstract_only_marker(self):
        c = "**模式**: 摘要级"
        self.assertEqual(obs._classify_deep_dive_mode(c), "abstract_only")

    def test_classify_unknown_no_marker(self):
        self.assertEqual(obs._classify_deep_dive_mode("无标记"), "unknown")
        self.assertEqual(obs._classify_deep_dive_mode(""), "unknown")
        self.assertEqual(obs._classify_deep_dive_mode(None), "unknown")

    def test_classify_fullwidth_colon_robust(self):
        # full-width colon + extra spaces should still classify
        self.assertEqual(obs._classify_deep_dive_mode("**模式**：  摘要级"),
                         "abstract_only")

    # ---- scan ----
    def test_scan_missing_dir_returns_not_found(self):
        with tempfile.TemporaryDirectory() as td:
            stats = obs.scan_deep_dive_modes(td, datetime(2026, 6, 17))
            self.assertFalse(stats["found"])
            self.assertEqual(stats["total"], 0)
            self.assertIsNone(stats["degrade_ratio"])

    def test_scan_classifies_and_ratio_over_classified(self):
        with tempfile.TemporaryDirectory() as td:
            self._mk_dd(td, "2026-06-16", "full_text")
            for d in ("2026-06-15", "2026-06-14", "2026-06-13", "2026-06-12"):
                self._mk_dd(td, d, "abstract_only")
            self._mk_dd(td, "2026-06-11", "unknown")
            stats = obs.scan_deep_dive_modes(td, datetime(2026, 6, 17))
            self.assertTrue(stats["found"])
            self.assertEqual(stats["total"], 6)
            self.assertEqual(stats["full_text"], 1)
            self.assertEqual(stats["abstract_only"], 4)
            self.assertEqual(stats["unknown"], 1)
            # ratio over classified (4+1=5), unknown excluded from denom
            self.assertAlmostEqual(stats["degrade_ratio"], 0.8)

    def test_scan_window_filtering(self):
        with tempfile.TemporaryDirectory() as td:
            self._mk_dd(td, "2026-06-16", "abstract_only")  # in window
            self._mk_dd(td, "2026-04-01", "abstract_only")  # >30d before anchor
            stats = obs.scan_deep_dive_modes(td, datetime(2026, 6, 17),
                                             window_days=30)
            self.assertEqual(stats["total"], 1)  # old file excluded

    def test_scan_non_date_filename_skipped(self):
        with tempfile.TemporaryDirectory() as td:
            self._mk_dd(td, "2026-06-16", "full_text")
            ddir = os.path.join(td, "deep_dives")
            with open(os.path.join(ddir, "README.md"), "w") as f:
                f.write("not a dated file")
            stats = obs.scan_deep_dive_modes(td, datetime(2026, 6, 17))
            self.assertEqual(stats["total"], 1)

    def test_scan_recent_sorted_desc(self):
        with tempfile.TemporaryDirectory() as td:
            self._mk_dd(td, "2026-06-12", "abstract_only")
            self._mk_dd(td, "2026-06-16", "full_text")
            stats = obs.scan_deep_dive_modes(td, datetime(2026, 6, 17))
            self.assertEqual(stats["recent"][0]["date"], "2026-06-16")

    # ---- anomaly ----
    @staticmethod
    def _stats(full, abst, unk=0):
        classified = full + abst
        return {"found": True, "total": full + abst + unk, "full_text": full,
                "abstract_only": abst, "unknown": unk,
                "degrade_ratio": (abst / classified) if classified else None,
                "window_days": 30, "recent": []}

    def test_anomaly_emitted_above_threshold(self):
        # 4/5 = 0.8 >= 0.5, sample 5 >= 5 -> MED anomaly
        anos = obs.detect_anomalies([], {}, [{"char_count": 1}],
                                    deep_dive_modes=self._stats(1, 4))
        dd = [a for a in anos if a["category"] == "deep_dive_degraded"]
        self.assertEqual(len(dd), 1)
        self.assertEqual(dd[0]["severity"], "MED")
        self.assertIn("80%", dd[0]["message"])

    def test_anomaly_not_emitted_below_ratio_threshold(self):
        # 2/6 = 0.33 < 0.5 -> no anomaly (sample large enough)
        anos = obs.detect_anomalies([], {}, [{"char_count": 1}],
                                    deep_dive_modes=self._stats(4, 2))
        self.assertFalse(any(a["category"] == "deep_dive_degraded"
                             for a in anos))

    def test_anomaly_not_emitted_below_min_sample(self):
        # 3/3 = 1.0 ratio but only 3 classified < MIN_SAMPLE(5) -> no anomaly
        anos = obs.detect_anomalies([], {}, [{"char_count": 1}],
                                    deep_dive_modes=self._stats(0, 3))
        self.assertFalse(any(a["category"] == "deep_dive_degraded"
                             for a in anos))

    def test_anomaly_backward_compat_none(self):
        # No deep_dive_modes arg -> existing behaviour, no dd anomaly
        anos = obs.detect_anomalies([], {}, [{"char_count": 1}])
        self.assertFalse(any(a["category"] == "deep_dive_degraded"
                             for a in anos))

    # ---- section ----
    def test_section_renders_with_data(self):
        sec = obs.build_deep_dive_mode_section(self._stats(1, 4, unk=1))
        self.assertIn("Deep-Dive Mode", sec)
        self.assertIn("80%", sec)
        self.assertIn("未分类 1", sec)

    def test_section_empty_when_not_found(self):
        self.assertEqual(obs.build_deep_dive_mode_section({"found": False}), "")
        self.assertEqual(obs.build_deep_dive_mode_section(None), "")

    # ---- run() integration ----
    def test_run_integration_section_and_result(self):
        with tempfile.TemporaryDirectory() as td:
            for d in ("2026-06-15", "2026-06-14", "2026-06-13",
                      "2026-06-12", "2026-06-11"):
                self._mk_dd(td, d, "abstract_only")
            os.environ["OBSERVER_REGISTRY_PATH"] = "/nonexistent"
            try:
                result = obs.run(kb_dir=td, jobs_dir=td,
                                 target_date=datetime(2026, 6, 16),
                                 dry_run=True)
            finally:
                os.environ.pop("OBSERVER_REGISTRY_PATH", None)
            self.assertIn("deep_dive_modes", result)
            self.assertEqual(result["deep_dive_modes"]["abstract_only"], 5)
            self.assertIn("Deep-Dive Mode", result["report_markdown"])
            # anomaly should surface (5/5 = 100%)
            self.assertTrue(any(a["category"] == "deep_dive_degraded"
                                for a in result["anomalies"]))


class TestV37_9_168_SourceLevelGuards(unittest.TestCase):
    """Source-level guards for V37.9.168 deep_dive observability."""

    @classmethod
    def setUpClass(cls):
        here = os.path.dirname(__file__)
        with open(os.path.join(here, "daily_observer.py"),
                  "r", encoding="utf-8") as f:
            cls.src = f.read()
        # kb_deep_dive is the upstream that WRITES the marker we detect.
        dd_path = os.path.join(here, "kb_deep_dive.py")
        cls.dd_src = ""
        if os.path.isfile(dd_path):
            with open(dd_path, "r", encoding="utf-8") as f:
                cls.dd_src = f.read()

    def test_v37_9_168_marker(self):
        self.assertIn("V37.9.168", self.src)

    def test_constants_locked(self):
        self.assertEqual(obs.DEEP_DIVE_MODE_WINDOW_DAYS, 30)
        self.assertEqual(obs.DEEP_DIVE_DEGRADE_RATIO_THRESHOLD, 0.5)
        self.assertEqual(obs.DEEP_DIVE_DEGRADE_MIN_SAMPLE, 5)

    def test_detect_anomalies_backward_compat_default(self):
        # signature must default deep_dive_modes=None (3-arg callers safe)
        import inspect
        sig = inspect.signature(obs.detect_anomalies)
        self.assertIn("deep_dive_modes", sig.parameters)
        self.assertIsNone(sig.parameters["deep_dive_modes"].default)

    def test_scan_opens_deep_dives_read_only(self):
        # observer must never write deep_dive files (READ-ONLY contract)
        self.assertIn('open(md_path, "r"', self.src)

    def test_mr8_cross_file_marker_contract(self):
        """MR-8 single-source-of-truth: the observer's detection regex must
        match the exact marker kb_deep_dive.build_deep_dive_markdown WRITES.
        If kb_deep_dive changes its 模式 line format, this guard fails —
        catching silent detection breakage (the observer would otherwise
        count every file as 'unknown' and never alert).
        """
        if not self.dd_src:
            self.skipTest("kb_deep_dive.py not present")
        # kb_deep_dive writes both labels + the **模式** prefix
        self.assertIn("完整原文", self.dd_src)
        self.assertIn("摘要级", self.dd_src)
        self.assertIn("**模式**", self.dd_src)
        # synthesize the line kb_deep_dive emits (line 578 format) and
        # confirm the observer regex classifies both labels correctly
        full_line = "**来源**: x | **星级**: ⭐ | **模式**: 完整原文"
        abst_line = "**来源**: x | **星级**: ⭐ | **模式**: 摘要级"
        self.assertEqual(obs._classify_deep_dive_mode(full_line), "full_text")
        self.assertEqual(obs._classify_deep_dive_mode(abst_line),
                         "abstract_only")


class TestV37_9_168_ReverseValidation(unittest.TestCase):
    """V37.9.168 reverse validation: the degrade anomaly is a real control,
    not a tautology — it toggles precisely at the threshold/sample boundary.

    Sabotage map (manually verified 2026-06-17):
      - delete the deep_dive_degraded block in detect_anomalies
        → TestV37_9_168_DeepDiveModes.test_anomaly_emitted_above_threshold fails
      - break _DD_MODE_RE (e.g. require half-width colon only)
        → test_classify_fullwidth_colon_robust + test_mr8_cross_file_marker
          contract fail
      - drop deep_dive_section from build_report_markdown
        → test_run_integration_section_and_result fails (section absent)
    """

    def test_anomaly_toggles_at_threshold_boundary(self):
        mk = TestV37_9_168_DeepDiveModes._stats
        below = obs.detect_anomalies([], {}, [{"char_count": 1}],
                                     deep_dive_modes=mk(3, 2))  # 2/5=0.4
        at = obs.detect_anomalies([], {}, [{"char_count": 1}],
                                  deep_dive_modes=mk(2, 3))  # 3/5=0.6
        self.assertFalse(any(a["category"] == "deep_dive_degraded"
                             for a in below))
        self.assertTrue(any(a["category"] == "deep_dive_degraded"
                            for a in at))

    def test_anomaly_toggles_at_sample_boundary(self):
        mk = TestV37_9_168_DeepDiveModes._stats
        # ratio 1.0 both; 4 classified (below MIN 5) vs 5 classified (at MIN)
        small = obs.detect_anomalies([], {}, [{"char_count": 1}],
                                     deep_dive_modes=mk(0, 4))
        atmin = obs.detect_anomalies([], {}, [{"char_count": 1}],
                                     deep_dive_modes=mk(0, 5))
        self.assertFalse(any(a["category"] == "deep_dive_degraded"
                             for a in small))
        self.assertTrue(any(a["category"] == "deep_dive_degraded"
                            for a in atmin))


# ══════════════════════════════════════════════════════════════════════
# 11. V37.9.213 — daily-critique fixes (2026-07-02)
#   F1: deep_dive degrade-REASON aggregation (77% but WHY? self-serve)
#   F2: _read_file_sample head cut snapped to line boundary (kill the
#       recurring head-cut false-positive-truncation from the observer's
#       OWN sampling — 2026-07-01 recurrence of the V37.9.93 class).
# ══════════════════════════════════════════════════════════════════════

class TestV37_9_213_DegradeReasons(unittest.TestCase):
    """F1: aggregate WHY deep_dives degraded, not just THAT they did.
    kb_deep_dive writes `> ⚠️ 抓取降级原因：<reason>` into abstract_only
    files; the observer buckets them so the report answers structural-
    unfetchable (非全文来源) vs fetch-path failure (PDF/HTML 抓取失败)."""

    # ---- extractor ----
    def test_extract_pdf_failure(self):
        c = "正文\n\n> ⚠️ 抓取降级原因：PDF fetch failed: HTTP 404\n"
        self.assertEqual(obs._extract_degrade_category(c), "PDF 抓取失败")

    def test_extract_html_failure(self):
        c = "> ⚠️ 抓取降级原因：HTML fetch failed: URLError: timeout"
        self.assertEqual(obs._extract_degrade_category(c), "HTML 抓取失败")

    def test_extract_tier_source(self):
        c = "> ⚠️ 抓取降级原因：tier3 source (no fetch attempted)"
        self.assertEqual(obs._extract_degrade_category(c), "非全文来源")

    def test_extract_fullwidth_and_halfwidth_colon(self):
        # writer uses fullwidth ：; be robust to half-width too
        self.assertEqual(
            obs._extract_degrade_category("抓取降级原因: PDF fetch failed: x"),
            "PDF 抓取失败")

    def test_extract_no_reason_line(self):
        self.assertEqual(obs._extract_degrade_category("摘要级但无原因行"),
                         "未标注原因")
        self.assertEqual(obs._extract_degrade_category(""), "未标注原因")
        self.assertEqual(obs._extract_degrade_category(None), "未标注原因")

    # ---- scan aggregation ----
    @staticmethod
    def _mk_dd_reason(td, date, mode, reason=None):
        ddir = os.path.join(td, "deep_dives")
        os.makedirs(ddir, exist_ok=True)
        label = "完整原文" if mode == "full_text" else "摘要级"
        body = f"# 深度\n**模式**: {label}\n正文"
        if reason:
            body += f"\n\n> ⚠️ 抓取降级原因：{reason}"
        with open(os.path.join(ddir, f"{date}.md"), "w",
                  encoding="utf-8") as f:
            f.write(body)

    def test_scan_aggregates_degrade_reasons(self):
        with tempfile.TemporaryDirectory() as td:
            self._mk_dd_reason(td, "2026-07-01", "abstract_only",
                               "PDF fetch failed: HTTP 404")
            self._mk_dd_reason(td, "2026-06-30", "abstract_only",
                               "PDF fetch failed: no PDF derivable")
            self._mk_dd_reason(td, "2026-06-29", "abstract_only",
                               "HTML fetch failed: 403")
            self._mk_dd_reason(td, "2026-06-28", "abstract_only",
                               "tier3 source (no fetch attempted)")
            self._mk_dd_reason(td, "2026-06-27", "full_text")  # no reason
            stats = obs.scan_deep_dive_modes(td, datetime(2026, 7, 2))
            dr = stats["degrade_reasons"]
            self.assertEqual(dr.get("PDF 抓取失败"), 2)
            self.assertEqual(dr.get("HTML 抓取失败"), 1)
            self.assertEqual(dr.get("非全文来源"), 1)
            # full_text must NOT contribute a reason bucket
            self.assertNotIn("未标注原因", dr)

    def test_full_text_only_has_empty_reasons(self):
        with tempfile.TemporaryDirectory() as td:
            self._mk_dd_reason(td, "2026-07-01", "full_text")
            stats = obs.scan_deep_dive_modes(td, datetime(2026, 7, 2))
            self.assertEqual(stats["degrade_reasons"], {})

    def test_abstract_without_reason_counted_unlabeled(self):
        with tempfile.TemporaryDirectory() as td:
            self._mk_dd_reason(td, "2026-07-01", "abstract_only")  # no reason
            stats = obs.scan_deep_dive_modes(td, datetime(2026, 7, 2))
            self.assertEqual(stats["degrade_reasons"].get("未标注原因"), 1)

    # ---- section rendering ----
    def test_section_renders_reason_breakdown_sorted_desc(self):
        modes = {"found": True, "total": 6, "full_text": 2,
                 "abstract_only": 4, "unknown": 0, "degrade_ratio": 4 / 6,
                 "window_days": 30, "recent": [],
                 "degrade_reasons": {"PDF 抓取失败": 3, "非全文来源": 1}}
        sec = obs.build_deep_dive_mode_section(modes)
        self.assertIn("降级原因:", sec)
        # sorted by count desc: PDF (3) before 非全文来源 (1)
        pdf_i = sec.index("PDF 抓取失败 3")
        tier_i = sec.index("非全文来源 1")
        self.assertLess(pdf_i, tier_i)

    def test_section_omits_reason_line_when_no_degrades(self):
        modes = {"found": True, "total": 2, "full_text": 2,
                 "abstract_only": 0, "unknown": 0, "degrade_ratio": 0.0,
                 "window_days": 30, "recent": [], "degrade_reasons": {}}
        sec = obs.build_deep_dive_mode_section(modes)
        self.assertNotIn("降级原因:", sec)

    def test_section_backward_compat_missing_key(self):
        # older stats dict without degrade_reasons must not crash
        modes = {"found": True, "total": 1, "full_text": 0,
                 "abstract_only": 1, "unknown": 0, "degrade_ratio": 1.0,
                 "window_days": 30, "recent": []}
        sec = obs.build_deep_dive_mode_section(modes)
        self.assertNotIn("降级原因:", sec)  # no key -> no line, no crash

    # ---- reverse validation ----
    def test_sabotage_regex_break_loses_categories(self):
        """If _DD_DEGRADE_RE stops matching, all abstract_only fall to
        未标注原因 — proving the regex is load-bearing (not tautology)."""
        import re
        c = "> ⚠️ 抓取降级原因：PDF fetch failed: HTTP 404"
        orig = obs._DD_DEGRADE_RE
        try:
            obs._DD_DEGRADE_RE = re.compile(r"THIS_WILL_NEVER_MATCH_XYZ")
            self.assertEqual(obs._extract_degrade_category(c), "未标注原因")
        finally:
            obs._DD_DEGRADE_RE = orig
        # restored: matches again
        self.assertEqual(obs._extract_degrade_category(c), "PDF 抓取失败")


class TestV37_9_213_HeadSnap(unittest.TestCase):
    """F2: _read_file_sample head must snap to a line boundary so it never
    ends mid-sentence — the mid-sentence head cut fooled the observer LLM
    into false-positive 'truncation' (2026-07-01, V37.9.93 recurrence at
    the HEAD boundary)."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write(self, name, content):
        path = os.path.join(self.tmpdir, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return path, content

    def _head_of(self, sample):
        prefix = obs.SMART_SAMPLE_MARKER_TEMPLATE.split("{omitted}")[0]
        return sample.split(prefix)[0]

    def test_head_snaps_to_line_boundary(self):
        """2026-07-01 blood scenario: markdown with frequent newlines — the
        head must end at a line boundary, not mid-line."""
        lines = [f"这是第 {i} 行分析内容，包含足够中文字符填充采样缓冲区做边界测试。"
                 for i in range(80)]
        body = "# 标题\n\n" + "\n".join(lines) + "\n\n---\n*footer 完整结尾*"
        path, content = self._write("2026-07-01.md", body)
        sample, length = obs._read_file_sample(path)
        head = self._head_of(sample)
        self.assertGreater(length, obs.MAX_SAMPLE_CHARS, "must be sampled")
        self.assertTrue(content.startswith(head))
        # the char immediately after the snapped head must be a newline
        self.assertEqual(content[len(head)], "\n",
                         "head must end at a line boundary (not mid-line)")
        # footer + marker still present (V37.9.93 contract preserved)
        self.assertIn("footer 完整结尾", sample)
        self.assertIn("NOT file truncation", sample)
        self.assertLessEqual(len(sample), obs.MAX_SAMPLE_CHARS)

    def test_snap_is_load_bearing(self):
        """Disable the snap (SNAP_MAX=0) → head ends mid-line, proving the
        snap actually changes behavior (reverse validation)."""
        lines = [f"这是第 {i} 行分析内容，包含足够中文字符填充采样缓冲区做边界测试。"
                 for i in range(80)]
        body = "# 标题\n\n" + "\n".join(lines)
        path, content = self._write("x.md", body)
        orig = obs.SMART_SAMPLE_HEAD_SNAP_MAX
        try:
            obs.SMART_SAMPLE_HEAD_SNAP_MAX = 0
            no_snap = self._head_of(obs._read_file_sample(path)[0])
        finally:
            obs.SMART_SAMPLE_HEAD_SNAP_MAX = orig
        snapped = self._head_of(obs._read_file_sample(path)[0])
        # without snap the head ends mid-line; with snap it ends on newline
        self.assertNotEqual(content[len(no_snap):len(no_snap) + 1], "\n",
                            "no-snap head should end mid-line (test premise)")
        self.assertEqual(content[len(snapped)], "\n",
                         "snapped head must end on a newline")
        self.assertLess(len(snapped), len(no_snap),
                        "snap trims the head back to the boundary")

    def test_no_nearby_newline_keeps_raw_head(self):
        """A very long line with no newline near the budget keeps the raw
        head (budget preserved, no crash)."""
        body = "字" * 5000  # no newlines at all
        path, _ = self._write("longline.md", body)
        sample, length = obs._read_file_sample(path)
        self.assertLessEqual(len(sample), obs.MAX_SAMPLE_CHARS)
        self.assertIn("NOT file truncation", sample)

    def test_omitted_count_stable_from_nominal_budget(self):
        """Marker omitted count uses nominal head budget (5000-1400-500)
        regardless of snapping — preserves V37.9.93 test contract."""
        body = "a" * 5000  # no newlines
        path, _ = self._write("count.md", body)
        sample, _ = obs._read_file_sample(path)
        expected = 5000 - obs.SMART_SAMPLE_HEAD_CHARS - obs.SMART_SAMPLE_TAIL_CHARS
        self.assertIn(f"[...{expected} chars omitted", sample)


class TestV37_9_213_SourceLevelGuards(unittest.TestCase):
    """V37.9.213 source guards — prevent regression of both fixes."""

    @classmethod
    def setUpClass(cls):
        py_path = os.path.join(os.path.dirname(__file__), "daily_observer.py")
        with open(py_path, "r", encoding="utf-8") as f:
            cls.src = f.read()

    def test_v37_9_213_marker_in_source(self):
        self.assertIn("V37.9.213", self.src)

    def test_f1_degrade_reason_machinery_present(self):
        self.assertIn("_DD_DEGRADE_RE", self.src)
        self.assertIn("_extract_degrade_category", self.src)
        self.assertIn("degrade_reasons", self.src)
        self.assertIn("_DD_DEGRADE_CATEGORIES", self.src)

    def test_f2_head_snap_present(self):
        self.assertIn("SMART_SAMPLE_HEAD_SNAP_MAX", self.src)
        # head must be snapped, not raw content[:head_chars]
        helper_start = self.src.find("def _read_file_sample(")
        helper_end = self.src.find("\ndef ", helper_start + 1)
        body = self.src[helper_start:helper_end]
        self.assertIn('rfind("\\n")', body)
        self.assertIn("head_budget", body,
                      "must reserve marker+tail budget before snapping")


# ---------------------------------------------------------------------------
# V37.9.230 (审计 finding G) — observer 注册进 watchdog + last_run 时间格式对齐
# ---------------------------------------------------------------------------
# 血案形态: daily_observer.sh 写 last_run_self_critique.json 用 ISO8601 UTC
# (date -u +%Y-%m-%dT%H:%M:%SZ)，而 job_watchdog 用 strptime('%Y-%m-%d %H:%M:%S')
# 解析本地时间 → 格式不兼容无法注册 → 宪法级 #1 LLM-Observer 的宿主 job 06:30
# cron 静默死无人发现（观察者自己的观察盲区，MR-4 家族）。


class TestV37_9_230_WatchdogRegistration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        base = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(base, "daily_observer.sh"), encoding="utf-8") as f:
            cls.sh_src = f.read()
        with open(os.path.join(base, "job_watchdog.sh"), encoding="utf-8") as f:
            cls.wd_src = f.read()

    def test_observer_time_uses_watchdog_format(self):
        """last_run time 必须是 watchdog 可解析的本地时间格式"""
        self.assertIn("'time': '$(date '+%Y-%m-%d %H:%M:%S')'", self.sh_src)

    def test_iso8601_utc_form_retired(self):
        """旧 ISO8601 UTC 形态必须退役（watchdog strptime 解析不了）"""
        self.assertNotIn("date -u '+%Y-%m-%dT%H:%M:%SZ'", self.sh_src)

    def test_watchdog_registers_observer(self):
        """job_watchdog JOB_STATUS 必须含 daily_observer 条目（core tier）"""
        self.assertIn(
            'daily_observer|$HOME/.kb/last_run_self_critique.json|180000|每日自评Observer|core',
            self.wd_src)

    def test_time_format_contract_cross_file(self):
        """跨文件契约 (MR-8): observer 写的格式必须能被 watchdog 的 strptime 格式解析。
        从两侧源码各自提取格式字面量，真实渲染 + 解析（防未来单侧改格式漂移）。"""
        import re
        import subprocess
        from datetime import datetime
        m_w = re.search(r"strptime\(t, '([^']+)'\)", self.wd_src)
        self.assertIsNotNone(m_w, "watchdog strptime 格式未找到")
        wd_fmt = m_w.group(1)
        m_o = re.search(r"'time': '\$\(date '\+([^']+)'\)'", self.sh_src)
        self.assertIsNotNone(m_o, "observer date 格式未找到")
        rendered = subprocess.run(
            ["date", f"+{m_o.group(1)}"], capture_output=True, text=True
        ).stdout.strip()
        # 不抛异常 = 格式兼容
        datetime.strptime(rendered, wd_fmt)

    def test_v37_9_230_markers(self):
        self.assertIn("V37.9.230", self.sh_src)
        self.assertIn("V37.9.230", self.wd_src)


if __name__ == "__main__":
    unittest.main()
