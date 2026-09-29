#!/usr/bin/env python3
"""V37.9.364 茶思屋 API 被 WAF 间歇拦截（HTTP 418）时重试一次。

背景（2026-09-29 Mac Mini 实测）：茶思屋站点前置华为云 WAF（拦截页
`<meta http-equiv="Server" content="CloudWAF" />`，标题「访问被拦截！」）。
本任务当天 11:00:01 与 13:08:32 各被拦一次，后一次紧跟在一次手动 200 之后；
随后与任务逐字相同的请求（同一个 /usr/bin/curl、同 UA、带与不带 _t、交互 shell
与 bash -lc 两种环境）8 次全部 200。拦截与请求形态无关，时有时无；
日志累计 6/76 次，被拦当天整份摘要缺席。

修复只在 418 时等待后重试一次，其余错误码行为不变。本守卫从真源码抽出
抓取块，换成假 curl 与假 sleep 在 bash 里真跑，逐项断言：
  - 418 后重试成功 → 继续往下走，且这一天的日志不被 watchdog 当成错误；
  - 两次都 418 → 仍写 error 状态并退出，日志仍能被 watchdog 扫到；
  - 200 / 500 / curl 失败都不重试（范围不扩大）；
  - curl 失败时状态码是 "000" 而不是 "000000"（函数化后的回归点）；
  - 默认等待 120 秒，且等待真的交给了 sleep。
"""
import json
import os
import re
import stat
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(ROOT, "jobs", "chaspark", "run_chaspark.sh")
WATCHDOG = os.path.join(ROOT, "job_watchdog.sh")
BLOCK_START = "# ── 1. 调用 Chaspark 官方 API"
BLOCK_END = "# ── 2. 解析 JSON"
CURL_LINE = 'CURL="/usr/bin/curl"'

FAKE_CURL = r"""#!/bin/bash
code=$(head -1 "$FAKE_SEQ")
tail -n +2 "$FAKE_SEQ" > "$FAKE_SEQ.tmp" && mv "$FAKE_SEQ.tmp" "$FAKE_SEQ"
echo "$*" >> "$FAKE_CALLS"
out=""
prev=""
for a in "$@"; do
  [ "$prev" = "-o" ] && out="$a"
  prev="$a"
done
case "$code" in
  200) printf '{"code":0,"data":{}}' > "$out" ;;
  418) printf '<html><meta http-equiv="Server" content="CloudWAF" /></html>' > "$out" ;;
  000) printf '000'; exit 28 ;;
  *) printf 'upstream error' > "$out" ;;
esac
printf '%s' "$code"
"""

FAKE_SLEEP = """#!/bin/bash
echo "$1" >> "$FAKE_SLEEP_LOG"
"""


def _src():
    with open(SCRIPT, encoding="utf-8") as f:
        return f.read()


def _fetch_block(src=None):
    s = src if src is not None else _src()
    i = s.index(BLOCK_START)
    j = s.index(BLOCK_END, i)
    return s[i:j]


def _watchdog_err_pattern():
    with open(WATCHDOG, encoding="utf-8") as f:
        m = re.search(r"local err_pattern='([^']+)'", f.read())
    assert m, "job_watchdog.sh 里找不到 err_pattern"
    return m.group(1)


def _write_exec(path, body):
    with open(path, "w", encoding="utf-8") as f:
        f.write(body)
    os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _run(codes, retry_sec="0", block=None):
    """跑一次抓取块；codes 是假 curl 依次返回的状态码。"""
    tmp = tempfile.mkdtemp(prefix="chaspark_waf_")
    fakebin = os.path.join(tmp, "bin")
    os.makedirs(fakebin)
    fake_curl = os.path.join(fakebin, "curl")
    _write_exec(fake_curl, FAKE_CURL)
    _write_exec(os.path.join(fakebin, "sleep"), FAKE_SLEEP)
    seq = os.path.join(tmp, "seq")
    with open(seq, "w") as f:
        f.write("\n".join(codes) + "\n")
    calls = os.path.join(tmp, "calls")
    sleep_log = os.path.join(tmp, "sleep_log")
    open(calls, "w").close()
    open(sleep_log, "w").close()

    body = block if block is not None else _fetch_block()
    assert CURL_LINE in body, "抓取块里找不到 CURL 定义行，无法换成假 curl"
    body = body.replace(CURL_LINE, 'CURL="$FAKE_CURL"')

    cache = os.path.join(tmp, "cache")
    prelude = (
        f'CACHE="{cache}"\n'
        'mkdir -p "$CACHE/raw"\n'
        'DAY="2026-09-29"\n'
        'TS="2026-09-29 11:00:00"\n'
        'STATUS_FILE="$CACHE/last_run.json"\n'
        'log() { echo "[$TS] chaspark: $1" >&2; }\n'
    )
    script = os.path.join(tmp, "run.sh")
    with open(script, "w", encoding="utf-8") as f:
        f.write(prelude + body + "\necho PASSED_FETCH\n")

    env = dict(os.environ)
    env["PATH"] = fakebin + os.pathsep + env.get("PATH", "")
    env["FAKE_CURL"] = fake_curl
    env["FAKE_SEQ"] = seq
    env["FAKE_CALLS"] = calls
    env["FAKE_SLEEP_LOG"] = sleep_log
    if retry_sec is None:
        env.pop("CHASPARK_WAF_RETRY_SEC", None)
    else:
        env["CHASPARK_WAF_RETRY_SEC"] = retry_sec
    p = subprocess.run(["bash", script], capture_output=True, text=True,
                       env=env, timeout=30)
    with open(calls) as f:
        n_calls = sum(1 for line in f if line.strip())
    with open(sleep_log) as f:
        sleeps = [line.strip() for line in f if line.strip()]
    status_path = os.path.join(cache, "last_run.json")
    status = None
    if os.path.exists(status_path):
        with open(status_path) as f:
            status = json.load(f)
    return {"rc": p.returncode, "stdout": p.stdout, "stderr": p.stderr,
            "calls": n_calls, "sleeps": sleeps, "status": status}


