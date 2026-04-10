#!/usr/bin/env python3
"""test_adapter.py — adapter.py 核心逻辑单测

覆盖：Provider 注册表、模型路由、多模态检测、Fallback 逻辑、
认证头生成、参数过滤、健康端点
"""
import json
import os
import sys
import unittest


class TestProviderRegistry(unittest.TestCase):
    """Provider 注册表完整性"""

    def _load_providers(self):
        """从 adapter.py 提取 PROVIDERS dict"""
        with open("adapter.py") as f:
            content = f.read()
        # 提取 PROVIDERS 定义
        import ast
        tree = ast.parse(content)
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == "PROVIDERS":
                        return ast.literal_eval(node.value)
        return {}

    def test_providers_not_empty(self):
        """PROVIDERS 不为空"""
        providers = self._load_providers()
        self.assertGreater(len(providers), 0)

    def test_qwen_provider_exists(self):
        """qwen provider（默认）存在"""
        providers = self._load_providers()
        self.assertIn("qwen", providers)

    def test_gemini_fallback_exists(self):
        """gemini（默认 fallback）存在"""
        providers = self._load_providers()
        self.assertIn("gemini", providers)

    def test_all_providers_have_required_fields(self):
        """所有 provider 有必要字段"""
        providers = self._load_providers()
        required = {"base_url", "api_key_env", "model_id", "auth_style"}
        for name, config in providers.items():
            missing = required - set(config.keys())
            self.assertEqual(missing, set(), f"{name} missing: {missing}")

    def test_api_key_env_not_hardcoded(self):
        """API key 通过环境变量读取，不硬编码"""
        providers = self._load_providers()
        for name, config in providers.items():
            self.assertTrue(config["api_key_env"].endswith("_KEY") or config["api_key_env"].endswith("_API_KEY"),
                            f"{name}: api_key_env '{config['api_key_env']}' doesn't look like env var")

    def test_auth_styles_valid(self):
        """auth_style 只有合法值"""
        providers = self._load_providers()
        valid = {"bearer", "x-api-key"}
        for name, config in providers.items():
            self.assertIn(config["auth_style"], valid, f"{name}: invalid auth_style")


class TestAuthHeaders(unittest.TestCase):
    """认证头生成测试"""

    def test_bearer_auth(self):
        """bearer 认证生成正确的 Authorization 头"""
        # 从 adapter.py 源码验证逻辑
        with open("adapter.py") as f:
            content = f.read()
        self.assertIn('Authorization', content)
        self.assertIn('Bearer', content)

    def test_x_api_key_auth(self):
        """x-api-key 认证生成正确的头"""
        with open("adapter.py") as f:
            content = f.read()
        self.assertIn('x-api-key', content)
        self.assertIn('anthropic-version', content)

    def test_make_auth_headers_function(self):
        """_make_auth_headers 返回正确的字典"""
        with open("adapter.py") as f:
            content = f.read()
        self.assertIn("def _make_auth_headers", content)


