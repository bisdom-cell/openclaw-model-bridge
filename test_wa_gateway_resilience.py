"""V37.8.13 — WhatsApp / Gateway 韧性守卫单测

2026-04-16 血案闭环：Gateway 宕 9h 完全静默
- auto_deploy quiet_alert 凌晨静默期吞掉所有推送（含 CRITICAL）
- wa_keepalive 不告警（设计缺陷）
- restart.sh 不验证 Gateway 是否真活

三层修复：
A. quiet_alert 静默期仅跳过 WhatsApp，Discord 始终推送
B. wa_keepalive 连续 N 次 WARN 自动升级 Discord #alerts
C. restart.sh post-bootstrap health verification
"""

import os
import re
import subprocess
import tempfile
import unittest

REPO = os.path.dirname(os.path.abspath(__file__))


def _read(relpath):
    with open(os.path.join(REPO, relpath), encoding="utf-8") as f:
        return f.read()


# ═══════════════════════════════════════════════════════
# A. auto_deploy.sh quiet_alert — Discord 不被静默
# ═══════════════════════════════════════════════════════

class TestQuietAlertDiscordAlways(unittest.TestCase):
    """quiet_alert 在凌晨静默期仍推 Discord（不跳过双通道）"""

    def setUp(self):
        self.src = _read("auto_deploy.sh")

    def test_quiet_hours_sends_discord(self):
        """静默期路径必须包含 discord message send"""
        # 找到 quiet_alert 函数体
        match = re.search(
            r"quiet_alert\(\)\s*\{(.*?)\n\}",
            self.src,
            re.DOTALL,
        )
        self.assertIsNotNone(match, "quiet_alert 函数未找到")
        body = match.group(1)
        # 在 is_quiet_hours 分支内（return 0 之前）必须有 discord send
        quiet_block = body.split("return 0")[0] if "return 0" in body else ""
        self.assertIn(
            "discord",
            quiet_block,
            "静默期路径缺少 discord 推送 — V37.8.13 修复要求静默期仅跳过 WhatsApp",
        )

    def test_quiet_hours_skips_whatsapp(self):
        """静默期路径不应发 WhatsApp"""
        match = re.search(
            r"quiet_alert\(\)\s*\{(.*?)\n\}",
            self.src,
            re.DOTALL,
        )
        body = match.group(1)
        quiet_block = body.split("return 0")[0] if "return 0" in body else ""
        self.assertNotIn(
            "whatsapp",
            quiet_block,
            "静默期路径不应发 WhatsApp（用户在睡觉）",
        )

    def test_v37_8_13_blood_lesson_comment(self):
        """quiet_alert 必须包含 V37.8.13 血案注释防止回退"""
        self.assertIn("V37.8.13", self.src)
        self.assertIn("Gateway", self.src[:3000])

    def test_system_alert_prefix_before_quiet_check(self):
        """[SYSTEM_ALERT] 前缀必须在静默期判断之前添加（所有路径统一标记）"""
        match = re.search(
            r"quiet_alert\(\)\s*\{(.*?)\n\}",
            self.src,
            re.DOTALL,
        )
        body = match.group(1)
        alert_pos = body.find("SYSTEM_ALERT")
        quiet_pos = body.find("is_quiet_hours")
        self.assertGreater(
            quiet_pos,
            alert_pos,
            "[SYSTEM_ALERT] 标记应在 is_quiet_hours 判断之前（所有路径都标记）",
        )


# ═══════════════════════════════════════════════════════
# B. wa_keepalive.sh — 连续 WARN 升级 Discord 告警
# ═══════════════════════════════════════════════════════