def _watchdog_hits(text):
    p = subprocess.run(["grep", "-ciE", _watchdog_err_pattern()],
                       input=text, capture_output=True, text=True)
    return int(p.stdout.strip() or "0")


class TestWafRetryBehavior(unittest.TestCase):
    def test_418_then_200_continues(self):
        r = _run(["418", "200"])
        self.assertEqual(r["rc"], 0, r["stderr"])
        self.assertIn("PASSED_FETCH", r["stdout"])
        self.assertEqual(r["calls"], 2)
        self.assertIsNone(r["status"], "重试成功不应写 error 状态")
        self.assertIn("WAF", r["stderr"])

    def test_retry_success_day_not_flagged_by_watchdog(self):
        r = _run(["418", "200"])
        self.assertEqual(_watchdog_hits(r["stderr"]), 0,
                         f"重试成功的日志不应被 watchdog 当成错误: {r['stderr']}")

    def test_418_twice_still_fails_loud(self):
        r = _run(["418", "418"])
        self.assertEqual(r["rc"], 1)
        self.assertNotIn("PASSED_FETCH", r["stdout"])
        self.assertEqual(r["calls"], 2, "只重试一次，不得无限重试")
        self.assertEqual(r["status"]["status"], "error")
        self.assertEqual(r["status"]["http_code"], "418")
        self.assertGreaterEqual(_watchdog_hits(r["stderr"]), 1,
                                "两次都被拦时 watchdog 必须能扫到")

    def test_200_first_no_retry(self):
        r = _run(["200"])
        self.assertEqual(r["rc"], 0)
        self.assertEqual(r["calls"], 1)
        self.assertEqual(r["sleeps"], [])
        self.assertNotIn("WAF", r["stderr"])

    def test_500_not_retried(self):
        r = _run(["500", "200"])
        self.assertEqual(r["rc"], 1)
        self.assertEqual(r["calls"], 1, "只有 418 才重试")
        self.assertEqual(r["status"]["http_code"], "500")

    def test_curl_failure_code_is_000(self):
        r = _run(["000", "200"])
        self.assertEqual(r["rc"], 1)
        self.assertEqual(r["calls"], 1)
        self.assertEqual(r["status"]["http_code"], "000",
                         "curl 失败时 -w 已输出 000，不得再拼成 000000")

    def test_default_wait_is_120_and_goes_through_sleep(self):
        r = _run(["418", "200"], retry_sec=None)
        self.assertEqual(r["sleeps"], ["120"])

    def test_env_override_wait(self):
        r = _run(["418", "200"], retry_sec="7")
        self.assertEqual(r["sleeps"], ["7"])


class TestReverseEvidence(unittest.TestCase):
    def test_block_without_retry_fails_on_single_418(self):
        """把重试分支拿掉后，同一个 418→200 序列必须失败，证明上面的断言不是空转。"""
        block = _fetch_block()
        m = re.search(r'\nif \[ "\$HTTP_CODE" = "418" \]; then\n.*?\nfi\n',
                      block, re.S)
        self.assertIsNotNone(m, "找不到重试分支，无法构造反向对照")
        r = _run(["418", "200"], block=block.replace(m.group(0), "\n"))
        self.assertEqual(r["rc"], 1)
        self.assertEqual(r["calls"], 1)


class TestSourceGuards(unittest.TestCase):
    def test_single_request_definition(self):
        """请求只在 fetch_api 里定义一次，重试不复制第二份 curl 命令。"""
        self.assertEqual(_src().count("content/recommend/slot"), 1)

    def test_retry_only_on_418(self):
        block = _fetch_block()
        self.assertIn('if [ "$HTTP_CODE" = "418" ]; then', block)
        self.assertEqual(block.count("HTTP_CODE=$(fetch_api)"), 2)

    def test_default_wait_constant(self):
        self.assertIn('WAF_RETRY_SEC="${CHASPARK_WAF_RETRY_SEC:-120}"', _fetch_block())

    def test_marker(self):
        self.assertIn("V37.9.364", _fetch_block())

    def test_bash_syntax(self):
        p = subprocess.run(["bash", "-n", SCRIPT], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)


if __name__ == "__main__":
    unittest.main()
