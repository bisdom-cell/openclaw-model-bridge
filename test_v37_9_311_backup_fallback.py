#!/usr/bin/env python3
"""V37.9.311 (2026-08-17) — 备份 fallback 落盘路径假设错误（Mac Mini 日志实证驱动）。

实证（2026-08-17 10:59 Mac Mini openclaw_backup.log）:
    Error: ENOENT: ... '/Users/bisdom/.openclaw/plugin-runtime-deps/
           openclaw-2026.4.27-.../.openclaw-runtime-deps.lock'
    WARN: --output failed, trying default location
    Backup archive: /Users/bisdom/openclaw-model-bridge/2026-08-17T02-59-18.213Z-openclaw-backup.tar.gz
    Created      : /Users/bisdom/openclaw-model-bridge/2026-08-17T02-59-18.213Z-openclaw-backup.tar.gz
    ERROR: backup created but file not found

两个真实后果:
  1. 旧代码去 `~/.openclaw/backups/*.tar.gz` 找, 而 4.27 实际写到**当前工作目录**,
     文件名 `<ISO8601>-openclaw-backup.tar.gz` → fallback 必然"找不到"→ exit 2 假失败,
     而备份其实已经生成 (同族: 缺工具/错路径伪装成否定结果, V37.9.310/288)。
  2. cron 的 CWD 是仓库目录 → 留下 1.0GB 未被 gitignore 的孤儿档, 且内含 ~/.openclaw
     凭据 → 误提交即凭据泄漏。

修法: 让 CLI 在 SSD 的 BACKUP_DIR 内生成 (subshell cd), 再 mv 成规范名。
"""

import fnmatch
import os
import re
import subprocess
import unittest

REPO = os.path.dirname(os.path.abspath(__file__))

# 4.27 CLI 无 --output 时的真实产物名（取自 Mac Mini 日志）
_CLI_DEFAULT_NAME = "2026-08-17T02-59-18.213Z-openclaw-backup.tar.gz"
# 脚本自己的规范名
_CANONICAL_NAME = "openclaw-backup-2026-08-17.tar.gz"
_FALLBACK_GLOB = "*-openclaw-backup.tar.gz"


def _read(name):
    with open(os.path.join(REPO, name), encoding="utf-8") as fh:
        return fh.read()


class TestFallbackGlob(unittest.TestCase):
    """glob 必须认出 CLI 产物、且不误伤脚本自己的规范名。"""

    def test_matches_cli_default_name(self):
        self.assertTrue(fnmatch.fnmatch(_CLI_DEFAULT_NAME, _FALLBACK_GLOB))

    def test_does_not_match_canonical_name(self):
        """否则 fallback 会把上一次的成品当成本次新产物 mv 走。"""
        self.assertFalse(fnmatch.fnmatch(_CANONICAL_NAME, _FALLBACK_GLOB))

    def test_script_uses_that_glob(self):
        src = _read("openclaw_backup.sh")
        self.assertIn(f'"$BACKUP_DIR"/{_FALLBACK_GLOB}', src)


class TestFallbackWritesToSsdNotCwd(unittest.TestCase):
    """核心血案: fallback 不得再污染 CWD（cron 下 = 仓库目录）。"""

    def setUp(self):
        self.src = _read("openclaw_backup.sh")

    def test_cli_invoked_inside_backup_dir(self):
        self.assertRegex(
            self.src,
            r'\(cd "\$BACKUP_DIR" && openclaw backup create --no-include-workspace\)',
            "fallback 必须在 BACKUP_DIR 内调用 CLI（否则写进 CWD=仓库目录）",
        )

    def test_wrong_path_assumption_retired(self):
        self.assertNotIn(
            "~/.openclaw/backups/*.tar.gz",
            self.src,
            "退役: 4.27 并不写到 ~/.openclaw/backups/（2026-08-17 日志实证）",
        )

    def test_bare_cli_call_without_cd_retired(self):
        """反模式: 不带 cd 的裸调用会落到 CWD。"""
        self.assertNotRegex(
            self.src,
            r"(?m)^\s*if openclaw backup create --no-include-workspace >> ",
            "退役: 无 cd 的裸 fallback 调用",
        )

    def test_uses_mv_not_cp(self):
        """同盘 mv = 秒级改名且不占双份空间（1GB 档 × 2 会挤爆盘）。"""
        self.assertRegex(self.src, r'if ! mv "\$LATEST" "\$BACKUP_FILE"')


class TestArchivesNeverEnterGit(unittest.TestCase):
    """纵深防御: 任一命名的备份档都不得被 git 追踪（内含 ~/.openclaw 凭据）。"""

    def _ignored(self, name):
        r = subprocess.run(
            ["git", "check-ignore", "-q", name],
            cwd=REPO, capture_output=True,
        )
        return r.returncode == 0

    def test_cli_default_name_ignored(self):
        self.assertTrue(self._ignored(_CLI_DEFAULT_NAME), "CLI 时间戳命名未被 gitignore")

    def test_canonical_name_ignored(self):
        self.assertTrue(self._ignored(_CANONICAL_NAME), "规范命名未被 gitignore")

    def test_gitignore_documents_reason(self):
        gi = _read(".gitignore")
        self.assertIn("V37.9.311", gi)
        self.assertIn("凭据", gi, "须记录为何不能入库（凭据泄漏面）")


class TestFailureStillLoud(unittest.TestCase):
    """修复不得削弱既有的失败可见性（V37.9.304 C6 谱系）。"""

    def setUp(self):
        self.src = _read("openclaw_backup.sh")

    def test_not_found_branch_still_exits_nonzero(self):
        self.assertRegex(
            self.src,
            r'ERROR: backup created but file not found in \$BACKUP_DIR"(?:.|\n)*?exit 2',
        )

    def test_error_lines_match_watchdog_pattern(self):
        """新增/改写的 ERROR 行必须仍被 job_watchdog 扫到。"""
        wd = _read("job_watchdog.sh")
        m = re.search(r"local err_pattern='([^']+)'", wd)
        self.assertIsNotNone(m)
        pat = re.compile(m.group(1))
        errs = [ln for ln in self.src.splitlines() if "ERROR:" in ln and "echo" in ln]
        self.assertTrue(errs, "未找到 ERROR 行")
        for ln in errs:
            self.assertTrue(pat.search(ln), f"该 ERROR 行不匹配 watchdog err_pattern: {ln.strip()[:70]}")

    def test_bash_syntax_valid(self):
        r = subprocess.run(
            ["bash", "-n", os.path.join(REPO, "openclaw_backup.sh")],
            capture_output=True, text=True,
        )
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_marker(self):
        self.assertIn("V37.9.311", self.src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
