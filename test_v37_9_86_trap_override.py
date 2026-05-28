"""V37.9.86: bash trap override lockdir 残留修复 — 单测守卫.

根因: bash 的 trap 是覆盖式而非追加式 — 同一信号只能有一个 handler。
当 cron 脚本先设 `trap 'rmdir "$LOCK"' EXIT` 后在 multi-chunk 分支
设 `trap 'rm -rf "$WA_CHUNK_DIR"' EXIT INT TERM` 时，第二个 trap
完全覆盖第一个，导致 lock 不清理。

修复: 合并两个 cleanup 到同一 trap handler。

血案: Mac Mini 5/12 实测发现 4 个 jobs (hf_papers/s2/rss_blogs/github_trending)
lockdir 残留 5h-45h，但脚本推送成功 — trap EXIT 只执行了 WA_CHUNK_DIR 清理。
"""
import os
import re
import glob
import subprocess
import unittest

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))


class TestTrapOverrideFix(unittest.TestCase):
    """12 个有 lockdir + WA_CHUNK_DIR 双 trap 的脚本必须合并 cleanup。"""

    SCRIPTS_WITH_LOCK_AND_CHUNKS = [
        "jobs/acl_anthology/run_acl_anthology.sh",
        "jobs/ai_leaders_x/run_ai_leaders_x.sh",
        "jobs/arxiv_monitor/run_arxiv.sh",
        "jobs/dblp/run_dblp.sh",
        "jobs/github_trending/run_github_trending.sh",
        "jobs/hf_papers/run_hf_papers.sh",
        "jobs/hn_watcher/run_hn_fixed.sh",
        "jobs/karpathy_x/run_karpathy_x.sh",
        "jobs/openclaw_official/run.sh",
        "jobs/openclaw_official/run_discussions.sh",
        "jobs/rss_blogs/run_rss_blogs.sh",
        "jobs/semantic_scholar/run_semantic_scholar.sh",
    ]

    def test_all_scripts_exist(self):
        for script in self.SCRIPTS_WITH_LOCK_AND_CHUNKS:
            path = os.path.join(REPO_ROOT, script)
            self.assertTrue(os.path.exists(path), f"missing: {script}")

    def test_no_standalone_wa_chunk_dir_trap_without_lock_cleanup(self):
        """每个含 WA_CHUNK_DIR trap 的脚本，如果有 lockdir，trap 必须也含 rmdir LOCK。"""
        buggy_pattern = re.compile(
            r"""trap\s+'rm\s+-rf\s+"\$WA_CHUNK_DIR"'\s+EXIT"""
        )
        combined_pattern = re.compile(
            r"""trap\s+'rmdir\s+"\$LOCK"[^']*rm\s+-rf\s+"\$WA_CHUNK_DIR"'\s+EXIT"""
        )
        for script in self.SCRIPTS_WITH_LOCK_AND_CHUNKS:
            path = os.path.join(REPO_ROOT, script)
            with open(path) as f:
                content = f.read()
            has_lock = 'trap' in content and 'rmdir "$LOCK"' in content
            if not has_lock:
                continue
            for ln, line in enumerate(content.split("\n"), 1):
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                if buggy_pattern.search(stripped):
                    self.fail(
                        f"{script}:L{ln} — standalone WA_CHUNK_DIR trap "
                        f"overrides LOCK cleanup (V37.9.86 regression)"
                    )

    def test_combined_trap_includes_both_cleanups(self):
        """含 WA_CHUNK_DIR trap 的行必须也含 rmdir LOCK (当脚本有 lockdir 时)。"""
        for script in self.SCRIPTS_WITH_LOCK_AND_CHUNKS:
            path = os.path.join(REPO_ROOT, script)
            with open(path) as f:
                content = f.read()
            has_lock_trap = bool(re.search(r"trap\s+'rmdir\s+\"\$LOCK\"", content))
            if not has_lock_trap:
                continue
            chunk_traps = []
            for ln, line in enumerate(content.split("\n"), 1):
                if line.strip().startswith("#"):
                    continue
                if "WA_CHUNK_DIR" in line and "trap" in line and "EXIT" in line:
                    chunk_traps.append((ln, line.strip()))
            for ln, line in chunk_traps:
                self.assertIn(
                    'rmdir "$LOCK"', line,
                    f"{script}:L{ln} WA_CHUNK_DIR trap missing LOCK cleanup"
                )

    def test_v37_9_86_marker_present(self):
        """至少 5 个修复文件含 V37.9.86 marker 注释。"""
        count = 0
        for script in self.SCRIPTS_WITH_LOCK_AND_CHUNKS:
            path = os.path.join(REPO_ROOT, script)
            with open(path) as f:
                if "V37.9.86" in f.read():
                    count += 1
        self.assertGreaterEqual(count, 5, "V37.9.86 marker should be in >=5 fixed scripts")

    def test_scripts_count_locked(self):
        """12 个脚本的列表不可悄悄缩减。"""
        self.assertEqual(len(self.SCRIPTS_WITH_LOCK_AND_CHUNKS), 12)


