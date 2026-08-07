"""V37.9.53 — Doubao Seed 2.0 Pro flip verified + reasoning capability 落地

V37.9.52 接入 doubao 但 verified_*=False (未实战). V37.9.53 基于 Mac Mini E2E
真实数据 (curl /chat/completions → HTTP 200 + 标准 OpenAI 兼容 JSON + reasoning_content)
flip verified_text=True + reasoning=True + verified_reasoning=True.

V37.9.53 涉及 framework 扩展:
- ProviderCapabilities 加 reasoning + verified_reasoning 字段
- verified_features() 包含 reasoning
- _capability_score 加 reasoning (+1) + verified_reasoning (+2)
- _capability_overlap + capability_overlap (公开 API) 加 reasoning 维度

效果: doubao cap_score 从 V37.9.52 的 5 升至 V37.9.53 的 11
(5 base caps + reasoning + 2 verified × 2 = 11), 自动排到 fallback chain
第 1 位取代 Gemini, 与用户"与 Qwen3 同级"预期对齐 — 通过 framework
自然推导而非硬编码.

覆盖范围:
1. TestReasoningCapabilityField: ProviderCapabilities.reasoning + verified_reasoning 字段存在 + 默认 False
2. TestDoubaoVerifiedFlagsV9_53: doubao verified_text=True / verified_reasoning=True / reasoning=True
3. TestDoubaoCapScoreRanking: doubao cap_score > gemini cap_score (新 fallback chain 排序)
4. TestVerifiedFeaturesIncludesReasoning: verified_features() 含 reasoning 字符串
5. TestFallbackChainDoubaoFirst: build_fallback_chain(qwen) doubao 排第 1
6. TestSourceLevelGuardsV9_53: framework + plugin 源码字面量守卫 (verified=True + reasoning=True)
"""
import importlib
import os
import re
import sys
import unittest


REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
DOUBAO_PLUGIN = os.path.join(REPO_ROOT, "providers.d", "doubao_provider.py")
PROVIDERS_PY = os.path.join(REPO_ROOT, "providers.py")


def _reload_providers():
    if "providers" in sys.modules:
        del sys.modules["providers"]
    return importlib.import_module("providers")


class TestReasoningCapabilityField(unittest.TestCase):
    """ProviderCapabilities 加 reasoning + verified_reasoning 字段 (V37.9.53 framework 扩展)."""

    def setUp(self):
        os.environ.pop("ARK_ENDPOINT_ID", None)
        self.providers = _reload_providers()

    def test_reasoning_field_default_false(self):
        caps = self.providers.ProviderCapabilities()
        self.assertFalse(caps.reasoning, "新字段 reasoning 默认 False (向后兼容 7 个 built-in)")

    def test_verified_reasoning_field_default_false(self):
        caps = self.providers.ProviderCapabilities()
        self.assertFalse(
            caps.verified_reasoning,
            "新字段 verified_reasoning 默认 False (向后兼容 7 个 built-in)",
        )

    def test_existing_7_providers_have_reasoning_false(self):
        """V37.9.53 引入 reasoning 不能破坏 7 个 built-in provider 行为."""
        for name in ("qwen", "openai", "gemini", "claude", "kimi", "minimax", "glm"):
            p = self.providers.get_provider(name)
            self.assertIsNotNone(p)
            self.assertFalse(
                p.capabilities.reasoning,
                f"{name} reasoning 必须默认 False (V37.9.53 不影响 built-in 行为)",
            )
            self.assertFalse(
                p.capabilities.verified_reasoning,
                f"{name} verified_reasoning 必须默认 False",
            )


class TestDoubaoVerifiedFlagsV9_53(unittest.TestCase):
    """V37.9.53: doubao flip verified_text + reasoning + verified_reasoning."""

    def setUp(self):
        os.environ.pop("ARK_ENDPOINT_ID", None)
        self.providers = _reload_providers()
        self.d = self.providers.get_registry().get("doubao")

    def test_verified_text_reset_v290(self):
        """V37.9.53 Ark 实测 True → V37.9.290 平台切换 ai-tokenhub 后重置 False
        (证据不迁移, E2E 复测后再 flip)."""
        self.assertFalse(
            self.d.capabilities.verified_text,
            "V37.9.290 平台切换后 verified_text 必须重置 False 待复测",
        )

    def test_reasoning_is_true(self):
        """V37.9.53: doubao seed 2.0 是 reasoning model (响应含 reasoning_content)."""
        self.assertTrue(
            self.d.capabilities.reasoning,
            "V37.9.53 reasoning 必须 True (响应含 reasoning_content 字段)",
        )

    def test_verified_reasoning_reset_v290(self):
        """V37.9.290 平台切换后重置 False (Ark 时代证据不迁移)."""
        self.assertFalse(
            self.d.capabilities.verified_reasoning,
            "V37.9.290 平台切换后 verified_reasoning 必须重置 False 待复测",
        )

    def test_unverified_flags_still_false(self):
        """V37.9.53 flip text+reasoning, V37.9.54 flip vision, V37.9.55 flip tool_calling+streaming.
        仅 verified_fallback 仍 False 等生产真 fire (V37.9.56+)."""
        c = self.d.capabilities
        # verified_tool_calling/streaming 已 V37.9.55 flip True
        # verified_vision 已 V37.9.54 flip True
        self.assertFalse(c.verified_fallback, "未在生产 fallback 真 fire, 留 V37.9.56+")


