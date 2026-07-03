"""V37.9.77 Capability Router Enforcement (?provider=X query param) — 单测.

V37.9.76 (shadow mode) → V37.9.77 (enforcement opt-in via ROUTER_ENFORCE env).

覆盖三个层面:
1. adapter.py _resolve_primary_provider 纯函数 (?provider=X query 解析 + FAIL-OPEN)
2. kb_dream.sh RADAR retry 真传 ?provider= 集成 (llm_call 6th 参数 + ROUTER_ENFORCE 门控)
3. router_decide.py mode 字段反映 ROUTER_ENFORCE env 状态 (shadow / on)

设计原则:
- ROUTER_ENFORCE=off (默认): V37.9.76 shadow 行为完全保留 (向后兼容)
- ROUTER_ENFORCE=on: 真路由切换, 但 adapter 不识别/缺 API key 仍 FAIL-OPEN 回 default
- 反向 sabotage 守卫真有效
"""

import json
import os
import subprocess
import sys
import unittest
from unittest.mock import patch, MagicMock


_REPO_ROOT = os.path.dirname(os.path.abspath(__file__))


class TestAdapterResolvePrimaryProvider(unittest.TestCase):
    """V37.9.77 Step 1: adapter._resolve_primary_provider 纯函数测试.

    避免启动 HTTP server, 直接 mock self.path 调 _resolve_primary_provider.
    """

    def setUp(self):
        # Reset modules to ensure clean state
        for m in ("adapter", "providers"):
            sys.modules.pop(m, None)
        sys.path.insert(0, _REPO_ROOT)
        # V37.9.232 (2026-07-03 治理红灯根因): 强制基线 env — 原 setdefault 在
        # 生产 env 已有 PROVIDER=doubao_21 (V37.9.222 flip) 时无效 → import adapter
        # 得 doubao_21 → 6 个断言 name=="qwen" 的测试全挂 → INV-ROUTER-001 ❌
        # (dev 无此 env 永远绿)。patch.dict 快照 + 显式 pop 路由类 env, stop 时
        # 整体还原 (hermetic, 不污染进程 env)。
        self._env = patch.dict(os.environ, {
            "REMOTE_API_KEY": "test-key-v37976",
            "PROVIDER": "qwen",
        })
        self._env.start()
        self.addCleanup(self._env.stop)
        for k in ("FALLBACK_ORDER", "FALLBACK_PROVIDER", "FAST_PROVIDER",
                  "MODEL_ID", "VL_MODEL_ID", "ROUTER_ENFORCE"):
            os.environ.pop(k, None)
        # Don't actually import adapter (it would start HTTP server in __main__)
        # Instead, test the _resolve_primary_provider method by importing without running

    def _make_handler_stub(self, path: str, enforce: str = "off"):
        """构造一个 ProxyHandler 实例 stub (不启动 HTTP server)."""
        # 用 monkey-patch ROUTER_ENFORCE 测试不同 env 状态
        with patch.dict(os.environ, {"ROUTER_ENFORCE": enforce}):
            import adapter
            # Create a stub instance without going through __init__
            handler = adapter.ProxyHandler.__new__(adapter.ProxyHandler)
            handler.path = path
            return handler._resolve_primary_provider()

    def test_no_override_returns_default_provider(self):
        """无 ?provider= → 返回默认 (PROVIDER_NAME 全局, ROUTER_ENFORCE 无影响)."""
        result = self._make_handler_stub("/v1/chat/completions", enforce="off")
        base, model, auth, key, name = result
        # 默认 PROVIDER=qwen
        self.assertEqual(name, "qwen",
                         "V37.9.77: 无 ?provider= 时返回默认 PROVIDER_NAME")

    def test_router_enforce_off_ignores_override(self):
        """ROUTER_ENFORCE=off (默认) → 即使有 ?provider= 也走默认 (PoC 安全网).

        必须配 ARK_API_KEY 隔离测试 — 否则第二层 API key 检查会同时阻止,
        让测试无法分辨是哪个 guard 起作用 (反向 sabotage 守卫失效).
        """
        with patch.dict(os.environ, {"ARK_API_KEY": "test-doubao-key-v37977-isolation"}):
            result = self._make_handler_stub("/v1/chat/completions?provider=doubao", enforce="off")
            _, _, _, _, name = result
            self.assertEqual(name, "qwen",
                             "V37.9.77: ROUTER_ENFORCE=off 时忽略 ?provider= 强制走默认 "
                             "(即使 ARK_API_KEY 配置存在)")

    def test_router_enforce_on_with_valid_override(self):
        """ROUTER_ENFORCE=on + ?provider=doubao + ARK_API_KEY 配置 → 用 doubao."""
        with patch.dict(os.environ, {"ARK_API_KEY": "test-doubao-key-v37977"}):
            result = self._make_handler_stub("/v1/chat/completions?provider=doubao", enforce="on")
            base, model, auth, key, name = result
            self.assertEqual(name, "doubao",
                             "V37.9.77: ROUTER_ENFORCE=on + 有效 ?provider=doubao → 用 doubao")
            self.assertIn("volces.com", base.lower(),
                          "V37.9.77: doubao base_url 应是 Volcengine Ark")
            self.assertEqual(key, "test-doubao-key-v37977",
                             "V37.9.77: doubao 应用 ARK_API_KEY")

    def test_router_enforce_on_missing_api_key_falls_back(self):
        """ROUTER_ENFORCE=on + ?provider=doubao 但 ARK_API_KEY 未配 → fallback default (FAIL-OPEN)."""
        with patch.dict(os.environ, {}, clear=False):
            # 清掉 ARK_API_KEY
            os.environ.pop("ARK_API_KEY", None)
            result = self._make_handler_stub("/v1/chat/completions?provider=doubao", enforce="on")
            _, _, _, _, name = result
            self.assertEqual(name, "qwen",
                             "V37.9.77 FAIL-OPEN: 选了未配 API key 的 provider 应回 default")

    def test_router_enforce_on_unknown_provider_falls_back(self):
        """ROUTER_ENFORCE=on + ?provider=nonexistent → fallback default (FAIL-OPEN silent)."""
        result = self._make_handler_stub("/v1/chat/completions?provider=nonexistent_xyz", enforce="on")
        _, _, _, _, name = result
        self.assertEqual(name, "qwen",
                         "V37.9.77 FAIL-OPEN: ?provider=未注册 provider → silent fallback default")

    def test_empty_provider_query_falls_back(self):
        """ROUTER_ENFORCE=on + ?provider= (空值) → fallback default."""
        result = self._make_handler_stub("/v1/chat/completions?provider=", enforce="on")
        _, _, _, _, name = result
        self.assertEqual(name, "qwen",
                         "V37.9.77: 空 ?provider= 值 → fallback default")

    def test_malformed_query_string_does_not_crash(self):
        """Malformed query string → 不抛异常, FAIL-OPEN 回 default."""
        # urlparse 应该宽容处理任何输入
        for bad_path in ["/v1/chat/completions?", "/v1/chat/completions?%%", "/v1/chat/completions?provider"]:
            result = self._make_handler_stub(bad_path, enforce="on")
            _, _, _, _, name = result
            self.assertEqual(name, "qwen",
                             f"V37.9.77: malformed path '{bad_path}' 应 FAIL-OPEN")


