"""V37.9.162 — WhatsApp 频道链接状态监控守卫单测

2026-06-16 血案闭环：WhatsApp session 被服务端登出后静默 7 小时
- 凌晨 6 小时重连风暴 (428/499/503) → 08:34 `session logged out` → channel exited
- Gateway HTTP:18789 全程健康 (200)、Discord 全程 connected，但 WhatsApp 频道已死
- wa_keepalive 只探 Gateway HTTP 端口 → 对频道链接状态完全盲 → 零告警

修复（两件套）：
A. wa_channel_status.py — 纯函数解析 `openclaw channels status` 的 WhatsApp 行
B. wa_keepalive.sh — Gateway 健康时额外查频道状态，掉线升级 Discord #alerts
   （MR-14 alert-path-must-not-depend-on-failing-subject — 走 Discord 不走 WhatsApp）

守卫：
- 解析正确性（健康/血案/各负面信号/不确定/缺失行）
- 手机号绝不进 reason / 输出
- FAIL-OPEN（缺失行/解析异常不告警）
- CLI 行为（stdin/文件/异常）
- 反向验证（血案原始行必触发 escalate）
- wa_keepalive.sh 集成源码守卫（调 parser / 走 Discord / 独立计数 / 仅 Gateway-OK 分支）
"""

import os
import re
import subprocess
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO)

from wa_channel_status import (  # noqa: E402
    _find_whatsapp_line,
    format_keepalive_output,
    parse_whatsapp_state,
)


def _read(relpath):
    with open(os.path.join(REPO, relpath), encoding="utf-8") as f:
        return f.read()


# 真实血案行（2026-06-16 08:34 后 channels status 实际输出，含手机号用于泄漏守卫）
INCIDENT_LINE = (
    "- WhatsApp default: enabled, configured, not linked, stopped, "
    "disconnected, dm:allowlist, allow:+85200000000, error:not linked"
)
HEALTHY_LINE = (
    "- WhatsApp default: enabled, configured, linked, running, connected, "
    "dm:allowlist, allow:+85200000000"
)


# ═══════════════════════════════════════════════════════
# A. 解析正确性
# ═══════════════════════════════════════════════════════

class TestFindWhatsAppLine(unittest.TestCase):
    def test_finds_whatsapp_line(self):
        txt = "Gateway reachable.\n- Discord default: connected\n" + HEALTHY_LINE
        self.assertEqual(_find_whatsapp_line(txt), HEALTHY_LINE)

    def test_case_insensitive(self):
        txt = "- whatsapp default: enabled, linked, connected"
        self.assertIsNotNone(_find_whatsapp_line(txt))

    def test_skips_discord_decorations(self):
        txt = "│\n◇\nGateway reachable.\n- Discord default: connected"
        self.assertIsNone(_find_whatsapp_line(txt))

    def test_empty_or_non_string(self):
        self.assertIsNone(_find_whatsapp_line(""))
        self.assertIsNone(_find_whatsapp_line(None))

    def test_line_without_colon_skipped(self):
        # 含 whatsapp 但无 ":" 不算频道状态行
        self.assertIsNone(_find_whatsapp_line("whatsapp is great"))


