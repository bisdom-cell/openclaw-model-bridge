#!/usr/bin/env python3
"""test_providers.py — Provider Compatibility Layer 单测 (V34)

覆盖：BaseProvider / ProviderCapabilities / ProviderRegistry /
      向后兼容 / 兼容性矩阵 / 模型查找 / 认证头生成 / 自定义 Provider
"""
import json
import os
import sys
import unittest
from unittest import mock


class TestProviderCapabilities(unittest.TestCase):
    """能力声明测试"""

    def test_supported_modalities_text_only(self):
        from providers import ProviderCapabilities
        caps = ProviderCapabilities(text=True, vision=False)
        self.assertEqual(caps.supported_modalities(), ["text"])

    def test_supported_modalities_multimodal(self):
        from providers import ProviderCapabilities
        caps = ProviderCapabilities(text=True, vision=True, audio=True, video=False)
        self.assertEqual(caps.supported_modalities(), ["text", "vision", "audio"])

    def test_verified_features_empty(self):
        from providers import ProviderCapabilities
        caps = ProviderCapabilities()
        self.assertEqual(caps.verified_features(), [])

    def test_verified_features_partial(self):
        from providers import ProviderCapabilities
        caps = ProviderCapabilities(verified_text=True, verified_fallback=True)
        self.assertEqual(caps.verified_features(), ["text", "fallback"])


class TestVerificationTier(unittest.TestCase):
    """V37.9.146 验证档位字段化 — tier 字段 + tier↔verified_* 一致性不变式。"""

    def test_tier_constants_and_order(self):
        from providers import (
            TIER_DECLARED, TIER_SMOKE_TESTED, TIER_FEATURE_VERIFIED,
            TIER_PRODUCTION_OBSERVED, VERIFICATION_TIERS)
        self.assertEqual(TIER_DECLARED, "declared")
        self.assertEqual(TIER_SMOKE_TESTED, "smoke_tested")
        self.assertEqual(TIER_FEATURE_VERIFIED, "feature_verified")
        self.assertEqual(TIER_PRODUCTION_OBSERVED, "production_observed")
        # 顺序 = 递增严格度 (declared 最弱 → production_observed 最强)
        self.assertEqual(VERIFICATION_TIERS, (
            "declared", "smoke_tested", "feature_verified", "production_observed"))

    def test_default_tier_is_declared(self):
        from providers import ProviderCapabilities, TIER_DECLARED
        caps = ProviderCapabilities()
        self.assertEqual(caps.verification_tier, TIER_DECLARED)
        self.assertEqual(caps.tier_note, "")
        self.assertEqual(caps.tier_evidence, "")

    # --- 一致性不变式: declared 档位 ---
    def test_declared_default_consistent(self):
        from providers import ProviderCapabilities
        caps = ProviderCapabilities()  # declared + 0 verified + 无 evidence
        self.assertEqual(caps.tier_consistency_violations(), [])

    def test_declared_with_verified_feature_violation(self):
        from providers import ProviderCapabilities, TIER_DECLARED
        caps = ProviderCapabilities(
            verified_text=True, verification_tier=TIER_DECLARED)
        violations = caps.tier_consistency_violations()
        self.assertTrue(violations)
        self.assertTrue(any("declared 但有" in v for v in violations))

    def test_declared_with_handwritten_evidence_violation(self):
        from providers import ProviderCapabilities, TIER_DECLARED
        caps = ProviderCapabilities(
            verification_tier=TIER_DECLARED, tier_evidence="不该手写的依据")
        violations = caps.tier_consistency_violations()
        self.assertTrue(any("不应手写 tier_evidence" in v for v in violations))

    # --- 一致性不变式: production_observed 档位 ---
    def test_production_observed_consistent(self):
        from providers import ProviderCapabilities, TIER_PRODUCTION_OBSERVED
        caps = ProviderCapabilities(
            verified_text=True, verified_fallback=True,
            verification_tier=TIER_PRODUCTION_OBSERVED, tier_evidence="生产真跑过")
        self.assertEqual(caps.tier_consistency_violations(), [])

    def test_production_observed_zero_verified_violation(self):
        from providers import ProviderCapabilities, TIER_PRODUCTION_OBSERVED
        caps = ProviderCapabilities(
            verification_tier=TIER_PRODUCTION_OBSERVED, tier_evidence="x")
        violations = caps.tier_consistency_violations()
        self.assertTrue(any("0 个 verified feature" in v for v in violations))

    def test_non_declared_requires_evidence(self):
        from providers import ProviderCapabilities, TIER_PRODUCTION_OBSERVED
        caps = ProviderCapabilities(
            verified_text=True, verification_tier=TIER_PRODUCTION_OBSERVED)
        violations = caps.tier_consistency_violations()
        self.assertTrue(any("需显式 tier_evidence" in v for v in violations))

    # --- 一致性不变式: smoke_tested 档位 (暂无占用, 规则已定义) ---
    def test_smoke_tested_requires_verified_text(self):
        from providers import ProviderCapabilities, TIER_SMOKE_TESTED
        # smoke 但 verified_text=False → 既 0 verified 又缺 verified_text
        caps = ProviderCapabilities(
            verification_tier=TIER_SMOKE_TESTED, tier_evidence="x")
        violations = caps.tier_consistency_violations()
        self.assertTrue(any("verified_text=True" in v for v in violations))

    def test_smoke_tested_consistent(self):
        from providers import ProviderCapabilities, TIER_SMOKE_TESTED
        caps = ProviderCapabilities(
            verified_text=True, verification_tier=TIER_SMOKE_TESTED,
            tier_evidence="最小 text 调用通过")
        self.assertEqual(caps.tier_consistency_violations(), [])

    def test_unknown_tier_violation(self):
        from providers import ProviderCapabilities
        caps = ProviderCapabilities(verification_tier="bogus_tier")
        violations = caps.tier_consistency_violations()
        self.assertTrue(any("unknown verification_tier" in v for v in violations))

    # --- 真 provider 档位 ---
    def test_qwen_doubao_production_observed(self):
        from providers import _default_registry, TIER_PRODUCTION_OBSERVED
        self.assertEqual(
            _default_registry.get("qwen").capabilities.verification_tier,
            TIER_PRODUCTION_OBSERVED)
        self.assertEqual(
            _default_registry.get("doubao").capabilities.verification_tier,
            TIER_PRODUCTION_OBSERVED)

    def test_declared_providers(self):
        from providers import _default_registry, TIER_DECLARED
        for name in ("openai", "claude", "kimi", "minimax", "glm"):
            self.assertEqual(
                _default_registry.get(name).capabilities.verification_tier,
                TIER_DECLARED, f"{name} 应为 declared")

    def test_gemini_retired_note(self):
        from providers import _default_registry, TIER_PRODUCTION_OBSERVED
        caps = _default_registry.get("gemini").capabilities
        self.assertEqual(caps.verification_tier, TIER_PRODUCTION_OBSERVED)
        self.assertEqual(caps.tier_note, "已退役出 fallback 链")

    def test_all_registered_providers_tier_consistent(self):
        """CI 守卫: 全部 9 provider 的 tier 与 verified_* 一致 (单一真理源不变式)。"""
        from providers import _default_registry
        violations = _default_registry.tier_consistency_violations()
        self.assertEqual(violations, [],
                         f"provider 档位不一致: {violations}")

    def test_registry_consistency_returns_prefixed(self):
        """registry 汇总用 '<name>: <msg>' 前缀 (供 --check-tiers 复用)。"""
        from providers import (
            ProviderRegistry, ProviderCapabilities, BaseProvider,
            ModelInfo, TIER_PRODUCTION_OBSERVED)
        reg = ProviderRegistry()
        p = BaseProvider()
        p.name = "broken"
        p.display_name = "Broken"
        p.base_url = "https://x"
        p.api_key_env = "X_KEY"
        p.models = [ModelInfo(model_id="m", is_default=True)]
        # production_observed 但 0 verified + 无 evidence → 不一致
        p.capabilities = ProviderCapabilities(
            verification_tier=TIER_PRODUCTION_OBSERVED)
        reg.register(p, validate=False)
        violations = reg.tier_consistency_violations()
        self.assertTrue(violations)
        self.assertTrue(all(v.startswith("broken:") for v in violations))

    # --- tier_table_lines 机器表 ---
    def test_tier_table_lines_structure(self):
        from providers import _default_registry
        lines = _default_registry.tier_table_lines()
        self.assertEqual(lines[0], "| Provider | 档位 | 依据 |")
        self.assertEqual(len(lines), 2 + 9)  # header + sep + 9 providers

    def test_tier_table_declared_uses_derived_evidence(self):
        from providers import _default_registry, _DECLARED_TIER_EVIDENCE
        lines = _default_registry.tier_table_lines()
        openai = [l for l in lines if l.startswith("| OpenAI |")][0]
        self.assertIn(_DECLARED_TIER_EVIDENCE, openai)

    def test_matrix_row_exposes_verification_tier(self):
        """V37.9.146: --json 矩阵行含 verification_tier (数据模型完整暴露)。"""
        from providers import QwenProvider
        row = QwenProvider().to_matrix_row()
        self.assertEqual(row["verification_tier"], "production_observed")


