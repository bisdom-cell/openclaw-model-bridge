#!/usr/bin/env python3
"""V37.9.170 notify.sh 微信 (openclaw-weixin) 推送分支守卫。

背景：2026-06-18 WhatsApp 频道因持续掉线/限流被禁用（ev 1027
`config set channels.whatsapp.enabled false`），主推送频道切换为 openclaw-weixin
（腾讯微信插件）。本测试守 notify.sh 的微信分支：
  - 路由正确（NOTIFY_CHANNELS 含 weixin + WEIXIN_TARGET 已设 → 真调
    `message send --channel openclaw-weixin`）
  - WEIXIN_TARGET 未设时整段安全跳过（仅发 Discord，不产生发送失败噪声）
  - 默认 NOTIFY_CHANNELS = discord（V37.9.179: WeChat 客服通道 contextToken 根因做不了
    cron 推送 → 退回 discord-only；weixin 分支保留供显式配置/交互，源码守卫）
  - WhatsApp 分支向后兼容（显式 NOTIFY_CHANNELS=whatsapp 仍走 whatsapp）

测试用 fake openclaw（把参数写进日志文件）隔离，绝不真调生产 openclaw CLI
（MR-9/MR-23 audit-observes-never-mutates 同族纪律）。
"""
import json
import os
import subprocess
import tempfile
import textwrap
import unittest

_REPO = os.path.dirname(os.path.abspath(__file__))
_NOTIFY = os.path.join(_REPO, "notify.sh")


def _run_notify(channels=None, weixin_target=None, discord_target="u123",
                cli_args="", msg="hello", return_stderr=False, repeat=1,
                fake_body=None, max_retries="1"):
    """真 subprocess source notify.sh + 调 notify，返回 fake openclaw 捕获的调用行。

    fake openclaw 把 "$@" 追加到 OPENCLAW_LOG（绕过 notify.sh 的 >/dev/null）。

    return_stderr=True → 返回 (calls, stderr)（V37.9.176 微信跳过 WARN 测试用）。
    repeat=N → 同一进程内连续调 notify N 次（验证一次性 WARN 每进程只 warn 一次）。
    fake_body → 自定义 fake openclaw bash 体（V37.9.180 冷调用超时测试用）。
    max_retries → NOTIFY_MAX_RETRIES（默认 1；冷调用 vs 真失败重试次数区分用 3）。
    """
    with tempfile.TemporaryDirectory() as td:
        fake = os.path.join(td, "openclaw")
        log = os.path.join(td, "calls.log")
        qdir = os.path.join(td, "queue")
        os.makedirs(qdir, exist_ok=True)
        with open(fake, "w") as f:
            f.write("#!/bin/bash\n" + (fake_body or
                    'printf "%s\\n" "$*" >> "$OPENCLAW_LOG"\nexit 0\n'))
        os.chmod(fake, 0o755)

        env = dict(os.environ)
        env["OPENCLAW"] = fake
        env["OPENCLAW_LOG"] = log
        env["NOTIFY_QUEUE_DIR"] = qdir
        env["DISCORD_TARGET"] = discord_target
        env["NOTIFY_MAX_RETRIES"] = max_retries
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

    def test_default_channels_is_discord_only(self):
        # V37.9.179: WeChat 客服通道做不了 cron 推送（contextToken 根因）→ 默认退回 discord-only。
        # 不设 NOTIFY_CHANNELS → 默认应只发 discord，不发 weixin（避免无效的 contextToken-missing 发送）。
        calls = _run_notify(channels=None, weixin_target="t1")
        self.assertNotIn("--channel openclaw-weixin", calls,
                         f"V37.9.179: 默认通道应退回 discord（weixin 收不到 cron 推送）:\n{calls}")
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

    def test_default_channels_is_discord(self):
        # V37.9.179: WeChat 客服通道 contextToken 根因 → 做不了 cron 推送 → 默认退回 discord-only。
        self.assertIn('NOTIFY_CHANNELS:-discord}', self.src,
                      "V37.9.179: 默认 NOTIFY_CHANNELS 应为 discord（唯一可靠定时推送通道）")
        self.assertNotIn('NOTIFY_CHANNELS:-openclaw-weixin,discord}', self.src,
                         "weixin 收不到 cron 推送（contextToken），不应留在默认推送通道")
        self.assertNotIn('NOTIFY_CHANNELS:-whatsapp,discord}', self.src,
                         "WhatsApp 仍 408 禁用，默认不应含（恢复时由 .env_shared 显式设）")

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

    def test_explicit_weixin_channel_empty_target_warns(self):
        # V37.9.179: 默认通道已退回 discord-only（不含 weixin），故 WARN 只在
        # 显式 NOTIFY_CHANNELS 含 openclaw-weixin（供交互/调试）但 target 空时触发。
        _, err = _run_notify(channels="openclaw-weixin,discord", weixin_target=None,
                             return_stderr=True)
        self.assertIn(self._WARN_SIG, err,
                      "显式含 weixin + target 空时应 WARN")

    def test_default_discord_only_no_weixin_warn(self):
        # V37.9.179: 默认 discord-only → 不含 weixin → 不应触发微信 WARN（无效发送已消除）
        _, err = _run_notify(channels=None, weixin_target=None, return_stderr=True)
        self.assertNotIn(self._WARN_SIG, err,
                         "默认 discord-only 不含 weixin，不应有微信跳过 WARN")


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


