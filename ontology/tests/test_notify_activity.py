#!/usr/bin/env python3
"""
test_notify_activity.py — V37.8.1 MRD-NOTIFY-002 activity layer regression tests

Background
----------
V37.8 rewrote _discover_silent_channels source layer correctly (detects both
--topic X and DISCORD_CH_X callers), but the activity layer scanned wrong paths:
  - notify_queue/*.json — only records failed retries, not successes
  - jobs/*/cache/*.log — not all jobs have cache/ dirs

This produced false positives: all 6 channels reported "7 days no activity"
even when Mac Mini was pushing daily.

V37.8.1 fix: activity layer now uses an explicit TOPIC_JOB_MAP that maps
each topic to its contributing job IDs, then looks up real log paths from
jobs_registry.yaml and checks log file mtimes.

Tests:
  1. TOPIC_JOB_MAP covers all 6 topics
  2. TOPIC_JOB_MAP job IDs exist in jobs_registry.yaml
  3. Source code no longer references notify_queue in activity layer
  4. Source code no longer references jobs/*/cache in activity layer
  5. TOPIC_JOB_MAP is loaded from governance_checker module
  6. Activity layer uses yaml to load registry
"""

import os
import sys
import unittest

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_ONTOLOGY_DIR = os.path.dirname(_TESTS_DIR)
_PROJECT_ROOT = os.path.dirname(_ONTOLOGY_DIR)
for p in [_ONTOLOGY_DIR, _PROJECT_ROOT]:
    if p not in sys.path:
        sys.path.insert(0, p)


class TestTopicJobMapCompleteness(unittest.TestCase):
    """TOPIC_JOB_MAP must cover all 6 Discord topics."""

    def test_all_six_topics_present(self):
        """Every Discord topic must have at least one job mapping."""
        src = open(os.path.join(_ONTOLOGY_DIR, "governance_checker.py")).read()
        for topic in ["papers", "freight", "alerts", "daily", "tech", "ontology"]:
            self.assertIn(f'"{topic}"', src,
                          f"TOPIC_JOB_MAP missing topic '{topic}'")

    def test_papers_has_all_academic_jobs(self):
        src = open(os.path.join(_ONTOLOGY_DIR, "governance_checker.py")).read()
        for job in ["arxiv_monitor", "hf_papers", "semantic_scholar",
                     "dblp", "acl_anthology", "ai_leaders_x"]:
            self.assertIn(f'"{job}"', src,
                          f"papers topic missing job '{job}'")

    def test_daily_has_core_jobs(self):
        src = open(os.path.join(_ONTOLOGY_DIR, "governance_checker.py")).read()
        for job in ["kb_review", "kb_evening", "kb_trend"]:
            self.assertIn(f'"{job}"', src,
                          f"daily topic missing job '{job}'")


class TestTopicJobMapRegistrySync(unittest.TestCase):
    """All job IDs in TOPIC_JOB_MAP must exist in jobs_registry.yaml."""

    def test_all_mapped_jobs_exist_in_registry(self):
        import yaml
        import re

        # Extract TOPIC_JOB_MAP from source
        src = open(os.path.join(_ONTOLOGY_DIR, "governance_checker.py")).read()
        # Find all job IDs mentioned in TOPIC_JOB_MAP block
        # Simpler: just extract all quoted strings in the TOPIC_JOB_MAP section
        map_match = re.search(r'TOPIC_JOB_MAP\s*=\s*\{(.+?)\}', src, re.DOTALL)
        self.assertIsNotNone(map_match, "TOPIC_JOB_MAP not found in source")
        map_block = map_match.group(1)
        # Extract job IDs (values in lists, not keys)
        job_ids = re.findall(r'"(\w+)"', map_block)
        # Remove topic names
        topics = {"papers", "freight", "alerts", "daily", "tech", "ontology"}
        job_ids = [j for j in job_ids if j not in topics]
        self.assertTrue(len(job_ids) > 0, "No job IDs found in TOPIC_JOB_MAP")

        # Load registry
        with open(os.path.join(_PROJECT_ROOT, "jobs_registry.yaml")) as f:
            reg = yaml.safe_load(f)
        registry_ids = {j["id"] for j in reg.get("jobs", [])}

        for jid in job_ids:
            self.assertIn(jid, registry_ids,
                          f"TOPIC_JOB_MAP job '{jid}' not in jobs_registry.yaml")


