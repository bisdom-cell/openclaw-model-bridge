#!/usr/bin/env python3
"""V37.9.371 — collector 的 stdout 是 JSON 数据契约，stderr 是诊断：两者不得混在一起。

血案（2026-10-07 16:30 watchdog CORE 告警 "KB每日深度: 异常状态 (parse_error)"）:
kb_deep_dive.sh / kb_evening.sh / kb_review.sh 三个包装脚本都用
`COLLECTOR_OUTPUT=$(python3 "$COLLECTOR" 2>&1)` 捕获 collector 输出，随后
`json.load` 解析。collector 只要往 stderr 写一行（LLM 重试 WARN / pdfminer 字体
警告 / bs4 XMLParsedAsHTMLWarning），那一行就混进 JSON → 解析失败 →
**一次成功的运行被记成 parse_error，当日产物整份丢弃**（不归档、不推送）。

最刺眼的一处：V37.9.306 给 kb_evening 加了 LLM 重试（retries=1），而重试 WARN
走 stderr——重试成功的那天，包装脚本看到的是「WARN 行 + JSON」，判 parse_error。
也就是说那次修复的成功路径在生产上从来到达不了用户；它的单测
（test_retry_logs_to_stderr 写着「不污染 stdout 的 JSON 契约」）在 collector 层
是对的，坏在包装脚本那道接缝上。

本守卫:
  A. 从三个脚本真源码抽出捕获+解析块跑真 bash（set -eEuo pipefail + ERR trap）:
     重试救回 → ok / 库警告 → ok / stdout 非 JSON 仍 parse_error（检出力不减）/
     崩溃时告警带 stderr 末尾 / 不留临时文件 / 不触发 ERR trap；
     反向证据：同一块换回 `2>&1` 必判 parse_error（证明守卫不是空转）。
  B. 重试 WARN 进入的 kb_evening.log / kb_review.log 在 watchdog 错误扫描里：
     从 job_watchdog.sh 真源码抽 err_pattern 跑真 grep —— 重试 WARN 不得匹配
     （救回的单次失败不是事故），返回值里的完整原因不变（整轮失败仍可告警）。
  C. 源码守卫（剥注释）+ 三脚本同形 + 全仓同类扫描（2>&1 捕获后被 json 解析）。
"""
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest import mock

REPO = os.path.dirname(os.path.abspath(__file__))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

import kb_review_collect as rc  # noqa: E402

SCRIPTS = ("kb_deep_dive.sh", "kb_evening.sh", "kb_review.sh")
BASH = shutil.which("bash")


def _read(name):
    with open(os.path.join(REPO, name), encoding="utf-8") as f:
        return f.read()


def _strip_comment_lines(src):
    return "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))


def _extract_block(name):
    """捕获 collector 输出 → 解析 status → parse_error 分支结束（含 exit 1 + fi）。"""
    lines = _read(name).splitlines()
    start = next(i for i, l in enumerate(lines)
                 if l.startswith("_COLLECTOR_ERR_FILE=$(mktemp)"))
    pe = next(i for i, l in enumerate(lines)
              if 'write_status "parse_error"' in l)
    end = pe + 2
    assert lines[end].strip() == "fi", (name, lines[end])
    return "\n".join(lines[start:end + 1])


def _legacy_block(block):
    """把修复后的块退回旧的 `2>&1` 合并形态（反向证据用）。"""
    legacy = block.replace('2>"$_COLLECTOR_ERR_FILE"', "2>&1")
    assert legacy != block
    return legacy


HARNESS_HEAD = textwrap.dedent("""\
    set -eEuo pipefail
    trap 'echo "ERR_TRAP_FIRED line $LINENO" >&2' ERR
    log() { echo "LOG: $1" >&2; }
    send_alert() { echo "ALERT: $1" >&2; }
    write_status() { echo "STATUS_WRITTEN: $*" >&2; }
    """)
HARNESS_TAIL = 'echo "FINAL_STATUS=$STATUS"\n'


