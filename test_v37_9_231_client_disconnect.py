#!/usr/bin/env python3
"""V37.9.231 审计 finding E 守卫 — client 断开 ≠ backend 失败。

血案背景（V37.9.220 adapter.log 实录 + 2026-07-02 审计 finding E, LOW-MED 热路径）:
    批量 job 的 HTTP client 超时先断开 → adapter 在 primary/fallback 上游成功后
    回写响应撞 BrokenPipeError。此前回写在 _forward_request 的 try 内:
    ① 健康 backend 被记 _circuit_breaker.record_failure()（误开断路器）
    ② client 断开被 fallback except 当 fallback 失败 → 继续对死 socket 试下一个
       fallback（每个都"FALLBACK OK ... 后 1ms 内 FAILED Broken pipe"）→ 链耗尽,
       浪费 ~500s tail + 上游配额。

修复（V37.9.231）: `_deliver()` 投递 helper — 回写单独兜 OSError（client 断开只
记 CLIENT GONE 日志绝不冒泡），四个回写点（primary / no-chain 502 / fallback /
all-failed 502）收编，投递失败与 backend 健康度、fallback 路由彻底解耦。
"""

import json
import os
import sys
import threading
import unittest
from urllib.request import Request, urlopen

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

REPO = os.path.dirname(os.path.abspath(__file__))


def _read(fname):
    with open(os.path.join(REPO, fname), encoding="utf-8") as f:
        return f.read()


# ---------------------------------------------------------------------------
# 1. _deliver helper 单元行为（handler 无 socket 实例化）
# ---------------------------------------------------------------------------

class TestDeliverHelper(unittest.TestCase):
    def _make_handler(self, write_exc=None, response_exc=None):
        import adapter
        h = adapter.ProxyHandler.__new__(adapter.ProxyHandler)
        sent = []

        def _send_response(s):
            if response_exc:
                raise response_exc
            sent.append(("__status__", s))

        h.send_response = _send_response
        h.send_header = lambda k, v: sent.append((k, v))
        h.end_headers = lambda: sent.append(("__end__", None))

        class _W:
            def __init__(self):
                self.data = b""

            def write(self, b):
                if write_exc:
                    raise write_exc
                self.data += b

        h.wfile = _W()
        return h, sent

    def test_delivered_returns_true(self):
        h, sent = self._make_handler()
        self.assertTrue(h._deliver(200, b"{}"))
        self.assertEqual(h.wfile.data, b"{}")

    def test_broken_pipe_returns_false_no_raise(self):
        """血案核心: client 断开 (BrokenPipeError) → False, 不冒泡"""
        h, _ = self._make_handler(write_exc=BrokenPipeError("sim client gone"))
        self.assertFalse(h._deliver(200, b"{}"))

    def test_connection_reset_returns_false(self):
        h, _ = self._make_handler(write_exc=ConnectionResetError("sim RST"))
        self.assertFalse(h._deliver(200, b"{}"))

    def test_non_oserror_propagates(self):
        """编程错误 (非 OSError) 不被吞 — except 范围严格 OSError"""
        h, _ = self._make_handler(response_exc=ValueError("bug"))
        with self.assertRaises(ValueError):
            h._deliver(200, b"{}")

    def test_extra_headers_passthrough(self):
        h, sent = self._make_handler()
        self.assertTrue(h._deliver(200, b"{}",
                                   extra_headers={"X-Adapter-Fallback": "fb"}))
        self.assertIn(("X-Adapter-Fallback", "fb"), sent)


# ---------------------------------------------------------------------------
# 2. E2E — 真 ThreadedServer 驱动完整 do_POST，client 断开时的路由/CB 行为
# ---------------------------------------------------------------------------

class _SpyCB:
    """断路器 spy: 记录 success/failure 调用次数, 永远 closed。"""

    def __init__(self):
        self.successes = 0
        self.failures = 0

    def is_open(self):
        return False

    def record_success(self):
        self.successes += 1

    def record_failure(self):
        self.failures += 1

    def state(self):
        return "closed"


def _raise_broken_pipe(self, status, body, extra_headers=None):
    """模拟 client 已断开: 任何回写立即 BrokenPipeError（_deliver 的被测对象是
    调用点错误处理策略, 非 _send_json 本身）。"""
    raise BrokenPipeError("simulated client disconnect")


