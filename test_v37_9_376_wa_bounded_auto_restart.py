#!/usr/bin/env python3
"""test_v37_9_376_wa_bounded_auto_restart.py — V37.9.376 wa_keepalive 有界自动重启守卫

背景（2026-10-27 用户决策 b）：
  4.27 在 WhatsApp 建连阶段失败后不重试，频道直接留在 `stopped`：10-20 405 停摆 12.5h、
  10-24 408 跨周末约 60h；两次都是一条 `restart.sh` 恢复、不扫码，而告警每 3h 送达 Discord
  无人处理。决策：wa_keepalive 在升级告警那一轮，若 parser 判「仍 linked 且 stopped」
  （新字段 auto=1），替人有界执行 restart.sh——每 6h 至多 1 次、24h 至多 2 次（失败的尝试同样
  计入配额，不循环），仍发告警并写明结果；not linked / 已登出永不自动（扫码是人工动作）；
  仅 disconnected 不动（可能是重连瞬态）；`WA_AUTO_RESTART=0` 即关闭。

守卫原则（原则 #36-1）：先断言用户看到的结果——告警正文里写了什么、restart.sh 被调了几次、
  频道重启后是什么——再断言机制。行为级测试全部从 wa_keepalive.sh 真源码抽函数跑真 bash，
  假 openclaw / 假 restart.sh / 隔离 HOME（MR-9：绝不碰生产状态文件）。

本文件分 5 类：
  A. parser auto 字段语义（linked+stopped 才 1；not linked / 登出 / 仅 disconnected / 健康 → 0）
  B. 行为级（真源码抽块跑真 bash）：血案回归 / 首次检测不动 / 6h 间隔 / 24h 配额 /
     窗口过期修剪 / not linked 永不 / 仅 disconnected 永不 / 失败也计配额且告警带原因 /
     env 开关 / 旧 4 段 parser 保守不动 / 重启后仍 stopped 保留人工建议 / 脚本缺失
  C. 整脚本端到端（真 wa_keepalive.sh + 假 curl/openclaw/restart.sh）：Gateway 宕 → 永不重启；
     Gateway 健康 + 408 linked+stopped 第二轮 → 恰好一次重启
  D. watchdog 契约（MR-8）：wa_keepalive.log 只在 LOG_FRESHNESS 不在 HOME_LOGS（不双重告警）；
     DONE/SKIPPED 行不匹配 err_pattern、FAILED 行匹配（将来若加入错误扫描，失败仍可见）
  E. 源码守卫（剥注释）：边界常量 / 先记账后执行 / 只在告警轮调用 / restart.sh 诚实退出码仍在
     / registry 描述 / FILE_MAP / full_regression 注册
"""
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest

REPO = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO)

from wa_channel_status import format_keepalive_output, parse_whatsapp_state  # noqa: E402

PHONE = "+85200000000"

# 2026-10-24 22:44 HKT 第二次终止（408 握手超时）的真实状态行形态（手机号占位）
LINE_408 = (
    "- WhatsApp default: enabled, configured, linked, stopped, disconnected, "
    f"dm:allowlist, allow:{PHONE}, error:"
    '{"error":{"data":{"reason":"408","location":"frc"},"isBoom":true,"isServer":false,'
    '"output":{"statusCode":408,"payload":{"statusCode":408,"error":"Request Time-out",'
    '"message":"WebSocket Error (Opening handshake has timed out)"},"headers":{}}},'
    '"date":"2026-10-24T14:44:45.083Z"}'
)
# 2026-10-20 第一次终止（405）
LINE_405 = (
    "- WhatsApp default: enabled, configured, linked, stopped, disconnected, "
    f"dm:allowlist, allow:{PHONE}, error:"
    '{"error":{"output":{"statusCode":405,"payload":{"message":"Connection Failure"}}},'
    '"date":"2026-10-19T18:29:08.941Z"}'
)
LINE_NOT_LINKED = (
    "- WhatsApp default: enabled, configured, not linked, stopped, disconnected, "
    f"dm:allowlist, allow:{PHONE}"
)
LINE_DISCONNECTED_ONLY = (
    f"- WhatsApp default: enabled, configured, linked, running, disconnected, dm:allowlist, allow:{PHONE}"
)
LINE_LOGGED_OUT = (
    '- WhatsApp default: enabled, configured, linked, stopped, error:{"output":{"statusCode":401}}'
)
LINE_HEALTHY = (
    f"- WhatsApp default: enabled, configured, linked, running, connected, dm:allowlist, allow:{PHONE}"
)
GW = "Gateway reachable.\n"


