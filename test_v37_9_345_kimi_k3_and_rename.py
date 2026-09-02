"""V37.9.345 — Kimi K3 接入 (第 13 个 provider) + doubao 槽位改名退役命名债.

血案背景 (V37.9.339 → V37.9.344, 三版误判):
  V37.9.339 把 registry 名 `doubao` 的槽位换成 Kimi K3, 却**保留 env 名 DOUBAO_API_KEY**,
  并在同版写下「保留旧名 = 零 blast radius」。然后让用户把 **Kimi 的 key 填进一个叫
  DOUBAO 的变量** —— 用户填了 doubao 的 key (任何人都会这么做) → 探针 InsufficientScope
  → 我连续三版 (341/342/343) 把矛盾归因于供应商 key↔模型标注错配。
  V37.9.344 keymap 实测: 四把 key 的供应商标注**逐条正确**, 真根因是我造的命名债。
  「零 blast radius」在写下当天就被证伪。

本套件守两件事:
  1. Kimi K3 作为独立 provider 的诚实语义 (declared / 全 verified_* False / 独立 env)
  2. **命名债的结构性退役** —— api_key_env 的厂商名必须与它服务的模型一致 (机器可检),
     这条守卫若在 V37.9.339 当天存在, CI 会当场红, 三版误判不会发生。
"""
import importlib
import os
import re
import subprocess
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.abspath(__file__))


def _strip_py_comments_and_docstrings(src: str) -> str:
    """用 tokenize 剥掉 Python 注释与三引号块 (docstring/长注释), 只留可执行结构.

    行号不保真 — 本守卫只关心 "剥完还有没有", 不关心在第几行。
    """
    import io
    import tokenize
    TRIPLE = ('"' * 3, "'" * 3)
    toks = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type == tokenize.COMMENT:
                continue
            if tok.type == tokenize.STRING:
                stripped = tok.string.lstrip("rbfuRBFU")
                if stripped.startswith(TRIPLE):
                    continue          # 三引号块 = docstring/长注释 → 剥掉
            toks.append((tok.string, tok.start[0]))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return "\n".join(l for l in src.splitlines() if not l.strip().startswith("#"))
    rows = {}
    for text, row in toks:
        rows.setdefault(row, []).append(text)
    return "\n".join(" ".join(rows[r]) for r in sorted(rows))
KIMI_PLUGIN = os.path.join(REPO, "providers.d", "kimi_k3_provider.py")
DOUBAO_SLOT_PLUGIN = os.path.join(REPO, "providers.d", "doubao_21_tokenhub_provider.py")


def _reload_providers():
    if "providers" in sys.modules:
        del sys.modules["providers"]
    return importlib.import_module("providers")