class TestClientGoneE2E(unittest.TestCase):
    """行为级铁证: client 断开时 primary 不触发 fallback、不记 CB failure；
    fallback 上游成功 + 回写失败时链停止（不再对死 socket 试下一个）。"""

    @classmethod
    def setUpClass(cls):
        import adapter
        cls.adapter = adapter
        cls._saved = {
            "FALLBACK_CHAIN": adapter.FALLBACK_CHAIN,
            "FAST_ROUTE": adapter.FAST_ROUTE,
            "_forward_request": adapter._forward_request,
            "_circuit_breaker": adapter._circuit_breaker,
        }
        cls._saved_send_json = adapter.ProxyHandler._send_json

        def _fb(name, base):
            return {"name": name, "base_url": base, "model_id": f"{name}-model",
                    "auth_style": "bearer", "api_key": "k",
                    "vl_model_id": "", "reasoning_off_body": None}

        cls.fb1 = _fb("fb_one", "http://fb-one.invalid/v1")
        cls.fb2 = _fb("fb_two", "http://fb-two.invalid/v1")
        adapter.FALLBACK_CHAIN = [cls.fb1, cls.fb2]
        adapter.FAST_ROUTE = None
        cls.srv = adapter.ThreadedServer(("127.0.0.1", 0), adapter.ProxyHandler)
        cls.port = cls.srv.server_address[1]
        cls.thread = threading.Thread(target=cls.srv.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        cls.adapter.ProxyHandler._send_json = cls._saved_send_json
        for k, v in cls._saved.items():
            setattr(cls.adapter, k, v)

    def setUp(self):
        self.cb = _SpyCB()
        self.adapter._circuit_breaker = self.cb
        self.forward_urls = []
        # client 断开模拟: 所有回写 BrokenPipe
        self.adapter.ProxyHandler._send_json = _raise_broken_pipe

    def tearDown(self):
        self.adapter.ProxyHandler._send_json = self._saved_send_json

    def _post(self):
        body = json.dumps(
            {"model": "test-model", "messages": [{"role": "user", "content": "hi"}]}
        ).encode()
        req = Request(f"http://127.0.0.1:{self.port}/v1/chat/completions",
                      data=body, method="POST")
        req.add_header("Content-Type", "application/json")
        try:
            with urlopen(req, timeout=15) as resp:
                resp.read()
        except Exception:
            pass  # server 无法回写（模拟 client gone），client 侧异常是预期

    def test_primary_success_client_gone_no_fallback_no_cb_failure(self):
        """血案 defect ①+②: primary 上游成功 + client 断开 → 不跑 fallback、
        健康 backend 不被记 CB failure"""
        ok = json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode()

        def fake_forward(url, data, auth_headers, timeout=300):
            self.forward_urls.append(url)
            return 200, ok

        self.adapter._forward_request = fake_forward
        self._post()
        self.assertEqual(len(self.forward_urls), 1,
                         f"client 断开绝不触发 fallback, 实际上游调用={self.forward_urls}")
        self.assertNotIn("fb-one.invalid", str(self.forward_urls))
        self.assertEqual(self.cb.failures, 0,
                         "client 断开不得给健康 backend 记 CB failure（误开断路器）")
        self.assertEqual(self.cb.successes, 1)

    def test_fallback_success_client_gone_stops_chain(self):
        """血案 defect ②: fb1 上游成功 + client 断开 → 链停止, fb2 绝不被调
        (修复前: 继续对死 socket 试完整条链, V37.9.220 浪费 ~500s)"""
        ok = json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode()

        def fake_forward(url, data, auth_headers, timeout=300):
            self.forward_urls.append(url)
            if "fb-one.invalid" in url:
                return 200, ok
            raise OSError("primary down (test)")

        self.adapter._forward_request = fake_forward
        self._post()
        self.assertEqual(len(self.forward_urls), 2,
                         f"应只调 primary+fb1, 实际={self.forward_urls}")
        self.assertNotIn("fb-two.invalid", str(self.forward_urls),
                         "client 断开后绝不再试下一个 fallback（对死 socket 纯浪费）")
        self.assertEqual(self.cb.failures, 1, "primary 真失败仍正常记 CB failure")


# ---------------------------------------------------------------------------
# 3. 源码守卫
# ---------------------------------------------------------------------------

class TestSourceGuards(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.src = _read("adapter.py")

    def test_deliver_helper_present_oserror_scoped(self):
        self.assertIn("def _deliver(self, status, body, extra_headers=None, tag=\"\"):", self.src)
        idx = self.src.index("def _deliver(")
        body = self.src[idx:idx + 1600]
        self.assertIn("except OSError", body)
        self.assertNotIn("except Exception", body,
                         "_deliver 只兜 OSError（写 socket 失败族），不得吞编程错误")

    def test_client_gone_log_present(self):
        self.assertIn("CLIENT GONE", self.src)

    def test_all_four_delivery_sites_use_deliver(self):
        """primary / no-chain 502 / fallback / all-failed 502 四个回写点全收编"""
        self.assertEqual(self.src.count("self._deliver("), 4,
                         "chat 路径应恰有 4 个 _deliver 回写点")

    def test_raw_send_json_only_inside_deliver(self):
        """do_POST 内裸 _send_json 调用退役 — 唯一调用点在 _deliver 内部
        (投递错误策略一物一形)"""
        self.assertEqual(self.src.count("self._send_json("), 1,
                         "self._send_json 应只剩 _deliver 内部一处调用")

    def test_fallback_deliver_preserves_header(self):
        """V37.9.229 X-Adapter-Fallback header 在 _deliver 收编后必须保留"""
        idx = self.src.index("FALLBACK OK:")
        seg = self.src[idx:idx + 1200]
        self.assertIn('extra_headers={"X-Adapter-Fallback": fb["name"]}', seg)
        self.assertIn("self._deliver(fb_status, fb_body", seg)

    def test_record_success_before_deliver(self):
        """CB 语义: record_success 在投递之前（backend 健康度只看 backend 调用）"""
        idx_ok = self.src.index("_circuit_breaker.record_success()")
        idx_deliver = self.src.index("self._deliver(status, resp_body", idx_ok)
        self.assertGreater(idx_deliver, idx_ok)

    def test_v37_9_231_marker(self):
        self.assertIn("V37.9.231", self.src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
