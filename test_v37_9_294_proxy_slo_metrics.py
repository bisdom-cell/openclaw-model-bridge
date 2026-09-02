#!/usr/bin/env python3
"""test_v37_9_294_proxy_slo_metrics.py — proxy 观测口径守卫（V37.9.294，对抗审计 C-F3/C-F4）

unfinished [33] 登记的四机制（LOW-MED，SLO 口径诚实性）：
  (a) C-F3 REST /data_clean 四站点裸 subprocess.run — TimeoutExpired/OSError 穿透
      handler → 连接直断空回复（与 LLM 拦截路径 try/except 不对称，运维面误导重启）
      → `_run_data_clean_subprocess` 统一执行（504/500 结构化 JSON，一物一形）
  (b) C-F4 followup 失败合成响应无 usage → record_success 记干净成功 + 原始 KB
      转储被当 PA 回复捕获进对话精华（KB 自反馈污染）
      → `_proxy_followup_degraded` 哨兵：record_fallback（降级进 SLO）+ 不捕获 + pop 不下传
  (c) C-F4 elapsed 在 resp.read() 后冻结 — 自定义工具（search_kb 语义检索 + followup
      LLM 最多 ~90s+）不计入 → p95 系统性低估
      → record_success/record_error 前重算（backend 日志行保留 backend-only 延迟）
  (d) C-F4 tool_ok = result[:100] 子串窗口 — expert read_only_violation /
      api_unavailable 等失败态记成 success（tool_success_rate 假 100%）
      → `classify_tool_result_ok` 纯函数（proxy_filters，JSON 结构化判定）

tool_proxy 顶层 serve_forever 不可 import（V37.9.226/288 惯例）→ 方法级测试用
AST 抽源 + exec-with-mocks（镜像 test_v37_9_288）。
"""

import ast
import io
import json
import os
import re
import subprocess
import sys
import textwrap
import types
import unittest

REPO = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO)

from proxy_filters import classify_tool_result_ok  # noqa: E402

_TOOL_PROXY = os.path.join(REPO, "tool_proxy.py")
_PROXY_FILTERS = os.path.join(REPO, "proxy_filters.py")


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _extract_method(method_name):
    """从 ProxyHandler 类体抽单个方法源码（不 import 模块，V37.9.288 同款）"""
    src = _read(_TOOL_PROXY)
    tree = ast.parse(src)
    lines = src.splitlines(keepends=True)
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "ProxyHandler":
            for sub in node.body:
                if isinstance(sub, ast.FunctionDef) and sub.name == method_name:
                    return textwrap.dedent(
                        "".join(lines[sub.lineno - 1:sub.end_lineno]))
    raise AssertionError(f"ProxyHandler 缺方法: {method_name}")


class TestClassifyToolResultOk(unittest.TestCase):
    """(d) 纯函数行为 — SLO tool_success_rate 口径"""

    def test_expert_read_only_violation_is_failure(self):
        """血案核心: 拒绝态曾被 result[:100] 窗口记成 success"""
        self.assertFalse(classify_tool_result_ok(
            '{"status": "read_only_violation", "message": "拒绝执行写操作"}'))

    def test_expert_api_unavailable_is_failure(self):
        self.assertFalse(classify_tool_result_ok(
            '{"status": "api_unavailable", "message": "DOUBAO_21_TOKENHUB_API_KEY not set"}'))

    def test_expert_quota_is_failure(self):
        self.assertFalse(classify_tool_result_ok('{"status": "quota_exceeded"}'))

    def test_expert_ok_is_success(self):
        self.assertTrue(classify_tool_result_ok(
            '{"status": "ok", "proposal": "使用 launchctl bootout"}'))

    def test_error_key_is_failure(self):
        self.assertFalse(classify_tool_result_ok('{"error": "文件不存在: x.csv"}'))

    def test_data_clean_success_no_status_key(self):
        """data_clean 成功输出无 status 键 → success（不误报）"""
        self.assertTrue(classify_tool_result_ok(
            '{"input": "f.csv", "original_rows": 10, "final_rows": 8, "steps": []}'))

    def test_plain_text_is_success(self):
        """search_kb 等纯文本结果 → success（FAIL-OPEN 不对无结构结果误报）"""
        self.assertTrue(classify_tool_result_ok("找到 3 条相关笔记：..."))

    def test_malformed_json_falls_back_to_heuristic(self):
        self.assertFalse(classify_tool_result_ok('{"error": "boom", broken'))
        self.assertTrue(classify_tool_result_ok('{"data": "fine", broken'))

    def test_non_dict_and_non_str_are_success(self):
        self.assertTrue(classify_tool_result_ok('[1, 2, 3]'))
        self.assertTrue(classify_tool_result_ok(None))