def _run_block(block, collector_src, tmpdir):
    col = os.path.join(tmpdir, "collector.py")
    with open(col, "w", encoding="utf-8") as f:
        f.write(collector_src)
    sh = os.path.join(tmpdir, "block.sh")
    with open(sh, "w", encoding="utf-8") as f:
        f.write(HARNESS_HEAD + f'COLLECTOR="{col}"\n' + block + "\n" + HARNESS_TAIL)
    mk_tmp = tempfile.mkdtemp(prefix="mktemp_area_", dir=tmpdir)  # 每次独立，泄漏归属不串
    env = dict(os.environ, KB_DIR=tmpdir, DAYS="1", REGISTRY="/nonexistent",
               TMPDIR=mk_tmp, REPO_DIR_FOR_TEST=REPO)
    p = subprocess.run([BASH, sh], capture_output=True, text=True, env=env,
                       timeout=60)
    return p.returncode, p.stdout + p.stderr, mk_tmp


# 用真实 rc.call_llm 的重试循环产生 stderr（只替换传输层），stdout 打印 JSON。
RETRY_RESCUED_COLLECTOR = textwrap.dedent("""\
    import json, os, sys
    sys.path.insert(0, os.environ["REPO_DIR_FOR_TEST"])
    import kb_review_collect as rc
    calls = []
    def fake_once(prompt, timeout=None, url=None, model=None):
        calls.append(1)
        if len(calls) == 1:
            return (False, "", "HTTP 502: Bad Gateway | upstream: ALL 4 FALLBACKS FAILED: deepseek_full HTTP 429")
        return (True, "analysis " * 30, "")
    rc._call_llm_once = fake_once
    ok, content, reason = rc.call_llm("p", retries=1)
    print(json.dumps({"status": "ok" if ok else "llm_failed", "chars": len(content)}))
    """)

# pdfminer（logging 无 handler → lastResort 写 stderr）+ bs4 风格 warnings。
LIBRARY_WARNING_COLLECTOR = textwrap.dedent("""\
    import json, logging, sys, warnings
    logging.getLogger("pdfminer.pdffont").warning(
        "Could not get FontBBox from font descriptor because None cannot be parsed as 4 floats")
    warnings.warn("It looks like you're using an HTML parser to parse an XML document.")
    print(json.dumps({"status": "ok"}))
    """)

NON_JSON_COLLECTOR = 'print("this is not json")\n'

CRASH_COLLECTOR = textwrap.dedent("""\
    import json, sys
    print("Traceback (most recent call last):", file=sys.stderr)
    print("ImportError: simulated import failure", file=sys.stderr)
    print(json.dumps({"status": "collector_failed", "reason": "ImportError: simulated"}))
    sys.exit(1)
    """)


