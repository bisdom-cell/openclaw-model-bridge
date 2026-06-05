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

# V37.9.110: 隔离 incident 文件, 防失败路径测试污染生产 ~/.kb/movespeed_incidents.jsonl.
# 血案: governance INV-RETRY-001 runtime check 跑本测试 → 15 个失败路径测试经 helper 的
# incident_capture 写真实 ~/.kb/movespeed_incidents.jsonl (caller="caller.sh") → 灌爆
# INV-MOVESPEED-TCC-001 的 24h>2 计数 → 假 CORE governance fail + 假 MOVESPEED watchdog 告警,
# 且每次 audit 重新污染 (永不"老化转绿") + 腐蚀真实 MOVESPEED 取证数据. MR-9 类: 测试写生产状态.
# 所有 helper 调用 (含 incident_capture) 经此 env 重定向到 /tmp throwaway, 绝不碰 ~/.kb.
_ISOLATED_INCIDENT_FILE = os.path.join(tempfile.gettempdir(),
                                       "test_movespeed_rsync_helper_incidents.jsonl")


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


def _make_fake_tmutil(tmpdir, running):
    """Create a fake tmutil in tmpdir/bin emitting 'Running = <running>'.

    Shares the same bin dir as _make_fake_rsync so a single PATH wins both.
    Call this BEFORE _make_fake_rsync and use the latter's returned PATH.
    """
    bin_dir = Path(tmpdir) / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    fake = bin_dir / "tmutil"
    fake.write_text(
        "#!/bin/bash\n"
        'echo "Backup session status:"\n'
        'echo "{"\n'
        f'echo "    Running = {running};"\n'
        'echo "}"\n',
        encoding="utf-8")
    fake.chmod(0o755)
    return str(bin_dir)


