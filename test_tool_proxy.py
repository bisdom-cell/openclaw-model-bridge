#!/usr/bin/env python3
"""
Unit tests for proxy_filters.py (V27).
Run: python3 -m pytest test_tool_proxy.py -v
  or: python3 test_tool_proxy.py
"""
import json
import unittest

# V27: import directly from the filters module (no more inline copy)
from proxy_filters import (
    fix_tool_args, is_allowed, filter_tools,
    truncate_messages, build_sse_response, should_strip_tools,
    classify_complexity,
    TOOL_PARAMS, VALID_BROWSER_PROFILES,
    ALLOWED_TOOLS, ALLOWED_PREFIXES,
    CUSTOM_TOOLS, CUSTOM_TOOL_NAMES,
)

NUM_CUSTOM_TOOLS = len(CUSTOM_TOOLS)


def make_response(name, args_dict):
    """Helper: build a minimal choices response with one tool call."""
    return {
        "choices": [{
            "message": {
                "role": "assistant",
                "tool_calls": [{
                    "function": {
                        "name": name,
                        "arguments": json.dumps(args_dict)
                    }
                }]
            }
        }]
    }


def get_args(rj):
    """Helper: extract parsed args from first tool call."""
    return json.loads(
        rj["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"]
    )


# ---- fix_tool_args tests (preserved from V26) ----

class TestBrowserProfileFix(unittest.TestCase):

    def test_invalid_profile_replaced(self):
        rj = make_response("browser_navigate", {"url": "https://example.com", "profile": "default"})
        modified = fix_tool_args(rj)
        self.assertTrue(modified)
        self.assertEqual(get_args(rj)["profile"], "openclaw")

    def test_valid_profile_unchanged(self):
        rj = make_response("browser_navigate", {"url": "https://example.com", "profile": "chrome"})
        modified = fix_tool_args(rj)
        self.assertFalse(modified)
        self.assertEqual(get_args(rj)["profile"], "chrome")

    def test_missing_profile_injected(self):
        rj = make_response("browser_navigate", {"url": "https://example.com"})
        modified = fix_tool_args(rj)
        self.assertTrue(modified)
        self.assertEqual(get_args(rj)["profile"], "openclaw")

    def test_invalid_target_replaced(self):
        rj = make_response("browser_click", {"selector": "#btn", "target": "bad_profile"})
        modified = fix_tool_args(rj)
        self.assertTrue(modified)
        self.assertEqual(get_args(rj)["target"], "openclaw")

    def test_valid_target_unchanged(self):
        rj = make_response("browser_click", {"selector": "#btn", "target": "openclaw"})
        fix_tool_args(rj)
        self.assertEqual(get_args(rj)["target"], "openclaw")


class TestParamAliases(unittest.TestCase):

    def test_read_file_path_alias(self):
        rj = make_response("read", {"file_path": "/tmp/foo.txt"})
        fix_tool_args(rj)
        args = get_args(rj)
        self.assertIn("path", args)
        self.assertNotIn("file_path", args)

    def test_read_filepath_alias(self):
        rj = make_response("read", {"filepath": "/tmp/bar.txt"})
        fix_tool_args(rj)
        args = get_args(rj)
        self.assertIn("path", args)

    def test_exec_cmd_alias(self):
        rj = make_response("exec", {"cmd": "ls -la"})
        fix_tool_args(rj)
        args = get_args(rj)
        self.assertIn("command", args)
        self.assertNotIn("cmd", args)

    def test_write_text_alias(self):
        rj = make_response("write", {"path": "/tmp/out.txt", "text": "hello"})
        fix_tool_args(rj)
        args = get_args(rj)
        self.assertIn("content", args)
        self.assertNotIn("text", args)

    def test_web_search_q_alias(self):
        rj = make_response("web_search", {"q": "python async"})
        fix_tool_args(rj)
        args = get_args(rj)
        self.assertIn("query", args)
        self.assertNotIn("q", args)


