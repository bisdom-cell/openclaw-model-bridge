#!/usr/bin/env python3
"""V37.9.304 守卫: 备份/运维链诚实修复 (对抗审计 C 镜头 + B-F7)。

覆盖六项修复:
  C1  movespeed_rsync_helper --strict-exit opt-in (真实退出码) + daily_sync 消费
      — helper 默认恒 exit 0 (20 个 set -e 调用方契约), daily_sync 曾拿 RC=$? 判
      成败 → status:"failed" 死代码, rsync 三连败仍写 status:ok (MOVESPEED 60 天
      潜伏血案同形)。
  C2  crontab_safe: cmd_restore 裸 crontab 未查 rc 即 ✅ (自动回滚路径静默失败) +
      matched_count 数注释行 → 正确删除被误回滚。
  C3  job_watchdog CRON_COUNT `|| echo "0"` — grep -c 已输出 0, 再补一行 →
      "0\n0" → integer expression 报错判假 → 清空告警在目标状态自我中和 (PASS)。
  C6  openclaw_backup 两处 cp 未查 rc 即打成功日志。
  C7  SSD 挂载检测仅 -d — 幽灵目录场景备份写进启动盘全绿。
  C8  diagnose.sh 段 4 python heredoc 无守卫 — stats 损坏时排障工具自杀于 4/7。
  B-F7 watchdog 错误率阈值硬编码 20, config alerts.error_rate_alert_pct 是孤儿键。

行为级测试用 fakebin PATH 注入 (rsync/tmutil/crontab stub), 全部 dev 可跑、
隔离 HOME 零真实状态污染 (MR-9)。
"""
import os
import re
import shutil
import subprocess
import tempfile
import unittest

REPO = os.path.dirname(os.path.abspath(__file__))
HELPER = os.path.join(REPO, "movespeed_rsync_helper.sh")
DAILY_SYNC = os.path.join(REPO, "movespeed_daily_sync.sh")
WATCHDOG = os.path.join(REPO, "job_watchdog.sh")
BACKUP = os.path.join(REPO, "openclaw_backup.sh")
DIAGNOSE = os.path.join(REPO, "diagnose.sh")
CRONTAB_SAFE = os.path.join(REPO, "crontab_safe.sh")
CONFIG = os.path.join(REPO, "config.yaml")


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _write_exec(path, content):
    with open(path, "w") as f:
        f.write(content)
    os.chmod(path, 0o755)


