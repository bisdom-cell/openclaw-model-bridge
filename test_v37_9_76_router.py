"""V37.9.76 Capability-Based Dynamic Router PoC — 单测.

覆盖三个层面:
1. providers.py::find_best_provider 纯函数 (Step 2 主交付)
2. router_decide.py 模块 + CLI (Step 3 集成 + Step 4 observability)
3. kb_dream.sh + run_hf_papers.sh shadow 模式集成守卫 (Step 3 切试水)

设计原则:
- shadow mode 不依赖 LLM 真调用 — 所有测试纯函数 / mock
- FAIL-OPEN 契约严格守 (registry/providers 缺失 → 不抛异常, 不阻塞 caller)
- 反向 sabotage 守卫真有效 (V37.9.74/V37.9.75 同款模式)
"""

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from unittest.mock import patch, MagicMock


_REPO_ROOT = os.path.dirname(os.path.abspath(__file__))


class TestFindBestProvider(unittest.TestCase):
    """V37.9.76 Step 2: providers.find_best_provider 纯函数测试."""

    def setUp(self):
        # 每个测试重新 import (干净 registry)
        if "providers" in sys.modules:
            del sys.modules["providers"]
        import providers
        self.providers = providers
        self.registry = providers._default_registry

    def test_required_text_picks_top_by_cap_score(self):
        """无 prefer 时 required=text → 选 cap_score 最高的 (V37.9.55 后 doubao=16)."""
        chosen = self.registry.find_best_provider(
            required={"text": True}, require_available=False
        )
        self.assertIsNotNone(chosen)
        # doubao 是 V37.9.55 后 cap_score 最高的 text+reasoning provider
        # qwen cap_score=14 (5 base + 5 verified*2 - reasoning verified)
        # doubao cap_score=16 (6 base + 5 verified*2)
        # 应选 doubao
        self.assertEqual(chosen.name, "doubao",
                         f"V37.9.76: required=text → cap_score 最高 doubao, got {chosen.name}")

    def test_required_vision_filters_non_vision_providers(self):
        """required=vision → 排除无 vision 的 provider (硬过滤)."""
        chosen = self.registry.find_best_provider(
            required={"text": True, "vision": True}, require_available=False
        )
        self.assertIsNotNone(chosen)
        # 必须有 vision capability
        self.assertTrue(chosen.capabilities.vision,
                        "V37.9.76: required=vision 时 chosen 必须支持 vision")

    def test_required_nonexistent_capability_returns_none(self):
        """required 含不存在的 capability → 返回 None (硬过滤排除所有)."""
        chosen = self.registry.find_best_provider(
            required={"nonexistent_cap_xyz": True}, require_available=False
        )
        self.assertIsNone(chosen,
                          "V37.9.76: 不存在的 capability → None (无人匹配)")

    def test_exclude_removes_named_providers(self):
        """exclude 排除指定 provider 名."""
        chosen = self.registry.find_best_provider(
            required={"text": True},
            exclude=["doubao"],
            require_available=False,
        )
        self.assertIsNotNone(chosen)
        self.assertNotEqual(chosen.name, "doubao",
                            "V37.9.76: exclude=[doubao] → 不可选 doubao")

    def test_prefer_adds_score_boost(self):
        """prefer 命中加 +10, 让原本平等的 provider 中胜出."""
        # qwen 和 doubao 都有 reasoning. prefer=reasoning → 偏向 reasoning
        chosen_no_prefer = self.registry.find_best_provider(
            required={"text": True}, require_available=False
        )
        chosen_with_prefer = self.registry.find_best_provider(
            required={"text": True}, prefer=["reasoning"], require_available=False
        )
        # 两个都应选 doubao (cap_score 最高 + reasoning)
        # 这里主要验证 prefer 参数不破坏选择
        self.assertEqual(chosen_no_prefer.name, "doubao")
        self.assertEqual(chosen_with_prefer.name, "doubao")
        # 验证 prefer 让 score 真增加 (内部 score 不直接暴露, 我们间接验证: 排除 doubao 后,
        # prefer=reasoning 应让 qwen (有 reasoning) 胜过 openai (无 reasoning))
        chosen_qwen_path = self.registry.find_best_provider(
            required={"text": True}, prefer=["reasoning"],
            exclude=["doubao"], require_available=False
        )
        self.assertEqual(chosen_qwen_path.name, "qwen",
                         "V37.9.76: exclude doubao + prefer reasoning → qwen (有 reasoning)")

    def test_require_available_false_includes_all(self):
        """require_available=False 即使无 API key 也包含所有 provider."""
        # Clear all API key envs to simulate dev environment
        with patch.dict(os.environ, {}, clear=True):
            chosen = self.registry.find_best_provider(
                required={"text": True}, require_available=False
            )
            # 即使 API key 都没有, 仍能选出 provider
            self.assertIsNotNone(chosen,
                                 "V37.9.76: require_available=False 时无 API key 仍能选")

    def test_require_available_true_filters_unset_api_keys(self):
        """require_available=True 排除无 API key 的 provider."""
        with patch.dict(os.environ, {}, clear=True):
            chosen = self.registry.find_best_provider(
                required={"text": True}, require_available=True
            )
            self.assertIsNone(chosen,
                              "V37.9.76: require_available=True 无 API key → None")

    def test_no_required_returns_some_provider(self):
        """required=None (无硬过滤) → 选 cap_score 最高的."""
        chosen = self.registry.find_best_provider(
            required=None, require_available=False
        )
        self.assertIsNotNone(chosen)

    def test_empty_required_dict_is_same_as_none(self):
        """required={} (空 dict) 等同 required=None."""
        a = self.registry.find_best_provider(
            required={}, require_available=False
        )
        b = self.registry.find_best_provider(
            required=None, require_available=False
        )
        # 两者应一致 (V37.9.76 设计: empty dict 也走"无硬过滤"路径)
        self.assertEqual(
            a.name if a else None, b.name if b else None,
            "V37.9.76: empty dict required 应等同 None"
        )

    def test_exclude_all_returns_none(self):
        """exclude 所有 provider → 返回 None."""
        all_names = list(self.registry._providers.keys())
        chosen = self.registry.find_best_provider(
            required={"text": True},
            exclude=all_names,
            require_available=False,
        )
        self.assertIsNone(chosen)


