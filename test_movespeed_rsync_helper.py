"""V37.9.27 — movespeed_rsync_helper.sh 单测 (jitter + retry + fail-loud + capture).

Coverage matrix:
  TestUsageErrors         — Missing args / missing -- separator
  TestEnvOverrides        — NO_SLEEP / NO_RETRY / MAX_ATTEMPTS / BACKOFF_BASE
  TestRsyncSuccess        — Single attempt success path (no retry needed)
  TestRsyncRetryRecovers  — First N attempts fail, last succeeds
  TestRsyncAllRetriesFail — All attempts fail → fail-loud + capture invocation
  TestCaptureHelperWired  — Verify capture helper is invoked on final failure
  TestSitesMigrationGuard — All 20 EXPECTED sites use helper (no legacy pattern)
  TestSourceLevelGuards   — Helper file structural literal guards

设计契约 (按原则 #28 防退化):
  - Helper 是 shell 脚本, 不能直接 import; 测试通过 subprocess 端到端跑
  - 用 PATH 注入 fake rsync 控制 rsync 行为 (success / fail / fail-then-success)
  - MOVESPEED_RSYNC_NO_SLEEP=1 跳过 30-180s jitter (确定性测试)
  - tempfile + tmpdir 隔离 (不污染真实 ~/.kb/)
"""

import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
HELPER = REPO_ROOT / "movespeed_rsync_helper.sh"


def _make_fake_rsync(tmpdir, behavior_script):
    """Create a fake rsync binary in tmpdir/bin that follows behavior_script.

    behavior_script: str — bash code body that emits stdout/stderr + exit code.
                     Receives rsync args as $1, $2, ... (transparent passthrough not needed).
    Returns: PATH string with tmpdir/bin prepended.
    """
    bin_dir = Path(tmpdir) / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    fake = bin_dir / "rsync"
    fake.write_text("#!/bin/bash\n" + behavior_script + "\n", encoding="utf-8")
    fake.chmod(0o755)
    # Prepend tmpdir/bin so fake rsync wins over real one
    return f"{bin_dir}:{os.environ.get('PATH', '')}"


def _run_helper(env_extra=None, args=None, timeout=30):
    """Invoke helper, return (returncode, stdout, stderr)."""
    env = os.environ.copy()
    if env_extra:
        env.update(env_extra)
    cmd = ["bash", str(HELPER)] + (args or [])
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=timeout)
    return proc.returncode, proc.stdout, proc.stderr


# ── tests ──────────────────────────────────────────────────────────────────

class TestUsageErrors(unittest.TestCase):
    """Missing args / missing -- separator → exit 2."""

    def test_no_args_returns_2(self):
        rc, _, err = _run_helper()
        self.assertEqual(rc, 2)
        self.assertIn("usage", err.lower())

    def test_only_caller_no_separator(self):
        rc, _, err = _run_helper(args=["/some/caller.sh"])
        self.assertEqual(rc, 2)

    def test_missing_separator_returns_2(self):
        rc, _, err = _run_helper(args=["/caller.sh", "-a", "/src", "/dst"])
        self.assertEqual(rc, 2)
        self.assertIn("missing -- separator", err)