class TestDoubaoCapScoreRanking(unittest.TestCase):
    """V37.9.53 后 doubao cap_score > gemini (取代 fallback chain 第 1 位)."""

    def setUp(self):
        os.environ.pop("ARK_ENDPOINT_ID", None)
        self.providers = _reload_providers()
        self.reg = self.providers.get_registry()

    def test_doubao_cap_score_below_gemini_after_reset_v290(self):
        doubao = self.reg.get("doubao")
        gemini = self.reg.get("gemini")
        doubao_score = self.reg._capability_score(doubao)
        gemini_score = self.reg._capability_score(gemini)
        # V37.9.290 平台切换重置 verified → doubao 6 base + 0 verified = 6 <
        # gemini 9 (5 base + 2 verified*2)。E2E 复测 flip 后此关系将再反转。
        self.assertLess(
            doubao_score, gemini_score,
            f"V37.9.290 重置后 doubao cap_score ({doubao_score}) 应 < gemini ({gemini_score})",
        )

    def test_doubao_cap_score_specific_value(self):
        doubao = self.reg.get("doubao")
        score = self.reg._capability_score(doubao)
        # 史: V37.9.53=10 → V37.9.54=12 → V37.9.55=16 (Ark 时代逐项 E2E flip)
        # V37.9.290 平台切换 ai-tokenhub → verified 全重置 → 6 (6 base + 0 verified)
        self.assertEqual(
            score, 6,
            f"V37.9.290 重置后 doubao cap_score 锁定 6 (6 base + 0 verified), got {score}",
        )

    def test_gemini_cap_score_unchanged(self):
        """V37.9.53 不应影响 gemini cap_score (向后兼容)."""
        gemini = self.reg.get("gemini")
        score = self.reg._capability_score(gemini)
        # 5 base (text+vision+tool_calling+streaming+json_mode) + 2 verified*2 = 9
        self.assertEqual(score, 9, f"gemini cap_score 应保持 9, got {score}")


class TestVerifiedFeaturesIncludesReasoning(unittest.TestCase):
    """verified_features() 返回值含 reasoning 字符串."""

    def setUp(self):
        os.environ.pop("ARK_ENDPOINT_ID", None)
        self.providers = _reload_providers()

    def test_doubao_verified_features_empty_after_reset_v290(self):
        d = self.providers.get_provider("doubao")
        features = d.capabilities.verified_features()
        self.assertNotIn("text", features, "V37.9.290 重置后 features 不应含 text")
        self.assertNotIn("reasoning", features, "V37.9.290 重置后 features 不应含 reasoning")

    def test_doubao_verified_features_exact_v290(self):
        d = self.providers.get_provider("doubao")
        features = d.capabilities.verified_features()
        # 史: V37.9.55 曾锁定 5 项 (Ark E2E); V37.9.290 平台切换全重置
        self.assertEqual(
            set(features), set(),
            f"V37.9.290 重置后 doubao verified_features 锁定空集, got {features}",
        )

    def test_other_providers_no_reasoning_in_verified(self):
        for name in ("qwen", "openai", "gemini", "claude", "kimi", "minimax", "glm"):
            p = self.providers.get_provider(name)
            self.assertNotIn(
                "reasoning", p.capabilities.verified_features(),
                f"{name} verified_features 不应含 reasoning (V37.9.53 仅 doubao 声明)",
            )