class TestModelInfo(unittest.TestCase):
    """模型信息测试"""

    def test_model_info_fields(self):
        from providers import ModelInfo
        m = ModelInfo(model_id="test-model", display_name="Test", context_window=4096)
        self.assertEqual(m.model_id, "test-model")
        self.assertEqual(m.context_window, 4096)

    def test_model_default_modalities(self):
        from providers import ModelInfo
        m = ModelInfo(model_id="test")
        self.assertEqual(m.modalities, ["text"])


class TestBaseProvider(unittest.TestCase):
    """基类接口测试"""

    def test_default_model(self):
        from providers import QwenProvider
        p = QwenProvider()
        dm = p.default_model()
        self.assertIsNotNone(dm)
        self.assertIn("Qwen3", dm.model_id)

    def test_vision_model(self):
        from providers import QwenProvider
        p = QwenProvider()
        vm = p.vision_model()
        self.assertIsNotNone(vm)
        self.assertIn("VL", vm.model_id)

    def test_no_vision_model(self):
        from providers import OpenAIProvider
        p = OpenAIProvider()
        # OpenAI gpt-4o supports vision but is not a separate VL model
        # is_vision=False by default, so vision_model() returns None
        vm = p.vision_model()
        self.assertIsNone(vm)

    def test_model_id_property(self):
        from providers import QwenProvider
        p = QwenProvider()
        self.assertEqual(p.model_id, "Qwen3-235B-A22B-Instruct-2507-W8A8")

    def test_vl_model_id_property(self):
        from providers import QwenProvider
        p = QwenProvider()
        self.assertEqual(p.vl_model_id, "Qwen2.5-VL-72B-Instruct")

    def test_vl_model_id_empty_when_none(self):
        from providers import OpenAIProvider
        p = OpenAIProvider()
        self.assertEqual(p.vl_model_id, "")

    def test_bearer_auth_headers(self):
        from providers import QwenProvider
        p = QwenProvider()
        headers = p.make_auth_headers("test-key")
        self.assertEqual(headers, {"Authorization": "Bearer test-key"})

    def test_x_api_key_auth_headers(self):
        from providers import ClaudeProvider
        p = ClaudeProvider()
        headers = p.make_auth_headers("test-key")
        self.assertIn("x-api-key", headers)
        self.assertEqual(headers["x-api-key"], "test-key")
        self.assertIn("anthropic-version", headers)

    def test_to_legacy_dict_has_required_fields(self):
        from providers import QwenProvider
        p = QwenProvider()
        d = p.to_legacy_dict()
        required = {"base_url", "api_key_env", "model_id", "auth_style"}
        self.assertTrue(required.issubset(set(d.keys())))

    def test_to_legacy_dict_includes_vl(self):
        from providers import QwenProvider
        p = QwenProvider()
        d = p.to_legacy_dict()
        self.assertIn("vl_model_id", d)

    def test_to_legacy_dict_no_vl_when_absent(self):
        from providers import GeminiProvider
        p = GeminiProvider()
        d = p.to_legacy_dict()
        self.assertNotIn("vl_model_id", d)

    def test_to_matrix_row(self):
        from providers import QwenProvider
        p = QwenProvider()
        row = p.to_matrix_row()
        self.assertEqual(row["provider"], "Qwen (Remote GPU)")
        self.assertIn("text", row["modalities"])
        self.assertTrue(row["tool_calling"])
        self.assertGreater(row["context_window"], 0)
        self.assertIsInstance(row["verified"], list)


class TestConcreteProviders(unittest.TestCase):
    """具体 Provider 实现测试"""

    def test_qwen_provider(self):
        from providers import QwenProvider
        p = QwenProvider()
        self.assertEqual(p.name, "qwen")
        self.assertEqual(p.auth_style, "bearer")
        self.assertTrue(p.capabilities.vision)
        self.assertTrue(p.capabilities.verified_text)
        self.assertEqual(len(p.models), 2)

    def test_openai_provider(self):
        from providers import OpenAIProvider
        p = OpenAIProvider()
        self.assertEqual(p.name, "openai")
        self.assertTrue(p.capabilities.audio)
        self.assertTrue(p.capabilities.json_mode)
        self.assertFalse(p.capabilities.verified_text)  # 未在生产验证

    def test_gemini_provider(self):
        from providers import GeminiProvider
        p = GeminiProvider()
        self.assertEqual(p.name, "gemini")
        self.assertTrue(p.capabilities.verified_fallback)
        self.assertGreater(p.capabilities.context_window, 1000000)

    def test_claude_provider(self):
        from providers import ClaudeProvider
        p = ClaudeProvider()
        self.assertEqual(p.name, "claude")
        self.assertEqual(p.auth_style, "x-api-key")
        self.assertFalse(p.capabilities.verified_text)

    def test_all_providers_have_name(self):
        from providers import get_registry
        for p in get_registry().all():
            self.assertTrue(p.name, f"Provider missing name")
            self.assertTrue(p.display_name, f"{p.name} missing display_name")

    def test_all_providers_have_base_url(self):
        from providers import get_registry
        for p in get_registry().all():
            self.assertTrue(p.base_url.startswith("https://"), f"{p.name}: base_url not HTTPS")

    def test_all_providers_have_api_key_env(self):
        from providers import get_registry
        for p in get_registry().all():
            self.assertTrue(p.api_key_env.endswith("_KEY") or p.api_key_env.endswith("_API_KEY"),
                            f"{p.name}: api_key_env doesn't look like env var")

    def test_all_providers_valid_auth_style(self):
        from providers import get_registry
        valid = {"bearer", "x-api-key"}
        for p in get_registry().all():
            self.assertIn(p.auth_style, valid, f"{p.name}: invalid auth_style")

    def test_all_providers_have_models(self):
        from providers import get_registry
        for p in get_registry().all():
            self.assertGreater(len(p.models), 0, f"{p.name}: no models")

    def test_all_providers_have_default_model(self):
        from providers import get_registry
        for p in get_registry().all():
            self.assertIsNotNone(p.default_model(), f"{p.name}: no default model")


