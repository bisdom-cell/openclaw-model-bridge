#!/usr/bin/env python3
"""V37.9.349 影响力指标机械化守卫 — influence_metrics.py + health_check 第 10 段「📣 影响力」.

背景: 纲领执行计划 §5 R1 把「论文引用 / PyPI 下载 / GitHub star」三条采集命令写成每季度手工
跑, 自 V37.9.248 建协议起没有一条实测值入库 (手工的问题不是慢, 是没人跑). 本版把它折进每周一
09:00 既有 health_check 周报 (零新 job / 零新 cron / 零新状态文件), 历史落 status.json
quality.influence (经 status_update 锁 = MR-9).

守卫五类 (行为级优先, 源码守卫只钉接线):
  1. 采集诚实: 每源独立 FAIL-OPEN, 不可达/字段漂移 → None 带原因码, **绝不写 0**
     (V37.9.322 F3「跑不动 ≠ 没问题」家族: 0 引用/0 下载会被读作真实测量值).
  2. 历史与趋势: 同日幂等覆盖 / 封顶 / delta 不可比 → None / 零增长 streak 只数可比对.
  3. 持久化 MR-9: 只写 status_update.STATUS_FILE (测试指向 tempdir, 绝不碰真 status.json),
     三源全不可达 → 不写不追加; 写失败 → 周报仍渲染.
  4. watchdog 契约 (MR-8 从 job_watchdog.sh 真源码抽 err_pattern): 渲染行与 stderr WARN 的
     原因码都不得匹配告警正则 (否则周报正文/日志会变成假告警), 含反向证据防空转.
  5. 接线: health_check.sh 经 safe_call 调模块 / --record 由 HEALTH_INFLUENCE_RECORD 门控 /
     块在 REPORT 里位于「周报完毕」之前 / 执行计划 §5 已退役手工三命令改指本模块 /
     日落法: jobs 仍 47, 无新状态文件.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO)

import influence_metrics as im  # noqa: E402
import status_update  # noqa: E402

HEALTH_SH = os.path.join(REPO, "health_check.sh")
WATCHDOG_SH = os.path.join(REPO, "job_watchdog.sh")
PLAN_MD = os.path.join(REPO, "docs", "charter_execution_plan_20260705.md")
REPO_STATUS = os.path.join(REPO, "status.json")


def _read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


def _exec_lines(src):
    """去掉 bash 注释行 (V37.9.178 家族: 守卫不得被自己的注释咬)."""
    return "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))


def _ok_getter(url, headers=None, timeout=8):
    if "semanticscholar" in url:
        return {"citationCount": 3, "influentialCitationCount": 1}, None
    if "pypistats" in url:
        return {"data": {"last_day": 2, "last_week": 42, "last_month": 150}}, None
    if "github" in url:
        return {"stargazers_count": 12, "forks_count": 2, "open_issues_count": 1}, None
    return None, "bad_url"


def _dead_getter(url, headers=None, timeout=8):
    return None, "connect_failed:refused"


def _snapshot(getter=_ok_getter, date="2026-09-07"):
    import datetime
    return im.collect(getter=getter, env={}, today=datetime.date.fromisoformat(date))


# ---------------------------------------------------------------------------
# 1. 采集诚实
# ---------------------------------------------------------------------------
class TestFetchHonesty(unittest.TestCase):

    def test_all_three_parse_real_shapes(self):
        snap = _snapshot()
        self.assertEqual(snap["s2"], {"ok": True, "error": None, "citations": 3, "influential": 1})
        self.assertEqual(snap["pypi"], {"ok": True, "error": None,
                                        "downloads_week": 42, "downloads_month": 150})
        self.assertEqual(snap["github"]["stars"], 12)
        self.assertEqual(snap["github"]["forks"], 2)
        self.assertTrue(snap["github"]["ok"])

    def test_unreachable_never_becomes_zero(self):
        """血案家族回归: 不可达必须是 None + 原因码, 绝不是 0."""
        snap = _snapshot(getter=_dead_getter)
        for src in ("s2", "pypi", "github"):
            self.assertFalse(snap[src]["ok"])
            self.assertEqual(snap[src]["error"], "connect_failed:refused")
            for k, v in snap[src].items():
                if k not in ("ok", "error"):
                    self.assertIsNone(v, f"{src}.{k} 不可达时必须 None 不是 {v!r}")
        row = im.flatten(snap)
        self.assertTrue(all(row[k] is None for k in im._METRIC_KEYS))
        self.assertFalse(im.any_source_ok(snap))

    def test_schema_drift_is_failure_not_zero(self):
        """上游改字段名 (例: citationCount 消失) → ok=False 带 schema_drift, 不是 0."""
        def drifted(url, headers=None, timeout=8):
            if "semanticscholar" in url:
                return {"citations_total": 9}, None
            if "pypistats" in url:
                return {"data": {"weekly": 5}}, None
            if "github" in url:
                return {"stars": 7}, None
        snap = _snapshot(getter=drifted)
        self.assertEqual(snap["s2"]["error"], "schema_drift:citationCount")
        self.assertEqual(snap["pypi"]["error"], "schema_drift:last_week")
        self.assertEqual(snap["github"]["error"], "schema_drift:stargazers_count")
        self.assertIsNone(snap["s2"]["citations"])
        self.assertIsNone(snap["pypi"]["downloads_week"])
        self.assertIsNone(snap["github"]["stars"])

    def test_as_int_rejects_bool_and_strings(self):
        self.assertIsNone(im._as_int(True))
        self.assertIsNone(im._as_int("12"))
        self.assertIsNone(im._as_int(None))
        self.assertEqual(im._as_int(12.0), 12)
        self.assertIsNone(im._as_int(12.5))

    def test_partial_outage_keeps_other_sources(self):
        """一源挂不影响其余两源 (per-source FAIL-OPEN)."""
        def partial(url, headers=None, timeout=8):
            if "pypistats" in url:
                return None, "http_503"
            return _ok_getter(url, headers, timeout)
        snap = _snapshot(getter=partial)
        self.assertTrue(snap["s2"]["ok"])
        self.assertFalse(snap["pypi"]["ok"])
        self.assertEqual(snap["pypi"]["error"], "http_503")
        self.assertTrue(snap["github"]["ok"])
        self.assertTrue(im.any_source_ok(snap))

    def test_optional_keys_only_sent_when_present(self):
        seen = {}

        def spy(url, headers=None, timeout=8):
            seen[url.split("/")[2]] = dict(headers or {})
            return _ok_getter(url, headers, timeout)
        im.collect(getter=spy, env={})
        self.assertNotIn("x-api-key", seen["api.semanticscholar.org"])
        self.assertNotIn("Authorization", seen["api.github.com"])
        seen.clear()
        im.collect(getter=spy, env={"S2_API_KEY": "k1", "GITHUB_TOKEN": "t1"})
        self.assertEqual(seen["api.semanticscholar.org"].get("x-api-key"), "k1")
        self.assertEqual(seen["api.github.com"].get("Authorization"), "Bearer t1")


# ---------------------------------------------------------------------------
# 2. 历史与趋势
# ---------------------------------------------------------------------------
class TestHistoryAndTrend(unittest.TestCase):

    def test_deltas_none_when_either_side_missing(self):
        row = {"citations": 3, "stars": 12, "downloads_week": None}
        prev = {"citations": 1, "stars": None, "downloads_week": 10}
        d = im.compute_deltas(row, prev)
        self.assertEqual(d["citations"], 2)
        self.assertIsNone(d["stars"])
        self.assertIsNone(d["downloads_week"])
        self.assertTrue(all(v is None for v in im.compute_deltas(row, None).values()))

    def test_merge_same_day_overwrites_not_duplicates(self):
        hist = [{"date": "2026-09-07", "citations": 1}]
        out = im._merge_history(hist, {"date": "2026-09-07", "citations": 2})
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["citations"], 2)
        out = im._merge_history(out, {"date": "2026-09-14", "citations": 2})
        self.assertEqual(len(out), 2)

    def test_history_capped(self):
        hist = [{"date": "2025-01-%02d" % (i % 28 + 1), "citations": i} for i in range(im.HISTORY_MAX + 5)]
        out = im._merge_history(hist, {"date": "2026-09-07", "citations": 99})
        self.assertEqual(len(out), im.HISTORY_MAX)
        self.assertEqual(out[-1]["citations"], 99)

    def test_flat_streak_counts_only_comparable_pairs(self):
        base = {"citations": 3, "downloads_week": 40, "stars": 12}
        hist = [dict(base, date="d%d" % i) for i in range(5)]  # 4 对全平
        self.assertEqual(im.flat_streak(hist), 4)
        hist[2]["citations"] = 2  # d2→d3 有增长 → streak 只数 d3→d4
        self.assertEqual(im.flat_streak(hist), 1)
        # 全 None 的一行不可比 → 跳过不计也不断
        hist = [dict(base, date="a"), {"date": "b", "citations": None, "downloads_week": None,
                                        "stars": None}, dict(base, date="c")]
        self.assertEqual(im.flat_streak(hist), 1, "全 None 周跳过但不打断: a↔c 仍可比")
        # 全 None 周既不算零增长周: [a, None, None] → 0 (没数据 ≠ 没增长)
        self.assertEqual(im.flat_streak([dict(base, date="a"), {"date": "b"}, {"date": "c"}]), 0)
        self.assertEqual(im.flat_streak([]), 0)
        self.assertEqual(im.flat_streak([dict(base, date="only")]), 0)


# ---------------------------------------------------------------------------
# 渲染
# ---------------------------------------------------------------------------
class TestRender(unittest.TestCase):

    def test_full_line_with_deltas(self):
        snap = _snapshot()
        line = im.render_line(snap, deltas={"citations": 1, "downloads_week": -3, "stars": 0})
        self.assertTrue(line.startswith("📣 影响力: "))
        self.assertIn("引用 3 (+1)", line)
        self.assertIn("PyPI 周下载 42 (-3)", line)
        self.assertIn("⭐ 12 (±0) fork 2", line)
        self.assertNotIn("零增长", line)

    def test_first_record_says_first(self):
        line = im.render_line(_snapshot(), first=True)
        self.assertEqual(line.count("(首次)"), 3)

    def test_partial_unreachable_marked_per_source(self):
        def partial(url, headers=None, timeout=8):
            if "semanticscholar" in url:
                return None, "http_429"
            return _ok_getter(url, headers, timeout)
        line = im.render_line(_snapshot(getter=partial))
        self.assertIn("引用 ⚠️不可达(http_429)", line)
        self.assertIn("PyPI 周下载 42", line)
        self.assertIn("⭐ 12", line)

    def test_all_unreachable_says_not_recorded(self):
        line = im.render_line(_snapshot(getter=_dead_getter))
        self.assertIn("三源均不可达", line)
        self.assertIn("本周不记录", line)
        self.assertNotRegex(line, r"引用 0|下载 0|⭐ 0", "不可达绝不渲染成 0")

    def test_streak_warning_threshold(self):
        snap = _snapshot()
        self.assertNotIn("零增长", im.render_line(snap, streak=im.FLAT_STREAK_WARN_WEEKS - 1))
        self.assertIn("连续 %d 周零增长" % im.FLAT_STREAK_WARN_WEEKS,
                      im.render_line(snap, streak=im.FLAT_STREAK_WARN_WEEKS))


# ---------------------------------------------------------------------------
# 3. 持久化 MR-9
# ---------------------------------------------------------------------------
class TestRecordMR9(unittest.TestCase):
    """全部写入指向 tempdir; 真 status.json (repo 副本) 全程 mtime 不变."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="infl_status_")
        self.status_path = os.path.join(self.tmp, "status.json")
        self._orig = status_update.STATUS_FILE
        status_update.STATUS_FILE = self.status_path
        with open(self.status_path, "w") as f:
            json.dump({"quality": {"security_score": 98}, "session_context": {}}, f)
        self.repo_mtime = os.stat(REPO_STATUS).st_mtime_ns

    def tearDown(self):
        status_update.STATUS_FILE = self._orig
        shutil.rmtree(self.tmp, ignore_errors=True)
        self.assertEqual(os.stat(REPO_STATUS).st_mtime_ns, self.repo_mtime,
                         "MR-9: 测试不得触碰 repo status.json")

    def _load(self):
        with open(self.status_path) as f:
            return json.load(f)

    def test_record_writes_quality_influence_and_keeps_other_keys(self):
        recorded, prev, hist = im.record(_snapshot())
        self.assertTrue(recorded)
        self.assertIsNone(prev)
        data = self._load()
        self.assertEqual(data["quality"]["security_score"], 98, "不得覆盖同级既有键")
        infl = data["quality"]["influence"]
        self.assertEqual(infl["updated"], "2026-09-07")
        self.assertEqual(infl["latest"]["citations"], 3)
        self.assertEqual(len(infl["history"]), 1)
        self.assertEqual(infl["sources"]["pypi"], im.PYPI_PACKAGE)
        self.assertEqual(data["updated_by"], "health_check")

    def test_second_week_gives_prev_and_delta(self):
        im.record(_snapshot(date="2026-09-07"))

        def grown(url, headers=None, timeout=8):
            obj, err = _ok_getter(url, headers, timeout)
            if obj and "citationCount" in obj:
                obj["citationCount"] = 5
            return obj, err
        recorded, prev, hist = im.record(_snapshot(getter=grown, date="2026-09-14"))
        self.assertTrue(recorded)
        self.assertEqual(prev["date"], "2026-09-07")
        self.assertEqual(len(hist), 2)
        row = im.flatten(_snapshot(getter=grown, date="2026-09-14"))
        self.assertEqual(im.compute_deltas(row, prev)["citations"], 2)

    def test_same_day_rerun_idempotent(self):
        im.record(_snapshot())
        im.record(_snapshot())
        self.assertEqual(len(self._load()["quality"]["influence"]["history"]), 1)

    def test_all_unreachable_does_not_write(self):
        before = _read(self.status_path)
        recorded, prev, hist = im.record(_snapshot(getter=_dead_getter))
        self.assertFalse(recorded)
        self.assertEqual(_read(self.status_path), before, "三源全不可达不得写任何东西")

    def test_no_new_state_file_only_status_json(self):
        """日落法: 历史只住既有 status.json, tempdir 里除 status.json(+lock) 无新文件."""
        im.record(_snapshot())
        names = sorted(os.listdir(self.tmp))
        self.assertTrue(set(names) <= {"status.json", "status.json.lock"}, names)

    def test_run_preview_does_not_write(self):
        before = _read(self.status_path)
        out = im.run(record_it=False, getter=_ok_getter, env={})
        self.assertFalse(out["recorded"])
        self.assertIn("(首次)", out["line"])
        self.assertEqual(_read(self.status_path), before)

    def test_record_failure_still_renders(self):
        """写失败 (锁/IO) → 周报仍渲染, 不抛. 用 monkeypatch 而非 chmod 探针 —— 本进程可能以
        root 跑, root 无视权限位 = V37.9.320/324 实录的假阴性."""
        orig = status_update.save_status

        def boom(*a, **k):
            raise OSError("disk full (simulated)")
        status_update.save_status = boom
        try:
            out = im.run(record_it=True, getter=_ok_getter, env={})
        finally:
            status_update.save_status = orig
        self.assertFalse(out["recorded"])
        self.assertTrue(out["line"].startswith("📣 影响力: 引用 3"), out["line"])
        self.assertNotIn("influence", self._load()["quality"], "写失败不得留下半截数据")