class TestKbDreamShellEnforcementIntegration(unittest.TestCase):
    """V37.9.77 Step 2: kb_dream.sh RADAR retry ?provider= 集成守卫."""

    def setUp(self):
        with open(os.path.join(_REPO_ROOT, "kb_dream.sh")) as f:
            self.src = f.read()

    def test_llm_call_signature_has_provider_override_6th_arg(self):
        """V37.9.77: llm_call 必须接受 6th 参数 provider_override."""
        self.assertIn('local provider_override="${6:-}"', self.src,
                      "V37.9.77: llm_call 必须有 6th 参数 provider_override")

    def test_llm_call_builds_effective_url_with_provider_query(self):
        """V37.9.77: llm_call 必须在 provider_override 非空时附加 ?provider= 到 URL."""
        self.assertIn('effective_url="${LLM_URL}?provider=${provider_override}"', self.src,
                      "V37.9.77: provider_override 时必须拼 ?provider= query 字符串")

    def test_curl_uses_effective_url_not_llm_url_const(self):
        """V37.9.77: curl 必须用 effective_url 不能用 LLM_URL 常量 (否则 override 失效)."""
        self.assertIn('"$effective_url"', self.src,
                      "V37.9.77: curl 必须用 effective_url 让 ?provider= 生效")

    def test_router_enforce_gate_in_radar_retry(self):
        """V37.9.77: RADAR retry 必须有 ROUTER_ENFORCE=on 门控才传 ?provider= (默认 off 安全)."""
        self.assertIn('ROUTER_ENFORCE:-off', self.src,
                      "V37.9.77: 必须用 ROUTER_ENFORCE env feature flag, 默认 off")
        # 验证 RADAR retry 后续调用 llm_call 时传 EFFECTIVE_PROVIDER 第 6 参数
        retry_call_idx = self.src.find('RADAR_RETRY_RESULT=$(llm_call "$RADAR_RETRY_PROMPT"')
        self.assertGreater(retry_call_idx, 0)
        # 检查这个 llm_call 行末尾含 $EFFECTIVE_PROVIDER
        retry_line_end = self.src.find('\n', retry_call_idx)
        retry_call_line = self.src[retry_call_idx:retry_line_end]
        self.assertIn('"$EFFECTIVE_PROVIDER"', retry_call_line,
                      "V37.9.77: llm_call RADAR retry 必须传 EFFECTIVE_PROVIDER 第 6 参数")

    def test_effective_provider_filtered_invalid_router_responses(self):
        """V37.9.77: EFFECTIVE_PROVIDER 必须排除 router 的 'unknown' / 'no_router_profile' 等错误值."""
        # 这些 sentinel 值不应被当作合法 provider 名传给 adapter
        self.assertIn('!= "unknown"', self.src,
                      "V37.9.77: 必须过滤 unknown sentinel")
        self.assertIn('!= "no_router_profile"', self.src,
                      "V37.9.77: 必须过滤 no_router_profile sentinel")
        self.assertIn('!= "no_matching_provider"', self.src,
                      "V37.9.77: 必须过滤 no_matching_provider sentinel")

    def test_default_behavior_preserves_v37_9_76_shadow(self):
        """V37.9.77 向后兼容: ROUTER_ENFORCE 默认 off 时, 行为同 V37.9.76 shadow log."""
        # log "V37.9.76 router (shadow):" 字面量必须保留 (默认 off 路径)
        self.assertIn("V37.9.76 router (shadow):", self.src,
                      "V37.9.77 向后兼容: 默认 off 时保留 V37.9.76 shadow 日志")