class TestProviderRegistry(unittest.TestCase):
    """注册表测试"""

    def test_default_registry_has_7_providers(self):
        # V37.9.201: doubao + deepseek 真插件 → 总数 9 (7 built-in + 2 真插件)
        from providers import get_registry
        reg = get_registry()
        self.assertEqual(len(reg.list_names()), 9)

    def test_get_existing_provider(self):
        from providers import get_registry
        reg = get_registry()
        p = reg.get("qwen")
        self.assertIsNotNone(p)
        self.assertEqual(p.name, "qwen")

    def test_get_nonexistent_returns_none(self):
        from providers import get_registry
        reg = get_registry()
        self.assertIsNone(reg.get("nonexistent"))

    def test_list_names(self):
        from providers import get_registry
        names = get_registry().list_names()
        self.assertIn("qwen", names)
        self.assertIn("gemini", names)
        self.assertIn("openai", names)
        self.assertIn("claude", names)
        self.assertIn("kimi", names)
        self.assertIn("minimax", names)
        self.assertIn("glm", names)

    def test_to_legacy_dict(self):
        from providers import get_registry
        legacy = get_registry().to_legacy_dict()
        self.assertIsInstance(legacy, dict)
        self.assertIn("qwen", legacy)
        # 验证旧格式字段
        for name, cfg in legacy.items():
            self.assertIn("base_url", cfg)
            self.assertIn("model_id", cfg)

    def test_compatibility_matrix(self):
        # V37.9.201: 9 行 (7 built-in + doubao + deepseek plugins)
        from providers import get_registry
        matrix = get_registry().compatibility_matrix()
        self.assertEqual(len(matrix), 9)
        for row in matrix:
            self.assertIn("provider", row)
            self.assertIn("models", row)
            self.assertIn("modalities", row)
            self.assertIn("verified", row)

    def test_register_custom_provider(self):
        from providers import ProviderRegistry, BaseProvider, ProviderCapabilities, ModelInfo
        reg = ProviderRegistry()
        custom = BaseProvider()
        custom.name = "custom"
        custom.display_name = "Custom LLM"
        custom.base_url = "https://custom.example.com/v1"
        custom.api_key_env = "CUSTOM_API_KEY"
        custom.auth_style = "bearer"
        custom.models = [ModelInfo(model_id="custom-v1", is_default=True)]
        custom.capabilities = ProviderCapabilities(text=True)
        reg.register(custom)
        self.assertEqual(len(reg.list_names()), 1)
        self.assertIsNotNone(reg.get("custom"))

    def test_custom_provider_in_matrix(self):
        from providers import ProviderRegistry, BaseProvider, ProviderCapabilities, ModelInfo
        reg = ProviderRegistry()
        custom = BaseProvider()
        custom.name = "test"
        custom.display_name = "Test"
        custom.base_url = "https://test.example.com/v1"
        custom.api_key_env = "TEST_KEY"
        custom.models = [ModelInfo(model_id="test-v1", is_default=True)]
        custom.capabilities = ProviderCapabilities(text=True, verified_text=True)
        reg.register(custom)
        matrix = reg.compatibility_matrix()
        self.assertEqual(len(matrix), 1)
        self.assertEqual(matrix[0]["provider"], "Test")
        self.assertIn("text", matrix[0]["verified"])


class TestBackwardCompatibility(unittest.TestCase):
    """向后兼容性测试 — 确保 adapter.py 无缝切换"""

    def test_providers_dict_exported(self):
        # V37.9.201: legacy PROVIDERS dict 含 9 entries (7 built-in + doubao + deepseek plugins)
        from providers import PROVIDERS
        self.assertIsInstance(PROVIDERS, dict)
        self.assertEqual(len(PROVIDERS), 9)

    def test_providers_dict_matches_old_format(self):
        """PROVIDERS dict 的格式与旧版 adapter.py 完全一致"""
        from providers import PROVIDERS
        for name, cfg in PROVIDERS.items():
            self.assertIn("base_url", cfg)
            self.assertIn("api_key_env", cfg)
            self.assertIn("model_id", cfg)
            self.assertIn("auth_style", cfg)
            # 值类型检查
            self.assertIsInstance(cfg["base_url"], str)
            self.assertIsInstance(cfg["model_id"], str)

    def test_qwen_vl_model_in_legacy(self):
        """旧格式 qwen 包含 vl_model_id"""
        from providers import PROVIDERS
        self.assertIn("vl_model_id", PROVIDERS["qwen"])
        self.assertIn("VL", PROVIDERS["qwen"]["vl_model_id"])

    def test_claude_auth_style_in_legacy(self):
        """旧格式 claude 使用 x-api-key"""
        from providers import PROVIDERS
        self.assertEqual(PROVIDERS["claude"]["auth_style"], "x-api-key")

    def test_adapter_imports_providers(self):
        """adapter.py 能从 providers.py 导入 PROVIDERS"""
        import ast
        with open("adapter.py") as f:
            content = f.read()
        self.assertIn("from providers import PROVIDERS", content)

    def test_adapter_has_fallback_inline(self):
        """adapter.py 有内联回退定义（providers.py 不可用时）"""
        import ast
        with open("adapter.py") as f:
            content = f.read()
        self.assertIn("ImportError", content)
        # 回退中仍有旧定义
        self.assertIn('"qwen":', content)


class TestAdapterIntegration(unittest.TestCase):
    """adapter.py 集成验证"""

    def _load_adapter_providers(self):
        """从 adapter.py 提取 PROVIDERS（兼容新旧两种方式）"""
        import ast
        with open("adapter.py") as f:
            content = f.read()
        tree = ast.parse(content)
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == "PROVIDERS":
                        try:
                            return ast.literal_eval(node.value)
                        except ValueError:
                            pass  # 新版是 import，不是 literal
        return None

    def test_adapter_syntax_valid(self):
        """adapter.py 语法正确"""
        import subprocess
        result = subprocess.run(
            [sys.executable, "-c", "import ast; ast.parse(open('adapter.py').read())"],
            capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 0, f"Syntax error: {result.stderr}")

    def test_providers_syntax_valid(self):
        """providers.py 语法正确"""
        import subprocess
        result = subprocess.run(
            [sys.executable, "-c", "import ast; ast.parse(open('providers.py').read())"],
            capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 0, f"Syntax error: {result.stderr}")

    def test_providers_importable(self):
        """providers.py 可以 import"""
        import subprocess
        result = subprocess.run(
            [sys.executable, "-c", "from providers import PROVIDERS, get_provider, get_registry"],
            capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 0, f"Import error: {result.stderr}")

    def test_health_endpoint_has_capabilities(self):
        """adapter.py 健康端点包含 capabilities 信息"""
        with open("adapter.py") as f:
            content = f.read()
        self.assertIn("capabilities", content)
        self.assertIn("verified", content)