# ---------------------------------------------------------------------------
# 4. watchdog 契约 (MR-8)
# ---------------------------------------------------------------------------
def _watchdog_err_pattern():
    src = _read(WATCHDOG_SH)
    m = re.search(r"local err_pattern='([^']+)'", src)
    assert m, "job_watchdog.sh 必须仍定义 err_pattern"
    return re.compile(m.group(1))


class TestWatchdogContract(unittest.TestCase):

    def test_reason_codes_do_not_match_alert_pattern(self):
        pat = _watchdog_err_pattern()
        self.assertIsNotNone(pat.search("HTTP 403 something"), "反向证据: 判据本身必须能抓 HTTP 4xx")
        self.assertIsNotNone(pat.search("ERROR: x"))
        codes = ["http_403", "http_429", "http_503", "connect_failed:Tunnel_connection_failed",
                 "bad_json", "schema_drift:citationCount", "connect_failed:timeout"]
        for c in codes:
            self.assertIsNone(pat.search(c), f"原因码 {c!r} 不得匹配 watchdog err_pattern")

    def test_rendered_lines_and_warns_do_not_alert(self):
        pat = _watchdog_err_pattern()

        def http_dead(url, headers=None, timeout=8):
            return None, "http_403"
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            snap = _snapshot(getter=http_dead)
        line = im.render_line(snap)
        for text in [line] + buf.getvalue().splitlines():
            self.assertIsNone(pat.search(text), f"{text!r} 会被 watchdog 当错误")
        self.assertIn("[influence] WARN:", buf.getvalue())

    def test_real_http_error_maps_to_underscore_code(self):
        """真 urllib HTTPError → http_<code> (不是 'HTTP Error 403' 原文)."""
        import urllib.error
        import urllib.request

        class _Boom:
            def __enter__(self):
                raise urllib.error.HTTPError("u", 403, "Forbidden", {}, None)

            def __exit__(self, *a):
                return False
        orig = urllib.request.urlopen
        urllib.request.urlopen = lambda *a, **k: _Boom()
        try:
            obj, err = im._get_json("https://example.invalid/x")
        finally:
            urllib.request.urlopen = orig
        self.assertIsNone(obj)
        self.assertEqual(err, "http_403")

    def test_health_check_log_not_in_watchdog_error_scan(self):
        """契约事实: health_check.log 只有 LOG_FRESHNESS 不在 HOME_LOGS 错误扫描 —
        本模块 WARN 走周报 cron 日志无告警风险. 若未来加入, 本测试提醒复核原因码形态."""
        src = _read(WATCHDOG_SH)
        m = re.search(r"HOME_LOGS=\((.*?)\n\)", src, re.S)
        self.assertIsNotNone(m)
        self.assertNotIn("health_check.log", m.group(1))
        self.assertIn("health_check|$HOME/health_check.log", src, "LOG_FRESHNESS 条目仍在")