class TestRouterDecideModule(unittest.TestCase):
    """V37.9.76 Step 3: router_decide.py 模块测试."""

    def setUp(self):
        # 干净 import
        for m in ["router_decide", "providers"]:
            if m in sys.modules:
                del sys.modules[m]
        sys.path.insert(0, _REPO_ROOT)
        import router_decide
        self.router_decide = router_decide

    def test_decide_known_job_returns_chosen(self):
        """decide(kb_dream) 应返回含 chosen=doubao 的 record (registry 已声明 profile)."""
        record = self.router_decide.decide(
            job_id="kb_dream", task="radar_retry", require_available=False
        )
        self.assertEqual(record["job_id"], "kb_dream")
        self.assertEqual(record["task"], "kb_dream/radar_retry")
        self.assertEqual(record["chosen"], "doubao",
                         "V37.9.76: kb_dream profile (text+prefer reasoning) → doubao")
        self.assertEqual(record["mode"], "shadow")
        self.assertEqual(record["reason"], "ok")
        self.assertTrue(record["v37_9_76"])

    def test_decide_unknown_job_returns_no_router_profile(self):
        """decide(nonexistent) → reason=no_router_profile, 不抛异常."""
        record = self.router_decide.decide(
            job_id="nonexistent_xyz_job",
            require_available=False,
        )
        self.assertEqual(record["reason"], "no_router_profile")
        self.assertIsNone(record["chosen"])

    def test_decide_known_job_without_capability_fields_returns_no_profile(self):
        """decide(arxiv_monitor) → reason=no_router_profile (registry 中 arxiv_monitor 无 capability fields)."""
        record = self.router_decide.decide(
            job_id="arxiv_monitor", require_available=False
        )
        self.assertEqual(record["reason"], "no_router_profile",
                         "V37.9.76: 未声明 capability fields → no_router_profile")

    def test_decide_with_exclude_filters_alternatives(self):
        """decide(kb_dream, exclude=[doubao]) → 不选 doubao."""
        record = self.router_decide.decide(
            job_id="kb_dream", exclude=["doubao"], require_available=False
        )
        self.assertNotEqual(record["chosen"], "doubao",
                            "V37.9.76: exclude doubao → chosen != doubao")
        # qwen 应该是 next pick
        self.assertEqual(record["chosen"], "qwen")

    def test_decide_with_profile_override(self):
        """profile_override 覆盖 registry — 用于测试不依赖 yaml."""
        record = self.router_decide.decide(
            job_id="test_job",
            profile_override={
                "required_capabilities": ["text"],
                "prefer": ["reasoning"],
                "cost_tier": "high",
            },
            require_available=False,
        )
        self.assertEqual(record["chosen"], "doubao")
        self.assertEqual(record["cost_tier"], "high")

    def test_record_schema_has_all_required_fields(self):
        """JSONL record 必须含所有合约字段 (caller 依赖 schema 稳定)."""
        record = self.router_decide.decide(
            job_id="kb_dream", require_available=False
        )
        expected_keys = {
            "ts", "task", "job_id", "required", "prefer", "cost_tier",
            "exclude", "chosen", "chosen_cap_score", "alternatives",
            "mode", "reason", "v37_9_76"
        }
        self.assertEqual(set(record.keys()), expected_keys,
                         "V37.9.76: record schema 必须稳定 (caller / observability 依赖)")

    def test_append_jsonl_creates_dir_and_appends(self):
        """append_jsonl 必须自动 mkdir -p + append 不覆盖."""
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "subdir", "router_decisions.jsonl")
            record1 = {"v37_9_76": True, "chosen": "qwen", "ts": "2026-05-18T10:00:00+08:00"}
            record2 = {"v37_9_76": True, "chosen": "doubao", "ts": "2026-05-18T10:01:00+08:00"}
            ok1 = self.router_decide.append_jsonl(record1, path=path)
            ok2 = self.router_decide.append_jsonl(record2, path=path)
            self.assertTrue(ok1 and ok2)
            self.assertTrue(os.path.isfile(path))
            with open(path) as f:
                lines = f.readlines()
            self.assertEqual(len(lines), 2, "V37.9.76: append-only 必须 2 行")
            self.assertEqual(json.loads(lines[0])["chosen"], "qwen")
            self.assertEqual(json.loads(lines[1])["chosen"], "doubao")

    def test_append_jsonl_fail_open_on_readonly_path(self):
        """append_jsonl FAIL-OPEN: 写权限失败返回 False 而非抛异常."""
        # /proc/cant-write-here 应该失败但不炸
        ok = self.router_decide.append_jsonl(
            {"v37_9_76": True}, path="/proc/cant-write-here-v37-9-76/test.jsonl"
        )
        self.assertFalse(ok, "V37.9.76: 写失败应返回 False 不抛异常")

    def test_load_profile_returns_none_for_missing_yaml(self):
        """yaml 缺失时 _load_yaml_job_profile 返回 None (FAIL-OPEN).

        V37.9.76-hotfix: 三档候选路径都要 patch (PATH + MAC_MINI + FALLBACK), 否则
        Mac Mini canonical 路径 ~/openclaw-model-bridge/jobs_registry.yaml 真存在
        会让 FAIL-OPEN 守卫失效.
        """
        # monkey-patch yaml 路径到不存在 (含 V37.9.76-hotfix 新加的 MAC_MINI 候选)
        with patch.object(self.router_decide, "JOBS_REGISTRY_PATH", "/nonexistent/foo.yaml"), \
             patch.object(self.router_decide, "JOBS_REGISTRY_MAC_MINI", "/nonexistent/baz.yaml"), \
             patch.object(self.router_decide, "JOBS_REGISTRY_FALLBACK", "/nonexistent/bar.yaml"):
            profile = self.router_decide._load_yaml_job_profile("kb_dream")
            self.assertIsNone(profile)

    def test_v37_9_76_hotfix_mac_mini_repo_path_candidate(self):
        """V37.9.76-hotfix: JOBS_REGISTRY_MAC_MINI 必须指向 Mac Mini canonical repo 路径.

        触发: 2026-05-18 09:29 Mac Mini 首跑实测发现 PATH + FALLBACK 都重合到 $HOME/jobs_registry.yaml
        (因 router_decide.py 部署到 ~/, dirname(__file__) = ~), yaml 找不到 → chosen=null silent.
        修复: 加 ~/openclaw-model-bridge/jobs_registry.yaml 作第二候选 (auto_deploy 同步源).
        """
        self.assertTrue(hasattr(self.router_decide, "JOBS_REGISTRY_MAC_MINI"),
                        "V37.9.76-hotfix: JOBS_REGISTRY_MAC_MINI 常量必须存在")
        expected = os.path.expanduser("~/openclaw-model-bridge/jobs_registry.yaml")
        self.assertEqual(self.router_decide.JOBS_REGISTRY_MAC_MINI, expected,
                         "V37.9.76-hotfix: 必须指向 ~/openclaw-model-bridge/jobs_registry.yaml")

    def test_v37_9_76_hotfix_three_candidates_in_search_order(self):
        """V37.9.76-hotfix: _load_yaml_job_profile 必须按 PATH → MAC_MINI → FALLBACK 顺序搜索.

        反向 sabotage: 若回退到 2 候选 (PATH + FALLBACK), Mac Mini 部署 router_decide 到 ~/
        会让两候选完全重合, silent fail. 守卫 candidates list 含 MAC_MINI.
        """
        with open(os.path.join(_REPO_ROOT, "router_decide.py")) as f:
            src = f.read()
        # candidates list 必须含三个常量
        self.assertIn("JOBS_REGISTRY_MAC_MINI", src,
                      "V37.9.76-hotfix: 必须用 JOBS_REGISTRY_MAC_MINI 常量")
        # 找 candidates 列表声明
        self.assertIn("[JOBS_REGISTRY_PATH, JOBS_REGISTRY_MAC_MINI, JOBS_REGISTRY_FALLBACK]", src,
                      "V37.9.76-hotfix: candidates 必须三档顺序锁 PATH→MAC_MINI→FALLBACK")