class TestHelperStrictExit(unittest.TestCase):
    """C1 行为级: helper strict/默认双契约 (dev 实测过的五种语义固化)。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="v304_helper_")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        # 隔离副本: SCRIPT_DIR=tmp → capture helper 缺失走 defensive skip 分支,
        # 失败路径不触发真实 incident 写入 (测试零副作用)
        self.helper_iso = os.path.join(self.tmp, "movespeed_rsync_helper.sh")
        shutil.copy(HELPER, self.helper_iso)
        self.fakebin = os.path.join(self.tmp, "fakebin")
        os.makedirs(self.fakebin)

    def _run(self, strict, rsync_rc=23, tm_running=False):
        _write_exec(os.path.join(self.fakebin, "rsync"), f"#!/bin/sh\nexit {rsync_rc}\n")
        env = dict(os.environ)
        env["PATH"] = self.fakebin + os.pathsep + env.get("PATH", "")
        env["MOVESPEED_RSYNC_NO_SLEEP"] = "1"
        env["MOVESPEED_RSYNC_MAX_ATTEMPTS"] = "1"
        if tm_running:
            _write_exec(os.path.join(self.fakebin, "tmutil"),
                        '#!/bin/sh\necho "Backup session status:"\necho "Running = 1"\n')
            env.pop("MOVESPEED_RSYNC_SKIP_TMUTIL_CHECK", None)
        else:
            env["MOVESPEED_RSYNC_SKIP_TMUTIL_CHECK"] = "1"
        args = ["bash", self.helper_iso]
        if strict:
            args.append("--strict-exit")
        args += ["test_caller.sh", "--", "-a", "/tmp/nonexistent_src_v304", "/tmp/nonexistent_dst_v304"]
        proc = subprocess.run(args, capture_output=True, text=True, env=env, timeout=60)
        return proc.returncode

    def test_default_mode_rsync_fail_exit0(self):
        """默认契约不变: rsync 全败仍 exit 0 (20 个 set -eo pipefail 调用方存活, V37.9.31)。"""
        self.assertEqual(self._run(strict=False, rsync_rc=23), 0)

    def test_strict_mode_rsync_fail_exit1(self):
        """血案回归 (C1): strict 模式 rsync 全败 → exit 1 (daily_sync 的 failed 分支复活)。"""
        self.assertEqual(self._run(strict=True, rsync_rc=23), 1)

    def test_strict_mode_rsync_ok_exit0(self):
        self.assertEqual(self._run(strict=True, rsync_rc=0), 0)

    def test_strict_mode_tm_running_exit3(self):
        """strict 模式 TM 备份进行中 → exit 3 (每日一次的 daily_sync 整天不备份必须可见)。"""
        self.assertEqual(self._run(strict=True, rsync_rc=0, tm_running=True), 3)

    def test_default_mode_tm_running_exit0(self):
        """默认契约: TM 跳过仍 exit 0 (多次/日内容 job 下个周期自然重试, V37.9.106)。"""
        self.assertEqual(self._run(strict=False, rsync_rc=0, tm_running=True), 0)

    def test_usage_error_still_exit2(self):
        """--strict-exit 后缺参数仍走 usage exit 2 (flag 解析不吞参数校验)。"""
        proc = subprocess.run(["bash", self.helper_iso, "--strict-exit"],
                              capture_output=True, text=True, timeout=30)
        self.assertEqual(proc.returncode, 2)


class TestDailySyncWiring(unittest.TestCase):
    """C1+C7 源码级: daily_sync 消费 strict 语义 + mount 表检测。"""

    def setUp(self):
        self.src = _read(DAILY_SYNC)

    def test_helper_called_with_strict_exit(self):
        self.assertRegex(self.src, r'bash "\$HELPER" --strict-exit',
                         "daily_sync 必须用 --strict-exit 调 helper (否则 RC 恒 0, failed 分支死代码)")

    def test_rc_capture_idiom(self):
        """RC=0 + || RC=$? 惯例 (set -e 安全, V37.9.274/282/292 同款)。"""
        self.assertIn("RC=0", self.src)
        self.assertRegex(self.src, r'\|\| RC=\$\?')
        # 旧裸 RC=$? 行 (helper 调用后独立一行) 已退役
        self.assertNotRegex(self.src, r'2>&1\n\s*RC=\$\?')

    def test_tm_skip_status_mapping(self):
        """RC==3 → skipped_tm_backup 状态 (watchdog catch-all 可见, 非静默吞掉)。"""
        self.assertRegex(self.src, r'"\$RC" -eq 3')
        self.assertIn('skipped_tm_backup', self.src)

    def test_mount_table_check(self):
        """C7: mount 表精确匹配 (幽灵目录场景 -d 永真 → 备份写进启动盘)。"""
        self.assertIn(' on /Volumes/MOVESPEED (', self.src)

    def test_syntax(self):
        proc = subprocess.run(["bash", "-n", DAILY_SYNC], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)


class TestWatchdogCronCount(unittest.TestCase):
    """C3: CRON_COUNT 单行整数, 清空告警可达。"""

    def setUp(self):
        self.src = _read(WATCHDOG)

    def _extract_lines(self):
        """从 watchdog 源码提取真实 CRON_COUNT 赋值行 (防守卫-实现漂移, V37.9.300 惯例)。"""
        lines = [l for l in self.src.split("\n")
                 if re.match(r'^CRON_COUNT=', l)]
        self.assertGreaterEqual(len(lines), 2, "应有赋值 + tr 清理两行")
        return "\n".join(lines)

    def test_empty_crontab_alert_fires(self):
        """血案回归: 空 crontab → 单行 "0" → -lt 10 告警分支真实可达
        (旧 `|| echo "0"` 产 "0\\n0" → integer expression 报错 → 落 PASS 分支)。"""
        with tempfile.TemporaryDirectory() as tmp:
            fakebin = os.path.join(tmp, "bin")
            os.makedirs(fakebin)
            _write_exec(os.path.join(fakebin, "crontab"), "#!/bin/sh\nexit 0\n")
            harness = (self._extract_lines() +
                       '\nprintf "val=[%s]\\n" "$CRON_COUNT"'
                       '\nif [ "$CRON_COUNT" -lt 10 ]; then echo ALERT_FIRES; else echo SILENT_PASS; fi\n')
            env = dict(os.environ)
            env["PATH"] = fakebin + os.pathsep + env["PATH"]
            proc = subprocess.run(["bash", "-c", harness], capture_output=True, text=True,
                                  env=env, timeout=30)
            self.assertIn("val=[0]", proc.stdout, "CRON_COUNT 必须是单行 0")
            self.assertIn("ALERT_FIRES", proc.stdout)
            self.assertNotIn("integer expression", proc.stderr)

    def test_empty_crontab_alert_fires_under_pipefail(self):
        """未来-proof: 即使 watchdog 之后加 pipefail, 该行仍产单行整数。"""
        with tempfile.TemporaryDirectory() as tmp:
            fakebin = os.path.join(tmp, "bin")
            os.makedirs(fakebin)
            _write_exec(os.path.join(fakebin, "crontab"), "#!/bin/sh\nexit 0\n")
            harness = ("set -o pipefail\n" + self._extract_lines() +
                       '\nif [ "$CRON_COUNT" -lt 10 ]; then echo ALERT_FIRES; fi\n')
            env = dict(os.environ)
            env["PATH"] = fakebin + os.pathsep + env["PATH"]
            proc = subprocess.run(["bash", "-c", harness], capture_output=True, text=True,
                                  env=env, timeout=30)
            self.assertIn("ALERT_FIRES", proc.stdout)
            self.assertNotIn("integer expression", proc.stderr)

    def test_double_echo_antipattern_retired(self):
        """源码守卫: crontab 计数行不得回退 `|| echo "0"` (grep -c 已输出计数)。"""
        for line in self.src.split("\n"):
            if line.startswith("CRON_COUNT=") and "crontab" in line:
                self.assertNotIn('|| echo', line,
                                 f'CRON_COUNT 行回退了 || echo 反模式: {line}')


class TestWatchdogErrorRateConfig(unittest.TestCase):
    """B-F7: 错误率阈值走 config, 孤儿键闭合。"""

    def setUp(self):
        self.src = _read(WATCHDOG)

    def test_config_key_consumed(self):
        """job_watchdog 消费 alerts.error_rate_alert_pct (原全仓唯一出现处是 config 自己)。"""
        non_comment = "\n".join(l for l in self.src.split("\n")
                                if not l.strip().startswith("#"))
        self.assertIn("error_rate_alert_pct", non_comment)

    def test_hardcoded_threshold_retired(self):
        """硬编码 `if error_rate > 20:` 已退役 (改比较 config 阈值变量)。"""
        self.assertNotRegex(self.src, r'if error_rate > 20:')
        self.assertRegex(self.src, r'if error_rate > _thr:')

    def test_config_declares_key(self):
        self.assertIn("error_rate_alert_pct", _read(CONFIG))


class TestBackupGuards(unittest.TestCase):
    """C6+C7: openclaw_backup cp rc 检查 + mount 表检测。"""

    def setUp(self):
        self.src = _read(BACKUP)

    def test_mount_table_check(self):
        self.assertIn(' on /Volumes/MOVESPEED (', self.src)

    def test_fallback_move_rc_checked(self):
        """fallback 落盘操作失败 → ERROR + exit (曾无条件 "OK (copied)")。

        V37.9.311 演进: 动词由 cp 改为 mv —— fallback 现在让 CLI 直接在 SSD 的
        BACKUP_DIR 内生成 (原先写到 CWD=仓库目录, 留下含凭据的 1GB 孤儿档), 因此
        改名是同盘操作, mv 秒级完成且不占双份空间 (1GB × 2 会挤盘)。
        **本守卫的意图不变**: 落盘操作必须检查退出码, 失败必须 ERROR + 非零退出,
        绝不无条件打成功。故断言放宽到 cp|mv 二选一, 但仍要求 `if !` 检查 + ERROR + exit。
        """
        self.assertRegex(
            self.src, r'if ! (?:cp|mv) "\$LATEST" "\$BACKUP_FILE"',
            "fallback 落盘操作必须检查退出码",
        )
        self.assertRegex(
            self.src,
            r'if ! (?:cp|mv) "\$LATEST" "\$BACKUP_FILE"; then\n\s*echo[^\n]*ERROR[^\n]*\n\s*exit \d',
            "失败分支必须 ERROR + 非零退出",
        )

    def test_status_cp_rc_checked(self):
        """status.json cp 失败 → ERROR 行 (匹配 watchdog err_pattern, 断档可见)。"""
        self.assertRegex(self.src, r'if cp "\$STATUS_SRC"')
        self.assertIn('ERROR: status.json backup cp failed', self.src)

    def test_syntax(self):
        proc = subprocess.run(["bash", "-n", BACKUP], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)


class TestDiagnoseGuard(unittest.TestCase):
    """C8: 段 4 heredoc 守卫 — stats 损坏不再杀死排障脚本。"""

    def test_section4_heredoc_guarded(self):
        src = _read(DIAGNOSE)
        self.assertRegex(src, r"python3 << 'PYEOF' \|\|",
                         "段 4 python heredoc 必须带 || 守卫 (set -eo pipefail 下损坏 stats 曾自杀于 4/7)")
        self.assertIn("解析失败", src)

    def test_syntax(self):
        proc = subprocess.run(["bash", "-n", DIAGNOSE], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)


class TestCrontabSafeBehavior(unittest.TestCase):
    """C2 行为级: 带注释 crontab 的 remove 不误回滚 + restore 失败诚实。"""

    _STUB = """#!/bin/bash
