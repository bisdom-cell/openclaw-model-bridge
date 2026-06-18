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
                cli_args="", msg="hello", return_stderr=False, repeat=1):
    """真 subprocess source notify.sh + 调 notify，返回 fake openclaw 捕获的调用行。

    fake openclaw 把 "$@" 追加到 OPENCLAW_LOG（绕过 notify.sh 的 >/dev/null）。

    return_stderr=True → 返回 (calls, stderr)（V37.9.176 微信跳过 WARN 测试用）。
    repeat=N → 同一进程内连续调 notify N 次（验证一次性 WARN 每进程只 warn 一次）。
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

        body = "; ".join([f'notify "{msg}" {cli_args}'] * repeat)
        script = f'source "{_NOTIFY}"; {body}; true'
        result = subprocess.run(["bash", "-c", script], env=env,
                                capture_output=True, text=True, timeout=20, cwd=_REPO)
        try:
            with open(log) as f:
                calls = f.read()
        except FileNotFoundError:
            calls = ""
        if return_stderr:
            return calls, result.stderr
        return calls


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


class TestWeixinSkipWarn(unittest.TestCase):
    """V37.9.176: NOTIFY_CHANNELS 含 weixin 但 WEIXIN_TARGET 空 → 一次性 stderr WARN。

    背景: 2026-06-18 ai_leaders_bsky 17:02 微信静默漏发（target 当时不在环境），
    Discord 收到、微信无任何信号，多轮排查才定位。V37.9.170 过渡期故意静默；
    过渡结束后这种 misconfig 应可见（cron 日志可 grep），故加一次性 WARN。
    """
    _WARN_SIG = "WEIXIN_TARGET 为空"

    def test_warns_when_weixin_in_channels_but_target_empty(self):
        # 微信在通道里但 target 空 → 应有 WARN + Discord 仍发出（仅微信被跳过）
        calls, err = _run_notify(channels="openclaw-weixin,discord",
                                 weixin_target=None, return_stderr=True)
        self.assertIn(self._WARN_SIG, err, f"应有微信跳过 WARN，stderr=\n{err}")
        self.assertNotIn("--channel openclaw-weixin", calls,
                         "target 空时不应真调微信")
        self.assertIn("--channel discord", calls, "Discord 仍应发出")

    def test_warn_fires_once_per_process(self):
        # 同进程连调 2 次 notify → WARN 只出现 1 次（一次性守卫）
        _, err = _run_notify(channels="openclaw-weixin,discord",
                             weixin_target=None, return_stderr=True, repeat=2)
        self.assertEqual(err.count(self._WARN_SIG), 1,
                         f"WARN 应每进程仅 1 次，实际 {err.count(self._WARN_SIG)}:\n{err}")

    def test_no_warn_when_target_set(self):
        # target 已设 → 正常发微信，无 WARN
        calls, err = _run_notify(channels="openclaw-weixin,discord",
                                 weixin_target="t1", return_stderr=True)
        self.assertNotIn(self._WARN_SIG, err, "target 已设不应 WARN")
        self.assertIn("--channel openclaw-weixin", calls)

    def test_no_warn_when_weixin_not_in_channels(self):
        # 通道里没有 weixin（如 whatsapp,discord）→ 不关微信的事，不应 WARN
        _, err = _run_notify(channels="whatsapp,discord", weixin_target=None,
                             discord_target="u1", return_stderr=True)
        self.assertNotIn(self._WARN_SIG, err,
                         "通道无 weixin 时不应有微信 WARN")

    def test_default_channels_with_empty_target_warns(self):
        # 不显式设 NOTIFY_CHANNELS（默认含 weixin）+ target 空 → 仍 WARN
        # （生产正是用默认通道，这是最该被捕获的场景）
        _, err = _run_notify(channels=None, weixin_target=None, return_stderr=True)
        self.assertIn(self._WARN_SIG, err,
                      "默认通道含 weixin，target 空时也应 WARN")


class TestWeixinSkipWarnSourceGuards(unittest.TestCase):
    """源码级守卫（防回退）+ sabotage 反向验证。"""

    def setUp(self):
        with open(_NOTIFY, encoding="utf-8") as f:
            self.src = f.read()

    def test_skip_warned_guard_var_defined(self):
        self.assertIn('_NOTIFY_WEIXIN_SKIP_WARNED=""', self.src,
                      "一次性 WARN 守卫变量必须在模块作用域初始化（set -u 安全）")

    def test_elif_warn_branch_present(self):
        self.assertIn('_NOTIFY_WEIXIN_SKIP_WARNED', self.src)
        self.assertIn(self._marker_phrase(), self.src)

    def test_v37_9_176_marker(self):
        self.assertIn("V37.9.176", self.src)

    def test_warn_guarded_by_once_flag(self):
        # WARN 分支条件必须含 -z "$_NOTIFY_WEIXIN_SKIP_WARNED"（否则每次 notify 都刷屏）
        self.assertRegex(
            self.src,
            r'elif echo "\$channels" \| grep -q "weixin".*-z "\$_NOTIFY_WEIXIN_SKIP_WARNED"',
            "WARN elif 必须由一次性守卫 flag 把关")

    @staticmethod
    def _marker_phrase():
        return "微信推送被跳过"


if __name__ == "__main__":
    unittest.main(verbosity=2)
