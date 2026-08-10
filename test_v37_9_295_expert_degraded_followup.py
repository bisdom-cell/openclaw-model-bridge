#!/usr/bin/env python3
"""test_v37_9_295_expert_degraded_followup.py — expert 真降级 followup 化守卫
（V37.9.295，对抗审计 C-F2 后半 / unfinished [34]）

血案背景: expert_escalate 走"直出不 followup"通道 (finish_reason=stop)，PA LLM
结构上看不到工具结果 → SOUL 规则 12 "api_unavailable → 我用基础模式自己回答"
只能靠用户手动重发兑现（V37.9.288 渲染文案"请把问题再发我一次"= 该缺口的诚实
版本）。V37.9.295 结构性兑现: api_unavailable/quota_exceeded 复用
_followup_llm_call 让基础模型同轮接管作答；二次失败走 V37.9.294 降级哨兵 →
用户收到 V37.9.288 渲染通知（行为等同旧版 = 降级的优雅降级）。

范围刻意收窄: read_only_violation (策略拒绝，接管作答=绕过契约) /
claude_pending (配置态) / parse_failed (expert 已回) 保持直出渲染。

tool_proxy 不可 import（顶层 serve_forever）→ AST 抽源 + exec-with-mocks
（V37.9.288/294 惯例）。🔴 行为变更需 Mac Mini E2E: 触发词 + expert 后端
不可用场景，观察 PA 同轮直接作答且开头带降级标注。
"""

import ast
import json
import os
import re
import sys
import textwrap
import types
import unittest

REPO = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO)

from proxy_filters import (  # noqa: E402
    EXPERT_DEGRADABLE_STATUSES,
    EXPERT_DEGRADED_FOLLOWUP_SYSTEM,
    is_expert_degradable_result,
    CUSTOM_TOOL_NAMES,
    ALLOWED_TOOLS,
    ALLOWED_PREFIXES,
    classify_tool_result_ok,
)

_TOOL_PROXY = os.path.join(REPO, "tool_proxy.py")


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _extract_method(method_name):
    """从 ProxyHandler 类体抽单个方法源码（V37.9.288/294 同款）"""
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


class TestDegradablePredicate(unittest.TestCase):
    """降级判定纯函数 + 范围钉死"""

    def test_api_unavailable_degradable(self):
        self.assertTrue(is_expert_degradable_result(
            '{"status": "api_unavailable", "error": "Volcengine timeout"}'))

    def test_quota_exceeded_degradable(self):
        self.assertTrue(is_expert_degradable_result('{"status": "quota_exceeded"}'))

    def test_read_only_violation_not_degradable(self):
        """策略拒绝态接管作答 = 绕过 read-only 契约，必须直出渲染"""
        self.assertFalse(is_expert_degradable_result('{"status": "read_only_violation"}'))

    def test_ok_and_config_states_not_degradable(self):
        for st in ("ok", "claude_pending", "parse_failed", "no_context", "dry_run"):
            self.assertFalse(is_expert_degradable_result(json.dumps({"status": st})),
                             f"status={st} 不该触发降级 followup")

    def test_malformed_inputs_fail_open(self):
        self.assertFalse(is_expert_degradable_result('{"status": "api_unavailable", broken'))
        self.assertFalse(is_expert_degradable_result("plain text"))
        self.assertFalse(is_expert_degradable_result(None))
        self.assertFalse(is_expert_degradable_result('[1, 2]'))

    def test_degradable_set_pinned(self):
        """集合演进须显式过守卫（新增 status 前先想清楚 SOUL 契约）"""
        self.assertEqual(EXPERT_DEGRADABLE_STATUSES, ("api_unavailable", "quota_exceeded"))

    def test_classify_consistency(self):
        """降级态同时必须被 V37.9.294 分类为工具失败（两口径一致）"""
        for st in EXPERT_DEGRADABLE_STATUSES:
            self.assertFalse(classify_tool_result_ok(json.dumps({"status": st})),
                             f"{st} 该记 tool 失败（降级是服务层面，工具本身失败了）")


