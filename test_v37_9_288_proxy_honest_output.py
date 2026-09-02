#!/usr/bin/env python3
"""V37.9.288 (对抗审计批次 2/2) — proxy 诚实输出守卫.

四类修复的行为级 + 源码级守卫:
  C-F5-B  自定义工具参数 null-归一 (LLM 发 {"key": null} 曾 AttributeError
          逃出拦截层, 伪装成后端 502)
  C-F5-A  fix_date_cols 从 schema 到拦截解析全链路打通 (曾被静默丢弃 →
          op_fix_dates 退回全表自动检测)
  B-F3    kb_rag/local_embed sys.exit → SystemExit 收编 (曾穿透全部
          except Exception 在 HTTP 线程内静默杀请求)
  B-F2    "搜索坏了" vs "没搜到" 语义区分 (曾同形塌缩 + "每日自动更新"
          假保证 → 假否定 fail-plausible)
  C-F2    expert_escalate 按 SOUL 规则 12 渲染 (曾裸 JSON / 英文报错直发用户)
  C-F1    data_clean per-step 失败诚实渲染 (error/skipped/unfixable 曾一律 ✓)

测试模式（项目惯例, 镜像 test_v37_9_226: tool_proxy.py 顶层 serve_forever 无
__main__ guard 不可 import）: AST 抽 ProxyHandler 方法源码 + exec-with-mocks
进隔离 namespace + stub self, 行为验证; 另配源码级守卫防回退。
"""
import ast
import json
import os
import subprocess
import sys
import textwrap
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import memory_plane  # noqa: E402  (import-safe; test_memory_plane 同款直接 import)

_REPO = os.path.dirname(os.path.abspath(__file__))
_TOOL_PROXY = os.path.join(_REPO, "tool_proxy.py")


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _extract_handler_bits(method_names):
    """从 ProxyHandler 类体抽方法源码 + VALID_SOURCES 类属性 (不 import 模块)."""
    src = _read(_TOOL_PROXY)
    tree = ast.parse(src)
    lines = src.splitlines(keepends=True)
    methods = {}
    valid_sources = {"all"}
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "ProxyHandler":
            for sub in node.body:
                if isinstance(sub, ast.FunctionDef) and sub.name in method_names:
                    methods[sub.name] = textwrap.dedent(
                        "".join(lines[sub.lineno - 1:sub.end_lineno]))
                if isinstance(sub, ast.Assign) and any(
                        getattr(t, "id", None) == "VALID_SOURCES"
                        for t in sub.targets):
                    valid_sources = ast.literal_eval(sub.value)
    missing = set(method_names) - set(methods)
    assert not missing, f"ProxyHandler 缺方法: {missing}"
    return methods, valid_sources


def _make_fn(method_name, extra_globals=None):
    """exec 单个方法进隔离 namespace, 返回 (fn, ns)."""
    methods, valid_sources = _extract_handler_bits({method_name})
    fake_os = types.SimpleNamespace(path=types.SimpleNamespace(
        exists=lambda p: True,
        expanduser=os.path.expanduser,
        join=os.path.join, dirname=os.path.dirname, abspath=os.path.abspath,
    ), sep=os.sep, environ=os.environ)
    ns = {
        "json": json, "sys": sys, "os": fake_os,
        "subprocess": types.SimpleNamespace(
            run=lambda *a, **k: (_ for _ in ()).throw(
                AssertionError("subprocess.run 未被测试 mock")),
            TimeoutExpired=subprocess.TimeoutExpired),
        "time": __import__("time"),
        "log": lambda *a, **k: None,
        "_confine_data_path": lambda p: (True, p),
        "uuid": __import__("uuid"),
    }
    if extra_globals:
        ns.update(extra_globals)
    exec(compile(methods[method_name], f"<{method_name}>", "exec"), ns)
    return ns[method_name], ns, valid_sources


class _StubSelf:
    pass


