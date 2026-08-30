#!/usr/bin/env python3
"""V37.9.283 (对抗审计 NOT-F5 + NOT-F3) 告警冷却 + quiet_alert notify 收编守卫。

NOT-F5 (proxy 告警风暴): ProxyStats token/连续错误告警无 cooldown —— session 过
TOKEN_WARN_THRESHOLD (195K) 后每个请求都 append alert → 每 turn 一条 Discord
[SYSTEM_ALERT] + 一个 notify 子进程 (放大 NOT-F1 并发 drain)；连续错误 streak 内
每个后续错误同款重发。修复 = ProxyStats per-key 冷却窗口 (ALERT_COOLDOWN_SEC,
config.yaml alerts.alert_cooldown_seconds 中心化, 默认 1800)：首次越线立即告警 /
窗口内抑制 / 状态恢复 (token 降回阈值下 · streak 被 success 打断) 重新武装 /
warn→critical 升级不受 warn 冷却影响 (独立 key)。

NOT-F3 (quiet_alert 裸发): auto_deploy 00-07 静默期分支完全绕过 notify (无重试/
无队列/stderr 丢弃)，且告警失败恰与被告警故障同 gateway 路径。修复 = 静默期收编
notify --channel discord --topic alerts (重试 3 次 + 失败队列；队列条目自带
channel=discord, 后续重放仍只走 Discord 不违静默期)，notify 未 source 时保留裸发兜底。
"""
import os
import re
import shutil
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO)

import proxy_filters as _pf
from proxy_filters import (
    ProxyStats,
    ALERT_COOLDOWN_SEC,
    TOKEN_WARN_THRESHOLD,
    TOKEN_CRITICAL_THRESHOLD,
    CONSECUTIVE_ERROR_ALERT,
)


def _read(fname):
    with open(os.path.join(REPO, fname), encoding="utf-8") as f:
        return f.read()


class _StatsIsolationMixin(unittest.TestCase):
    """STATS_FILE 隔离（MR-9: 测试不污染 ~/proxy_stats.json，镜像 test_tool_proxy setUp）。"""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="v283_stats_")
        self._old_stats_file = _pf.STATS_FILE
        _pf.STATS_FILE = os.path.join(self._tmpdir, "proxy_stats.json")
        self.addCleanup(lambda: setattr(_pf, "STATS_FILE", self._old_stats_file))
        self.addCleanup(lambda: shutil.rmtree(self._tmpdir, ignore_errors=True))


