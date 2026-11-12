#!/usr/bin/env python3
"""V37.9.386 守卫 — SLO 降级率告警必须说出原因。

血案 (2026-10-28 → 11-09, V37.9.381 复盘的「掩护者」):
    主力 key 被停用, 401 持续 12 天, 全流量由 fallback 承接。每一层都记成功,
    唯一可见信号是 watchdog 告警里一行「degradation_rate: 90.0% (目标: <5.0%)」——
    只有百分比。原因从第一次失败起就在 adapter 里, 但没有任何读者拿得到。

修复 (三个既有文件, 零新 job / 零新状态文件):
    adapter      /health 暴露 primary_last_error{reason,at} / primary_last_ok
    proxy        降级按去向计数 slo.fallback_by_provider
    slo_checker  降级率违规时, 违规行同一行带上: 去向 / 主力+断路器 / 最近失败原因

测试隔离 (MR-9): ProxyStats 的 STATS_FILE 指向临时目录; adapter 只起临时端口并替换
_forward_request, 绝不触网也不碰 5001; slo_checker CLI 用隔离 HOME + 指向本测试
起的假 /health。
"""
from __future__ import annotations

import http.server
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import unittest
from urllib.request import Request, urlopen

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

import slo_checker  # noqa: E402

_CFG = {"slo": {"latency_p95_ms": 30000, "tool_success_rate_pct": 95.0,
                "degradation_rate_pct": 5.0, "timeout_rate_pct": 3.0,
                "auto_recovery_rate_pct": 90.0}}


def _read(name):
    with open(os.path.join(_HERE, name), encoding="utf-8") as f:
        return f.read()


def _strip_comment_lines(src):
    return "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))


def _incident_stats():
    """血案形状: 458 请求里 412 次由 fallback 服务。"""
    return {"total_requests": 458,
            "slo": {"latency": {"p95": 2100, "count": 458},
                    "tool_success_rate_pct": 100.0, "tool_calls_total": 20,
                    "degradation_rate_pct": 90.0, "timeout_rate_pct": 0.0,
                    "auto_recovery_rate_pct": 100.0, "failure_streaks": 0,
                    "fallback_count": 412,
                    "fallback_by_provider": {"deepseek_full": 410, "kb_followup": 2}}}


_INCIDENT_HEALTH = {
    "ok": True, "provider": "doubao_21", "model": "ep-20990101000000-zzzzz",
    "fallback_chain": ["deepseek_full", "deepseek", "qwen"],
    "circuit_breaker": "open",
    "primary_last_error": {"reason": "HTTP Error 401: Unauthorized",
                           "at": "2026-10-28 17:00:28"},
}


def _alert(stats, health):
    results, _ = slo_checker.check_slo(stats, _CFG)
    ev = {"degradation_rate": slo_checker.degradation_evidence(stats, health)}
    return slo_checker.format_alert(results, ev)


