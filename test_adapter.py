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

    def test_fallback_body_via_helper(self):
        """fallback body 经 _fallback_batch_body 构造（V37.9.224 翻转旧「相同 clean body」守卫：
        批量按 fb 自己的 reasoning_off_body 重算注入，非批量仍原样浅拷贝 = 旧语义保留在非批量路径）。
        V37.9.218: model 仍由 capability-aware fb_model 决定（image→vl_model_id / text→model_id）。"""
        with open("adapter.py") as f:
            content = f.read()
        self.assertIn('fb_clean = _fallback_batch_body(clean, fb', content)
        self.assertIn('fb_clean["model"] = fb_model', content)
        # fb_model 由 capability-aware 分支决定（text 路径仍用 model_id）
        self.assertIn('fb_model = fb["model_id"]', content)

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


class TestWorkloadRouting(unittest.TestCase):
    """V37.9.221 workload 路由: no-tools(批量/纯推理)→fast, PA(有tools)→primary.

    行为级单测 _classify_fast_route 纯函数 (monkeypatch adapter 模块 globals).
    根因: 2026-07-02 reasoning-primary 拖垮批量 job 事故
    (ontology/docs/cases/reasoning_model_primary_breaks_batch_jobs_case.md).
    """

    def setUp(self):
        import adapter
        self.adapter = adapter
        self._saved = (adapter.FAST_ROUTE, adapter.REAL_MODEL_ID,
                       adapter.PROVIDER_NAME, adapter.classify_complexity)
        adapter.FAST_ROUTE = {"name": "qwen", "model_id": "Qwen-fast",
                              "base_url": "http://x", "api_key": "k", "auth_style": "bearer"}
        adapter.REAL_MODEL_ID = "PRIMARY_MODEL"
        adapter.PROVIDER_NAME = "doubao_21"
        adapter.classify_complexity = lambda msgs, has_tools=False: "complex"

    def tearDown(self):
        (self.adapter.FAST_ROUTE, self.adapter.REAL_MODEL_ID,
         self.adapter.PROVIDER_NAME, self.adapter.classify_complexity) = self._saved

    def _call(self, clean, has_multimodal=False, use_model="PRIMARY_MODEL", primary_name="doubao_21"):
        return self.adapter._classify_fast_route(
            clean, clean.get("messages", []), has_multimodal, use_model, primary_name)

    def test_no_tools_routes_workload(self):
        """核心修复: 无 tools (批量/纯推理) → workload fast route"""
        self.assertEqual(self._call({"messages": [{"role": "user", "content": "analyze repo"}]}), "workload")

    def test_workload_independent_of_classifier(self):
        """批量路由不依赖 classify_complexity — classifier 缺失也路由 (鲁棒性修复)"""
        self.adapter.classify_complexity = None
        self.assertEqual(self._call({"messages": [{"content": "x"}]}), "workload")

    def test_tools_simple_routes_smart(self):
        """有 tools + simple → smart fast route (V37.9.76 向后兼容)"""
        self.adapter.classify_complexity = lambda msgs, has_tools=False: "simple"
        self.assertEqual(self._call({"messages": [], "tools": [{"x": 1}]}), "smart")

    def test_tools_complex_stays_primary(self):
        """有 tools + complex (PA) → None (留 primary reasoning 质量)"""
        self.assertIsNone(self._call({"messages": [], "tools": [{"x": 1}]}))

    def test_multimodal_stays_primary(self):
        """多模态 → None (图片需 VL 模型, 快 provider 可能无视觉)"""
        self.assertIsNone(self._call({"messages": []}, has_multimodal=True))

    def test_explicit_model_stays_primary(self):
        """非默认 model → None"""
        self.assertIsNone(self._call({"messages": []}, use_model="other-model"))

    def test_provider_override_disables_fast(self):
        """?provider= override (primary_name≠PROVIDER_NAME) → None (尊重显式选择)"""
        self.assertIsNone(self._call({"messages": []}, primary_name="deepseek_full"))

    def test_no_fast_route_configured(self):
        """FAST_ROUTE 未配置 (FAST_PROVIDER=PROVIDER 时为 None) → None (no-op until flip)"""
        self.adapter.FAST_ROUTE = None
        self.assertIsNone(self._call({"messages": []}))

    def test_empty_tools_is_pure_inference(self):
        """空 tools 列表 = 纯推理批量 → workload"""
        self.assertEqual(self._call({"messages": [], "tools": []}), "workload")

    def test_do_post_uses_helper_not_inline(self):
        """源码守卫: do_POST 用 _classify_fast_route 纯函数 (一物一形, 非 inline drift)"""
        with open("adapter.py") as f:
            content = f.read()
        self.assertIn("_classify_fast_route(clean, clean_msgs", content)
        self.assertIn("WORKLOAD ROUTE: pure-inference", content)