# ── fake openclaw behaviors for queue-drain tests (V37.9.177 Fix B) ──
_FAKE_POISON = (
    'tgt=""; while [ $# -gt 0 ]; do [ "$1" = "--target" ] && tgt="$2"; shift; done\n'
    'if [ "$tgt" = "BAD" ]; then echo "Error: Unknown target \\"BAD\\" for openclaw-weixin." >&2; exit 1; fi\n'
    'printf "%s\\n" "SENT $tgt" >> "$TD_CALLS"; exit 0\n'
)
_FAKE_TRANSIENT = 'echo "Error: connection refused (backend down)" >&2; exit 1\n'


def _run_drain(entries, fake_behavior, age=()):
    """跑 _notify_drain_queue，返回 (remaining_files:set, stderr:str, calls:str)。

    entries: list[(filename, dict)] 写入临时队列目录。
    fake_behavior: fake openclaw 的 bash 体（决定 exit/stderr）。
    age: 需把 mtime 设为 1h 前的文件名集合（V37.9.230 孤儿 claim 回收测试用）。
    """
    import time as _time
    with tempfile.TemporaryDirectory() as td:
        qd = os.path.join(td, "q")
        os.makedirs(qd)
        fake = os.path.join(td, "openclaw")
        calls = os.path.join(td, "calls.log")
        with open(fake, "w") as f:
            f.write("#!/bin/bash\n" + fake_behavior)
        os.chmod(fake, 0o755)
        for name, d in entries:
            with open(os.path.join(qd, name), "w") as f:
                json.dump(d, f)
        for name in age:
            old_t = _time.time() - 3600
            os.utime(os.path.join(qd, name), (old_t, old_t))
        env = dict(os.environ)
        env["OPENCLAW"] = fake
        env["TD_CALLS"] = calls
        env["NOTIFY_QUEUE_DIR"] = qd
        result = subprocess.run(
            ["bash", "-c", f'source "{_NOTIFY}" && _notify_drain_queue'],
            env=env, capture_output=True, text=True, timeout=20, cwd=_REPO)
        remaining = set(os.listdir(qd))
        try:
            with open(calls) as f:
                callstr = f.read()
        except FileNotFoundError:
            callstr = ""
        return remaining, result.stderr, callstr


