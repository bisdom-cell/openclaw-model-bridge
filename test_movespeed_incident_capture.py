#!/usr/bin/env python3
"""
test_movespeed_incident_capture.py — V37.9.14

Unit tests for movespeed_incident_capture.sh:
  - Helper contract: never-fail, writes valid JSONL, cross-platform degrade
  - Rotation: respects MOVESPEED_INCIDENT_MAX_SIZE
  - Bash 3.2 compat: no bash 4+ syntax (for macOS Mac Mini default bash)
  - Invariant coverage: all 20 rsync fail-loud sites invoke the helper
  - FILE_MAP deployment mapping present
  - INV-BACKUP-001 extended with check 4 (helper invocation guard)
"""

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
HELPER_PATH = os.path.join(REPO_ROOT, "movespeed_incident_capture.sh")

# The 20 rsync fail-loud call sites. If we add/move a site, update this list;
# the invariant test will fail fast so we know coverage drifted.
EXPECTED_RSYNC_SITES = [
    "kb_save_arxiv.sh",
    "kb_dream.sh",
    "kb_evening.sh",
    "kb_inject.sh",
    "kb_review.sh",
    "run_hn_fixed.sh",
    "jobs/hf_papers/run_hf_papers.sh",
    "jobs/semantic_scholar/run_semantic_scholar.sh",
    "jobs/dblp/run_dblp.sh",
    "jobs/ontology_sources/run_ontology_sources.sh",
    "jobs/karpathy_x/run_karpathy_x.sh",
    "jobs/rss_blogs/run_rss_blogs.sh",
    "jobs/arxiv_monitor/run_arxiv.sh",
    "jobs/freight_watcher/run_freight.sh",
    "jobs/acl_anthology/run_acl_anthology.sh",
    "jobs/github_trending/run_github_trending.sh",
    "jobs/ai_leaders_x/run_ai_leaders_x.sh",
    "jobs/openclaw_official/run_discussions.sh",
    "jobs/openclaw_official/run.sh",
]


def run_helper(exit_code, caller, incident_file, max_size=None, env_extra=None, timeout=15):
    """Invoke the helper, return (returncode, stdout, stderr)."""
    env = os.environ.copy()
    env["MOVESPEED_INCIDENT_FILE"] = incident_file
    if max_size is not None:
        env["MOVESPEED_INCIDENT_MAX_SIZE"] = str(max_size)
    if env_extra:
        env.update(env_extra)
    proc = subprocess.run(
        ["bash", HELPER_PATH, str(exit_code), caller],
        capture_output=True,
        text=True,
        env=env,
        timeout=timeout,
    )
    return proc.returncode, proc.stdout, proc.stderr


class TestHelperExists(unittest.TestCase):
    def test_helper_file_exists(self):
        self.assertTrue(
            os.path.isfile(HELPER_PATH),
            f"Helper missing at {HELPER_PATH}",
        )

    def test_helper_is_executable(self):
        self.assertTrue(
            os.access(HELPER_PATH, os.X_OK),
            "Helper must be executable (chmod +x)",
        )

    def test_helper_shebang_is_bash(self):
        with open(HELPER_PATH, "r", encoding="utf-8") as fp:
            first = fp.readline().rstrip()
        self.assertTrue(
            first.startswith("#!") and "bash" in first,
            f"Expected bash shebang, got: {first!r}",
        )

    def test_helper_syntax_clean(self):
        """`bash -n` must pass — catches typos pre-deploy."""
        rc = subprocess.run(
            ["bash", "-n", HELPER_PATH],
            capture_output=True,
        ).returncode
        self.assertEqual(rc, 0, "bash -n syntax check failed")