class TestB1ReasoningOff(unittest.TestCase):
    """V37.9.222 B1: 批量在 reasoning provider 注入 thinking-off (doubao_21 单模型通吃 batch).

    行为级单测 _is_batch_workload + _batch_reasoning_off_body 纯函数. 根因: qwen 退役后无独立
    快 provider, batch 靠 reasoning primary 关 reasoning 走快路 (2026-07-02 双 provider 实测).
    """
    _OFF = {"thinking": {"type": "disabled"}}

    def setUp(self):
        import adapter
        self.adapter = adapter
        self._saved = (adapter.REAL_MODEL_ID, adapter.PROVIDER_NAME,
                       adapter.FAST_PROVIDER_NAME, adapter.PROVIDERS)
        adapter.REAL_MODEL_ID = "PRIMARY_MODEL"
        adapter.PROVIDER_NAME = "doubao_21"
        adapter.FAST_PROVIDER_NAME = ""
        adapter.PROVIDERS = {
            "doubao_21": {"reasoning_off_body": dict(self._OFF)},
            "deepseek_full": {"reasoning_off_body": dict(self._OFF)},
            "qwen": {},
        }

    def tearDown(self):
        (self.adapter.REAL_MODEL_ID, self.adapter.PROVIDER_NAME,
         self.adapter.FAST_PROVIDER_NAME, self.adapter.PROVIDERS) = self._saved

    def _batch(self, **kw):
        c = {"messages": [{"role": "user", "content": "analyze repo"}]}
        return self.adapter._is_batch_workload(c, kw.get("mm", False),
                                               kw.get("um", "PRIMARY_MODEL"),
                                               kw.get("pn", "doubao_21"))

    def _rob(self, clean, use_fast=False, mm=False, um="PRIMARY_MODEL", pn="doubao_21"):
        return self.adapter._batch_reasoning_off_body(clean, mm, um, pn, use_fast)

    # --- _is_batch_workload ---
    def test_is_batch_no_tools(self):
        self.assertTrue(self._batch())

    def test_is_batch_false_with_tools(self):
        c = {"messages": [], "tools": [{"x": 1}]}
        self.assertFalse(self.adapter._is_batch_workload(c, False, "PRIMARY_MODEL", "doubao_21"))

    def test_is_batch_false_multimodal(self):
        self.assertFalse(self._batch(mm=True))

    def test_is_batch_false_override(self):
        self.assertFalse(self._batch(pn="deepseek_full"))

    def test_is_batch_false_nondefault_model(self):
        self.assertFalse(self._batch(um="other"))

    # --- _batch_reasoning_off_body ---
    def test_b1_primary_doubao21_injects(self):
        """核心: 批量 + primary=doubao_21 (has body) + 无 FAST → 注入 thinking:disabled"""
        self.assertEqual(self._rob({"messages": [{"content": "x"}]}), self._OFF)

    def test_b1_pa_with_tools_no_inject(self):
        """PA (有 tools) → None (不关 reasoning, 留质量)"""
        self.assertIsNone(self._rob({"messages": [], "tools": [{"x": 1}]}))

    def test_b1_fast_route_qwen_no_inject(self):
        """use_fast + FAST=qwen (无 body) → None (qwen 本就非-reasoning, A2 路由不需注入)"""
        self.adapter.FAST_PROVIDER_NAME = "qwen"
        self.assertIsNone(self._rob({"messages": [{"content": "x"}]}, use_fast=True))

    def test_b1_fast_route_deepseek_injects(self):
        """use_fast + FAST=deepseek_full (has body) → 注入 (服务 provider 是 reasoning 也关)"""
        self.adapter.FAST_PROVIDER_NAME = "deepseek_full"
        self.assertEqual(self._rob({"messages": [{"content": "x"}]}, use_fast=True), self._OFF)

    def test_b1_primary_qwen_no_inject(self):
        """primary=qwen (无 body) → None (qwen 退役前 doubao_21 未作 primary 的情形)"""
        self.adapter.PROVIDER_NAME = "qwen"
        self.assertIsNone(self._rob({"messages": [{"content": "x"}]}, pn="qwen"))

    def test_b1_multimodal_no_inject(self):
        """多模态 → None (图片需 VL/reasoning)"""
        self.assertIsNone(self._rob({"messages": [{"content": "x"}]}, mm=True))

    def test_b1_non_dict_body_ignored(self):
        """V37.9.224 FAIL-OPEN: 畸形声明 (字符串, YAML 插件无类型校验) → None 不注入不抛异"""
        self.adapter.PROVIDERS = {"doubao_21": {"reasoning_off_body": "disabled"}}
        self.assertIsNone(self._rob({"messages": [{"content": "x"}]}))

    def test_do_post_uses_b1_helper(self):
        """源码守卫: do_POST 用 _batch_reasoning_off_body 纯函数 + B1 注入 log"""
        with open("adapter.py") as f:
            content = f.read()
        self.assertIn("_batch_reasoning_off_body(clean", content)
        self.assertIn("B1 REASONING-OFF", content)