class TestNullArgsSafety(unittest.TestCase):
    """C-F5-B: 参数 null-归一 (行为级, exec-with-mocks)."""

    def setUp(self):
        self.fn, self.ns, vs = _make_fn("_execute_custom_tool")
        self.h = _StubSelf()
        self.h.VALID_SOURCES = vs

    def test_search_kb_null_query_with_recent_hours(self):
        # schema 明写 "使用 recent_hours 时 query 可为空" — {"query": null,
        # "recent_hours": 24} 是模型预期行为, 曾 None.strip() AttributeError
        self.h._search_kb = mock.Mock(return_value="STUB_RESULT")
        r = self.fn(self.h, "search_kb",
                    json.dumps({"query": None, "recent_hours": 24}))
        self.assertEqual(r, "STUB_RESULT")
        self.assertEqual(self.h._search_kb.call_args[0][0], "recent")

    def test_search_kb_null_query_without_recent_returns_error(self):
        self.h._search_kb = mock.Mock()
        r = self.fn(self.h, "search_kb", json.dumps({"query": None}))
        self.assertIn("error", json.loads(r))
        self.h._search_kb.assert_not_called()

    def test_search_kb_null_source_falls_back_to_all(self):
        self.h._search_kb = mock.Mock(return_value="ok")
        self.fn(self.h, "search_kb",
                json.dumps({"query": "q", "source": None}))
        self.assertEqual(self.h._search_kb.call_args[0][1], "all")

    def test_data_clean_null_config_no_crash(self):
        # config: null 曾在 config.get(...) 处 AttributeError (伪装成后端 502)
        self.h._data_clean_path = lambda: "/fake/data_clean.py"
        captured = {}

        def fake_run(cmd, **k):
            captured["cmd"] = cmd
            return types.SimpleNamespace(stdout="{}", stderr="", returncode=0)
        self.ns["subprocess"] = types.SimpleNamespace(
            run=fake_run, TimeoutExpired=subprocess.TimeoutExpired)
        r = self.fn(self.h, "data_clean", json.dumps({
            "action": "execute", "file": "/tmp/x.csv",
            "config": None, "ops": None, "fix_case_cols": None,
            "fix_date_cols": None}))
        self.assertNotIn("AttributeError", str(r))
        self.assertIn("--ops", captured["cmd"])


class TestFixDateColsPlumbed(unittest.TestCase):
    """C-F5-A: fix_date_cols 拦截路径打通."""

    def test_fix_date_cols_reaches_cli(self):
        fn, ns, vs = _make_fn("_execute_custom_tool")
        h = _StubSelf()
        h._data_clean_path = lambda: "/fake/data_clean.py"
        captured = {}

        def fake_run(cmd, **k):
            captured["cmd"] = cmd
            return types.SimpleNamespace(stdout="{}", stderr="", returncode=0)
        ns["subprocess"] = types.SimpleNamespace(
            run=fake_run, TimeoutExpired=subprocess.TimeoutExpired)
        fn(h, "data_clean", json.dumps({
            "action": "execute", "file": "/tmp/x.csv",
            "ops": "fix_dates", "fix_date_cols": "order_date"}))
        self.assertIn("--fix-date-cols", captured["cmd"],
                      "C-F5-A: LLM 按 schema 传的列限制曾被静默丢弃")
        self.assertIn("order_date", captured["cmd"])

    def test_source_has_flag_in_both_paths(self):
        # LLM 拦截路径 + REST 路径都必须支持 --fix-date-cols
        src = _read(_TOOL_PROXY)
        self.assertGreaterEqual(src.count('"--fix-date-cols"'), 2)