class TestWaKeepaliveEscalation(unittest.TestCase):
    """wa_keepalive 连续 WARN 自动升级到 Discord #alerts"""

    def setUp(self):
        self.src = _read("wa_keepalive.sh")

    def test_warn_count_file_exists(self):
        """必须有 WARN 计数器文件路径定义"""
        self.assertRegex(
            self.src,
            r"WARN_COUNT_FILE|warn_count",
            "缺少 WARN 计数器文件定义",
        )

    def test_counter_increment_on_warn(self):
        """WARN 时必须递增计数器"""
        self.assertRegex(
            self.src,
            r"PREV_COUNT.*\+.*1|count.*\+.*1",
            "WARN 时缺少计数器递增逻辑",
        )

    def test_counter_reset_on_ok(self):
        """OK 时必须重置计数器（写 "0" 到计数器文件）"""
        # 简单守卫：源码中 "Gateway reachable" 后 5 行内必须有 echo "0" > 计数器
        lines = self.src.splitlines()
        found_reset = False
        for i, line in enumerate(lines):
            if "Gateway reachable" in line:
                window = "\n".join(lines[i : i + 5])
                if '"0"' in window and "WARN_COUNT" in window:
                    found_reset = True
                    break
        self.assertTrue(
            found_reset,
            "成功分支（Gateway reachable 后 5 行内）缺少计数器重置",
        )

    def test_escalation_uses_discord_only(self):
        """升级告警必须强制走 Discord（不走 WhatsApp，因为 Gateway 宕时 WA 不通）"""
        self.assertIn("discord", self.src)
        self.assertIn("DISCORD_CH_ALERTS", self.src)

    def test_escalation_threshold_defined(self):
        """升级阈值必须定义（ESCALATE_FIRST）"""
        self.assertRegex(
            self.src,
            r"ESCALATE_FIRST\s*=\s*\d+",
            "缺少 ESCALATE_FIRST 阈值定义",
        )

    def test_v37_8_13_blood_lesson_comment(self):
        """必须包含 V37.8.13 血案注释"""
        self.assertIn("V37.8.13", self.src)

    def test_env_sourced_for_discord_vars(self):
        """必须 source bash_profile 或 env_shared 获取 DISCORD_CH_ALERTS"""
        self.assertRegex(
            self.src,
            r"source.*bash_profile|source.*env_shared",
            "缺少 source bash_profile/env_shared（DISCORD_CH_ALERTS 不会自动可用）",
        )

    def test_system_alert_prefix_in_escalation(self):
        """升级告警必须有 [SYSTEM_ALERT] 前缀"""
        self.assertIn("SYSTEM_ALERT", self.src)

    def test_alert_includes_recovery_command(self):
        """告警消息包含恢复命令（launchctl bootstrap）"""
        self.assertIn("launchctl bootstrap", self.src)


# ═══════════════════════════════════════════════════════
# C. restart.sh — post-bootstrap health verification
# ═══════════════════════════════════════════════════════

class TestRestartGatewayVerification(unittest.TestCase):
    """restart.sh 必须在 bootstrap 后验证 Gateway 健康"""

    def setUp(self):
        self.src = _read("restart.sh")

    def test_health_check_after_bootstrap(self):
        """bootstrap 后必须有 curl localhost:18789 健康探测"""
        bootstrap_pos = self.src.find("launchctl bootstrap")
        health_pos = self.src.find("localhost:18789")
        self.assertGreater(bootstrap_pos, -1, "launchctl bootstrap 未找到")
        self.assertGreater(health_pos, -1, "localhost:18789 健康探测未找到")
        self.assertGreater(
            health_pos,
            bootstrap_pos,
            "健康探测必须在 bootstrap 之后（先启动再验证）",
        )

    def test_retry_loop_exists(self):
        """健康探测必须有重试循环（Gateway 启动需要数秒）"""
        self.assertRegex(
            self.src,
            r"for\s+\w+\s+in\s+1\s+2\s+3|_gw_attempt",
            "缺少重试循环（Gateway 启动可能需要几秒）",
        )

    def test_failure_warning_message(self):
        """健康验证失败时必须输出警告"""
        self.assertIn(
            "Gateway failed to become healthy",
            self.src,
            "缺少健康验证失败的警告输出",
        )

    def test_v37_8_13_blood_lesson_comment(self):
        """必须包含 V37.8.13 血案注释"""
        self.assertIn("V37.8.13", self.src)

    def test_does_not_exit_on_gateway_failure(self):
        """Gateway 验证失败不应 exit 1（Proxy 和 Adapter 已经正常运行）"""
        # 找到 GATEWAY_HEALTHY 检查后的代码块
        match = re.search(
            r"if.*GATEWAY_HEALTHY.*then(.*?)fi",
            self.src,
            re.DOTALL,
        )
        if match:
            block = match.group(1)
            self.assertNotIn(
                "exit 1",
                block,
                "Gateway 验证失败不应 exit 1（Proxy/Adapter 正常）",
            )