class TestParseWhatsAppState(unittest.TestCase):
    def test_incident_line_escalates(self):
        st = parse_whatsapp_state("Gateway reachable.\n" + INCIDENT_LINE)
        self.assertTrue(st["present"])
        self.assertFalse(st["linked"])
        self.assertFalse(st["connected"])
        self.assertTrue(st["disconnected"])
        self.assertTrue(st["stopped"])
        self.assertEqual(st["error"], "not linked")
        self.assertTrue(st["should_escalate"])

    def test_healthy_line_no_escalate(self):
        st = parse_whatsapp_state("Gateway reachable.\n" + HEALTHY_LINE)
        self.assertTrue(st["present"])
        self.assertTrue(st["linked"])
        self.assertTrue(st["connected"])
        self.assertFalse(st["disconnected"])
        self.assertFalse(st["stopped"])
        self.assertIsNone(st["error"])
        self.assertFalse(st["should_escalate"])
        self.assertEqual(st["reason"], "connected")

    def test_not_linked_token_not_confused_with_linked(self):
        # "not linked" 含子串 "linked"，必须精确 token 区分
        st = parse_whatsapp_state("- WhatsApp default: enabled, not linked")
        self.assertFalse(st["linked"])
        self.assertTrue(st["should_escalate"])

    def test_disconnected_not_confused_with_connected(self):
        st = parse_whatsapp_state("- WhatsApp default: enabled, linked, disconnected")
        self.assertFalse(st["connected"])
        self.assertTrue(st["disconnected"])
        self.assertTrue(st["should_escalate"])

    def test_only_stopped_escalates(self):
        st = parse_whatsapp_state("- WhatsApp default: enabled, linked, connected, stopped")
        self.assertTrue(st["stopped"])
        self.assertTrue(st["should_escalate"])

    def test_only_error_escalates(self):
        st = parse_whatsapp_state("- WhatsApp default: enabled, linked, connected, error:auth failure")
        self.assertEqual(st["error"], "auth failure")
        self.assertTrue(st["should_escalate"])

    def test_missing_line_fail_open(self):
        st = parse_whatsapp_state("Gateway reachable.\n- Discord default: connected")
        self.assertFalse(st["present"])
        self.assertFalse(st["should_escalate"])
        self.assertEqual(st["reason"], "whatsapp_line_not_found")

    def test_indeterminate_present_no_escalate(self):
        # 频道行存在但既无明确正面也无明确负面 → 不告警（FAIL-OPEN 防格式变化误报）
        st = parse_whatsapp_state("- WhatsApp default: enabled, configured")
        self.assertTrue(st["present"])
        self.assertFalse(st["should_escalate"])
        self.assertEqual(st["reason"], "indeterminate")

    def test_reason_lists_all_negative_signals(self):
        st = parse_whatsapp_state(INCIDENT_LINE)
        for sig in ("not linked", "disconnected", "stopped", "error=not linked"):
            self.assertIn(sig, st["reason"])

    def test_empty_input(self):
        st = parse_whatsapp_state("")
        self.assertFalse(st["present"])
        self.assertFalse(st["should_escalate"])


# ═══════════════════════════════════════════════════════
# B. 手机号泄漏守卫（reason / 输出绝不含 allow:+... 号码）
# ═══════════════════════════════════════════════════════

class TestNoPhoneLeak(unittest.TestCase):
    def test_incident_reason_no_phone(self):
        st = parse_whatsapp_state(INCIDENT_LINE)
        self.assertNotIn("85200000000", st["reason"])

    def test_healthy_reason_no_phone(self):
        st = parse_whatsapp_state(HEALTHY_LINE)
        self.assertNotIn("85200000000", st["reason"])

    def test_format_output_no_phone(self):
        for line in (INCIDENT_LINE, HEALTHY_LINE):
            out = format_keepalive_output(parse_whatsapp_state(line))
            self.assertNotIn("85200000000", out)

    def test_allow_token_never_in_reason(self):
        # allow:+... token 永不进 reason（reason 只拼负面信号）
        st = parse_whatsapp_state(INCIDENT_LINE)
        self.assertNotIn("allow:", st["reason"])


# ═══════════════════════════════════════════════════════
# C. format_keepalive_output
# ═══════════════════════════════════════════════════════

class TestFormatKeepaliveOutput(unittest.TestCase):
    def test_escalate_format(self):
        out = format_keepalive_output(parse_whatsapp_state(INCIDENT_LINE))
        self.assertTrue(out.startswith("1|1|"))

    def test_healthy_format(self):
        out = format_keepalive_output(parse_whatsapp_state(HEALTHY_LINE))
        self.assertEqual(out, "0|1|connected")

    def test_missing_format(self):
        out = format_keepalive_output(parse_whatsapp_state("- Discord: connected"))
        self.assertEqual(out, "0|0|whatsapp_line_not_found")

    def test_pipe_delimited_three_fields(self):
        out = format_keepalive_output(parse_whatsapp_state(INCIDENT_LINE))
        # reason 内含 "; " 但字段分隔是 "|"，恰好 3 段
        self.assertEqual(len(out.split("|")), 3)