class TestHelperContract(unittest.TestCase):
    """The single most critical property: the helper must never fail the caller."""

    def test_exits_zero_on_dev_env(self):
        """On dev/Linux with no /Volumes/MOVESPEED, exit must still be 0."""
        with tempfile.TemporaryDirectory() as td:
            inc = os.path.join(td, "incidents.jsonl")
            rc, _, _ = run_helper(23, "/fake/kb_inject.sh", inc)
            self.assertEqual(rc, 0, "Helper must never propagate failure")

    def test_exits_zero_with_empty_args(self):
        """Even missing argv must not crash."""
        with tempfile.TemporaryDirectory() as td:
            inc = os.path.join(td, "incidents.jsonl")
            proc = subprocess.run(
                ["bash", HELPER_PATH],
                capture_output=True,
                text=True,
                env={**os.environ, "MOVESPEED_INCIDENT_FILE": inc},
                timeout=15,
            )
            self.assertEqual(proc.returncode, 0)

    def test_exits_zero_when_exit_code_contains_special_chars(self):
        """Shell metacharacters in args must not break escaping."""
        with tempfile.TemporaryDirectory() as td:
            inc = os.path.join(td, "incidents.jsonl")
            rc, _, _ = run_helper("23; rm -rf /tmp/foo", "/fake/a'b\"c.sh", inc)
            self.assertEqual(rc, 0)
            self.assertTrue(os.path.isfile(inc))
            # Ensure the literal injection text landed in a json string field
            # and was NOT executed:
            with open(inc) as fp:
                rec = json.loads(fp.readline())
            self.assertIn("rm -rf", rec["exit_code"])

    def test_exits_zero_when_tmpdir_broken(self):
        """If TMPDIR points to nowhere writable, we still get a line out."""
        with tempfile.TemporaryDirectory() as td:
            inc = os.path.join(td, "incidents.jsonl")
            rc, _, _ = run_helper(
                1,
                "/fake/caller.sh",
                inc,
                env_extra={"TMPDIR": "/nonexistent/path/that/does/not/exist"},
            )
            self.assertEqual(rc, 0)
            # We may or may not have written a line depending on fallback;
            # contract is exit 0, not line-count.

    def test_writes_valid_jsonl(self):
        with tempfile.TemporaryDirectory() as td:
            inc = os.path.join(td, "incidents.jsonl")
            rc, _, _ = run_helper(23, "/x/kb_inject.sh", inc)
            self.assertEqual(rc, 0)
            self.assertTrue(os.path.isfile(inc))
            with open(inc) as fp:
                lines = [l for l in fp.read().splitlines() if l.strip()]
            self.assertEqual(len(lines), 1)
            rec = json.loads(lines[0])
            self.assertEqual(rec["caller"], "kb_inject.sh")
            self.assertEqual(rec["exit_code"], "23")
            self.assertTrue(rec["timestamp_iso"])
            # These fields must exist (empty-string is fine on dev env)
            for key in (
                "mount",
                "disk_info",
                "ls_top",
                "ls_kb",
                "df",
                "probe_top",
                "probe_kb",
                "procs",
                "os",
                "env",
            ):
                self.assertIn(key, rec, f"missing field {key}")

    def test_appends_on_repeated_invocation(self):
        """Second call must append, not overwrite."""
        with tempfile.TemporaryDirectory() as td:
            inc = os.path.join(td, "incidents.jsonl")
            run_helper(1, "/a/caller_one.sh", inc)
            run_helper(2, "/b/caller_two.sh", inc)
            run_helper(3, "/c/caller_three.sh", inc)
            with open(inc) as fp:
                lines = [json.loads(l) for l in fp.read().splitlines() if l.strip()]
            self.assertEqual(len(lines), 3)
            callers = [r["caller"] for r in lines]
            self.assertEqual(
                callers, ["caller_one.sh", "caller_two.sh", "caller_three.sh"]
            )

    def test_basename_extraction(self):
        """Caller arg with full path should be stored as basename only."""
        with tempfile.TemporaryDirectory() as td:
            inc = os.path.join(td, "incidents.jsonl")
            run_helper(
                5,
                "/Users/bisdom/.openclaw/jobs/arxiv_monitor/run_arxiv.sh",
                inc,
            )
            with open(inc) as fp:
                rec = json.loads(fp.readline())
            self.assertEqual(rec["caller"], "run_arxiv.sh")

    def test_timestamp_is_iso8601_utc(self):
        with tempfile.TemporaryDirectory() as td:
            inc = os.path.join(td, "incidents.jsonl")
            run_helper(1, "/x/a.sh", inc)
            with open(inc) as fp:
                rec = json.loads(fp.readline())
            self.assertRegex(
                rec["timestamp_iso"],
                r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$",
            )