def _run_helper(env_extra=None, args=None, timeout=30):
    """Invoke helper, return (returncode, stdout, stderr)."""
    env = os.environ.copy()
    # V37.9.110: isolate incident file (failure-path tests must not pollute the
    # real ~/.kb/movespeed_incidents.jsonl — see _ISOLATED_INCIDENT_FILE note).
    env.setdefault("MOVESPEED_INCIDENT_FILE", _ISOLATED_INCIDENT_FILE)
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
        # V37.9.31: helper exits 0 on all-retry-fail (fail-open contract)
        self.assertEqual(rc, 0,
                         "V37.9.31: helper exits 0 on rsync fail (was rsync exit code)")
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
    """All attempts fail → fail-loud WARN: SSD + V37.9.31 fail-open exit 0.

    V37.9.31: helper now exits 0 on all-retry-fail to preserve caller's
    `set -e` liveness. Fail-loud WARN + capture remain intact. This restores
    the V37.9.4-V37.9.26 invariant where `rsync ... 2>&1 || echo WARN`
    always returned 0 to caller.
    """

    def setUp(self):
        self._td = tempfile.mkdtemp(prefix="rsync_helper_fail_")

    def tearDown(self):
        shutil.rmtree(self._td, ignore_errors=True)

    def test_all_fail_emits_warn_ssd_and_exits_zero_v37_9_31(self):
        """V37.9.31: helper exits 0 (was rsync exit code) — caller's set -e safe."""
        path = _make_fake_rsync(self._td, 'echo "always fail"; exit 23')
        rc, _, err = _run_helper(
            env_extra={
                "PATH": path,
                "MOVESPEED_RSYNC_NO_SLEEP": "1",
                "MOVESPEED_RSYNC_BACKOFF_BASE": "0",
            },
            args=["/test/caller.sh", "--", "-a", "/src/", "/dst/"],
        )
        self.assertEqual(
            rc, 0,
            "V37.9.31: helper MUST exit 0 even when rsync fails (fail-open). "
            "If non-zero, callers with `set -e` (20 sites) will be killed "
            "mid-script — that's the V37.9.27 regression fixed in V37.9.31.",
        )
        # Fail-loud signals MUST still be present — observability unchanged
        self.assertIn("WARN: SSD", err, "INV-BACKUP-001 WARN string contract")
        self.assertIn("after 3 retries", err)
        self.assertIn("exit=23", err, "rsync exit code reported in WARN message")

    def test_caller_with_set_e_survives_rsync_failure_v37_9_31(self):
        """V37.9.31: simulate `set -e` caller; helper must NOT kill it."""
        path = _make_fake_rsync(self._td, 'echo "always fail"; exit 12')
        # Build a minimal caller that uses set -e and expects to continue
        caller_script = Path(self._td) / "caller.sh"
        caller_script.write_text(textwrap.dedent("""\
            #!/usr/bin/env bash
            set -eo pipefail
            bash {helper} "$0" -- -a /src/ /dst/
            # If we reach here, set -e didn't kill us — V37.9.31 contract holds
            echo "POST_RSYNC_REACHED"
            exit 99
        """).format(helper=str(HELPER)), encoding="utf-8")
        caller_script.chmod(0o755)
        proc = subprocess.run(
            ["bash", str(caller_script)],
            capture_output=True,
            text=True,
            env={
                **os.environ,
                "PATH": path,
                "MOVESPEED_RSYNC_NO_SLEEP": "1",
                "MOVESPEED_RSYNC_BACKOFF_BASE": "0",
                "MOVESPEED_INCIDENT_FILE": _ISOLATED_INCIDENT_FILE,  # V37.9.110 隔离
            },
            timeout=30,
        )
        self.assertIn(
            "POST_RSYNC_REACHED",
            proc.stdout,
            "V37.9.31 fail-open: caller's set -e MUST NOT kill it on rsync fail. "
            "If POST_RSYNC_REACHED missing, V37.9.27 regression has returned.",
        )
        self.assertEqual(
            proc.returncode, 99,
            "Caller's own exit code (99) propagates, not rsync's (12)",
        )


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
            "MOVESPEED_INCIDENT_FILE": _ISOLATED_INCIDENT_FILE,  # V37.9.110 隔离
        })
        proc = subprocess.run(
            ["bash", str(helper_dir / "movespeed_rsync_helper.sh"),
             "/some/caller.sh", "--", "-a", "/src/", "/dst/"],
            capture_output=True, text=True, env=env, timeout=15,
        )
        # V37.9.31: helper now exits 0 (fail-open) — capture helper still invoked
        self.assertEqual(
            proc.returncode, 0,
            "V37.9.31: helper exits 0 fail-open (was non-zero); capture still runs",
        )
        self.assertTrue(marker.exists(),
            f"Capture helper should be invoked on final failure. stderr={proc.stderr}")
        marker_content = marker.read_text()
        self.assertIn("/some/caller.sh", marker_content)