class TestAlertSaysWhy(unittest.TestCase):
    """用户看到的那条告警长什么样 (原则 #36-1: 先断言结果)。"""

    def test_incident_alert_names_destination_primary_breaker_and_reason(self):
        text = _alert(_incident_stats(), _INCIDENT_HEALTH)
        line = [l for l in text.splitlines() if "degradation_rate" in l]
        self.assertEqual(len(line), 1, text)
        line = line[0]
        self.assertIn("90.0%", line)
        self.assertIn("deepseek_full×410", line)
        self.assertIn("412/458", line)
        self.assertIn("doubao_21", line)
        self.assertIn("断路器 open", line)
        self.assertIn("HTTP Error 401: Unauthorized", line)
        self.assertIn("2026-10-28 17:00:28", line)
        self.assertIn("未成功过", line)

    def test_reverse_evidence_old_alert_had_only_a_percentage(self):
        results, _ = slo_checker.check_slo(_incident_stats(), _CFG)
        old = slo_checker.format_alert(results)
        self.assertIn("degradation_rate: 90.0%", old)
        self.assertNotIn("401", old)
        self.assertNotIn("deepseek_full", old)

    def test_evidence_stays_on_the_violation_line(self):
        """watchdog 把 --alert 输出按行拆成独立告警: 证据另起行 = 一条违规被数成多条。"""
        text = _alert(_incident_stats(), _INCIDENT_HEALTH)
        self.assertEqual(len(text.splitlines()), 2, text)  # 标题行 + 1 条违规

    def test_multiline_reason_cannot_break_the_line(self):
        h = dict(_INCIDENT_HEALTH)
        h["primary_last_error"] = {"reason": "boom\nsecond line", "at": "t"}
        self.assertEqual(len(_alert(_incident_stats(), h).splitlines()), 2)

    def test_endpoint_id_never_rendered(self):
        self.assertNotIn("ep-2099", _alert(_incident_stats(), _INCIDENT_HEALTH))

    def test_health_unreachable_is_said_not_guessed(self):
        text = _alert(_incident_stats(), None)
        self.assertIn("/health 不可达", text)
        self.assertIn("deepseek_full×410", text)  # proxy 侧去向仍在
        self.assertNotIn("断路器", text)

    def test_old_adapter_without_reason_fields_is_said(self):
        h = {"provider": "doubao_21", "circuit_breaker": "half-open"}
        text = _alert(_incident_stats(), h)
        self.assertIn("断路器 half-open", text)
        self.assertIn("失败原因未提供", text)

    def test_old_proxy_without_destination_map_is_said(self):
        st = _incident_stats()
        del st["slo"]["fallback_by_provider"]
        text = _alert(st, _INCIDENT_HEALTH)
        self.assertIn("去向未记录", text)
        self.assertIn("412/458", text)

    def test_primary_healthy_degradation_from_other_cause(self):
        """主力没失败过而降级率高 (如 kb_followup 降级) → 不得凭空给主力安一个故障。"""
        st = _incident_stats()
        st["slo"]["fallback_by_provider"] = {"kb_followup": 412}
        h = {"provider": "doubao_21_tokenhub", "circuit_breaker": "closed",
             "primary_last_ok": "2026-11-12 09:00:00"}
        text = _alert(st, h)
        self.assertIn("kb_followup×412", text)
        self.assertIn("无失败记录", text)
        self.assertNotIn("最近失败:", text)

    def test_other_violations_unchanged(self):
        st = _incident_stats()
        st["slo"]["timeout_rate_pct"] = 50.0
        text = _alert(st, _INCIDENT_HEALTH)
        tl = [l for l in text.splitlines() if "timeout_rate" in l][0]
        self.assertTrue(tl.rstrip().endswith("(目标: <3.0%)"), tl)

    def test_no_violation_no_alert(self):
        st = _incident_stats()
        st["slo"]["degradation_rate_pct"] = 1.0
        results, _ = slo_checker.check_slo(st, _CFG)
        self.assertEqual(slo_checker.format_alert(results, {"degradation_rate": ["x"]}), "")