class TestKimiK3Registration(unittest.TestCase):
    def setUp(self):
        sys.path.insert(0, REPO)
        self.providers = _reload_providers()
        self.reg = self.providers.get_registry()

    def test_kimi_k3_registered(self):
        self.assertIn("kimi_k3", self.reg.list_names())

    def test_contract_valid(self):
        p = self.reg.get("kimi_k3")
        self.assertEqual(self.providers.ProviderContract.validate(p), [])

    def test_independent_env_not_doubao(self):
        """🔴 血案核心: Kimi 的 key 必须住在名字里写着 KIMI 的变量里。"""
        p = self.reg.get("kimi_k3")
        self.assertEqual(p.api_key_env, "KIMI_K3_API_KEY")
        self.assertNotIn("DOUBAO", p.api_key_env,
                         "V37.9.344 血案: Kimi key 曾被要求填进 DOUBAO_API_KEY")

    def test_distinct_from_builtin_kimi(self):
        """区别于第 5 个 built-in kimi (Moonshot 官方) — 不同 endpoint/key/model。"""
        k3 = self.reg.get("kimi_k3")
        builtin = self.reg.get("kimi")
        self.assertNotEqual(k3.base_url, builtin.base_url)
        self.assertNotEqual(k3.api_key_env, builtin.api_key_env)
        self.assertNotEqual(k3.default_model().model_id, builtin.default_model().model_id)

    def test_feature_verified_after_e2e_v346(self):
        """V37.9.346 Mac Mini E2E 3/3 → declared 升 feature_verified (只 flip 实测项)。"""
        c = self.reg.get("kimi_k3").capabilities
        self.assertEqual(c.verification_tier, "feature_verified")
        self.assertEqual(set(c.verified_features()),
                         {"text", "tool_calling", "streaming", "reasoning"})
        self.assertIn("V37.9.346", c.tier_evidence)

    def test_unprobed_features_stay_false_v346(self):
        """🔴 「探针通过了」≠「全绿」: vision/json_mode 从未探测过, 必须仍是 False。

        V37.9.342 忍住不翻 verified_text 的同款纪律 —— 证据标准要在证据看起来
        很好的时候才真正起作用。
        """
        c = self.reg.get("kimi_k3").capabilities
        feats = c.verified_features()
        self.assertNotIn("vision", feats)
        self.assertNotIn("fallback", feats, "未真在生产 fallback 链接管过")
        self.assertFalse(c.json_mode, "json_mode 未探测不得声明")
        self.assertFalse(c.vision, "vision 未探测不得声明")

    def test_capability_declaration_matches_probe_evidence(self):
        """声明必须与探针证据一致 (V37.9.254 over-declare / V37.9.346 reasoning 翻案)。"""
        c = self.reg.get("kimi_k3").capabilities
        self.assertTrue(c.text and c.tool_calling and c.streaming, "OpenAI /v1 基线")
        self.assertTrue(c.reasoning,
                        "V37.9.346 实测 reasoning_tokens=190 → 保守 False 已翻案")
        for attr in ("vision", "json_mode"):
            self.assertFalse(getattr(c, attr), f"{attr} 未实测不得声明")

    def test_vision_off_means_no_vl_routing(self):
        """V37.9.218 capability-aware vision fallback: 不声明 vision → vl_model_id 空,
        image 请求不会被路由到未实测 image_url 透传的 provider (under-declare 是安全方向)。"""
        p = self.reg.get("kimi_k3")
        vl = [m for m in p.models if m.is_vision]
        self.assertEqual(vl, [], "kimi_k3 不得提供 vision 模型")

    def test_no_reasoning_off_body(self):
        """V37.9.224: thinking 片段是 Ark/DeepSeek 家族参数, 未测参数可能 400 打断 fallback。

        🔴 V37.9.346 证实 K3 有 reasoning 通道后仍不声明 —— 「有没有 reasoning」与
        「接不接受关-reasoning 的请求体参数」是两件事, 后者从未实测。
        """
        self.assertIsNone(self.reg.get("kimi_k3").reasoning_off_body)

    def test_tier_consistency(self):
        self.assertEqual(self.reg.tier_consistency_violations(), [])

    def test_registry_has_13_providers(self):
        names = self.reg.list_names()
        self.assertEqual(len(names), 13,
                         f"V37.9.345: 7 built-in + 6 plugins = 13, got {len(names)}: {names}")


class TestKeyEnvVendorConsistency(unittest.TestCase):
    """🔴 V37.9.344 命名债的机器化退役.

    规则: 若 api_key_env 里出现某个厂商 token, 该 provider 默认模型的 model_id
    必须含相容 token。V37.9.339 的 `DOUBAO_API_KEY → kimi-k3-260716` 形态会被此守卫
    当场抓住 —— 那正是让用户填错 key 的那一步。
    """

    # env 里的厂商 token → model_id 中可接受的 token 之一
    VENDOR_TOKENS = {
        "DOUBAO": ("doubao",),
        "KIMI": ("kimi",),
        "MOONSHOT": ("kimi", "moonshot"),
        "DEEPSEEK": ("deepseek",),
        "GLM": ("glm",),
        "MINIMAX": ("minimax",),
        "GEMINI": ("gemini",),
        "OPENAI": ("gpt", "openai"),
        "ANTHROPIC": ("claude",),
        "QWEN": ("qwen",),
    }

    def setUp(self):
        sys.path.insert(0, REPO)
        self.providers = _reload_providers()

    def _violations(self, pairs):
        """pairs: [(provider_name, api_key_env, model_id)] → 违规列表."""
        bad = []
        for name, env, model_id in pairs:
            env_u, mid_l = env.upper(), model_id.lower()
            for vendor, accepted in self.VENDOR_TOKENS.items():
                if vendor in env_u and not any(t in mid_l for t in accepted):
                    bad.append(f"{name}: env={env} 含 {vendor} 但 model={model_id} 不含 {accepted}")
        return bad

    def test_all_providers_vendor_consistent(self):
        pairs = [(p.name, p.api_key_env, p.default_model().model_id)
                 for p in self.providers.get_registry().all()]
        self.assertGreaterEqual(len(pairs), 13, "防空转: 必须真扫到全部 provider")
        self.assertEqual(self._violations(pairs), [],
                         "api_key_env 的厂商名必须与它服务的模型一致 (V37.9.344 命名债)")

    def test_blood_lesson_shape_is_caught(self):
        """反向证据: V37.9.339 的真实形态必须被同一判据抓住 (证上一条非空转)。"""
        bad = self._violations([("doubao", "DOUBAO_API_KEY", "kimi-k3-260716")])
        self.assertEqual(len(bad), 1)
        self.assertIn("DOUBAO", bad[0])
        self.assertIn("kimi-k3-260716", bad[0])

    def test_vendor_neutral_env_names_exempt(self):
        """REMOTE_API_KEY / ARK_21_API_KEY 是平台/中性名不含厂商 token → 不误报。"""
        self.assertEqual(
            self._violations([("qwen", "REMOTE_API_KEY", "Qwen3-235B"),
                              ("doubao_21", "ARK_21_API_KEY", "doubao-seed-2-1-pro-260628")]),
            [])