class TestActivityLayerNoLegacyPaths(unittest.TestCase):
    """V37.8.1: activity layer must NOT scan notify_queue or jobs/*/cache."""

    def _read_activity_code_lines(self):
        """Extract executable (non-comment) lines from the activity layer section."""
        src = open(os.path.join(_ONTOLOGY_DIR, "governance_checker.py")).read()
        start = src.find("# ── Activity layer:")
        end = src.find("# ── 合并结论", start)
        self.assertGreater(start, 0, "Activity layer section not found")
        self.assertGreater(end, start, "Merge conclusion section not found")
        section = src[start:end]
        # Only keep non-comment executable lines
        lines = [l for l in section.split("\n")
                 if l.strip() and not l.strip().startswith("#")]
        return "\n".join(lines)

    def test_no_notify_queue_in_code(self):
        """Executable code must not reference notify_queue (comments are OK)."""
        code = self._read_activity_code_lines()
        self.assertNotIn("notify_queue", code,
                         "Activity layer code still references notify_queue (V37.8 bug)")

    def test_no_cache_log_glob_in_code(self):
        """Executable code must not glob jobs/*/cache/*.log."""
        code = self._read_activity_code_lines()
        # Check for the specific V37.8 pattern: glob(home, "jobs", "*", "cache"...)
        self.assertNotIn('"cache"', code,
                         "Activity layer code still references jobs/*/cache (V37.8 bug)")

    def test_uses_yaml_registry(self):
        """Activity layer should load jobs_registry.yaml for log paths."""
        code = self._read_activity_code_lines()
        self.assertIn("jobs_registry.yaml", code)

    def test_uses_topic_job_map(self):
        code = self._read_activity_code_lines()
        self.assertIn("TOPIC_JOB_MAP", code)


class TestPreflightStatusJsonExemption(unittest.TestCase):
    """V37.8.1: preflight must exempt status.json from md5 drift check."""

    def test_status_json_exemption_in_preflight(self):
        src = open(os.path.join(_PROJECT_ROOT, "preflight_check.sh")).read()
        self.assertIn('status.json', src)
        self.assertIn('豁免', src,
                      "preflight_check.sh missing status.json exemption")

    def test_exemption_before_md5_comparison(self):
        """The status.json skip must appear before the md5 hash comparison."""
        src = open(os.path.join(_PROJECT_ROOT, "preflight_check.sh")).read()
        exempt_pos = src.find('status.json')
        md5_pos = src.find('HASH_SRC=')
        self.assertGreater(md5_pos, 0)
        self.assertGreater(exempt_pos, 0)
        # The exemption check must be before the md5 comparison in the file
        # (both are in the FILE_MAP loop, exemption continues before md5)
        self.assertLess(exempt_pos, md5_pos,
                        "status.json exemption must appear before md5 comparison")


class TestPreflightKbIndexWarnThreshold(unittest.TestCase):
    """V37.8.1: KB index uses coverage percentage threshold (>=90% = warn, <90% = fail)."""

    def test_coverage_percentage_threshold(self):
        src = open(os.path.join(_PROJECT_ROOT, "preflight_check.sh")).read()
        self.assertIn('-ge 90', src,
                      "preflight should use >=90% coverage threshold")

    def test_warn_for_high_coverage(self):
        """>=90% coverage should produce warn, not fail."""
        src = open(os.path.join(_PROJECT_ROOT, "preflight_check.sh")).read()
        idx = src.find('-ge 90')
        self.assertGreater(idx, 0)
        section = src[idx:idx + 200]
        self.assertIn('warn', section,
                      ">=90% coverage should produce warn")

    def test_fail_for_low_coverage(self):
        """<90% coverage should produce fail."""
        src = open(os.path.join(_PROJECT_ROOT, "preflight_check.sh")).read()
        idx = src.find('-ge 90')
        self.assertGreater(idx, 0)
        section = src[idx:idx + 400]
        self.assertIn('fail', section,
                      "<90% coverage should produce fail")


if __name__ == "__main__":
    unittest.main()