def _read(rel):
    with open(os.path.join(REPO, rel), encoding="utf-8") as f:
        return f.read()


def _cat(path, default=""):
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as f:
        return f.read()


def _strip_comments(src):
    out = []
    for line in src.splitlines():
        if line.lstrip().startswith("#"):
            continue
        out.append(line)
    return "\n".join(out)


def _extract_funcs(src):
    funcs = re.findall(r"(_wa_\w+\(\)\s*\{.*?\n\})", src, re.DOTALL)
    names = [re.match(r"(_wa_\w+)", f).group(1) for f in funcs]
    return funcs, names


# ═══════════════════════════════════════════════════════
# A. parser auto 字段语义
# ═══════════════════════════════════════════════════════

class TestParserAutoField(unittest.TestCase):
    def test_408_linked_stopped_is_auto_eligible(self):
        st = parse_whatsapp_state(LINE_408)
        self.assertEqual(st["action"], "restart")
        self.assertTrue(st["auto_restart_ok"])
        self.assertTrue(format_keepalive_output(st).startswith("1|1|restart|1|"))

    def test_405_linked_stopped_is_auto_eligible(self):
        self.assertTrue(parse_whatsapp_state(LINE_405)["auto_restart_ok"])

    def test_not_linked_never_auto(self):
        st = parse_whatsapp_state(LINE_NOT_LINKED)
        self.assertEqual(st["action"], "login")
        self.assertFalse(st["auto_restart_ok"])

    def test_logged_out_code_never_auto_even_if_linked_stopped(self):
        st = parse_whatsapp_state(LINE_LOGGED_OUT)
        self.assertEqual(st["action"], "login")
        self.assertFalse(st["auto_restart_ok"])

    def test_disconnected_only_not_auto(self):
        """仅 disconnected（无 stopped）可能是重连瞬态：告警建议重启，但不自动"""
        st = parse_whatsapp_state(LINE_DISCONNECTED_ONLY)
        self.assertEqual(st["action"], "restart")
        self.assertFalse(st["auto_restart_ok"])
        self.assertTrue(format_keepalive_output(st).startswith("1|1|restart|0|"))

    def test_healthy_and_missing_not_auto(self):
        self.assertFalse(parse_whatsapp_state(LINE_HEALTHY)["auto_restart_ok"])
        self.assertFalse(parse_whatsapp_state("- Discord: connected")["auto_restart_ok"])
        self.assertEqual(format_keepalive_output(parse_whatsapp_state(LINE_HEALTHY)),
                         "0|1|none|0|connected")

    def test_reason_is_last_segment_even_with_pipe_inside(self):
        line = '- WhatsApp default: linked, stopped, error:{"msg":"a|b"}'
        out = format_keepalive_output(parse_whatsapp_state(line))
        parts = out.split("|", 4)
        self.assertEqual(parts[:4], ["1", "1", "restart", "1"])
        self.assertEqual(parts[4], 'stopped; error={"msg":"a|b"}')

    def test_cli_five_fields_fail_open_and_no_phone(self):
        exe = [sys.executable, os.path.join(REPO, "wa_channel_status.py")]
        r = subprocess.run(exe, input=GW + LINE_408, capture_output=True, text=True, timeout=30)
        self.assertTrue(r.stdout.startswith("1|1|restart|1|"), r.stdout)
        self.assertNotIn("85200000000", r.stdout)
        r2 = subprocess.run(exe, input="", capture_output=True, text=True, timeout=30)
        self.assertEqual(r2.stdout.strip(), "0|0|none|0|whatsapp_line_not_found")
        r3 = subprocess.run(exe + ["/nonexistent/status.txt"], capture_output=True, text=True, timeout=30)
        self.assertEqual(r3.stdout.strip(), "0|0|none|0|read_error")


