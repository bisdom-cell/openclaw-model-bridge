#!/usr/bin/env python3
"""
V37.9.328 对抗审计守卫 — 日志扫描消费者 vs 生产者字符串契约（MR-8 跨文件）

镜头：「声称在扫的东西，生产者真的在写吗」

血案（2026-08-25 对抗审计，三条 grounded 探针复现）：
  F1 (HIGH) job_watchdog.sh 的「主备双失败 = LLM 完全不可用」CRITICAL 告警
     grep 的是 "FALLBACK ALSO FAILED" —— 该字符串在 adapter.py 全部 git 历史里
     从未存在过（git log -S 该串 -- adapter.py 为空）。真串是 run 级
     "ALL <N> FALLBACKS FAILED" 与 per-attempt "FALLBACK <name> FAILED"。
     探针：primary + 4 个 fallback 全失败的真实日志 → adapter_critical = 0 不告警。
  F2 (MED) conv_quality.py 同款死串 → 日报 fallback 段永远「失败 0」；
     且它的单测用手写 fixture 喂了这个不存在的串并断言 failed==1
     = 绿测试守着死指标（V37.9.299 fake-not-faithful + V37.9.320 SS-1 家族）。
     探针渲染：「🔄 Fallback降级：触发 1 次 / 成功 0 / 失败 0」= 全链宕机读作无事发生。
  F2b (MED) 断路器开时 primary 被跳过（无 PRIMARY FAILED 行）→ triggered 少计，
     而 FALLBACK OK 照计 → success > triggered 的自相矛盾算术。
  F3 (MED) token_report「日环比…（昨日 N）」的 N 取自 history 的上一条记录而非
     上一个日历日，缺日时把 4 天前当昨日（V37.9.323 feedback[0] 标「最新」同族）。

守卫策略：不 hardcode 日志字符串，从 adapter.py 真源码 AST 抽 log() f-string 模板，
渲染成真实日志行后喂给两个消费者（watchdog 的真 grep + conv_quality 的真 parser）。
任一侧改动而另一侧没跟 → 立即 fail。
"""
import ast
import os
import re
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta

REPO = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO)

ADAPTER_PY = os.path.join(REPO, "adapter.py")
WATCHDOG_SH = os.path.join(REPO, "job_watchdog.sh")
CONV_QUALITY_PY = os.path.join(REPO, "conv_quality.py")
TOKEN_REPORT_PY = os.path.join(REPO, "token_report.py")

DATE = "2026-08-25"
PROVIDER = "doubao_21"


# ---------------------------------------------------------------------------
# 从 adapter.py 真源码抽 log() f-string 模板（不 hardcode 字符串 = 真 drift guard）
# ---------------------------------------------------------------------------
def _adapter_log_templates():
    """Return list of log() f-string templates with {} for each interpolation."""
    with open(ADAPTER_PY, "r") as f:
        tree = ast.parse(f.read())
    templates = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not (isinstance(node.func, ast.Name) and node.func.id == "log"):
            continue
        if not node.args:
            continue
        arg = node.args[0]
        if isinstance(arg, ast.JoinedStr):
            parts = []
            for v in arg.values:
                if isinstance(v, ast.Constant):
                    parts.append(str(v.value))
                else:
                    parts.append("{}")
            templates.append("".join(parts))
        elif isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            templates.append(arg.value)
    return templates


def _find_template(suffix_pattern):
    """Find the (single) adapter log template whose body matches suffix_pattern."""
    hits = [t for t in _adapter_log_templates() if re.search(suffix_pattern, t)]
    return hits


def _render(template, *values):
    """Render a {}-template with the given values in order."""
    out = template
    for v in values:
        out = out.replace("{}", str(v), 1)
    return out


def _adapter_line(body):
    """Wrap a rendered log body into a full adapter.log line (adapter.py log())."""
    return f"[adapter:{PROVIDER}] {DATE} 03:00:00 {body}"


def _strip_comment_lines(text, marker="#"):
    """Drop comment-only lines (V37.9.178 家族: 守卫不得被自己的注释满足)."""
    return "\n".join(
        ln for ln in text.splitlines()
        if not ln.lstrip().startswith(marker)
    )