# ---------------------------------------------------------------------------
# 5. 接线 + 日落法
# ---------------------------------------------------------------------------
class TestHealthCheckWiring(unittest.TestCase):

    def setUp(self):
        self.src = _read(HEALTH_SH)
        self.code = _exec_lines(self.src)

    def test_segment_calls_module_via_safe_call(self):
        self.assertRegex(self.code,
                         r"INFLUENCE_BLOCK=\$\(safe_call \"python3 '\$REPO_DIR/influence_metrics\.py' \$INFL_ARGS\"")
        self.assertIn('"📣 影响力: 工具不可用 (influence_metrics.py 缺失)"', self.code)

    def test_record_gated_by_env(self):
        self.assertIn('INFL_ARGS="--record"', self.code)
        self.assertRegex(self.code, r'\[ "\$\{HEALTH_INFLUENCE_RECORD:-1\}" = "0" \]')
        self.assertIn('INFL_ARGS="--no-record"', self.code)

    def test_block_in_report_before_footer(self):
        rep = self.src[self.src.find('REPORT="📊'):self.src.find('✅ 周报完毕')]
        self.assertIn("${INFLUENCE_BLOCK}", rep)
        self.assertLess(rep.find("💾 外挂 SSD"), rep.find("${INFLUENCE_BLOCK}"))

    def test_header_documents_tenth_segment(self):
        self.assertIn("V37.9.349", self.src[:3000])
        self.assertIn("📣 影响力", self.src[:3000])

    def test_behavior_segment_rendered_by_real_module(self):
        """真跑 health_check.sh (隔离 env + 死代理): 第 10 段来自模块 (三源均不可达行) 而非
        fallback 文案, 且位于周报完毕之前; 全程不写 status.json."""
        env = os.environ.copy()
        env["OPENCLAW_REPO_DIR"] = REPO
        env["HOME"] = tempfile.mkdtemp(prefix="infl_home_")
        env["HEALTH_JSON_PATH"] = os.path.join(env["HOME"], "h.json")
        env["HEALTH_PUSH_MARKER"] = os.path.join(env["HOME"], "marker")
        open(env["HEALTH_PUSH_MARKER"], "w").close()
        env["HEALTH_PUSH_MIN_INTERVAL_SEC"] = "999999999"
        env["OPENCLAW_BIN"] = "/usr/bin/true"
        env["OPENCLAW"] = "/usr/bin/true"
        env["HEALTH_INFLUENCE_RECORD"] = "0"
        for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY"):
            env[k] = "http://127.0.0.1:9"
        env.pop("NO_PROXY", None)
        env.pop("no_proxy", None)
        before = os.stat(REPO_STATUS).st_mtime_ns
        try:
            proc = subprocess.run(["bash", HEALTH_SH], capture_output=True, text=True,
                                  timeout=150, env=env)
        finally:
            shutil.rmtree(env["HOME"], ignore_errors=True)
        out = proc.stdout
        self.assertIn("📣 影响力: ⚠️ 三源均不可达", out, out[-800:])
        self.assertNotIn("influence_metrics.py 缺失", out)
        self.assertLess(out.find("📣 影响力"), out.find("✅ 周报完毕"))
        self.assertEqual(os.stat(REPO_STATUS).st_mtime_ns, before, "MR-9")