# ═══════════════════════════════════════════════════════
# B. 行为级：真源码抽函数跑真 bash
# ═══════════════════════════════════════════════════════

class _Harness(unittest.TestCase):
    """隔离 HOME + 假 openclaw + 假 restart.sh，跑真 _wa_channel_check / _wa_auto_restart。"""

    def _run(self, before, after=None, prev_count="1", stamps=None, restart_rc=0,
             env_extra=None, parser_src=None, restart_missing=False):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        restarted_marker = os.path.join(tmp, "restarted")
        count_file = os.path.join(tmp, "restart_calls")
        sent_marker = os.path.join(tmp, "discord_sent.txt")
        with open(os.path.join(tmp, "status_before.txt"), "w") as f:
            f.write(before)
        with open(os.path.join(tmp, "status_after.txt"), "w") as f:
            f.write(after if after is not None else before)
        fake_openclaw = os.path.join(tmp, "openclaw")
        with open(fake_openclaw, "w") as f:
            f.write(
                "#!/bin/bash\n"
                'if [ "$1" = "channels" ] && [ "$2" = "status" ]; then\n'
                f'  if [ -f "{restarted_marker}" ]; then cat "{tmp}/status_after.txt"; '
                f'else cat "{tmp}/status_before.txt"; fi\n'
                "  exit 0\n"
                "fi\n"
                'if [ "$1" = "message" ] && [ "$2" = "send" ]; then\n'
                "  while [ $# -gt 0 ]; do\n"
                f'    if [ "$1" = "--message" ]; then shift; printf "%s" "$1" > "{sent_marker}"; fi\n'
                "    shift\n"
                "  done\n"
                "  exit 0\n"
                "fi\n"
                "exit 0\n"
            )
        os.chmod(fake_openclaw, 0o755)
        fake_restart = os.path.join(tmp, "restart.sh")
        if not restart_missing:
            with open(fake_restart, "w") as f:
                f.write(
                    "#!/bin/bash\n"
                    f'echo x >> "{count_file}"\n'
                    f'touch "{restarted_marker}"\n'
                    "echo '[restart] fake restart'\n"
                    f"exit {restart_rc}\n"
                )
            os.chmod(fake_restart, 0o755)
        if parser_src is None:
            shutil.copy(os.path.join(REPO, "wa_channel_status.py"), os.path.join(tmp, "wa_channel_status.py"))
        else:
            with open(os.path.join(tmp, "wa_channel_status.py"), "w") as f:
                f.write(parser_src)
        warn_file = os.path.join(tmp, ".wa_channel_warn_count")
        with open(warn_file, "w") as f:
            f.write(prev_count)
        stamps_file = os.path.join(tmp, ".wa_auto_restart_stamps")
        if stamps is not None:
            with open(stamps_file, "w") as f:
                f.write("".join(f"{int(s)}\n" for s in stamps))
        log_file = os.path.join(tmp, "wa_keepalive.log")
        env_lines = ""
        for k, v in (env_extra or {}).items():
            env_lines += f'{k}="{v}"\n'
        harness = f"""#!/bin/bash
HOME="{tmp}"
OPENCLAW="{fake_openclaw}"
LOG="{log_file}"
TS="2026-10-27 12:00:00"
WA_CHANNEL_WARN_FILE="{warn_file}"
ESCALATE_FIRST=2
ESCALATE_REPEAT=6
DISCORD_CH_ALERTS="test-alerts"
WA_AUTO_RESTART="${{WA_AUTO_RESTART:-1}}"
WA_AUTO_RESTART_STAMPS="{stamps_file}"
AUTO_RESTART_MIN_GAP_SEC=21600
AUTO_RESTART_WINDOW_SEC=86400
AUTO_RESTART_DAILY_MAX=2
RESTART_SH="{fake_restart}"
{env_lines}"""
        funcs, names = _extract_funcs(_read("wa_keepalive.sh"))
        self.assertIn("_wa_channel_check", names)
        self.assertIn("_wa_auto_restart", names)
        harness += "\n".join(funcs) + "\n_wa_channel_check\n"
        hp = os.path.join(tmp, "harness.sh")
        with open(hp, "w") as f:
            f.write(harness)
        r = subprocess.run(["bash", hp], capture_output=True, text=True, timeout=60)
        res = {
            "rc": r.returncode,
            "stderr": r.stderr,
            "warn": _cat(warn_file, "?").strip(),
            "log": _cat(log_file),
            "sent": _cat(sent_marker, None),
            "restarts": len(_cat(count_file).splitlines()),
            "stamps": [int(x) for x in _cat(stamps_file).split()],
        }
        return res