class TestNotifyQueueEviction(unittest.TestCase):
    """V37.9.177 Fix B: 队列错误类型感知淘汰 — 永久错误立即淘汰（解队头阻塞），瞬态保留。

    背景: 2026-06-18 占位符 target poison 条目每次 notify 都 REPLAY FAIL 刷屏 + break
    挡住后面正常排队消息永不重放（head-of-line blocking）。
    """
    _POISON = ("20260618_100000_openclaw-weixin_1.json",
               {"ts": "x", "channel": "openclaw-weixin", "target": "BAD",
                "topic": "tech", "msg": "poison"})
    _LEGIT = ("20260618_110000_discord_2.json",
              {"ts": "x", "channel": "discord", "target": "GOOD",
               "topic": "tech", "msg": "legit"})

    def test_permanent_error_evicts_entry(self):
        remaining, err, _ = _run_drain([self._POISON], _FAKE_POISON)
        self.assertEqual(remaining, set(), f"poison(永久错误)应被淘汰，残留={remaining}")
        self.assertIn("REPLAY EVICT", err)

    def test_transient_error_keeps_entry(self):
        name = self._LEGIT[0]
        remaining, err, _ = _run_drain([self._LEGIT], _FAKE_TRANSIENT)
        self.assertIn(name, remaining, "瞬态错误应保留条目（下次重试）")
        self.assertIn("保留队列", err)
        self.assertNotIn("REPLAY EVICT", err, "瞬态错误不应淘汰")

    def test_head_of_line_unblocked(self):
        # poison(oldest) + legit(newer): poison 淘汰后必须继续重放 legit（解队头阻塞）
        remaining, err, calls = _run_drain([self._POISON, self._LEGIT], _FAKE_POISON)
        self.assertEqual(remaining, set(),
                         f"poison 淘汰 + legit 重放成功后队列应空，残留={remaining}")
        self.assertIn("REPLAY EVICT", err)
        self.assertIn("SENT GOOD", calls, "legit 条目必须被重放（不被 poison 卡住）")

    def test_successful_replay_removes_entry(self):
        remaining, err, calls = _run_drain([self._LEGIT], _FAKE_POISON)
        self.assertEqual(remaining, set(), "成功重放应移除条目")
        self.assertIn("REPLAY OK", err)
        self.assertIn("SENT GOOD", calls)


class TestNotifyQueueEvictionSourceGuards(unittest.TestCase):
    def setUp(self):
        with open(_NOTIFY, encoding="utf-8") as f:
            self.src = f.read()

    def test_evict_branch_present(self):
        self.assertIn("REPLAY EVICT", self.src)

    def test_permanent_error_pattern_present(self):
        self.assertRegex(self.src, r"unknown target\|invalid target")

    def test_evict_continues_not_breaks(self):
        # 淘汰分支必须 continue（处理下一条），不能 break（否则没解队头阻塞）
        idx = self.src.find("REPLAY EVICT")
        self.assertNotEqual(idx, -1)
        seg = self.src[idx:idx + 300]
        self.assertIn("continue", seg, "淘汰后必须 continue 处理下一条（解队头阻塞）")

    def test_transient_still_breaks(self):
        # 瞬态分支必须保留 break（避雪崩）。V37.9.230 后瞬态分支多一行
        # mv-back（claim 恢复原名），break 仍必须紧随其后。
        self.assertRegex(self.src, r"保留队列（瞬态.*\n(\s*mv .*\n)?\s*break")

    def test_v37_9_177_marker(self):
        self.assertIn("V37.9.177", self.src)