def _watchdog_adapter_grep_pattern():
    """Extract the grep pattern the watchdog uses for the adapter CRITICAL scan."""
    with open(WATCHDOG_SH, "r") as f:
        src = _strip_comment_lines(f.read())
    m = re.search(r'adapter_critical=\$\([^)]*grep -c(E?)\s+"([^"]+)"', src)
    if not m:
        return None, None
    return m.group(1), m.group(2)


def _grep_count(pattern, extended, lines):
    """Run the real grep with the real pattern against real lines."""
    with tempfile.NamedTemporaryFile("w", suffix=".log", delete=False) as f:
        f.write("\n".join(lines) + "\n")
        path = f.name
    try:
        flags = "-cE" if extended else "-c"
        out = subprocess.run(["grep", flags, pattern, path],
                             capture_output=True, text=True)
        return int(out.stdout.strip() or 0)
    finally:
        os.unlink(path)


# 真实全链宕机日志（primary + 全部 fallback 失败），全部从 adapter.py 模板渲染
def _total_outage_lines():
    t_primary = _find_template(r"PRIMARY FAILED")[0]
    t_attempt = _find_template(r"^\{\}FALLBACK \{\} FAILED")[0]
    t_all = _find_template(r"ALL .*FALLBACKS FAILED")[0]
    tag = "[abc12345] "
    return [
        _adapter_line(_render(t_primary, tag, 30000, "HTTP Error 502")),
        _adapter_line(_render(t_attempt, tag, "deepseek_full", 60000, "timeout")),
        _adapter_line(_render(t_attempt, tag, "doubao", 90000, "timeout")),
        _adapter_line(_render(t_all, tag, 4)),
    ]


def _fallback_recovered_lines():
    """Fallback 尝试失败一次但最终成功 = 不是「LLM 完全不可用」，不得告警。"""
    t_primary = _find_template(r"PRIMARY FAILED")[0]
    t_attempt = _find_template(r"^\{\}FALLBACK \{\} FAILED")[0]
    t_ok = _find_template(r"FALLBACK OK")[0]
    tag = "[def67890] "
    return [
        _adapter_line(_render(t_primary, tag, 30000, "HTTP Error 502")),
        _adapter_line(_render(t_attempt, tag, "deepseek_full", 60000, "timeout")),
        _adapter_line(_render(t_ok, tag, 200, 3000, 65000, "doubao")),
    ]


class TestAdapterLogProducerContract(unittest.TestCase):
    """生产者侧: adapter.py 到底写了哪些字符串（不 hardcode, AST 抽真源码）。"""

    def test_extraction_not_vacuous(self):
        """防空转: 抽不到模板时下面全部断言都会假通过。"""
        templates = _adapter_log_templates()
        self.assertGreaterEqual(len(templates), 20,
                                f"adapter.py log() 模板只抽到 {len(templates)} 条，抽取器可能坏了")

    def test_run_level_all_failed_template_exists(self):
        hits = _find_template(r"ALL .*FALLBACKS FAILED")
        self.assertEqual(len(hits), 1,
                         f"run 级『全部 fallback 失败』模板应恰 1 条，实得 {hits}")

    def test_per_attempt_failed_template_exists(self):
        hits = _find_template(r"^\{\}FALLBACK \{\} FAILED")
        self.assertEqual(len(hits), 1, f"per-attempt 失败模板应恰 1 条，实得 {hits}")

    def test_primary_failed_and_fallback_ok_templates_exist(self):
        self.assertEqual(len(_find_template(r"PRIMARY FAILED")), 1)
        self.assertEqual(len(_find_template(r"FALLBACK OK")), 1)
        self.assertEqual(len(_find_template(r"CIRCUIT BREAKER OPEN")), 1)

    def test_empty_chain_template_exists(self):
        self.assertEqual(len(_find_template(r"NO FALLBACK CHAIN")), 1)

    def test_dead_string_never_emitted(self):
        """血案钉死: 两个消费者读了一个 adapter 全历史从未写过的串。"""
        with open(ADAPTER_PY, "r") as f:
            self.assertNotIn("FALLBACK ALSO FAILED", f.read(),
                             "adapter.py 出现了 'FALLBACK ALSO FAILED' —— "
                             "若这是新增的真实输出，请同步复核两个消费者的判据")