# ═══════════════════════════════════════════════════════
# D. CLI 行为（subprocess 真跑）
# ═══════════════════════════════════════════════════════

class TestCliBehavior(unittest.TestCase):
    def _run(self, stdin_text=None, arg=None):
        cmd = [sys.executable, os.path.join(REPO, "wa_channel_status.py")]
        if arg:
            cmd.append(arg)
        return subprocess.run(
            cmd, input=stdin_text, capture_output=True, text=True, timeout=30
        )

    def test_stdin_incident(self):
        r = self._run(stdin_text="Gateway reachable.\n" + INCIDENT_LINE)
        self.assertEqual(r.returncode, 0)
        self.assertTrue(r.stdout.strip().startswith("1|1|"))

    def test_stdin_healthy(self):
        r = self._run(stdin_text="Gateway reachable.\n" + HEALTHY_LINE)
        self.assertEqual(r.stdout.strip(), "0|1|connected")

    def test_stdin_empty_fail_open(self):
        r = self._run(stdin_text="")
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout.strip(), "0|0|whatsapp_line_not_found")

    def test_file_arg(self):
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
            f.write("Gateway reachable.\n" + INCIDENT_LINE)
            path = f.name
        try:
            r = self._run(arg=path)
            self.assertTrue(r.stdout.strip().startswith("1|1|"))
        finally:
            os.unlink(path)

    def test_cli_no_phone_leak(self):
        r = self._run(stdin_text=INCIDENT_LINE)
        self.assertNotIn("85200000000", r.stdout)


# ═══════════════════════════════════════════════════════
# E. 反向验证（血案原始行必触发 escalate；篡改解析器立即被抓）
# ═══════════════════════════════════════════════════════

class TestReverseValidation(unittest.TestCase):
    def test_exact_incident_line_escalates(self):
        """2026-06-16 血案原始 channels status 行必须触发 escalate=1"""
        st = parse_whatsapp_state(INCIDENT_LINE)
        self.assertTrue(
            st["should_escalate"],
            "血案原始行未触发 escalate — 这正是静默 7h 要防的场景",
        )

    def test_healthy_line_must_not_escalate(self):
        """健康行绝不能误报（否则恢复后仍告警噪声）"""
        st = parse_whatsapp_state(HEALTHY_LINE)
        self.assertFalse(st["should_escalate"])


# ═══════════════════════════════════════════════════════
# F. wa_keepalive.sh 集成源码守卫
# ═══════════════════════════════════════════════════════