class TestRestSubprocessHardening(unittest.TestCase):
    """(a) REST data_clean subprocess 统一执行（行为级 exec-with-mocks + 源码守卫）"""

    def _make(self, run_impl):
        src = _extract_method("_run_data_clean_subprocess")
        responses = []

        class StubSelf:
            def _json_response(self, code, payload):
                responses.append((code, payload))

        ns = {
            "subprocess": types.SimpleNamespace(
                run=run_impl, TimeoutExpired=subprocess.TimeoutExpired),
            "log": lambda *a, **k: None,
        }
        exec(compile(src, "<_run_data_clean_subprocess>", "exec"), ns)
        return ns["_run_data_clean_subprocess"], StubSelf(), responses

    def test_timeout_returns_none_and_504(self):
        def raise_timeout(*a, **k):
            raise subprocess.TimeoutExpired(cmd=a[0], timeout=k.get("timeout"))
        fn, stub, responses = self._make(raise_timeout)
        result = fn(stub, ["python3", "data_clean.py", "profile", "f.csv"], 30, "profile")
        self.assertIsNone(result, "超时必须返回 None（调用方直接 return）")
        self.assertEqual(len(responses), 1)
        self.assertEqual(responses[0][0], 504, "超时必须发 504 结构化响应（旧版连接直断空回复）")
        self.assertIn("超时", responses[0][1]["error"])

    def test_oserror_returns_none_and_500(self):
        def raise_oserror(*a, **k):
            raise OSError(2, "No such file or directory")
        fn, stub, responses = self._make(raise_oserror)
        result = fn(stub, ["python3", "gone.py"], 10, "list-ops")
        self.assertIsNone(result)
        self.assertEqual(responses[0][0], 500)

    def test_success_passthrough_no_response(self):
        marker = types.SimpleNamespace(stdout='{"ok": true}', stderr="")
        fn, stub, responses = self._make(lambda *a, **k: marker)
        result = fn(stub, ["python3", "x"], 10, "profile")
        self.assertIs(result, marker)
        self.assertEqual(responses, [], "成功路径不得发响应（由调用方处理 stdout）")

    def test_all_four_rest_sites_converted(self):
        """_handle_data_clean 内零裸 subprocess.run，四站点全走统一执行器"""
        src = _extract_method("_handle_data_clean")
        self.assertNotIn("subprocess.run(", src,
                         "REST 路径出现裸 subprocess.run → TimeoutExpired 穿透连接直断（血案 a 回归）")
        self.assertEqual(src.count("self._run_data_clean_subprocess("), 4,
                         "四站点 (list-ops/profile/execute/validate) 必须全部收编")
        self.assertEqual(src.count("if result is None:"), 4,
                         "每站点必须处理 None（响应已发，直接 return）")