class TestSemanticSearchSentinel(unittest.TestCase):
    """B-F2/B-F3: _semantic_search 返回 (results, degraded_reason) + SystemExit 收编."""

    def _run(self, mp_query, sub_run=None):
        fn, ns, _ = _make_fn("_semantic_search")
        fake_mp = types.ModuleType("memory_plane")
        fake_mp.query = mp_query
        if sub_run is not None:
            ns["subprocess"] = types.SimpleNamespace(
                run=sub_run, TimeoutExpired=subprocess.TimeoutExpired)
        with mock.patch.dict(sys.modules, {"memory_plane": fake_mp}):
            return fn(_StubSelf(), "q")

    def test_healthy_empty_is_not_degraded(self):
        results, degraded = self._run(lambda *a, **k: [])
        self.assertEqual(results, [])
        self.assertIsNone(degraded, "检索完成 0 命中 ≠ 检索坏了")

    def test_systemexit_contained_and_degraded_detected(self):
        # B-F3 血案链: kb_rag sys.exit(1) (vectors.bin 损坏) → in-process 撞
        # SystemExit → 曾穿透杀死 HTTP 线程; 现收编 → subprocess 回退 →
        # fresh 进程 exit 1 → returncode 检测 → 显式 degraded
        def boom(*a, **k):
            raise SystemExit(1)

        def sub_fail(*a, **k):
            return types.SimpleNamespace(returncode=1, stdout="",
                                         stderr="向量文件损坏")
        results, degraded = self._run(boom, sub_run=sub_fail)
        self.assertEqual(results, [])
        self.assertIsNotNone(degraded, "B-F3: SystemExit 必须被收编且检测为降级")
        self.assertIn("exit 1", degraded)

    def test_subprocess_timeout_is_degraded(self):
        def boom(*a, **k):
            raise ImportError("no module")

        def sub_timeout(*a, **k):
            raise subprocess.TimeoutExpired(cmd="kb_rag", timeout=30)
        results, degraded = self._run(boom, sub_run=sub_timeout)
        self.assertEqual(results, [])
        self.assertIn("超时", degraded)


class TestSearchKbHonestMessages(unittest.TestCase):
    """B-F2: 假否定与假保证退役."""

    def _search(self, sem_return, kw_return):
        fn, ns, vs = _make_fn("_search_kb")
        h = _StubSelf()
        h.VALID_SOURCES = vs
        h._semantic_search = mock.Mock(return_value=sem_return)
        h._keyword_search = mock.Mock(return_value=kw_return)
        return fn(h, "qwen3 moe")

    def test_degraded_no_results_does_not_claim_not_found(self):
        r = self._search(([], "语义检索进程异常退出 (exit 1)"), "")
        self.assertIn("检索系统异常", r)
        self.assertNotIn("未找到", r,
                         "检索坏了不得断言'知识库中没有' (假否定 fail-plausible)")

    def test_healthy_not_found_is_honest_no_false_assurance(self):
        r = self._search(([], None), "")
        self.assertIn("未找到", r)
        self.assertNotIn("每日自动更新", r,
                         "索引新鲜度在查询时不可验证, 假保证已退役")
        self.assertNotIn("检索系统异常", r)

    def test_degraded_with_keyword_hits_is_labeled(self):
        r = self._search(([], "语义检索超时 (30s)"), "[notes] 命中片段…")
        self.assertIn("语义检索层异常", r)
        self.assertIn("命中片段", r)


class TestExpertEscalateRendering(unittest.TestCase):
    """C-F2: SOUL 规则 12 渲染契约."""

    def setUp(self):
        # 存实例属性 (类属性会触发描述符绑定把函数变 bound method)
        self.fn, _, _ = _make_fn("_format_tool_result")

    def _fmt(self, payload):
        # _format_tool_result 是 @staticmethod (fn_name, fn_args_str, result)
        return self.fn("expert_escalate", "{}",
                       json.dumps(payload, ensure_ascii=False))

    def test_ok_renders_proposal_not_raw_json(self):
        r = self._fmt({"status": "ok", "backend": "doubao_21_tokenhub",
                       "proposal": "建议采用方案A", "rationale": "因为X",
                       "confidence": "high", "refs": ["case1.md"],
                       "usage": {"prompt_tokens": 999}})
        self.assertIn("专家意见", r)
        self.assertIn("建议采用方案A", r)
        self.assertIn("置信度", r)
        self.assertNotIn("prompt_tokens", r, "usage 内部字段不得直发用户")
        self.assertNotIn('"status"', r, "裸 JSON dump 已退役")

    def test_api_unavailable_chinese_degradation_hint(self):
        r = self._fmt({"status": "api_unavailable",
                       "error": "ARK_API_KEY not set. Configure in Mac Mini plist"})
        self.assertIn("基础模式", r, "SOUL 规则 12: api_unavailable → 引导基础模式")
        self.assertNotIn("ARK_API_KEY", r, "英文运维指引不得直发 WhatsApp 用户")
        self.assertNotIn("❌ 错误", r)

    def test_quota_exceeded_template(self):
        r = self._fmt({"status": "quota_exceeded", "error": "daily quota 30/30"})
        self.assertIn("配额", r)

    def test_read_only_violation_template(self):
        r = self._fmt({"status": "read_only_violation",
                       "violations": ["rm -rf x"], "error": "shell command found"})
        self.assertIn("read-only", r)
        self.assertIn("拒绝", r)

    def test_unknown_status_falls_back_honestly(self):
        r = self._fmt({"status": "parse_failed", "error": "bad json"})
        self.assertIn("未完成", r)
        self.assertIn("parse_failed", r)