class TestBoundedAutoRestartRuntime(_Harness):
    def test_blood_regression_408_second_detection_restarts_once_and_reports(self):
        """血案回归：10-24 那条状态行第二次检测（=告警轮）→ 恰好一次 restart.sh，告警写明结果。"""
        now = int(time.time())
        r = self._run(GW + LINE_408, after=GW + LINE_HEALTHY, prev_count="1")
        self.assertEqual(r["restarts"], 1, r["stderr"])
        self.assertEqual(len(r["stamps"]), 1)
        self.assertLessEqual(abs(r["stamps"][0] - now), 120)
        self.assertIn("AUTO_RESTART_DONE: rc=0", r["log"])
        self.assertIsNotNone(r["sent"])
        self.assertIn("自动重启: 已执行 restart.sh (rc=0, 24h 内第 1/2 次)", r["sent"])
        self.assertIn("重启后频道: connected", r["sent"])
        self.assertIn("无需操作", r["sent"])
        self.assertNotIn("85200000000", r["sent"])
        self.assertIn("SYSTEM_ALERT", r["sent"])

    def test_first_detection_does_not_restart(self):
        """第 1 次检测（未达 ESCALATE_FIRST）：只记数，不告警、不重启。"""
        r = self._run(GW + LINE_408, prev_count="0")
        self.assertEqual(r["warn"], "1")
        self.assertEqual(r["restarts"], 0)
        self.assertIsNone(r["sent"])
        self.assertNotIn("AUTO_RESTART", r["log"])

    def test_non_alert_round_does_not_restart(self):
        """第 3 次检测不是告警轮（2, 8, 14…）：既不告警也不重启。"""
        r = self._run(GW + LINE_408, prev_count="2")
        self.assertEqual(r["warn"], "3")
        self.assertEqual(r["restarts"], 0)
        self.assertIsNone(r["sent"])

    def test_gap_blocks_restart_within_6h(self):
        now = int(time.time())
        r = self._run(GW + LINE_408, prev_count="1", stamps=[now - 3600])
        self.assertEqual(r["restarts"], 0)
        self.assertEqual(len(r["stamps"]), 1)
        self.assertIn("AUTO_RESTART_SKIPPED", r["log"])
        self.assertIsNotNone(r["sent"])
        self.assertIn("自动重启: 未执行", r["sent"])
        self.assertIn("间隔须 ≥ 6h", r["sent"])
        self.assertIn("bash ~/openclaw-model-bridge/restart.sh", r["sent"])

    def test_gap_elapsed_allows_second_restart(self):
        now = int(time.time())
        r = self._run(GW + LINE_408, after=GW + LINE_HEALTHY, prev_count="1", stamps=[now - 7 * 3600])
        self.assertEqual(r["restarts"], 1)
        self.assertEqual(len(r["stamps"]), 2)
        self.assertIn("24h 内第 2/2 次", r["sent"])

    def test_daily_quota_blocks_third_restart(self):
        now = int(time.time())
        r = self._run(GW + LINE_408, prev_count="1", stamps=[now - 7 * 3600, now - 13 * 3600])
        self.assertEqual(r["restarts"], 0)
        self.assertEqual(len(r["stamps"]), 2)
        self.assertIn("配额用尽", r["sent"])
        self.assertIn("需人工处置", r["sent"])
        self.assertIn("bash ~/openclaw-model-bridge/restart.sh", r["sent"])

    def test_window_expiry_prunes_old_stamps(self):
        now = int(time.time())
        r = self._run(GW + LINE_408, after=GW + LINE_HEALTHY, prev_count="1",
                      stamps=[now - 25 * 3600, now - 30 * 3600])
        self.assertEqual(r["restarts"], 1)
        self.assertEqual(len(r["stamps"]), 1, "过期戳必须被修剪，只留本次")
        self.assertIn("24h 内第 1/2 次", r["sent"])

    def test_not_linked_never_restarts(self):
        r = self._run(GW + LINE_NOT_LINKED, prev_count="1")
        self.assertEqual(r["restarts"], 0)
        self.assertEqual(r["stamps"], [])
        self.assertIsNotNone(r["sent"])
        self.assertIn("openclaw channels login --channel whatsapp", r["sent"])
        self.assertNotIn("自动重启", r["sent"])

    def test_disconnected_only_never_restarts(self):
        r = self._run(GW + LINE_DISCONNECTED_ONLY, prev_count="1")
        self.assertEqual(r["restarts"], 0)
        self.assertIsNotNone(r["sent"])
        self.assertIn("bash ~/openclaw-model-bridge/restart.sh", r["sent"])
        self.assertNotIn("自动重启", r["sent"])

    def test_failed_restart_consumes_quota_and_alert_says_manual(self):
        r = self._run(GW + LINE_408, prev_count="1", restart_rc=1)
        self.assertEqual(r["restarts"], 1)
        self.assertEqual(len(r["stamps"]), 1, "失败的尝试同样计入配额")
        self.assertIn("AUTO_RESTART_FAILED: restart.sh rc=1", r["log"])
        self.assertIn("自动重启: restart.sh 失败 (rc=1", r["sent"])
        self.assertIn("需人工介入", r["sent"])
        self.assertIn("bash ~/openclaw-model-bridge/restart.sh", r["sent"])
        self.assertNotIn("无需操作", r["sent"])

    def test_restart_ok_but_channel_still_stopped_keeps_manual_hint(self):
        r = self._run(GW + LINE_408, after=GW + LINE_408, prev_count="1", restart_rc=0)
        self.assertEqual(r["restarts"], 1)
        self.assertIn("重启后频道: disconnected; stopped", r["sent"])
        self.assertIn("bash ~/openclaw-model-bridge/restart.sh", r["sent"])
        self.assertNotIn("无需操作", r["sent"])

    def test_env_switch_disables_auto_restart(self):
        r = self._run(GW + LINE_408, prev_count="1", env_extra={"WA_AUTO_RESTART": "0"})
        self.assertEqual(r["restarts"], 0)
        self.assertEqual(r["stamps"], [])
        self.assertIn("已停用", r["sent"])
        self.assertIn("bash ~/openclaw-model-bridge/restart.sh", r["sent"])

    def test_missing_restart_script_skips_and_alerts(self):
        r = self._run(GW + LINE_408, prev_count="1", restart_missing=True)
        self.assertEqual(r["restarts"], 0)
        self.assertIn("restart.sh 不存在", r["sent"])
        self.assertIn("bash ~/openclaw-model-bridge/restart.sh", r["sent"])

    def test_old_four_field_parser_is_conservative(self):
        """部署窗口内 shell 新、parser 旧（4 段无 auto）：不自动重启，告警仍建议人工重启。"""
        old = ("import sys\nsys.stdin.read()\n"
               "print('1|1|restart|disconnected; stopped; error=x')\n")
        r = self._run(GW + LINE_408, prev_count="1", parser_src=old)
        self.assertEqual(r["restarts"], 0)
        self.assertIn("状态: disconnected; stopped; error=x", r["sent"])
        self.assertIn("bash ~/openclaw-model-bridge/restart.sh", r["sent"])
        self.assertNotIn("自动重启", r["sent"])

    def test_healthy_resets_and_never_touches_stamps(self):
        now = int(time.time())
        r = self._run(GW + LINE_HEALTHY, prev_count="5", stamps=[now - 3600])
        self.assertEqual(r["warn"], "0")
        self.assertEqual(r["restarts"], 0)
        self.assertEqual(r["stamps"], [now - 3600])
        self.assertIsNone(r["sent"])