class TestEnvOverrides(unittest.TestCase):
    """Verify env overrides take effect (NO_SLEEP / NO_RETRY / MAX_ATTEMPTS / BACKOFF_BASE)."""

    def setUp(self):
        self._td = tempfile.mkdtemp(prefix="rsync_helper_env_")

    def tearDown(self):
        shutil.rmtree(self._td, ignore_errors=True)

    def test_no_sleep_skips_jitter_phase(self):
        """NO_SLEEP=1 → no '错峰 sleep' message in stderr."""
        path = _make_fake_rsync(self._td, 'echo "fake rsync ok"; exit 0')
        rc, out, err = _run_helper(
            env_extra={
                "PATH": path,
                "MOVESPEED_RSYNC_NO_SLEEP": "1",
                "MOVESPEED_RSYNC_NO_RETRY": "1",
            },
            args=["/test/caller.sh", "--", "-a", "/src/", "/dst/"],
        )
        self.assertEqual(rc, 0)
        self.assertNotIn("错峰 sleep", err,
            "MOVESPEED_RSYNC_NO_SLEEP=1 should skip jitter")

    def test_no_retry_runs_only_once(self):
        """NO_RETRY=1 → MAX_ATTEMPTS=1 → only 1 invocation."""
        # Fake rsync that always fails + counts invocations via /tmp file
        count_file = Path(self._td) / "count"
        count_file.write_text("0", encoding="utf-8")
        path = _make_fake_rsync(self._td, textwrap.dedent(f"""
            n=$(cat {count_file})
            n=$((n + 1))
            echo $n > {count_file}
            echo "fake rsync attempt $n"
            exit 1
        """))
        rc, _, _ = _run_helper(
            env_extra={
                "PATH": path,
                "MOVESPEED_RSYNC_NO_SLEEP": "1",
                "MOVESPEED_RSYNC_NO_RETRY": "1",
            },
            args=["/test/caller.sh", "--", "-a", "/src/", "/dst/"],
        )
        self.assertEqual(rc, 1)
        attempts = int(count_file.read_text().strip())
        self.assertEqual(attempts, 1, "NO_RETRY=1 should run rsync exactly once")

    def test_max_attempts_override(self):
        """MAX_ATTEMPTS=2 → exactly 2 invocations on persistent failure."""
        count_file = Path(self._td) / "count"
        count_file.write_text("0", encoding="utf-8")
        path = _make_fake_rsync(self._td, textwrap.dedent(f"""
            n=$(cat {count_file})
            n=$((n + 1))
            echo $n > {count_file}
            exit 1
        """))
        _, _, _ = _run_helper(
            env_extra={
                "PATH": path,
                "MOVESPEED_RSYNC_NO_SLEEP": "1",
                "MOVESPEED_RSYNC_MAX_ATTEMPTS": "2",
                "MOVESPEED_RSYNC_BACKOFF_BASE": "0",  # speed up test
            },
            args=["/test/caller.sh", "--", "-a", "/src/", "/dst/"],
        )
        attempts = int(count_file.read_text().strip())
        self.assertEqual(attempts, 2)

    def test_max_attempts_clamped_to_safe_range(self):
        """MAX_ATTEMPTS=999 should be clamped to 10 (defensive)."""
        count_file = Path(self._td) / "count"
        count_file.write_text("0", encoding="utf-8")
        path = _make_fake_rsync(self._td, textwrap.dedent(f"""
            n=$(cat {count_file})
            n=$((n + 1))
            echo $n > {count_file}
            exit 1
        """))
        _run_helper(
            env_extra={
                "PATH": path,
                "MOVESPEED_RSYNC_NO_SLEEP": "1",
                "MOVESPEED_RSYNC_MAX_ATTEMPTS": "999",
                "MOVESPEED_RSYNC_BACKOFF_BASE": "0",
            },
            args=["/test/caller.sh", "--", "-a", "/src/", "/dst/"],
            timeout=60,
        )
        attempts = int(count_file.read_text().strip())
        self.assertLessEqual(attempts, 10,
            "MAX_ATTEMPTS clamped to 10 to prevent runaway loops")

    def test_invalid_max_attempts_falls_back_to_3(self):
        """Non-numeric MAX_ATTEMPTS → fall back to default 3."""
        count_file = Path(self._td) / "count"
        count_file.write_text("0", encoding="utf-8")
        path = _make_fake_rsync(self._td, textwrap.dedent(f"""
            n=$(cat {count_file}); n=$((n + 1)); echo $n > {count_file}; exit 1
        """))
        _run_helper(
            env_extra={
                "PATH": path,
                "MOVESPEED_RSYNC_NO_SLEEP": "1",
                "MOVESPEED_RSYNC_MAX_ATTEMPTS": "not_a_number",
                "MOVESPEED_RSYNC_BACKOFF_BASE": "0",
            },
            args=["/test/caller.sh", "--", "-a", "/src/", "/dst/"],
        )
        attempts = int(count_file.read_text().strip())
        self.assertEqual(attempts, 3)