class TestExecuteStepRendering(unittest.TestCase):
    """C-F1: per-step 局部失败诚实渲染."""

    def setUp(self):
        self.fn, _, _ = _make_fn("_format_tool_result")

    def _fmt(self, report):
        return self.fn("data_clean",
                       json.dumps({"action": "execute"}),
                       json.dumps(report, ensure_ascii=False))

    def test_skipped_and_error_steps_not_green(self):
        # 血案场景: fix_case 未传目标列 → skipped, 用户曾看到 "✓ fix_case"
        r = self._fmt({
            "input": "sales.xlsx", "original_rows": 5000, "final_rows": 4998,
            "output": "sales_cleaned.xlsx",
            "steps": [
                {"operation": "trim", "cells_trimmed": 12},
                {"operation": "fix_case", "skipped": "未指定目标列"},
                {"operation": "remove_duplicates",
                 "error": "未知操作: remove_duplicates"},
            ]})
        self.assertIn("⚠️ fix_case 跳过", r)
        self.assertIn("❌ remove_duplicates 失败", r)
        self.assertNotIn("✓ fix_case", r)
        self.assertNotIn("✓ remove_duplicates", r)
        self.assertIn("2 个操作未执行/失败", r)
        self.assertFalse(r.startswith("✅"), "有局部失败时 header 不得全绿")

    def test_all_clean_still_green(self):
        r = self._fmt({
            "input": "a.csv", "original_rows": 10, "final_rows": 9,
            "output": "a_cleaned.csv",
            "steps": [{"operation": "trim", "cells_trimmed": 3},
                      {"operation": "dedup", "rows_removed": 1}]})
        self.assertTrue(r.startswith("✅"))
        self.assertIn("✓ trim", r)

    def test_fix_dates_shows_columns_and_unfixable(self):
        # C-F5-A 可观测性半边: fix_dates 实际改动列 + 无法解析计数可见
        r = self._fmt({
            "input": "a.csv", "original_rows": 10, "final_rows": 10,
            "output": "a_cleaned.csv",
            "steps": [{"operation": "fix_dates", "dates_fixed": 7,
                       "columns": ["order_date", "ship_date"],
                       "unfixable": [{"row": 3, "column": "order_date",
                                      "value": "n/a"}]}]})
        self.assertIn("order_date", r)
        self.assertIn("1 个值无法解析", r)


class TestMemoryPlaneSystemExitContainment(unittest.TestCase):
    """B-F3: memory_plane 层级边界收编 SystemExit."""

    def test_kb_search_contains_systemexit(self):
        fake_rag = types.ModuleType("kb_rag")

        def bad_search(*a, **k):
            sys.exit(1)
        fake_rag.search = bad_search
        with mock.patch.dict(sys.modules, {"kb_rag": fake_rag}):
            r = memory_plane._kb_search("q", top_k=3)
        self.assertEqual(r, [], "SystemExit 必须降级为空结果, 不得穿透")

    def test_source_guards(self):
        src = _read(os.path.join(_REPO, "memory_plane.py"))
        self.assertGreaterEqual(src.count("except (Exception, SystemExit)"), 2,
                                "_kb_search + query 双站点都必须收编 SystemExit")
        self.assertIn("V37.9.288", src)
        tp = _read(_TOOL_PROXY)
        self.assertIn("except (Exception, SystemExit) as e:", tp,
                      "_semantic_search in-process 路径必须收编 SystemExit")


if __name__ == "__main__":
    unittest.main()
