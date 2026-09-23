#!/usr/bin/env python3
"""V37.9.359 notify 返回码语义守卫 —「≥1 通道送达即 0」。

血案 (2026-09-23): WhatsApp 428「Connection Terminated」掉线 ~8h, Discord 全程送达,
但 notify() 只要任一通道失败就 return 1 → 每个 job 自记 send_failed + watchdog 给 arxiv
发 CORE 假告警 + arxiv 未标 seen 准备重推 (Discord 重复)。成功被说成失败 =
fail-plausible 的反向形态。preflight 16/19 注释 (V37.9.174) 早已声明契约「发出≥1 即 0」,
实现却不是 = 声称与事实无人对账 (原则 #36-4)。

修复: 部分失败 → 失败通道入队 (at-least-once 重放) + stderr 打稳定信号 PARTIAL: + rc 0;
全通道失败仍 rc 1。preflight 据 PARTIAL: 给 warn (不再报「全通道未发出」fail, 也不静默 pass)。

测试全部用 fake openclaw + 隔离队列目录, 绝不真调生产 CLI (MR-9/MR-23)。
"""
import os
import re
import subprocess
import tempfile
import unittest


def _read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()

_REPO = os.path.dirname(os.path.abspath(__file__))
_NOTIFY = os.path.join(_REPO, "notify.sh")
_PREFLIGHT = os.path.join(_REPO, "preflight_check.sh")
_WATCHDOG = os.path.join(_REPO, "job_watchdog.sh")

# fake openclaw: 指定通道失败 (非冷调用超时签名的普通错误), 其余成功
_FAKE_TMPL = """#!/bin/bash
printf "%s\\n" "$*" >> "$OPENCLAW_LOG"
for ch in {failing}; do
  if [[ " $* " == *" --channel $ch "* ]]; then
    echo "Error: Connection Terminated (428 Precondition Required)" >&2
    exit 1
  fi
done
exit 0
"""


def _run(channels, failing=(), extra=""):
    """返回 (rc, stderr, queued_files)。"""
    with tempfile.TemporaryDirectory() as td:
        fake = os.path.join(td, "openclaw")
        qdir = os.path.join(td, "queue")
        os.makedirs(qdir)
        with open(fake, "w") as f:
            f.write(_FAKE_TMPL.format(failing=" ".join(failing) or "__none__"))
        os.chmod(fake, 0o755)
        env = dict(os.environ)
        env.update({
            "OPENCLAW": fake, "OPENCLAW_LOG": os.path.join(td, "calls.log"),
            "NOTIFY_QUEUE_DIR": qdir, "NOTIFY_CHANNELS": channels,
            "NOTIFY_MAX_RETRIES": "1", "DISCORD_TARGET": "u123",
            "OPENCLAW_PHONE": "+85200000000",
        })
        env.pop("WEIXIN_TARGET", None)
        script = f'source "{_NOTIFY}"; notify "hello" {extra}; echo "RC=$?"'
        r = subprocess.run(["bash", "-c", script], env=env, capture_output=True,
                           text=True, timeout=30, cwd=td)
        m = re.search(r"RC=(\d+)", r.stdout)
        assert m, f"未拿到 rc:\n{r.stdout}\n{r.stderr}"
        return int(m.group(1)), r.stderr, sorted(os.listdir(qdir))


class TestNotifyReturnSemantics(unittest.TestCase):
    def test_blood_lesson_partial_delivery_returns_zero(self):
        # 2026-09-23 形态: WhatsApp 掉线, Discord 送达 → 用户已收到 → rc 0
        rc, err, q = _run("whatsapp,discord", failing=("whatsapp",))
        self.assertEqual(rc, 0, f"部分送达必须 rc=0 (否则 job 记 send_failed):\n{err}")
        self.assertIn("PARTIAL:", err, "部分失败必须留稳定信号, 不得静默")
        self.assertEqual(len(q), 1, f"失败通道必须入队待重放: {q}")
        self.assertIn("whatsapp", q[0])

    def test_all_channels_fail_still_returns_one(self):
        # 检出力不减: 用户什么都没收到时必须失败
        rc, err, q = _run("whatsapp,discord", failing=("whatsapp", "discord"))
        self.assertEqual(rc, 1)
        self.assertNotIn("PARTIAL:", err, "全失败不是部分失败")
        self.assertEqual(len(q), 2)

    def test_single_channel_failure_returns_one(self):
        # 默认 discord-only 配置下 Discord 挂 = 全失败
        rc, err, _ = _run("discord", failing=("discord",))
        self.assertEqual(rc, 1)

    def test_all_ok_no_partial_signal(self):
        rc, err, q = _run("whatsapp,discord")
        self.assertEqual(rc, 0)
        self.assertNotIn("PARTIAL:", err, "全部送达不得误报部分失败")
        self.assertEqual(q, [])

    def test_explicit_channel_failure_returns_one(self):
        # kb_deep_dive 类 --channel whatsapp 单发: 掉线必须 rc 1 (它自己的 Discord 另发)
        rc, _, _ = _run("whatsapp,discord", failing=("whatsapp",),
                        extra="--channel whatsapp")
        self.assertEqual(rc, 1)

    def test_consumer_pattern_records_sent_on_partial(self):
        # 40+ job 的真实消费形态: if notify ...; then sent; else send_failed
        with tempfile.TemporaryDirectory() as td:
            fake = os.path.join(td, "openclaw")
            with open(fake, "w") as f:
                f.write(_FAKE_TMPL.format(failing="whatsapp"))
            os.chmod(fake, 0o755)
            env = dict(os.environ, OPENCLAW=fake, OPENCLAW_LOG=os.path.join(td, "l"),
                       NOTIFY_QUEUE_DIR=os.path.join(td, "q"),
                       NOTIFY_CHANNELS="whatsapp,discord", NOTIFY_MAX_RETRIES="1",
                       DISCORD_CH_PAPERS="c1", DISCORD_TARGET="u1")
            script = (f'set -eo pipefail; source "{_NOTIFY}"; '
                      'if notify "msg" --topic papers 2>/dev/null; then echo STATUS=sent; '
                      'else echo STATUS=send_failed; fi')
            r = subprocess.run(["bash", "-c", script], env=env, capture_output=True,
                               text=True, timeout=30, cwd=td)
            self.assertIn("STATUS=sent", r.stdout, r.stderr)