class TestRouterDecideCli(unittest.TestCase):
    """V37.9.76 Step 3: router_decide.py CLI subprocess 测试."""

    def test_cli_basic_invocation(self):
        """CLI --job-id kb_dream --no-log → stdout=doubao, exit 0."""
        result = subprocess.run(
            ["python3", os.path.join(_REPO_ROOT, "router_decide.py"),
             "--job-id", "kb_dream", "--task", "radar_retry", "--no-log"],
            capture_output=True, text=True, timeout=15,
            env={**os.environ, "PYTHONPATH": _REPO_ROOT},
        )
        self.assertEqual(result.returncode, 0, f"CLI exit 0, got {result.returncode}: {result.stderr}")
        self.assertEqual(result.stdout.strip(), "doubao",
                         "V37.9.76 CLI: stdout 默认是 chosen name")

    def test_cli_json_mode_outputs_full_record(self):
        """CLI --json → stdout 是完整 JSON record."""
        result = subprocess.run(
            ["python3", os.path.join(_REPO_ROOT, "router_decide.py"),
             "--job-id", "kb_dream", "--no-log", "--json"],
            capture_output=True, text=True, timeout=15,
            env={**os.environ, "PYTHONPATH": _REPO_ROOT},
        )
        self.assertEqual(result.returncode, 0)
        record = json.loads(result.stdout.strip())
        self.assertEqual(record["job_id"], "kb_dream")
        self.assertEqual(record["chosen"], "doubao")
        self.assertTrue(record["v37_9_76"])

    def test_cli_fail_open_on_unknown_job(self):
        """CLI 未知 job_id → stdout=no_router_profile, exit 0 (FAIL-OPEN)."""
        result = subprocess.run(
            ["python3", os.path.join(_REPO_ROOT, "router_decide.py"),
             "--job-id", "definitely_nonexistent_v37_9_76", "--no-log"],
            capture_output=True, text=True, timeout=15,
            env={**os.environ, "PYTHONPATH": _REPO_ROOT},
        )
        self.assertEqual(result.returncode, 0,
                         "V37.9.76 CLI: FAIL-OPEN 未知 job 仍 exit 0")
        self.assertIn("no_router_profile", result.stdout.strip())

    def test_cli_stderr_writes_diagnostics_not_stdout(self):
        """MR-11: log() 写 stderr 不污染 stdout 的命令替换."""
        result = subprocess.run(
            ["python3", os.path.join(_REPO_ROOT, "router_decide.py"),
             "--job-id", "kb_dream", "--no-log"],
            capture_output=True, text=True, timeout=15,
            env={**os.environ, "PYTHONPATH": _REPO_ROOT},
        )
        # stdout 应只有 "doubao" + 换行 — 没有 "[router_decide]" 字样
        self.assertNotIn("[router_decide]", result.stdout,
                         "V37.9.76 MR-11: log 不应污染 stdout (caller $() 命令替换会拿到)")
        # stderr 应有诊断
        self.assertIn("[router_decide]", result.stderr,
                      "V37.9.76 MR-11: log 写 stderr 留诊断")

    def test_cli_exclude_param(self):
        """CLI --exclude doubao → chosen=qwen."""
        result = subprocess.run(
            ["python3", os.path.join(_REPO_ROOT, "router_decide.py"),
             "--job-id", "kb_dream", "--exclude", "doubao", "--no-log"],
            capture_output=True, text=True, timeout=15,
            env={**os.environ, "PYTHONPATH": _REPO_ROOT},
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "qwen")