@unittest.skipUnless(BASH, "bash required")
class TestWrapperBlockBehavior(unittest.TestCase):
    """三个包装脚本的真实捕获+解析块，跑真 bash。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="v371_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _each(self):
        for name in SCRIPTS:
            yield name, _extract_block(name)

    def test_block_extraction_not_vacuous(self):
        for name, block in self._each():
            self.assertIn('python3 "$COLLECTOR"', block, name)
            self.assertIn("json.load", block, name)
            self.assertIn('write_status "parse_error"', block, name)

    def test_retry_rescued_run_is_ok_not_parse_error(self):
        """血案回归：重试救回的成功运行必须是 ok，产物不得被丢弃。"""
        for name, block in self._each():
            with self.subTest(script=name):
                rc_, out, _ = _run_block(block, RETRY_RESCUED_COLLECTOR, self.tmp)
                self.assertEqual(rc_, 0, out)
                self.assertIn("FINAL_STATUS=ok", out)
                self.assertNotIn("parse_error", out)
                self.assertIn("collector stderr: [kb_collect] WARN: LLM attempt 1/2", out,
                              "stderr 必须仍然进日志（诊断不丢）")

    def test_library_warnings_do_not_break_json(self):
        """pdfminer / bs4 一类库警告走 stderr，不得让成功的运行变 parse_error。"""
        for name, block in self._each():
            with self.subTest(script=name):
                rc_, out, _ = _run_block(block, LIBRARY_WARNING_COLLECTOR, self.tmp)
                self.assertEqual(rc_, 0, out)
                self.assertIn("FINAL_STATUS=ok", out)
                self.assertIn("FontBBox", out)

    def test_non_json_stdout_is_still_parse_error(self):
        """检出力不减：stdout 本身不是 JSON 仍判 parse_error。"""
        for name, block in self._each():
            with self.subTest(script=name):
                rc_, out, _ = _run_block(block, NON_JSON_COLLECTOR, self.tmp)
                self.assertEqual(rc_, 1, out)
                self.assertIn("STATUS_WRITTEN: parse_error", out)

    def test_crash_alert_carries_stderr_tail(self):
        """collector 崩溃：走 collector_failed，告警正文带 stdout 原因 + stderr 末尾。"""
        for name, block in self._each():
            with self.subTest(script=name):
                rc_, out, _ = _run_block(block, CRASH_COLLECTOR, self.tmp)
                self.assertEqual(rc_, 1, out)
                self.assertIn("STATUS_WRITTEN: collector_failed", out)
                alert = [l for l in out.splitlines() if l.startswith("ALERT:")]
                self.assertEqual(len(alert), 1, out)
                self.assertIn("ImportError: simulated import failure", alert[0])
                self.assertIn('"reason": "ImportError: simulated"', alert[0])
                self.assertNotIn("ERR_TRAP_FIRED", out, "|| 分支内不得触发 ERR trap")

    def test_no_temp_file_left_behind(self):
        for collector in (RETRY_RESCUED_COLLECTOR, CRASH_COLLECTOR, NON_JSON_COLLECTOR):
            for name, block in self._each():
                with self.subTest(script=name):
                    _, out, mk_tmp = _run_block(block, collector, self.tmp)
                    self.assertEqual(os.listdir(mk_tmp), [], out)

    def test_stderr_forwarding_is_bounded(self):
        """库警告可能上百行：日志只留最后 20 行 + 总行数。"""
        noisy = textwrap.dedent("""\
            import json, sys
            for i in range(150):
                print(f"warn line {i}", file=sys.stderr)
            print(json.dumps({"status": "ok"}))
            """)
        for name, block in self._each():
            with self.subTest(script=name):
                rc_, out, _ = _run_block(block, noisy, self.tmp)
                self.assertEqual(rc_, 0, out)
                fwd = [l for l in out.splitlines() if l.startswith("LOG: collector stderr: warn line")]
                self.assertEqual(len(fwd), 20)
                self.assertIn("warn line 149", fwd[-1])
                self.assertIn("150 行", out)

    def test_reverse_evidence_legacy_merge_breaks(self):
        """反向证据：同一块退回 `2>&1` → 重试救回的运行必判 parse_error。"""
        for name, block in self._each():
            with self.subTest(script=name):
                rc_, out, _ = _run_block(_legacy_block(block),
                                         RETRY_RESCUED_COLLECTOR, self.tmp)
                self.assertEqual(rc_, 1, out)
                self.assertIn("STATUS_WRITTEN: parse_error", out)


def _watchdog_err_pattern():
    src = _read("job_watchdog.sh")
    m = re.search(r"local err_pattern='([^']+)'", src)
    assert m, "job_watchdog.sh err_pattern not found"
    return m.group(1)


def _grep_matches(pattern, text):
    p = subprocess.run(["grep", "-ciE", pattern], input=text, capture_output=True, text=True)
    return int(p.stdout.strip() or "0")


REALISTIC_REASONS = [
    "HTTP 502: Bad Gateway | upstream: ALL 4 FALLBACKS FAILED: deepseek_full HTTP 429",
    "HTTP 500: Internal Server Error",
    "URLError: [Errno 61] Connection refused",
    "TimeoutError: timed out",
    "JSONDecodeError: Expecting value: line 1 column 1 (char 0)",
    "invalid response structure: 'choices'",
    "LLM returned empty content",
    "LLM content too short (12 chars, min 80)",
    "",
]


class TestRetryWarnWatchdogContract(unittest.TestCase):
    """重试 WARN 进入被 watchdog 扫描的日志：per-attempt 不匹配，run 级照常匹配。"""

    def _warn_line(self, reason):
        seq = [(False, "", reason), (True, "x" * 120, "")]
        calls = []

        def fake_once(prompt, timeout=None, url=None, model=None):
            calls.append(1)
            return seq[len(calls) - 1]

        buf = io.StringIO()
        with mock.patch.object(rc, "_call_llm_once", fake_once):
            with mock.patch("sys.stderr", new=buf):
                ok, _, _ = rc.call_llm("p", retries=1)
        self.assertTrue(ok)
        return buf.getvalue()

    def test_logs_feeding_watchdog_scan_fact(self):
        """为什么这条契约重要：kb_evening / kb_review 的日志在错误扫描名单里。"""
        src = _read("job_watchdog.sh")
        self.assertIn('"$HOME/kb_evening.log|kb_evening"', src)
        self.assertIn('"$HOME/kb_review.log|kb_review"', src)

    def test_retry_warn_never_matches_err_pattern(self):
        pat = _watchdog_err_pattern()
        for reason in REALISTIC_REASONS:
            with self.subTest(reason=reason):
                line = self._warn_line(reason)
                self.assertIn("attempt 1/2", line)
                wrapped = f"[2026-10-07 22:00:00] kb_evening: collector stderr: {line}"
                self.assertEqual(_grep_matches(pat, wrapped), 0, wrapped)

    def test_reverse_evidence_raw_reason_would_match(self):
        """反向证据：若 WARN 照抄原始原因，watchdog 会把救回的重试当事故。"""
        pat = _watchdog_err_pattern()
        raw = ("[kb_collect] WARN: LLM attempt 1/2 failed "
               f"({REALISTIC_REASONS[0]}), retrying...")
        self.assertGreater(_grep_matches(pat, raw), 0)

    def test_reason_codes_keep_diagnostic_value(self):
        self.assertEqual(rc._attempt_reason_code(REALISTIC_REASONS[0]), "http_502")
        self.assertEqual(rc._attempt_reason_code("URLError: refused"), "urlerror")
        self.assertEqual(rc._attempt_reason_code("TimeoutError: timed out"), "timeouterror")
        self.assertEqual(rc._attempt_reason_code(""), "unknown")
        self.assertEqual(rc._attempt_reason_code(None), "unknown")
        self.assertTrue(re.fullmatch(r"[a-z0-9_]{1,40}",
                                     rc._attempt_reason_code("LLM content too short (12 chars, min 80)")))

    def test_exhausted_retry_returns_full_reason(self):
        """整轮失败时返回值里的完整原因不变 → run 级 ERROR 行/告警照常带它。"""
        def fake_once(prompt, timeout=None, url=None, model=None):
            return (False, "", REALISTIC_REASONS[0])

        with mock.patch.object(rc, "_call_llm_once", fake_once):
            with mock.patch("sys.stderr", new=io.StringIO()):
                ok, _, reason = rc.call_llm("p", retries=1)
        self.assertFalse(ok)
        self.assertEqual(reason, REALISTIC_REASONS[0])
        pat = _watchdog_err_pattern()
        run_level = f"[2026-10-07 22:00:00] kb_evening: ERROR: LLM 晚间整理失败: {reason}"
        self.assertGreater(_grep_matches(pat, run_level), 0)

    def test_forwarding_header_line_does_not_match(self):
        pat = _watchdog_err_pattern()
        for name in SCRIPTS:
            block = _extract_block(name)
            m = re.search(r'log "(collector stderr: \$\{COLLECTOR_STDERR_LINES\}[^"]*)"', block)
            self.assertIsNotNone(m, name)
            header = "[2026-10-07 22:00:00] x: " + m.group(1).replace("${COLLECTOR_STDERR_LINES}", "3")
            self.assertEqual(_grep_matches(pat, header), 0, header)


class TestSourceGuards(unittest.TestCase):

    def test_merge_form_retired_in_all_three(self):
        for name in SCRIPTS:
            code = _strip_comment_lines(_read(name))
            self.assertNotIn('python3 "$COLLECTOR" 2>&1', code, name)
            self.assertIn('python3 "$COLLECTOR" 2>"$_COLLECTOR_ERR_FILE")', code, name)
            self.assertIn('rm -f "$_COLLECTOR_ERR_FILE"', code, name)

    def test_three_blocks_same_shape(self):
        """MR-8：三份块只允许 env 前缀与 write_status 参数不同。"""
        def norm(b):
            b = _strip_comment_lines(b)
            b = re.sub(r"COLLECTOR_OUTPUT=\$\([^\n]*?python3", "COLLECTOR_OUTPUT=$(ENV python3", b)
            b = re.sub(r'write_status "collector_failed"[^\n]*', "WS_FAIL", b)
            b = re.sub(r'write_status "parse_error"[^\n]*', "WS_PARSE", b)
            return b
        shapes = {name: norm(_extract_block(name)) for name in SCRIPTS}
        first = shapes[SCRIPTS[0]]
        for name in SCRIPTS[1:]:
            self.assertEqual(shapes[name], first, name)

    def test_no_merged_capture_is_json_parsed_anywhere(self):
        """全仓同类扫描：任何 `VAR=$(… python3 … 2>&1)` 之后被 json 解析的变量。"""
        offenders = []
        checked = 0
        for root, dirs, files in os.walk(REPO):
            dirs[:] = [d for d in dirs if d not in (".git", "node_modules", "__pycache__")]
            for fn in files:
                if not fn.endswith(".sh"):
                    continue
                path = os.path.join(root, fn)
                with open(path, encoding="utf-8", errors="replace") as f:
                    code = _strip_comment_lines(f.read())
                checked += 1
                for m in re.finditer(r"\b([A-Z_][A-Z0-9_]*)=\$\((?:[^()\n]|\$\([^()\n]*\))*python3(?:[^()\n]|\$\([^()\n]*\))*2>&1\)", code):
                    var = m.group(1)
                    if re.search(r'"\$' + var + r'"\s*\|\s*python3[^\n]*json\.load', code) or \
                       re.search(r'json\.loads\(os\.environ\["' + var + r'"\]\)', code):
                        offenders.append(f"{os.path.relpath(path, REPO)}:{var}")
        self.assertGreater(checked, 50, "扫描面过小 = 守卫空转")
        self.assertEqual(offenders, [])

    def test_class_scanner_catches_legacy_form(self):
        """反向证据：把旧形态喂给同一扫描判据必须被抓住。"""
        legacy = ('COLLECTOR_OUTPUT=$(KB_DIR="$KB_DIR" python3 "$COLLECTOR" 2>&1) || {\n'
                  "}\n"
                  "STATUS=$(echo \"$COLLECTOR_OUTPUT\" | python3 -c 'import json,sys; "
                  "print(json.load(sys.stdin).get(\"status\"))')\n")
        m = re.search(r"\b([A-Z_][A-Z0-9_]*)=\$\((?:[^()\n]|\$\([^()\n]*\))*python3(?:[^()\n]|\$\([^()\n]*\))*2>&1\)", legacy)
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), "COLLECTOR_OUTPUT")
        self.assertRegex(legacy, r'"\$COLLECTOR_OUTPUT"\s*\|\s*python3[^\n]*json\.load')

    def test_v371_markers(self):
        for name in SCRIPTS:
            self.assertIn("V37.9.371", _read(name), name)
        self.assertIn("V37.9.371", _read("kb_review_collect.py"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