class TestWatchdogAdapterCriticalScan(unittest.TestCase):
    """消费者 1: job_watchdog.sh 的「LLM 完全不可用」CRITICAL 告警。"""

    def test_pattern_extractable(self):
        extended, pattern = _watchdog_adapter_grep_pattern()
        self.assertIsNotNone(pattern, "抽不到 watchdog 的 adapter_critical grep 判据")

    def test_blood_lesson_total_outage_fires(self):
        """血案回归: primary + 全部 fallback 失败必须告警（修复前实测 count=0）。"""
        extended, pattern = _watchdog_adapter_grep_pattern()
        count = _grep_count(pattern, extended, _total_outage_lines())
        self.assertGreater(count, 0,
                           "全链宕机日志未触发 watchdog CRITICAL 告警 —— "
                           "判据与 adapter.py 实际输出脱节（2026-08-25 血案）")

    def test_recovered_fallback_does_not_fire(self):
        """检出力边界: fallback 尝试失败但最终成功 ≠ LLM 不可用，不得误报。"""
        extended, pattern = _watchdog_adapter_grep_pattern()
        count = _grep_count(pattern, extended, _fallback_recovered_lines())
        self.assertEqual(count, 0,
                         "per-attempt 失败被当成『主备双失败』= 误报（run 级 vs per-file "
                         "契约，镜像 V37.9.292）")

    def test_healthy_log_does_not_fire(self):
        extended, pattern = _watchdog_adapter_grep_pattern()
        lines = [_adapter_line("[aaa11111] ROUTER: primary=doubao_21"),
                 _adapter_line("[bbb22222] CLIENT GONE: response ready but client disconnected")]
        self.assertEqual(_grep_count(pattern, extended, lines), 0)

    def test_dead_pattern_retired(self):
        with open(WATCHDOG_SH, "r") as f:
            body = _strip_comment_lines(f.read())
        self.assertNotIn("FALLBACK ALSO FAILED", body,
                         "job_watchdog.sh 可执行行仍在 grep 已证实不存在的字符串")