# ═══════════════════════════════════════════════════════
# C. 整脚本端到端（真 wa_keepalive.sh）
# ═══════════════════════════════════════════════════════

class TestWholeScriptWiring(unittest.TestCase):
    def _run_script(self, gateway_http, status_line, prev_count="1"):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        bind = os.path.join(tmp, "bin")
        os.makedirs(bind)
        with open(os.path.join(bind, "curl"), "w") as f:
            f.write(f"#!/bin/bash\nprintf '%s' '{gateway_http}'\nexit 0\n")
        os.chmod(os.path.join(bind, "curl"), 0o755)
        count_file = os.path.join(tmp, "restart_calls")
        with open(os.path.join(tmp, "restart.sh"), "w") as f:
            f.write(f"#!/bin/bash\necho x >> \"{count_file}\"\nexit 0\n")
        os.chmod(os.path.join(tmp, "restart.sh"), 0o755)
        with open(os.path.join(tmp, "status.txt"), "w") as f:
            f.write(GW + status_line)
        oc = os.path.join(tmp, "openclaw")
        with open(oc, "w") as f:
            f.write("#!/bin/bash\n"
                    f'if [ "$1" = "channels" ]; then cat "{tmp}/status.txt"; fi\nexit 0\n')
        os.chmod(oc, 0o755)
        shutil.copy(os.path.join(REPO, "wa_channel_status.py"), os.path.join(tmp, "wa_channel_status.py"))
        with open(os.path.join(tmp, ".wa_channel_warn_count"), "w") as f:
            f.write(prev_count)
        env = dict(os.environ)
        env.update({"HOME": tmp, "PATH": bind + ":" + os.environ.get("PATH", ""),
                    "OPENCLAW": oc, "WA_RESTART_SH": os.path.join(tmp, "restart.sh"),
                    "DISCORD_CH_ALERTS": "t"})
        r = subprocess.run(["bash", os.path.join(REPO, "wa_keepalive.sh")],
                           capture_output=True, text=True, timeout=120, env=env)
        calls = len(_cat(count_file).splitlines())
        log = _cat(os.path.join(tmp, "wa_keepalive.log"))
        return r, calls, log

    def test_gateway_down_never_auto_restarts(self):
        r, calls, log = self._run_script("000", LINE_408, prev_count="1")
        self.assertEqual(calls, 0)
        self.assertIn("Gateway 不可达", log)
        self.assertNotIn("AUTO_RESTART", log)

    def test_gateway_up_linked_stopped_second_round_restarts_once(self):
        r, calls, log = self._run_script("200", LINE_408, prev_count="1")
        self.assertEqual(calls, 1, (r.stderr, log))
        self.assertIn("AUTO_RESTART_DONE", log)

    def test_gateway_up_healthy_no_restart(self):
        r, calls, log = self._run_script("200", LINE_HEALTHY, prev_count="1")
        self.assertEqual(calls, 0)
        self.assertIn("WhatsApp 频道 connected", log)


