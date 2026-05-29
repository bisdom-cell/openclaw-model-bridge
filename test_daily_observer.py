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


if __name__ == "__main__":
    unittest.main()