class TestPartialSignalContract(unittest.TestCase):
    def test_partial_line_not_matching_watchdog_err_pattern(self):
        # PARTIAL 是成功态附注, 不应被 watchdog 日志扫描当错误; 失败通道的 FAIL: 行仍会被扫到
        src = _read(_WATCHDOG)
        m = re.search(r"local err_pattern='([^']+)'", src)
        self.assertTrue(m, "job_watchdog.sh 必须仍定义 err_pattern")
        pat = re.compile(m.group(1), re.I)
        _, err, _ = _run("whatsapp,discord", failing=("whatsapp",))
        partial = [l for l in err.splitlines() if "PARTIAL:" in l]
        self.assertEqual(len(partial), 1)
        self.assertIsNone(pat.search(partial[0]), partial[0])
        # 反向证据: 失败通道本身的 FAIL: 行仍可见 (部分失败不被洗白)
        self.assertTrue(any(pat.search(l) for l in err.splitlines() if "FAIL:" in l))


def _preflight_block():
    src = _read(_PREFLIGHT)
    start = src.index('if notify "🔧 preflight push test')
    end = src.index('warn "notify.sh 未加载', start)
    blk = src[start:end]
    blk = blk[:blk.rindex("else")]  # 去掉 command -v 的 else 分支
    return blk


class TestPreflightPushCheck(unittest.TestCase):
    def _run_block(self, notify_body):
        blk = _preflight_block()
        with tempfile.TemporaryDirectory() as td:
            script = (
                'pass(){ echo "PASS:$1"; }; warn(){ echo "WARN:$1"; }; fail(){ echo "FAIL_CHK:$1"; }\n'
                f'notify(){{ {notify_body} }}\n'
                f'PUSH_ERR="{td}/err"; PUSH_TEST_LAST="{td}/last"\n'
                + blk + f'\n[ -f "{td}/last" ] && echo STAMPED || echo NOT_STAMPED\n')
            r = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                               timeout=20)
            return r.stdout + r.stderr

    def test_partial_is_warn_not_fail_and_not_stamped(self):
        out = self._run_block(
            'echo "[notify] FAIL: WhatsApp 3次重试均失败，入队" >&2; '
            'echo "[notify] PARTIAL: 1 个通道已送达" >&2; return 0;')
        self.assertIn("WARN:推送部分通道失败", out)
        self.assertIn("FAIL: WhatsApp", out, "warn 需带出具体失败通道")
        self.assertNotIn("FAIL_CHK", out)
        self.assertNotIn("PASS:", out)
        self.assertIn("NOT_STAMPED", out, "部分失败不得写小时缓存, 下次 preflight 须重测")

    def test_full_success_is_pass_and_stamped(self):
        out = self._run_block('return 0;')
        self.assertIn("PASS:推送通道正常", out)
        self.assertIn("STAMPED", out)
        self.assertNotIn("NOT_STAMPED", out)

    def test_total_failure_still_fails(self):
        out = self._run_block('echo "[notify] ERROR: discord 失败" >&2; return 1;')
        self.assertIn("FAIL_CHK:推送通道失败（notify 全通道未发出）", out)


class TestSourceGuards(unittest.TestCase):
    def setUp(self):
        self.src = _read(_NOTIFY)

    def test_old_return_rc_retired(self):
        body = self.src[self.src.index("\nnotify() {"):self.src.index("\nnotify_file()")]
        code = "\n".join(l for l in body.splitlines() if not l.strip().startswith("#"))
        self.assertNotRegex(code, r"return \$rc", "旧「任一通道失败即 1」形态不得回归")
        self.assertIn("PARTIAL:", code)
        self.assertIn('[ "$sent" -eq 0 ] && return 1', code)

    def test_marker(self):
        self.assertIn("V37.9.359", self.src)
        self.assertIn("V37.9.359", _read(_PREFLIGHT))

    def test_bash_syntax(self):
        for p in (_NOTIFY, _PREFLIGHT):
            r = subprocess.run(["bash", "-n", p], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