class TestRouterDecideModeReflectsRouterEnforce(unittest.TestCase):
    """V37.9.77 Step 4: router_decide.py mode 字段反映 ROUTER_ENFORCE env."""

    def setUp(self):
        for m in ("router_decide", "providers"):
            sys.modules.pop(m, None)
        sys.path.insert(0, _REPO_ROOT)

    def test_resolve_mode_default_off_returns_shadow(self):
        """ROUTER_ENFORCE 未设/off → mode='shadow'."""
        import router_decide
        with patch.dict(os.environ, {"ROUTER_ENFORCE": "off"}):
            self.assertEqual(router_decide._resolve_mode(), "shadow")
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ROUTER_ENFORCE", None)
            self.assertEqual(router_decide._resolve_mode(), "shadow",
                             "V37.9.77: ROUTER_ENFORCE 未设 → shadow 默认")

    def test_resolve_mode_on_returns_on(self):
        """ROUTER_ENFORCE=on/true/1/yes (大小写不敏感) → mode='on'."""
        import router_decide
        for val in ("on", "ON", "On", "true", "True", "1", "yes", "Yes"):
            with patch.dict(os.environ, {"ROUTER_ENFORCE": val}):
                self.assertEqual(router_decide._resolve_mode(), "on",
                                 f"V37.9.77: ROUTER_ENFORCE={val} → on")

    def test_resolve_mode_unknown_value_falls_back_shadow(self):
        """ROUTER_ENFORCE=任意未知值 → fallback shadow (FAIL-CLOSED, 不轻易启用)."""
        import router_decide
        for val in ("maybe", "garbage", "TRUEFALSE", "2"):
            with patch.dict(os.environ, {"ROUTER_ENFORCE": val}):
                self.assertEqual(router_decide._resolve_mode(), "shadow",
                                 f"V37.9.77: 未知值 {val} 应回 shadow 安全默认")

    def test_record_mode_field_uses_resolve_mode(self):
        """decide() record.mode 字段必须用 _resolve_mode() 而非硬编码 'shadow'."""
        import router_decide
        with patch.dict(os.environ, {"ROUTER_ENFORCE": "on"}):
            record = router_decide.decide(
                job_id="kb_dream", task="test", require_available=False
            )
            self.assertEqual(record["mode"], "on",
                             "V37.9.77: ROUTER_ENFORCE=on 时 record.mode 应为 on")
        with patch.dict(os.environ, {"ROUTER_ENFORCE": "off"}):
            record = router_decide.decide(
                job_id="kb_dream", task="test", require_available=False
            )
            self.assertEqual(record["mode"], "shadow",
                             "V37.9.77: ROUTER_ENFORCE=off 时 record.mode 应为 shadow")


