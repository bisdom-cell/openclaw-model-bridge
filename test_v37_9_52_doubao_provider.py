"""V37.9.52 — Doubao Seed 2.0 Pro Provider 接入测试

覆盖范围:
1. TestDoubaoProviderRegistration: plugin 自动发现 + 合约通过 + 8 个 provider 入注册表
2. TestDoubaoEndpointIdHandling: ARK_ENDPOINT_ID env 注入 / 缺 env → fallback / 空字符串处理
3. TestDoubaoCapabilities: text + vision + tool_calling + streaming 声明 + verified=False 待真测
4. TestDoubaoInFallbackChain: build_fallback_chain(qwen) 包含 doubao
5. TestModuleReentryGuard: V37.9.52 关键 fix 回归 — python3 providers.py 直接执行
   时 sys.modules['providers'] alias 让 plugin 子类 issubclass 检查不再失败
6. TestSourceLevelGuards: V37.9.52 marker / ARK env var 字面量 / Volcengine base_url /
   security 守卫 (API key 不得硬编码)

V37.9.52 module 重入 bug 复盘: providers.py 直接执行 (__name__='__main__') 时,
plugin 内 `from providers import BaseProvider` 触发 providers 模块二次加载, 产生
两份独立的 BaseProvider 类对象 → plugin 子类 issubclass 检查永远失败. 修复 = 在
providers.py 顶部加 sys.modules alias 让 __main__ 与 providers 指向同一 module.
"""
import importlib
import os
import re
import subprocess
import sys
import unittest


REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
DOUBAO_PLUGIN = os.path.join(REPO_ROOT, "providers.d", "doubao_provider.py")
PROVIDERS_PY = os.path.join(REPO_ROOT, "providers.py")


def _reload_providers():
    """Reload providers module to pick up env var changes in dev tests."""
    if "providers" in sys.modules:
        del sys.modules["providers"]
    return importlib.import_module("providers")


class TestDoubaoProviderRegistration(unittest.TestCase):
    """Plugin 自动发现 + 合约通过 + 注册到 default registry."""

    def setUp(self):
        os.environ.pop("ARK_ENDPOINT_ID", None)
        self.providers = _reload_providers()

    def test_doubao_plugin_file_exists(self):
        self.assertTrue(
            os.path.isfile(DOUBAO_PLUGIN),
            f"V37.9.52 doubao plugin file must exist at {DOUBAO_PLUGIN}",
        )

    def test_doubao_in_default_registry(self):
        names = self.providers.get_registry().list_names()
        self.assertIn("doubao", names, f"doubao must be registered, got {names}")

    def test_total_provider_count_is_8(self):
        names = self.providers.get_registry().list_names()
        self.assertEqual(
            len(names), 12,
            f"V37.9.254 must have 7 built-in + 5 plugins = 12 providers, "
            f"got {len(names)}: {names}",
        )

    def test_no_plugin_load_errors(self):
        errors = self.providers.get_registry().plugin_errors
        relevant = [e for e in errors if "doubao" in e]
        self.assertEqual(
            relevant, [],
            f"doubao plugin must load without errors, got: {relevant}",
        )

    def test_doubao_contract_validates(self):
        d = self.providers.get_registry().get("doubao")
        self.assertIsNotNone(d)
        violations = self.providers.ProviderContract.validate(d)
        self.assertEqual(
            violations, [],
            f"doubao contract violations: {violations}",
        )

    def test_doubao_provider_metadata(self):
        d = self.providers.get_registry().get("doubao")
        self.assertEqual(d.name, "doubao")
        self.assertEqual(d.api_key_env, "ARK_API_KEY")
        self.assertEqual(d.base_url, "https://ark.cn-beijing.volces.com/api/v3")
        self.assertEqual(d.auth_style, "bearer")
        self.assertIn("Doubao", d.display_name)
        self.assertIn("Volcengine", d.display_name)