class TestRsyncSuccess(unittest.TestCase):
    """Single attempt success path."""

    def setUp(self):
        self._td = tempfile.mkdtemp(prefix="rsync_helper_ok_")

    def tearDown(self):
        shutil.rmtree(self._td, ignore_errors=True)

    def test_first_attempt_success_exits_0(self):
        path = _make_fake_rsync(self._td, 'echo "rsync output"; exit 0')
        rc, out, err = _run_helper(
            env_extra={
                "PATH": path,
                "MOVESPEED_RSYNC_NO_SLEEP": "1",
            },
            args=["/test/caller.sh", "--", "-a", "/src/", "/dst/"],
        )
        self.assertEqual(rc, 0)
        # Successful first attempt should NOT print 'recovered'
        self.assertNotIn("recovered", err)
        # Should NOT print fail-loud WARN: SSD
        self.assertNotIn("WARN: SSD", err)


class TestRsyncRetryRecovers(unittest.TestCase):
    """First N attempts fail, last succeeds → exit 0 + 'recovered' message."""

    def setUp(self):
        self._td = tempfile.mkdtemp(prefix="rsync_helper_retry_")

    def tearDown(self):
        shutil.rmtree(self._td, ignore_errors=True)

    def test_third_attempt_recovers(self):
        """Fail twice, succeed on 3rd → exit 0 + 'recovered on attempt 3' message."""
        count_file = Path(self._td) / "count"
        count_file.write_text("0", encoding="utf-8")
        path = _make_fake_rsync(self._td, textwrap.dedent(f"""
            n=$(cat {count_file})
            n=$((n + 1))
            echo $n > {count_file}
            if [ "$n" -lt 3 ]; then
                exit 1
            fi
            echo "rsync attempt $n succeeded"
            exit 0
        """))
        rc, _, err = _run_helper(
            env_extra={
                "PATH": path,
                "MOVESPEED_RSYNC_NO_SLEEP": "1",
                "MOVESPEED_RSYNC_BACKOFF_BASE": "0",  # zero backoff for test speed
            },
            args=["/test/caller.sh", "--", "-a", "/src/", "/dst/"],
        )
        self.assertEqual(rc, 0)
        self.assertIn("recovered on attempt 3", err)
        # Should NOT print fail-loud WARN: SSD on success
        self.assertNotIn("WARN: SSD", err)
        attempts = int(count_file.read_text().strip())
        self.assertEqual(attempts, 3)

    def test_second_attempt_recovers(self):
        """Fail once, succeed on 2nd."""
        count_file = Path(self._td) / "count"
        count_file.write_text("0", encoding="utf-8")
        path = _make_fake_rsync(self._td, textwrap.dedent(f"""
            n=$(cat {count_file}); n=$((n + 1)); echo $n > {count_file}
            [ "$n" -lt 2 ] && exit 1 || exit 0
        """))
        rc, _, err = _run_helper(
            env_extra={
                "PATH": path,
                "MOVESPEED_RSYNC_NO_SLEEP": "1",
                "MOVESPEED_RSYNC_BACKOFF_BASE": "0",
            },
            args=["/test/caller.sh", "--", "-a", "/src/", "/dst/"],
        )
        self.assertEqual(rc, 0)
        self.assertIn("recovered on attempt 2", err)