class TestJobsRegistrySchemaExtension(unittest.TestCase):
    """V37.9.76 Step 1: jobs_registry.yaml schema 扩展守卫."""

    def setUp(self):
        with open(os.path.join(_REPO_ROOT, "jobs_registry.yaml")) as f:
            self.yaml_src = f.read()

    def test_kb_dream_has_capability_profile(self):
        """kb_dream 必须有 V37.9.76 capability profile (required/prefer/cost_tier)."""
        # 找 kb_dream 段
        kb_dream_idx = self.yaml_src.find("- id: kb_dream\n")
        self.assertGreater(kb_dream_idx, 0)
        # 取 kb_dream 后 1500 字符内 (足够包含整个 entry)
        window = self.yaml_src[kb_dream_idx:kb_dream_idx + 1500]
        self.assertIn("required_capabilities:", window,
                      "V37.9.76: kb_dream 必须声明 required_capabilities")
        self.assertIn("prefer:", window,
                      "V37.9.76: kb_dream 必须声明 prefer")
        self.assertIn("cost_tier:", window,
                      "V37.9.76: kb_dream 必须声明 cost_tier")
        self.assertIn("V37.9.76", window,
                      "V37.9.76: kb_dream profile 必须带 marker (审计可追)")

    def test_hf_papers_has_capability_profile(self):
        """hf_papers 必须有 V37.9.76 capability profile."""
        idx = self.yaml_src.find("- id: hf_papers\n")
        self.assertGreater(idx, 0)
        window = self.yaml_src[idx:idx + 1500]
        self.assertIn("required_capabilities:", window,
                      "V37.9.76: hf_papers 必须声明 required_capabilities")
        self.assertIn("cost_tier:", window)
        self.assertIn("V37.9.76", window)

    def test_arxiv_monitor_does_not_have_profile_yet(self):
        """V37.9.76 PoC 仅 2 个 job 试水, arxiv_monitor 等其他 job 暂不声明.

        守卫此契约: 防 V37.9.76+ 错误批量推广到全部 job 破坏渐进策略.
        V37.9.77+ 一周观察后才考虑扩到更多 job.
        """
        idx = self.yaml_src.find("- id: arxiv_monitor\n")
        self.assertGreater(idx, 0)
        # arxiv_monitor entry 长度约 300 字符 (含 kb_source_file/label/description)
        # 安全 window 500 字符
        window = self.yaml_src[idx:idx + 500]
        # arxiv_monitor 不应有 V37.9.76 profile fields
        self.assertNotIn("required_capabilities:", window,
                         "V37.9.76 PoC scope: arxiv_monitor 暂不试水 (V37.9.77+ 评估扩展)")