class TestDoubaoEndpointIdHandling(unittest.TestCase):
    """ARK_ENDPOINT_ID env 注入 / fallback / 空字符串处理."""

    def setUp(self):
        os.environ.pop("ARK_ENDPOINT_ID", None)

    def tearDown(self):
        os.environ.pop("ARK_ENDPOINT_ID", None)

    def test_dev_env_no_endpoint_id_uses_fallback(self):
        """缺 env → 用 fallback 公开模型号 doubao-seed-2-0-pro."""
        os.environ.pop("ARK_ENDPOINT_ID", None)
        providers = _reload_providers()
        d = providers.get_registry().get("doubao")
        self.assertEqual(d.model_id, "doubao-seed-2-0-pro")

    def test_env_endpoint_id_injected(self):
        """有 env → 真实 endpoint ID 注入 model_id."""
        os.environ["ARK_ENDPOINT_ID"] = "ep-20260511174451-dlhm8"
        providers = _reload_providers()
        d = providers.get_registry().get("doubao")
        self.assertEqual(d.model_id, "ep-20260511174451-dlhm8")

    def test_empty_endpoint_id_uses_fallback(self):
        """空字符串 → fallback (不能让空 model_id 进合约)."""
        os.environ["ARK_ENDPOINT_ID"] = ""
        providers = _reload_providers()
        d = providers.get_registry().get("doubao")
        self.assertEqual(d.model_id, "doubao-seed-2-0-pro")

    def test_whitespace_endpoint_id_uses_fallback(self):
        """空白字符串 → fallback (防 env 配置失误)."""
        os.environ["ARK_ENDPOINT_ID"] = "   "
        providers = _reload_providers()
        d = providers.get_registry().get("doubao")
        self.assertEqual(d.model_id, "doubao-seed-2-0-pro")


class TestDoubaoCapabilities(unittest.TestCase):
    """能力声明: text + vision + tool_calling + streaming + json_mode, verified=False."""

    def setUp(self):
        os.environ.pop("ARK_ENDPOINT_ID", None)
        self.providers = _reload_providers()
        self.d = self.providers.get_registry().get("doubao")

    def test_text_capability(self):
        self.assertTrue(self.d.capabilities.text)

    def test_vision_capability(self):
        self.assertTrue(self.d.capabilities.vision)

    def test_tool_calling_capability(self):
        self.assertTrue(self.d.capabilities.tool_calling)

    def test_streaming_capability(self):
        self.assertTrue(self.d.capabilities.streaming)

    def test_json_mode_capability(self):
        self.assertTrue(self.d.capabilities.json_mode)

    def test_no_audio_no_video(self):
        self.assertFalse(self.d.capabilities.audio)
        self.assertFalse(self.d.capabilities.video)

    def test_context_window_is_256k(self):
        self.assertEqual(self.d.capabilities.context_window, 262144)

    def test_verified_state_evolution(self):
        """V37.9.52 引入 doubao (全 verified_*=False), V37.9.53 flip text+reasoning,
        V37.9.54 flip vision, V37.9.55 flip tool_calling+streaming.
        本 test 锁定 V37.9.55 后仍未实测的 flags (仅 verified_fallback).
        详细 V37.9.55 状态守卫见 test_v37_9_55_doubao_more_verified.py."""
        c = self.d.capabilities
        # 仅 verified_fallback 仍未实测 (V37.9.56+ 生产真 fire 后 flip)
        self.assertFalse(c.verified_fallback, "verified_fallback 未在生产 fire (待 V37.9.56+)")

    def test_default_model_is_vision_capable(self):
        """doubao seed 2.0 是多模态主力, 默认 model 同时承担 text + vision."""
        dm = self.d.default_model()
        self.assertIsNotNone(dm)
        self.assertIn("text", dm.modalities)
        self.assertIn("vision", dm.modalities)
        self.assertTrue(dm.is_default)
        self.assertTrue(dm.is_vision)


class TestDoubaoInFallbackChain(unittest.TestCase):
    """build_fallback_chain(qwen) 自动包含 doubao."""

    def setUp(self):
        os.environ.pop("ARK_ENDPOINT_ID", None)
        self.providers = _reload_providers()

    def test_doubao_appears_in_qwen_fallback_chain(self):
        chain = self.providers.get_registry().build_fallback_chain("qwen")
        names = [p.name for p in chain]
        self.assertIn("doubao", names, f"doubao must appear in qwen fallback chain, got {names}")

    def test_doubao_excluded_from_self_fallback_chain(self):
        chain = self.providers.get_registry().build_fallback_chain("doubao")
        names = [p.name for p in chain]
        self.assertNotIn("doubao", names, "fallback chain must exclude primary itself")


