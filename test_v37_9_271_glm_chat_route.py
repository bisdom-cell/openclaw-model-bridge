#!/usr/bin/env python3
"""V37.9.271 — chat-界面 provider 前缀路由守卫 (unfinished [29]).

用户需求: 在 WhatsApp/Discord 聊天里直接用 GLM-5.2 (前缀 'glm ' → GLM chat,
此前只能 code_assist.sh CLI / ?provider= API 才能调 glm5_coding)。

守卫三层:
  1. detect_provider_prefix 纯函数行为 (命中/未命中/词边界/FAIL-OPEN/单轮语义)
  2. PROVIDER_PREFIX_ROUTES → registry 一致性 (前缀映射到真实 provider)
  3. tool_proxy do_POST wiring 源码守卫 (import / _routed_provider 初始化 /
     strip-tools 条件含前缀路由 / forward URL 加 ?provider=) + forward URL 行为级
     (tool_proxy 顶层 serve_forever 不可 import 的项目惯例 V37.9.132/226/239)。

反向验证 (sabotage): 退回任一 wiring → 对应源码守卫 FAIL (load-bearing)。
"""
import os
import unittest

from proxy_filters import (
    detect_provider_prefix,
    PROVIDER_PREFIX_ROUTES,
    PREFIX_ROUTE_CODING_SYSTEM,
)

_HERE = os.path.dirname(os.path.abspath(__file__))
_TOOL_PROXY = os.path.join(_HERE, "tool_proxy.py")


class TestDetectProviderPrefix(unittest.TestCase):
    """纯函数行为: chat 前缀路由检测。"""

    def test_hit_strips_prefix_replaces_system_drops_history(self):
        p, nm = detect_provider_prefix([
            {"role": "system", "content": "SOUL.md PA 身份 Wei 三方宪法 ..."},
            {"role": "assistant", "content": "之前的 PA 回复"},
            {"role": "user", "content": "glm 写个快排"},
        ])
        self.assertEqual(p, "glm5_coding")
        # 单轮独立: system(coding) + user(剥前缀), 历史/PA 身份被丢弃
        self.assertEqual(len(nm), 2)
        self.assertEqual(nm[0]["role"], "system")
        self.assertEqual(nm[0]["content"], PREFIX_ROUTE_CODING_SYSTEM)
        self.assertNotIn("SOUL", nm[0]["content"])
        self.assertNotIn("Wei", nm[0]["content"])
        self.assertEqual(nm[1]["role"], "user")
        self.assertEqual(nm[1]["content"], "写个快排")

    def test_case_insensitive_and_extra_whitespace(self):
        p, nm = detect_provider_prefix([{"role": "user", "content": "GLM  帮我重构"}])
        self.assertEqual(p, "glm5_coding")
        self.assertEqual(nm[1]["content"], "帮我重构")

    def test_content_blocks_format(self):
        p, nm = detect_provider_prefix(
            [{"role": "user", "content": [{"type": "text", "text": "glm 解释 GIL"}]}]
        )
        self.assertEqual(p, "glm5_coding")
        self.assertEqual(nm[1]["content"], "解释 GIL")

    def test_word_boundary_no_false_match(self):
        # 'glmnet 是什么' 不路由 — 前缀后须跟空白 (词边界)，否则误伤正常提问
        p, _ = detect_provider_prefix([{"role": "user", "content": "glmnet 是什么库"}])
        self.assertIsNone(p)

    def test_bare_prefix_no_query_not_routed(self):
        for txt in ("glm", "glm ", "glm   ", "GLM\t"):
            p, _ = detect_provider_prefix([{"role": "user", "content": txt}])
            self.assertIsNone(p, txt)

    def test_no_prefix_passthrough_unchanged(self):
        msgs = [{"role": "user", "content": "帮我写个快排"}]
        p, nm = detect_provider_prefix(msgs)
        self.assertIsNone(p)
        self.assertEqual(nm, msgs)  # 原样返回, 不改动

    def test_only_last_user_message_checked(self):
        # 前缀在历史 user (非当前轮) → 不路由; 只看最后一条 user
        p, _ = detect_provider_prefix([
            {"role": "user", "content": "glm 旧问题"},
            {"role": "assistant", "content": "..."},
            {"role": "user", "content": "普通后续问题"},
        ])
        self.assertIsNone(p)

    def test_last_user_prefix_after_assistant_routed(self):
        p, nm = detect_provider_prefix([
            {"role": "user", "content": "普通问题"},
            {"role": "assistant", "content": "..."},
            {"role": "user", "content": "glm 现在这个用 GLM 写"},
        ])
        self.assertEqual(p, "glm5_coding")
        self.assertEqual(nm[1]["content"], "现在这个用 GLM 写")

    def test_fail_open_edge_cases(self):
        # 空/非法/无 user/None content → (None, 原样) 不抛异常
        self.assertEqual(detect_provider_prefix([]), (None, []))
        self.assertIsNone(detect_provider_prefix("not a list")[0])
        self.assertIsNone(detect_provider_prefix(None)[0])
        self.assertIsNone(
            detect_provider_prefix([{"role": "assistant", "content": "glm x"}])[0]
        )
        self.assertIsNone(
            detect_provider_prefix([{"role": "user", "content": None}])[0]
        )