class TestKbDreamShadowIntegration(unittest.TestCase):
    """V37.9.76 Step 3: kb_dream.sh RADAR retry 集成守卫."""

    def setUp(self):
        with open(os.path.join(_REPO_ROOT, "kb_dream.sh")) as f:
            self.src = f.read()

    def test_router_decide_called_before_radar_retry_llm_call(self):
        """V37.9.76 router_decide.py 调用必须在 RADAR retry llm_call 之前 (shadow log 先).

        V37.9.77 扩窗 500→1000: ROUTER_ENFORCE 块插入推开 router_decide 到 llm_call 距离 (合法演进).
        语义不变: router_decide 必须在 llm_call 之前出现, 不允许漂移到无关位置.
        """
        # 找 RADAR_RETRY_RESULT=$(llm_call ...) 行
        retry_call_idx = self.src.find('RADAR_RETRY_RESULT=$(llm_call "$RADAR_RETRY_PROMPT"')
        self.assertGreater(retry_call_idx, 0, "RADAR retry llm_call 必须存在")
        # retry_call_idx 之前 1000 字符内必须有 router_decide.py 调用 (V37.9.77 扩窗容纳 enforcement 块)
        window = self.src[max(0, retry_call_idx - 1000):retry_call_idx]
        self.assertIn("router_decide.py", window,
                      "V37.9.76: router_decide.py 调用必须在 RADAR retry llm_call 之前 1000 字符内")
        self.assertIn("ROUTER_CHOICE", window,
                      "V37.9.76: 必须用 ROUTER_CHOICE 变量捕获决策")

    def test_router_call_passes_correct_job_id_and_task(self):
        """router_decide 调用必须传 --job-id kb_dream --task radar_retry."""
        self.assertIn("--job-id kb_dream --task radar_retry", self.src,
                      "V37.9.76: kb_dream RADAR retry 必须传正确的 job_id + task label")

    def test_router_call_is_fail_open(self):
        """router_decide 调用必须 FAIL-OPEN (2>/dev/null || true)."""
        # 找 router_decide.py 调用行
        idx = self.src.find('python3 "$HOME/router_decide.py"')
        self.assertGreater(idx, 0)
        # 该行后 200 字符内必须有 2>/dev/null + || true (FAIL-OPEN)
        window = self.src[idx:idx + 300]
        self.assertIn("2>/dev/null", window,
                      "V37.9.76 FAIL-OPEN: router 调用必须 redirect stderr 避污染 caller log")
        self.assertIn("|| true", window,
                      "V37.9.76 FAIL-OPEN: router 调用必须有 || true 兜底 (异常不阻塞)")

    def test_router_dual_path_lookup(self):
        """router_decide.py 路径必须双 fallback: $HOME/ + $SCRIPT_DIR/ (dev/Mac Mini 兼容)."""
        # $HOME/router_decide.py 是 Mac Mini 部署路径 (auto_deploy FILE_MAP)
        self.assertIn('$HOME/router_decide.py', self.src)
        # $SCRIPT_DIR/router_decide.py 是 dev 环境路径 (源码同目录)
        self.assertIn('$SCRIPT_DIR/router_decide.py', self.src)

    def test_shadow_mode_explicit_in_log(self):
        """日志必须显式标 shadow 模式 (运维一眼看出不是 enforcement)."""
        self.assertIn("V37.9.76 router (shadow)", self.src,
                      "V37.9.76: 必须显式标 shadow 模式 + V37.9.76 marker")
        self.assertIn("decision logged, not enforced", self.src,
                      "V37.9.76: shadow 模式必须显式说明不强制路由")