class TestExtraParamsStripped(unittest.TestCase):

    def test_extra_params_removed(self):
        rj = make_response("web_search", {"query": "test", "extra_field": "noise", "another": 42})
        fix_tool_args(rj)
        args = get_args(rj)
        self.assertIn("query", args)
        self.assertNotIn("extra_field", args)
        self.assertNotIn("another", args)

    def test_allowed_params_preserved(self):
        rj = make_response("write", {"path": "/tmp/f.txt", "content": "data"})
        modified = fix_tool_args(rj)
        args = get_args(rj)
        self.assertFalse(modified)
        self.assertEqual(args["path"], "/tmp/f.txt")
        self.assertEqual(args["content"], "data")


class TestMalformedArgs(unittest.TestCase):

    def test_invalid_json_args_handled(self):
        rj = {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "tool_calls": [{
                        "function": {"name": "web_search", "arguments": "{not valid json"}
                    }]
                }
            }]
        }
        # Should not raise
        fix_tool_args(rj)

    def test_no_tool_calls_no_crash(self):
        rj = {"choices": [{"message": {"role": "assistant", "content": "Hello"}}]}
        modified = fix_tool_args(rj)
        self.assertFalse(modified)

    def test_empty_choices_no_crash(self):
        modified = fix_tool_args({"choices": []})
        self.assertFalse(modified)

    def test_missing_choices_key(self):
        modified = fix_tool_args({})
        self.assertFalse(modified)


# ---- V27 new tests: is_allowed, filter_tools, truncate_messages, build_sse ----

class TestIsAllowed(unittest.TestCase):

    def test_exact_match(self):
        self.assertTrue(is_allowed("web_search"))
        self.assertTrue(is_allowed("exec"))
        self.assertTrue(is_allowed("tts"))

    def test_prefix_match(self):
        self.assertTrue(is_allowed("browser_navigate"))
        self.assertTrue(is_allowed("browser_click"))

    def test_rejected(self):
        self.assertFalse(is_allowed("dangerous_tool"))
        self.assertFalse(is_allowed("shell"))
        self.assertFalse(is_allowed(""))


class TestFilterTools(unittest.TestCase):

    def test_filters_and_keeps(self):
        tools = [
            {"function": {"name": "web_search", "parameters": {"old": True}}},
            {"function": {"name": "dangerous_tool", "parameters": {}}},
            {"function": {"name": "browser_click", "parameters": {}}},
        ]
        filtered, all_names, kept = filter_tools(tools)
        self.assertEqual(len(filtered), 2 + NUM_CUSTOM_TOOLS)
        self.assertIn("web_search", kept)
        self.assertIn("browser_click", kept)
        self.assertNotIn("dangerous_tool", kept)
        self.assertEqual(len(all_names), 3)

    def test_schema_replaced(self):
        tools = [{"function": {"name": "exec", "parameters": {"bloated": True}}}]
        filtered, _, _ = filter_tools(tools)
        self.assertIn("command", filtered[0]["function"]["parameters"]["properties"])

    def test_empty_input(self):
        filtered, all_names, kept = filter_tools([])
        self.assertEqual(len(filtered), NUM_CUSTOM_TOOLS)  # 自定义工具仍注入