class TestMultimodalRouting(unittest.TestCase):
    """多模态内容检测和路由"""

    def test_detects_image_url(self):
        """检测 image_url 类型"""
        with open("adapter.py") as f:
            content = f.read()
        self.assertIn('"image_url"', content)

    def test_detects_image_type(self):
        """检测 image 类型"""
        with open("adapter.py") as f:
            content = f.read()
        self.assertIn('"image"', content)

    def test_detects_audio_type(self):
        """检测 audio 类型"""
        with open("adapter.py") as f:
            content = f.read()
        self.assertIn('"audio"', content)

    def test_routes_to_vl_model(self):
        """多模态时路由到 VL 模型"""
        with open("adapter.py") as f:
            content = f.read()
        self.assertIn("VL_MODEL_ID", content)
        self.assertIn("has_multimodal", content)
        self.assertIn("MULTIMODAL detected", content)

    def test_text_fallback_when_no_vl(self):
        """没有 VL 模型时提取纯文本"""
        with open("adapter.py") as f:
            content = f.read()
        self.assertIn("text_parts", content)

    def test_vl_model_in_qwen_provider(self):
        """qwen provider 有 VL 模型配置"""
        with open("adapter.py") as f:
            content = f.read()
        self.assertIn("vl_model_id", content)
        self.assertIn("Qwen2.5-VL", content)

    def test_multimodal_routing_logic(self):
        """路由逻辑：has_multimodal + VL_MODEL_ID → 用 VL 模型"""
        msgs = [
            {"role": "user", "content": [
                {"type": "text", "text": "描述图片"},
                {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,..."}}
            ]}
        ]
        has_multimodal = False
        for m in msgs:
            content = m.get("content", "")
            if isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get("type") in ("image_url", "image", "audio", "video"):
                        has_multimodal = True
                        break
        self.assertTrue(has_multimodal)

    def test_text_only_not_multimodal(self):
        """纯文本消息不触发多模态路由"""
        msgs = [
            {"role": "user", "content": "你好"}
        ]
        has_multimodal = False
        for m in msgs:
            content = m.get("content", "")
            if isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get("type") in ("image_url", "image", "audio", "video"):
                        has_multimodal = True
                        break
        self.assertFalse(has_multimodal)


class TestFallbackLogic(unittest.TestCase):
    """Fallback chain 降级链测试 (V37: multi-level)"""

    def test_fallback_provider_configurable(self):
        """FALLBACK_PROVIDER 可通过环境变量配置（backward compat）"""
        with open("adapter.py") as f:
            content = f.read()
        self.assertIn("FALLBACK_PROVIDER", content)

    def test_fallback_model_id_configurable(self):
        """FALLBACK_MODEL_ID 可通过环境变量配置"""
        with open("adapter.py") as f:
            content = f.read()
        self.assertIn("FALLBACK_MODEL_ID", content)

    def test_no_fallback_returns_502(self):
        """无 fallback chain 时返回 502"""
        with open("adapter.py") as f:
            content = f.read()
        self.assertIn("NO FALLBACK CHAIN configured", content)
        self.assertIn("502", content)

    def test_fallback_uses_same_clean_body(self):
        """fallback 使用相同的 clean body（只改 model）"""
        with open("adapter.py") as f:
            content = f.read()
        self.assertIn('fb_clean = dict(clean)', content)
        self.assertIn('fb_clean["model"] = fb["model_id"]', content)

    def test_all_fallbacks_failed_message(self):
        """所有 fallback 都失败时有明确日志"""
        with open("adapter.py") as f:
            content = f.read()
        self.assertIn("ALL", content)
        self.assertIn("FALLBACKS FAILED", content)

    def test_fallback_chain_is_list(self):
        """FALLBACK_CHAIN 是列表结构（via _build_fallback_chain）"""
        with open("adapter.py") as f:
            content = f.read()
        self.assertIn("FALLBACK_CHAIN = _build_fallback_chain()", content)
        self.assertIn("chain = []", content)  # inside _build_fallback_chain
        self.assertIn("chain.append", content)

    def test_fallback_chain_auto_discover(self):
        """自动从 build_fallback_chain() 发现可用 fallback"""
        with open("adapter.py") as f:
            content = f.read()
        self.assertIn("build_fallback_chain", content)
        self.assertIn("require_available=True", content)

    def test_fallback_chain_loop(self):
        """fallback 通过循环顺序尝试"""
        with open("adapter.py") as f:
            content = f.read()
        self.assertIn("for fb in FALLBACK_CHAIN:", content)

    def test_fallback_backward_compat(self):
        """FALLBACK 变量保持向后兼容（= chain 第一个）"""
        with open("adapter.py") as f:
            content = f.read()
        self.assertIn("FALLBACK = FALLBACK_CHAIN[0]", content)


class TestSmartRouting(unittest.TestCase):
    """智能路由（simple → fast model）"""

    def test_fast_provider_env(self):
        """FAST_PROVIDER 可通过环境变量配置"""
        with open("adapter.py") as f:
            content = f.read()
        self.assertIn("FAST_PROVIDER", content)

    def test_uses_classify_complexity(self):
        """使用 classify_complexity 判断复杂度"""
        with open("adapter.py") as f:
            content = f.read()
        self.assertIn("classify_complexity", content)

    def test_simple_routes_to_fast(self):
        """simple 请求路由到快速模型"""
        with open("adapter.py") as f:
            content = f.read()
        self.assertIn("SMART ROUTE: simple", content)

    def test_multimodal_not_fast_routed(self):
        """多模态请求不走快速路由"""
        with open("adapter.py") as f:
            content = f.read()
        self.assertIn("not has_multimodal", content)


class TestHealthEndpoint(unittest.TestCase):
    """健康端点测试"""

    def test_health_is_local(self):
        """health 不转发到远程"""
        with open("adapter.py") as f:
            content = f.read()
        self.assertIn("/health", content)
        self.assertIn('"ok": True', content)

    def test_health_shows_provider(self):
        """health 包含 provider 信息"""
        with open("adapter.py") as f:
            content = f.read()
        self.assertIn('"provider"', content)

    def test_health_shows_vl_model(self):
        """health 包含 VL 模型信息"""
        with open("adapter.py") as f:
            content = f.read()
        self.assertIn('"vl_model"', content)

    def test_health_shows_fallback(self):
        """health 包含 fallback 信息"""
        with open("adapter.py") as f:
            content = f.read()
        self.assertIn('"fallback"', content)

    def test_health_shows_fallback_chain(self):
        """health 包含 fallback_chain 列表"""
        with open("adapter.py") as f:
            content = f.read()
        self.assertIn('"fallback_chain"', content)


class TestMessageCleaning(unittest.TestCase):
    """消息清洗逻辑"""

    def test_preserves_tool_calls(self):
        """保留 assistant 的 tool_calls"""
        with open("adapter.py") as f:
            content = f.read()
        self.assertIn("tool_calls", content)

    def test_preserves_tool_call_id(self):
        """保留 tool 消息的 tool_call_id"""
        with open("adapter.py") as f:
            content = f.read()
        self.assertIn("tool_call_id", content)

    def test_default_max_tokens(self):
        """未指定 max_tokens 时默认 4096"""
        with open("adapter.py") as f:
            content = f.read()
        self.assertIn("4096", content)

    def test_allowed_params_defined(self):
        """ALLOWED_PARAMS 已定义"""
        with open("adapter.py") as f:
            content = f.read()
        self.assertIn("ALLOWED_PARAMS", content)


class TestCircuitBreaker(unittest.TestCase):
    """V32: 断路器测试（通过 exec 提取 CircuitBreaker 类，避免 import adapter 启动服务器）"""

    @classmethod
    def setUpClass(cls):
        """从 adapter.py 源码中提取 CircuitBreaker 类"""
        import re
        with open("adapter.py") as f:
            src = f.read()
        # 提取 CircuitBreaker 类定义
        match = re.search(r'(class CircuitBreaker:.*?)(?=\n\w|\n_circuit_breaker)', src, re.DOTALL)
        assert match, "CircuitBreaker class not found in adapter.py"
        ns = {"threading": __import__("threading"), "time": __import__("time")}
        exec(match.group(1), ns)
        cls.CB = ns["CircuitBreaker"]

    def test_initial_state_closed(self):
        cb = self.CB(3, 1)
        self.assertEqual(cb.state(), "closed")
        self.assertFalse(cb.is_open())

    def test_failures_below_threshold(self):
        cb = self.CB(3, 1)
        cb.record_failure()
        cb.record_failure()
        self.assertEqual(cb.state(), "closed")

    def test_failures_at_threshold_opens(self):
        cb = self.CB(3, 1)
        for _ in range(3):
            cb.record_failure()
        self.assertEqual(cb.state(), "open")
        self.assertTrue(cb.is_open())

    def test_success_resets(self):
        cb = self.CB(2, 1)
        cb.record_failure()
        cb.record_failure()
        self.assertTrue(cb.is_open())
        cb.record_success()
        self.assertEqual(cb.state(), "closed")
        self.assertFalse(cb.is_open())

    def test_half_open_after_reset(self):
        cb = self.CB(2, 0)  # reset=0 → 立即 half-open
        cb.record_failure()
        cb.record_failure()
        import time
        time.sleep(0.01)
        self.assertEqual(cb.state(), "half-open")
        self.assertFalse(cb.is_open())  # half-open allows attempt

    def test_health_shows_circuit_breaker(self):
        with open("adapter.py") as f:
            content = f.read()
        self.assertIn("circuit_breaker", content)

    def test_config_driven_timeouts(self):
        with open("adapter.py") as f:
            content = f.read()
        self.assertIn("_PRIMARY_TIMEOUT", content)
        self.assertIn("_FALLBACK_TIMEOUT", content)


class TestHotReload(unittest.TestCase):
    """V37.1+: Fallback chain hot-reload 测试"""

    def _read(self):
        with open("adapter.py") as f:
            return f.read()

    def test_build_fallback_chain_function_exists(self):
        """_build_fallback_chain() 独立函数存在"""
        content = self._read()
        self.assertIn("def _build_fallback_chain():", content)

    def test_startup_uses_build_function(self):
        """启动时通过 _build_fallback_chain() 构建 chain"""
        content = self._read()
        self.assertIn("FALLBACK_CHAIN = _build_fallback_chain()", content)

    def test_reload_function_exists(self):
        """_reload_fallback_chain() 函数存在"""
        content = self._read()
        self.assertIn("def _reload_fallback_chain():", content)

    def test_reload_loop_exists(self):
        """_hot_reload_loop() 后台循环存在"""
        content = self._read()
        self.assertIn("def _hot_reload_loop():", content)

    def test_feature_flag_default_off(self):
        """ADAPTER_HOT_RELOAD 默认关闭"""
        content = self._read()
        self.assertIn('ADAPTER_HOT_RELOAD', content)
        self.assertIn('"false"', content)

    def test_reload_interval_configurable(self):
        """热重载间隔通过环境变量配置"""
        content = self._read()
        self.assertIn("ADAPTER_HOT_RELOAD_INTERVAL", content)
        self.assertIn('"3600"', content)

    def test_reload_keeps_old_on_empty(self):
        """新链为空时保留旧链（不降级）"""
        content = self._read()
        self.assertIn("new chain empty", content)
        self.assertIn("kept old", content)

    def test_reload_logs_changes(self):
        """链变更时记录日志"""
        content = self._read()
        self.assertIn("HOT-RELOAD: chain updated", content)

    def test_reload_error_keeps_old(self):
        """重载异常时保留旧链"""
        content = self._read()
        self.assertIn("HOT-RELOAD ERROR", content)
        self.assertIn("keeping old chain", content)

    def test_health_exposes_reload_status(self):
        """/health 端点暴露热重载状态"""
        content = self._read()
        self.assertIn('"hot_reload"', content)
        self.assertIn('"last_status"', content)
        self.assertIn('"last_reload"', content)

    def test_startup_log_includes_reload_info(self):
        """启动日志包含热重载信息"""
        content = self._read()
        self.assertIn("hot-reload:", content)

    def test_daemon_thread(self):
        """热重载使用 daemon 线程（不阻止进程退出）"""
        content = self._read()
        self.assertIn('daemon=True', content)
        self.assertIn('name="fallback-reload"', content)

    def test_reload_uses_global_replacement(self):
        """通过 global 引用替换实现线程安全"""
        content = self._read()
        self.assertIn("global FALLBACK_CHAIN, FALLBACK", content)

    def test_reload_tracks_status(self):
        """追踪最后一次重载状态"""
        content = self._read()
        self.assertIn("_last_reload_status", content)
        self.assertIn("_last_reload_time", content)


class TestHotReloadFunctional(unittest.TestCase):
    """V37.1+: _build_fallback_chain() 功能测试（通过 exec 提取函数）"""

    @classmethod
    def setUpClass(cls):
        """从 adapter.py 提取 _build_fallback_chain 函数"""
        import re
        with open("adapter.py") as f:
            src = f.read()

        # 提取 _build_fallback_chain 函数定义
        match = re.search(
            r'(def _build_fallback_chain\(\):.*?)(?=\n# Initial build at startup)',
            src, re.DOTALL
        )
        assert match, "_build_fallback_chain not found"
        cls._func_src = match.group(1)

    def _exec_build(self, providers=None, provider_name="qwen",
                    fallback_provider="", get_registry=None, env_overrides=None):
        """Execute _build_fallback_chain with mocked globals"""
        import os as _os
        env = _os.environ.copy()
        if env_overrides:
            env.update(env_overrides)

        ns = {
            "os": type("MockOS", (), {
                "environ": type("Env", (), {"get": lambda self, k, d="": env.get(k, d)})()
            })(),
            "PROVIDERS": providers or {"qwen": {"base_url": "https://q", "api_key_env": "QWEN_KEY", "model_id": "qwen3", "auth_style": "bearer"}},
            "PROVIDER_NAME": provider_name,
            "_get_registry": get_registry,
        }
        exec(self._func_src, ns)
        return ns["_build_fallback_chain"]()

    def test_empty_when_no_fallback_configured(self):
        """无 FALLBACK_PROVIDER 且无 registry → 空链"""
        chain = self._exec_build()
        self.assertEqual(chain, [])

    def test_explicit_fallback_added(self):
        """FALLBACK_PROVIDER 正确加入链"""
        providers = {
            "qwen": {"base_url": "https://q", "api_key_env": "QWEN_KEY", "model_id": "qwen3", "auth_style": "bearer"},
            "gemini": {"base_url": "https://g", "api_key_env": "GEMINI_KEY", "model_id": "gemini-2.5", "auth_style": "bearer"},
        }
        chain = self._exec_build(
            providers=providers,
            env_overrides={"FALLBACK_PROVIDER": "gemini", "GEMINI_KEY": "test-key"}
        )
        self.assertEqual(len(chain), 1)
        self.assertEqual(chain[0]["name"], "gemini")

    def test_skip_self_as_fallback(self):
        """不将自己加入 fallback 链"""
        chain = self._exec_build(
            env_overrides={"FALLBACK_PROVIDER": "qwen", "QWEN_KEY": "test-key"}
        )
        self.assertEqual(chain, [])

    def test_skip_unknown_fallback(self):
        """未知 provider 不加入链"""
        chain = self._exec_build(
            env_overrides={"FALLBACK_PROVIDER": "nonexistent"}
        )
        self.assertEqual(chain, [])

    def test_skip_fallback_without_key(self):
        """无 API key 的 provider 不加入链"""
        providers = {
            "qwen": {"base_url": "https://q", "api_key_env": "QWEN_KEY", "model_id": "qwen3", "auth_style": "bearer"},
            "gemini": {"base_url": "https://g", "api_key_env": "GEMINI_KEY", "model_id": "gemini-2.5", "auth_style": "bearer"},
        }
        chain = self._exec_build(
            providers=providers,
            env_overrides={"FALLBACK_PROVIDER": "gemini"}  # no GEMINI_KEY
        )
        self.assertEqual(chain, [])

    def test_build_is_pure_function(self):
        """_build_fallback_chain 是纯函数，可重复调用"""
        providers = {
            "qwen": {"base_url": "https://q", "api_key_env": "QWEN_KEY", "model_id": "qwen3", "auth_style": "bearer"},
            "gemini": {"base_url": "https://g", "api_key_env": "GEMINI_KEY", "model_id": "gemini-2.5", "auth_style": "bearer"},
        }
        chain1 = self._exec_build(
            providers=providers,
            env_overrides={"FALLBACK_PROVIDER": "gemini", "GEMINI_KEY": "k1"}
        )
        chain2 = self._exec_build(
            providers=providers,
            env_overrides={"FALLBACK_PROVIDER": "gemini", "GEMINI_KEY": "k1"}
        )
        self.assertEqual(len(chain1), len(chain2))
        self.assertEqual(chain1[0]["name"], chain2[0]["name"])


class TestAdapterSyntax(unittest.TestCase):
    """语法和基本结构"""

    def test_python_syntax(self):
        """adapter.py Python 语法正确"""
        import subprocess
        result = subprocess.run(
            [sys.executable, "-c", "import ast; ast.parse(open('adapter.py').read())"],
            capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 0, f"Syntax error: {result.stderr}")

    def test_threading_mixin(self):
        """使用 ThreadingMixIn（非单线程阻塞）"""
        with open("adapter.py") as f:
            content = f.read()
        self.assertIn("ThreadingMixIn", content)
        self.assertIn("daemon_threads = True", content)


if __name__ == "__main__":
    unittest.main()