class TestHfPapersShadowIntegration(unittest.TestCase):
    """V37.9.76 Step 3: hf_papers run script 集成守卫."""

    def setUp(self):
        with open(os.path.join(_REPO_ROOT, "jobs/hf_papers/run_hf_papers.sh")) as f:
            self.src = f.read()

    def test_router_called_before_llm_call(self):
        """router_decide 必须在 call_llm_single_with_retry 之前调用."""
        retry_idx = self.src.find('if RESULT=$(call_llm_single_with_retry')
        self.assertGreater(retry_idx, 0)
        window = self.src[max(0, retry_idx - 500):retry_idx]
        self.assertIn("router_decide.py", window,
                      "V37.9.76: hf_papers router_decide 必须在 llm 调用前")

    def test_router_passes_correct_job_id_task(self):
        """hf_papers 必须传 --job-id hf_papers --task per_paper."""
        self.assertIn("--job-id hf_papers --task per_paper", self.src,
                      "V37.9.76: hf_papers router 调用必须传 per_paper task label")

    def test_fail_open_contract(self):
        """hf_papers router 调用必须 FAIL-OPEN (cron 不依赖 router 成功)."""
        idx = self.src.find('python3 "$HOME/router_decide.py"')
        self.assertGreater(idx, 0)
        window = self.src[idx:idx + 300]
        self.assertIn("2>/dev/null", window)
        self.assertIn("|| true", window)