class TestTokenAlertCooldown(_StatsIsolationMixin):
    """NOT-F5 token 告警风暴抑制（血案回归核心）。"""

    def test_warn_storm_suppressed(self):
        """血案回归: 阈值持续满足的 5 个连续请求只产 1 条告警（此前 = 5 条）。"""
        stats = ProxyStats()
        for _ in range(5):
            stats.record_success({"prompt_tokens": TOKEN_WARN_THRESHOLD + 100,
                                  "total_tokens": TOKEN_WARN_THRESHOLD + 200})
        alerts = stats.pop_alerts()
        self.assertEqual(len(alerts), 1, f"期望冷却抑制后恰 1 条告警，实得 {len(alerts)}")
        self.assertIn("预警", alerts[0])

    def test_critical_storm_suppressed(self):
        """critical 同款: 5 个连续 critical 请求只产 1 条告警。"""
        stats = ProxyStats()
        for _ in range(5):
            stats.record_success({"prompt_tokens": TOKEN_CRITICAL_THRESHOLD + 100,
                                  "total_tokens": TOKEN_CRITICAL_THRESHOLD + 200})
        alerts = stats.pop_alerts()
        self.assertEqual(len(alerts), 1)
        self.assertIn("临界", alerts[0])

    def test_first_crossing_fires_immediately(self):
        """首次越线立即告警（冷却不引入首报延迟 — 现有 test_token_critical_alert 契约保持）。"""
        stats = ProxyStats()
        stats.record_success({"prompt_tokens": TOKEN_WARN_THRESHOLD + 1,
                              "total_tokens": TOKEN_WARN_THRESHOLD + 2})
        self.assertEqual(len(stats.pop_alerts()), 1)

    def test_warn_to_critical_escalation_immediate(self):
        """warn 冷却中升级到 critical 必须立即告警（独立 key，升级不被抑制）。"""
        stats = ProxyStats()
        stats.record_success({"prompt_tokens": TOKEN_WARN_THRESHOLD + 100,
                              "total_tokens": TOKEN_WARN_THRESHOLD + 200})
        stats.record_success({"prompt_tokens": TOKEN_CRITICAL_THRESHOLD + 100,
                              "total_tokens": TOKEN_CRITICAL_THRESHOLD + 200})
        alerts = stats.pop_alerts()
        self.assertEqual(len(alerts), 2)
        self.assertIn("预警", alerts[0])
        self.assertIn("临界", alerts[1])

    def test_critical_stamps_warn_no_oscillation(self):
        """critical 已叫后降回 warn 带不紧跟重复叫（critical 盖 warn 时间戳）。"""
        stats = ProxyStats()
        stats.record_success({"prompt_tokens": TOKEN_CRITICAL_THRESHOLD + 100,
                              "total_tokens": TOKEN_CRITICAL_THRESHOLD + 200})
        stats.record_success({"prompt_tokens": TOKEN_WARN_THRESHOLD + 100,
                              "total_tokens": TOKEN_WARN_THRESHOLD + 200})
        alerts = stats.pop_alerts()
        self.assertEqual(len(alerts), 1)
        self.assertIn("临界", alerts[0])

    def test_cooldown_expiry_realerts(self):
        """冷却窗口过期且条件仍满足 → 周期性再告警（频率有界非永久静默）。"""
        stats = ProxyStats()
        stats.record_success({"prompt_tokens": TOKEN_WARN_THRESHOLD + 100,
                              "total_tokens": TOKEN_WARN_THRESHOLD + 200})
        stats._alert_last_sent["token_warn"] -= (ALERT_COOLDOWN_SEC + 1)
        stats.record_success({"prompt_tokens": TOKEN_WARN_THRESHOLD + 100,
                              "total_tokens": TOKEN_WARN_THRESHOLD + 200})
        self.assertEqual(len(stats.pop_alerts()), 2)

    def test_rearm_after_session_reset(self):
        """token 降回阈值下（session 重置）→ 重新武装，下次越线立即告警。"""
        stats = ProxyStats()
        stats.record_success({"prompt_tokens": TOKEN_WARN_THRESHOLD + 100,
                              "total_tokens": TOKEN_WARN_THRESHOLD + 200})
        stats.record_success({"prompt_tokens": 1000, "total_tokens": 2000})
        stats.record_success({"prompt_tokens": TOKEN_WARN_THRESHOLD + 100,
                              "total_tokens": TOKEN_WARN_THRESHOLD + 200})
        self.assertEqual(len(stats.pop_alerts()), 2)

    def test_below_threshold_never_alerts(self):
        """低于阈值不告警（既有契约不回归）。"""
        stats = ProxyStats()
        for _ in range(3):
            stats.record_success({"prompt_tokens": 1000, "total_tokens": 2000})
        self.assertEqual(len(stats.pop_alerts()), 0)


class TestConsecutiveErrorCooldown(_StatsIsolationMixin):
    """NOT-F5 连续错误告警风暴抑制。"""

    def test_streak_storm_suppressed(self):
        """血案回归: 10 连错只产 1 条告警（此前 = 第 3~10 个每个都发 = 8 条）。"""
        stats = ProxyStats()
        for _ in range(10):
            stats.record_error(502, "backend down")
        alerts = stats.pop_alerts()
        self.assertEqual(len(alerts), 1)
        self.assertIn("连续", alerts[0])

    def test_threshold_crossing_fires_immediately(self):
        """恰达阈值立即告警（现有 test_consecutive_errors_alert 契约保持）。"""
        stats = ProxyStats()
        for _ in range(CONSECUTIVE_ERROR_ALERT):
            stats.record_error(502, "timeout")
        self.assertEqual(len(stats.pop_alerts()), 1)

    def test_rearm_after_recovery(self):
        """streak 被 success 打断 → 重新武装，下一个 streak 达阈值立即告警。"""
        stats = ProxyStats()
        for _ in range(CONSECUTIVE_ERROR_ALERT):
            stats.record_error(502, "timeout")
        stats.record_success({"prompt_tokens": 100, "total_tokens": 200})
        for _ in range(CONSECUTIVE_ERROR_ALERT):
            stats.record_error(502, "timeout")
        alerts = stats.pop_alerts()
        self.assertEqual(len(alerts), 2, "两个独立 streak 应各告警一次")

    def test_streak_cooldown_expiry_realerts_with_updated_count(self):
        """streak 持续超过冷却窗口 → 再告警且计数已更新（可见恶化程度）。"""
        stats = ProxyStats()
        for _ in range(CONSECUTIVE_ERROR_ALERT):
            stats.record_error(502, "timeout")
        stats._alert_last_sent["consecutive_errors"] -= (ALERT_COOLDOWN_SEC + 1)
        stats.record_error(502, "timeout")
        alerts = stats.pop_alerts()
        self.assertEqual(len(alerts), 2)
        self.assertIn(f"连续 {CONSECUTIVE_ERROR_ALERT + 1} 次", alerts[1])

    def test_recovery_metric_unaffected_by_cooldown(self):
        """冷却不改变 _recovery_total 语义（V37.8.13 修复保持）。"""
        stats = ProxyStats()
        for _ in range(CONSECUTIVE_ERROR_ALERT):
            stats.record_error(502, "timeout")
        stats.record_success({"prompt_tokens": 100, "total_tokens": 200})
        self.assertEqual(stats._recovery_total, 1)
        self.assertEqual(stats.consecutive_errors, 0)