class TestAdapterSourceLevelGuards(unittest.TestCase):
    """V37.9.77 Step 1: adapter.py 源码级 V37.9.77 守卫."""

    def setUp(self):
        with open(os.path.join(_REPO_ROOT, "adapter.py")) as f:
            self.src = f.read()

    def test_resolve_primary_provider_function_defined(self):
        """V37.9.77: _resolve_primary_provider 方法必须定义."""
        self.assertIn("def _resolve_primary_provider(self):", self.src,
                      "V37.9.77: adapter ProxyHandler 必须有 _resolve_primary_provider 方法")

    def test_router_enforce_env_feature_flag(self):
        """V37.9.77: ROUTER_ENFORCE env 必须是 feature flag, 默认 off."""
        self.assertIn('ROUTER_ENFORCE', self.src,
                      "V37.9.77: adapter 必须用 ROUTER_ENFORCE env")
        self.assertIn('"ROUTER_ENFORCE", "off"', self.src,
                      "V37.9.77: ROUTER_ENFORCE 默认必须是 off (PoC 安全网)")

    def test_query_string_parsed_via_urlparse_parse_qs(self):
        """V37.9.77: 必须用 urllib.parse.urlparse + parse_qs 解析 ?provider=."""
        self.assertIn("parse_qs", self.src,
                      "V37.9.77: 必须 import parse_qs (urllib.parse)")
        self.assertIn('qs.get("provider"', self.src,
                      "V37.9.77: 必须 parse_qs 取 provider 字段")

    def test_clean_path_strips_query_string_before_forwarding(self):
        """V37.9.77: forwarded URL 必须 strip ?provider= query (provider API 不识别)."""
        self.assertIn('_parsed_path = urlparse(self.path)', self.src,
                      "V37.9.77: 必须先 urlparse self.path")
        self.assertIn('_clean_path = _parsed_path.path', self.src,
                      "V37.9.77: 必须用 .path (不含 query) 拼 forward URL")

    def test_fail_open_unknown_provider(self):
        """V37.9.77: ?provider=unknown → 回 default (FAIL-OPEN silent fallback)."""
        # 反向 sabotage 防御: 如果检测到错误 (e.g. raise) 而不是 return default, 立即抓
        self.assertIn("override not in PROVIDERS", self.src,
                      "V37.9.77: 必须检查 override 在 PROVIDERS 中")
        # 必须有显式 fallback return, 不能 raise/exit
        self.assertIn("return TARGET_BASE, REAL_MODEL_ID, AUTH_STYLE, API_KEY, PROVIDER_NAME", self.src,
                      "V37.9.77 FAIL-OPEN: 必须显式 return 默认元组, 不能 raise")

    def test_override_disables_fast_route(self):
        """V37.9.77: override 时禁用 FAST_ROUTE (尊重 router 选择)."""
        self.assertIn("primary_name == PROVIDER_NAME", self.src,
                      "V37.9.77: FAST_ROUTE 触发条件必须含 primary_name == PROVIDER_NAME")

    def test_override_log_marker(self):
        """V37.9.77: override 真激活时必须有 log 日志 (审计可追)."""
        self.assertIn("V37.9.77 ROUTER OVERRIDE", self.src,
                      "V37.9.77: override 时必须 log marker (审计可追)")


class TestV37977EndToEndShellGuards(unittest.TestCase):
    """V37.9.77 综合端到端守卫 — adapter + kb_dream + router_decide 三方契约一致."""

    def setUp(self):
        self.adapter_src = open(os.path.join(_REPO_ROOT, "adapter.py")).read()
        self.kbdream_src = open(os.path.join(_REPO_ROOT, "kb_dream.sh")).read()
        self.router_src = open(os.path.join(_REPO_ROOT, "router_decide.py")).read()

    def test_router_enforce_consistent_naming_across_files(self):
        """V37.9.77: ROUTER_ENFORCE 字面量必须在三处一致 (adapter / kb_dream / router_decide)."""
        for src, name in [(self.adapter_src, "adapter"),
                          (self.kbdream_src, "kb_dream"),
                          (self.router_src, "router_decide")]:
            self.assertIn("ROUTER_ENFORCE", src,
                          f"V37.9.77: {name} 必须引用 ROUTER_ENFORCE env 实现三方契约")

    def test_default_off_consistent(self):
        """V37.9.77: 三处都必须用 'off' 作默认值 (PoC 安全).

        Adapter: os.environ.get("ROUTER_ENFORCE", "off")
        kb_dream: ${ROUTER_ENFORCE:-off}
        router_decide: os.environ.get("ROUTER_ENFORCE", "off")
        """
        self.assertIn('"ROUTER_ENFORCE", "off"', self.adapter_src)
        self.assertIn('ROUTER_ENFORCE:-off', self.kbdream_src)
        self.assertIn('"ROUTER_ENFORCE", "off"', self.router_src)

    def test_v37_9_77_marker_in_three_files(self):
        """V37.9.77: marker 必须在三文件可 grep (审计追溯)."""
        for src, name in [(self.adapter_src, "adapter"),
                          (self.kbdream_src, "kb_dream"),
                          (self.router_src, "router_decide")]:
            self.assertIn("V37.9.77", src,
                          f"V37.9.77: {name} 必须含 V37.9.77 marker")


if __name__ == "__main__":
    unittest.main(verbosity=2)