class TestFallbackChainDoubaoFirst(unittest.TestCase):
    """V37.9.53 doubao 自动排到 fallback chain 第 1 位 (取代 gemini)."""

    def setUp(self):
        os.environ.pop("ARK_ENDPOINT_ID", None)
        self.providers = _reload_providers()
        self.reg = self.providers.get_registry()

    def test_doubao_21_first_in_qwen_fallback_chain_v290(self):
        """史: V37.9.53 doubao(2.0) 曾第 1; V37.9.217 doubao_21 (16) 登顶;
        V37.9.290 doubao(2.0) verified 重置 (6) 沉到 gemini 之后。
        生产不受影响: FALLBACK_ORDER env 权威 (V37.9.218) + gemini config 排除。"""
        chain = self.reg.build_fallback_chain("qwen")
        names = [p.name for p in chain]
        self.assertEqual(
            names[0], "doubao_21",
            f"doubao_21 (16, 5 verified) 应排 chain 第 1 位, got {names}",
        )
        self.assertNotEqual(names[0], "doubao")

    def test_gemini_below_verified_doubao_in_qwen_fallback_chain(self):
        # V37.9.53 原意: doubao(2.0) verified reasoning → 排在 gemini 之前 (把 gemini 挤下去)。
        # V37.9.217: doubao_21 (旗舰 5 verified 含 reasoning) 加入后也高于 gemini(2 verified),
        # gemini 被两个 verified doubao 挤到更后 → 断言 verified doubao 均排在 gemini 之前
        # (保留原意, 去脆弱位置字面量)。
        chain = self.reg.build_fallback_chain("qwen")
        names = [p.name for p in chain]
        # V37.9.290: doubao(2.0) verified 重置后沉到 gemini 之后 (6 < 9);
        # doubao_21 (16, 5 verified) 仍排 gemini 之前。原断言方向随重置反转。
        self.assertGreater(names.index("doubao"), names.index("gemini"),
                           f"V37.9.290 重置后 doubao(2.0) 应排 gemini 之后, got {names}")
        self.assertLess(names.index("doubao_21"), names.index("gemini"),
                        f"doubao_21 (5 verified) 应排在 gemini 之前, got {names}")


class TestSourceLevelGuardsV9_53(unittest.TestCase):
    """V37.9.53 source-level guards 防 framework + plugin 关键改动被重构破坏."""

    @classmethod
    def setUpClass(cls):
        with open(PROVIDERS_PY, encoding="utf-8") as f:
            cls.providers_src = f.read()
        with open(DOUBAO_PLUGIN, encoding="utf-8") as f:
            cls.plugin_src = f.read()

    def test_providers_py_has_reasoning_field(self):
        """V37.9.53 framework: ProviderCapabilities 必须有 reasoning 字段定义."""
        self.assertIn("reasoning: bool = False", self.providers_src)

    def test_providers_py_has_verified_reasoning_field(self):
        self.assertIn("verified_reasoning: bool = False", self.providers_src)

    def test_providers_py_has_v37_9_53_marker(self):
        self.assertIn("V37.9.53", self.providers_src)

    def test_capability_score_includes_reasoning(self):
        """_capability_score 必须含 reasoning 维度 + verified_reasoning."""
        # 找 _capability_score 函数体
        match = re.search(
            r"def _capability_score\(self.*?return score",
            self.providers_src, re.DOTALL,
        )
        self.assertIsNotNone(match)
        body = match.group(0)
        self.assertIn("'reasoning'", body, "_capability_score 缺 reasoning 加分")
        self.assertIn("'verified_reasoning'", body, "_capability_score 缺 verified_reasoning 加分")

    def test_verified_features_includes_reasoning(self):
        """verified_features() 必须输出 reasoning 字符串."""
        match = re.search(
            r"def verified_features\(self.*?return features",
            self.providers_src, re.DOTALL,
        )
        self.assertIsNotNone(match)
        body = match.group(0)
        self.assertIn('features.append("reasoning")', body)

    def test_doubao_plugin_verified_text_reset_v290(self):
        """V37.9.53 曾 True (Ark 实测) → V37.9.290 平台切换重置 False 待复测."""
        self.assertRegex(
            self.plugin_src,
            r"verified_text\s*=\s*False",
            "V37.9.290 doubao_provider.py 必须 verified_text=False (平台切换重置)",
        )

    def test_doubao_plugin_reasoning_true(self):
        self.assertRegex(
            self.plugin_src,
            r"reasoning\s*=\s*True",
            "V37.9.53 doubao 必须声明 reasoning=True",
        )

    def test_doubao_plugin_verified_reasoning_reset_v290(self):
        self.assertRegex(
            self.plugin_src,
            r"verified_reasoning\s*=\s*False",
            "V37.9.290 doubao 必须 verified_reasoning=False (平台切换重置)",
        )

    def test_doubao_plugin_has_v9_53_marker(self):
        """plugin 头注释含 V37.9.53 升级说明 (追溯性)."""
        self.assertIn("V37.9.53", self.plugin_src)
        self.assertIn(
            "Mac Mini", self.plugin_src,
            "V37.9.53 plugin 必须说明实测来源 (Mac Mini E2E)",
        )

    def test_doubao_plugin_unverified_flags_still_false(self):
        """V37.9.53 flip text+reasoning. V37.9.54 flip vision.
        V37.9.55 flip tool_calling+streaming. 仅 verified_fallback 仍 False."""
        # 仅 verified_fallback 未 flip — 守 False 等 V37.9.56+ 生产真 fire
        self.assertRegex(
            self.plugin_src,
            r"verified_fallback\s*=\s*False",
            "守 verified_fallback=False (未在生产真 fire, V37.9.56+ flip)",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
