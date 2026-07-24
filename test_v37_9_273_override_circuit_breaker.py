#!/usr/bin/env python3
"""V37.9.273 — ?provider= override 请求不污染默认 primary 的断路器 (审计 finding).

审计发现 (控制平面热路径): `_circuit_breaker` 是单进程全局单例, do_POST 对**任何**
服务该请求的 provider (含 ?provider=X override, 如 V37.9.271 GLM chat 路由) 记录
success/failure, 却又用同一断路器 gate 默认 primary → override provider 健康度污染
默认 primary 断路器状态。血案双向:
  (1) 几个失败的 glm 请求 → 断路器开 → 下个 PA 请求跳过健康的默认 primary + 误报 SLO 降级
  (2) 断路器开 (默认 primary 故障) → override 请求被错误跳过 (用户显式选的 provider 没试)

修复 (V37.9.273): is_override = (primary_name != PROVIDER_NAME); override 请求既不读
(cb_open 加 `not is_override`) 也不写 (record_success/failure 加 `if not is_override`)
默认 primary 断路器。

守卫两层:
  1. E2E — 真 ThreadedServer 驱动 do_POST + SpyCB, 用真 ?provider=glm5_coding override
     (ROUTER_ENFORCE=on) 验证断路器行为
  2. 源码守卫 — is_override 定义 + 3 处断路器守卫

反向验证 (sabotage): 移除任一 `not is_override` 守卫 → 对应 E2E 断言 FAIL。
"""
import json
import os
import threading
import unittest
from urllib.request import Request, urlopen

_HERE = os.path.dirname(os.path.abspath(__file__))


class _SpyCB:
    """断路器 spy: 记录 success/failure 调用次数, 默认 closed。"""

    def __init__(self):
        self.successes = 0
        self.failures = 0
        self._open = False

    def is_open(self):
        return self._open

    def record_success(self):
        self.successes += 1

    def record_failure(self):
        self.failures += 1

    def state(self):
        return "open" if self._open else "closed"


class TestOverrideCircuitBreakerE2E(unittest.TestCase):
    """行为级铁证: override 请求不读/不写默认 primary 断路器。"""

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
        cls._saved_env = {k: os.environ.get(k) for k in ("ROUTER_ENFORCE", "GLM5_API_KEY")}
        # override 需要 ROUTER_ENFORCE=on + glm5_coding 有 api_key (dev 无真 key, 注入 test key)
        os.environ["ROUTER_ENFORCE"] = "on"
        os.environ["GLM5_API_KEY"] = "test-override-key"
        cls.override_base = adapter.PROVIDERS["glm5_coding"]["base_url"]  # ark.cn-beijing...
        # 一个 fake fallback (override/default 失败后走它, 隔离断路器 = primary-only 语义)
        cls.fb = {"name": "fb_one", "base_url": "http://fb-one.invalid/v1",
                  "model_id": "fb-model", "auth_style": "bearer", "api_key": "k",
                  "vl_model_id": "", "reasoning_off_body": None}
        adapter.FALLBACK_CHAIN = [cls.fb]
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
        for k, v in cls._saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def setUp(self):
        self.cb = _SpyCB()
        self.adapter._circuit_breaker = self.cb
        self.forward_urls = []

    def tearDown(self):
        self.adapter._forward_request = self._saved["_forward_request"]

    def _post(self, query=""):
        body = json.dumps(
            {"model": self.adapter.REAL_MODEL_ID,
             "messages": [{"role": "user", "content": "hi"}]}
        ).encode()
        req = Request(f"http://127.0.0.1:{self.port}/v1/chat/completions{query}",
                      data=body, method="POST")
        req.add_header("Content-Type", "application/json")
        try:
            with urlopen(req, timeout=15) as resp:
                resp.read()
        except Exception:
            pass  # 502/回写等 client 侧异常是预期 (我们只查断路器状态)

    def _install_forward(self, fail=True):
        ok = json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode()

        def fake_forward(url, data, auth_headers, timeout=300):
            self.forward_urls.append(url)
            if fail:
                raise OSError("provider down (test)")
            return 200, ok
        self.adapter._forward_request = fake_forward

    def test_override_failure_does_not_record_cb_failure(self):
        # 血案 ①: override 请求失败不得给默认 primary 断路器记 failure
        self._install_forward(fail=True)
        self._post(query="?provider=glm5_coding")
        self.assertTrue(any(self.override_base in u for u in self.forward_urls),
                        f"override primary 应被尝试, 实际 urls={self.forward_urls}")
        self.assertEqual(self.cb.failures, 0,
                         "override 失败不得误开默认 primary 断路器")

    def test_override_success_does_not_record_cb_success(self):
        # override 成功不得重置默认 primary 断路器 (掩盖真实 primary 故障)
        self._install_forward(fail=False)
        self._post(query="?provider=glm5_coding")
        self.assertEqual(self.cb.successes, 0,
                         "override 成功不得重置默认 primary 断路器")

    def test_default_request_records_cb_failure_normally(self):
        # 回归: 非 override (默认 primary) 请求失败仍正常记断路器 failure
        self._install_forward(fail=True)
        self._post(query="")  # 无 ?provider= → 默认 primary (qwen)
        self.assertEqual(self.cb.failures, 1,
                         "默认 primary 失败仍须正常记 CB failure")

    def test_default_request_records_cb_success_normally(self):
        # 回归: 默认 primary 成功仍正常记断路器 success
        self._install_forward(fail=False)
        self._post(query="")
        self.assertEqual(self.cb.successes, 1,
                         "默认 primary 成功仍须正常记 CB success")

    def test_open_breaker_does_not_gate_override(self):
        # 血案 ②: 断路器开时 override 请求仍尝试其 primary (不被 cb_open 跳过)
        self.cb._open = True
        self._install_forward(fail=True)
        self._post(query="?provider=glm5_coding")
        self.assertTrue(any(self.override_base in u for u in self.forward_urls),
                        f"断路器开时 override primary 仍应被尝试, 实际={self.forward_urls}")

    def test_open_breaker_gates_default_request(self):
        # 对照: 断路器开时默认请求跳过 primary, 只走 fallback (证 gating 对默认仍有效)
        self.cb._open = True
        self._install_forward(fail=True)
        self._post(query="")
        self.assertFalse(any(self.adapter.TARGET_BASE in u for u in self.forward_urls),
                         f"断路器开时默认 primary 应被跳过, 实际={self.forward_urls}")


class TestSourceGuards(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(os.path.join(_HERE, "adapter.py"), encoding="utf-8") as f:
            cls.src = f.read()

    def test_is_override_defined(self):
        import re
        self.assertRegex(self.src, r"is_override\s*=\s*\(primary_name\s*!=\s*PROVIDER_NAME\)")

    def test_cb_open_excludes_override(self):
        import re
        self.assertRegex(
            self.src,
            r"cb_open\s*=\s*FALLBACK_CHAIN\s+and\s+\(not is_override\)\s+and\s+_circuit_breaker\.is_open\(\)",
        )

    def test_record_success_guarded(self):
        import re
        self.assertRegex(
            self.src, r"if not is_override:\s*.*\n\s*_circuit_breaker\.record_success\(\)"
        )

    def test_record_failure_guarded(self):
        import re
        self.assertRegex(
            self.src, r"if not is_override:\s*.*\n\s*_circuit_breaker\.record_failure\(\)"
        )

    def test_marker_present(self):
        self.assertIn("V37.9.273", self.src)


if __name__ == "__main__":
    unittest.main()
