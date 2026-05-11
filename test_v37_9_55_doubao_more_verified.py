"""V37.9.55 — Doubao Seed 2.0 Pro flip verified_tool_calling + verified_streaming

V37.9.54 已 flip verified_text + verified_vision + verified_reasoning.
V37.9.55 基于 Mac Mini E2E 实测数据 flip 剩余 2 个 capability:

测试 1 (tool_calling): curl 带 OpenAI tools schema:
- finish_reason="tool_calls"
- message.tool_calls=[{id, function:{name:"get_weather", arguments:'{"city":"北京"}'}}]
- arguments 是合法 JSON 字符串
- reasoning_content 显示思考过程

测试 2 (streaming): curl 带 stream:true:
- SSE data: {chat.completion.chunk}
- delta.content + delta.reasoning_content 双字段流式
- chunk 标准 OpenAI 流式协议

V37.9.55 cap_score: doubao 12→16 (6 base + 5 verified*2), 超越 Qwen3 14
(4 base + 5 verified*2), framework 视角 doubao 是 registry 最强 provider.
但 primary 仍是 Qwen3 (PROVIDER_NAME env=qwen), V37.9.56+ 决定是否切 primary.

剩余 1 个未 flip: verified_fallback (留生产真 fire 后, 不可人为造).
"""
import importlib
import os
import re
import sys
import unittest


REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
DOUBAO_PLUGIN = os.path.join(REPO_ROOT, "providers.d", "doubao_provider.py")


def _reload_providers():
    if "providers" in sys.modules:
        del sys.modules["providers"]
    return importlib.import_module("providers")


class TestVerifiedToolCallingFlagsV9_55(unittest.TestCase):
    """V37.9.55: doubao verified_tool_calling=True (Mac Mini E2E)."""

    def setUp(self):
        os.environ.pop("ARK_ENDPOINT_ID", None)
        self.providers = _reload_providers()
        self.d = self.providers.get_provider("doubao")

    def test_verified_tool_calling_is_true(self):
        self.assertTrue(
            self.d.capabilities.verified_tool_calling,
            "V37.9.55 verified_tool_calling 必须 True (Mac Mini curl tools schema 实测通过)",
        )

    def test_verified_features_includes_tool_calling(self):
        features = self.d.capabilities.verified_features()
        self.assertIn("tool_calling", features)


class TestVerifiedStreamingFlagsV9_55(unittest.TestCase):
    """V37.9.55: doubao verified_streaming=True (Mac Mini E2E)."""

    def setUp(self):
        os.environ.pop("ARK_ENDPOINT_ID", None)
        self.providers = _reload_providers()
        self.d = self.providers.get_provider("doubao")

    def test_verified_streaming_is_true(self):
        self.assertTrue(
            self.d.capabilities.verified_streaming,
            "V37.9.55 verified_streaming 必须 True (Mac Mini curl stream:true 实测 SSE 通过)",
        )

    def test_verified_features_includes_streaming(self):
        features = self.d.capabilities.verified_features()
        self.assertIn("streaming", features)


class TestVerifiedFeaturesV9_55Complete(unittest.TestCase):
    """V37.9.55: verified_features 完整集合 = [text, vision, tool_calling, streaming, reasoning]."""

    def setUp(self):
        os.environ.pop("ARK_ENDPOINT_ID", None)
        self.providers = _reload_providers()
        self.d = self.providers.get_provider("doubao")

    def test_doubao_verified_features_v37_9_55(self):
        features = self.d.capabilities.verified_features()
        self.assertEqual(
            set(features),
            {"text", "vision", "tool_calling", "streaming", "reasoning"},
            f"V37.9.55 doubao 应锁定 5 verified features, got {features}",
        )


