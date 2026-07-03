#!/usr/bin/env python3
"""V37.9.229 审计 finding A 守卫 — SLO fallback 可观测（record_fallback 复活）。

血案背景（2026-07-02 多镜头对抗审计 finding A, MED-HIGH）:
    proxy_stats 位于 tool_proxy（adapter 之前），fallback 发生在 adapter 内部，
    proxy 只见正常 200 → record_fallback 死代码零生产调用 → degradation_rate_pct
    结构性永远 0% → slo_benchmark 永远 PASS / slo_dashboard 永远 Fallbacks:0。
    V37.9.220 场景（primary 全宕 100% fallback）时 SLO 仍报 healthy
    = fail-plausible SLO（正是论文 arXiv:2606.14589 批判的模式）。

修复（V37.9.229, 复用既有 HTTP 接缝零新状态源）:
    1. adapter fallback 成功响应加 `X-Adapter-Fallback: <provider>` header
       （_send_json 加可选 extra_headers；primary 成功 / 502 错误路径不加）
    2. tool_proxy 响应路径读该 header → proxy_stats.record_fallback()
       （header 在 proxy 消费，不下传 gateway）
    3. proxy_filters 两个 slo 落盘 dict 补 4 个消费方一直在读但 producer 从未
       落盘的原始计数（fallback_count / tool_calls_success / recovery_total /
       failure_streaks）——slo_benchmark degradation.fallback_count 与
       slo_dashboard Fallbacks 行从恒 0 变真值。

测试隔离（MR-9）: 所有触碰 record_* 的测试都 monkeypatch proxy_filters.STATS_FILE
到临时目录，绝不写真实 ~/proxy_stats.json。
"""

import json
import os
import re
import sys
import tempfile
import textwrap
import threading
import unittest
from urllib.request import Request, urlopen
from urllib.error import HTTPError

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

REPO = os.path.dirname(os.path.abspath(__file__))


def _read(fname):
    with open(os.path.join(REPO, fname), encoding="utf-8") as f:
        return f.read()


# ---------------------------------------------------------------------------
# 1. adapter._send_json extra_headers 单元行为（handler 无 socket 实例化）
# ---------------------------------------------------------------------------

class TestAdapterSendJsonExtraHeaders(unittest.TestCase):
    """_send_json 可选 extra_headers 契约（V37.9.229 新增参数）。"""

    def _make_handler(self):
        import adapter
        h = adapter.ProxyHandler.__new__(adapter.ProxyHandler)
        sent = []
        h.send_response = lambda s: sent.append(("__status__", s))
        h.send_header = lambda k, v: sent.append((k, v))
        h.end_headers = lambda: sent.append(("__end__", None))

        class _W:
            def __init__(self):
                self.data = b""

            def write(self, b):
                self.data += b

        h.wfile = _W()
        return h, sent

    def test_extra_headers_sent(self):
        h, sent = self._make_handler()
        h._send_json(200, b"{}", extra_headers={"X-Adapter-Fallback": "doubao_21"})
        self.assertIn(("X-Adapter-Fallback", "doubao_21"), sent)
        self.assertEqual(h.wfile.data, b"{}")

    def test_default_no_extra_headers(self):
        """向后兼容: 不传 extra_headers → 只有 Content-Type/Content-Length"""
        h, sent = self._make_handler()
        h._send_json(200, b"{}")
        header_keys = [k for k, _ in sent if not k.startswith("__")]
        self.assertEqual(sorted(header_keys), ["Content-Length", "Content-Type"])

    def test_extra_headers_before_end_headers(self):
        """extra header 必须在 end_headers 之前发出（否则不生效）"""
        h, sent = self._make_handler()
        h._send_json(200, b"{}", extra_headers={"X-Adapter-Fallback": "fb"})
        idx_hdr = sent.index(("X-Adapter-Fallback", "fb"))
        idx_end = sent.index(("__end__", None))
        self.assertLess(idx_hdr, idx_end)


# ---------------------------------------------------------------------------
# 2. adapter E2E — 真 ThreadedServer 驱动完整 do_POST（monkeypatch _forward_request）
# ---------------------------------------------------------------------------