class TestTruncateMessages(unittest.TestCase):

    def test_no_truncation_needed(self):
        msgs = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hi"},
        ]
        result, dropped = truncate_messages(msgs, max_bytes=100000)
        self.assertEqual(dropped, 0)
        self.assertEqual(len(result), 2)

    def test_truncation_drops_old(self):
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "A" * 1000},
            {"role": "assistant", "content": "B" * 1000},
            {"role": "user", "content": "C" * 1000},
        ]
        result, dropped = truncate_messages(msgs, max_bytes=2000)
        self.assertGreater(dropped, 0)
        # System message always kept
        self.assertEqual(result[0]["role"], "system")

    def test_system_always_kept(self):
        msgs = [
            {"role": "system", "content": "important"},
            {"role": "user", "content": "X" * 500000},
        ]
        result, dropped = truncate_messages(msgs, max_bytes=1000)
        self.assertEqual(result[0]["role"], "system")

    def test_dynamic_trim_at_85pct(self):
        """V31: 当 last_prompt_tokens >= 85% of context limit 时，动态缩减 max_bytes 到 50KB。"""
        msgs = [
            {"role": "system", "content": "sys"},
        ] + [
            {"role": "user" if i % 2 == 0 else "assistant", "content": "X" * 5000}
            for i in range(40)  # ~200KB of messages
        ]
        # 不传 last_prompt_tokens → 使用默认 200KB，大部分保留
        _, dropped_default = truncate_messages(msgs, max_bytes=200000)
        # 传入 85% token 用量 → 动态缩减到 50KB，裁剪更多
        _, dropped_85pct = truncate_messages(msgs, max_bytes=200000,
                                             last_prompt_tokens=int(260000 * 0.86))
        self.assertGreater(dropped_85pct, dropped_default)

    def test_dynamic_trim_at_70pct(self):
        """V31: 70-85% 区间，缩减到 100KB。"""
        msgs = [
            {"role": "system", "content": "sys"},
        ] + [
            {"role": "user" if i % 2 == 0 else "assistant", "content": "X" * 5000}
            for i in range(40)
        ]
        _, dropped_default = truncate_messages(msgs, max_bytes=200000)
        _, dropped_70pct = truncate_messages(msgs, max_bytes=200000,
                                             last_prompt_tokens=int(260000 * 0.72))
        self.assertGreater(dropped_70pct, dropped_default)

    def test_dynamic_trim_below_70pct(self):
        """V31: < 70% 时不额外裁剪。"""
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "Hi"},
        ]
        _, dropped_normal = truncate_messages(msgs, max_bytes=200000)
        _, dropped_low = truncate_messages(msgs, max_bytes=200000,
                                           last_prompt_tokens=int(260000 * 0.5))
        self.assertEqual(dropped_normal, dropped_low)  # 同样为 0


class TestBuildSSE(unittest.TestCase):

    def test_basic_sse(self):
        rj = {
            "id": "abc",
            "created": 123,
            "model": "test",
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": "hello"},
                "finish_reason": "stop"
            }]
        }
        sse = build_sse_response(rj)
        text = sse.decode()
        self.assertIn("data:", text)
        self.assertIn('"content": "hello"', text)
        self.assertTrue(text.endswith("data: [DONE]\n\n"))

    def test_tool_calls_in_sse(self):
        rj = {
            "id": "abc", "created": 123, "model": "test",
            "choices": [{
                "index": 0,
                "message": {
                    "role": "assistant",
                    "tool_calls": [{"function": {"name": "exec", "arguments": "{}"}}]
                },
                "finish_reason": "tool_calls"
            }]
        }
        sse = build_sse_response(rj)
        text = sse.decode()
        self.assertIn("tool_calls", text)

    def test_empty_choices(self):
        sse = build_sse_response({"choices": []})
        self.assertEqual(sse, b"data: [DONE]\n\n")


class TestNullMessageHandling(unittest.TestCase):
    """P0 fix: null message in choices should not crash."""

    def test_fix_tool_args_null_message(self):
        rj = {"choices": [{"message": None}]}
        modified = fix_tool_args(rj)
        self.assertFalse(modified)

    def test_fix_tool_args_missing_message(self):
        rj = {"choices": [{"finish_reason": "stop"}]}
        modified = fix_tool_args(rj)
        self.assertFalse(modified)

    def test_build_sse_null_message(self):
        rj = {"id": "x", "created": 0, "model": "m",
              "choices": [{"index": 0, "message": None, "finish_reason": "stop"}]}
        sse = build_sse_response(rj)
        self.assertIn(b"data:", sse)
        self.assertIn(b"[DONE]", sse)

    def test_build_sse_missing_message(self):
        rj = {"id": "x", "created": 0, "model": "m",
              "choices": [{"index": 0, "finish_reason": "stop"}]}
        sse = build_sse_response(rj)
        self.assertIn(b"[DONE]", sse)


