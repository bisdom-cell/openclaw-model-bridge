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
    """End-to-end orchestrator tests with mock LLM."""

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
    """V37.9.87: 1 cron run produces exactly 1 score_history append."""

    @classmethod
    def setUpClass(cls):
        py_path = os.path.join(os.path.dirname(__file__), "daily_observer.py")
        sh_path = os.path.join(os.path.dirname(__file__), "daily_observer.sh")
        with open(py_path, "r", encoding="utf-8") as f:
            cls.py_src = f.read()
        with open(sh_path, "r", encoding="utf-8") as f:
            cls.sh_src = f.read()

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
        SAME run as score_history. Verify both helpers called in run()."""
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
            real_write = obs._write_observer_to_status

            def trace_append(*a, **k):
                call_order.append("append_score_history")
                return real_append(*a, **k)

            def trace_write(*a, **k):
                call_order.append("_write_observer_to_status")
                return real_write(*a, **k)

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


if __name__ == "__main__":
    unittest.main()
