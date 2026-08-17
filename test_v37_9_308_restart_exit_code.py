#!/usr/bin/env python3
"""V37.9.308 (2026-08-17) — restart.sh 诚实退出码 + Gateway bootstrap 守卫 + auto_deploy 告警。

对抗审计 2026-08-14 登记项 [43](a) C4 MED 的兑现。

血案（三段静默链，全部经本次修复闭合）:
  1. `restart_via_launchd` 的 rc=1（bootstrap 失败 / 10s 内未健康）只被调用方的
     `[ "$_ad_rc" -eq 2 ]` 分支看一眼就丢掉 → restart.sh **恒 exit 0**。
  2. 于是 auto_deploy.sh 的 `bash restart.sh || { ❌ restart.sh 失败; exit 1 }`
     是**死代码** → 把 adapter/proxy 留在崩溃循环里的部署被记成 "✅ 服务重启完成"。
  3. 即使该分支触发, 它也只写 auto_deploy.log —— 而该日志在 job_watchdog 里只有
     LOG_FRESHNESS(4200s mtime), **不在 HOME_LOGS 错误扫描名单** → 零告警。
  最窄也最脏的窗口: 漂移修复路径（auto_deploy DRIFT_REASON, HAS_NEW_COMMITS=false）
  同样置 NEED_RESTART=true, 但其后的 preflight 兜底只在 HAS_NEW_COMMITS 时运行
  → 该路径下服务死掉连兜底体检都没有。

另一条（C4 后半, 破坏性而非静默）: Gateway 段 `launchctl bootstrap` 是裸调用,
在 `set -e` 下失败即刻杀脚本, 而上一行刚 bootout 成功 → Gateway 被留在 booted-out
态（launchd 不再认识该 label, KeepAlive 拉不起来）, 且健康验证/⚠️ 日志/退出汇总
全部不执行。

刻意不做的事（日落法 #34 / 原则 #8）: 不加重启重试。launchd KeepAlive 已经在重试
进程本身; auto_deploy 侧再加一层 = 与 launchd 抢管理权的新机器。本修复只让既有的
失败信号读到真值, 并抵达既有告警通道 —— 净新增 runtime 机制 = 0。

测试形态: 行为级 fakebin PATH 注入（镜像 V37.9.304 test_v37_9_304_ops_chain_honesty）
—— 真跑 restart.sh, 用假 launchctl/curl/sleep/lsof/nohup 控制成败, 隔离 HOME 零副作用。
"""

import os
import shutil
import stat
import subprocess
import tempfile
import unittest

REPO = os.path.dirname(os.path.abspath(__file__))
RESTART_SH = os.path.join(REPO, "restart.sh")
AUTO_DEPLOY_SH = os.path.join(REPO, "auto_deploy.sh")


def _read(name):
    with open(os.path.join(REPO, name), encoding="utf-8") as fh:
        return fh.read()


# ── fake 可执行文件 ────────────────────────────────────────────────────────
# launchctl: bootout/kickstart 恒成功; bootstrap 可经 env 指定对某个 plist 失败。
_FAKE_LAUNCHCTL = r"""#!/bin/sh
case "$1" in
  bootstrap)
    if [ -n "${FAKE_BOOTSTRAP_FAIL:-}" ]; then
      case "$3" in
        *"$FAKE_BOOTSTRAP_FAIL"*) exit 1 ;;
      esac
    fi
    exit 0 ;;
  *) exit 0 ;;
esac
"""

# curl: 按 URL 里的端口回不同 HTTP code（restart.sh 用 -w %{http_code} 捕 stdout）。
_FAKE_CURL = r"""#!/bin/sh
for a in "$@"; do
  case "$a" in
    *:5001*)  echo "${FAKE_CURL_5001:-200}";  exit 0 ;;
    *:5002*)  echo "${FAKE_CURL_5002:-200}";  exit 0 ;;
    *:18789*) echo "${FAKE_CURL_18789:-200}"; exit 0 ;;
  esac
done
echo 000
"""

