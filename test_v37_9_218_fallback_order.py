"""V37.9.218 — FALLBACK_ORDER 显式有序 fallback 链 + capability-aware vision fallback.

用户偏好顺序 doubao_21 > deepseek_full > doubao > deepseek > qwen(逐步老化):
- cap_score 自动排**排不出** deepseek_full > doubao (doubao 5 verified > deepseek_full 3),
  且旧机制只有 1 个显式槽 (FALLBACK_PROVIDER) → 需要显式有序列表 (一物一形, 替代单槽)。
- FALLBACK_ORDER = 逗号分隔有序 provider 名, 权威 (覆盖 cap_score + 旧单槽)。
- primary 自动排除 (可传完整偏好, 切换 primary 无需改 FALLBACK_ORDER)。
- capability-aware: image 请求跳过纯文本 provider (无 vl_model_id), 用 vision-capable 的 vl 模型。
"""
import os
import re
import unittest
from unittest import mock

import adapter

_ADAPTER_SRC = os.path.join(os.path.dirname(__file__), "adapter.py")
# 5 provider 全给 fake key → 都 available (dev 无真 key)
_ALL_KEYS = {
    "REMOTE_API_KEY": "k", "ARK_21_API_KEY": "k", "DEEPSEEK_FULL_API_KEY": "k",
    "ARK_API_KEY": "k", "DEEPSEEK_API_KEY": "k",
}


def _chain(order, primary="qwen", keys=None, extra=None, exclude=None):
    """调 _build_fallback_chain, 受控 FALLBACK_ORDER + primary + keys."""
    env = {"FALLBACK_ORDER": order}
    env.update(keys if keys is not None else _ALL_KEYS)
    if extra:
        env.update(extra)
    # 清掉可能残留的 FALLBACK_PROVIDER (除非 extra 显式设)
    with mock.patch.dict(os.environ, env, clear=False):
        if "FALLBACK_PROVIDER" not in env:
            os.environ.pop("FALLBACK_PROVIDER", None)
        with mock.patch.object(adapter, "PROVIDER_NAME", primary):
            if exclude is not None:
                with mock.patch.object(adapter, "_FALLBACK_EXCLUDE", set(exclude)):
                    return adapter._build_fallback_chain()
            return adapter._build_fallback_chain()


class TestFallbackOrderMechanism(unittest.TestCase):
    """FALLBACK_ORDER 显式有序机制核心行为。"""

    def test_exact_user_order_qwen_primary(self):
        # 用户顺序; qwen primary → qwen 自动排除 → 精确 4 元链
        names = [fb["name"] for fb in _chain("doubao_21,deepseek_full,doubao,deepseek,qwen")]
        self.assertEqual(names, ["doubao_21", "deepseek_full", "doubao", "deepseek"])

    def test_order_defies_cap_score(self):
        # 关键: cap_score 排不出 deepseek_full(3 verified) 在 doubao(5 verified) 之前;
        # FALLBACK_ORDER 让它成立 = 显式有序的价值。
        names = [fb["name"] for fb in _chain("doubao_21,deepseek_full,doubao,deepseek,qwen")]
        self.assertLess(names.index("deepseek_full"), names.index("doubao"),
                        "FALLBACK_ORDER 必须让 deepseek_full 排在 doubao 之前 (cap_score 做不到)")

    def test_primary_auto_excluded_flip_safe(self):
        # 切 primary=doubao_21, 同一 FALLBACK_ORDER → doubao_21 排除, qwen 进链末尾
        # (切换 primary 无需改 FALLBACK_ORDER — flip-safe)。
        names = [fb["name"] for fb in
                 _chain("doubao_21,deepseek_full,doubao,deepseek,qwen", primary="doubao_21")]
        self.assertEqual(names, ["deepseek_full", "doubao", "deepseek", "qwen"])

    def test_unavailable_no_key_skipped(self):
        # 只给 doubao_21 + doubao key → deepseek/deepseek_full 无 key 跳过
        names = [fb["name"] for fb in _chain(
            "doubao_21,deepseek_full,doubao,deepseek",
            keys={"REMOTE_API_KEY": "k", "ARK_21_API_KEY": "k", "ARK_API_KEY": "k"})]
        self.assertEqual(names, ["doubao_21", "doubao"])

    def test_unknown_provider_skipped(self):
        names = [fb["name"] for fb in _chain("doubao_21,nonexistent_xyz,doubao")]
        self.assertEqual(names, ["doubao_21", "doubao"])

    def test_fallback_exclude_respected(self):
        # _FALLBACK_EXCLUDE (geo-block) 里的 provider 跳过
        names = [fb["name"] for fb in
                 _chain("doubao_21,deepseek_full,doubao", exclude={"deepseek_full"})]
        self.assertEqual(names, ["doubao_21", "doubao"])

    def test_dedup_preserves_order(self):
        names = [fb["name"] for fb in _chain("doubao_21,doubao,doubao_21,deepseek")]
        self.assertEqual(names, ["doubao_21", "doubao", "deepseek"])

    def test_precedence_over_legacy_single_slot(self):
        # FALLBACK_ORDER 与 FALLBACK_PROVIDER 同时设 → FALLBACK_ORDER 权威, 单槽忽略
        names = [fb["name"] for fb in _chain(
            "doubao_21,deepseek_full", extra={"FALLBACK_PROVIDER": "doubao"})]
        self.assertEqual(names, ["doubao_21", "deepseek_full"])
        self.assertNotIn("doubao", names, "FALLBACK_PROVIDER=doubao 应被忽略")

    def test_empty_order_falls_through_to_legacy(self):
        # FALLBACK_ORDER 空 → 走 legacy (cap_score + FALLBACK_PROVIDER), 不报错
        with mock.patch.dict(os.environ, {**_ALL_KEYS}, clear=False):
            os.environ.pop("FALLBACK_ORDER", None)
            os.environ.pop("FALLBACK_PROVIDER", None)
            chain = adapter._build_fallback_chain()
        # legacy 路径至少产出非空链 (5 provider 都有 fake key)
        self.assertTrue(all("vl_model_id" in fb for fb in chain),
                        "legacy 路径的 entry 也必须带 vl_model_id (V37.9.218)")


