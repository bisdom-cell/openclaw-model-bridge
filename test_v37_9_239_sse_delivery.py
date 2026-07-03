#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V37.9.239 守卫 — 审计 finding B-delivery: streaming fallthrough 内容类型修复。

背景（V37.9.228 诚实登记的 delivery follow-up，2026-07-02 审计最后一个可修项）:
  tool_proxy do_POST 的 200-response parse 块抛异后 fall through 到通用回写——
  此前无条件发 `application/json` + raw JSON body。client 请求的是 stream:true
  （was_streaming=True）时，期待 text/event-stream 的 Gateway SSE 解析器收到
  JSON = **内容类型错配**（transport 层直接 choke，比流内错误更糟）。

修复策略（fallthrough 且 was_streaming）:
  1. 先重试 build_sse_response(rj)——崩溃点在 parse 之后（fix_tool_args /
     custom-tool / alerts）时 rj 是合法 completion → 投递**真实内容**为 SSE。
  2. rj 未绑定（json.loads 失败 → NameError）或 build 再崩（schema-drift 垃圾）
     → SSE **error frame**（`data: {"error":...}` + `data: [DONE]`），客户端 SSE
     传输层可解析，错误在流内 surface。
  3. 非 streaming 请求走原 application/json 路径不变。

tool_proxy 顶层 serve_forever 不可 import → extract-block + exec-with-fakes
（V37.9.132/175/228 同款模式）。
"""
import json
import os
import re
import unittest

_REPO = os.path.dirname(os.path.abspath(__file__))
_TP = os.path.join(_REPO, "tool_proxy.py")


def _read_tp():
    with open(_TP, encoding="utf-8") as f:
        return f.read()


def _extract_delivery_block():
    """抽取 fallthrough 回写块——用 V37.9.239 注释锚定唯一站点（send_response(resp.status)
    在文件里有 2 处，泛匹配会抓到更早的 followup 站点跨越数千行）。"""
    src = _read_tp()
    m = re.search(
        r"( +)self\.send_response\(resp\.status\)\n"
        r"(\1# V37\.9\.239.*?self\.wfile\.write\(resp_body\)\n)",
        src, re.S)
    assert m, "V37.9.239 fallthrough 回写块未找到（锚点=send_response 后紧跟 V37.9.239 注释）"
    indent = m.group(1)
    block = indent + "self.send_response(resp.status)\n" + m.group(2)
    # 去公共缩进让 exec 可跑
    lines = block.split("\n")
    return "\n".join(ln[len(indent):] if ln.startswith(indent) else ln for ln in lines)


class _FakeHandler:
    """捕获 send_response/header/body 的假 handler。"""

    def __init__(self):
        self.status = None
        self.headers = {}
        self.body = b""

        class _W:
            def __init__(w):
                w.buf = b""

            def write(w, b):
                w.buf += b
        self.wfile = _W()

    def send_response(self, code):
        self.status = code

    def send_header(self, k, v):
        self.headers[k] = v

    def end_headers(self):
        pass


def _run_block(was_streaming, path="/v1/chat/completions",
               rj_defined=False, rj_value=None, build_raises=False):
    """在受控命名空间执行抽取的回写块，返回 FakeHandler。"""
    import proxy_filters as pf
    h = _FakeHandler()

    class _Resp:
        status = 200

    def _build(rj_arg):
        if build_raises:
            raise ValueError("sse boom")
        return pf.build_sse_response(rj_arg)

    ns = {
        "self": h,
        "resp": _Resp(),
        "resp_body": b'{"raw": "backend body"}',
        "was_streaming": was_streaming,
        "json": json,
        "build_sse_response": _build,
    }
    # 关键: rj 未定义场景 = 不放进 ns（复现 json.loads 失败后 rj 未绑定 → NameError）
    if rj_defined:
        ns["rj"] = rj_value
    # path gate；块含 return（SSE 分支）→ 包进函数体 exec 再调用
    block = _extract_delivery_block().replace("self.path", repr(path))
    wrapped = "def _block():\n" + "\n".join(
        ("    " + ln) if ln.strip() else ln for ln in block.split("\n"))
    exec(compile(wrapped, "<delivery_block>", "exec"), ns)
    ns["_block"]()
    return h


class TestSseDeliveryBehavior(unittest.TestCase):
    """行为级：exec 真实回写块（literal 与源码同源），验证三条路径。"""

    def test_streaming_rj_unbound_gets_sse_error_frame(self):
        """json.loads 失败（rj 未绑定）+ stream:true → SSE error frame 非 application/json。"""
        h = _run_block(was_streaming=True, rj_defined=False)
        self.assertEqual(h.headers.get("Content-Type"), "text/event-stream",
                         "SSE client 收到了 %s（内容类型错配 = 修复前 bug）"
                         % h.headers.get("Content-Type"))
        out = h.wfile.buf.decode()
        self.assertIn('"proxy_sse_conversion_error"', out)
        self.assertIn("data: [DONE]", out)

    def test_streaming_valid_rj_delivers_real_completion(self):
        """崩溃点在 parse 之后（rj 合法）→ 重试 build 成功 → 投递真实内容为 SSE。"""
        rj = {"id": "x", "created": 1, "model": "m",
              "choices": [{"index": 0,
                           "message": {"role": "assistant", "content": "hello"},
                           "finish_reason": "stop"}]}
        h = _run_block(was_streaming=True, rj_defined=True, rj_value=rj)
        self.assertEqual(h.headers.get("Content-Type"), "text/event-stream")
        out = h.wfile.buf.decode()
        self.assertIn('"hello"', out, "合法 completion 应被投递而非 error frame")
        self.assertNotIn("proxy_sse_conversion_error", out)
        self.assertIn("data: [DONE]", out)

    def test_streaming_build_crashes_falls_to_error_frame(self):
        """rj 存在但 build 再崩（schema-drift 垃圾）→ error frame。"""
        h = _run_block(was_streaming=True, rj_defined=True,
                       rj_value={"choices": "garbage"}, build_raises=True)
        self.assertEqual(h.headers.get("Content-Type"), "text/event-stream")
        self.assertIn("proxy_sse_conversion_error", h.wfile.buf.decode())

    def test_non_streaming_unchanged_json_path(self):
        """非 streaming 请求 → 原 application/json 路径完全不变。"""
        h = _run_block(was_streaming=False, rj_defined=False)
        self.assertEqual(h.headers.get("Content-Type"), "application/json")
        self.assertEqual(h.wfile.buf, b'{"raw": "backend body"}')

    def test_non_chat_endpoint_unchanged(self):
        """非 chat 端点（如 /embeddings）即使 stream flag 也走 json 路径。"""
        h = _run_block(was_streaming=True, path="/v1/embeddings", rj_defined=False)
        self.assertEqual(h.headers.get("Content-Type"), "application/json")


class TestSseDeliverySourceGuards(unittest.TestCase):
    def setUp(self):
        self.src = _read_tp()

    def test_v239_marker(self):
        self.assertIn("V37.9.239", self.src)

    def test_streaming_gate_before_json_fallthrough(self):
        """was_streaming 分支必须在 application/json 回写之前。"""
        blk = _extract_delivery_block()
        i_gate = blk.find("if was_streaming and")
        i_json = blk.find('self.send_header("Content-Type", "application/json")')
        self.assertGreater(i_gate, -1, "streaming gate 缺失（修复被回退）")
        self.assertGreater(i_json, i_gate, "json 回写必须在 streaming gate 之后")

    def test_retry_then_error_frame_order(self):
        """先重试 build_sse_response（投递真内容优先），失败才 error frame。"""
        blk = _extract_delivery_block()
        i_retry = blk.find("_sse_out = build_sse_response(rj)")
        i_frame = blk.find("proxy_sse_conversion_error")
        self.assertGreater(i_retry, -1)
        self.assertGreater(i_frame, i_retry, "error frame 应是 build 重试失败后的 fallback")

    def test_sse_branch_returns(self):
        """SSE 分支必须 return（不得继续落到 json 回写 = 双重响应）。"""
        blk = _extract_delivery_block()
        seg = blk[blk.find("if was_streaming and"):blk.find('self.send_header("Content-Type", "application/json")')]
        self.assertIn("return", seg)


if __name__ == "__main__":
    unittest.main(verbosity=2)