class TestRotation(unittest.TestCase):
    def test_rotation_happens_when_oversized(self):
        """When file > MAX_SIZE, existing file moves to .1 and new write starts fresh."""
        with tempfile.TemporaryDirectory() as td:
            inc = os.path.join(td, "incidents.jsonl")
            # Seed a big file
            with open(inc, "w") as fp:
                fp.write("x" * 200)
            # Set threshold very low to force rotation
            run_helper(1, "/x/a.sh", inc, max_size=100)
            self.assertTrue(os.path.isfile(inc + ".1"), ".1 rotation missing")
            with open(inc) as fp:
                content = fp.read().strip()
            # New file has only the new JSONL line, not the old 'x' content
            self.assertFalse(content.startswith("x"))

    def test_rotation_skipped_when_small(self):
        with tempfile.TemporaryDirectory() as td:
            inc = os.path.join(td, "incidents.jsonl")
            with open(inc, "w") as fp:
                fp.write('{"first":"line"}\n')
            run_helper(1, "/x/b.sh", inc, max_size=10485760)  # 10MB
            self.assertFalse(os.path.isfile(inc + ".1"))
            with open(inc) as fp:
                lines = fp.read().splitlines()
            self.assertEqual(len(lines), 2)  # original + new


class TestBashCompat(unittest.TestCase):
    """Mac Mini ships bash 3.2 — forbid bash 4+ syntax in the helper."""

    def _read_helper_code(self):
        """Return helper source with comment lines stripped.

        Bash-4-only constructs may legitimately appear in header documentation
        (e.g. "no ${var@Q}, no mapfile"), which would false-positive a naive
        regex scan. We strip lines whose first non-whitespace char is `#`.
        """
        with open(HELPER_PATH, "r", encoding="utf-8") as fp:
            raw = fp.read()
        lines = []
        for line in raw.splitlines():
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue
            lines.append(line)
        return "\n".join(lines)

    def test_no_quote_transform_operator(self):
        # ${var@Q} is bash 4.4+
        self.assertNotRegex(
            self._read_helper_code(),
            r"\$\{[^}]*@[QUEAK][^}]*\}",
            "Uses ${var@...} operator (bash 4.4+), incompatible with macOS bash 3.2",
        )

    def test_no_case_transform(self):
        # ${var^^} / ${var,,} are bash 4+
        self.assertNotRegex(
            self._read_helper_code(),
            r"\$\{[^}]*\^\^[^}]*\}|\$\{[^}]*,,[^}]*\}",
            "Uses bash 4+ case transform",
        )

    def test_no_associative_arrays(self):
        # declare -A is bash 4+
        self.assertNotRegex(
            self._read_helper_code(),
            r"\bdeclare\s+-A\b|\blocal\s+-A\b",
            "Uses associative arrays (bash 4+)",
        )

    def test_no_mapfile_or_readarray(self):
        self.assertNotRegex(
            self._read_helper_code(),
            r"\b(mapfile|readarray)\b",
            "Uses mapfile/readarray (bash 4+)",
        )

    def _read_helper_raw(self):
        with open(HELPER_PATH, "r", encoding="utf-8") as fp:
            return fp.read()

    def test_literal_warn_ssd_present_for_inv_backup(self):
        """INV-BACKUP-001 check 2 scans every .sh containing rsync+MOVESPEED and
        requires 'WARN: SSD'. Helper mentions MOVESPEED in comments/code, so it
        must also carry the string (in the header comment) to stay compliant.
        """
        src = self._read_helper_raw()
        if "MOVESPEED" in src:
            self.assertIn(
                "WARN: SSD",
                src,
                "INV-BACKUP-001 check 2 will fail without 'WARN: SSD' token",
            )

    def test_no_rsync_antipattern_in_helper(self):
        """Helper must not accidentally contain the exact anti-pattern that
        INV-BACKUP-001 check 1 bans."""
        self.assertNotRegex(
            self._read_helper_raw(),
            r"rsync.*MOVESPEED.*2>/dev/null\s*\|\|\s*true",
            "Helper contains the banned anti-pattern",
        )