class TestBrowserParamStripping(unittest.TestCase):
    """P0 fix: browser_* tools should strip extra params."""

    def test_browser_navigate_extra_params_stripped(self):
        rj = make_response("browser_navigate",
                           {"url": "https://example.com", "profile": "openclaw",
                            "width": 1024, "headless": True})
        fix_tool_args(rj)
        args = get_args(rj)
        self.assertIn("url", args)
        self.assertIn("profile", args)
        self.assertNotIn("width", args)
        self.assertNotIn("headless", args)

    def test_browser_click_extra_params_stripped(self):
        rj = make_response("browser_click",
                           {"selector": "#btn", "profile": "openclaw", "delay": 100})
        fix_tool_args(rj)
        args = get_args(rj)
        self.assertIn("selector", args)
        self.assertNotIn("delay", args)

    def test_browser_snapshot_minimal(self):
        rj = make_response("browser_snapshot", {"profile": "openclaw", "format": "png"})
        fix_tool_args(rj)
        args = get_args(rj)
        self.assertIn("profile", args)
        self.assertNotIn("format", args)


class TestTruncateSystemMessage(unittest.TestCase):
    """P1 fix: oversized system message should be truncated."""

    def test_large_system_message_truncated(self):
        msgs = [
            {"role": "system", "content": "X" * 200000},
            {"role": "user", "content": "Hi"},
        ]
        result, dropped = truncate_messages(msgs, max_bytes=50000)
        sys_content = result[0]["content"]
        self.assertLess(len(sys_content), 200000)
        self.assertIn("[truncated]", sys_content)


class TestShouldStripTools(unittest.TestCase):
    """[NO_TOOLS] marker detection tests."""

    def test_marker_in_user_message(self):
        msgs = [{"role": "user", "content": "规则：5. [NO_TOOLS] 直接推理"}]
        self.assertTrue(should_strip_tools(msgs))

    def test_marker_in_system_message(self):
        msgs = [{"role": "system", "content": "[NO_TOOLS] pure inference"}]
        self.assertTrue(should_strip_tools(msgs))

    def test_no_marker(self):
        msgs = [{"role": "user", "content": "请帮我查一下天气"}]
        self.assertFalse(should_strip_tools(msgs))

    def test_empty_messages(self):
        self.assertFalse(should_strip_tools([]))

    def test_non_string_content_without_marker(self):
        msgs = [{"role": "user", "content": [{"type": "text", "text": "hello"}]}]
        self.assertFalse(should_strip_tools(msgs))

    def test_marker_in_content_blocks(self):
        """[NO_TOOLS] in array-format content (OpenAI content blocks)."""
        msgs = [{"role": "user", "content": [
            {"type": "text", "text": "规则：5. [NO_TOOLS] 直接推理"}
        ]}]
        self.assertTrue(should_strip_tools(msgs))

    def test_marker_in_mixed_content_blocks(self):
        """[NO_TOOLS] in one of multiple content blocks."""
        msgs = [{"role": "user", "content": [
            {"type": "text", "text": "请生成客户画像"},
            {"type": "text", "text": "[NO_TOOLS] 禁止搜索"}
        ]}]
        self.assertTrue(should_strip_tools(msgs))


# ---- Abnormal response handling tests (contract-style) ----