class TestCLIOutput(unittest.TestCase):
    """CLI 输出测试"""

    def test_matrix_output(self):
        """python3 providers.py 输出 Markdown 表格"""
        import subprocess
        result = subprocess.run(
            [sys.executable, "providers.py"],
            capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("Provider Compatibility Matrix", result.stdout)
        self.assertIn("Qwen", result.stdout)
        self.assertIn("Verification Status", result.stdout)

    def test_json_output(self):
        """python3 providers.py --json 输出合法 JSON"""
        import subprocess
        result = subprocess.run(
            [sys.executable, "providers.py", "--json"],
            capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 0)
        data = json.loads(result.stdout)
        self.assertIsInstance(data, list)
        # V37.9.201: 9 行 (7 built-in + doubao + deepseek plugins)
        self.assertEqual(len(data), 9)


class TestChineseProviders(unittest.TestCase):
    """中国国内 Provider 测试"""

    def test_kimi_provider(self):
        from providers import KimiProvider
        p = KimiProvider()
        self.assertEqual(p.name, "kimi")
        self.assertEqual(p.auth_style, "bearer")
        self.assertIn("moonshot.ai", p.base_url)
        self.assertEqual(p.api_key_env, "MOONSHOT_API_KEY")
        self.assertEqual(len(p.models), 1)
        self.assertTrue(p.capabilities.tool_calling)
        self.assertTrue(p.capabilities.vision)
        self.assertEqual(p.capabilities.context_window, 262144)

    def test_kimi_default_model(self):
        from providers import KimiProvider
        p = KimiProvider()
        dm = p.default_model()
        self.assertIsNotNone(dm)
        self.assertEqual(dm.model_id, "kimi-k2.5")

    def test_minimax_provider(self):
        from providers import MiniMaxProvider
        p = MiniMaxProvider()
        self.assertEqual(p.name, "minimax")
        self.assertEqual(p.auth_style, "bearer")
        self.assertIn("minimaxi.com", p.base_url)
        self.assertEqual(p.api_key_env, "MINIMAX_API_KEY")
        self.assertEqual(len(p.models), 1)
        self.assertTrue(p.capabilities.tool_calling)
        self.assertTrue(p.capabilities.vision)
        self.assertEqual(p.capabilities.context_window, 204800)

    def test_minimax_default_model(self):
        from providers import MiniMaxProvider
        p = MiniMaxProvider()
        dm = p.default_model()
        self.assertIsNotNone(dm)
        self.assertEqual(dm.model_id, "MiniMax-M2.7")

    def test_glm_provider(self):
        from providers import GLMProvider
        p = GLMProvider()
        self.assertEqual(p.name, "glm")
        self.assertEqual(p.auth_style, "bearer")
        self.assertIn("bigmodel.cn", p.base_url)
        self.assertEqual(p.api_key_env, "GLM_API_KEY")
        self.assertEqual(len(p.models), 2)
        self.assertTrue(p.capabilities.vision)
        self.assertTrue(p.capabilities.tool_calling)

    def test_glm_has_vision_model(self):
        from providers import GLMProvider
        p = GLMProvider()
        vm = p.vision_model()
        self.assertIsNotNone(vm)
        self.assertEqual(vm.model_id, "glm-5v-turbo")

    def test_glm_default_model(self):
        from providers import GLMProvider
        p = GLMProvider()
        dm = p.default_model()
        self.assertIsNotNone(dm)
        self.assertEqual(dm.model_id, "glm-5")

    def test_glm_vl_model_in_legacy(self):
        from providers import GLMProvider
        p = GLMProvider()
        d = p.to_legacy_dict()
        self.assertIn("vl_model_id", d)
        self.assertEqual(d["vl_model_id"], "glm-5v-turbo")

    def test_chinese_providers_in_legacy_dict(self):
        from providers import PROVIDERS
        self.assertIn("kimi", PROVIDERS)
        self.assertIn("minimax", PROVIDERS)
        self.assertIn("glm", PROVIDERS)

    def test_chinese_providers_openai_compatible(self):
        """All Chinese providers use bearer auth (OpenAI-compatible)."""
        from providers import get_registry
        for name in ["kimi", "minimax", "glm"]:
            p = get_registry().get(name)
            self.assertEqual(p.auth_style, "bearer", f"{name} should use bearer auth")


class TestProviderContract(unittest.TestCase):
    """Provider contract validation tests"""

    def _make_valid_provider(self, **overrides):
        from providers import BaseProvider, ModelInfo, ProviderCapabilities
        p = BaseProvider()
        p.name = overrides.get('name', 'test')
        p.display_name = overrides.get('display_name', 'Test Provider')
        p.base_url = overrides.get('base_url', 'https://api.test.com/v1')
        p.api_key_env = overrides.get('api_key_env', 'TEST_API_KEY')
        p.auth_style = overrides.get('auth_style', 'bearer')
        p.models = overrides.get('models', [
            ModelInfo(model_id='test-v1', is_default=True)
        ])
        p.capabilities = overrides.get('capabilities', ProviderCapabilities(text=True))
        return p

    def test_valid_provider_passes(self):
        from providers import ProviderContract
        p = self._make_valid_provider()
        self.assertEqual(ProviderContract.validate(p), [])

    def test_missing_name(self):
        from providers import ProviderContract
        p = self._make_valid_provider(name='')
        violations = ProviderContract.validate(p)
        self.assertTrue(any('name' in v for v in violations))

    def test_missing_base_url(self):
        from providers import ProviderContract
        p = self._make_valid_provider(base_url='')
        violations = ProviderContract.validate(p)
        self.assertTrue(any('base_url' in v for v in violations))

    def test_missing_api_key_env(self):
        from providers import ProviderContract
        p = self._make_valid_provider(api_key_env='')
        violations = ProviderContract.validate(p)
        self.assertTrue(any('api_key_env' in v for v in violations))

    def test_no_models(self):
        from providers import ProviderContract
        p = self._make_valid_provider(models=[])
        violations = ProviderContract.validate(p)
        self.assertTrue(any('model' in v.lower() for v in violations))

    def test_model_without_id(self):
        from providers import ProviderContract, ModelInfo
        p = self._make_valid_provider(models=[ModelInfo(model_id='')])
        violations = ProviderContract.validate(p)
        self.assertTrue(any('model_id' in v for v in violations))

    def test_multiple_defaults(self):
        from providers import ProviderContract, ModelInfo
        p = self._make_valid_provider(models=[
            ModelInfo(model_id='m1', is_default=True),
            ModelInfo(model_id='m2', is_default=True),
        ])
        violations = ProviderContract.validate(p)
        self.assertTrue(any('is_default' in v for v in violations))

    def test_invalid_auth_style(self):
        from providers import ProviderContract
        p = self._make_valid_provider(auth_style='invalid')
        violations = ProviderContract.validate(p)
        self.assertTrue(any('auth_style' in v for v in violations))

    def test_valid_auth_styles(self):
        from providers import ProviderContract
        for style in ['bearer', 'x-api-key', 'query-param', 'custom']:
            p = self._make_valid_provider(auth_style=style)
            violations = ProviderContract.validate(p)
            self.assertEqual(violations, [], f"auth_style '{style}' should be valid")

    def test_vision_capability_without_vision_model(self):
        from providers import ProviderContract, ProviderCapabilities, ModelInfo
        caps = ProviderCapabilities(text=True, vision=True)
        p = self._make_valid_provider(
            capabilities=caps,
            models=[ModelInfo(model_id='text-only', modalities=['text'])]
        )
        violations = ProviderContract.validate(p)
        self.assertTrue(any('vision' in v for v in violations))

    def test_vision_capability_with_vision_model(self):
        from providers import ProviderContract, ProviderCapabilities, ModelInfo
        caps = ProviderCapabilities(text=True, vision=True)
        p = self._make_valid_provider(
            capabilities=caps,
            models=[ModelInfo(model_id='vis', modalities=['text', 'vision'], is_default=True)]
        )
        violations = ProviderContract.validate(p)
        self.assertEqual(violations, [])

    def test_all_builtin_providers_pass_contract(self):
        from providers import ProviderContract, get_registry
        for p in get_registry().all():
            violations = ProviderContract.validate(p)
            self.assertEqual(violations, [], f"{p.name} has contract violations: {violations}")


class TestContractViolationError(unittest.TestCase):
    """ContractViolationError tests"""

    def test_error_message(self):
        from providers import ContractViolationError
        err = ContractViolationError("bad_provider", ["name is required", "no models"])
        self.assertIn("bad_provider", str(err))
        self.assertIn("name is required", str(err))

    def test_error_attributes(self):
        from providers import ContractViolationError
        err = ContractViolationError("bad", ["v1", "v2"])
        self.assertEqual(err.provider_name, "bad")
        self.assertEqual(err.violations, ["v1", "v2"])

    def test_is_value_error(self):
        from providers import ContractViolationError
        err = ContractViolationError("x", ["v"])
        self.assertIsInstance(err, ValueError)


class TestPluginLoaderYAML(unittest.TestCase):
    """YAML plugin loading tests"""

    def setUp(self):
        import tempfile
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir)

    def _write_yaml(self, filename, content):
        path = os.path.join(self.tmpdir, filename)
        with open(path, 'w') as f:
            f.write(content)
        return path

    def test_load_valid_yaml(self):
        from providers import PluginLoader
        path = self._write_yaml("deepseek.yaml", """
name: deepseek
display_name: DeepSeek
base_url: https://api.deepseek.com/v1
api_key_env: DEEPSEEK_API_KEY
auth_style: bearer
models:
  - model_id: deepseek-chat
    display_name: DeepSeek V3
    modalities: [text]
    context_window: 65536
    max_output_tokens: 8192
    is_default: true
capabilities:
  text: true
  tool_calling: true
  streaming: true
  context_window: 65536
""")
        p = PluginLoader.from_yaml(path)
        self.assertEqual(p.name, "deepseek")
        self.assertEqual(p.display_name, "DeepSeek")
        self.assertEqual(p.base_url, "https://api.deepseek.com/v1")
        self.assertEqual(p.api_key_env, "DEEPSEEK_API_KEY")
        self.assertEqual(p.auth_style, "bearer")
        self.assertEqual(len(p.models), 1)
        self.assertEqual(p.models[0].model_id, "deepseek-chat")
        self.assertTrue(p.capabilities.tool_calling)
        self.assertTrue(p.capabilities.streaming)
        self.assertEqual(p.capabilities.context_window, 65536)

    def test_yaml_default_auth_style(self):
        from providers import PluginLoader
        path = self._write_yaml("minimal.yaml", """
name: minimal
base_url: https://api.minimal.com/v1
api_key_env: MINIMAL_API_KEY
models:
  - model_id: mini-v1
    is_default: true
""")
        p = PluginLoader.from_yaml(path)
        self.assertEqual(p.auth_style, "bearer")

    def test_yaml_display_name_defaults_to_name(self):
        from providers import PluginLoader
        path = self._write_yaml("noname.yaml", """
name: noname
base_url: https://api.x.com/v1
api_key_env: X_KEY
models:
  - model_id: x-v1
    is_default: true
""")
        p = PluginLoader.from_yaml(path)
        self.assertEqual(p.display_name, "noname")

    def test_yaml_multiple_models(self):
        from providers import PluginLoader
        path = self._write_yaml("multi.yaml", """
name: multi
base_url: https://api.multi.com/v1
api_key_env: MULTI_KEY
models:
  - model_id: text-v1
    is_default: true
  - model_id: vision-v1
    is_vision: true
    modalities: [text, vision]
""")
        p = PluginLoader.from_yaml(path)
        self.assertEqual(len(p.models), 2)
        self.assertIsNotNone(p.default_model())
        self.assertIsNotNone(p.vision_model())

    def test_yaml_legacy_dict(self):
        from providers import PluginLoader
        path = self._write_yaml("legacy.yaml", """
name: legtest
base_url: https://api.leg.com/v1
api_key_env: LEG_KEY
models:
  - model_id: leg-v1
    is_default: true
""")
        p = PluginLoader.from_yaml(path)
        d = p.to_legacy_dict()
        self.assertEqual(d['base_url'], 'https://api.leg.com/v1')
        self.assertEqual(d['model_id'], 'leg-v1')

    def test_yaml_invalid_not_dict(self):
        from providers import PluginLoader
        path = self._write_yaml("bad.yaml", "- just a list")
        with self.assertRaises(ValueError):
            PluginLoader.from_yaml(path)

    def test_yaml_plugin_source_recorded(self):
        from providers import PluginLoader
        path = self._write_yaml("src.yaml", """
name: src
base_url: https://api.src.com/v1
api_key_env: SRC_KEY
models:
  - model_id: src-v1
    is_default: true
""")
        p = PluginLoader.from_yaml(path)
        self.assertEqual(p._plugin_source, path)