class TestWaKeepaliveIntegration(unittest.TestCase):
    def setUp(self):
        self.src = _read("wa_keepalive.sh")

    def test_v37_9_162_marker(self):
        self.assertIn("V37.9.162", self.src)

    def test_calls_channel_check_function(self):
        self.assertIn("_wa_channel_check", self.src)

    def test_invokes_parser(self):
        self.assertIn("wa_channel_status.py", self.src)

    def test_separate_warn_counter(self):
        """频道掉线用独立计数器（与 Gateway-down 计数解耦）"""
        self.assertIn("WA_CHANNEL_WARN_FILE", self.src)
        self.assertNotIn(
            "$WA_CHANNEL_WARN_FILE\" = \"$WARN_COUNT_FILE",
            self.src,
            "频道计数器不应与 Gateway 计数器同一文件",
        )

    def test_escalation_uses_discord_only(self):
        """频道掉线升级的 *发送路径* 必须走 Discord（WhatsApp 已死，MR-14）

        注：恢复指令文本里出现 `channels login --channel whatsapp` 是合法的（给用户看的
        修复命令），但 `message send` 的实际发送路径绝不能是 whatsapp。
        """
        m = re.search(r"_wa_channel_check\(\)\s*\{(.*?)\n\}", self.src, re.DOTALL)
        self.assertIsNotNone(m, "_wa_channel_check 函数体未找到")
        body = m.group(1)
        self.assertIn("message send --channel discord", body)
        self.assertNotIn(
            "message send --channel whatsapp",
            body,
            "频道掉线告警的发送路径不得走 WhatsApp（告警链不得依赖失效主体自身）",
        )

    def test_check_only_in_gateway_ok_branch(self):
        """_wa_channel_check 调用点在 Gateway-OK 分支（Gateway 宕时 status 无意义）"""
        # 调用点必须在 "Gateway reachable" 之后、"else" 之前
        ok_pos = self.src.find("Gateway reachable (HTTP")
        call_pos = self.src.find("    _wa_channel_check\n")
        self.assertGreater(ok_pos, -1)
        self.assertGreater(call_pos, ok_pos, "_wa_channel_check 调用应在 Gateway-OK 分支内")

    def test_fail_open_parser_missing(self):
        """parser 缺失时 FAIL-OPEN（return 0 不阻塞）"""
        m = re.search(r"_wa_channel_check\(\)\s*\{(.*?)\n\}", self.src, re.DOTALL)
        body = m.group(1)
        self.assertIn("return 0", body)
        self.assertIn("FAIL-OPEN", body)

    def test_recovery_command_in_alert(self):
        """告警消息含恢复命令 channels login"""
        self.assertIn("openclaw channels login --channel whatsapp", self.src)

    def test_system_alert_prefix(self):
        m = re.search(r"_wa_channel_check\(\)\s*\{(.*?)\n\}", self.src, re.DOTALL)
        body = m.group(1)
        self.assertIn("SYSTEM_ALERT", body)

    def test_reuses_escalate_thresholds(self):
        """复用既有 ESCALATE_FIRST / ESCALATE_REPEAT 阈值（与 Gateway 一致）"""
        m = re.search(r"_wa_channel_check\(\)\s*\{(.*?)\n\}", self.src, re.DOTALL)
        body = m.group(1)
        self.assertIn("ESCALATE_FIRST", body)
        self.assertIn("ESCALATE_REPEAT", body)


# ═══════════════════════════════════════════════════════
# G. 行为级集成（fake openclaw stub 真跑 wa_keepalive 频道检查逻辑）
# ═══════════════════════════════════════════════════════