# ═══════════════════════════════════════════════════════
# D. 跨文件守卫
# ═══════════════════════════════════════════════════════

class TestCrossFileGuards(unittest.TestCase):
    """跨文件一致性守卫"""

    def test_auto_deploy_file_map_has_wa_keepalive(self):
        """auto_deploy FILE_MAP 必须部署 wa_keepalive.sh"""
        src = _read("auto_deploy.sh")
        self.assertIn("wa_keepalive.sh", src)

    def test_auto_deploy_file_map_has_restart(self):
        """auto_deploy FILE_MAP 必须部署 restart.sh"""
        src = _read("auto_deploy.sh")
        self.assertIn("restart.sh", src)

    def test_wa_keepalive_does_not_send_whatsapp(self):
        """wa_keepalive 的告警路径不应走 WhatsApp（Gateway 宕时 WA 不通）"""
        src = _read("wa_keepalive.sh")
        # 告警消息发送部分不应有 --channel whatsapp
        escalation_block = src[src.find("ESCALAT") :] if "ESCALAT" in src else ""
        self.assertNotIn(
            "--channel whatsapp",
            escalation_block,
            "wa_keepalive 告警不应走 WhatsApp（告警链不得依赖失效主体自身）",
        )

    def test_watchdog_only_monitors_enabled_jobs(self):
        """job_watchdog JOBS 数组里的 job 必须在 jobs_registry.yaml 是 enabled=true

        V37.8.13 教训：扩容 watchdog 时把 pwc (enabled=false 已停用) 加进监控
        → 永久"未更新"告警噪声。修复后加此守卫防回退。
        """
        import re
        try:
            import yaml
        except ImportError:
            self.skipTest("PyYAML 未安装")
        with open(os.path.join(REPO, "jobs_registry.yaml")) as f:
            reg = yaml.safe_load(f)
        enabled_ids = {j["id"] for j in reg["jobs"] if j.get("enabled", False)}
        # 解析 JOBS 数组里的 job_id（每条 "id|status_file|max_silence|name|tier"）
        wd_src = _read("job_watchdog.sh")
        # 仅扫 JOBS 数组段（开始于 JOBS=( 结束于 第一个右括号）
        m = re.search(r"JOBS=\(\s*\n(.*?)\n\)", wd_src, re.DOTALL)
        self.assertIsNotNone(m, "JOBS 数组未找到")
        jobs_block = m.group(1)
        wd_ids = []
        for line in jobs_block.splitlines():
            line = line.strip()
            if not line.startswith('"') or "|" not in line:
                continue
            jid = line.lstrip('"').split("|", 1)[0]
            wd_ids.append(jid)
        # job_watchdog 用了一些别名 (run_hn_fixed→hn_watcher 等)，建立映射
        ALIASES = {
            "run_hn_fixed": "hn_watcher",
            "openclaw_run": "openclaw_run",
            "run_discussions": "run_discussions",
        }
        for wd_id in wd_ids:
            registry_id = ALIASES.get(wd_id, wd_id)
            # 查找 registry 是否有匹配项
            found_enabled = wd_id in enabled_ids or registry_id in enabled_ids
            # 部分 watchdog id 用了 hn_watcher / openclaw_run 这类自定义名
            # 兼容：检查 registry 任何 enabled job id 包含 wd_id 子串
            if not found_enabled:
                found_enabled = any(
                    wd_id in eid or eid in wd_id for eid in enabled_ids
                )
            self.assertTrue(
                found_enabled,
                f"watchdog 监控了 '{wd_id}' 但 registry 中无 enabled 对应 job — "
                "扩容 watchdog 必须只加 enabled 的 job",
            )


if __name__ == "__main__":
    unittest.main()