class _FollowupHarness:
    """exec _followup_llm_call，捕获发出的请求体"""

    def __init__(self, fail=False):
        self.sent_bodies = []
        self.logs = []
        src = _extract_method("_followup_llm_call")
        harness = self

        class _Resp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return json.dumps({
                    "choices": [{"message": {"role": "assistant", "content": "基础模式回答内容"},
                                 "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 10, "total_tokens": 20},
                }).encode()

        def fake_urlopen(req, timeout=60):
            if fail:
                raise OSError("backend down")
            return _Resp()

        class _Req:
            def __init__(self, url, data=None, method="POST"):
                harness.sent_bodies.append(json.loads(data))

            def add_header(self, *a):
                pass

        ns = {
            "json": json,
            "log": lambda m: harness.logs.append(m),
            "Request": _Req,
            "_safe_urlopen": fake_urlopen,
            "BACKEND": "http://127.0.0.1:5001",
        }
        exec(compile(src, "<_followup_llm_call>", "exec"), ns)
        self.fn = ns["_followup_llm_call"]
        self.stub = types.SimpleNamespace()


class TestFollowupTemplateParam(unittest.TestCase):
    """(模板参数化) 默认 search_kb 语义不变 + expert 模板生效 + 二次失败语义"""

    BODY = {"model": "m", "messages": [{"role": "user", "content": "系统为什么慢"}]}

    def test_default_template_is_search_kb_wording(self):
        h = _FollowupHarness()
        h.fn(h.stub, dict(self.BODY), "KB 命中片段", "rid1")
        sys_msg = h.sent_bodies[0]["messages"][-1]
        self.assertEqual(sys_msg["role"], "system")
        self.assertIn("知识库搜索结果", sys_msg["content"])
        self.assertIn("KB 命中片段", sys_msg["content"])
        self.assertTrue(any("SEARCH_KB followup" in l for l in h.logs),
                        "默认 tag 必须保持 SEARCH_KB（日志语义不回归）")

    def test_expert_template_applied(self):
        h = _FollowupHarness()
        notice = "⚠️ Doubao 专家后端暂不可用（API/网络故障）。请把问题再发我一次，我用基础模式直接回答。"
        h.fn(h.stub, dict(self.BODY), notice, "rid2",
             system_template=EXPERT_DEGRADED_FOLLOWUP_SYSTEM, tag="EXPERT_DEGRADED")
        sys_msg = h.sent_bodies[0]["messages"][-1]
        self.assertIn("基础模式", sys_msg["content"])
        self.assertIn(notice, sys_msg["content"], "失败通知必须作为 {context} 注入")
        self.assertNotIn("知识库搜索结果", sys_msg["content"])
        self.assertTrue(any("EXPERT_DEGRADED followup" in l for l in h.logs))

    def test_followup_strips_tools(self):
        h = _FollowupHarness()
        body = dict(self.BODY)
        body["tools"] = [{"type": "function"}]
        h.fn(h.stub, body, "x", "rid3",
             system_template=EXPERT_DEGRADED_FOLLOWUP_SYSTEM, tag="EXPERT_DEGRADED")
        self.assertNotIn("tools", h.sent_bodies[0], "降级 followup 不得再带工具")

    def test_double_failure_keeps_v294_sentinel_and_notice(self):
        """二次失败 → 内容=渲染通知（V37.9.288 行为等同）+ V37.9.294 哨兵"""
        h = _FollowupHarness(fail=True)
        notice = "⚠️ Doubao 专家后端暂不可用（API/网络故障）。请把问题再发我一次，我用基础模式直接回答。"
        rj = h.fn(h.stub, dict(self.BODY), notice, "rid4",
                  system_template=EXPERT_DEGRADED_FOLLOWUP_SYSTEM, tag="EXPERT_DEGRADED")
        self.assertTrue(rj.get("_proxy_followup_degraded"))
        self.assertEqual(rj["choices"][0]["message"]["content"], notice)


class TestHandleCustomToolCallsBehavior(unittest.TestCase):
    """(wiring 行为级) expert 降级路由 vs 直出渲染"""

    def _run(self, tool_result, fn_name="expert_escalate", original_body=None):
        src = _extract_method("_handle_custom_tool_calls")
        calls = {"followup": [], "format": []}

        class StubSelf:
            def _execute_custom_tool(self, name, args):
                return tool_result

            def _format_tool_result(self, name, args, result):
                calls["format"].append(result)
                return f"[RENDERED]{json.loads(result).get('status', '?')}"

            def _followup_llm_call(self, body, context, rid, system_template=None, tag="SEARCH_KB"):
                calls["followup"].append(
                    {"context": context, "template": system_template, "tag": tag})
                return {"choices": [{"message": {"content": "接管回答"}}], "_marker": "followup"}

            def _extract_tool_calls_from_text(self, content):
                return []

        ns = {
            "json": json, "log": lambda *a, **k: None,
            "CUSTOM_TOOL_NAMES": CUSTOM_TOOL_NAMES,
            "ALLOWED_TOOLS": ALLOWED_TOOLS,
            "ALLOWED_PREFIXES": ALLOWED_PREFIXES,
            "classify_tool_result_ok": classify_tool_result_ok,
            "is_expert_degradable_result": is_expert_degradable_result,
            "EXPERT_DEGRADED_FOLLOWUP_SYSTEM": EXPERT_DEGRADED_FOLLOWUP_SYSTEM,
            "proxy_stats": types.SimpleNamespace(record_tool_call=lambda success: None),
            "uuid": __import__("uuid"),
        }
        exec(compile(src, "<_handle_custom_tool_calls>", "exec"), ns)
        rj = {"choices": [{"message": {"tool_calls": [
            {"function": {"name": fn_name, "arguments": "{}"}}]}}]}
        if original_body is None:
            original_body = {"model": "m", "messages": [{"role": "user", "content": "q"}]}
        out = ns["_handle_custom_tool_calls"](StubSelf(), rj, original_body, "rid")
        return out, calls

    def test_api_unavailable_routes_to_followup(self):
        out, calls = self._run('{"status": "api_unavailable", "error": "boom"}')
        self.assertEqual(len(calls["followup"]), 1, "api_unavailable 必须 followup 化真降级")
        fu = calls["followup"][0]
        self.assertIs(fu["template"], EXPERT_DEGRADED_FOLLOWUP_SYSTEM)
        self.assertEqual(fu["tag"], "EXPERT_DEGRADED")
        self.assertIn("[RENDERED]api_unavailable", fu["context"],
                      "followup 上下文必须是渲染后的失败通知（兼作二次失败兜底内容）")
        self.assertEqual(out.get("_marker"), "followup", "必须返回 followup 的响应")

    def test_quota_exceeded_routes_to_followup(self):
        out, calls = self._run('{"status": "quota_exceeded"}')
        self.assertEqual(len(calls["followup"]), 1)

    def test_read_only_violation_renders_directly(self):
        out, calls = self._run('{"status": "read_only_violation"}')
        self.assertEqual(len(calls["followup"]), 0, "策略拒绝态不得接管作答（绕过契约）")
        self.assertIn("[RENDERED]read_only_violation",
                      out["choices"][0]["message"]["content"])

    def test_ok_renders_directly(self):
        out, calls = self._run('{"status": "ok", "proposal": "x"}')
        self.assertEqual(len(calls["followup"]), 0)

    def test_no_original_body_falls_back_to_render(self):
        # 显式传空 dict 测 original_body 缺失场景（None 会被 helper 换成默认 body）
        out2, calls2 = self._run('{"status": "api_unavailable"}', original_body={})
        self.assertEqual(len(calls2["followup"]), 0, "无 original_body 必须退回直出渲染")
        self.assertIn("[RENDERED]api_unavailable",
                      out2["choices"][0]["message"]["content"])


class TestSourceGuards(unittest.TestCase):
    """标记 + 模板契约 + 默认语义 pin"""

    def test_markers(self):
        self.assertIn("V37.9.295", _read(_TOOL_PROXY))
        self.assertIn("V37.9.295", _read(os.path.join(REPO, "proxy_filters.py")))

    def test_template_contract(self):
        t = EXPERT_DEGRADED_FOLLOWUP_SYSTEM
        self.assertIn("{context}", t)
        self.assertIn("严禁编造专家意见", t, "反幻觉条款不可移除")
        self.assertIn("不要要求用户重发", t,
                      "防混淆条款不可移除（通知文案含「再发我一次」，照抄=降级没发生）")
        self.assertIn("基础模式", t)

    def test_default_kb_wording_unchanged(self):
        """search_kb 默认路径文案 byte-level pin（V37.9.295 参数化不得改默认语义）"""
        src = _extract_method("_followup_llm_call")
        self.assertIn("以下是从用户知识库中搜索到的结果。请基于这些结果回答用户的问题。", src)
        self.assertIn('tag="SEARCH_KB"', src, "默认 tag 必须是 SEARCH_KB")

    def test_wiring_source(self):
        src = _extract_method("_handle_custom_tool_calls")
        self.assertIn("is_expert_degradable_result(result)", src)
        self.assertIn("system_template=EXPERT_DEGRADED_FOLLOWUP_SYSTEM", src)
        self.assertIn('tag="EXPERT_DEGRADED"', src)
        m = re.search(r"if needs_llm_followup and original_body:.*?if expert_degraded_notice and original_body:",
                      src, re.S)
        self.assertIsNotNone(m, "search_kb followup 必须优先于 expert 降级分支（合并结果已含通知）")


if __name__ == "__main__":
    unittest.main(verbosity=1)