class TestPluginLoaderPython(unittest.TestCase):
    """Python plugin loading tests"""

    def setUp(self):
        import tempfile
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir)

    def _write_py(self, filename, content):
        path = os.path.join(self.tmpdir, filename)
        with open(path, 'w') as f:
            f.write(content)
        return path

    def test_load_valid_python_plugin(self):
        from providers import PluginLoader
        path = self._write_py("myprovider.py", """
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from providers import BaseProvider, ModelInfo, ProviderCapabilities

class MyProvider(BaseProvider):
    name = "myprovider"
    display_name = "My Provider"
    base_url = "https://api.my.com/v1"
    api_key_env = "MY_API_KEY"
    auth_style = "bearer"
    models = [ModelInfo(model_id="my-v1", is_default=True)]
    capabilities = ProviderCapabilities(text=True, streaming=True)
""")
        p = PluginLoader.from_python(path)
        self.assertEqual(p.name, "myprovider")
        self.assertTrue(p.capabilities.streaming)

    def test_python_no_subclass(self):
        from providers import PluginLoader
        path = self._write_py("empty.py", "x = 1\n")
        with self.assertRaises(ValueError) as ctx:
            PluginLoader.from_python(path)
        self.assertIn("No BaseProvider subclass", str(ctx.exception))

    def test_python_multiple_subclasses(self):
        from providers import PluginLoader
        path = self._write_py("multi.py", """
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from providers import BaseProvider, ModelInfo

class ProviderA(BaseProvider):
    name = "a"
    base_url = "https://a.com/v1"
    api_key_env = "A_KEY"
    models = [ModelInfo(model_id="a-v1", is_default=True)]

class ProviderB(BaseProvider):
    name = "b"
    base_url = "https://b.com/v1"
    api_key_env = "B_KEY"
    models = [ModelInfo(model_id="b-v1", is_default=True)]
""")
        with self.assertRaises(ValueError) as ctx:
            PluginLoader.from_python(path)
        self.assertIn("Multiple", str(ctx.exception))

    def test_python_custom_auth(self):
        from providers import PluginLoader
        path = self._write_py("customauth.py", """
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from providers import BaseProvider, ModelInfo, ProviderCapabilities

class CustomAuthProvider(BaseProvider):
    name = "customauth"
    display_name = "Custom Auth"
    base_url = "https://api.custom.com/v1"
    api_key_env = "CUSTOM_KEY"
    auth_style = "custom"
    models = [ModelInfo(model_id="c-v1", is_default=True)]
    capabilities = ProviderCapabilities(text=True)

    def make_auth_headers(self, api_key):
        return {"X-Custom": f"Token {api_key}"}
""")
        p = PluginLoader.from_python(path)
        self.assertEqual(p.name, "customauth")
        headers = p.make_auth_headers("mykey")
        self.assertEqual(headers, {"X-Custom": "Token mykey"})