class TestNotifyQueueClaimV230(unittest.TestCase):
    """V37.9.230 (审计 finding D): drain mv-to-own claim — 消除并发重复投递 TOCTOU。

    两并发 notify 同时 drain 会都 find 到同一队列文件 → 双双重放 = 用户收到重复。
    修复: 处理前 `mv $f $f.claim.$$`（rename 原子只有赢家成功）；瞬态失败 mv 回
    原名（at-least-once 不变）；孤儿 claim（进程在窗口内被杀）>10min 回收。
    """
    _LEGIT = ("20260703_100000_discord_2.json",
              {"ts": "x", "channel": "discord", "target": "GOOD",
               "topic": "tech", "msg": "legit"})

    def test_success_leaves_no_claim_orphan(self):
        """成功重放后队列目录全空（含 claim 文件, 不留孤儿）"""
        remaining, err, calls = _run_drain([self._LEGIT], _FAKE_POISON)
        self.assertEqual(remaining, set())
        self.assertIn("SENT GOOD", calls)

    def test_transient_restores_original_name(self):
        """瞬态失败 → claim 恢复回原 .json 名（at-least-once: 下次 drain 还能重放）"""
        remaining, err, _ = _run_drain([self._LEGIT], _FAKE_TRANSIENT)
        self.assertEqual(remaining, {self._LEGIT[0]},
                         f"瞬态失败后必须恢复原名, 实际残留={remaining}")

    def test_foreign_fresh_claim_untouched(self):
        """他进程新鲜 claim（<10min）不碰不重放 — 这正是防重复投递的机制核心"""
        claimed = (self._LEGIT[0] + ".claim.4242", self._LEGIT[1])
        remaining, err, calls = _run_drain([claimed], _FAKE_POISON)
        self.assertEqual(remaining, {claimed[0]}, "新鲜 claim 必须原样保留")
        self.assertNotIn("SENT GOOD", calls, "被他进程 claim 的条目绝不能重放（重复投递）")

    def test_stale_claim_recovered_and_replayed(self):
        """孤儿 claim（>10min, 进程在窗口内被杀）→ 回收恢复 .json → 本轮即重放"""
        claimed_name = self._LEGIT[0] + ".claim.4242"
        remaining, err, calls = _run_drain(
            [(claimed_name, self._LEGIT[1])], _FAKE_POISON, age=(claimed_name,))
        self.assertEqual(remaining, set(), "孤儿 claim 应被回收并成功重放")
        self.assertIn("SENT GOOD", calls, "回收后的消息必须被重放（at-least-once 不丢失）")

    def test_eviction_semantics_preserved(self):
        """V37.9.177 淘汰语义在 claim 机制下保留: poison 淘汰 + 后续 legit 照常重放"""
        poison = ("20260703_090000_openclaw-weixin_1.json",
                  {"ts": "x", "channel": "openclaw-weixin", "target": "BAD",
                   "topic": "tech", "msg": "poison"})
        remaining, err, calls = _run_drain([poison, self._LEGIT], _FAKE_POISON)
        self.assertEqual(remaining, set())
        self.assertIn("REPLAY EVICT", err)
        self.assertIn("SENT GOOD", calls)


class TestNotifyQueueClaimV230SourceGuards(unittest.TestCase):
    def setUp(self):
        with open(_NOTIFY, encoding="utf-8") as f:
            self.src = f.read()

    def test_claim_mv_present(self):
        self.assertIn('claim="$f.claim.$$"', self.src)
        self.assertIn('mv "$f" "$claim" 2>/dev/null || continue', self.src)

    def test_transient_moves_claim_back(self):
        """瞬态分支必须 mv 回原名（否则消息以 claim 名滞留 = 丢失）"""
        idx = self.src.find("保留队列（瞬态")
        self.assertNotEqual(idx, -1)
        seg = self.src[idx:idx + 200]
        self.assertIn('mv "$claim" "$f"', seg)

    def test_stale_claim_recovery_present(self):
        self.assertIn('-name "*.claim.*"', self.src)
        self.assertIn("-mmin +10", self.src)

    def test_replay_paths_remove_claim_not_original(self):
        """OK/coldcall/evict 三条移除路径删的是 claim 文件（原名已被 mv 走）"""
        self.assertEqual(self.src.count('rm -f "$claim"'), 3)

    def test_v37_9_230_marker(self):
        self.assertIn("V37.9.230", self.src)