# ═══════════════════════════════════════════════════════
# D. watchdog 契约（MR-8，从 job_watchdog 真源码抽）
# ═══════════════════════════════════════════════════════

class TestWatchdogContract(unittest.TestCase):
    def setUp(self):
        self.wd = _read("job_watchdog.sh")

    def _home_logs_block(self):
        m = re.search(r"\nHOME_LOGS=\((.*?)\n\)", self.wd, re.DOTALL)
        self.assertIsNotNone(m)
        return _strip_comments(m.group(1))

    def test_wa_keepalive_log_not_error_scanned_but_freshness_monitored(self):
        """wa_keepalive 自己已按轮次推 Discord（含自动重启结果），不进 HOME_LOGS 防双重告警；
        死活仍由 LOG_FRESHNESS 盯。"""
        self.assertNotIn("wa_keepalive.log", self._home_logs_block())
        self.assertRegex(self.wd, r'"wa_keepalive\|\$HOME/wa_keepalive\.log\|\d+\|')

    def test_log_lines_vs_err_pattern(self):
        m = re.search(r"local err_pattern='([^']+)'", self.wd)
        self.assertIsNotNone(m)
        pat = m.group(1)

        def hit(line):
            return subprocess.run(["grep", "-ciE", pat], input=line + "\n",
                                  capture_output=True, text=True).stdout.strip() == "1"

        self.assertFalse(hit("[t] AUTO_RESTART: 频道 linked+stopped，执行 /x/restart.sh (24h 内第 1/2 次)"))
        self.assertFalse(hit("[t] AUTO_RESTART_DONE: rc=0, 重启后频道: connected"))
        self.assertFalse(hit("[t] AUTO_RESTART_SKIPPED: 24h 配额用尽 (2/2)"))
        self.assertTrue(hit("[t] AUTO_RESTART_FAILED: restart.sh rc=1, 重启后频道: stopped"),
                        "失败行须保持 FAILED: 形态——若将来进错误扫描，失败仍可见")