class TestDoubaoSlotRenamed(unittest.TestCase):
    """doubao 槽位改名 doubao_21_tokenhub — 命名债退役后的现态守卫."""

    def setUp(self):
        sys.path.insert(0, REPO)
        self.providers = _reload_providers()
        self.reg = self.providers.get_registry()

    def test_new_name_registered_old_name_gone(self):
        names = self.reg.list_names()
        self.assertIn("doubao_21_tokenhub", names)
        self.assertNotIn("doubao", names,
                         "V37.9.345: 旧槽位名 doubao 已退役 (它服务的不再是 2.0)")

    def test_env_renamed(self):
        self.assertEqual(self.reg.get("doubao_21_tokenhub").api_key_env,
                         "DOUBAO_21_TOKENHUB_API_KEY")

    def test_plugin_file_renamed(self):
        self.assertTrue(os.path.isfile(DOUBAO_SLOT_PLUGIN))
        self.assertFalse(os.path.isfile(os.path.join(REPO, "providers.d", "doubao_provider.py")),
                         "旧文件名必须已 git mv 退役")

    def test_platform_redundancy_documented(self):
        """同一 model_id 两平台是刻意设计 (Ark vs ai-tokenhub), 未来 session 勿去重。"""
        a = self.reg.get("doubao_21")
        b = self.reg.get("doubao_21_tokenhub")
        self.assertEqual(a.default_model().model_id, b.default_model().model_id,
                         "同模型 = 平台冗余的前提")
        self.assertNotEqual(a.base_url, b.base_url, "不同平台")
        self.assertNotEqual(a.display_name, b.display_name, "display_name 须可区分平台")

    def test_expert_backend_points_at_renamed_slot(self):
        """MR-8: expert_escalation 的 key env 必须与它复用的槽位一致。"""
        sys.path.insert(0, REPO)
        import expert_escalation as ee
        self.assertEqual(ee.EXPERT_API_KEY_ENV,
                         self.reg.get("doubao_21_tokenhub").api_key_env)
        self.assertEqual(ee.EXPERT_DEFAULT_MODEL_ID,
                         self.reg.get("doubao_21_tokenhub").default_model().model_id)


class TestExpertBackendLegacyAlias(unittest.TestCase):
    """LLM-facing 枚举改名纪律: 未清 session 的 PA 仍会发 backend='doubao'.

    MR-9: escalate() 即便 dry_run 也会写审计记录并计入每日配额 —— 必须传隔离的
    audit_log_path, 否则跑测试会烧掉生产 expert 配额 (本套件首版真烧了 40 次)。
    """

    def setUp(self):
        sys.path.insert(0, REPO)
        import expert_escalation as ee
        self.ee = ee
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.audit = os.path.join(self._td.name, "expert_escalations.jsonl")

    def test_legacy_alias_accepted(self):
        r = self.ee.escalate("Q", backend="doubao", dry_run=True,
                             audit_log_path=self.audit)
        self.assertNotEqual(r.get("status"), "unknown_backend",
                            "旧值 doubao 必须仍被接受 (未清 session 的 PA 会发它)")

    def test_new_default_accepted(self):
        r = self.ee.escalate("Q", backend="default", dry_run=True,
                             audit_log_path=self.audit)
        self.assertNotEqual(r.get("status"), "unknown_backend")

    def test_does_not_touch_production_audit_log(self):
        """MR-9 血案回归: 本套件绝不写 ~/.kb/audit/expert_escalations.jsonl。"""
        prod = self.ee.DEFAULT_AUDIT_LOG
        before = os.path.getsize(prod) if os.path.isfile(prod) else -1
        self.ee.escalate("Q", backend="default", dry_run=True,
                         audit_log_path=self.audit)
        after = os.path.getsize(prod) if os.path.isfile(prod) else -1
        self.assertEqual(before, after, "测试写进了生产 expert 审计日志 (会烧真配额)")
        self.assertTrue(os.path.isfile(self.audit), "隔离审计文件必须真被写 (防空转)")

    def test_unknown_still_rejected(self):
        r = self.ee.escalate("Q", backend="bogus_backend")
        self.assertEqual(r["status"], "unknown_backend")

    def test_schema_enum_is_vendor_neutral(self):
        """PA 看到的 enum 不得写死厂商名 (槽位换过 3 次模型)。"""
        import proxy_filters
        tools = [t for t in proxy_filters.CUSTOM_TOOLS
                 if t["function"]["name"] == "expert_escalate"]
        self.assertEqual(len(tools), 1)
        fn = tools[0]["function"]
        enum = fn["parameters"]["properties"]["backend"]["enum"]
        self.assertIn("default", enum)
        blob = str(fn)
        for vendor in ("Doubao", "Kimi", "DeepSeek", "GLM"):
            self.assertNotIn(vendor, blob,
                             f"expert_escalate schema 不得写死厂商名 {vendor}")