class TestVisionCapableEntries(unittest.TestCase):
    """entry.vl_model_id 用于 capability-aware vision fallback。"""

    def test_vision_providers_have_vl_model_id(self):
        by_name = {fb["name"]: fb for fb in
                   _chain("doubao_21,doubao,deepseek_full,deepseek,qwen", primary="openai",
                          keys={**_ALL_KEYS, "OPENAI_API_KEY": "k"})}
        # doubao/doubao_21 单模型多模态 → vl_model_id 非空
        self.assertTrue(by_name["doubao_21"]["vl_model_id"])
        self.assertTrue(by_name["doubao"]["vl_model_id"])

    def test_text_only_providers_have_empty_vl_model_id(self):
        by_name = {fb["name"]: fb for fb in
                   _chain("deepseek_full,deepseek,doubao_21")}
        # 纯文本 provider → vl_model_id 空 → image 请求会被跳过
        self.assertEqual(by_name["deepseek_full"]["vl_model_id"], "")
        self.assertEqual(by_name["deepseek"]["vl_model_id"], "")


class TestCapabilityAwareSkipSourceGuards(unittest.TestCase):
    """fallback 循环 capability-aware 跳过逻辑 (源码级守卫)。"""

    @classmethod
    def setUpClass(cls):
        cls.src = open(_ADAPTER_SRC, encoding="utf-8").read()

    def test_loop_skips_text_only_for_image(self):
        # has_multimodal + 无 vl_model_id → continue (跳过纯文本 provider)
        self.assertIn("if has_multimodal:", self.src)
        self.assertRegex(self.src, r'fb_vl\s*=\s*fb\.get\("vl_model_id"')
        self.assertIn("if not fb_vl:", self.src)

    def test_loop_uses_vl_model_for_vision(self):
        # image 请求用 fb_vl (vl 模型) 而非 model_id
        self.assertIn("fb_model = fb_vl", self.src)
        self.assertIn('fb_model = fb["model_id"]', self.src)
        self.assertIn('fb_clean["model"] = fb_model', self.src)

    def test_skip_logic_has_continue(self):
        # 定位 has_multimodal 跳过块必须有 continue (不 break, 继续下一个)
        m = re.search(r"if has_multimodal:\s*\n\s*fb_vl.*?continue", self.src, re.DOTALL)
        self.assertIsNotNone(m, "capability-aware 跳过块缺 continue")

    def test_skip_records_error_reason(self):
        self.assertIn("skipped (text-only, image request)", self.src)


class TestV37_9_218_SourceGuards(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.src = open(_ADAPTER_SRC, encoding="utf-8").read()

    def test_marker_present(self):
        self.assertIn("V37.9.218", self.src)

    def test_fallback_order_env_read(self):
        self.assertIn('os.environ.get("FALLBACK_ORDER"', self.src)

    def test_entry_helper_defined(self):
        self.assertIn("def _entry_from_registry(", self.src)
        self.assertIn('"vl_model_id"', self.src)

    def test_primary_excluded_in_order_path(self):
        # FALLBACK_ORDER 路径显式排除 primary
        self.assertIn("name == PROVIDER_NAME", self.src)

    def test_precedence_warn_present(self):
        self.assertIn("FALLBACK_ORDER 优先", self.src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