class TestAbnormalResponses(unittest.TestCase):
    """异常响应处理：模拟 provider 返回各种非正常响应。"""

    def test_fix_tool_args_empty_arguments_string(self):
        """工具调用 arguments 为空字符串"""
        rj = {"choices": [{"message": {"role": "assistant", "tool_calls": [
            {"function": {"name": "exec", "arguments": ""}}
        ]}}]}
        modified = fix_tool_args(rj)
        # 应不崩溃，空字符串解析为空 dict
        self.assertFalse(modified)

    def test_fix_tool_args_arguments_is_dict(self):
        """部分 provider 返回 arguments 为 dict 而非 JSON string"""
        rj = {"choices": [{"message": {"role": "assistant", "tool_calls": [
            {"function": {"name": "exec", "arguments": {"command": "ls"}}}
        ]}}]}
        modified = fix_tool_args(rj)
        self.assertFalse(modified)

    def test_fix_tool_args_nested_null_fields(self):
        """tool_calls 列表中包含残缺的 function 字段"""
        rj = {"choices": [{"message": {"role": "assistant", "tool_calls": [
            {"function": None},
            {"function": {"name": "exec", "arguments": '{"command": "pwd"}'}},
        ]}}]}
        # Should handle None function gracefully
        fix_tool_args(rj)

    def test_build_sse_with_usage(self):
        """SSE 转换应忽略 usage 字段（只输出 choices）"""
        rj = {
            "id": "x", "created": 0, "model": "m",
            "usage": {"prompt_tokens": 100, "total_tokens": 200},
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}]
        }
        sse = build_sse_response(rj)
        text = sse.decode()
        self.assertIn('"content": "ok"', text)
        self.assertIn("[DONE]", text)

    def test_build_sse_multiple_choices(self):
        """多个 choices（n>1 场景）"""
        rj = {
            "id": "x", "created": 0, "model": "m",
            "choices": [
                {"index": 0, "message": {"role": "assistant", "content": "a"}, "finish_reason": "stop"},
                {"index": 1, "message": {"role": "assistant", "content": "b"}, "finish_reason": "stop"},
            ]
        }
        sse = build_sse_response(rj)
        text = sse.decode()
        # 应有两个 data: 事件 + [DONE]
        data_lines = [l for l in text.split('\n') if l.startswith('data:') and '[DONE]' not in l]
        self.assertEqual(len(data_lines), 2)

    def test_build_sse_content_with_special_chars(self):
        """SSE 内容含换行、引号、unicode"""
        rj = {
            "id": "x", "created": 0, "model": "m",
            "choices": [{"index": 0,
                         "message": {"role": "assistant", "content": '你好\n"世界"\t🌍'},
                         "finish_reason": "stop"}]
        }
        sse = build_sse_response(rj)
        # 应该能正确 JSON 序列化
        text = sse.decode()
        self.assertIn("data:", text)
        self.assertIn("[DONE]", text)

    def test_truncate_tool_messages_preserved(self):
        """截断时 tool role 消息不应被拆散（orphan tool_call_id）"""
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "A" * 1000},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "tc1", "function": {"name": "exec", "arguments": "{}"}}]},
            {"role": "tool", "content": "result", "tool_call_id": "tc1"},
            {"role": "user", "content": "B" * 1000},
        ]
        result, dropped = truncate_messages(msgs, max_bytes=5000)
        # system 始终保留
        self.assertEqual(result[0]["role"], "system")

    def test_truncate_all_dropped_except_system(self):
        """极端截断：system 消息占满预算"""
        msgs = [
            {"role": "system", "content": "S" * 100000},
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": "Hello"},
        ]
        result, dropped = truncate_messages(msgs, max_bytes=60000)
        # system 被截断但保留，其他消息尽量保留
        self.assertEqual(result[0]["role"], "system")
        self.assertIn("[truncated]", result[0]["content"])

    def test_fix_tool_args_unknown_tool_passthrough(self):
        """未知工具名（不在 TOOL_PARAMS 中）不应被修改"""
        rj = make_response("custom_unknown_tool", {"foo": "bar", "baz": 42})
        modified = fix_tool_args(rj)
        args = get_args(rj)
        self.assertFalse(modified)
        self.assertEqual(args["foo"], "bar")
        self.assertEqual(args["baz"], 42)

    def test_filter_tools_malformed_entries(self):
        """工具列表中包含残缺条目"""
        tools = [
            {"function": {"name": "exec", "parameters": {}}},
            {"function": {}},  # 缺 name
            {},  # 缺 function
            {"function": {"name": "web_search", "parameters": {}}},
        ]
        filtered, all_names, kept = filter_tools(tools)
        self.assertEqual(len(filtered), 2 + NUM_CUSTOM_TOOLS)
        self.assertIn("exec", kept)
        self.assertIn("web_search", kept)


