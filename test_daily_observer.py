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

    def test_discord_only_not_whatsapp(self):
        self.assertIn("--topic daily", self.src)
        self.assertNotIn("--topic whatsapp", self.src)
        self.assertNotIn("--channel whatsapp", self.src)

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


if __name__ == "__main__":
    unittest.main()
