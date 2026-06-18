#!/usr/bin/env python3
"""V37.9.170 notify.sh 微信 (openclaw-weixin) 推送分支守卫。

背景：2026-06-18 WhatsApp 频道因持续掉线/限流被禁用（ev 1027
`config set channels.whatsapp.enabled false`），主推送频道切换为 openclaw-weixin
（腾讯微信插件）。本测试守 notify.sh 的微信分支：
  - 路由正确（NOTIFY_CHANNELS 含 weixin + WEIXIN_TARGET 已设 → 真调
    `message send --channel openclaw-weixin`）
  - WEIXIN_TARGET 未设时整段安全跳过（仅发 Discord，不产生发送失败噪声）
  - 默认 NOTIFY_CHANNELS = openclaw-weixin,discord（源码守卫）
  - WhatsApp 分支向后兼容（显式 NOTIFY_CHANNELS=whatsapp 仍走 whatsapp）

测试用 fake openclaw（把参数写进日志文件）隔离，绝不真调生产 openclaw CLI
（MR-9/MR-23 audit-observes-never-mutates 同族纪律）。
"""
import os
import subprocess
import tempfile
import textwrap
import unittest

_REPO = os.path.dirname(os.path.abspath(__file__))
_NOTIFY = os.path.join(_REPO, "notify.sh")


def _run_notify(channels=None, weixin_target=None, discord_target="u123",
                cli_args="", msg="hello"):
    """真 subprocess source notify.sh + 调 notify，返回 fake openclaw 捕获的调用行。

    fake openclaw 把 "$@" 追加到 OPENCLAW_LOG（绕过 notify.sh 的 >/dev/null）。
    """
    with tempfile.TemporaryDirectory() as td:
        fake = os.path.join(td, "openclaw")
        log = os.path.join(td, "calls.log")
        qdir = os.path.join(td, "queue")
        os.makedirs(qdir, exist_ok=True)
        with open(fake, "w") as f:
            f.write('#!/bin/bash\nprintf "%s\\n" "$*" >> "$OPENCLAW_LOG"\nexit 0\n')
        os.chmod(fake, 0o755)

        env = dict(os.environ)
        env["OPENCLAW"] = fake
        env["OPENCLAW_LOG"] = log
        env["NOTIFY_QUEUE_DIR"] = qdir
        env["DISCORD_TARGET"] = discord_target
        env["NOTIFY_MAX_RETRIES"] = "1"
        if channels is not None:
            env["NOTIFY_CHANNELS"] = channels
        else:
            env.pop("NOTIFY_CHANNELS", None)  # 用 notify.sh 默认值
        if weixin_target is not None:
            env["WEIXIN_TARGET"] = weixin_target
        else:
            env.pop("WEIXIN_TARGET", None)

        script = f'source "{_NOTIFY}" && notify "{msg}" {cli_args} || true'
        subprocess.run(["bash", "-c", script], env=env,
                       capture_output=True, text=True, timeout=20, cwd=_REPO)
        try:
            with open(log) as f:
                return f.read()
        except FileNotFoundError:
            return ""


class TestNotifyWeixinBranch(unittest.TestCase):
    def test_bash_syntax_valid(self):
        r = subprocess.run(["bash", "-n", _NOTIFY], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, f"notify.sh 语法错误:\n{r.stderr}")

    def test_weixin_routed_when_target_set(self):
        calls = _run_notify(channels="openclaw-weixin,discord",
                            weixin_target="6d56c8fb0a0d-im-bot")
        self.assertIn("--channel openclaw-weixin", calls,
                      f"微信分支未触发真实发送:\n{calls}")
        self.assertIn("6d56c8fb0a0d-im-bot", calls)

    def test_weixin_skipped_when_target_unset(self):
        # WEIXIN_TARGET 未设 → 微信分支整段跳过（仅 Discord），无 openclaw-weixin 调用
        calls = _run_notify(channels="openclaw-weixin,discord", weixin_target=None)
        self.assertNotIn("--channel openclaw-weixin", calls,
                         f"WEIXIN_TARGET 未设却仍发微信（应安全跳过）:\n{calls}")
        self.assertIn("--channel discord", calls, "Discord 仍应发送")

    def test_default_channels_is_weixin_discord(self):
        # 不设 NOTIFY_CHANNELS → 用 notify.sh 默认值，应含 openclaw-weixin
        calls = _run_notify(channels=None, weixin_target="t1")
        self.assertIn("--channel openclaw-weixin", calls,
                      f"默认 NOTIFY_CHANNELS 应含 openclaw-weixin:\n{calls}")
        self.assertNotIn("--channel whatsapp", calls,
                         "默认不应再发已禁用的 WhatsApp")

    def test_whatsapp_backward_compat(self):
        # 显式 NOTIFY_CHANNELS=whatsapp 仍走 whatsapp 分支（重启用路径不破坏）
        calls = _run_notify(channels="whatsapp", weixin_target="t1")
        self.assertIn("--channel whatsapp", calls)
        self.assertNotIn("--channel openclaw-weixin", calls,
                         "channels=whatsapp 不应误触发微信分支")

    def test_explicit_channel_weixin_cli(self):
        # notify --channel openclaw-weixin 显式只发微信
        calls = _run_notify(channels="openclaw-weixin,discord", weixin_target="t1",
                            cli_args="--channel openclaw-weixin")
        self.assertIn("--channel openclaw-weixin", calls)
        self.assertNotIn("--channel discord", calls,
                         "--channel openclaw-weixin 应只发微信")


class TestNotifyWeixinSourceGuards(unittest.TestCase):
    """源码级守卫：防回退到 whatsapp,discord 默认 + 微信变量/分支存在。"""

    def setUp(self):
        with open(_NOTIFY) as f:
            self.src = f.read()

    def test_default_not_reverted_to_whatsapp(self):
        self.assertIn('NOTIFY_CHANNELS:-openclaw-weixin,discord}', self.src,
                      "默认 NOTIFY_CHANNELS 应为 openclaw-weixin,discord")
        self.assertNotIn('NOTIFY_CHANNELS:-whatsapp,discord}', self.src,
                         "默认不应回退到旧的 whatsapp,discord")

    def test_weixin_target_var_defined(self):
        self.assertIn('_NOTIFY_WEIXIN_TARGET="${WEIXIN_TARGET:-}"', self.src)

    def test_weixin_branch_present(self):
        self.assertIn('grep -q "weixin"', self.src)
        self.assertIn('_notify_send_with_retry openclaw-weixin', self.src)

    def test_v37_9_170_marker(self):
        self.assertIn("V37.9.170", self.src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
