#!/usr/bin/env python3
"""
test_governance_cron_matcher.py — regression test for INV-CRON-003/004 matcher

Background:
-----------
The original bash-lc-enforcement and crontab-no-duplicates checks used naive
substring matching (`script in line` / `sname in l`). This produced
false-positives whenever one registry entry's command was a prefix of another,
as with Map/Reduce split scheduling:

    kb_dream.sh                   # Reduce job
    kb_dream.sh --map-sources     # Map Sources job
    kb_dream.sh --map-notes       # Map Notes job

`kb_dream.sh` appears as a substring in all three crontab lines, so the
Reduce job was mis-counted as 3 duplicates, contradicting the check's own
declaration ("same script with different args is legal split scheduling").

Fix:
----
Use `endswith(entry)` + word-boundary (char before entry must be path
separator, whitespace, or quote). This isolates each entry to exactly one
crontab line even when entries share prefixes.

This test locks in the fix so the bug can't silently regress.
"""

import os
import re
import sys
import unittest

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_ONTOLOGY_DIR = os.path.dirname(_TESTS_DIR)
_PROJECT_ROOT = os.path.dirname(_ONTOLOGY_DIR)
for p in [_ONTOLOGY_DIR, _PROJECT_ROOT]:
    if p not in sys.path:
        sys.path.insert(0, p)


def _cron_cmd_invokes(line, entry):
    """Mirror of the matcher in governance_ontology.yaml INV-CRON-003/004.

    Return True iff this crontab line's command invokes exactly this registry
    entry. Uses endswith + word-boundary to avoid false-positives on shared
    prefixes.
    """
    parts = line.split(None, 5)
    if len(parts) < 6:
        return False
    cmd = parts[5]
    cmd = re.split(r"\s*(?:>>|>|<|2>|\|)", cmd, maxsplit=1)[0]
    cmd = cmd.rstrip(" '\"")
    if not cmd.endswith(entry):
        return False
    idx = len(cmd) - len(entry)
    if idx == 0:
        return True
    return cmd[idx - 1] in "/ \t\"'"


# Real Mac Mini crontab snapshot (2026-04-11) showing the Map-Reduce split
# scheduling that exposed the original substring-matching bug.
SAMPLE_CRONTAB = """*/2 * * * * bash -lc 'bash ~/openclaw-model-bridge/auto_deploy.sh >> ~/.openclaw/logs/auto_deploy.log 2>&1'
0 8,14,20 * * * bash -lc 'bash ~/.openclaw/jobs/freight_watcher/run_freight.sh >> ~/.openclaw/logs/jobs/freight_watcher.log 2>&1'
0 3 * * * bash -lc 'bash ~/kb_dream.sh >> ~/kb_dream.log 2>&1'
0 0 * * * cd ~ && bash kb_dream.sh --map-sources >> ~/kb_dream.log 2>&1
40 0 * * * cd ~ && bash kb_dream.sh --map-notes >> ~/kb_dream.log 2>&1
0 7 * * * bash -lc "~/governance_audit_cron.sh" >> ~/governance_audit.log 2>&1
0 14 * * * mkdir -p $HOME/.openclaw/logs/jobs; bash -lc "$HOME/.openclaw/jobs/github_trending/run_github_trending.sh >> $HOME/.openclaw/logs/jobs/github_trending.log 2>&1"
"""