class TestAutoDeployFileMap(unittest.TestCase):
    """V37.9.76 Step 3-4: auto_deploy FILE_MAP 部署守卫."""

    def setUp(self):
        with open(os.path.join(_REPO_ROOT, "auto_deploy.sh")) as f:
            self.src = f.read()

    def test_router_decide_in_file_map(self):
        """router_decide.py 必须在 FILE_MAP 中部署到 Mac Mini $HOME/."""
        self.assertIn(
            'router_decide.py|$HOME/router_decide.py',
            self.src,
            "V37.9.76: router_decide.py 必须在 auto_deploy FILE_MAP 部署到 Mac Mini",
        )
        # marker 注释审计可追
        self.assertIn("V37.9.76", self.src)


class TestV37976SourceLevelGuards(unittest.TestCase):
    """V37.9.76 综合源码级守卫 (反向 sabotage 真有效)."""

    def setUp(self):
        with open(os.path.join(_REPO_ROOT, "providers.py")) as f:
            self.providers_src = f.read()
        with open(os.path.join(_REPO_ROOT, "router_decide.py")) as f:
            self.router_src = f.read()

    def test_find_best_provider_exists_in_providers(self):
        """providers.py 必须含 find_best_provider 定义 (Step 2 核心交付)."""
        self.assertIn("def find_best_provider(", self.providers_src,
                      "V37.9.76: providers.py 必须有 find_best_provider")

    def test_find_best_provider_has_required_prefer_exclude_params(self):
        """find_best_provider 签名必须含 required/prefer/exclude 三参数 (V37.9.76 锁定 API)."""
        self.assertIn("required: Optional[Dict[str, bool]]", self.providers_src,
                      "V37.9.76: find_best_provider 必须接受 required: dict 参数")
        self.assertIn("prefer: Optional[List[str]]", self.providers_src,
                      "V37.9.76: find_best_provider 必须接受 prefer: list 参数")
        self.assertIn("exclude: Optional[List[str]]", self.providers_src,
                      "V37.9.76: find_best_provider 必须接受 exclude: list 参数")

    def test_router_decide_has_v37_9_76_marker(self):
        """router_decide.py 必须含 V37.9.76 marker (审计可追)."""
        self.assertIn("V37.9.76", self.router_src,
                      "V37.9.76: router_decide.py 必须含 marker")

    def test_router_decide_mode_default_is_shadow(self):
        """shadow 模式必须是默认 (PoC 阶段 enforcement 不可启用)."""
        self.assertIn('_MODE_DEFAULT = "shadow"', self.router_src,
                      "V37.9.76: 默认 mode 必须是 shadow (PoC enforcement 不可启用)")

    def test_router_decide_log_writes_to_stderr_mr11(self):
        """MR-11: log() 必须写 stderr (防 $() 命令替换污染)."""
        self.assertIn("file=sys.stderr", self.router_src,
                      "V37.9.76 MR-11: log() 必须显式写 sys.stderr")

    def test_router_decide_uses_lazy_import_for_yaml_providers(self):
        """providers + yaml 必须 lazy import (避 dev 环境模块缺失炸 caller)."""
        # PyYAML lazy import 在 _load_yaml_job_profile 内
        self.assertIn("import yaml", self.router_src,
                      "V37.9.76: lazy import yaml 必须在函数内")
        # providers lazy import 在 _call_find_best_provider 内
        self.assertIn("from providers import _default_registry", self.router_src,
                      "V37.9.76: lazy import providers 必须在函数内")
        # 模块顶部不能直接 import 这些重依赖
        top_60_lines = self.router_src[:3000]  # 前 60 行 ≈ 3000 字符
        self.assertNotIn("\nimport yaml\n", top_60_lines,
                         "V37.9.76: 模块顶部禁直接 import yaml (lazy import 契约)")

    def test_jsonl_path_locked_to_home_kb(self):
        """JSONL 路径必须锁定 ~/.kb/router_decisions.jsonl (V3 路标观察期数据真理源)."""
        self.assertIn('JSONL_PATH = os.path.expanduser("~/.kb/router_decisions.jsonl")', self.router_src,
                      "V37.9.76: JSONL_PATH 必须锁定 ~/.kb/router_decisions.jsonl")


if __name__ == "__main__":
    unittest.main(verbosity=2)