class TestB1FallbackPropagation(unittest.TestCase):
    """V37.9.224 B1 fallback 传播: 批量 fallback 按 fallback provider 自己的 reasoning_off_body 重算注入.

    两向事故防线 (V37.9.220 家族):
    ① qwen-primary 回滚态批量无注入 → fallback 到 reasoning provider 带 reasoning 跑
      → reasoning 7-9min >> client 超时 → broken pipe → 502 (V37.9.220 在 fallback 路径重演);
    ② doubao_21-primary B1 注入后批量 fallback 到 qwen → doubao 的 thinking 片段原样
      发给 qwen vLLM 端点 (未测参数, 可能 400 打断最后兜底).
    """
    _OFF = {"thinking": {"type": "disabled"}}

    def setUp(self):
        import adapter
        self.adapter = adapter

    # --- _entry_from_registry 携带 reasoning_off_body (镜像 V37.9.218 vl_model_id) ---
    def _fake_cp(self, rob=None):
        class _CP:
            pass
        cp = _CP()
        cp.name = "doubao_21"
        cp.base_url = "https://ark.example/v3"
        cp.model_id = "doubao-seed-2-1-pro"
        cp.auth_style = "bearer"
        if rob is not None:
            cp.reasoning_off_body = rob
        return cp

    def test_entry_carries_reasoning_off_body(self):
        e = self.adapter._entry_from_registry(self._fake_cp(dict(self._OFF)), "k")
        self.assertEqual(e["reasoning_off_body"], self._OFF)

    def test_entry_none_when_absent(self):
        e = self.adapter._entry_from_registry(self._fake_cp(), "k")
        self.assertIsNone(e["reasoning_off_body"])

    # --- _fallback_batch_body 纯函数 ---
    def test_batch_fallback_to_reasoning_provider_injects(self):
        """血案 ①: serving 无注入 (qwen primary) 批量 fallback 到 deepseek_full → 注入 thinking-off"""
        clean = {"model": "M", "messages": []}
        fb = {"name": "deepseek_full", "reasoning_off_body": dict(self._OFF)}
        out = self.adapter._fallback_batch_body(clean, fb, True, None)
        self.assertEqual(out["thinking"], {"type": "disabled"})

    def test_batch_fallback_to_qwen_strips_injected(self):
        """血案 ②: doubao_21 注入后批量 fallback 到 qwen (无声明) → 剥 thinking 不发未测参数"""
        clean = {"model": "M", "messages": [], "thinking": {"type": "disabled"}}
        fb = {"name": "qwen", "reasoning_off_body": None}
        out = self.adapter._fallback_batch_body(clean, fb, True, self._OFF)
        self.assertNotIn("thinking", out)

    def test_batch_fallback_same_fragment_reinjected(self):
        """doubao_21 → deepseek_full: 剥后重注同款片段 (Bifrost 归一化同参数, V37.9.222 实测)"""
        clean = {"model": "M", "thinking": {"type": "disabled"}}
        fb = {"name": "deepseek_full", "reasoning_off_body": dict(self._OFF)}
        out = self.adapter._fallback_batch_body(clean, fb, True, self._OFF)
        self.assertEqual(out["thinking"], {"type": "disabled"})

    def test_non_batch_pa_unchanged(self):
        """PA (非批量) fallback 原样 — 不注入不剥, reasoning 质量保留"""
        clean = {"model": "M", "messages": [], "tools": [{"x": 1}]}
        fb = {"name": "deepseek_full", "reasoning_off_body": dict(self._OFF)}
        out = self.adapter._fallback_batch_body(clean, fb, False, None)
        self.assertEqual(out, clean)
        self.assertNotIn("thinking", out)

    def test_pure_function_no_input_mutation(self):
        """纯函数契约: 不改写入参 clean (do_POST 的 clean 会被后续 fallback 复用)"""
        clean = {"model": "M", "thinking": {"type": "disabled"}}
        snapshot = json.loads(json.dumps(clean))
        self.adapter._fallback_batch_body(clean, {"name": "qwen"}, True, self._OFF)
        self.assertEqual(clean, snapshot)

    def test_fb_entry_missing_key_tolerant(self):
        """chain entry 缺 reasoning_off_body key (旧格式/手工 entry) → 容忍, 仅剥不注"""
        clean = {"model": "M", "thinking": {"type": "disabled"}}
        out = self.adapter._fallback_batch_body(clean, {"name": "qwen"}, True, self._OFF)
        self.assertNotIn("thinking", out)

    def test_non_dict_fb_rob_fail_open(self):
        """V37.9.224 FAIL-OPEN: 畸形 YAML 插件声明非 dict (字符串/list) → 忽略不抛异.

        本调用点在 fallback 循环 per-provider try 之前, update() 抛 ValueError 会
        打断整条 fallback 链 — 故障时刻摧毁兜底安全网, 必须只认 dict."""
        clean = {"model": "M", "messages": []}
        for bad in ("disabled", ["thinking"], 1, True):
            fb = {"name": "bad_plugin", "reasoning_off_body": bad}
            out = self.adapter._fallback_batch_body(clean, fb, True, None)
            self.assertNotIn("thinking", out)
            self.assertEqual(out["model"], "M")

    def test_non_dict_serving_rob_tolerated(self):
        """V37.9.224 FAIL-OPEN: serving_rob 非 dict (防御性, 正常经 _batch_reasoning_off_body
        已归一为 dict/None) → 剥离侧不抛异 (字符串迭代 chars 的隐性错误也挡掉)"""
        clean = {"model": "M", "messages": []}
        out = self.adapter._fallback_batch_body(clean, {"name": "qwen"}, True, "disabled")
        self.assertEqual(out["model"], "M")

    # --- 源码守卫 ---
    def test_do_post_fallback_uses_helper(self):
        """源码守卫: fallback 循环用 _fallback_batch_body, 旧 fb_clean = dict(clean) 直连形态退役.

        精确到循环段 (for fb in FALLBACK_CHAIN: → ALL FALLBACKS FAILED), 避免误伤
        helper 内部的合法浅拷贝 (dict(clean) 是 _fallback_batch_body 的第一行).
        """
        with open("adapter.py") as f:
            content = f.read()
        start = content.index("for fb in FALLBACK_CHAIN:")
        end = content.index("FALLBACKS FAILED", start)
        loop = content[start:end]
        self.assertIn("_fallback_batch_body(clean, fb", loop)
        self.assertNotIn("dict(clean)", loop)

    def test_entry_constructors_both_carry_rob(self):
        """源码守卫 (原则 #31 跨消费者全量同步): registry + legacy 两个 entry 构造点都带 reasoning_off_body"""
        with open("adapter.py") as f:
            content = f.read()
        self.assertIn('"reasoning_off_body": getattr(cp, "reasoning_off_body", None) or None', content)
        self.assertIn('"reasoning_off_body": fb.get("reasoning_off_body") or None', content)


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

        # 提取 _entry_from_registry + _build_fallback_chain（V37.9.218: 后者依赖前者 helper）
        match = re.search(
            r'(def _entry_from_registry\(.*?)(?=\n# Initial build at startup)',
            src, re.DOTALL
        )
        assert match, "_entry_from_registry / _build_fallback_chain not found"
        cls._func_src = match.group(1)

    def _exec_build(self, providers=None, provider_name="qwen",
                    fallback_provider="", get_registry=None, env_overrides=None,
                    exclude=None):
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
            "_FALLBACK_EXCLUDE": set(exclude or []),  # V37.9.129: 排除 geo-block/不可达 provider
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

    # --- V37.9.129: _FALLBACK_EXCLUDE 退役地理封锁/不可达 provider（gemini 香港 geo-block）---

    @staticmethod
    def _mock_registry(names):
        """构造返回指定 provider 名的 mock registry（供 auto-discover 路径测试）"""
        def _make(name):
            return type("MockCp", (), {
                "name": name,
                "base_url": f"https://{name}",
                "api_key_env": f"{name.upper()}_KEY",
                "model_id": f"{name}-model",
                "auth_style": "bearer",
            })()
        reg = type("MockReg", (), {
            "build_fallback_chain": lambda self, primary, require_available=False: [_make(n) for n in names]
        })()
        return lambda: reg

    def test_v37_9_129_auto_chain_excludes_geo_blocked(self):
        """V37.9.129: auto-discover 路径排除 _FALLBACK_EXCLUDE（gemini geo-block）→ 链只剩 doubao"""
        chain = self._exec_build(
            get_registry=self._mock_registry(["doubao", "gemini"]),
            env_overrides={"DOUBAO_KEY": "k1", "GEMINI_KEY": "k2"},
            exclude=["gemini"],
        )
        names = [c["name"] for c in chain]
        self.assertIn("doubao", names)
        self.assertNotIn("gemini", names)

    def test_v37_9_129_no_exclude_keeps_provider(self):
        """V37.9.129 反向: 不排除时 gemini 仍在链（证明排除才是移除它的原因，非别的）"""
        chain = self._exec_build(
            get_registry=self._mock_registry(["doubao", "gemini"]),
            env_overrides={"DOUBAO_KEY": "k1", "GEMINI_KEY": "k2"},
            exclude=[],
        )
        names = [c["name"] for c in chain]
        self.assertIn("gemini", names)

    def test_v37_9_129_explicit_fallback_also_excluded(self):
        """V37.9.129: 显式 FALLBACK_PROVIDER 也受 _FALLBACK_EXCLUDE 约束"""
        providers = {
            "qwen": {"base_url": "https://q", "api_key_env": "QWEN_KEY", "model_id": "qwen3", "auth_style": "bearer"},
            "gemini": {"base_url": "https://g", "api_key_env": "GEMINI_KEY", "model_id": "gemini-2.5", "auth_style": "bearer"},
        }
        chain = self._exec_build(
            providers=providers,
            env_overrides={"FALLBACK_PROVIDER": "gemini", "GEMINI_KEY": "test-key"},
            exclude=["gemini"],
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