class TestCronCmdInvokes(unittest.TestCase):
    """Unit tests for the endswith+word-boundary matcher."""

    def test_exact_entry_matches_exact_line(self):
        line = "0 3 * * * bash -lc 'bash ~/kb_dream.sh >> ~/kb_dream.log 2>&1'"
        self.assertTrue(_cron_cmd_invokes(line, "kb_dream.sh"))

    def test_reduce_entry_does_not_match_map_line(self):
        """Regression: kb_dream.sh (Reduce) must not match --map-sources line."""
        map_line = "0 0 * * * cd ~ && bash kb_dream.sh --map-sources >> ~/kb_dream.log 2>&1"
        self.assertFalse(_cron_cmd_invokes(map_line, "kb_dream.sh"))

    def test_map_sources_entry_matches_its_line(self):
        line = "0 0 * * * cd ~ && bash kb_dream.sh --map-sources >> ~/kb_dream.log 2>&1"
        self.assertTrue(_cron_cmd_invokes(line, "kb_dream.sh --map-sources"))

    def test_map_sources_entry_does_not_match_map_notes_line(self):
        map_notes_line = "40 0 * * * cd ~ && bash kb_dream.sh --map-notes >> ~/kb_dream.log 2>&1"
        self.assertFalse(_cron_cmd_invokes(map_notes_line, "kb_dream.sh --map-sources"))

    def test_entry_with_path_prefix_in_line(self):
        line = "0 8,14,20 * * * bash -lc 'bash ~/.openclaw/jobs/freight_watcher/run_freight.sh >> ~/.openclaw/logs/jobs/freight_watcher.log 2>&1'"
        self.assertTrue(_cron_cmd_invokes(line, "run_freight.sh"))

    def test_word_boundary_rejects_partial_name(self):
        """`dream.sh` must not match `kb_dream.sh`."""
        line = "0 3 * * * bash -lc 'bash ~/kb_dream.sh >> ~/kb_dream.log 2>&1'"
        self.assertFalse(_cron_cmd_invokes(line, "dream.sh"))

    def test_mkdir_prefix_does_not_break_match(self):
        line = '0 14 * * * mkdir -p $HOME/.openclaw/logs/jobs; bash -lc "$HOME/.openclaw/jobs/github_trending/run_github_trending.sh >> $HOME/.openclaw/logs/jobs/github_trending.log 2>&1"'
        self.assertTrue(_cron_cmd_invokes(line, "run_github_trending.sh"))

    def test_double_quoted_command_match(self):
        line = '0 7 * * * bash -lc "~/governance_audit_cron.sh" >> ~/governance_audit.log 2>&1'
        self.assertTrue(_cron_cmd_invokes(line, "governance_audit_cron.sh"))

    def test_empty_line_returns_false(self):
        self.assertFalse(_cron_cmd_invokes("", "foo.sh"))

    def test_comment_style_not_enough_fields(self):
        self.assertFalse(_cron_cmd_invokes("# comment", "foo.sh"))


class TestInvCron004NoFalsePositive(unittest.TestCase):
    """End-to-end regression: kb_dream Reduce must NOT be flagged as duplicate."""

    def test_kb_dream_reduce_counts_as_one(self):
        lines = [l for l in SAMPLE_CRONTAB.splitlines() if l.strip() and not l.strip().startswith("#")]
        count = sum(1 for l in lines if _cron_cmd_invokes(l, "kb_dream.sh"))
        self.assertEqual(count, 1, "kb_dream Reduce must match exactly 1 crontab line, got {}".format(count))

    def test_kb_dream_map_sources_counts_as_one(self):
        lines = [l for l in SAMPLE_CRONTAB.splitlines() if l.strip() and not l.strip().startswith("#")]
        count = sum(1 for l in lines if _cron_cmd_invokes(l, "kb_dream.sh --map-sources"))
        self.assertEqual(count, 1)

    def test_kb_dream_map_notes_counts_as_one(self):
        lines = [l for l in SAMPLE_CRONTAB.splitlines() if l.strip() and not l.strip().startswith("#")]
        count = sum(1 for l in lines if _cron_cmd_invokes(l, "kb_dream.sh --map-notes"))
        self.assertEqual(count, 1)


class TestInvCron003DetectsMissingBashLc(unittest.TestCase):
    """End-to-end regression: checker must flag real missing-bash-lc cases."""

    def test_map_sources_entry_flagged_as_missing_bash_lc(self):
        entry = "kb_dream.sh --map-sources"
        found_line = None
        for line in SAMPLE_CRONTAB.splitlines():
            if not line.strip() or line.strip().startswith("#"):
                continue
            if _cron_cmd_invokes(line, entry):
                found_line = line
                break
        self.assertIsNotNone(found_line)
        self.assertNotIn("bash -lc", found_line)

    def test_reduce_entry_finds_bash_lc_line(self):
        entry = "kb_dream.sh"
        found_line = None
        for line in SAMPLE_CRONTAB.splitlines():
            if not line.strip() or line.strip().startswith("#"):
                continue
            if _cron_cmd_invokes(line, entry):
                found_line = line
                break
        self.assertIsNotNone(found_line)
        self.assertIn("bash -lc", found_line)


class TestYamlMatcherInSync(unittest.TestCase):
    """Sanity check: the matcher logic in governance_ontology.yaml must match
    this test file's local copy. If this fails, someone changed one side
    without the other."""

    def test_yaml_contains_endswith_matcher(self):
        yaml_path = os.path.join(_PROJECT_ROOT, "ontology", "governance_ontology.yaml")
        with open(yaml_path) as f:
            content = f.read()
        # Both INV-CRON-003 and INV-CRON-004 should use the _cron_cmd_invokes helper
        self.assertIn("_cron_cmd_invokes", content)
        self.assertIn("cmd.endswith(entry)", content)
        # And must NOT fall back to the naive `sname in l` substring approach
        self.assertNotIn("if sname in l:", content)
        self.assertNotIn("if script in line and not line.strip().startswith('#'):", content)


if __name__ == "__main__":
    unittest.main(verbosity=2)