class TestConvQualityFallbackMetrics(unittest.TestCase):
    """消费者 2: conv_quality.py 的 fallback 三计数（行为级，真 parser）。"""

    def setUp(self):
        import conv_quality
        self.cq = conv_quality
        self.tmp = tempfile.mkdtemp()
        self._orig = (conv_quality.PROXY_LOG, conv_quality.ADAPTER_LOG)
        conv_quality.PROXY_LOG = os.path.join(self.tmp, "proxy.log")
        conv_quality.ADAPTER_LOG = os.path.join(self.tmp, "adapter.log")
        # 一次成功请求打底，让 format_report 不走「今日无请求记录」早退
        self._write_proxy([
            f"[proxy] {DATE} 01:00:00 [ok000001] Backend: 200 1000b 500ms stream=False",
        ])

    def tearDown(self):
        self.cq.PROXY_LOG, self.cq.ADAPTER_LOG = self._orig

    def _write_proxy(self, lines):
        with open(self.cq.PROXY_LOG, "w") as f:
            f.write("\n".join(lines) + "\n")

    def _write_adapter(self, lines):
        with open(self.cq.ADAPTER_LOG, "w") as f:
            f.write("\n".join(lines) + "\n")

    def test_blood_lesson_all_failed_counted(self):
        """血案回归: 全链宕机时 failed 必须 >0（修复前恒 0）。"""
        self._write_adapter(_total_outage_lines())
        fb = self.cq.parse_logs(DATE)["fallback"]
        self.assertEqual(fb["triggered"], 1)
        self.assertEqual(fb["failed"], 1,
                         f"全链宕机 failed 仍为 {fb['failed']} —— 日报会渲染成「失败 0」")

    def test_report_renders_failure(self):
        """用户真正看到的那行必须说失败 1，不是失败 0。"""
        self._write_adapter(_total_outage_lines())
        report = self.cq.format_report(self.cq.parse_logs(DATE))
        self.assertRegex(report, r"失败 1")

    def test_recovered_fallback_counts_success_not_failed(self):
        """回归: 最终成功的降级仍记 success（检出力不换方向）。"""
        self._write_adapter(_fallback_recovered_lines())
        fb = self.cq.parse_logs(DATE)["fallback"]
        self.assertEqual(fb["success"], 1)
        self.assertEqual(fb["failed"], 0)

    def test_circuit_breaker_open_counted_as_triggered(self):
        """断路器开时 primary 被跳过 → 无 PRIMARY FAILED 行，triggered 不得漏计。"""
        t_cb = _find_template(r"CIRCUIT BREAKER OPEN")[0]
        t_ok = _find_template(r"FALLBACK OK")[0]
        tag = "[ccc33333] "
        self._write_adapter([
            _adapter_line(_render(t_cb, tag, 4)),
            _adapter_line(_render(t_ok, tag, 200, 3000, 5000, "doubao")),
        ])
        fb = self.cq.parse_logs(DATE)["fallback"]
        self.assertEqual(fb["success"], 1)
        self.assertGreaterEqual(fb["triggered"], fb["success"],
                                "success > triggered = 自相矛盾算术（断路器路径漏计）")

    def test_empty_chain_counted_as_failed(self):
        """同一个洞的第二形态: fallback 链为空时 primary 失败直接 502。

        既没有 ALL...FALLBACKS FAILED 也没有 FALLBACK OK —— 不计则 failed 再次恒 0。
        生产当前配了 4 个 fallback 不触发; H1-C 第二实例(PUSH-only)可能不配链。
        """
        t_primary = _find_template(r"PRIMARY FAILED")[0]
        t_nochain = _find_template(r"NO FALLBACK CHAIN")[0]
        tag = "[eee55555] "
        self._write_adapter([
            _adapter_line(_render(t_primary, tag, 30000, "HTTP Error 502")),
            _adapter_line(_render(t_nochain, tag)),
        ])
        fb = self.cq.parse_logs(DATE)["fallback"]
        self.assertEqual(fb["triggered"], 1)
        self.assertEqual(fb["failed"], 1,
                         "链为空时的失败请求未计入 failed —— 日报又会说「失败 0」")

    def test_arithmetic_consistency_on_outage(self):
        self._write_adapter(_total_outage_lines())
        fb = self.cq.parse_logs(DATE)["fallback"]
        self.assertGreaterEqual(fb["triggered"], fb["success"] + fb["failed"])

    def test_no_fallback_events_stays_zero(self):
        """防误报: 干净日志三计数全 0。"""
        self._write_adapter([_adapter_line("[ddd44444] ROUTER: primary=doubao_21")])
        fb = self.cq.parse_logs(DATE)["fallback"]
        self.assertEqual((fb["triggered"], fb["success"], fb["failed"]), (0, 0, 0))

    def test_dead_pattern_retired(self):
        with open(CONV_QUALITY_PY, "r") as f:
            body = _strip_comment_lines(f.read())
        self.assertNotIn("FALLBACK ALSO FAILED", body,
                         "conv_quality.py 可执行行仍在匹配已证实不存在的字符串")


class TestTokenReportPrevDayLabel(unittest.TestCase):
    """F3: 「日环比…（昨日 N）」的 N 必须真的是昨日。"""

    def setUp(self):
        import token_report
        self.tr = token_report

    def _data(self, date, total=300_000):
        return {
            "date": date, "request_count": 40,
            "total_prompt_tokens": int(total * 0.8),
            "total_completion_tokens": int(total * 0.2),
            "total_tokens": total, "avg_prompt": 6250, "median_prompt": 5000,
            "max_prompt": 20000,
            "distribution": {"<10K": 30, "10-50K": 10, "50-100K": 0, "100K+": 0},
            "context_pressure": {"warn_75pct": 0, "critical_90pct": 0},
            "peak_hour": 9, "peak_hour_tokens": 100000,
            "hourly": {"9": {"prompt": 80000, "total": 100000, "requests": 20}},
        }

    def _compare_line(self, report):
        for ln in report.splitlines():
            if "环比" in ln:
                return ln.strip()
        return ""

    def test_blood_lesson_gap_not_labeled_yesterday(self):
        """血案: history 缺 3 天 → 4 天前的数据被标成「昨日」。"""
        report = self.tr.format_report(
            self._data("2026-08-25", 300_000),
            {"date": "2026-08-21", "total_tokens": 1_000_000},
        )
        line = self._compare_line(report)
        self.assertTrue(line, "环比行消失了")
        self.assertNotIn("昨日", line,
                         f"4 天前的数据被标成『昨日』: {line}")
        self.assertIn("2026-08-21", line, f"未披露真实对比日期: {line}")

    def test_true_yesterday_still_labeled_yesterday(self):
        """检出力不减: 真昨日仍说昨日。"""
        report = self.tr.format_report(
            self._data("2026-08-25", 300_000),
            {"date": "2026-08-24", "total_tokens": 400_000},
        )
        self.assertIn("昨日", self._compare_line(report))

    def test_no_prev_no_comparison(self):
        report = self.tr.format_report(self._data("2026-08-25"), None)
        self.assertEqual(self._compare_line(report), "")

    def test_prev_without_date_is_not_called_yesterday(self):
        """旧 history 条目缺 date 字段 → FAIL-OPEN 但不得谎称昨日。"""
        report = self.tr.format_report(
            self._data("2026-08-25", 300_000), {"total_tokens": 400_000})
        line = self._compare_line(report)
        self.assertTrue(line)
        self.assertNotIn("昨日", line)

    def test_dead_success_rids_retired(self):
        """F4: 未被读取的 success_rids 整段（含多余一遍全日志扫描）已退役。"""
        with open(TOKEN_REPORT_PY, "r") as f:
            body = _strip_comment_lines(f.read())
        self.assertNotIn("success_rids", body)


