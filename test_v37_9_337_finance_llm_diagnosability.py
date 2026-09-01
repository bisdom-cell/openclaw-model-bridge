#!/usr/bin/env python3
"""V37.9.337 finance_news LLM 失败可定性守卫。

血案（2026-09-01 07:30 生产实录）：finance_news 报 `llm_failed`，日志只有一行
「ERROR: LLM 3次调用全部失败，推送原始标题」——运维面**无法判断**是超时 /
连接拒绝 / HTTP 错误 / 内容过短，只能上机手查。三处成因：

  1. `curl ... 2>llm.stderr || true` 把 curl 退出码**完全吞掉**
     （28=超时 / 7=连接拒绝 这两个最有信息量的信号直接消失）；
  2. 解析用裸 `except: sys.exit(1)`，bad_json / http_error / no_choices
     三类合并成同一个「失败」；
  3. `len(c) > 200` 否则算失败——reasoning 模型吃满 token 只剩短 content
     （V37.9.204 老教训）会被报成「调用失败」，读者以为是网络问题。

而 10 个兄弟 job（arxiv/hf_papers/dblp/s2/…）**早就**有 V37.9.36 三层分类
（`__LLM_HTTP_ERROR__` / `__LLM_PARSE_FAIL__` / empty content）——finance_news
是唯一没跟的那个，而今天失败的恰好就是它（一物一形违反）。

本守卫用**真源码抽出的 LLM 块**跑真 mock adapter，逐类断言分类正确。
🔴 绝不绑 5001（Mac Mini 上那是生产 adapter，MR-9 test-pollutes-production）：
测试把抽出的块里的 URL 换成临时端口，被测的是分类逻辑不是 URL。
"""
import json
import os
import re
import socket
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

ROOT = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(ROOT, "jobs", "finance_news", "run_finance_news.sh")
BLOCK_START = "LLM_MAX_TIME=180"
BLOCK_END = 'if [ "$LLM_OK" != "true" ]; then'


def _src():
    with open(SCRIPT, encoding="utf-8") as f:
        return f.read()