class TestProxyRecordsDestination(unittest.TestCase):
    def _make(self):
        import proxy_filters as pf
        ps = pf.ProxyStats()
        tmp = tempfile.mkdtemp(prefix="v386_stats_")
        old = pf.STATS_FILE
        pf.STATS_FILE = os.path.join(tmp, "proxy_stats.json")
        self.addCleanup(lambda: setattr(pf, "STATS_FILE", old))
        return ps, pf.STATS_FILE

    def test_counts_by_provider_in_both_surfaces(self):
        ps, path = self._make()
        for _ in range(5):
            ps.record_success({}, latency_ms=10)
        ps.record_fallback("deepseek_full")
        ps.record_fallback("deepseek_full")
        ps.record_fallback("kb_followup")
        slo = ps.get_stats_dict()["slo"]
        self.assertEqual(slo["fallback_by_provider"],
                         {"deepseek_full": 2, "kb_followup": 1})
        self.assertEqual(slo["fallback_count"], 3)
        ps._write_stats()
        with open(path) as f:
            disk = json.load(f)["slo"]
        self.assertEqual(disk["fallback_by_provider"], slo["fallback_by_provider"])

    def test_sum_equals_fallback_count(self):
        """去向分项之和必须等于总数 (原则 #36-4: 数字要对得上账)。"""
        ps, _ = self._make()
        for name in ("a", "b", "a", "", "c"):
            ps.record_fallback(name)
        slo = ps.get_stats_dict()["slo"]
        self.assertEqual(sum(slo["fallback_by_provider"].values()), slo["fallback_count"])
        self.assertEqual(slo["fallback_by_provider"].get("unknown"), 1)

    def test_legacy_call_without_argument_still_works(self):
        ps, _ = self._make()
        ps.record_fallback()
        self.assertEqual(ps.get_stats_dict()["slo"]["fallback_count"], 1)

    def test_day_reset_clears_destinations(self):
        ps, _ = self._make()
        ps.record_fallback("deepseek_full")
        ps._today = "1999-01-01"
        ps.record_success({}, latency_ms=10)
        slo = ps.get_stats_dict()["slo"]
        self.assertEqual(slo["fallback_by_provider"], {})
        self.assertEqual(slo["fallback_count"], 0)

    def test_tool_proxy_passes_the_header_value(self):
        src = _strip_comment_lines(_read("tool_proxy.py"))
        self.assertIn("proxy_stats.record_fallback(_fb_provider)", src)
        self.assertIn('proxy_stats.record_fallback("kb_followup")', src)
        self.assertNotIn("proxy_stats.record_fallback()", src)


class _SpyCB:
    def __init__(self):
        self._open = False

    def is_open(self):
        return self._open

    def record_success(self):
        pass

    def record_failure(self):
        pass

    def state(self):
        return "open" if self._open else "closed"