# ═══════════════════════════════════════════════════════
# E. 源码守卫（剥注释）
# ═══════════════════════════════════════════════════════

class TestSourceGuards(unittest.TestCase):
    def setUp(self):
        self.src = _read("wa_keepalive.sh")
        self.code = _strip_comments(self.src)
        funcs, names = _extract_funcs(self.src)
        self.funcs = dict(zip(names, funcs))

    def test_bounds_constants(self):
        self.assertRegex(self.code, r"AUTO_RESTART_MIN_GAP_SEC=21600")
        self.assertRegex(self.code, r"AUTO_RESTART_WINDOW_SEC=86400")
        self.assertRegex(self.code, r"AUTO_RESTART_DAILY_MAX=2")
        self.assertRegex(self.code, r'WA_AUTO_RESTART="\$\{WA_AUTO_RESTART:-1\}"')

    def test_stamp_written_before_restart_executes(self):
        body = _strip_comments(self.funcs["_wa_auto_restart"])
        i_stamp = body.index('echo "$now" >> "$WA_AUTO_RESTART_STAMPS"')
        i_run = body.index('bash "$script"')
        self.assertLess(i_stamp, i_run, "先记账再执行：被中途杀掉也要消耗配额")

    def test_auto_restart_only_inside_alert_round(self):
        body = _strip_comments(self.funcs["_wa_channel_check"])
        i_gate = body.index('"$prev" -eq "$ESCALATE_FIRST"')
        i_call = body.index('_wa_auto_restart "$parser"')
        i_msg = body.index('local amsg=')
        self.assertLess(i_gate, i_call)
        self.assertLess(i_call, i_msg, "结果须在告警正文组装之前就绪")
        self.assertIn('if [ "$auto" = "1" ]; then', body)

    def test_five_field_parse_in_shell(self):
        body = _strip_comments(self.funcs["_wa_channel_check"])
        self.assertIn('auto="${rest%%|*}"', body)
        self.assertIn('reason="${rest#*|}"', body)

    def test_restart_exit_code_honesty_still_in_place(self):
        """跨文件（MR-8）：ran_failed 的判定依赖 restart.sh V37.9.308 诚实退出码。"""
        rs = _strip_comments(_read("restart.sh"))
        self.assertIn('if [ -n "$RESTART_FAILED" ]; then', rs)
        self.assertRegex(rs, r'RESTART_FAILED[^\n]*\n[^\n]*\n\s*exit 1')

    def test_registry_and_deploy_wiring(self):
        reg = _read("jobs_registry.yaml")
        m = re.search(r"- id: wa_keepalive\n(.*?)\n\n", reg, re.DOTALL)
        self.assertIsNotNone(m)
        self.assertIn("自动重启", m.group(1))
        dep = _read("auto_deploy.sh")
        for f in ("wa_keepalive.sh", "wa_channel_status.py", "restart.sh"):
            self.assertIn(f"\"{f}|$HOME/{f}\"", dep)
        self.assertIn("test_v37_9_376_wa_bounded_auto_restart", _read("full_regression.sh"))

    def test_scripts_executable(self):
        for f in ("wa_keepalive.sh", "restart.sh"):
            self.assertTrue(os.stat(os.path.join(REPO, f)).st_mode & stat.S_IXUSR, f)
        self.assertEqual(subprocess.run(["bash", "-n", os.path.join(REPO, "wa_keepalive.sh")]).returncode, 0)


if __name__ == "__main__":
    unittest.main()