class TestModuleReentryGuard(unittest.TestCase):
    """V37.9.52 关键 fix 回归守卫 — 防 plugin 子类 issubclass 检查在直接执行模式失败.

    Bug 复现: providers.py 顶部缺 sys.modules alias 时, `python3 providers.py` 直接
    执行模式让 plugin 子类继承的 BaseProvider 与 PluginLoader 闭包引用的 BaseProvider
    是不同对象 → "No BaseProvider subclass found" 错误.
    """

    def test_direct_execution_validate_succeeds(self):
        """python3 providers.py --validate 必须 0 错误 (V37.9.52 fix 回归)."""
        result = subprocess.run(
            [sys.executable, PROVIDERS_PY, "--validate"],
            capture_output=True, text=True, timeout=30, cwd=REPO_ROOT,
        )
        self.assertEqual(
            result.returncode, 0,
            f"python3 providers.py --validate must succeed, stderr={result.stderr}",
        )
        self.assertIn("OK doubao", result.stdout)
        self.assertNotIn("No BaseProvider subclass found", result.stdout)
        self.assertNotIn("No BaseProvider subclass found", result.stderr)

    def test_direct_execution_lists_8_providers(self):
        result = subprocess.run(
            [sys.executable, PROVIDERS_PY],
            capture_output=True, text=True, timeout=30, cwd=REPO_ROOT,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("12 providers", result.stdout)  # V37.9.254: +glm5_coding
        self.assertIn("doubao", result.stdout)
        self.assertIn("deepseek", result.stdout)

    def test_providers_py_has_sys_modules_alias_fix(self):
        """source 级守卫: providers.py 必须有 V37.9.52 sys.modules alias 防 bug 回归."""
        with open(PROVIDERS_PY, encoding="utf-8") as f:
            src = f.read()
        self.assertIn(
            'sys.modules["providers"] = sys.modules[__name__]', src,
            "V37.9.52 sys.modules alias 必须保留 — 删除会导致直接执行模式 plugin 加载失败",
        )
        self.assertIn("V37.9.52", src, "V37.9.52 注释标记必须保留以追溯 fix 来源")


class TestSourceLevelGuards(unittest.TestCase):
    """V37.9.52 source-level guards 防止未来重构破坏关键契约."""

    def setUp(self):
        with open(DOUBAO_PLUGIN, encoding="utf-8") as f:
            self.plugin_src = f.read()

    def test_v37_9_52_version_marker(self):
        self.assertIn("V37.9.52", self.plugin_src)

    def test_correct_base_url(self):
        self.assertIn("https://ark.cn-beijing.volces.com/api/v3", self.plugin_src)

    def test_correct_api_key_env_var(self):
        self.assertIn('api_key_env = "ARK_API_KEY"', self.plugin_src)

    def test_endpoint_id_env_var(self):
        self.assertIn("ARK_ENDPOINT_ID", self.plugin_src)

    def test_fallback_model_id_is_public_identifier(self):
        """fallback model_id 必须是公开 model 标识符, 不得是用户专属 endpoint ID."""
        # 公开标识符 doubao-seed-2-0-pro 可以入代码
        self.assertIn("doubao-seed-2-0-pro", self.plugin_src)
        # 用户专属 endpoint ID 不得入代码 (即便用户豁免也守 public repo 安全底线)
        self.assertNotIn(
            "ep-20260511174451-dlhm8", self.plugin_src,
            "用户专属 endpoint ID 不得硬编码 — 必须走 ARK_ENDPOINT_ID env var",
        )

    def test_no_api_key_hardcoded(self):
        """安全底线: API key 任何形式都不得入代码 (即便用户豁免)."""
        # 检测 ark-XXXX 格式的密钥前缀
        self.assertNotRegex(
            self.plugin_src, r"ark-[a-f0-9]{8}-",
            "Volcengine ARK API key 不得硬编码 — 必须走 ARK_API_KEY env var",
        )
        # 检测任何 Bearer token 字面量
        self.assertNotIn(
            "Bearer ", self.plugin_src,
            "API key 不得作为 Bearer 字面量入代码",
        )

    def test_single_baseprovider_subclass(self):
        """V37 plugin loader 要求 exactly one BaseProvider subclass."""
        subclass_pattern = re.compile(r"^class\s+(\w+)\s*\(\s*BaseProvider\s*\)", re.MULTILINE)
        matches = subclass_pattern.findall(self.plugin_src)
        self.assertEqual(
            len(matches), 1,
            f"V37.9.52 plugin must define exactly one BaseProvider subclass, found: {matches}",
        )

    def test_imports_from_providers_module(self):
        """plugin 必须 from providers import — 与 V37 plugin 框架契约对齐."""
        self.assertRegex(
            self.plugin_src,
            r"from\s+providers\s+import\s+.*BaseProvider",
            "plugin 必须 from providers import BaseProvider",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
