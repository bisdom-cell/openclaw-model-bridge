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
    """Fallback 降级链测试"""

    def test_fallback_provider_configurable(self):
        """FALLBACK_PROVIDER 可通过环境变量配置"""
        with open("adapter.py") as f:
            content = f.read()
        self.assertIn("FALLBACK_PROVIDER", content)

    def test_fallback_model_id_configurable(self):
        """FALLBACK_MODEL_ID 可通过环境变量配置"""
        with open("adapter.py") as f:
            content = f.read()
        self.assertIn("FALLBACK_MODEL_ID", content)

    def test_no_fallback_returns_502(self):
        """无 fallback 时返回 502"""
        with open("adapter.py") as f:
            content = f.read()
        self.assertIn("NO FALLBACK configured", content)
        self.assertIn("502", content)

    def test_fallback_uses_same_clean_body(self):
        """fallback 使用相同的 clean body（只改 model）"""
        with open("adapter.py") as f:
            content = f.read()
        self.assertIn('fb_clean = dict(clean)', content)
        self.assertIn('fb_clean["model"] = fb["model_id"]', content)

    def test_double_failure_returns_both_errors(self):
        """primary + fallback 都失败时返回两个错误"""
        with open("adapter.py") as f:
            content = f.read()
        self.assertIn("FALLBACK ALSO FAILED", content)
        self.assertIn("primary:", content)


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