class TestPluginDiscovery(unittest.TestCase):
    """Plugin directory discovery tests"""

    def setUp(self):
        import tempfile
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir)

    def _write_file(self, filename, content):
        path = os.path.join(self.tmpdir, filename)
        with open(path, 'w') as f:
            f.write(content)
        return path

    def test_discover_empty_directory(self):
        from providers import PluginLoader
        results = PluginLoader.discover(self.tmpdir)
        self.assertEqual(results, [])

    def test_discover_skips_underscore_files(self):
        from providers import PluginLoader
        self._write_file("_example.yaml", "name: skip\n")
        results = PluginLoader.discover(self.tmpdir)
        self.assertEqual(results, [])

    def test_discover_skips_dot_files(self):
        from providers import PluginLoader
        self._write_file(".hidden.yaml", "name: hidden\n")
        results = PluginLoader.discover(self.tmpdir)
        self.assertEqual(results, [])

    def test_discover_nonexistent_directory(self):
        from providers import PluginLoader
        results = PluginLoader.discover("/nonexistent/path/12345")
        self.assertEqual(results, [])

    def test_discover_yaml_plugin(self):
        from providers import PluginLoader
        self._write_file("testprov.yaml", """
name: testprov
base_url: https://api.test.com/v1
api_key_env: TEST_KEY
models:
  - model_id: test-v1
    is_default: true
""")
        results = PluginLoader.discover(self.tmpdir)
        self.assertEqual(len(results), 1)
        provider, error = results[0]
        self.assertIsNone(error)
        self.assertEqual(provider.name, "testprov")

    def test_discover_reports_errors(self):
        from providers import PluginLoader
        self._write_file("bad.yaml", "- not a dict")
        results = PluginLoader.discover(self.tmpdir)
        self.assertEqual(len(results), 1)
        provider, error = results[0]
        self.assertIsNone(provider)
        self.assertIn("bad.yaml", error)

    def test_discover_skips_non_plugin_extensions(self):
        from providers import PluginLoader
        self._write_file("readme.txt", "not a plugin")
        self._write_file("data.json", '{"not": "plugin"}')
        results = PluginLoader.discover(self.tmpdir)
        self.assertEqual(results, [])

    def test_discover_mixed_valid_and_invalid(self):
        from providers import PluginLoader
        self._write_file("good.yaml", """
name: good
base_url: https://api.good.com/v1
api_key_env: GOOD_KEY
models:
  - model_id: good-v1
    is_default: true
""")
        self._write_file("bad.yaml", "not: {valid: yaml: here")
        results = PluginLoader.discover(self.tmpdir)
        self.assertEqual(len(results), 2)
        successes = [(p, e) for p, e in results if e is None]
        failures = [(p, e) for p, e in results if e is not None]
        self.assertEqual(len(successes), 1)
        self.assertEqual(len(failures), 1)


class TestRegistryWithValidation(unittest.TestCase):
    """Registry register() with contract validation"""

    def _make_valid_provider(self):
        from providers import BaseProvider, ModelInfo, ProviderCapabilities
        p = BaseProvider()
        p.name = "valid"
        p.base_url = "https://api.valid.com/v1"
        p.api_key_env = "VALID_KEY"
        p.models = [ModelInfo(model_id="v1", is_default=True)]
        p.capabilities = ProviderCapabilities(text=True)
        return p

    def test_register_valid_passes(self):
        from providers import ProviderRegistry
        reg = ProviderRegistry()
        reg.register(self._make_valid_provider())
        self.assertEqual(len(reg.list_names()), 1)

    def test_register_invalid_raises(self):
        from providers import ProviderRegistry, BaseProvider, ContractViolationError
        reg = ProviderRegistry()
        bad = BaseProvider()  # all defaults = empty
        with self.assertRaises(ContractViolationError):
            reg.register(bad)

    def test_register_skip_validation(self):
        from providers import ProviderRegistry, BaseProvider
        reg = ProviderRegistry()
        bad = BaseProvider()
        bad.name = "raw"
        reg.register(bad, validate=False)
        self.assertIn("raw", reg.list_names())

    def test_unregister_existing(self):
        from providers import ProviderRegistry
        reg = ProviderRegistry()
        reg.register(self._make_valid_provider())
        self.assertTrue(reg.unregister("valid"))
        self.assertEqual(len(reg.list_names()), 0)

    def test_unregister_nonexistent(self):
        from providers import ProviderRegistry
        reg = ProviderRegistry()
        self.assertFalse(reg.unregister("ghost"))


class TestRegistryLoadPlugins(unittest.TestCase):
    """Registry.load_plugins() integration tests"""

    def setUp(self):
        import tempfile
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir)

    def _write_yaml(self, filename, content):
        path = os.path.join(self.tmpdir, filename)
        with open(path, 'w') as f:
            f.write(content)
        return path

    def test_load_plugins_adds_to_registry(self):
        from providers import ProviderRegistry, QwenProvider
        reg = ProviderRegistry()
        reg.register(QwenProvider())
        self._write_yaml("newprov.yaml", """
name: newprov
base_url: https://api.new.com/v1
api_key_env: NEW_KEY
models:
  - model_id: new-v1
    is_default: true
""")
        errors = reg.load_plugins(self.tmpdir)
        self.assertEqual(errors, [])
        self.assertEqual(len(reg.list_names()), 2)
        self.assertIn("newprov", reg.list_names())

    def test_load_plugins_skips_conflicts(self):
        from providers import ProviderRegistry, QwenProvider
        reg = ProviderRegistry()
        reg.register(QwenProvider())
        self._write_yaml("qwen.yaml", """
name: qwen
base_url: https://api.fake.com/v1
api_key_env: FAKE_KEY
models:
  - model_id: fake-v1
    is_default: true
""")
        errors = reg.load_plugins(self.tmpdir)
        self.assertEqual(len(errors), 1)
        self.assertIn("conflicts", errors[0])
        # Original should be preserved
        self.assertEqual(reg.get("qwen").base_url, "https://hkagentx.hkopenlab.com/v1")

    def test_load_plugins_skips_invalid(self):
        from providers import ProviderRegistry
        reg = ProviderRegistry()
        self._write_yaml("invalid.yaml", """
name: ""
base_url: ""
api_key_env: ""
models: []
""")
        errors = reg.load_plugins(self.tmpdir)
        self.assertGreater(len(errors), 0)
        self.assertEqual(len(reg.list_names()), 0)

    def test_load_plugins_in_legacy_dict(self):
        from providers import ProviderRegistry
        reg = ProviderRegistry()
        self._write_yaml("plug.yaml", """
name: plug
base_url: https://api.plug.com/v1
api_key_env: PLUG_KEY
models:
  - model_id: plug-v1
    is_default: true
capabilities:
  text: true
""")
        reg.load_plugins(self.tmpdir)
        legacy = reg.to_legacy_dict()
        self.assertIn("plug", legacy)
        self.assertEqual(legacy["plug"]["base_url"], "https://api.plug.com/v1")
        self.assertEqual(legacy["plug"]["model_id"], "plug-v1")

    def test_plugin_errors_property(self):
        from providers import ProviderRegistry
        reg = ProviderRegistry()
        self._write_yaml("bad.yaml", "- list not dict")
        reg.load_plugins(self.tmpdir)
        self.assertGreater(len(reg.plugin_errors), 0)

    def test_load_plugins_empty_dir(self):
        from providers import ProviderRegistry
        reg = ProviderRegistry()
        errors = reg.load_plugins(self.tmpdir)
        self.assertEqual(errors, [])