class TestProxyStats(unittest.TestCase):
    """ProxyStats 监控状态机测试。"""

    def test_consecutive_errors_alert(self):
        """连续错误达到阈值应产生告警"""
        from proxy_filters import ProxyStats, CONSECUTIVE_ERROR_ALERT
        stats = ProxyStats()
        for i in range(CONSECUTIVE_ERROR_ALERT):
            stats.record_error(502, "timeout")
        alerts = stats.pop_alerts()
        self.assertTrue(len(alerts) > 0)
        self.assertIn("连续", alerts[0])

    def test_success_resets_consecutive(self):
        """成功请求应重置连续错误计数"""
        from proxy_filters import ProxyStats
        stats = ProxyStats()
        stats.record_error(502, "timeout")
        stats.record_error(502, "timeout")
        stats.record_success({"prompt_tokens": 100, "total_tokens": 200})
        self.assertEqual(stats.consecutive_errors, 0)

    def test_token_critical_alert(self):
        """prompt_tokens 超过临界阈值应触发告警"""
        from proxy_filters import ProxyStats, TOKEN_CRITICAL_THRESHOLD
        stats = ProxyStats()
        stats.record_success({"prompt_tokens": TOKEN_CRITICAL_THRESHOLD + 1, "total_tokens": TOKEN_CRITICAL_THRESHOLD + 100})
        alerts = stats.pop_alerts()
        self.assertTrue(len(alerts) > 0)
        self.assertIn("临界", alerts[0])

    def test_no_alert_below_threshold(self):
        """正常 token 用量不应触发告警"""
        from proxy_filters import ProxyStats
        stats = ProxyStats()
        stats.record_success({"prompt_tokens": 1000, "total_tokens": 2000})
        alerts = stats.pop_alerts()
        self.assertEqual(len(alerts), 0)

    def test_pop_alerts_clears(self):
        """pop_alerts 后告警列表应清空"""
        from proxy_filters import ProxyStats
        stats = ProxyStats()
        stats.record_success({"prompt_tokens": 250000, "total_tokens": 260000})
        alerts1 = stats.pop_alerts()
        alerts2 = stats.pop_alerts()
        self.assertTrue(len(alerts1) > 0)
        self.assertEqual(len(alerts2), 0)


class TestClassifyComplexity(unittest.TestCase):
    """classify_complexity 智能路由测试"""

    def test_simple_short_query(self):
        """短问答应该被分类为 simple"""
        msgs = [
            {"role": "user", "content": "今天天气怎么样？"}
        ]
        self.assertEqual(classify_complexity(msgs, has_tools=False), "simple")

    def test_simple_few_turns(self):
        """少轮对话应该被分类为 simple"""
        msgs = [
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "你好！有什么可以帮你？"},
            {"role": "user", "content": "谢谢"},
        ]
        self.assertEqual(classify_complexity(msgs, has_tools=False), "simple")

    def test_complex_with_tools(self):
        """有工具时应该被分类为 complex"""
        msgs = [{"role": "user", "content": "hi"}]
        self.assertEqual(classify_complexity(msgs, has_tools=True), "complex")

    def test_complex_long_message(self):
        """长用户消息应该被分类为 complex"""
        msgs = [
            {"role": "user", "content": "请帮我分析以下代码的性能问题：" + "x" * 300}
        ]
        self.assertEqual(classify_complexity(msgs, has_tools=False), "complex")

    def test_complex_many_turns(self):
        """多轮对话应该被分类为 complex"""
        msgs = []
        for i in range(12):
            msgs.append({"role": "user", "content": f"问题{i}"})
            msgs.append({"role": "assistant", "content": f"回答{i}"})
        self.assertEqual(classify_complexity(msgs, has_tools=False), "complex")

    def test_complex_multimodal(self):
        """多模态消息应该被分类为 complex"""
        msgs = [
            {"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
                {"type": "text", "text": "这是什么？"}
            ]}
        ]
        self.assertEqual(classify_complexity(msgs, has_tools=False), "complex")

    def test_simple_no_tools_marker(self):
        """[NO_TOOLS] 标记应该被分类为 simple"""
        msgs = [
            {"role": "system", "content": "[NO_TOOLS] 你是一个助手"},
            {"role": "user", "content": "x" * 500},  # 长消息但有 NO_TOOLS
        ]
        self.assertEqual(classify_complexity(msgs, has_tools=False), "simple")

    def test_system_messages_excluded_from_count(self):
        """system 消息不应计入对话轮数"""
        msgs = [
            {"role": "system", "content": "你是助手"},
            {"role": "system", "content": "工具说明"},
            {"role": "user", "content": "你好"},
        ]
        self.assertEqual(classify_complexity(msgs, has_tools=False), "simple")

    def test_empty_messages(self):
        """空消息列表应该是 simple"""
        self.assertEqual(classify_complexity([], has_tools=False), "simple")


if __name__ == "__main__":
    unittest.main(verbosity=2)