class TestChannelCheckRuntime(unittest.TestCase):
    """用 fake openclaw + 隔离 HOME 真跑 _wa_channel_check，验证端到端行为。"""

    def _run_check(self, channels_status_output, prev_count="0"):
        """构造隔离环境跑 wa_keepalive.sh 的 _wa_channel_check（单独 source + 调用）。

        Returns (warn_count_after, log_contents, discord_sent_msg_or_None)
        """
        tmp = tempfile.mkdtemp()
        # fake openclaw: `channels status` 打印给定输出; `message send` 把 --message 写文件
        fake_openclaw = os.path.join(tmp, "openclaw")
        sent_marker = os.path.join(tmp, "discord_sent.txt")
        with open(fake_openclaw, "w") as f:
            f.write(
                "#!/bin/bash\n"
                'if [ "$1" = "channels" ] && [ "$2" = "status" ]; then\n'
                f"  cat {tmp}/status_out.txt\n"
                "  exit 0\n"
                "fi\n"
                'if [ "$1" = "message" ] && [ "$2" = "send" ]; then\n'
                "  # 提取 --message 的下一个参数写入 marker\n"
                "  while [ $# -gt 0 ]; do\n"
                '    if [ "$1" = "--message" ]; then shift; echo "$1" > '
                f"{sent_marker}; fi\n"
                "    shift\n"
                "  done\n"
                "  exit 0\n"
                "fi\n"
                "exit 0\n"
            )
        os.chmod(fake_openclaw, 0o755)
        with open(os.path.join(tmp, "status_out.txt"), "w") as f:
            f.write(channels_status_output)
        # 把 wa_channel_status.py 复制进 fake HOME（_wa_channel_check 找 $HOME/wa_channel_status.py）
        import shutil
        shutil.copy(os.path.join(REPO, "wa_channel_status.py"),
                    os.path.join(tmp, "wa_channel_status.py"))
        warn_file = os.path.join(tmp, ".wa_channel_warn_count")
        with open(warn_file, "w") as f:
            f.write(prev_count)
        log_file = os.path.join(tmp, "wa_keepalive.log")

        # 提取 _wa_channel_check 函数体 + 必要变量，构造可独立运行的脚本
        harness = f"""#!/bin/bash
HOME="{tmp}"
OPENCLAW="{fake_openclaw}"
LOG="{log_file}"
TS="2026-06-16 16:00:00"
WA_CHANNEL_WARN_FILE="{warn_file}"
ESCALATE_FIRST=2
ESCALATE_REPEAT=6
DISCORD_CH_ALERTS="test-alerts"
"""
        # 抽 _wa_channel_check 函数定义
        src = _read("wa_keepalive.sh")
        m = re.search(r"(_wa_channel_check\(\)\s*\{.*?\n\})", src, re.DOTALL)
        harness += m.group(1) + "\n_wa_channel_check\n"
        harness_path = os.path.join(tmp, "harness.sh")
        with open(harness_path, "w") as f:
            f.write(harness)

        subprocess.run(["bash", harness_path], capture_output=True, text=True, timeout=30)
        warn_after = open(warn_file).read().strip() if os.path.exists(warn_file) else "?"
        log = open(log_file).read() if os.path.exists(log_file) else ""
        sent = open(sent_marker).read() if os.path.exists(sent_marker) else None
        shutil.rmtree(tmp, ignore_errors=True)
        return warn_after, log, sent

    def test_incident_first_warn_no_escalate_yet(self):
        """血案行第 1 次：计数 0→1，未达 ESCALATE_FIRST=2，不推 Discord"""
        warn, log, sent = self._run_check("Gateway reachable.\n" + INCIDENT_LINE, prev_count="0")
        self.assertEqual(warn, "1")
        self.assertIn("WhatsApp 频道异常", log)
        self.assertIsNone(sent, "第 1 次不应推 Discord")

    def test_incident_second_warn_escalates_discord(self):
        """血案行第 2 次：计数 1→2，达阈值，推 Discord #alerts"""
        warn, log, sent = self._run_check("Gateway reachable.\n" + INCIDENT_LINE, prev_count="1")
        self.assertEqual(warn, "2")
        self.assertIsNotNone(sent, "第 2 次应推 Discord")
        self.assertIn("WhatsApp 频道连续 2 次掉线", sent)
        self.assertIn("SYSTEM_ALERT", sent)
        self.assertNotIn("85200000000", sent, "告警不得泄漏手机号")

    def test_healthy_resets_counter(self):
        """健康行：计数重置为 0，不推 Discord"""
        warn, log, sent = self._run_check("Gateway reachable.\n" + HEALTHY_LINE, prev_count="5")
        self.assertEqual(warn, "0")
        self.assertIsNone(sent)
        self.assertIn("WhatsApp 频道 connected", log)

    def test_missing_line_resets_no_escalate(self):
        """缺 WhatsApp 行：FAIL-OPEN，计数重置，不推 Discord"""
        warn, log, sent = self._run_check("Gateway reachable.\n- Discord: connected", prev_count="3")
        self.assertEqual(warn, "0")
        self.assertIsNone(sent)


# ═══════════════════════════════════════════════════════
# H. 部署 / 注册守卫
# ═══════════════════════════════════════════════════════

class TestDeploymentGuards(unittest.TestCase):
    def test_auto_deploy_file_map_has_parser(self):
        self.assertIn("wa_channel_status.py", _read("auto_deploy.sh"))

    def test_full_regression_registers_suite(self):
        self.assertIn("test_wa_channel_status", _read("full_regression.sh"))


if __name__ == "__main__":
    unittest.main()