class TestFollowupDegradedSentinel(unittest.TestCase):
    """(b) followup 失败降级哨兵（行为级 + do_POST wiring 源码守卫）"""

    def test_failed_followup_carries_sentinel(self):
        src = _extract_method("_followup_llm_call")

        def raise_urlopen(*a, **k):
            raise OSError("backend down")

        ns = {
            "json": json,
            "log": lambda *a, **k: None,
            "Request": lambda *a, **k: types.SimpleNamespace(add_header=lambda *x: None),
            "_safe_urlopen": raise_urlopen,
            "BACKEND": "http://127.0.0.1:5001",
        }
        exec(compile(src, "<_followup_llm_call>", "exec"), ns)
        stub = types.SimpleNamespace()
        rj = ns["_followup_llm_call"](stub, {"model": "m", "messages": []}, "KB 原始结果" * 10, "rid1")
        self.assertTrue(rj.get("_proxy_followup_degraded"),
                        "失败 followup 必须携带降级哨兵（旧版伪装干净成功）")
        self.assertIn("KB 原始结果", rj["choices"][0]["message"]["content"])

    def test_do_post_consumes_sentinel(self):
        src = _extract_method("do_POST")
        self.assertIn('rj.pop("_proxy_followup_degraded"', src,
                      "do_POST 必须消费并 pop 哨兵（防下传 Gateway）")
        m = re.search(r"_followup_degraded = \(.*?\n(.*?)# Log model decision", src, re.S)
        self.assertIsNotNone(m)
        self.assertIn("proxy_stats.record_fallback()", m.group(0),
                      "降级必须进 SLO degradation_rate（V37.9.229 同款语义）")

    def test_degraded_dump_not_captured(self):
        """原始 KB 转储不得捕获进对话精华（KB 自反馈污染）"""
        src = _extract_method("do_POST")
        self.assertIn("not _routed_provider and not _followup_degraded", src,
                      "content 捕获分支必须双守卫（V37.9.272 路由 + V37.9.294 降级）")


class TestElapsedRecompute(unittest.TestCase):
    """(c) elapsed 冻结修复 — SLO 记全轮延迟"""

    def setUp(self):
        self.do_post = _extract_method("do_POST")
        self.recompute = "elapsed = int((time.monotonic() - t0) * 1000)"

    def test_recompute_before_record_success(self):
        m = re.search(r"_followup_degraded = \(.*?usage = rj\.get\(\"usage\"", self.do_post, re.S)
        self.assertIsNotNone(m)
        self.assertIn(self.recompute, m.group(0),
                      "record_success 前必须重算 elapsed（旧冻结值漏计自定义工具最多 ~90s+）")

    def test_recompute_in_parse_except(self):
        m = re.search(r"except Exception as e:\s*\n\s+log\(f\"\[\{rid\}\] Parse error.*?record_error",
                      self.do_post, re.S)
        self.assertIsNotNone(m)
        self.assertIn(self.recompute, m.group(0),
                      "parse-except 的 record_error 前必须重算 elapsed")

    def test_backend_log_line_keeps_backend_only_latency(self):
        """backend 日志行保留 backend-only 延迟作独立信号（不被重算覆盖语义）"""
        m = re.search(r"elapsed = int\(\(time\.monotonic\(\) - t0\) \* 1000\)\s*\n\s*"
                      r"log\(f\"\[\{rid\}\] Backend:", self.do_post)
        self.assertIsNotNone(m, "Backend 日志行前的原始 elapsed 计算必须保留")


class TestSourceGuards(unittest.TestCase):
    """标记与退役守卫"""

    def test_markers(self):
        self.assertIn("V37.9.294", _read(_TOOL_PROXY))
        self.assertIn("V37.9.294", _read(_PROXY_FILTERS))

    def test_tool_ok_uses_classifier(self):
        src = _read(_TOOL_PROXY)
        self.assertIn("tool_ok = classify_tool_result_ok(result)", src)
        self.assertNotIn("'\"error\"' in result[:100]", src,
                         "旧 result[:100] 子串窗口必须退役（血案 d 回归）")

    def test_classifier_imported_from_proxy_filters(self):
        # 闭括号锚定行首 (import 注释里的 ")" 会让非贪婪 .*?) 提前截断)
        m = re.search(r"from proxy_filters import \((.*?)\n\)", _read(_TOOL_PROXY), re.S)
        self.assertIsNotNone(m)
        self.assertIn("classify_tool_result_ok", m.group(1),
                      "分类器必须从 proxy_filters 导入（纯函数单测层）")


if __name__ == "__main__":
    unittest.main(verbosity=1)