STATE="${FAKE_CRON_STATE:?}"
if [ "${1:-}" = "-l" ]; then
    cat "$STATE" 2>/dev/null
    exit 0
fi
if [ "${FAKE_CRON_INSTALL_RC:-0}" != "0" ]; then
    exit "${FAKE_CRON_INSTALL_RC}"
fi
cp "$1" "$STATE"
"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="v304_cron_")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.home = os.path.join(self.tmp, "home")
        os.makedirs(self.home)
        self.fakebin = os.path.join(self.tmp, "bin")
        os.makedirs(self.fakebin)
        _write_exec(os.path.join(self.fakebin, "crontab"), self._STUB)
        self.state = os.path.join(self.tmp, "cron_state")

    def _env(self, install_rc=0):
        env = dict(os.environ)
        env["PATH"] = self.fakebin + os.pathsep + env["PATH"]
        env["HOME"] = self.home
        env["FAKE_CRON_STATE"] = self.state
        env["FAKE_CRON_INSTALL_RC"] = str(install_rc)
        return env

    def test_remove_with_matching_comment_no_false_rollback(self):
        """血案回归 (C2): pattern 命中注释行时, 正确的删除不再被误回滚。

        旧口径: matched_count 数全部行 (注释+活跃)=2, 活跃只少 1 →
        expected=before-2 ≠ after=before-1 → "自动回滚！" 误杀正确操作。
        """
        lines = [f"0 {i} * * * /bin/job_{i}.sh" for i in range(6)]
        lines.append("# maintenance note: run_foo.sh disabled once (2026-08)")
        lines.append("30 4 * * * /bin/run_foo.sh")
        with open(self.state, "w") as f:
            f.write("\n".join(lines) + "\n")
        proc = subprocess.run(["bash", CRONTAB_SAFE, "remove", "run_foo.sh"],
                              capture_output=True, text=True, env=self._env(), timeout=60)
        out = proc.stdout + proc.stderr
        self.assertEqual(proc.returncode, 0, out)
        self.assertNotIn("自动回滚", out)
        self.assertIn("已删除 1 条", out)
        final = _read(self.state)
        self.assertNotIn("run_foo.sh", final)
        self.assertIn("/bin/job_0.sh", final)
        self.assertIn("/bin/job_5.sh", final)

    def test_restore_install_failure_honest(self):
        """C2: 恢复安装失败 → ❌ 恢复失败 + 非零退出 (曾无条件 ✅ 已恢复)。"""
        with open(self.state, "w") as f:
            f.write("0 1 * * * /bin/x.sh\n")
        backup_dir = os.path.join(self.home, ".crontab_backups")
        os.makedirs(backup_dir)
        with open(os.path.join(backup_dir, "crontab_20990101_000000.bak"), "w") as f:
            f.write("0 2 * * * /bin/y.sh\n")
        proc = subprocess.run(["bash", CRONTAB_SAFE, "restore", "20990101"],
                              capture_output=True, text=True,
                              env=self._env(install_rc=1), timeout=60)
        out = proc.stdout + proc.stderr
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("恢复失败", out)
        self.assertNotIn("✅ 已恢复", out)

    def test_matched_count_active_only_source_guard(self):
        """源码守卫: matched_count 管道含活跃行过滤 (与 count_entries 同口径)。"""
        src = _read(CRONTAB_SAFE)
        m = re.search(r"matched_count=\$\(crontab -l[^\n]*\)", src)
        self.assertIsNotNone(m)
        self.assertIn("grep -v '^#'", m.group(0))

    def test_restore_rc_checked_source_guard(self):
        src = _read(CRONTAB_SAFE)
        self.assertRegex(src, r'if ! crontab "\$restore_file"')


if __name__ == "__main__":
    unittest.main(verbosity=2)