class TestSitesMigrationGuard(unittest.TestCase):
    """V37.9.27 — All 20 EXPECTED sites must use new helper (no legacy pattern)."""

    EXPECTED_SITES = [
        "kb_save_arxiv.sh", "kb_dream.sh", "kb_evening.sh", "kb_inject.sh",
        "kb_review.sh", "kb_deep_dive.sh", "jobs/hn_watcher/run_hn_fixed.sh",
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


class TestTmutilPreCheck(unittest.TestCase):
    """V37.9.106 Phase 0: Time Machine 备份预检 (TM 争用是 36 incidents 主因)."""

    def setUp(self):
        self._td = tempfile.mkdtemp(prefix="rsync_helper_tmutil_")

    def tearDown(self):
        shutil.rmtree(self._td, ignore_errors=True)

    def _run(self, running, skip_env=None, extra_env=None):
        """注入 fake tmutil (Running=<running>) + fake rsync (touch marker), 跑 helper."""
        marker = Path(self._td) / "rsync_called"
        _make_fake_tmutil(self._td, running=running)
        path = _make_fake_rsync(self._td, f'touch "{marker}"; echo ok; exit 0')
        env = {"PATH": path, "MOVESPEED_RSYNC_NO_SLEEP": "1"}
        if skip_env is not None:
            env["MOVESPEED_RSYNC_SKIP_TMUTIL_CHECK"] = skip_env
        if extra_env:
            env.update(extra_env)
        rc, out, err = _run_helper(
            env_extra=env, args=["/test/caller.sh", "--", "-a", "/src/", "/dst/"])
        return rc, out, err, marker

    def test_tm_running_skips_rsync(self):
        """TM 备份中 (Running=1) → 跳过 rsync, exit 0, 不调 rsync."""
        rc, out, err, marker = self._run(running=1)
        self.assertEqual(rc, 0, "TM 备份中跳过应 exit 0 (fail-open)")
        self.assertIn("Time Machine", err)
        self.assertFalse(marker.exists(),
            "TM 备份进行中 rsync 不应被调用 (跳过避 EOF 争用)")

    def test_tm_idle_proceeds_to_rsync(self):
        """TM 空闲 (Running=0) → 正常调 rsync."""
        rc, out, err, marker = self._run(running=0)
        self.assertEqual(rc, 0)
        self.assertTrue(marker.exists(),
            "TM 空闲时 rsync 应正常被调用")
        self.assertNotIn("Time Machine 备份进行中", err)

    def test_skip_env_bypasses_tmutil_check(self):
        """MOVESPEED_RSYNC_SKIP_TMUTIL_CHECK=1 → 即使 TM 备份中也跑 rsync (测试逃生口)."""
        rc, out, err, marker = self._run(running=1, skip_env="1")
        self.assertEqual(rc, 0)
        self.assertTrue(marker.exists(),
            "SKIP=1 应绕过 tmutil 预检直接跑 rsync")
        self.assertNotIn("Time Machine 备份进行中", err)

    def test_no_tmutil_proceeds_fail_open(self):
        """无 tmutil (非 macOS / dev Linux) → command -v 失败 → 照常 rsync (FAIL-OPEN)."""
        # 不注入 fake tmutil, 但 PATH 仅含 fake rsync bin (无 tmutil)
        marker = Path(self._td) / "rsync_called"
        path = _make_fake_rsync(self._td, f'touch "{marker}"; echo ok; exit 0')
        # 用最小 PATH (仅 fake bin + 基础), 确保无真 tmutil (Linux dev 本就无)
        rc, out, err = _run_helper(
            env_extra={"PATH": path, "MOVESPEED_RSYNC_NO_SLEEP": "1"},
            args=["/test/caller.sh", "--", "-a", "/src/", "/dst/"])
        self.assertEqual(rc, 0)
        self.assertTrue(marker.exists(),
            "无 tmutil 时应 FAIL-OPEN 照常 rsync (跨平台)")


class TestTmutilSourceGuards(unittest.TestCase):
    """V37.9.106 源码级守卫."""

    @classmethod
    def setUpClass(cls):
        cls.src = HELPER.read_text(encoding="utf-8")

    def test_v37_9_106_marker(self):
        self.assertIn("V37.9.106", self.src, "Phase 0 marker 便于追溯")

    def test_phase0_before_phase1(self):
        """Phase 0 tmutil 预检必须在 Phase 1 jitter 之前 (用 ── section header 定位代码段)."""
        p0 = self.src.find("── Phase 0")
        p1 = self.src.find("── Phase 1")
        self.assertGreater(p0, 0, "Phase 0 section header 必须存在")
        self.assertGreater(p1, p0, "Phase 0 必须在 Phase 1 之前")

    def test_command_v_tmutil_guard(self):
        """跨平台: command -v tmutil 守卫 (非 macOS / 缺 tmutil → 跳过预检)."""
        self.assertIn("command -v tmutil", self.src)

    def test_skip_env_override(self):
        self.assertIn("MOVESPEED_RSYNC_SKIP_TMUTIL_CHECK", self.src)

    def test_running_pattern(self):
        self.assertIn("Running = 1", self.src,
            "必须检测 tmutil status 的 Running = 1")

    def test_skip_is_fail_open_exit_0(self):
        """TM 备份中跳过分支必须 exit 0 (fail-open, 不算 incident)."""
        idx = self.src.find("Time Machine 备份进行中")
        self.assertGreater(idx, 0)
        after = self.src[idx:idx + 200]
        self.assertIn("exit 0", after, "跳过分支必须 exit 0")


class TestV37_9_110_IncidentFileIsolation(unittest.TestCase):
    """V37.9.110: 失败路径测试必须隔离 incident 文件, 不得污染生产 ~/.kb/movespeed_incidents.jsonl.

    血案: governance INV-RETRY-001 runtime check 跑本测试 → 失败路径测试经 helper 的
    incident_capture 写真实 ~/.kb (caller="caller.sh") → 灌爆 INV-MOVESPEED-TCC-001 24h>2
    → 假 CORE governance fail + 假 MOVESPEED 告警, 且每次 audit 重新污染 (永不老化转绿).
    MR-9 类 (测试写生产状态). 反向: 移除隔离 → Mac Mini 真实 ~/.kb 文件会增长 → 行为测试 fail.
    """

    # 注: 用 inspect.getsource 只查目标函数体, 避免"守卫自己的断言字符串污染 self.src"
    # 的自引用陷阱 (V37.9.110 reverse-validation 抓到的真坑).

    def test_isolated_file_uses_tempdir_not_real_kb(self):
        import test_movespeed_rsync_helper as mod
        iso = mod._ISOLATED_INCIDENT_FILE
        self.assertIn(tempfile.gettempdir(), iso, "隔离文件必须在 /tmp throwaway")
        self.assertNotIn("/.kb/", iso, "隔离文件绝不能指向真实 ~/.kb")

    def test_run_helper_setdefaults_incident_file(self):
        """_run_helper 函数体必须 setdefault MOVESPEED_INCIDENT_FILE (隔离所有 _run_helper 调用)."""
        import inspect
        import test_movespeed_rsync_helper as mod
        fn_src = inspect.getsource(mod._run_helper)
        self.assertIn("MOVESPEED_INCIDENT_FILE", fn_src,
                      "_run_helper 必须设 MOVESPEED_INCIDENT_FILE")
        self.assertIn("setdefault", fn_src)
        self.assertIn("_ISOLATED_INCIDENT_FILE", fn_src)

    def test_inline_subprocess_calls_isolated(self):
        """2 个绕过 _run_helper 的 inline subprocess (test_caller_with_set_e +
        TestCaptureHelperWired) 的函数体也必须含 MOVESPEED_INCIDENT_FILE 隔离."""
        import inspect
        import test_movespeed_rsync_helper as mod
        for fn in (TestRsyncAllRetriesFail.test_caller_with_set_e_survives_rsync_failure_v37_9_31,
                   mod.TestCaptureHelperWired.test_capture_helper_invoked_on_all_fail):
            fn_src = inspect.getsource(fn)
            self.assertIn("MOVESPEED_INCIDENT_FILE", fn_src,
                          f"{fn.__name__} 的 inline env 必须含 MOVESPEED_INCIDENT_FILE 隔离")

    def test_failure_path_does_not_grow_real_kb_incidents(self):
        """端到端反向验证: 跑失败 helper 后, 真实 ~/.kb/movespeed_incidents.jsonl 不增长.
        dev 无 ~/.kb capture → 平凡通过; Mac Mini 真激活 → 隔离移除则此测试 fail (文件增长)."""
        real_inc = os.path.expanduser("~/.kb/movespeed_incidents.jsonl")
        before = os.path.getsize(real_inc) if os.path.isfile(real_inc) else -1
        with tempfile.TemporaryDirectory() as td:
            path = _make_fake_rsync(td, 'echo "always fail"; exit 23')
            _run_helper(
                env_extra={"PATH": path, "MOVESPEED_RSYNC_NO_SLEEP": "1",
                           "MOVESPEED_RSYNC_BACKOFF_BASE": "0"},
                args=["/test/caller.sh", "--", "-a", "/src/", "/dst/"])
        after = os.path.getsize(real_inc) if os.path.isfile(real_inc) else -1
        self.assertEqual(before, after,
            "V37.9.110: 失败路径测试不得改动真实 ~/.kb/movespeed_incidents.jsonl "
            "(隔离移除 → governance INV-RETRY-001 跑本测试会污染 → 假 CORE 告警)")


if __name__ == "__main__":
    unittest.main(verbosity=2)