class TestCronDoctorDaemonCheck(unittest.TestCase):
    """F5: cron_doctor.sh Linux 分支的 pgrep 判据必须真能匹配到进程。

    pgrep 的 pattern 是 ERE —— `\\|` 是字面竖线不是「或」。原写法找的是一个名字
    逐字等于 `cron|crond` 的进程, 永不匹配 → Linux 上 cron 正常也恒报「未运行」。
    macOS 生产走 launchctl 分支不受影响; 影响 dev 与 H1-C 第二实例(Linux)。
    """

    CRON_DOCTOR = os.path.join(REPO, "cron_doctor.sh")

    def _linux_branch_pattern(self):
        with open(self.CRON_DOCTOR, "r") as f:
            src = _strip_comment_lines(f.read())
        m = re.search(r'pgrep -x "([^"]+)"', src)
        return m.group(1) if m else None

    def test_pattern_extractable(self):
        self.assertIsNotNone(self._linux_branch_pattern(),
                             "抽不到 cron_doctor 的 pgrep 判据")

    def test_bre_escape_retired(self):
        pat = self._linux_branch_pattern()
        self.assertNotIn("\\|", pat,
                         f"pgrep ERE pattern 里仍有 BRE 转义 `\\|`: {pat!r}")

    def test_alternation_present(self):
        """防空转: 收紧后仍要覆盖 cron 与 crond 两种守护进程名。"""
        pat = self._linux_branch_pattern()
        self.assertIn("|", pat)
        self.assertIn("cron", pat)
        self.assertIn("crond", pat)

    def test_pattern_semantics_against_real_process(self):
        """行为级: 起一个名字已知的真进程, 证明 BRE 形态匹配不到、ERE 形态匹配得到。

        不依赖 cron 是否在跑 —— 只验证判据的**形态**。2026-08-25 实测: 6 个 bash
        在跑时 `pgrep -x "bash\\|sh"` rc=1（该转义在 ERE 里是字面竖线）。
        """
        proc = subprocess.Popen(["sleep", "30"])
        try:
            broken = subprocess.run(["pgrep", "-x", "sleep\\|nosuchproc"],
                                    capture_output=True)
            fixed = subprocess.run(["pgrep", "-x", "sleep|nosuchproc"],
                                   capture_output=True)
            self.assertNotEqual(broken.returncode, 0,
                                "BRE 转义形态竟匹配到了真进程 —— 探针前提失效")
            self.assertEqual(fixed.returncode, 0,
                             "ERE 形态匹配不到已知存在的 sleep 进程 —— 探针前提失效")
        finally:
            proc.terminate()
            proc.wait(timeout=5)


class TestV37_9_328_Markers(unittest.TestCase):
    def test_markers_present(self):
        for path in (WATCHDOG_SH, CONV_QUALITY_PY, TOKEN_REPORT_PY,
                     os.path.join(REPO, "cron_doctor.sh")):
            with open(path, "r") as f:
                self.assertIn("V37.9.328", f.read(),
                              f"{os.path.basename(path)} 缺 V37.9.328 血案 marker")


if __name__ == "__main__":
    unittest.main(verbosity=2)