class TestAdapterExposesReason(unittest.TestCase):
    """真 adapter 进程内起临时端口: 主力失败 → /health 说出原因。"""

    @classmethod
    def setUpClass(cls):
        import adapter
        cls.adapter = adapter
        cls._saved = {k: getattr(adapter, k) for k in (
            "FALLBACK_CHAIN", "FAST_ROUTE", "_forward_request", "_circuit_breaker")}
        cls._saved_env = {k: os.environ.get(k) for k in ("ROUTER_ENFORCE", "GLM5_API_KEY")}
        os.environ["ROUTER_ENFORCE"] = "on"
        os.environ["GLM5_API_KEY"] = "test-override-key"
        adapter.FALLBACK_CHAIN = [{
            "name": "fb_one", "base_url": "http://fb-one.invalid/v1",
            "model_id": "fb-model", "auth_style": "bearer", "api_key": "k",
            "vl_model_id": "", "reasoning_off_body": None}]
        adapter.FAST_ROUTE = None
        cls.srv = adapter.ThreadedServer(("127.0.0.1", 0), adapter.ProxyHandler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        for k, v in cls._saved.items():
            setattr(cls.adapter, k, v)
        for k, v in cls._saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def setUp(self):
        self.cb = _SpyCB()
        self.adapter._circuit_breaker = self.cb
        with self.adapter._primary_last_lock:
            self.adapter._primary_last.update({"error": "", "error_at": 0.0, "ok_at": 0.0})

    def tearDown(self):
        self.adapter._forward_request = self._saved["_forward_request"]

    def _install(self, primary_exc=None):
        ok = json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode()

        def fake(url, data, auth_headers, timeout=300):
            if "fb-one.invalid" in url:
                return 200, ok
            if primary_exc is not None:
                raise primary_exc
            return 200, ok
        self.adapter._forward_request = fake

    def _post(self, query=""):
        body = json.dumps({"model": self.adapter.REAL_MODEL_ID,
                           "messages": [{"role": "user", "content": "hi"}]}).encode()
        req = Request(f"http://127.0.0.1:{self.port}/v1/chat/completions{query}",
                      data=body, method="POST")
        req.add_header("Content-Type", "application/json")
        try:
            with urlopen(req, timeout=15) as resp:
                resp.read()
        except Exception:
            pass

    def _health(self):
        with urlopen(f"http://127.0.0.1:{self.port}/health", timeout=5) as r:
            return json.loads(r.read())

    def test_fresh_adapter_has_neither_field(self):
        h = self._health()
        self.assertNotIn("primary_last_error", h)
        self.assertNotIn("primary_last_ok", h)

    def test_primary_failure_reason_reaches_health(self):
        self._install(OSError("HTTP Error 401: Unauthorized"))
        self._post()
        h = self._health()
        self.assertEqual(h["primary_last_error"]["reason"], "HTTP Error 401: Unauthorized")
        self.assertRegex(h["primary_last_error"]["at"], r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d$")
        self.assertNotIn("primary_last_ok", h)

    def test_reason_survives_while_breaker_is_open(self):
        """断路器打开后主力不再被尝试 —— 保留下来的必须是让它打开的那个原因。"""
        self._install(OSError("HTTP Error 401: Unauthorized"))
        self._post()
        self.cb._open = True
        self._install(OSError("should never be attempted"))
        self._post()
        self._post()
        h = self._health()
        self.assertEqual(h["circuit_breaker"], "open")
        self.assertEqual(h["primary_last_error"]["reason"], "HTTP Error 401: Unauthorized")

    def test_success_records_ok_time_and_keeps_last_error(self):
        self._install(OSError("timed out"))
        self._post()
        self._install(None)
        self._post()
        h = self._health()
        self.assertIn("primary_last_ok", h)
        self.assertEqual(h["primary_last_error"]["reason"], "timed out")

    def test_override_request_does_not_touch_default_primary_record(self):
        self._install(OSError("override provider down"))
        self._post("?provider=glm5_coding")
        h = self._health()
        self.assertNotIn("primary_last_error", h)
        self.assertNotIn("primary_last_ok", h)

    def test_secret_shaped_text_is_redacted_and_long_text_truncated(self):
        s = self.adapter._sanitize_primary_error
        # 假密钥按片段拼接: 整串字面量会被仓库自己的泄漏扫描当成真密钥
        fake_key = "sk" + "-" + "abcdef0123456789abcdef"
        out = s(f"401 for key {fake_key} and ep-20990101000000-zzzzz "
                "Authorization: Bearer tok_en-123")
        for leak in ("abcdef0123456789", "ep-2099", "tok_en-123"):
            self.assertNotIn(leak, out)
        self.assertIn("[redacted]", out)
        self.assertLessEqual(len(s("x" * 5000)), self.adapter._PRIMARY_ERR_MAX + 1)
        self.assertNotIn("\n", s("a\nb"))
        self.assertTrue(s(OSError()))  # 空消息 → 回落到异常类型名

    def test_end_to_end_adapter_health_into_alert(self):
        """生产端与消费端对接: 真 /health 的字段名就是 slo_checker 读的字段名。"""
        self._install(OSError("HTTP Error 401: Unauthorized"))
        self._post()
        health = slo_checker.read_adapter_health(
            f"http://127.0.0.1:{self.port}/health")
        text = _alert(_incident_stats(), health)
        self.assertIn("HTTP Error 401: Unauthorized", text)
        self.assertIn(self.adapter.PROVIDER_NAME, text)


class _FakeHealth(http.server.BaseHTTPRequestHandler):
    payload = b"{}"
    hits = 0

    def do_GET(self):
        type(self).hits += 1
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(type(self).payload)

    def log_message(self, *a):
        pass


class TestCliContract(unittest.TestCase):
    """真 subprocess 跑 slo_checker --alert (watchdog 的调用形态)。"""

    @classmethod
    def setUpClass(cls):
        cls.srv = http.server.HTTPServer(("127.0.0.1", 0), _FakeHealth)
        cls.url = f"http://127.0.0.1:{cls.srv.server_address[1]}/health"
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def _run(self, stats, url):
        home = tempfile.mkdtemp(prefix="v386_home_")
        with open(os.path.join(home, "proxy_stats.json"), "w") as f:
            json.dump(stats, f)
        env = dict(os.environ, HOME=home, ADAPTER_HEALTH_URL=url)
        return subprocess.run([sys.executable, os.path.join(_HERE, "slo_checker.py"), "--alert"],
                              capture_output=True, text=True, env=env, timeout=60, cwd=_HERE)

    def test_violation_prints_reason_and_exits_2(self):
        _FakeHealth.payload = json.dumps(_INCIDENT_HEALTH).encode()
        p = self._run(_incident_stats(), self.url)
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
        self.assertIn("HTTP Error 401: Unauthorized", p.stdout)
        self.assertEqual(len(p.stdout.strip().splitlines()), 2, p.stdout)

    def test_dead_health_endpoint_still_alerts(self):
        p = self._run(_incident_stats(), "http://127.0.0.1:9/health")
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
        self.assertIn("degradation_rate: 90.0%", p.stdout)
        self.assertIn("/health 不可达", p.stdout)

    def test_garbage_health_body_treated_as_unreachable(self):
        _FakeHealth.payload = b"<html>not json</html>"
        p = self._run(_incident_stats(), self.url)
        self.assertEqual(p.returncode, 2)
        self.assertIn("/health 不可达", p.stdout)

    def test_health_not_read_when_degradation_is_fine(self):
        """平日零开销: 降级率达标时不发 /health 请求。"""
        st = _incident_stats()
        st["slo"]["degradation_rate_pct"] = 1.0
        _FakeHealth.payload = json.dumps(_INCIDENT_HEALTH).encode()
        before = _FakeHealth.hits
        p = self._run(st, self.url)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(_FakeHealth.hits, before)


class TestContracts(unittest.TestCase):
    def test_alert_text_trips_no_critical_classifier(self):
        """watchdog 用子串把告警分 CRITICAL/WARNING: 证据片段不得让 SLO 行被升级。"""
        src = _read("job_watchdog.sh")
        m = re.search(r'case "\$a" in\n\s*(\*".*?)\)\n\s*CRITICAL_COUNT', src, re.S)
        self.assertIsNotNone(m, "未找到 watchdog 严重度分类行")
        needles = re.findall(r'\*"([^"]+)"\*', m.group(1))
        self.assertGreaterEqual(len(needles), 4)
        for health in (_INCIDENT_HEALTH, None, {"provider": "p", "circuit_breaker": "closed"}):
            text = _alert(_incident_stats(), health)
            for n in needles:
                self.assertNotIn(n, text)

    def test_watchdog_still_reads_alert_line_by_line(self):
        src = _strip_comment_lines(_read("job_watchdog.sh"))
        self.assertRegex(src, r'done <<< "\$SLO_ALERT"')

    def test_health_keys_consumed_are_produced(self):
        adapter_src = _strip_comment_lines(_read("adapter.py"))
        checker_src = _strip_comment_lines(_read("slo_checker.py"))
        for key in ("primary_last_error", "primary_last_ok"):
            self.assertIn(f'"{key}"', adapter_src)
            self.assertIn(f'"{key}"', checker_src)
        self.assertIn("info.update(_primary_last_for_health())", adapter_src)

    def test_proxy_key_consumed_is_produced(self):
        self.assertEqual(
            _strip_comment_lines(_read("proxy_filters.py")).count('"fallback_by_provider"'), 2)
        self.assertIn('"fallback_by_provider"', _strip_comment_lines(_read("slo_checker.py")))

    def test_registered_in_full_regression(self):
        self.assertIn("test_v37_9_386_degradation_alert_evidence.py",
                      _read("full_regression.sh"))


if __name__ == "__main__":
    unittest.main(verbosity=1)