class TestCLIValidate(unittest.TestCase):
    """CLI --validate flag tests"""

    def test_validate_all_pass(self):
        import subprocess
        result = subprocess.run(
            [sys.executable, "providers.py", "--validate"],
            capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("All providers valid", result.stdout)
        self.assertIn("OK", result.stdout)

    def test_cli_shows_plugin_count(self):
        import subprocess
        result = subprocess.run(
            [sys.executable, "providers.py"],
            capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("built-in", result.stdout)


class TestFindByCapability(unittest.TestCase):
    """Capability-based provider discovery tests"""

    def test_find_vision_providers(self):
        from providers import get_registry
        vision = get_registry().find_by_capability(vision=True)
        names = [p.name for p in vision]
        self.assertIn("qwen", names)
        self.assertIn("openai", names)
        self.assertGreater(len(vision), 0)

    def test_find_audio_providers(self):
        from providers import get_registry
        audio = get_registry().find_by_capability(audio=True)
        names = [p.name for p in audio]
        self.assertIn("openai", names)
        # Qwen doesn't support audio
        self.assertNotIn("qwen", names)

    def test_find_tool_calling_and_streaming(self):
        from providers import get_registry
        results = get_registry().find_by_capability(tool_calling=True, streaming=True)
        self.assertGreater(len(results), 0)
        for p in results:
            self.assertTrue(p.capabilities.tool_calling)
            self.assertTrue(p.capabilities.streaming)

    def test_find_nonexistent_combination(self):
        from providers import get_registry
        # No provider has video support
        results = get_registry().find_by_capability(video=True)
        self.assertEqual(results, [])

    def test_find_json_mode(self):
        from providers import get_registry
        results = get_registry().find_by_capability(json_mode=True)
        names = [p.name for p in results]
        self.assertIn("openai", names)
        self.assertIn("gemini", names)
        # Qwen doesn't have json_mode
        self.assertNotIn("qwen", names)

    def test_find_with_false_value(self):
        from providers import get_registry
        # Providers that do NOT support vision
        text_only = get_registry().find_by_capability(vision=False)
        for p in text_only:
            self.assertFalse(p.capabilities.vision)

    def test_find_empty_kwargs_returns_all(self):
        from providers import get_registry
        results = get_registry().find_by_capability()
        self.assertEqual(len(results), len(get_registry().list_names()))

    def test_find_on_custom_registry(self):
        from providers import ProviderRegistry, BaseProvider, ModelInfo, ProviderCapabilities
        reg = ProviderRegistry()
        p1 = BaseProvider()
        p1.name = "vis"
        p1.base_url = "https://vis.com/v1"
        p1.api_key_env = "V_KEY"
        p1.models = [ModelInfo(model_id="v1", modalities=["text", "vision"], is_default=True, is_vision=True)]
        p1.capabilities = ProviderCapabilities(text=True, vision=True)
        p2 = BaseProvider()
        p2.name = "txt"
        p2.base_url = "https://txt.com/v1"
        p2.api_key_env = "T_KEY"
        p2.models = [ModelInfo(model_id="t1", is_default=True)]
        p2.capabilities = ProviderCapabilities(text=True, vision=False)
        reg.register(p1)
        reg.register(p2)
        vision = reg.find_by_capability(vision=True)
        self.assertEqual(len(vision), 1)
        self.assertEqual(vision[0].name, "vis")


class TestBuildFallbackChain(unittest.TestCase):
    """Auto-generated fallback chain tests"""

    def test_fallback_chain_excludes_primary(self):
        from providers import get_registry
        chain = get_registry().build_fallback_chain("qwen")
        names = [p.name for p in chain]
        self.assertNotIn("qwen", names)

    def test_fallback_chain_returns_all_others(self):
        from providers import get_registry
        chain = get_registry().build_fallback_chain("qwen")
        self.assertEqual(len(chain), len(get_registry().list_names()) - 1)

    def test_fallback_chain_verified_first(self):
        from providers import get_registry
        chain = get_registry().build_fallback_chain("qwen")
        # Gemini has verified_fallback=True, should rank high
        names = [p.name for p in chain]
        gemini_idx = names.index("gemini")
        # Gemini should be in top positions (has verified features)
        self.assertLessEqual(gemini_idx, 2)

    def test_fallback_chain_nonexistent_primary(self):
        from providers import get_registry
        chain = get_registry().build_fallback_chain("nonexistent")
        self.assertEqual(chain, [])

    def test_fallback_chain_custom_registry(self):
        from providers import ProviderRegistry, BaseProvider, ModelInfo, ProviderCapabilities
        reg = ProviderRegistry()
        primary = BaseProvider()
        primary.name = "primary"
        primary.base_url = "https://p.com/v1"
        primary.api_key_env = "P_KEY"
        primary.models = [ModelInfo(model_id="p1", modalities=["text", "vision"], is_default=True)]
        primary.capabilities = ProviderCapabilities(
            text=True, vision=True, tool_calling=True
        )
        # fb1: high overlap (text+vision+tool), verified
        fb1 = BaseProvider()
        fb1.name = "fb1"
        fb1.base_url = "https://fb1.com/v1"
        fb1.api_key_env = "F1_KEY"
        fb1.models = [ModelInfo(model_id="f1", modalities=["text", "vision"], is_default=True)]
        fb1.capabilities = ProviderCapabilities(
            text=True, vision=True, tool_calling=True,
            verified_text=True, verified_fallback=True
        )
        # fb2: low overlap (text only), no verification
        fb2 = BaseProvider()
        fb2.name = "fb2"
        fb2.base_url = "https://fb2.com/v1"
        fb2.api_key_env = "F2_KEY"
        fb2.models = [ModelInfo(model_id="f2", is_default=True)]
        fb2.capabilities = ProviderCapabilities(text=True)
        reg.register(primary)
        reg.register(fb2)
        reg.register(fb1)
        chain = reg.build_fallback_chain("primary")
        self.assertEqual(len(chain), 2)
        # fb1 should rank before fb2 (more overlap + verified)
        self.assertEqual(chain[0].name, "fb1")
        self.assertEqual(chain[1].name, "fb2")


class TestCapabilityOverlap(unittest.TestCase):
    """Capability overlap comparison tests"""

    def test_overlap_qwen_gemini(self):
        from providers import get_registry
        overlap = get_registry().capability_overlap("qwen", "gemini")
        self.assertIsInstance(overlap, dict)
        self.assertTrue(overlap["text"])
        self.assertTrue(overlap["vision"])
        self.assertTrue(overlap["tool_calling"])
        self.assertTrue(overlap["streaming"])

    def test_overlap_nonexistent(self):
        from providers import get_registry
        overlap = get_registry().capability_overlap("qwen", "nonexistent")
        self.assertEqual(overlap, {})

    def test_overlap_has_all_capability_fields(self):
        from providers import get_registry
        overlap = get_registry().capability_overlap("qwen", "openai")
        # V37.9.53: capability_overlap 维度从 7 扩展到 8 (加 reasoning)
        expected_keys = {"text", "vision", "audio", "video",
                         "tool_calling", "streaming", "json_mode", "reasoning"}
        self.assertEqual(set(overlap.keys()), expected_keys)

    def test_overlap_audio_qwen_vs_openai(self):
        from providers import get_registry
        overlap = get_registry().capability_overlap("qwen", "openai")
        # Qwen has no audio, OpenAI has audio → overlap is False
        self.assertFalse(overlap["audio"])


class TestAvailableProviders(unittest.TestCase):
    """Registry.available() tests"""

    def test_available_checks_env(self):
        from providers import ProviderRegistry, BaseProvider, ModelInfo, ProviderCapabilities
        reg = ProviderRegistry()
        p = BaseProvider()
        p.name = "envtest"
        p.base_url = "https://env.com/v1"
        p.api_key_env = "_TEST_AVAIL_KEY_12345"
        p.models = [ModelInfo(model_id="e1", is_default=True)]
        p.capabilities = ProviderCapabilities(text=True)
        reg.register(p)
        # Key not set → not available
        avail = reg.available()
        self.assertEqual(len(avail), 0)
        # Set key → available
        os.environ["_TEST_AVAIL_KEY_12345"] = "test-key"
        try:
            avail = reg.available()
            self.assertEqual(len(avail), 1)
            self.assertEqual(avail[0].name, "envtest")
        finally:
            del os.environ["_TEST_AVAIL_KEY_12345"]

    def test_available_empty_key_is_unavailable(self):
        from providers import ProviderRegistry, BaseProvider, ModelInfo, ProviderCapabilities
        reg = ProviderRegistry()
        p = BaseProvider()
        p.name = "empty"
        p.base_url = "https://e.com/v1"
        p.api_key_env = "_TEST_EMPTY_KEY_12345"
        p.models = [ModelInfo(model_id="e1", is_default=True)]
        p.capabilities = ProviderCapabilities(text=True)
        reg.register(p)
        os.environ["_TEST_EMPTY_KEY_12345"] = ""
        try:
            avail = reg.available()
            self.assertEqual(len(avail), 0)
        finally:
            del os.environ["_TEST_EMPTY_KEY_12345"]


class TestFallbackChainCLI(unittest.TestCase):
    """CLI --fallback-chain tests"""

    def test_fallback_chain_cli(self):
        import subprocess
        result = subprocess.run(
            [sys.executable, "providers.py", "--fallback-chain", "qwen"],
            capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("Fallback Chain", result.stdout)
        self.assertIn("qwen", result.stdout)
        self.assertIn("Gemini", result.stdout)

    def test_fallback_chain_cli_nonexistent(self):
        import subprocess
        result = subprocess.run(
            [sys.executable, "providers.py", "--fallback-chain", "nonexistent"],
            capture_output=True, text=True
        )
        self.assertNotEqual(result.returncode, 0)


class TestDefaultRegistryPluginDir(unittest.TestCase):
    """Test that the default registry handles providers.d/ correctly"""

    def test_example_files_not_loaded(self):
        """Files starting with _ in providers.d/ should not be loaded."""
        from providers import get_registry
        names = get_registry().list_names()
        # _example_provider.py 的示例 provider 名 "custom" 必须不被加载 (验证 _ 前缀豁免)。
        # V37.9.201: 不再用 "deepseek" 当 canary — deepseek 已是真插件 (providers.d/deepseek_provider.py)
        self.assertNotIn("custom", names)
        # V37.9.201: 7 built-in + 2 真插件 (doubao + deepseek) = 9 (_example.* 仍被跳过)
        self.assertEqual(len(names), 9)
        self.assertIn("doubao", names, "doubao 真插件必须加载 (与 _example 被跳过形成对照)")
        self.assertIn("deepseek", names, "V37.9.201 deepseek 真插件必须加载 (providers.d/deepseek_provider.py)")

    def test_providers_d_exists(self):
        """providers.d/ directory should exist for plugin discovery."""
        providers_d = os.path.join(os.path.dirname(__file__), "providers.d")
        self.assertTrue(os.path.isdir(providers_d))


class TestDeepSeekProvider(unittest.TestCase):
    """V37.9.201 — DeepSeek-V4-Pro 插件 (env-驱动 base_url + key, 公开 repo 安全底线)。"""

    _PLUGIN = os.path.join(os.path.dirname(__file__), "providers.d", "deepseek_provider.py")

    def _load_class(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("deepseek_plugin_test", self._PLUGIN)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.DeepSeekProvider

    def test_registered_with_correct_identity(self):
        from providers import get_provider
        ds = get_provider("deepseek")
        self.assertIsNotNone(ds)
        self.assertEqual(ds.name, "deepseek")
        self.assertEqual(ds.model_id, "DeepSeek-V4-Pro")
        self.assertEqual(ds.api_key_env, "DEEPSEEK_API_KEY")
        self.assertEqual(ds.auth_style, "bearer")

    def test_tier_feature_verified_after_e2e(self):
        # V37.9.202: Mac Mini E2E 实测 text/streaming/tool_calling 3/3 → feature_verified
        # (诚实语义, 原则 #23 — 只 flip 实测过的; reasoning/vision/json_mode 未测保持 False)
        from providers import get_provider
        caps = get_provider("deepseek").capabilities
        self.assertEqual(caps.verification_tier, "feature_verified")
        self.assertTrue(caps.verified_text)
        self.assertTrue(caps.verified_tool_calling)
        self.assertTrue(caps.verified_streaming)
        self.assertTrue(caps.json_mode, "V37.9.203 实测 response_format=json_object 通过")
        # 实测得知不支持/未暴露 → False (非未知)
        self.assertFalse(caps.vision, "V37.9.203 实测 400 非多模态")
        self.assertFalse(caps.verified_vision)
        self.assertFalse(caps.verified_fallback)
        self.assertFalse(caps.reasoning, "reasoning:null 无 R1 通道, 不得声明")
        # feature_verified 非 declared 档位 → 必须有显式 tier_evidence (引用 E2E 证据)
        self.assertTrue(caps.tier_evidence)
        self.assertIn("E2E", caps.tier_evidence)

    def test_base_url_env_driven_with_public_fallback(self):
        Cls = self._load_class()
        # dev 无 DEEPSEEK_BASE_URL → 公开 fallback (非机密, 合约通过)
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("DEEPSEEK_BASE_URL", None)
            p = Cls()
            self.assertEqual(p.base_url, "https://api.deepseek.com/v1")
        # 设 env → 私有端点注入 (Mac Mini 路径)
        with mock.patch.dict(os.environ, {"DEEPSEEK_BASE_URL": "http://x.test/tok/v1"}):
            p = Cls()
            self.assertEqual(p.base_url, "http://x.test/tok/v1")

    def test_excluded_from_available_without_key(self):
        from providers import get_registry
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("DEEPSEEK_API_KEY", None)
            avail = [p.name for p in get_registry().available()]
            self.assertNotIn("deepseek", avail)
        with mock.patch.dict(os.environ, {"DEEPSEEK_API_KEY": "sk-test"}):
            avail = [p.name for p in get_registry().available()]
            self.assertIn("deepseek", avail)

    def test_no_secret_hardcoded_in_repo(self):
        """🔴 公开 repo 安全底线: 机密绝不入库 (不在测试里嵌明文机密, 用 regex 检测)。"""
        import re
        src = open(self._PLUGIN, encoding="utf-8").read()
        # 无裸 IPv4 (私有端点是裸 IP, 绝不能硬编码)
        self.assertIsNone(re.search(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b", src),
                          "插件不得含裸 IP — 私有端点走 DEEPSEEK_BASE_URL env")
        # 无 sk- 形式的 API key 字面量
        self.assertIsNone(re.search(r"sk-[A-Za-z0-9]{12,}", src),
                          "插件不得含 sk- key 字面量 — 走 DEEPSEEK_API_KEY env")
        # 必须引用两个 env 变量
        self.assertIn("DEEPSEEK_API_KEY", src)
        self.assertIn("DEEPSEEK_BASE_URL", src)
        # fallback 是公开域名 (非机密)
        self.assertIn("https://api.deepseek.com/v1", src)


if __name__ == "__main__":
    unittest.main()