class TestSourceGuards(unittest.TestCase):
    def test_kimi_plugin_no_hardcoded_key(self):
        """🔴 公开 repo 安全底线: key 只走 env。"""
        with open(KIMI_PLUGIN) as fh:
            src = fh.read()
        self.assertIsNone(re.search(r"sk-[0-9a-zA-Z]{16,}", src),
                          "plugin 不得硬编码任何 sk- key")
        self.assertIn('api_key_env = "KIMI_K3_API_KEY"', src)

    def test_kimi_plugin_records_naming_debt_lesson(self):
        """血案留痕: 未来 session 读 plugin 就知道为什么 env 必须独立命名。"""
        with open(KIMI_PLUGIN) as fh:
            src = fh.read()
        self.assertIn("V37.9.345", src)
        self.assertIn("V37.9.344", src, "必须引用 keymap 实测破案的那一版")
        self.assertIn("KIMI_K3_API_KEY", src)

    def test_file_map_registers_both_plugins(self):
        """V37.9.206 教训: provider 插件漏进 FILE_MAP → check_registry 硬 error。"""
        with open(os.path.join(REPO, "auto_deploy.sh")) as fh:
            src = fh.read()
        self.assertIn("providers.d/kimi_k3_provider.py", src)
        self.assertIn("providers.d/doubao_21_tokenhub_provider.py", src)
        self.assertNotIn("providers.d/doubao_provider.py", src,
                         "旧文件名必须已从 FILE_MAP 退役")

    def test_no_stale_doubao_api_key_env_in_runtime(self):
        """DOUBAO_API_KEY (旧 env) 不得再出现在任何 runtime **可执行**行里.

        必须剥注释 + docstring: 本次改名的血案叙述里逐字写着被退役的 DOUBAO_API_KEY
        (V37.9.325/178 家族 — 守卫被自己的文档咬)。剥完仍要有内容, 否则守卫空转。
        """
        offenders = []
        scanned_py = 0
        for root, dirs, files in os.walk(REPO):
            dirs[:] = [d for d in dirs
                       if d not in (".git", "__pycache__", "docs", "node_modules")]
            for f in files:
                if not (f.endswith(".py") or f.endswith(".sh")) or f.startswith("test_"):
                    continue
                path = os.path.join(root, f)
                with open(path, errors="replace") as fh:
                    src = fh.read()
                if f.endswith(".py"):
                    scanned_py += 1
                    body = _strip_py_comments_and_docstrings(src)
                else:
                    body = "\n".join(l for l in src.splitlines()
                                     if not l.strip().startswith("#"))
                for line in body.splitlines():
                    if re.search(r"\bDOUBAO_API_KEY\b", line):
                        offenders.append(
                            f"{os.path.relpath(path, REPO)}: {line.strip()[:80]}")
        self.assertGreater(scanned_py, 30, "防空转: 必须真扫到运行时 .py")
        self.assertEqual(offenders, [],
                         f"旧 env DOUBAO_API_KEY 仍活在可执行行: {offenders}")

    def test_strip_helper_is_load_bearing(self):
        """防空转的防空转: 剥离器必须真剥掉三引号块与注释, 且不误剥可执行行。"""
        q = '"' * 3
        sample = (
            'x = 1  # DOUBAO_API_KEY in comment\n'
            + q + 'docstring mentions DOUBAO_API_KEY' + q + '\n'
            'os.environ["DOUBAO_API_KEY"]\n'
        )
        out = _strip_py_comments_and_docstrings(sample)
        self.assertEqual(out.count("DOUBAO_API_KEY"), 1,
                         "注释与 docstring 里的必须被剥, 可执行行的必须留下")

    def test_check_registry_passes(self):
        r = subprocess.run([sys.executable, os.path.join(REPO, "check_registry.py")],
                           capture_output=True, text=True, timeout=120, cwd=REPO)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
