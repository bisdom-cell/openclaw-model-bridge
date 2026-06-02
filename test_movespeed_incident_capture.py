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
    "kb_deep_dive.sh",
    "jobs/hn_watcher/run_hn_fixed.sh",
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
    "movespeed_daily_sync.sh",  # V37.9.86: KB→SSD daily sync (uses helper)
    "kb_radar.sh",  # V37.9.99: Opportunity Radar Stage 5 radar/ 备份 (uses helper)
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
                "ownership_top",  # V37.9.29 (b): real UID:GID at /Volumes/MOVESPEED
                "ownership_kb",   # V37.9.29 (b): real UID:GID at /Volumes/MOVESPEED/KB
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
        """V37.9.14: site 直接调 movespeed_incident_capture.sh.
        V37.9.27 升级: site 调 movespeed_rsync_helper.sh, 后者内部接管 capture.
        任一模式都合规 (helper 是 single source of truth, MR-8)."""
        missing = []
        for rel in EXPECTED_RSYNC_SITES:
            content = self._read(rel)
            has_legacy = "movespeed_incident_capture.sh" in content
            has_v37_9_27 = "movespeed_rsync_helper.sh" in content
            if not (has_legacy or has_v37_9_27):
                missing.append(rel)
        self.assertEqual(
            [],
            missing,
            f"{len(missing)} sites missing capture wiring (need either "
            f"V37.9.14 movespeed_incident_capture.sh direct call OR "
            f"V37.9.27 movespeed_rsync_helper.sh wrapper): {missing}",
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
                # V37.9.26: monitor consumers (read incidents.jsonl + alert)
                # reference "rsync" + "MOVESPEED" strings ONLY in alert message
                # text, never invoke rsync. Don't conflate with production sites.
                if rel == "job_watchdog.sh":
                    continue
                # V37.9.27: rsync helper itself wraps rsync calls, but it's the
                # single source of truth — exempt from "site" detection
                # (sites that USE the helper are already in EXPECTED_RSYNC_SITES).
                if rel == "movespeed_rsync_helper.sh":
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


class TestV37929MountFieldFix(unittest.TestCase):
    """V37.9.29 candidate (a): mount field truncation bug fix.

    Bug history (2026-04-29 → 2026-05-05, ~8 days silent misdiagnosis):
      - Line 63 used `grep -i -e MOVESPEED -e Volumes` matching ALL /Volumes/*
        entries (Preboot/Data/Recovery/MOVESPEED), often >400 chars combined.
      - Line 120 read_file limit was 400 → MOVESPEED line frequently truncated
        out of the captured window.
      - incident_analyzer.py mount-state classifier (line 215-225) scans for
        "read-only" / "read-write" substrings, never finds them in truncated
        output, falls into else → reports "other_or_unmounted".
      - Resulting metric "21/21 other_or_unmounted" was used in V37.9.28
        diagnosis as evidence of unmount → 4-step incorrect hypothesis chain.

    Fix:
      1. Narrow grep to MOVESPEED only — single line ~80 chars.
      2. Raise limit 400 → 800 as defense-in-depth for future verbose mount.
    """

    def test_grep_only_matches_movespeed_no_volumes_context(self):
        """Source guard: -e Volumes must be removed (root cause)."""
        with open(HELPER_PATH, "r", encoding="utf-8") as fp:
            content = fp.read()
        # Must use single-target grep
        self.assertIn(
            "mount 2>/dev/null | grep -i MOVESPEED >",
            content,
            "Expected narrow grep pattern (V37.9.29 fix)",
        )
        # The old wide pattern must be gone
        self.assertNotIn(
            "grep -i -e MOVESPEED -e Volumes",
            content,
            "Old wide grep pattern still present — re-introduces truncation bug",
        )

    def test_mount_field_limit_raised_to_800(self):
        """Source guard: defense-in-depth limit increase 400 → 800."""
        with open(HELPER_PATH, "r", encoding="utf-8") as fp:
            content = fp.read()
        self.assertIn(
            'read_file("mount", 800)',
            content,
            "Expected mount field limit=800 (V37.9.29 defense-in-depth)",
        )
        # Old 400 limit must not coexist (would shadow the fix on first match)
        self.assertNotIn(
            'read_file("mount", 400)',
            content,
            "Old 400-char limit still present",
        )

    def test_v37_9_29_marker_present(self):
        """Source guard: V37.9.29 attribution comment near the fix."""
        with open(HELPER_PATH, "r", encoding="utf-8") as fp:
            content = fp.read()
        self.assertIn("V37.9.29", content, "V37.9.29 marker comment missing")

    def test_mount_field_full_capture_with_many_volumes_present(self):
        """Behavior test: inject fake `mount` outputting many /Volumes/* entries
        with MOVESPEED at the END. With the OLD pattern this would truncate
        MOVESPEED out; with the NEW pattern the captured field must contain
        MOVESPEED and NOT be marked [truncated].
        """
        # Build fake `mount` shell shim that prints multi-volume output similar
        # to what macOS does, with MOVESPEED last (worst case for truncation).
        with tempfile.TemporaryDirectory() as fake_bin:
            fake_mount = os.path.join(fake_bin, "mount")
            # Each line ~70-90 chars; 6 lines totalling ~500 chars before MOVESPEED.
            fake_output_lines = [
                "/dev/disk1s1 on / (apfs, local, journaled)",
                "devfs on /dev (devfs, local, nobrowse)",
                "/dev/disk1s2 on /System/Volumes/Preboot (apfs, sealed, local, nobrowse)",
                "/dev/disk1s3 on /System/Volumes/Recovery (apfs, sealed, local, nobrowse)",
                "/dev/disk1s4 on /System/Volumes/Data (apfs, local, journaled, nobrowse)",
                "/dev/disk1s5 on /System/Volumes/VM (apfs, local, noexec, nobrowse, nosuid)",
                "/dev/disk1s6 on /System/Volumes/Update (apfs, sealed, local, nobrowse)",
                # MOVESPEED last — would be truncated under old grep+400 combo
                "/dev/disk6s1 on /Volumes/MOVESPEED (apfs, local, nodev, nosuid, journaled, mounted by bisdom)",
            ]
            with open(fake_mount, "w", encoding="utf-8") as fp:
                fp.write("#!/bin/sh\n")
                # Use printf to avoid echo/quoting traps; each line as separate arg
                for line in fake_output_lines:
                    # Shell-escape via single quotes; no quotes in our test data
                    fp.write(f"printf '%s\\n' '{line}'\n")
            os.chmod(fake_mount, 0o755)

            with tempfile.TemporaryDirectory() as td:
                inc = os.path.join(td, "incidents.jsonl")
                # Prepend fake bin to PATH so `mount` resolves to our shim.
                env_extra = {"PATH": fake_bin + os.pathsep + os.environ.get("PATH", "")}
                rc, _, _ = run_helper(
                    1, "/x/run_freight.sh", inc, env_extra=env_extra
                )
                self.assertEqual(rc, 0)
                with open(inc, "r", encoding="utf-8") as fp:
                    rec = json.loads(fp.readline())
                mount_str = rec.get("mount", "")
                # Critical: MOVESPEED line must be in captured field
                self.assertIn(
                    "MOVESPEED",
                    mount_str,
                    f"MOVESPEED missing from captured mount field: {mount_str!r}",
                )
                # Critical: no truncation marker
                self.assertNotIn(
                    "[truncated]",
                    mount_str,
                    "MOVESPEED line should fit in 800 char limit after narrow grep",
                )
                # Critical: must contain the file system descriptor that
                # incident_analyzer's mount-state classifier reads.
                # macOS APFS mount line includes 'apfs' but `mount -v` style
                # would include 'rw'. Verify the analyzer-relevant info survives.
                self.assertTrue(
                    "apfs" in mount_str.lower() or "/Volumes/MOVESPEED" in mount_str,
                    f"Expected MOVESPEED filesystem details preserved, got: {mount_str!r}",
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)