class TestCooldownConfigPlumbing(unittest.TestCase):
    """ALERT_COOLDOWN_SEC 阈值中心化（config.yaml → config_loader → proxy_filters）。"""

    def test_constant_value_from_config(self):
        self.assertEqual(ALERT_COOLDOWN_SEC, 1800)

    def test_config_yaml_has_key(self):
        src = _read("config.yaml")
        self.assertIn("alert_cooldown_seconds", src)

    def test_config_loader_plumbing_with_default(self):
        src = _read("config_loader.py")
        self.assertRegex(
            src,
            r'ALERT_COOLDOWN_SEC.*alert_cooldown_seconds.*1800',
            "config_loader 缺 ALERT_COOLDOWN_SEC ← alerts.alert_cooldown_seconds (默认 1800)",
        )


class TestProxyFiltersSourceGuards(unittest.TestCase):
    """NOT-F5 源码守卫（防冷却门控被回退）。"""

    def setUp(self):
        self.src = _read("proxy_filters.py")

    def test_v37_9_283_marker(self):
        self.assertIn("V37.9.283", self.src)

    def test_should_alert_gates_all_three_keys(self):
        """三个告警 key 都必须经 _should_alert 门控。"""
        for key in ("token_critical", "token_warn", "consecutive_errors"):
            self.assertIn(
                f'self._should_alert("{key}")',
                self.src,
                f"告警 key {key} 未经 _should_alert 冷却门控（NOT-F5 回退）",
            )

    def test_rearm_sites_present(self):
        """状态恢复重新武装：token 降回阈值下 + streak 结束都要 pop 冷却 key。"""
        self.assertIn('self._alert_last_sent.pop("token_warn", None)', self.src)
        self.assertIn('self._alert_last_sent.pop("token_critical", None)', self.src)
        self.assertIn('self._alert_last_sent.pop("consecutive_errors", None)', self.src)

    def test_no_ungated_token_alert_append(self):
        """token 告警 append 必须在 _should_alert 门控之内（正则: append 紧邻前文有门控）。

        V37.9.334 锚点演进: 'Qwen context 临界' → '模型 context 临界'（告警文案去 provider
        硬编码, 守卫意图不变 = 冷却门控必须在 append 之前）。"""
        idx = self.src.find("模型 context 临界")
        self.assertGreater(idx, 0)
        window = self.src[max(0, idx - 400):idx]
        self.assertIn('_should_alert("token_critical")', window,
                      "critical append 前 400 字符内无冷却门控 = 裸 append 回退")


class TestQuietAlertNotifyRouted(unittest.TestCase):
    """NOT-F3 源码守卫：quiet_alert 静默期分支收编 notify（与 test_wa_gateway_resilience
    的 4 个既有 pin 互补——那边守「quiet 块有 discord/无 whatsapp/标记顺序」，
    这边守「quiet 块经 notify 获得重试+队列，裸发只在 fallback 分支」。"""

    def setUp(self):
        self.src = _read("auto_deploy.sh")
        match = re.search(r"quiet_alert\(\)\s*\{(.*?)\n\}", self.src, re.DOTALL)
        assert match, "quiet_alert 函数未找到"
        body = match.group(1)
        self.quiet_block = body.split("return 0")[0] if "return 0" in body else ""

    def test_quiet_block_routes_notify_discord_only(self):
        """静默期主路径必须是 notify --channel discord --topic alerts（重试+队列）。"""
        self.assertIn(
            'notify "$msg" --channel discord --topic alerts',
            self.quiet_block,
            "静默期未收编 notify（NOT-F3 回退到裸发 = 无重试/无队列）",
        )

    def test_quiet_block_bare_send_only_as_fallback(self):
        """裸 openclaw message send 只允许出现在 notify 不可达的 fallback 分支
        （command -v notify 守卫必须在裸发之前）。"""
        guard_pos = self.quiet_block.find("command -v notify")
        bare_pos = self.quiet_block.find("openclaw message send")
        self.assertGreater(guard_pos, -1, "静默期缺 command -v notify 可用性守卫")
        self.assertGreater(bare_pos, guard_pos,
                           "裸发必须在 command -v notify 守卫之后（fallback 分支）")

    def test_v37_9_283_marker_in_quiet_block(self):
        self.assertIn("V37.9.283", self.quiet_block)


if __name__ == "__main__":
    unittest.main(verbosity=2)