class TestRegistryConsistency(unittest.TestCase):
    """PROVIDER_PREFIX_ROUTES → 真实 provider 一致性 (config 漂移守卫)。"""

    def test_routes_non_empty(self):
        self.assertTrue(PROVIDER_PREFIX_ROUTES)

    def test_all_mapped_providers_exist_in_registry(self):
        from providers import PROVIDERS
        for pfx, prov in PROVIDER_PREFIX_ROUTES.items():
            self.assertIn(
                prov, PROVIDERS,
                f"前缀 {pfx!r} 映射到不存在的 provider {prov!r}",
            )

    def test_glm_prefix_maps_to_glm5_coding(self):
        self.assertEqual(PROVIDER_PREFIX_ROUTES.get("glm"), "glm5_coding")


class TestProxyWiringSourceGuards(unittest.TestCase):
    """tool_proxy do_POST wiring 源码守卫 (顶层不可 import — extract-source)。"""

    @classmethod
    def setUpClass(cls):
        with open(_TOOL_PROXY, encoding="utf-8") as f:
            cls.src = f.read()

    def test_imports_detect_provider_prefix(self):
        self.assertIn("detect_provider_prefix", self.src)

    def test_routed_provider_initialized(self):
        # _routed_provider = None 必须在 /chat 块外初始化 (forward URL 在块外读它)
        self.assertRegex(self.src, r"_routed_provider\s*=\s*None")

    def test_hook_called_in_chat_block(self):
        self.assertRegex(
            self.src, r"_routed_provider,\s*_routed_msgs\s*=\s*detect_provider_prefix\("
        )

    def test_strip_tools_condition_includes_route(self):
        # GLM chat 不带 PA 工具: 强制清空工具的条件必须含 _routed_provider
        self.assertRegex(self.src, r"if\s+_routed_provider\s+or\s+should_strip_tools\(")

    def test_forward_url_appends_provider_query(self):
        # 锚定 forward URL 块特有的 _sep 三元 + url 重赋值 (非日志行的
        # provider={_routed_provider} — 否则守卫被 PREFIX ROUTE log 咬, V37.9.178 教训)
        self.assertRegex(self.src, r'_sep\s*=\s*"&"\s+if\s+\("\?"\s+in\s+self\.path\)')
        self.assertRegex(self.src, r'url\s*=\s*f"\{url\}\{_sep\}provider=\{_routed_provider\}"')

    def test_marker_present(self):
        self.assertIn("V37.9.271", self.src)


class TestForwardUrlBehavior(unittest.TestCase):
    """行为级: 复现 do_POST forward URL 构造 (docs 意图 + 与源码守卫互证)。"""

    @staticmethod
    def _build_url(backend, path, routed_provider):
        url = f"{backend}{path}"
        if routed_provider:
            sep = "&" if ("?" in path) else "?"
            url = f"{url}{sep}provider={routed_provider}"
        return url

    def test_appends_when_routed_no_existing_query(self):
        self.assertEqual(
            self._build_url("http://127.0.0.1:5001", "/v1/chat/completions", "glm5_coding"),
            "http://127.0.0.1:5001/v1/chat/completions?provider=glm5_coding",
        )

    def test_appends_with_existing_query(self):
        self.assertEqual(
            self._build_url(
                "http://127.0.0.1:5001", "/v1/chat/completions?foo=1", "glm5_coding"
            ),
            "http://127.0.0.1:5001/v1/chat/completions?foo=1&provider=glm5_coding",
        )

    def test_no_append_when_not_routed(self):
        self.assertEqual(
            self._build_url("http://127.0.0.1:5001", "/v1/chat/completions", None),
            "http://127.0.0.1:5001/v1/chat/completions",
        )


if __name__ == "__main__":
    unittest.main()