class TestCapScoreV9_55(unittest.TestCase):
    """V37.9.55: cap_score 12→16 + 排名超越 Qwen3."""

    def setUp(self):
        os.environ.pop("ARK_ENDPOINT_ID", None)
        self.providers = _reload_providers()
        self.reg = self.providers.get_registry()

    def test_doubao_cap_score_is_16(self):
        """V37.9.55: 6 base (text/vision/tool_calling/streaming/json_mode/reasoning)
        + 5 verified*2 (text/vision/tool_calling/streaming/reasoning) = 16."""
        doubao = self.reg.get("doubao")
        score = self.reg._capability_score(doubao)
        self.assertEqual(
            score, 16,
            f"V37.9.55 doubao cap_score 锁定 16 (6 base + 5 verified*2), got {score}",
        )

    def test_doubao_cap_score_greater_than_qwen(self):
        """V37.9.55: doubao 超越 Qwen3 — framework 视角 doubao 是 registry 最强 provider.
        (但 primary 仍是 Qwen3, V37.9.56+ 决定是否切 primary)."""
        doubao = self.reg.get("doubao")
        qwen = self.reg.get("qwen")
        doubao_score = self.reg._capability_score(doubao)
        qwen_score = self.reg._capability_score(qwen)
        self.assertGreater(
            doubao_score, qwen_score,
            f"V37.9.55 doubao ({doubao_score}) 应 > qwen ({qwen_score}) "
            f"(doubao 多 1 reasoning base + 2 verified*1 — reasoning + reasoning_verified)",
        )


class TestRemainingUnverified(unittest.TestCase):
    """V37.9.55 剩 verified_fallback=False (诚实语义 — 未在生产真 fire)."""

    def setUp(self):
        os.environ.pop("ARK_ENDPOINT_ID", None)
        self.providers = _reload_providers()
        self.d = self.providers.get_provider("doubao")

    def test_verified_fallback_still_false(self):
        """verified_fallback 必须 False 直到生产中作为 fallback 真被调用过.
        不能人为 curl 模拟 (那不是真 fallback fire), 守诚实语义."""
        self.assertFalse(
            self.d.capabilities.verified_fallback,
            "V37.9.55 verified_fallback 守 False (生产真 fire 后才能 V37.9.56+ flip)",
        )

    def test_verified_fallback_not_in_features(self):
        features = self.d.capabilities.verified_features()
        self.assertNotIn(
            "fallback", features,
            "verified_fallback=False 时 features 不应含 fallback 字符串",
        )


class TestPluginSourceV9_55(unittest.TestCase):
    """V37.9.55 source-level guards."""

    @classmethod
    def setUpClass(cls):
        with open(DOUBAO_PLUGIN, encoding="utf-8") as f:
            cls.src = f.read()

    def test_plugin_verified_tool_calling_true(self):
        self.assertRegex(
            self.src,
            r"verified_tool_calling\s*=\s*True",
            "V37.9.55 plugin 必须 verified_tool_calling=True",
        )

    def test_plugin_verified_streaming_true(self):
        self.assertRegex(
            self.src,
            r"verified_streaming\s*=\s*True",
            "V37.9.55 plugin 必须 verified_streaming=True",
        )

    def test_plugin_verified_fallback_still_false(self):
        self.assertRegex(
            self.src,
            r"verified_fallback\s*=\s*False",
            "V37.9.55 守 verified_fallback=False (生产 fire 后再 flip)",
        )

    def test_plugin_has_v37_9_55_marker(self):
        self.assertIn("V37.9.55", self.src)

    def test_plugin_mentions_tool_calls_schema(self):
        """V37.9.55 注释必须说明 tool_calling schema 形式追溯实测来源."""
        self.assertIn("tool_calls", self.src, "V37.9.55 plugin 注释必须含 tool_calls 字面量证据")
        self.assertIn("SSE", self.src, "V37.9.55 plugin 注释必须含 SSE 字面量证据 (streaming)")


class TestV37955VersionMarker(unittest.TestCase):
    def test_version_file_is_v37_9_55(self):
        with open(os.path.join(REPO_ROOT, "VERSION"), encoding="utf-8") as f:
            self.assertEqual(f.read().strip(), "0.37.9.55")


if __name__ == "__main__":
    unittest.main(verbosity=2)