class TestColdCallTimeout(unittest.TestCase):
    """V37.9.180: 4.27 CLI 冷调用超时 = gateway 已投递但 CLI 10s 放弃 →
    按已投递处理（不重试避免重复投递、不入队）。2026-06-18 WhatsApp 3 条重复血案。
    """
    _COLD = ('printf "%s\\n" "$*" >> "$OPENCLAW_LOG"\n'
             'echo "Error: gateway timeout after 10000ms" >&2\nexit 1\n')
    _HARD = ('printf "%s\\n" "$*" >> "$OPENCLAW_LOG"\n'
             'echo "Error: connection refused" >&2\nexit 1\n')

    def test_coldcall_no_retry_single_send(self):
        # 冷调用超时 → 只发 1 次（即便 max_retries=3 也早返回，不重复投递）
        calls, err = _run_notify(channels="whatsapp", fake_body=self._COLD,
                                 max_retries="3", return_stderr=True)
        self.assertEqual(calls.count("message send"), 1,
                         f"冷调用超时应只发 1 次（不重试避免重复），实际:\n{calls}")
        self.assertIn("冷调用", err)

    def test_coldcall_not_queued(self):
        # 冷调用超时按已投递处理 → 不入队
        _, err = _run_notify(channels="whatsapp", fake_body=self._COLD,
                             max_retries="3", return_stderr=True)
        self.assertNotIn("QUEUED", err, "冷调用超时应按已投递处理，不入队")

    def test_real_failure_still_retries_and_queues(self):
        # 真失败（connection refused，非超时签名）仍重试满 + 入队（不被误当冷调用）
        calls, err = _run_notify(channels="whatsapp", fake_body=self._HARD,
                                 max_retries="3", return_stderr=True)
        self.assertEqual(calls.count("message send"), 3,
                         f"真失败应重试 3 次（非冷调用），实际:\n{calls}")
        self.assertIn("QUEUED", err)


class TestColdCallTimeoutSourceGuards(unittest.TestCase):
    def setUp(self):
        with open(_NOTIFY, encoding="utf-8") as f:
            self.src = f.read()

    def test_helper_defined(self):
        self.assertIn("_notify_is_coldcall_timeout()", self.src)

    def test_helper_used_in_send_and_drain(self):
        # def + 至少 2 处调用（send 重试路径 + drain 重放路径）
        self.assertGreaterEqual(self.src.count("_notify_is_coldcall_timeout"), 3,
                                "冷调用判别应在发送和重放两条路径都用")

    def test_signature_pattern_present(self):
        self.assertRegex(self.src, r"gateway timeout\|timeout after \[0-9\]\+ms")

    def test_v37_9_180_marker(self):
        self.assertIn("V37.9.180", self.src)