class TestCli(unittest.TestCase):

    def _run(self, *args):
        env = os.environ.copy()
        env["HOME"] = tempfile.mkdtemp(prefix="infl_cli_")
        for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY"):
            env[k] = "http://127.0.0.1:9"
        env.pop("NO_PROXY", None)
        env.pop("no_proxy", None)
        try:
            return subprocess.run([sys.executable, os.path.join(REPO, "influence_metrics.py"), *args],
                                  capture_output=True, text=True, timeout=90, env=env)
        finally:
            shutil.rmtree(env["HOME"], ignore_errors=True)

    def test_dead_network_is_fail_open_rc0_and_honest(self):
        before = os.stat(REPO_STATUS).st_mtime_ns
        p = self._run("--record", "--json")
        self.assertEqual(p.returncode, 0, p.stderr)
        d = json.loads(p.stdout)
        self.assertFalse(d["recorded"])
        self.assertIn("三源均不可达", d["line"])
        self.assertTrue(all(d["row"][k] is None for k in im._METRIC_KEYS))
        self.assertEqual(os.stat(REPO_STATUS).st_mtime_ns, before,
                         "三源全不可达 + --record 也不得写 status.json")
        pat = _watchdog_err_pattern()
        for l in p.stderr.splitlines():
            self.assertIsNone(pat.search(l), l)

    def test_default_is_line_only(self):
        p = self._run()
        self.assertEqual(p.returncode, 0)
        self.assertTrue(p.stdout.startswith("📣 影响力:"), p.stdout)