class TestInvariantCoverage(unittest.TestCase):
    """Structural invariant: every rsync fail-loud site must invoke the helper."""

    def _read(self, rel):
        with open(os.path.join(REPO_ROOT, rel), "r", encoding="utf-8") as fp:
            return fp.read()

    def test_every_expected_site_exists(self):
        for rel in EXPECTED_RSYNC_SITES:
            path = os.path.join(REPO_ROOT, rel)
            self.assertTrue(
                os.path.isfile(path),
                f"Expected rsync call site missing: {rel}",
            )

    def test_every_expected_site_invokes_helper(self):
        missing = []
        for rel in EXPECTED_RSYNC_SITES:
            content = self._read(rel)
            if "movespeed_incident_capture.sh" not in content:
                missing.append(rel)
        self.assertEqual(
            [],
            missing,
            f"{len(missing)} sites missing helper invocation: {missing}",
        )

    def test_helper_invocation_is_inside_rsync_failure_branch(self):
        """
        Ensure `movespeed_incident_capture.sh` is wired into the rsync
        failure branch, not somewhere random. We verify this by checking that
        each invocation is preceded within the same line (or nearby) by the
        WARN: SSD echo — the shared fail-loud marker.

        V37.9.4 established the pattern:
          rsync ... 2>&1 || { echo "[x] WARN: SSD rsync failed..." >&2; helper ...; }
        """
        errors = []
        for rel in EXPECTED_RSYNC_SITES:
            content = self._read(rel)
            for m in re.finditer(r"movespeed_incident_capture\.sh", content):
                # Look back up to 400 chars; expect 'WARN: SSD' and 'rsync' both present
                start = max(0, m.start() - 400)
                window = content[start : m.start()]
                if "WARN: SSD" not in window or "rsync" not in window:
                    errors.append(rel)
                    break
        self.assertEqual(
            [],
            errors,
            f"Helper not wired into rsync fail-loud branch in: {errors}",
        )

    def test_no_extra_undeclared_sites(self):
        """
        If someone adds a new rsync-to-MOVESPEED without updating
        EXPECTED_RSYNC_SITES, catch it here.
        """
        found = []
        for root, dirs, files in os.walk(REPO_ROOT):
            # Skip .git and virtualenvs
            dirs[:] = [d for d in dirs if d not in (".git", "node_modules", "__pycache__")]
            for fn in files:
                if not fn.endswith(".sh"):
                    continue
                path = os.path.join(root, fn)
                rel = os.path.relpath(path, REPO_ROOT)
                # Helper itself doesn't call rsync — skip
                if rel == "movespeed_incident_capture.sh":
                    continue
                try:
                    with open(path, "r", encoding="utf-8") as fp:
                        content = fp.read()
                except (IOError, UnicodeDecodeError):
                    continue
                if "rsync" in content and "MOVESPEED" in content:
                    found.append(rel)
        # Normalize for comparison
        found_set = set(found)
        expected_set = set(EXPECTED_RSYNC_SITES)
        extras = found_set - expected_set
        missing = expected_set - found_set
        self.assertEqual(
            set(),
            extras,
            f"New rsync-MOVESPEED sites not in EXPECTED_RSYNC_SITES: {extras}",
        )
        self.assertEqual(
            set(),
            missing,
            f"EXPECTED sites not found in repo: {missing}",
        )


class TestFileMapCoverage(unittest.TestCase):
    def test_helper_in_auto_deploy_file_map(self):
        auto_deploy = os.path.join(REPO_ROOT, "auto_deploy.sh")
        with open(auto_deploy, "r", encoding="utf-8") as fp:
            content = fp.read()
        self.assertIn(
            "movespeed_incident_capture.sh",
            content,
            "Helper must be added to auto_deploy.sh FILE_MAP for Mac Mini deployment",
        )


class TestGovernanceExtension(unittest.TestCase):
    """INV-BACKUP-001 must have a check 4 referencing the helper."""

    def test_inv_backup_001_gains_helper_invocation_check(self):
        path = os.path.join(REPO_ROOT, "ontology", "governance_ontology.yaml")
        with open(path, "r", encoding="utf-8") as fp:
            content = fp.read()
        # Must reference the helper filename in a check clause
        self.assertIn("movespeed_incident_capture.sh", content)


if __name__ == "__main__":
    unittest.main(verbosity=2)