def _llm_block(src=None):
    s = src if src is not None else _src()
    i = s.index(BLOCK_START)
    j = s.index(BLOCK_END, i)
    return s[i:j]


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _Mock(BaseHTTPRequestHandler):
    mode = "ok"

    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        self.rfile.read(n)
        m = _Mock.mode
        if m == "hang":
            import time
            time.sleep(20)
            return
        if m == "badjson":
            body = b"<html>upstream returned a portal page</html>"
        elif m == "httperr":
            body = json.dumps({"error": {"message": "all 4 fallbacks failed"}}).encode()
        elif m == "nochoices":
            body = json.dumps({"id": "resp_1"}).encode()
        elif m == "short":
            body = json.dumps({"choices": [{"message": {"content": "太短"}}]}).encode()
        else:
            body = json.dumps({"choices": [{"message": {"content": "A" * 500}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class _Server:
    def __init__(self, mode):
        _Mock.mode = mode
        self.port = _free_port()
        self.httpd = HTTPServer(("127.0.0.1", self.port), _Mock)
        self.t = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self):
        self.t.start()
        return self

    def __exit__(self, *a):
        self.httpd.shutdown()
        self.httpd.server_close()


def _run_block(port, max_time=180):
    """把真源码抽出的 LLM 块跑起来，返回 (LLM_OK, reason, content_len, log)。"""
    block = _llm_block()
    block = block.replace("LLM_MAX_TIME=180", f"LLM_MAX_TIME={max_time}")
    block = block.replace("http://127.0.0.1:5001/v1/chat/completions",
                          f"http://127.0.0.1:{port}/v1/chat/completions")
    block = block.replace('sleep "$((attempt * 10))"', "sleep 0")
    with tempfile.TemporaryDirectory() as d:
        cache = os.path.join(d, "cache")
        os.makedirs(cache)
        with open(os.path.join(cache, "llm_payload.json"), "w") as f:
            json.dump({"model": "default",
                       "messages": [{"role": "user", "content": "hi"}]}, f)
        harness = (
            "set -eo pipefail\n"
            f'CACHE="{cache}"\n'
            'LLM_RAW="$CACHE/raw.json"\n'
            "PYTHON3=python3\n"
            "REMOTE_API_KEY=dummy\n"
            'LLM_CONTENT=""\n'
            'log(){ echo "LOG: $*"; }\n'
            + block +
            '\necho "RESULT|$LLM_OK|${LAST_LLM_FAIL_REASON:-}|${#LLM_CONTENT}"\n'
        )
        path = os.path.join(d, "h.sh")
        with open(path, "w") as f:
            f.write(harness)
        p = subprocess.run(["bash", path], capture_output=True, text=True, timeout=120)
        line = [l for l in p.stdout.splitlines() if l.startswith("RESULT|")]
        assert line, f"harness 未产出 RESULT: {p.stdout[-400:]} {p.stderr[-400:]}"
        _, ok, reason, ln = line[0].split("|", 3)
        return ok == "true", reason.strip(), int(ln), p.stdout


class TestFailureClassification(unittest.TestCase):
    """行为级：每类失败必须给出可区分的原因（血案回归）。"""

    def test_timeout_is_identifiable(self):
        """🔴 血案核心：超时此前被 `|| true` 吞掉，完全不可见。"""
        with _Server("hang") as s:
            ok, reason, _, _ = _run_block(s.port, max_time=2)
        self.assertFalse(ok)
        self.assertIn("timeout", reason)
        self.assertIn("rc=28", reason)

    def test_connection_refused_is_identifiable(self):
        port = _free_port()  # 无人监听
        ok, reason, _, _ = _run_block(port, max_time=5)
        self.assertFalse(ok)
        self.assertIn("rc=7", reason)

    def test_bad_json_distinct(self):
        with _Server("badjson") as s:
            ok, reason, _, _ = _run_block(s.port)
        self.assertFalse(ok)
        self.assertIn("bad_json", reason)

    def test_http_error_body_surfaced(self):
        """adapter 的错误体要带进原因（全链 fallback 失败时最有用）。"""
        with _Server("httperr") as s:
            ok, reason, _, _ = _run_block(s.port)
        self.assertFalse(ok)
        self.assertIn("http_error", reason)
        self.assertIn("fallbacks failed", reason)

    def test_no_choices_distinct(self):
        with _Server("nochoices") as s:
            ok, reason, _, _ = _run_block(s.port)
        self.assertFalse(ok)
        self.assertIn("no_choices", reason)

    def test_short_content_not_reported_as_call_failure(self):
        """F3：内容过短是内容问题，不得再谎称『调用失败』。"""
        with _Server("short") as s:
            ok, reason, _, _ = _run_block(s.port)
        self.assertFalse(ok)
        self.assertIn("short_content", reason)
        self.assertNotIn("curl", reason)

    def test_success_path_not_broken(self):
        """检出力不减：正常响应仍成功，且不误报原因。"""
        with _Server("ok") as s:
            ok, reason, ln, _ = _run_block(s.port)
        self.assertTrue(ok, f"正常响应被误判失败: {reason}")
        self.assertEqual("", reason)
        self.assertEqual(500, ln)

    def test_per_attempt_reason_logged(self):
        """每次尝试都要把原因写进日志，不能只在最后报一句。"""
        with _Server("httperr") as s:
            _, _, _, out = _run_block(s.port)
        attempts = [l for l in out.splitlines()
                    if l.startswith("LOG: WARN: LLM attempt")]
        self.assertEqual(3, len(attempts), f"应有 3 条 per-attempt 日志: {attempts}")
        for l in attempts:
            self.assertIn("http_error", l, f"日志缺原因: {l}")


class TestSourceGuards(unittest.TestCase):
    """源码守卫：退役吞错误的反模式，保持与兄弟 job 的一物一形。"""

    def setUp(self):
        self.src = _src()
        self.block = _llm_block(self.src)

    def test_block_extracted_non_empty(self):
        self.assertGreater(len(self.block), 800,
                           "防空转：LLM 块切片过小，锚点可能已漂移")
        self.assertIn("curl", self.block)

    def test_curl_exit_code_captured(self):
        self.assertIn("CURL_RC", self.block,
                      "curl 退出码必须捕获——它是唯一能区分超时与空响应的信号")
        self.assertRegex(self.block, r"\|\|\s*CURL_RC=\$\?")

    def test_curl_failure_not_swallowed(self):
        """退役 `curl ... || true`（把所有 curl 失败压成同一个空串）。"""
        curl_lines = [l for l in self.block.splitlines() if "5001/v1/chat" in l]
        self.assertEqual(1, len(curl_lines))
        self.assertNotIn("|| true", curl_lines[0],
                         "curl 失败不得再被 `|| true` 吞掉")

    def test_no_useless_subshell_for_key(self):
        """F4：`$(echo $VAR)` 是无用 subshell 且未加引号会 word-split。"""
        self.assertNotIn("$(echo $REMOTE_API_KEY)", self.block)
        self.assertIn('Bearer $REMOTE_API_KEY', self.block)

    def test_three_layer_classification_mirrors_siblings(self):
        """与 10 个兄弟 job 的 V37.9.36 三层分类保持一物一形。"""
        for cls in ("bad_json", "http_error", "no_choices"):
            self.assertIn(cls, self.block, f"缺分类 {cls}")

    def test_short_content_threshold_is_named_constant(self):
        self.assertIn("LLM_MIN_CONTENT", self.block)
        self.assertNotRegex(self.block, r"len\(c\)\s*>\s*200",
                            "阈值须走具名常量，不得散落字面量")

    def test_sleep_guard_is_explicit_if(self):
        """`[ cond ] && sleep` 在 set -e 下语义微妙，须用显式 if。"""
        self.assertNotRegex(self.block, r"^\s*\[\s*\"\$attempt\".*\]\s*&&\s*sleep",
                            "退役 `[ cond ] && sleep` 隐式形态")
        self.assertRegex(self.block, r'if \[ "\$attempt" -lt 3 \]; then sleep')

    def test_status_file_carries_reason(self):
        """llm_failed 的原因必须进 status，运维面无需上机查日志。"""
        self.assertIn("llm_fail_reason", self.src)
        m = re.search(r'printf .*llm_fail_reason.*\n(?:.*\n){0,3}?.*LAST_LLM_FAIL_REASON',
                      self.src)
        self.assertIsNotNone(m, "status 的 llm_fail_reason 未接上真实原因变量")

    def test_marker(self):
        self.assertIn("V37.9.337", self.src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