class TestV379236SubshellErrtraceSentinel(unittest.TestCase):
    """V37.9.236: notify.sh $() 站点子 shell errtrace 假 FATAL 根治守卫。

    血案 (2026-07-03): alerts.log 三条 "<label> FATAL abort exit=1 line=101" —
    auto_deploy 12:25:47 + 14:00:02（都紧跟漂移告警推送 ~100s 后）+ watchdog
    12:31:57（12:30 告警推送后）。三个不同脚本报同一 line=101，且各自的第 101 行
    全部无辜 → 真相: LINENO 报的是 sourced 文件 notify.sh 内的行号，101 正是
    _notify_send_with_retry 的 openclaw 发送行。governed caller (set -eE + trap ERR)
    的 ERR trap 在 macOS bash 3.2 下被 $() 子 shell 继承（V37.9.105/131 quirk 同族），
    外层 && / || true 救不了子 shell 内部 → openclaw 设计性非零退出（4.27 冷调用
    超时 = 重试循环的日常输入）在子 shell 内误触发 caller ERR trap → 假 FATAL。
    V37.9.214 governance_audit "line=101 LINENO 误报" 同根真相（当时行号其实是真的）。

    修复 = 哨兵守卫: $() 内 `|| printf '\\n%s' '__NOTIFY_SEND_RC_FAIL__'` 让子 shell
    末命令永远成功，失败经哨兵带出、主 shell 剥哨兵判 rc。不碰 errtrace 状态
    （set +E/-E 切换是 V37.9.214 教训的 landmine，且会泄漏改变 caller 的 MR-19 覆盖）。

    诚实边界: dev bash 5.x 不复现该 3.2 quirk（V37.9.131 实证）→ 行为级只能验证
    哨兵机制正确性（rc 判别/失败路径语义不变），假 FATAL 消失须 Mac Mini 观察
    （下次漂移/watchdog 告警推送撞冷调用超时后 alerts.log 无新 FATAL line=101）。
    """

    @classmethod
    def setUpClass(cls):
        with open(_NOTIFY, encoding="utf-8") as f:
            cls.src = f.read()

    # ── 源码守卫：哨兵在位 + 旧无守卫形态退役 ────────────────────────────

    def test_send_site_sentinel_guarded(self):
        i = self.src.index("_notify_send_with_retry()")
        region = self.src[i:i + 2600]
        self.assertIn(
            ">/dev/null || printf '\\n%s' '__NOTIFY_SEND_RC_FAIL__')", region,
            "发送站点 $() 内层必须有哨兵守卫（子 shell 末命令永远成功）")

    def test_replay_site_sentinel_guarded(self):
        i = self.src.index("_notify_drain_queue()")
        region = self.src[i:i + 4200]
        self.assertIn(
            ">/dev/null || printf '\\n%s' '__NOTIFY_SEND_RC_FAIL__')", region,
            "队列重放站点 $() 内层必须有哨兵守卫")

    def test_old_unguarded_send_form_retired(self):
        self.assertNotIn(
            "--json 2>&1 >/dev/null) && {", self.src,
            "旧形态 `$(openclaw ...) && {` 回归 = 子 shell 内 openclaw 非零退出"
            "重新裸露给 bash 3.2 继承的 ERR trap → 假 FATAL line=101 复发")

    def test_sentinel_exactly_two_send_sites(self):
        self.assertEqual(
            self.src.count("|| printf '\\n%s' '__NOTIFY_SEND_RC_FAIL__'"), 2,
            "哨兵应恰好在 2 个 openclaw 发送站点（send + replay）")

    def test_sentinel_stripped_before_use(self):
        # 剥哨兵 + 剥尾部换行都必须在位（send_err/replay_err 各一组）
        self.assertEqual(self.src.count('%__NOTIFY_SEND_RC_FAIL__}"'), 4,
                         "send/replay 各需 1 次判别 + 1 次剥除 = 4 处 % 模式")

    def test_parse_sites_inner_guarded(self):
        i = self.src.index("_notify_drain_queue()")
        region = self.src[i:i + 4200]
        for key in ("channel", "target", "msg"):
            self.assertIn(f"['{key}'])\" < \"$claim\" 2>/dev/null || true)", region,
                          f"{key} 解析 $() 内层必须 || true（损坏 claim 防假 FATAL）")
            self.assertIn(f'[ -n "${key}" ] ||', region,
                          f"{key} 空输出判失败守卫缺失")
        self.assertNotIn('2>/dev/null) || { mv "$claim"', region,
                         "旧外层 || 形态回归（外层救不了 3.2 子 shell 内部）")

    def test_find_sites_inner_guarded(self):
        self.assertIn('-mmin +10 2>/dev/null || true)"', self.src,
                      "孤儿 claim find 站点内层 || true 缺失")
        self.assertEqual(
            self.src.count('{ find "$_NOTIFY_QUEUE_DIR" -name "*.json" -type f 2>/dev/null || true; }'),
            2, "qfiles + queue_status 两个 find|pipe 站点须内层守卫（pipefail 同族）")

    def test_v37_9_236_marker(self):
        self.assertIn("V37.9.236", self.src)

    # ── 行为级：哨兵机制下失败/成功路径语义不变 ──────────────────────────

    def test_failure_stderr_clean_of_sentinel(self):
        # 真失败：stderr 报错文本必须干净（哨兵剥除后不泄漏进日志/队列）
        calls, err = _run_notify(
            channels="discord", return_stderr=True,
            fake_body='echo "Error: boom-unique-9236" >&2\nexit 1\n')
        self.assertIn("boom-unique-9236", err, "失败 stderr 应被捕获进 ERROR 日志")
        self.assertNotIn("__NOTIFY_SEND_RC_FAIL__", err,
                         "哨兵字符串泄漏进用户可见日志 = 剥除逻辑坏了")

    def test_corrupt_claim_file_handled_gracefully(self):
        # 损坏 claim（invalid JSON）→ 新 [ -n ] 守卫路径：mv 回原名保留，
        # 不 crash、不阻塞后续合法条目
        import time as _time
        with tempfile.TemporaryDirectory() as td:
            qd = os.path.join(td, "q")
            os.makedirs(qd)
            fake = os.path.join(td, "openclaw")
            with open(fake, "w") as f:
                f.write("#!/bin/bash\nexit 0\n")
            os.chmod(fake, 0o755)
            with open(os.path.join(qd, "20260703_000001_discord_1.json"), "w") as f:
                f.write("{not valid json!!")
            with open(os.path.join(qd, "20260703_000002_discord_2.json"), "w") as f:
                json.dump({"ts": "x", "channel": "discord", "target": "T",
                           "topic": "tech", "msg": "legit"}, f)
            env = dict(os.environ)
            env["OPENCLAW"] = fake
            env["NOTIFY_QUEUE_DIR"] = qd
            result = subprocess.run(
                ["bash", "-c", f'source "{_NOTIFY}" && _notify_drain_queue'],
                env=env, capture_output=True, text=True, timeout=20, cwd=_REPO)
            self.assertEqual(result.returncode, 0)
            remaining = set(os.listdir(qd))
            self.assertIn("20260703_000001_discord_1.json", remaining,
                          "损坏条目应 mv 回原名保留（不静默丢失）")
            self.assertNotIn("20260703_000002_discord_2.json", remaining,
                             "合法条目应被重放并移除（损坏条目不阻塞它）")
            self.assertIn("REPLAY OK", result.stderr)

    def test_governed_caller_contract(self):
        # 回归地板：governed 形态（set -eEuo pipefail + trap ERR）下调 notify，
        # openclaw 失败时主脚本必须完整走完且 dev bash 下 trap 不触发。
        # 注: bash 5.x 本就不复现 3.2 的子 shell 继承 quirk，本测试在 dev 不是
        # old/new 判别器——它固化的是"notify 可被 governed 脚本安全调用"契约，
        # 真判别 = Mac Mini alerts.log 观察（docstring 诚实边界）。
        with tempfile.TemporaryDirectory() as td:
            fake = os.path.join(td, "openclaw")
            with open(fake, "w") as f:
                f.write('#!/bin/bash\necho "gateway timeout after 10000ms" >&2\nexit 1\n')
            os.chmod(fake, 0o755)
            trapfile = os.path.join(td, "trap.txt")
            script = os.path.join(td, "governed.sh")
            with open(script, "w") as f:
                f.write(textwrap.dedent(f"""\
                    #!/usr/bin/env bash
                    set -eEuo pipefail
                    trap 'echo "FATAL line=$LINENO" >> "{trapfile}"' ERR
                    source "{_NOTIFY}"
                    notify "drift alert test" --topic alerts >/dev/null 2>&1 || true
                    echo "COMPLETED"
                    """))
            env = dict(os.environ)
            env["OPENCLAW"] = fake
            env["NOTIFY_QUEUE_DIR"] = os.path.join(td, "q")
            env["NOTIFY_CHANNELS"] = "discord"
            env["DISCORD_TARGET"] = "u123"
            result = subprocess.run(["bash", script], env=env,
                                    capture_output=True, text=True, timeout=20, cwd=_REPO)
            self.assertIn("COMPLETED", result.stdout, "governed 脚本必须完整走完")
            self.assertFalse(os.path.exists(trapfile),
                             "notify 内部的设计性失败不得触发 governed caller 的 ERR trap")


if __name__ == "__main__":
    unittest.main(verbosity=2)