class TestTrapOverrideBehavior(unittest.TestCase):
    """行为层验证: bash trap override 真实行为。"""

    def test_standalone_trap_overrides_previous(self):
        """反向验证: 单独的 trap 确实会覆盖之前的 trap (bash 文档行为)。"""
        script = """#!/bin/bash
LOCK=$(mktemp -d)
trap 'rmdir "$LOCK" 2>/dev/null' EXIT
CHUNK=$(mktemp -d)
trap 'rm -rf "$CHUNK"' EXIT
echo "LOCK=$LOCK"
echo "CHUNK=$CHUNK"
"""
        result = subprocess.run(
            ["bash", "-c", script],
            capture_output=True, text=True, timeout=10
        )
        self.assertEqual(result.returncode, 0)
        lines = result.stdout.strip().split("\n")
        lock_dir = lines[0].split("=", 1)[1]
        chunk_dir = lines[1].split("=", 1)[1]
        self.assertFalse(
            os.path.exists(chunk_dir),
            "CHUNK should be cleaned by second trap"
        )
        lock_exists = os.path.exists(lock_dir)
        if lock_exists:
            os.rmdir(lock_dir)
        self.assertTrue(
            lock_exists,
            "LOCK should still exist — proves bash trap override is real "
            "(second trap replaces first, LOCK cleanup is lost)"
        )

    def test_combined_trap_cleans_both(self):
        """正向验证: 合并 trap 清理两个目录。"""
        script = """#!/bin/bash
LOCK=$(mktemp -d)
trap 'rmdir "$LOCK" 2>/dev/null' EXIT
CHUNK=$(mktemp -d)
trap 'rmdir "$LOCK" 2>/dev/null; rm -rf "$CHUNK"' EXIT INT TERM
echo "LOCK=$LOCK"
echo "CHUNK=$CHUNK"
"""
        result = subprocess.run(
            ["bash", "-c", script],
            capture_output=True, text=True, timeout=10
        )
        self.assertEqual(result.returncode, 0)
        lines = result.stdout.strip().split("\n")
        lock_dir = lines[0].split("=", 1)[1]
        chunk_dir = lines[1].split("=", 1)[1]
        self.assertFalse(
            os.path.exists(chunk_dir),
            "CHUNK should be cleaned"
        )
        self.assertFalse(
            os.path.exists(lock_dir),
            "LOCK should also be cleaned by combined trap"
        )


class TestNoOtherTrapOverrideRisk(unittest.TestCase):
    """全 repo 扫描: 不允许有新的 standalone WA_CHUNK_DIR trap 覆盖 LOCK。"""

    def test_repo_scan_no_standalone_chunk_trap_in_lockdir_scripts(self):
        """全 repo .sh 扫描: 任何含 lockdir + WA_CHUNK_DIR 的脚本不得有 standalone trap。"""
        buggy_re = re.compile(
            r"""^\s*trap\s+'rm\s+-rf\s+"\$WA_CHUNK_DIR"'\s+EXIT"""
        )
        violations = []
        for pattern in ["jobs/*/run_*.sh", "*.sh"]:
            for path in glob.glob(os.path.join(REPO_ROOT, pattern)):
                with open(path) as f:
                    content = f.read()
                has_lock = 'mkdir "$LOCK"' in content or "mkdir $LOCK" in content
                if not has_lock:
                    continue
                for ln, line in enumerate(content.split("\n"), 1):
                    if line.strip().startswith("#"):
                        continue
                    if buggy_re.search(line):
                        rel = os.path.relpath(path, REPO_ROOT)
                        violations.append(f"{rel}:L{ln}")
        self.assertEqual(
            violations, [],
            f"standalone WA_CHUNK_DIR trap overriding LOCK found: {violations}"
        )


if __name__ == "__main__":
    unittest.main()
