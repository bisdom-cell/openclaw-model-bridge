#!/usr/bin/env python3
"""V37.9.309 (2026-08-17) — SSD 挂载检测的可诊断性 + 匹配模式回归钉死。

背景: 2026-08-15 起连续告警「KB-SSD每日备份: 异常状态 (skipped_no_ssd)」+
「ERROR: SSD not mounted (dir or mount-table check failed), skip backup」。
git -L 证实该消息文本是 V37.9.304 (2026-08-14) 引入的新文案 → Mac Mini 在 8-15
03:00 前已同步该版, 失败恰始于同步后第一次备份。

V37.9.304 把挂载检测从 `-d` 改为 `-d && mount 表精确匹配`, 两个条件**合并成一条
错误信息** → 看日志分不清是「SSD 没插」还是「有幽灵目录但没真挂载」, 定性只能靠
人工上机敲 mount。本版拆开两个分支并在幽灵分支 dump mount 表实况 —— 诊断信息属于
告警本身的一部分, 不该逼人再上机取证一次。

本套件同时**钉死匹配模式的行为**: 该模式是「盲区解除 vs 我引入误判」的分水岭,
必须对真实 macOS mount 输出格式 (exfat / APFS / HFS+) 判为已挂载, 对幽灵重挂
("MOVESPEED 1") 与未挂载判为未挂载。
"""

import os
import re
import subprocess
import unittest

REPO = os.path.dirname(os.path.abspath(__file__))


def _read(name):
    with open(os.path.join(REPO, name), encoding="utf-8") as fh:
        return fh.read()


# 该模式是两处脚本共用的判据（MR-8 单一形态）
_MOUNT_PATTERN = " on /Volumes/MOVESPEED ("


class TestMountPatternBehavior(unittest.TestCase):
    """匹配模式对真实 macOS mount 输出的判定 —— H1/H2 的分水岭。"""

    def _matches(self, line):
        r = subprocess.run(
            ["grep", "-q", _MOUNT_PATTERN],
            input=line, text=True, capture_output=True,
        )
        return r.returncode == 0

    def test_real_macos_mounted_formats_are_recognized(self):
        """正常挂载必须判为已挂载 —— 否则就是我方误判堵住备份（H2）。"""
        for fs, line in (
            ("exfat", "/dev/disk4s1 on /Volumes/MOVESPEED (exfat, local, nodev, nosuid, noowners)"),
            ("apfs", "/dev/disk4s2 on /Volumes/MOVESPEED (apfs, local, nodev, nosuid, journaled, noowners)"),
            ("hfs", "/dev/disk4s2 on /Volumes/MOVESPEED (hfs, local, nodev, nosuid, journaled, noowners)"),
        ):
            with self.subTest(fs=fs):
                self.assertTrue(self._matches(line), f"{fs} 正常挂载被误判为未挂载")

    def test_ghost_remount_suffix_is_rejected(self):
        """幽灵目录场景: 真 SSD 被挂到 'MOVESPEED 1', 不得误认为已挂载。"""
        self.assertFalse(
            self._matches("/dev/disk4s1 on /Volumes/MOVESPEED 1 (exfat, local, nodev)")
        )

    def test_unmounted_is_rejected(self):
        self.assertFalse(
            self._matches("/dev/disk3s1s1 on / (apfs, sealed, local, read-only, journaled)")
        )

    def test_pattern_is_identical_in_both_scripts(self):
        """MR-8 一物一形: 两处脚本必须用同一个判据。"""
        for name in ("openclaw_backup.sh", "movespeed_daily_sync.sh"):
            with self.subTest(script=name):
                self.assertIn(f'grep -q "{_MOUNT_PATTERN}"', _read(name))


class TestDiagnosableMessages(unittest.TestCase):
    """两个失败原因必须在日志里可区分（V37.9.304 合并成一条是本次诊断的绊脚石）。"""

    def test_backup_splits_two_conditions(self):
        src = _read("openclaw_backup.sh")
        self.assertNotIn(
            '[ ! -d "/Volumes/MOVESPEED" ] || ! mount',
            src,
            "退役: 两个条件合并成一条(日志分不清哪个失败)",
        )
        self.assertIn("目录 /Volumes/MOVESPEED 不存在", src)
        self.assertIn("目录存在但不在 mount 表", src)

    def test_sync_splits_two_conditions(self):
        src = _read("movespeed_daily_sync.sh")
        self.assertNotIn('[ ! -d "/Volumes/MOVESPEED" ] || ! mount', src)
        self.assertIn("目录 /Volumes/MOVESPEED 不存在", src)
        self.assertIn("目录存在但不在 mount 表", src)

    def test_ghost_branch_dumps_actual_mount_table(self):
        """幽灵分支必须打印 mount 表实况 —— 否则仍要人工上机取证。"""
        for name in ("openclaw_backup.sh", "movespeed_daily_sync.sh"):
            with self.subTest(script=name):
                src = _read(name)
                self.assertRegex(src, r"MOUNT_LINES=\$\(mount \| grep -i movespeed")
                self.assertIn("mount 表", src)

    def test_empty_mount_table_has_explicit_placeholder(self):
        """无匹配行时不得留空串（空串在日志里读不出信息）。"""
        for name in ("openclaw_backup.sh", "movespeed_daily_sync.sh"):
            with self.subTest(script=name):
                self.assertIn("mount 表中无任何 MOVESPEED 行", _read(name))


class TestObservabilityContractsPreserved(unittest.TestCase):
    """本次改动不得弄丢既有的告警可见性链路。"""

    def test_backup_keeps_error_prefix_for_watchdog(self):
        """openclaw_backup.log 在 watchdog HOME_LOGS 里, 靠 'ERROR:' 命中 err_pattern。"""
        src = _read("openclaw_backup.sh")
        wd = _read("job_watchdog.sh")
        m = re.search(r"local err_pattern='([^']+)'", wd)
        self.assertIsNotNone(m, "未找到 watchdog err_pattern")
        pat = re.compile(m.group(1))
        fired = [
            ln for ln in src.splitlines()
            if "SSD not mounted" in ln and pat.search(ln)
        ]
        self.assertTrue(fired, "SSD 失败行不再匹配 watchdog err_pattern = 告警丢失")

    def test_sync_still_writes_skipped_no_ssd_status(self):
        """daily_sync 的可见性靠 last_run 状态 + watchdog catch-all（V37.9.287）。"""
        src = _read("movespeed_daily_sync.sh")
        self.assertIn('"status":"skipped_no_ssd"', src)
        self.assertIn('"time"', src, "V37.9.287: 时间键必须是 time 不是 ts")

    def test_bash_syntax_valid(self):
        for name in ("openclaw_backup.sh", "movespeed_daily_sync.sh"):
            with self.subTest(script=name):
                r = subprocess.run(
                    ["bash", "-n", os.path.join(REPO, name)],
                    capture_output=True, text=True,
                )
                self.assertEqual(r.returncode, 0, r.stderr)

    def test_marker(self):
        for name in ("openclaw_backup.sh", "movespeed_daily_sync.sh"):
            with self.subTest(script=name):
                self.assertIn("V37.9.309", _read(name))


if __name__ == "__main__":
    unittest.main(verbosity=2)