class TestAdapterFallbackHeaderE2E(unittest.TestCase):
    """行为级铁证: fallback 服务的响应带 X-Adapter-Fallback, primary/502 不带。

    镜像 V37.9.224 的 E2E 方法（真 ThreadedServer + monkeypatch _forward_request），
    本版首次把该 harness 固化为回归测试。
    """

    @classmethod
    def setUpClass(cls):
        import adapter
        cls.adapter = adapter
        cls._saved = {
            "FALLBACK_CHAIN": adapter.FALLBACK_CHAIN,
            "FAST_ROUTE": adapter.FAST_ROUTE,
            "_forward_request": adapter._forward_request,
        }
        cls.fb_entry = {
            "name": "fb_test",
            "base_url": "http://fb-test.invalid/v1",
            "model_id": "fb-model",
            "auth_style": "bearer",
            "api_key": "test-key",
            "vl_model_id": "",
            "reasoning_off_body": None,
        }
        adapter.FALLBACK_CHAIN = [cls.fb_entry]
        adapter.FAST_ROUTE = None
        cls.srv = adapter.ThreadedServer(("127.0.0.1", 0), adapter.ProxyHandler)
        cls.port = cls.srv.server_address[1]
        cls.thread = threading.Thread(target=cls.srv.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        for k, v in cls._saved.items():
            setattr(cls.adapter, k, v)

    def setUp(self):
        # 每个测试前强制断路器 closed（前一测试的 primary 失败会 record_failure）
        self.adapter._circuit_breaker.record_success()

    def _post(self):
        body = json.dumps(
            {"model": "test-model", "messages": [{"role": "user", "content": "hi"}]}
        ).encode()
        req = Request(
            f"http://127.0.0.1:{self.port}/v1/chat/completions",
            data=body,
            method="POST",
        )
        req.add_header("Content-Type", "application/json")
        return urlopen(req, timeout=15)

    def test_fallback_served_response_carries_header(self):
        """血案核心: primary 宕 → fallback 服务 → 响应必带 X-Adapter-Fallback"""
        ok_body = json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode()

        def fake_forward(url, data, auth_headers, timeout=300):
            if "fb-test.invalid" in url:
                return 200, ok_body
            raise OSError("primary down (test)")

        self.adapter._forward_request = fake_forward
        with self._post() as resp:
            self.assertEqual(resp.status, 200)
            self.assertEqual(resp.headers.get("X-Adapter-Fallback"), "fb_test")
            self.assertEqual(resp.read(), ok_body)

    def test_primary_served_response_has_no_header(self):
        """primary 正常服务 → 绝不误标 fallback（否则 degradation_rate 虚高）"""
        ok_body = json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode()

        def fake_forward(url, data, auth_headers, timeout=300):
            return 200, ok_body

        self.adapter._forward_request = fake_forward
        with self._post() as resp:
            self.assertEqual(resp.status, 200)
            self.assertIsNone(resp.headers.get("X-Adapter-Fallback"))

    def test_all_failed_502_has_no_header(self):
        """全链失败 502 是错误（proxy 走 record_error）不是降级服务 → 不带 header"""

        def fake_forward(url, data, auth_headers, timeout=300):
            raise OSError("everything down (test)")

        self.adapter._forward_request = fake_forward
        with self.assertRaises(HTTPError) as ctx:
            self._post().read()
        self.assertEqual(ctx.exception.code, 502)
        self.assertIsNone(ctx.exception.headers.get("X-Adapter-Fallback"))


# ---------------------------------------------------------------------------
# 3. proxy 侧 header 消费行为（literal-as-guard: 抽 tool_proxy 真源码块 exec）
# ---------------------------------------------------------------------------

class TestProxyHeaderConsumptionBehavior(unittest.TestCase):
    """tool_proxy 的 wiring 块必须真调 record_fallback（真 ProxyStats 行为级）。

    tool_proxy.py 顶层 serve_forever 不可 import（项目惯例 V37.9.132/175/226/228）
    → literal-as-guard: 从源码抽出 wiring 块 exec，drift 时先在提取处 fail。
    """

    def _extract_block(self):
        src = _read("tool_proxy.py")
        # 捕获含首行前导缩进（dedent 需要各行共同前缀才能对齐）
        m = re.search(
            r"\n( +_fb_provider = resp\.headers\.get\(\"X-Adapter-Fallback\", \"\"\)\n"
            r".*?DEGRADED.*?\n)",
            src,
            re.DOTALL,
        )
        self.assertIsNotNone(m, "V37.9.229 wiring 块未在 tool_proxy.py 找到")
        return textwrap.dedent(m.group(1))

    def _run_block(self, headers):
        from proxy_filters import ProxyStats
        import proxy_filters as pf

        ps = ProxyStats()
        tmpdir = tempfile.mkdtemp(prefix="v229_stats_")
        old_stats = pf.STATS_FILE
        pf.STATS_FILE = os.path.join(tmpdir, "proxy_stats.json")
        logs = []

        class _Resp:
            pass

        resp = _Resp()

        class _Headers:
            def __init__(self, d):
                self._d = d

            def get(self, k, default=None):
                return self._d.get(k, default)

        resp.headers = _Headers(headers)
        ns = {
            "resp": resp,
            "proxy_stats": ps,
            "log": logs.append,
            "rid": "testrid",
        }
        try:
            exec(self._extract_block(), ns)
        finally:
            pf.STATS_FILE = old_stats
        return ps, logs

    def test_header_present_records_fallback(self):
        ps, logs = self._run_block({"X-Adapter-Fallback": "doubao_21"})
        self.assertEqual(ps.fallback_count, 1)
        self.assertTrue(any("DEGRADED" in l for l in logs))
        self.assertTrue(any("doubao_21" in l for l in logs))

    def test_header_absent_no_fallback(self):
        ps, logs = self._run_block({})
        self.assertEqual(ps.fallback_count, 0)
        self.assertEqual(logs, [])


# ---------------------------------------------------------------------------
# 4. ProxyStats slo 落盘原始计数（消费方一直在读但从未被写的 4 字段）
# ---------------------------------------------------------------------------

class TestProxyStatsSloRawCounters(unittest.TestCase):
    """_write_stats + get_stats_dict 的 slo dict 必须含 4 个原始计数字段。"""

    def _make_isolated(self):
        import proxy_filters as pf
        from proxy_filters import ProxyStats

        ps = ProxyStats()
        tmpdir = tempfile.mkdtemp(prefix="v229_stats_")
        self._old_stats = pf.STATS_FILE
        self._pf = pf
        pf.STATS_FILE = os.path.join(tmpdir, "proxy_stats.json")
        self.addCleanup(self._restore)
        return ps, pf.STATS_FILE

    def _restore(self):
        self._pf.STATS_FILE = self._old_stats

    def test_get_stats_dict_slo_raw_counters(self):
        ps, _ = self._make_isolated()
        for _ in range(10):
            ps.record_success({}, latency_ms=100)
        ps.record_fallback()
        ps.record_fallback()
        ps.record_tool_call(success=True)
        slo = ps.get_stats_dict()["slo"]
        self.assertEqual(slo["fallback_count"], 2)
        self.assertEqual(slo["tool_calls_success"], 1)
        self.assertIn("recovery_total", slo)
        self.assertIn("failure_streaks", slo)
        self.assertAlmostEqual(slo["degradation_rate_pct"], 20.0)

    def test_write_stats_file_contains_raw_counters(self):
        ps, stats_file = self._make_isolated()
        ps.record_success({}, latency_ms=100)
        ps.record_fallback()
        ps._write_stats()
        with open(stats_file) as f:
            data = json.load(f)
        slo = data["slo"]
        self.assertEqual(slo["fallback_count"], 1)
        self.assertIn("tool_calls_success", slo)
        self.assertIn("recovery_total", slo)
        self.assertIn("failure_streaks", slo)


# ---------------------------------------------------------------------------
# 5. 消费链端到端: ProxyStats → slo_benchmark / slo_dashboard 真值贯通
# ---------------------------------------------------------------------------

class TestConsumerChainSeesFallback(unittest.TestCase):
    """把'消费方现在能看见 fallback'从声明升级为机器可证。"""

    def _stats_with_fallbacks(self):
        import proxy_filters as pf
        from proxy_filters import ProxyStats

        ps = ProxyStats()
        tmpdir = tempfile.mkdtemp(prefix="v229_stats_")
        old = pf.STATS_FILE
        pf.STATS_FILE = os.path.join(tmpdir, "proxy_stats.json")
        try:
            for _ in range(10):
                ps.record_success({}, latency_ms=100)
            ps.record_fallback()
            ps.record_fallback()
            return ps.get_stats_dict()
        finally:
            pf.STATS_FILE = old

    def test_slo_benchmark_degradation_nonzero(self):
        """血案反面: V37.9.220 场景（fallback 发生）下 benchmark 不再报 0"""
        import slo_benchmark

        report = slo_benchmark.build_report(self._stats_with_fallbacks(), {"slo": {}})
        self.assertEqual(report["degradation"]["fallback_count"], 2)
        self.assertAlmostEqual(report["degradation"]["degradation_rate_pct"], 20.0)

    def test_slo_dashboard_snapshot_nonzero(self):
        import slo_dashboard

        snap = slo_dashboard.extract_snapshot(self._stats_with_fallbacks())
        self.assertEqual(snap["fallback_count"], 2)
        self.assertAlmostEqual(snap["degradation_pct"], 20.0)


# ---------------------------------------------------------------------------
# 6. 源码守卫（wiring 在位 + 顺序 + 反回退）
# ---------------------------------------------------------------------------

class TestSourceGuards(unittest.TestCase):
    def test_tool_proxy_reads_header_and_records(self):
        src = _read("tool_proxy.py")
        self.assertIn('resp.headers.get("X-Adapter-Fallback"', src)
        self.assertIn("proxy_stats.record_fallback()", src)

    def test_tool_proxy_wiring_position(self):
        """record_fallback 必须在 Backend log 之后、do_POST parse 块之前
        （请求被 fallback 服务这一事实与后续 parse 成败无关）。
        注: `rj = json.loads(resp_body)` 在 followup LLM 调用处另有一次出现,
        故用带偏移的顺序查找锚定 do_POST 内的真实顺序（找不到会 ValueError）。"""
        src = _read("tool_proxy.py")
        idx_backend = src.index("] Backend:")
        idx_record = src.index("proxy_stats.record_fallback()", idx_backend)
        # record 之后必须还有 do_POST 的 parse（证明 record 在 parse 块之前）
        src.index("rj = json.loads(resp_body)", idx_record)

    def test_adapter_fallback_send_passes_header(self):
        src = _read("adapter.py")
        self.assertIn('"X-Adapter-Fallback": fb["name"]', src)

    def test_adapter_primary_send_no_header(self):
        """primary 成功路径的投递不带 extra_headers（不误标降级）。
        V37.9.231 后投递经 _deliver（client 断开 ≠ backend 失败），形态演进。"""
        src = _read("adapter.py")
        self.assertIn("self._deliver(status, resp_body, tag=tag)", src)

    def test_adapter_send_json_signature(self):
        src = _read("adapter.py")
        self.assertIn("def _send_json(self, status, body, extra_headers=None):", src)

    def test_proxy_filters_both_slo_dicts_carry_raw_counters(self):
        """_write_stats + get_stats_dict 两处 slo dict 都带原始计数（一物一形双写点）"""
        src = _read("proxy_filters.py")
        self.assertEqual(src.count('"fallback_count": self.fallback_count'), 2)
        self.assertEqual(src.count('"tool_calls_success": self.tool_calls_success'), 2)
        self.assertEqual(src.count('"recovery_total": self._recovery_total'), 2)
        self.assertEqual(src.count('"failure_streaks": self._failure_streaks'), 2)

    def test_v37_9_229_markers(self):
        for fname in ("tool_proxy.py", "adapter.py", "proxy_filters.py"):
            self.assertIn("V37.9.229", _read(fname), f"{fname} 缺 V37.9.229 marker")

    def test_header_consumed_not_forwarded(self):
        """proxy 自建响应头（Content-Type/Content-Length），X-Adapter-Fallback
        不出现在 proxy 的 send_header 调用中（消费不下传 gateway）"""
        src = _read("tool_proxy.py")
        self.assertNotIn('send_header("X-Adapter-Fallback"', src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