# sleep 置空 → 把 ~40s 的健康轮询压到亚秒级, 测试才跑得动。
_FAKE_NOOP = "#!/bin/sh\nexit 0\n"
# lsof 无匹配 → 非零（真 lsof 语义）
_FAKE_LSOF = "#!/bin/sh\nexit 1\n"


class _RestartHarness(unittest.TestCase):
    """起一个隔离 HOME + fakebin PATH, 真跑 restart.sh。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="v379308_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = os.path.join(self.tmp, "home")
        self.agents = os.path.join(self.home, "Library", "LaunchAgents")
        os.makedirs(self.agents)
        self.bin = os.path.join(self.tmp, "fakebin")
        os.makedirs(self.bin)
        for name, body in (
            ("launchctl", _FAKE_LAUNCHCTL),
            ("curl", _FAKE_CURL),
            ("sleep", _FAKE_NOOP),
            ("nohup", _FAKE_NOOP),
            ("lsof", _FAKE_LSOF),
        ):
            p = os.path.join(self.bin, name)
            with open(p, "w", encoding="utf-8") as fh:
                fh.write(body)
            os.chmod(p, os.stat(p).st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    def _write_plists(self, adapter=True, proxy=True, gateway=True):
        want = {
            "com.openclaw.adapter.plist": adapter,
            "com.openclaw.proxy.plist": proxy,
            "ai.openclaw.gateway.plist": gateway,
        }
        for name, enabled in want.items():
            if enabled:
                with open(os.path.join(self.agents, name), "w", encoding="utf-8") as fh:
                    fh.write("<plist/>")

    def _run(self, **env_over):
        env = dict(os.environ)
        env.update(
            {
                "HOME": self.home,
                "PATH": self.bin + ":/usr/bin:/bin",
                "OPENCLAW": "/bin/true",
            }
        )
        env.update({k: str(v) for k, v in env_over.items()})
        return subprocess.run(
            ["bash", RESTART_SH],
            capture_output=True,
            text=True,
            env=env,
            timeout=120,
        )


class TestHonestExitCode(_RestartHarness):
    """核心血案回归: 服务未恢复健康时 restart.sh 必须 exit 非零。"""

    def test_all_healthy_exits_zero_and_reports_done(self):
        """对照组: 三个服务都健康 → exit 0 + Done!（不得因本修复变成假失败）。"""
        self._write_plists()
        r = self._run()
        self.assertEqual(r.returncode, 0, f"健康路径不该失败: {r.stdout}\n{r.stderr}")
        self.assertIn("Done!", r.stdout)
        self.assertNotIn("未恢复健康", r.stdout)

    def test_adapter_unhealthy_exits_nonzero(self):
        """🔴 血案回归: adapter 10s 内未健康 → 此前 exit 0 静默, 现在必须 exit 1 且点名。"""
        self._write_plists()
        r = self._run(FAKE_CURL_5001="000")
        self.assertNotEqual(r.returncode, 0, f"adapter 不健康却 exit 0 = 血案复发\n{r.stdout}")
        self.assertIn("Adapter(:5001)", r.stdout)
        self.assertNotIn("Done!", r.stdout, "失败时不得报 Done!")

    def test_proxy_unhealthy_exits_nonzero(self):
        self._write_plists()
        r = self._run(FAKE_CURL_5002="000")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("ToolProxy(:5002)", r.stdout)

    def test_gateway_slow_start_warns_but_does_not_fail_deploy(self):
        """V37.8.13 决定保留: bootstrap 成功但 15s 未健康 → 只 ⚠️, 不判整体失败。

        理由: launchd 已接管且 KeepAlive 在重试, Adapter/Proxy 正常; 持续不健康由
        wa_keepalive (每 30min 真实发送) + job_watchdog 兜底, 不靠 restart.sh 退出码。
        """
        self._write_plists()
        r = self._run(FAKE_CURL_18789="000")
        self.assertEqual(
            r.returncode, 0, f"慢启动不应让整个部署判失败（V37.8.13）\n{r.stdout}"
        )
        self.assertIn("Gateway failed to become healthy", r.stdout, "仍必须 ⚠️ 告警")

    def test_multiple_failures_all_named(self):
        """多个服务同时失败时汇总必须完整（不因短路只报第一个）。"""
        self._write_plists()
        r = self._run(FAKE_CURL_5001="000", FAKE_CURL_5002="000")
        self.assertNotEqual(r.returncode, 0)
        for tag in ("Adapter(:5001)", "ToolProxy(:5002)"):
            self.assertIn(tag, r.stdout, f"汇总缺 {tag}")

    def test_adapter_failure_does_not_abort_before_proxy_and_gateway(self):
        """adapter 失败后脚本必须继续重启 proxy/gateway（失败汇总 ≠ 提前退出）。"""
        self._write_plists()
        r = self._run(FAKE_CURL_5001="000")
        self.assertIn("Restarting Tool Proxy", r.stdout)
        self.assertIn("Starting Gateway", r.stdout)


class TestGatewayBootstrapGuard(_RestartHarness):
    """C4 后半: bootout 成功 + bootstrap 失败, 脚本不得死在半路。"""

    def test_gateway_bootstrap_failure_does_not_kill_script(self):
        """🔴 血案回归: 此前裸 bootstrap 在 set -e 下杀脚本, Gateway 留 booted-out 且零日志。"""
        self._write_plists()
        r = self._run(FAKE_BOOTSTRAP_FAIL="ai.openclaw.gateway")
        # 关键: 脚本要走完 —— 失败汇总行是最后一段的产物, 出现即证明没死在 bootstrap
        self.assertIn("bootstrap 失败", r.stdout)
        self.assertIn("booted-out", r.stdout)
        self.assertIn("未恢复健康", r.stdout, "脚本必须走到结尾的失败汇总")
        self.assertIn("bootstrap失败", r.stdout)
        self.assertNotEqual(r.returncode, 0)

    def test_bootstrap_failure_and_slow_start_are_distinct(self):
        """两种 Gateway 故障必须区别对待（不是同一件事）。

        bootstrap 失败 = launchd 没接管 → KeepAlive 无从重试 → 永久 down → exit 1;
        慢启动 = launchd 已接管在重试 → 仅 ⚠️ → exit 0（V37.8.13）。
        """
        self._write_plists()
        hard = self._run(FAKE_BOOTSTRAP_FAIL="ai.openclaw.gateway")
        soft = self._run(FAKE_CURL_18789="000")
        self.assertNotEqual(hard.returncode, 0, "bootstrap 失败必须判失败")
        self.assertEqual(soft.returncode, 0, "慢启动不得判失败")

    def test_gateway_bootstrap_failure_skips_useless_health_polling(self):
        """bootstrap 失败后不应再声称 'loaded via launchd'。"""
        self._write_plists()
        r = self._run(FAKE_BOOTSTRAP_FAIL="ai.openclaw.gateway")
        self.assertNotIn("Gateway loaded via launchd", r.stdout)

    def test_adapter_bootstrap_failure_counted(self):
        """adapter bootstrap 失败 (helper rc=1) 同样进汇总。"""
        self._write_plists()
        r = self._run(FAKE_BOOTSTRAP_FAIL="com.openclaw.adapter")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("Adapter(:5001)", r.stdout)


class TestFallbackPathUnchanged(_RestartHarness):
    """rc=2 (plist 缺失) 是文档化的 dev fallback, 不得被误判为失败。"""

    def test_missing_plists_still_exit_zero(self):
        self._write_plists(adapter=False, proxy=False, gateway=False)
        r = self._run()
        self.assertEqual(r.returncode, 0, f"dev fallback 路径不得报失败\n{r.stdout}")
        self.assertIn("fallback to nohup", r.stdout)
        self.assertIn("Done!", r.stdout)

    def test_missing_adapter_plist_does_not_pollute_failure_list(self):
        """只缺 adapter plist → 走 nohup fallback, 其余健康 → 仍 exit 0。"""
        self._write_plists(adapter=False)
        r = self._run()
        self.assertEqual(r.returncode, 0)
        self.assertNotIn("Adapter(:5001)", r.stdout)


class TestSloCheckerLockedWrite(unittest.TestCase):
    """V37.9.308 (对抗审计 B-F8, MR-9): slo_checker --update 收编 status_lock + save_status。

    此前是裸 RMW: 写本身原子, 但 load→replace 窗口无锁 → 并发写者
    (kb_status_refresh 每小时 / full_regression 回写 / status_update CLI) 的改动被整份
    覆盖静默丢失。
    """

    def setUp(self):
        self.src = _read("slo_checker.py")

    def test_uses_status_lock_and_save_helper(self):
        self.assertRegex(self.src, r"(?m)^\s*with status_update\.status_lock\(\)")
        self.assertRegex(self.src, r"(?m)^\s*status_update\.save_status\(")

    def test_bare_rmw_retired(self):
        """反模式守卫: 不得再自建第二套写路径（固定 tmp + 手写 os.replace）。"""
        self.assertNotIn('tmp = STATUS_JSON + ".tmp"', self.src)
        self.assertNotRegex(
            self.src,
            r"(?m)^\s*os\.replace\(tmp, STATUS_JSON\)",
            "退役: slo_checker 自建 os.replace 写路径（应走 save_status）",
        )

    def test_write_is_locked_and_preserves_concurrent_fields(self):
        """行为级: 真跑 --update, 断言经锁写入且不吞掉他人字段。"""
        tmp = tempfile.mkdtemp(prefix="v379308_slo_")
        self.addCleanup(shutil.rmtree, tmp, True)
        os.makedirs(os.path.join(tmp, ".kb"))
        status_path = os.path.join(tmp, ".kb", "status.json")
        with open(status_path, "w", encoding="utf-8") as fh:
            fh.write('{"health": {"existing": "keepme"}, "priorities": [{"id": "p1"}]}')
        with open(os.path.join(tmp, "proxy_stats.json"), "w", encoding="utf-8") as fh:
            fh.write(
                '{"slo": {"p95_latency_ms": 100, "success_rate": 1.0, '
                '"tool_success_rate": 1.0, "degradation_rate": 0.0, "recovery_rate": 1.0, '
                '"total_requests": 500, "tool_calls": 50, "failure_streaks": 5, '
                '"recovered_streaks": 5}}'
            )
        env = dict(os.environ)
        env["HOME"] = tmp
        r = subprocess.run(
            ["python3", os.path.join(REPO, "slo_checker.py"), "--update"],
            capture_output=True, text=True, env=env, timeout=60, cwd=REPO,
        )
        self.assertIn("SLO status written", r.stdout, f"写入未发生: {r.stdout}\n{r.stderr}")
        import json as _json

        with open(status_path, encoding="utf-8") as fh:
            data = _json.load(fh)
        self.assertIn("slo", data["health"], "health.slo 未写入")
        self.assertEqual(data["health"].get("existing"), "keepme", "并发字段被吞")
        self.assertEqual(data.get("priorities"), [{"id": "p1"}], "并发字段被吞")
        self.assertEqual(data.get("updated_by"), "slo_checker")
        self.assertTrue(
            os.path.exists(status_path + ".lock"), "未经 status_lock（无伴生锁文件）"
        )

    def test_does_not_fabricate_kb_status_on_dev(self):
        """dev 脚枪: 无 ~/.kb 时不得凭空造 ~/.kb/status.json 劫持后续路径解析。"""
        tmp = tempfile.mkdtemp(prefix="v379308_slodev_")
        self.addCleanup(shutil.rmtree, tmp, True)
        with open(os.path.join(tmp, "proxy_stats.json"), "w", encoding="utf-8") as fh:
            fh.write('{"slo": {"p95_latency_ms": 100, "success_rate": 1.0}}')
        env = dict(os.environ)
        env["HOME"] = tmp
        subprocess.run(
            ["python3", os.path.join(REPO, "slo_checker.py"), "--update"],
            capture_output=True, text=True, env=env, timeout=60, cwd=REPO,
        )
        self.assertFalse(
            os.path.exists(os.path.join(tmp, ".kb", "status.json")),
            "不得在 dev 凭空创建 ~/.kb/status.json",
        )


class TestBadgeSemverTokenTolerance(unittest.TestCase):
    """V37.9.308: config.md 的 VERSION semver token 必须能匹配「真 bump」的头部。

    原模式写死 " 不变" → 任何真的 bump 了版本的发布, 头部照实写 "VERSION x.y.z" 就
    匹配不上, token 静默不再被管理（本次即当场触发 TOKEN_NOT_FOUND 警告）。
    """

    def test_pattern_matches_both_shapes(self):
        import re as _re

        src = _read("gen_readme_badges.py")
        m = _re.search(r'\("VERSION semver",\s*r"([^"]+)"', src)
        self.assertIsNotNone(m, "未找到 VERSION semver token 模式")
        pat = _re.compile(m.group(1))
        self.assertTrue(pat.search("VERSION 0.37.9.140"), "必须匹配真 bump 的头部")
        self.assertTrue(pat.search("VERSION 0.37.9.139 不变"), "必须向后兼容旧头部")

    def test_config_md_semver_token_is_managed(self):
        """端到端: --check 不得对 config.md 的 semver token 报 TOKEN_NOT_FOUND。"""
        r = subprocess.run(
            ["python3", os.path.join(REPO, "gen_readme_badges.py"), "--check"],
            capture_output=True, text=True, cwd=REPO, timeout=120,
        )
        self.assertNotIn(
            "VERSION semver: TOKEN_NOT_FOUND",
            r.stdout + r.stderr,
            "config.md semver token 失管（模式与头部实际写法不符）",
        )


class TestSourceGuards(unittest.TestCase):
    """源码级守卫: 防止未来把诚实退出码改回静默。"""

    def setUp(self):
        self.src = _read("restart.sh")
        self.deploy = _read("auto_deploy.sh")

    def test_marker(self):
        self.assertIn("V37.9.308", self.src)
        self.assertIn("V37.9.308", self.deploy)

    def test_failure_accumulator_exists(self):
        self.assertIn("RESTART_FAILED=", self.src)

    def test_exit_nonzero_on_failure(self):
        self.assertRegex(
            self.src,
            r'if \[ -n "\$RESTART_FAILED" \]; then(?:.|\n)*?exit 1',
            "必须在 RESTART_FAILED 非空时 exit 1",
        )

    def test_adapter_and_proxy_rc1_branches_exist(self):
        """rc=1 分支必须存在 —— 退役『只看 -eq 2』的旧形态。"""
        self.assertRegex(self.src, r'elif \[ "\$_ad_rc" -ne 0 \]; then')
        self.assertRegex(self.src, r'elif \[ "\$_px_rc" -ne 0 \]; then')

    def test_gateway_bootstrap_is_guarded_not_bare(self):
        """反模式守卫: Gateway bootstrap 不得是裸调用（set -e 下会杀脚本）。"""
        self.assertRegex(
            self.src,
            r'if ! launchctl bootstrap "gui/\$\(id -u\)" "\$GATEWAY_PLIST"; then',
            "Gateway bootstrap 必须带守卫",
        )
        self.assertNotRegex(
            self.src,
            r'(?m)^\s*launchctl bootstrap "gui/\$\(id -u\)" "\$GATEWAY_PLIST"\s*$',
            "退役: 裸 launchctl bootstrap（无 if 守卫）",
        )

    def test_no_retry_machinery_added(self):
        """日落法: 本修复刻意不加重启重试（launchd KeepAlive 已在管进程）。"""
        self.assertNotRegex(
            self.src,
            r"(?i)for\s+_?retry|RESTART_RETRIES|retry_restart",
            "不应引入重启重试机器 —— 与 launchd KeepAlive 抢管理权",
        )

    def test_v37_8_13_gateway_health_logic_preserved(self):
        """跨版本守卫: V37.8.13 健康验证不得被本次改动弄丢。"""
        self.assertIn("V37.8.13", self.src)
        self.assertIn("GATEWAY_HEALTHY=false", self.src)
        self.assertIn("Gateway failed to become healthy", self.src)

    def _restart_failure_block(self):
        """精确切出 `bash restart.sh || { ... }` 这一个块。

        不能用 `restart.sh 失败"(?:.|\n)*?quiet_alert` 这类前向扫描 —— auto_deploy 里
        还有 4 处无关的 quiet_alert（drift/cron/preflight）, 前向扫描会撞上它们从而
        变成永远为真的空转守卫（本守卫初版即如此, 由 sabotage 反向验证当场抓出）。
        """
        start = self.deploy.index('bash "$HOME/restart.sh"')
        end = self.deploy.index("\n    }", start)
        block = self.deploy[start:end]
        # 防空转: 切片必须真的是那个失败分支
        self.assertIn("restart.sh 失败", block, "切片没命中 restart 失败分支")
        self.assertLess(len(block), 1200, "切片过大, 可能跨出了该块")
        return block

    def test_auto_deploy_failure_branch_alerts(self):
        """跨文件契约: auto_deploy 的 restart 失败分支必须走 quiet_alert 而非只写日志。

        auto_deploy.log 只有 LOG_FRESHNESS, 不在 job_watchdog HOME_LOGS 错误扫描名单,
        只写日志 = 零告警。
        """
        block = self._restart_failure_block()
        # 必须锚定到**可执行行**: 块内注释里也写着 quiet_alert 字样, 裸 assertIn 会被
        # 自己的注释满足（V37.9.178/285/301 同款「守卫被自己的注释咬」, 本守卫初版即中招,
        # 由 sabotage 反向验证当场抓出）。注释行以 # 开头, ^\s*quiet_alert 不会匹配。
        self.assertRegex(
            block,
            r'(?m)^\s*quiet_alert\s+"',
            "restart.sh 失败分支必须在块内**真正调用** quiet_alert（只写日志 = 零告警）",
        )

    def test_auto_deploy_failure_branch_still_exits_nonzero(self):
        """失败分支必须仍 exit 1（告警不替代中止部署流程）。"""
        self.assertIn("exit 1", self._restart_failure_block())

    def test_auto_deploy_log_still_absent_from_watchdog_error_scan(self):
        """记录本修复的前提事实: auto_deploy.log 确实不在 HOME_LOGS。

        若未来有人把它加进 HOME_LOGS, 本守卫会 fail 并提示复核 quiet_alert 是否重复告警
        （原则 #8: 谁已经在管这件事）。
        """
        wd = _read("job_watchdog.sh")
        start = wd.index("HOME_LOGS=(")
        block = wd[start : wd.index(")", wd.index("mm_index.log", start))]
        # 防空转: 切片必须真含已知条目, 否则 assertNotIn 恒为真
        self.assertIn("kb_evening.log", block, "HOME_LOGS 切片失效")
        self.assertIn("mm_index.log", block, "HOME_LOGS 切片失效")
        self.assertNotIn(
            "auto_deploy.log",
            block,
            "auto_deploy.log 进了 HOME_LOGS → 复核 V37.9.308 quiet_alert 是否成为重复告警",
        )

    def test_bash_syntax_valid(self):
        for name in ("restart.sh", "auto_deploy.sh"):
            r = subprocess.run(
                ["bash", "-n", os.path.join(REPO, name)], capture_output=True, text=True
            )
            self.assertEqual(r.returncode, 0, f"{name}: {r.stderr}")

    def test_bash32_safe_no_empty_array_expansion(self):
        """Mac Mini 是 bash 3.2: 空数组 ${arr[@]} + set -u = unbound variable。

        本修复刻意用字符串累加器。守卫防止未来改成数组踩这颗雷。
        """
        self.assertNotRegex(
            self.src,
            r"RESTART_FAILED=\(",
            "RESTART_FAILED 必须是字符串, 不能是数组（bash 3.2 + set -u 地雷）",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