class TestCrossFileAndSunset(unittest.TestCase):

    def test_pypi_name_matches_pyproject(self):
        src = _read(os.path.join(REPO, "pyproject.toml"))
        m = re.search(r'^name = "([^"]+)"', src, re.M)
        self.assertEqual(m.group(1), im.PYPI_PACKAGE)

    def test_arxiv_id_is_the_published_paper(self):
        self.assertIn(im.ARXIV_ID, _read(os.path.join(REPO, "README.md")))

    def test_execution_plan_r1_points_to_module(self):
        plan = _read(PLAN_MD)
        sec = plan[plan.find("### §5 R1"):plan.find("### §6 R2")]
        self.assertIn("influence_metrics.py", sec)
        self.assertIn("quality.influence", sec)
        self.assertNotIn("pip install --quiet pypistats", sec, "手工三命令已退役 (日落法)")
        self.assertNotIn("pypistats recent", sec)

    def test_claude_md_row_says_ten_segments(self):
        row = [l for l in _read(os.path.join(REPO, "CLAUDE.md")).splitlines()
               if l.startswith("| `health_check.sh` |")]
        self.assertEqual(len(row), 1)
        self.assertIn("10 段", row[0])
        self.assertIn("📣", row[0])

    def test_no_new_job(self):
        reg = _read(os.path.join(REPO, "jobs_registry.yaml"))
        self.assertEqual(len(re.findall(r"^  - id: ", reg, re.M)), 47)
        self.assertNotIn("influence", reg)


if __name__ == "__main__":
    unittest.main()