class TestRsyncAllRetriesFail(unittest.TestCase):
    """All attempts fail → fail-loud WARN: SSD + exit non-zero."""

    def setUp(self):
        self._td = tempfile.mkdtemp(prefix="rsync_helper_fail_")

    def tearDown(self):
        shutil.rmtree(self._td, ignore_errors=True)

    def test_all_fail_emits_warn_ssd_and_exits_nonzero(self):
        path = _make_fake_rsync(self._td, 'echo "always fail"; exit 23')
        rc, _, err = _run_helper(
            env_extra={
                "PATH": path,
                "MOVESPEED_RSYNC_NO_SLEEP": "1",
                "MOVESPEED_RSYNC_BACKOFF_BASE": "0",
            },
            args=["/test/caller.sh", "--", "-a", "/src/", "/dst/"],
        )
        self.assertEqual(rc, 23, "Exit code should propagate from rsync")
        self.assertIn("WARN: SSD", err)
        self.assertIn("after 3 retries", err)
        self.assertIn("exit=23", err)


class TestCaptureHelperWired(unittest.TestCase):
    """Verify capture helper invoked on final failure (V37.9.14 contract)."""

    def setUp(self):
        self._td = tempfile.mkdtemp(prefix="rsync_helper_capture_")

    def tearDown(self):
        shutil.rmtree(self._td, ignore_errors=True)

    def test_capture_helper_invoked_on_all_fail(self):
        """All retries fail → capture helper called with exit code + caller."""
        # Stage a tmp dir with custom SCRIPT_DIR resolution:
        # We override CAPTURE_HELPER detection by placing a fake helper next
        # to a copy of the rsync helper.
        td = Path(self._td)
        helper_dir = td / "scripts"
        helper_dir.mkdir()
        # Copy helper script
        shutil.copy(HELPER, helper_dir / "movespeed_rsync_helper.sh")
        # Create marker capture helper next to it
        marker = td / "capture_invoked"
        capture_stub = helper_dir / "movespeed_incident_capture.sh"
        capture_stub.write_text(
            f'#!/bin/bash\necho "captured exit=$1 caller=$2" > {marker}\nexit 0\n',
            encoding="utf-8",
        )
        capture_stub.chmod(0o755)
        # Fake always-fail rsync
        path = _make_fake_rsync(self._td, 'exit 1')
        rc, _, err = _run_helper(
            env_extra={
                "PATH": path,
                "MOVESPEED_RSYNC_NO_SLEEP": "1",
                "MOVESPEED_RSYNC_BACKOFF_BASE": "0",
                "MOVESPEED_RSYNC_NO_RETRY": "1",
            },
            args=[],  # we'll override below
            timeout=15,
        )
        # Direct invocation with custom helper path
        env = os.environ.copy()
        env.update({
            "PATH": path,
            "MOVESPEED_RSYNC_NO_SLEEP": "1",
            "MOVESPEED_RSYNC_BACKOFF_BASE": "0",
            "MOVESPEED_RSYNC_NO_RETRY": "1",
        })
        proc = subprocess.run(
            ["bash", str(helper_dir / "movespeed_rsync_helper.sh"),
             "/some/caller.sh", "--", "-a", "/src/", "/dst/"],
            capture_output=True, text=True, env=env, timeout=15,
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertTrue(marker.exists(),
            f"Capture helper should be invoked on final failure. stderr={proc.stderr}")
        marker_content = marker.read_text()
        self.assertIn("/some/caller.sh", marker_content)


class TestSitesMigrationGuard(unittest.TestCase):
    """V37.9.27 — All 20 EXPECTED sites must use new helper (no legacy pattern)."""

    EXPECTED_SITES = [
        "kb_save_arxiv.sh", "kb_dream.sh", "kb_evening.sh", "kb_inject.sh",
        "kb_review.sh", "kb_deep_dive.sh", "run_hn_fixed.sh",
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

    def test_every_site_invokes_helper(self):
        missing = []
        for rel in self.EXPECTED_SITES:
            p = REPO_ROOT / rel
            if not p.exists():
                missing.append(f"{rel} (file missing)")
                continue
            content = p.read_text(encoding="utf-8")
            if "movespeed_rsync_helper.sh" not in content:
                missing.append(rel)
        self.assertEqual([], missing,
            f"V37.9.27: {len(missing)} sites missing helper invocation: {missing}")

    def test_no_site_has_legacy_inline_capture_pattern(self):
        """Legacy pattern: rsync ... 2>&1 || { _rc=$?; ... incident_capture; }
        Must be fully replaced by helper invocation. V37.9.27 forward."""
        legacy_found = []
        for rel in self.EXPECTED_SITES:
            p = REPO_ROOT / rel
            if not p.exists():
                continue
            content = p.read_text(encoding="utf-8")
            # Detect legacy pattern: rsync followed by inline capture call
            # (helper file itself doesn't have rsync calls outside its own logic)
            for line in content.split("\n"):
                if ("rsync" in line and "2>&1" in line and
                        "movespeed_incident_capture.sh" in line and
                        "movespeed_rsync_helper.sh" not in line):
                    legacy_found.append(f"{rel}: {line.strip()[:80]}...")
        self.assertEqual([], legacy_found,
            f"V37.9.27 legacy pattern still present in {len(legacy_found)} sites: {legacy_found}")


class TestSourceLevelGuards(unittest.TestCase):
    """V37.9.27 — Helper structural guards (literal grep)."""

    @classmethod
    def setUpClass(cls):
        cls.helper_src = HELPER.read_text(encoding="utf-8")

    def test_helper_has_v37_9_27_marker(self):
        self.assertIn("V37.9.27", self.helper_src)

    def test_helper_has_jitter_range_30_180s(self):
        """Phase 1: 30-180s jitter (not 5-15min as initially considered)."""
        self.assertIn("JITTER_S=$((30 + RANDOM % 151))", self.helper_src,
            "Jitter must be 30-180s (30 + 0..150)")

    def test_helper_has_max_attempts_3_default(self):
        self.assertIn('MAX_ATTEMPTS="${MOVESPEED_RSYNC_MAX_ATTEMPTS:-3}"', self.helper_src)

    def test_helper_has_backoff_base_10_default(self):
        self.assertIn('BACKOFF_BASE="${MOVESPEED_RSYNC_BACKOFF_BASE:-10}"', self.helper_src)

    def test_helper_clamps_max_attempts_to_safe_range(self):
        """Defensive: prevent runaway loops on env typo."""
        self.assertIn("MAX_ATTEMPTS=10", self.helper_src,
            "Must clamp MAX_ATTEMPTS to 10 (defensive)")

    def test_helper_emits_warn_ssd_on_failure(self):
        """V37.9.4 INV-BACKUP-001 contract: WARN: SSD literal in stderr."""
        self.assertIn('WARN: SSD rsync failed', self.helper_src)

    def test_helper_invokes_capture_on_failure(self):
        """V37.9.14 INV-BACKUP-001 check 4 contract: capture helper invoked."""
        self.assertIn("movespeed_incident_capture.sh", self.helper_src)

    def test_helper_writes_diagnostics_to_stderr(self):
        """MR-11: log/debug to stderr, not stdout (avoid command substitution pollution)."""
        # All echo-to-stderr lines should have >&2
        # Sample check: jitter message must go to stderr
        for line in self.helper_src.split("\n"):
            stripped = line.strip()
            if stripped.startswith('echo "[$(basename') or stripped.startswith('echo "['):
                if "WARN" in stripped or "retry" in stripped or "recovered" in stripped or "错峰" in stripped or "missing --" in stripped:
                    self.assertTrue(">&2" in stripped or "stderr" in stripped,
                        f"Diagnostic line should write to stderr (MR-11): {stripped[:80]}")

    def test_helper_executable(self):
        self.assertTrue(os.access(HELPER, os.X_OK),
            "Helper must be executable")


if __name__ == "__main__":
    unittest.main(verbosity=2)
