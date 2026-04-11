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
    classify_complexity, filter_system_alerts, SYSTEM_ALERT_MARKER,
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

    def test_tool_count_never_exceeds_max(self):
        """V36.2: 工具数量硬上限 — CLAUDE.md 声明 <=12，filter_tools() 必须强制执行。"""
        from config_loader import MAX_TOOLS
        # 构造 20 个合法工具（超过上限）
        allowed = ["web_search", "web_fetch", "read", "write", "edit", "exec",
                    "memory_search", "memory_get", "sessions_spawn", "sessions_send",
                    "sessions_history", "agents_list", "cron", "message", "tts", "image"]
        tools = [{"function": {"name": n, "parameters": {}}} for n in allowed]
        filtered, _, kept = filter_tools(tools)
        self.assertLessEqual(len(filtered), MAX_TOOLS,
            f"filter_tools() produced {len(filtered)} tools, violates <= {MAX_TOOLS} constraint")
        # 自定义工具必须保留
        for ct in CUSTOM_TOOLS:
            ct_name = ct["function"]["name"]
            self.assertIn(ct_name, kept, f"Custom tool '{ct_name}' must survive truncation")

    def test_tool_count_small_input_untouched(self):
        """少量工具不应被截断。"""
        tools = [
            {"function": {"name": "exec", "parameters": {}}},
            {"function": {"name": "read", "parameters": {}}},
        ]
        filtered, _, _ = filter_tools(tools)
        self.assertEqual(len(filtered), 2 + NUM_CUSTOM_TOOLS)


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


class TestSessionToolSchemas(unittest.TestCase):
    """V35 PoC: sessions_spawn/send/history/agents_list schema injection tests."""

    def test_sessions_spawn_schema_injected(self):
        """sessions_spawn should get CLEAN_SCHEMA with agent + message params."""
        tools = [{"function": {"name": "sessions_spawn", "parameters": {"bloated": True}}}]
        filtered, _, kept = filter_tools(tools)
        self.assertIn("sessions_spawn", kept)
        schema = filtered[0]["function"]["parameters"]
        self.assertIn("agent", schema["properties"])
        self.assertIn("message", schema["properties"])
        self.assertEqual(schema["required"], ["agent", "message"])
        self.assertTrue(schema.get("additionalProperties") is False)

    def test_sessions_send_schema_injected(self):
        """sessions_send should get CLEAN_SCHEMA with sessionId + message params."""
        tools = [{"function": {"name": "sessions_send", "parameters": {"bloated": True}}}]
        filtered, _, kept = filter_tools(tools)
        self.assertIn("sessions_send", kept)
        schema = filtered[0]["function"]["parameters"]
        self.assertIn("sessionId", schema["properties"])
        self.assertIn("message", schema["properties"])
        self.assertEqual(schema["required"], ["sessionId", "message"])

    def test_sessions_history_schema_injected(self):
        """sessions_history should get CLEAN_SCHEMA with sessionId param."""
        tools = [{"function": {"name": "sessions_history", "parameters": {}}}]
        filtered, _, kept = filter_tools(tools)
        self.assertIn("sessions_history", kept)
        schema = filtered[0]["function"]["parameters"]
        self.assertIn("sessionId", schema["properties"])
        self.assertEqual(schema["required"], ["sessionId"])

    def test_agents_list_schema_injected(self):
        """agents_list should get CLEAN_SCHEMA with no required params."""
        tools = [{"function": {"name": "agents_list", "parameters": {"junk": True}}}]
        filtered, _, kept = filter_tools(tools)
        self.assertIn("agents_list", kept)
        schema = filtered[0]["function"]["parameters"]
        self.assertEqual(schema["properties"], {})
        self.assertTrue(schema.get("additionalProperties") is False)

    def test_sessions_tools_in_allowed(self):
        """All session tools must be in ALLOWED_TOOLS."""
        for name in ("sessions_spawn", "sessions_send", "sessions_history", "agents_list"):
            self.assertIn(name, ALLOWED_TOOLS)

    def test_sessions_tools_in_tool_params(self):
        """All session tools must have TOOL_PARAMS entries."""
        self.assertEqual(TOOL_PARAMS["sessions_spawn"], {"agent", "message"})
        self.assertEqual(TOOL_PARAMS["sessions_send"], {"sessionId", "message"})
        self.assertEqual(TOOL_PARAMS["sessions_history"], {"sessionId"})
        self.assertEqual(TOOL_PARAMS["agents_list"], set())

    def test_spawn_args_cleaned(self):
        """fix_tool_args should strip extra params from sessions_spawn."""
        resp = make_response("sessions_spawn", {
            "agent": "ops",
            "message": "check health",
            "bogus_param": "should be removed"
        })
        fix_tool_args(resp)
        args = get_args(resp)
        self.assertIn("agent", args)
        self.assertIn("message", args)
        self.assertNotIn("bogus_param", args)

    def test_send_args_cleaned(self):
        """fix_tool_args should strip extra params from sessions_send."""
        resp = make_response("sessions_send", {
            "sessionId": "abc-123",
            "message": "hello",
            "extra": "gone"
        })
        fix_tool_args(resp)
        args = get_args(resp)
        self.assertIn("sessionId", args)
        self.assertNotIn("extra", args)


class TestToolCallXMLStripping(unittest.TestCase):
    """V37.1+: 幻觉工具调用 <tool_call> XML 清理测试"""

    def _read_proxy(self):
        with open("tool_proxy.py") as f:
            return f.read()

    def test_xml_stripped_from_content(self):
        """<tool_call> XML 始终从 content 中清除"""
        content = self._read_proxy()
        self.assertIn("re.sub", content)
        self.assertIn("<tool_call>", content)
        self.assertIn("</tool_call>", content)

    def test_hallucinated_tool_logged(self):
        """幻觉工具名有日志记录"""
        content = self._read_proxy()
        self.assertIn("HALLUCINATED TOOL stripped", content)

    def test_unknown_tool_not_in_tool_calls(self):
        """不存在的工具不应出现在 tool_calls 中"""
        content = self._read_proxy()
        # 验证存在过滤逻辑：只保留 CUSTOM_TOOL_NAMES + ALLOWED_TOOLS + ALLOWED_PREFIXES
        self.assertIn("CUSTOM_TOOL_NAMES", content)
        self.assertIn("ALLOWED_TOOLS", content)
        self.assertIn("ALLOWED_PREFIXES", content)

    def test_empty_tool_calls_removed(self):
        """所有工具都是幻觉时 tool_calls 被移除"""
        content = self._read_proxy()
        self.assertIn('msg.pop("tool_calls", None)', content)

    def test_valid_custom_tool_preserved(self):
        """有效自定义工具（search_kb/data_clean）不被过滤"""
        for name in CUSTOM_TOOL_NAMES:
            self.assertIn(name, CUSTOM_TOOL_NAMES)

    def test_summarize_not_valid_tool(self):
        """'summarize' 不是任何已知工具"""
        self.assertNotIn("summarize", ALLOWED_TOOLS)
        self.assertNotIn("summarize", CUSTOM_TOOL_NAMES)
        self.assertFalse(any("summarize".startswith(p) for p in ALLOWED_PREFIXES))


class TestOntologyEquivalence(unittest.TestCase):
    """验证 ONTOLOGY_MODE=on 时引擎输出与硬编码完全等价。"""

    @classmethod
    def setUpClass(cls):
        """Load ontology engine for comparison."""
        try:
            import sys, os
            sys.path.insert(0, os.path.join(os.path.dirname(__file__), "ontology"))
            from engine import ToolOntology
            cls.onto = ToolOntology()
            cls.available = True
        except Exception:
            cls.onto = None
            cls.available = False

    def test_engine_available(self):
        """Ontology engine should be loadable."""
        self.assertTrue(self.available, "ontology/engine.py not loadable")

    def test_allowed_tools_equivalence(self):
        """Engine allowed_tools == hardcoded ALLOWED_TOOLS."""
        if not self.available:
            self.skipTest("engine not available")
        data = self.onto.generate_proxy_data()
        self.assertEqual(data["ALLOWED_TOOLS"], ALLOWED_TOOLS)

    def test_allowed_prefixes_equivalence(self):
        """Engine prefixes == hardcoded ALLOWED_PREFIXES."""
        if not self.available:
            self.skipTest("engine not available")
        data = self.onto.generate_proxy_data()
        self.assertEqual(data["ALLOWED_PREFIXES"], ALLOWED_PREFIXES)

    def test_tool_params_equivalence(self):
        """Engine TOOL_PARAMS keys and values == hardcoded."""
        if not self.available:
            self.skipTest("engine not available")
        data = self.onto.generate_proxy_data()
        self.assertEqual(set(data["TOOL_PARAMS"].keys()), set(TOOL_PARAMS.keys()))
        for name in TOOL_PARAMS:
            self.assertEqual(data["TOOL_PARAMS"][name], TOOL_PARAMS[name],
                             f"TOOL_PARAMS mismatch for {name}")

    def test_custom_tool_names_equivalence(self):
        """Engine custom tool names == hardcoded."""
        if not self.available:
            self.skipTest("engine not available")
        data = self.onto.generate_proxy_data()
        self.assertEqual(data["CUSTOM_TOOL_NAMES"], CUSTOM_TOOL_NAMES)

    def test_alias_resolution_read(self):
        """Engine resolve_alias for read matches hardcoded."""
        if not self.available:
            self.skipTest("engine not available")
        for alt in ["file_path", "file", "filepath", "filename"]:
            resolved, changed = self.onto.resolve_alias("read", {alt: "/tmp/x"})
            self.assertTrue(changed, f"read alias {alt} not resolved")
            self.assertIn("path", resolved)
            self.assertNotIn(alt, resolved)

    def test_alias_resolution_exec(self):
        """Engine resolve_alias for exec matches hardcoded."""
        if not self.available:
            self.skipTest("engine not available")
        for alt in ["cmd", "shell", "bash", "script"]:
            resolved, changed = self.onto.resolve_alias("exec", {alt: "ls"})
            self.assertTrue(changed, f"exec alias {alt} not resolved")
            self.assertIn("command", resolved)

    def test_alias_resolution_write(self):
        """Engine resolve_alias for write matches hardcoded."""
        if not self.available:
            self.skipTest("engine not available")
        for alt in ["text", "data", "body", "file_content"]:
            resolved, changed = self.onto.resolve_alias("write", {alt: "hi", "path": "/tmp/x"})
            self.assertTrue(changed, f"write alias {alt} not resolved")
            self.assertIn("content", resolved)

    def test_alias_resolution_web_search(self):
        """Engine resolve_alias for web_search matches hardcoded."""
        if not self.available:
            self.skipTest("engine not available")
        for alt in ["search_query", "q", "keyword", "search"]:
            resolved, changed = self.onto.resolve_alias("web_search", {alt: "test"})
            self.assertTrue(changed, f"web_search alias {alt} not resolved")
            self.assertIn("query", resolved)

    def test_canonical_params_no_change(self):
        """Canonical params should not trigger alias resolution."""
        if not self.available:
            self.skipTest("engine not available")
        _, changed = self.onto.resolve_alias("read", {"path": "/tmp/x"})
        self.assertFalse(changed)
        _, changed = self.onto.resolve_alias("exec", {"command": "ls"})
        self.assertFalse(changed)

    def test_fix_tool_args_on_mode_equivalence(self):
        """fix_tool_args output is identical under on vs hardcoded for all alias cases."""
        if not self.available:
            self.skipTest("engine not available")
        cases = [
            ("read", {"file_path": "/tmp/x"}),
            ("exec", {"cmd": "ls -la"}),
            ("write", {"path": "/tmp/f", "text": "hello"}),
            ("web_search", {"q": "python"}),
            ("read", {"path": "/tmp/x"}),  # canonical, no alias
        ]
        for name, args in cases:
            # Hardcoded path
            rj_hard = make_response(name, dict(args))
            fix_tool_args(rj_hard)
            hard_result = get_args(rj_hard)

            # Engine path
            resolved, _ = self.onto.resolve_alias(name, dict(args))
            allowed = TOOL_PARAMS.get(name, set())
            engine_result = {k: v for k, v in resolved.items() if k in allowed}

            self.assertEqual(hard_result, engine_result,
                             f"Mismatch for {name} {args}: "
                             f"hardcoded={hard_result} engine={engine_result}")


# ---------------------------------------------------------------------------
# V37.4.3: filter_system_alerts tests
# ---------------------------------------------------------------------------
# 案例：2026-04-11 13:06 PA 幻觉事件
#   12:30 job_watchdog 通过 notify.sh --topic alerts 推送 "🚨 系统监控告警"
#   Gateway 把这条消息写入 sessions.json 作为 assistant 历史消息
#   13:06 用户问哲学问题（本体×随机×贝叶斯），Qwen3 attention 把 recent
#   assistant 告警误判为"未完成任务"，生成 macOS FDA 跟进指令替换用户真实回答
# 修复：notify.sh / 告警直发路径加 [SYSTEM_ALERT] 前缀，
#       tool_proxy 构建 LLM 请求前 filter_system_alerts() 剥离带标记消息
# ---------------------------------------------------------------------------

class TestFilterSystemAlerts(unittest.TestCase):
    """验证 filter_system_alerts 正确移除告警消息，防止污染 PA 对话上下文。"""

    def test_marker_constant_exists(self):
        """SYSTEM_ALERT_MARKER 常量必须存在且可见"""
        self.assertEqual(SYSTEM_ALERT_MARKER, "[SYSTEM_ALERT]")

    def test_removes_marked_assistant(self):
        """带 [SYSTEM_ALERT] 前缀的 assistant 消息必须被移除"""
        messages = [
            {"role": "system", "content": "You are PA"},
            {"role": "user", "content": "上次对话"},
            {"role": "assistant", "content": "[SYSTEM_ALERT]\n🚨 系统监控告警\nbash cron_doctor.sh"},
            {"role": "user", "content": "本体×随机×贝叶斯的架构观点"},
        ]
        filtered, dropped = filter_system_alerts(messages)
        self.assertEqual(dropped, 1)
        self.assertEqual(len(filtered), 3)
        # 告警消息已被剥离，用户哲学问题完整保留
        contents = [m.get("content") for m in filtered]
        self.assertNotIn("[SYSTEM_ALERT]\n🚨 系统监控告警\nbash cron_doctor.sh", contents)
        self.assertIn("本体×随机×贝叶斯的架构观点", contents)

    def test_removes_marked_user(self):
        """即使 user role 消息以标记开头（不常见但可能）也要剥离"""
        messages = [
            {"role": "user", "content": "[SYSTEM_ALERT]\nwatchdog ping"},
            {"role": "user", "content": "正常问题"},
        ]
        filtered, dropped = filter_system_alerts(messages)
        self.assertEqual(dropped, 1)
        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0]["content"], "正常问题")

    def test_preserves_system_role(self):
        """system role 消息永远保留，即使内容恰好以标记开头也不剥离
        （SOUL.md 等宪法级 prompt 不受告警过滤影响）"""
        messages = [
            {"role": "system", "content": "[SYSTEM_ALERT] should still stay"},
            {"role": "user", "content": "hi"},
        ]
        filtered, dropped = filter_system_alerts(messages)
        self.assertEqual(dropped, 0)
        self.assertEqual(len(filtered), 2)

    def test_preserves_normal_messages(self):
        """正常对话不应被误伤"""
        messages = [
            {"role": "system", "content": "You are PA"},
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "你好，有什么可以帮你"},
            {"role": "user", "content": "请解释贝叶斯推理"},
            {"role": "assistant", "content": "贝叶斯推理是..."},
        ]
        filtered, dropped = filter_system_alerts(messages)
        self.assertEqual(dropped, 0)
        self.assertEqual(len(filtered), 5)

    def test_marker_only_at_start(self):
        """标记只在消息开头才触发剥离；正文中引用标记文本不应被误伤
        （否则用户讨论 [SYSTEM_ALERT] 这个 feature 本身就会被过滤）"""
        messages = [
            {"role": "user", "content": "今天的告警 [SYSTEM_ALERT] 是什么意思？"},
            {"role": "assistant", "content": "说明：[SYSTEM_ALERT] 前缀用于"},
        ]
        filtered, dropped = filter_system_alerts(messages)
        self.assertEqual(dropped, 0)
        self.assertEqual(len(filtered), 2)

    def test_marker_with_leading_whitespace(self):
        """消息开头有空白（空格/换行/tab）时，lstrip 后开头匹配仍要剥离"""
        messages = [
            {"role": "assistant", "content": "   \n[SYSTEM_ALERT]\nreal alert"},
            {"role": "assistant", "content": "\t\t[SYSTEM_ALERT] tab alert"},
        ]
        filtered, dropped = filter_system_alerts(messages)
        self.assertEqual(dropped, 2)
        self.assertEqual(len(filtered), 0)

    def test_content_blocks_openai_format(self):
        """OpenAI content blocks（list of {type,text}）格式的消息也要支持"""
        messages = [
            {"role": "assistant", "content": [
                {"type": "text", "text": "[SYSTEM_ALERT]\nalert in block"},
            ]},
            {"role": "user", "content": [
                {"type": "text", "text": "正常的多模态问题"},
            ]},
        ]
        filtered, dropped = filter_system_alerts(messages)
        self.assertEqual(dropped, 1)
        self.assertEqual(len(filtered), 1)

    def test_empty_messages(self):
        """空 messages 列表不 crash"""
        filtered, dropped = filter_system_alerts([])
        self.assertEqual(dropped, 0)
        self.assertEqual(filtered, [])

    def test_missing_content(self):
        """role 字段存在但 content 缺失时不 crash"""
        messages = [
            {"role": "assistant"},  # no content
            {"role": "user", "content": None},
        ]
        filtered, dropped = filter_system_alerts(messages)
        self.assertEqual(dropped, 0)
        self.assertEqual(len(filtered), 2)

    def test_log_fn_called_on_drop(self):
        """有 log_fn 时，丢弃消息触发日志调用"""
        logs = []
        messages = [
            {"role": "assistant", "content": "[SYSTEM_ALERT]\nfoo"},
            {"role": "user", "content": "bar"},
        ]
        _, dropped = filter_system_alerts(messages, log_fn=lambda m: logs.append(m))
        self.assertEqual(dropped, 1)
        self.assertEqual(len(logs), 1)
        self.assertIn("dropped 1", logs[0])

    def test_log_fn_not_called_when_clean(self):
        """无告警消息时不产生日志噪声"""
        logs = []
        messages = [{"role": "user", "content": "normal"}]
        _, _ = filter_system_alerts(messages, log_fn=lambda m: logs.append(m))
        self.assertEqual(logs, [])

    def test_integration_with_truncate(self):
        """端到端：filter_system_alerts + truncate_messages 顺序正确
        确保告警在截断之前被剥离，避免 'last-N 保留窗口' 把告警误带进 LLM 上下文"""
        messages = [{"role": "system", "content": "SOUL.md"}]
        # 制造 100 条用户消息 + 中间一条告警
        for i in range(100):
            messages.append({"role": "user", "content": f"msg {i}"})
            if i == 50:
                messages.append({
                    "role": "assistant",
                    "content": f"[SYSTEM_ALERT]\n告警于第 {i} 条消息后推送\nbash cron_doctor.sh"
                })
        # 最后是用户真实问题
        messages.append({"role": "user", "content": "本体×随机×贝叶斯哲学问题"})

        # 先过滤再截断
        filtered, dropped = filter_system_alerts(messages)
        self.assertEqual(dropped, 1)

        truncated, trunc_dropped = truncate_messages(filtered, max_bytes=100000)
        # 告警已不在，truncate 的"最近保留窗口"绝对不会携带它
        for m in truncated:
            content = m.get("content", "")
            if isinstance(content, str):
                self.assertFalse(
                    content.lstrip().startswith("[SYSTEM_ALERT]"),
                    "告警消息泄漏到截断后的上下文 — filter 顺序错误或未生效"
                )

    def test_multiple_alerts_all_dropped(self):
        """多条告警累积时全部剥离"""
        messages = [
            {"role": "assistant", "content": "[SYSTEM_ALERT]\nalert 1"},
            {"role": "user", "content": "ok"},
            {"role": "assistant", "content": "[SYSTEM_ALERT]\nalert 2"},
            {"role": "user", "content": "still ok"},
            {"role": "assistant", "content": "[SYSTEM_ALERT]\nalert 3"},
            {"role": "user", "content": "final"},
        ]
        filtered, dropped = filter_system_alerts(messages)
        self.assertEqual(dropped, 3)
        self.assertEqual(len(filtered), 3)
        for m in filtered:
            self.assertNotIn("[SYSTEM_ALERT]", m["content"])


class TestNotifyShAlertMarker(unittest.TestCase):
    """验证 notify.sh 和告警直发路径正确写入 [SYSTEM_ALERT] 前缀（声明层检查）"""

    def test_notify_sh_alerts_branch_has_marker(self):
        """notify.sh 的 --topic alerts 分支必须在消息前加 [SYSTEM_ALERT] 前缀"""
        with open("notify.sh") as f:
            src = f.read()
        # 查找 alerts 分支 + 标记写入
        self.assertIn('topic" = "alerts"', src,
                      "notify.sh 缺少 alerts topic 分支")
        self.assertIn("[SYSTEM_ALERT]", src,
                      "notify.sh alerts 分支缺少 [SYSTEM_ALERT] 前缀注入")

    def test_auto_deploy_quiet_alert_has_marker(self):
        """auto_deploy.sh 的 quiet_alert 函数必须加 [SYSTEM_ALERT] 前缀"""
        with open("auto_deploy.sh") as f:
            src = f.read()
        # quiet_alert 函数体内必须有标记注入
        import re
        m = re.search(r'quiet_alert\(\).*?^\}', src, re.DOTALL | re.MULTILINE)
        self.assertIsNotNone(m, "auto_deploy.sh 找不到 quiet_alert 函数")
        body = m.group(0)
        self.assertIn("[SYSTEM_ALERT]", body,
                      "auto_deploy.sh quiet_alert 缺少 [SYSTEM_ALERT] 前缀注入")

    def test_hn_err_path_has_marker(self):
        """run_hn_fixed.sh 的 ERR_MSG 告警路径必须加 [SYSTEM_ALERT] 前缀"""
        with open("run_hn_fixed.sh") as f:
            src = f.read()
        # LLM调用失败 的 ERR_MSG 块
        self.assertIn("[SYSTEM_ALERT]", src,
                      "run_hn_fixed.sh 告警路径缺少 [SYSTEM_ALERT] 前缀")
        self.assertIn("HN Watcher LLM调用失败", src)

    def test_discussions_err_paths_have_marker(self):
        """run_discussions.sh 的 2 处 ERR_MSG 都必须加 [SYSTEM_ALERT] 前缀"""
        with open("jobs/openclaw_official/run_discussions.sh") as f:
            src = f.read()
        count = src.count("[SYSTEM_ALERT]")
        self.assertGreaterEqual(count, 2,
                                f"run_discussions.sh 告警路径至少应有 2 处 [SYSTEM_ALERT]，实际 {count}")

    def test_release_err_path_has_marker(self):
        """run.sh (OpenClaw Releases) 的 ERR_MSG 必须加 [SYSTEM_ALERT] 前缀"""
        with open("jobs/openclaw_official/run.sh") as f:
            src = f.read()
        self.assertIn("[SYSTEM_ALERT]", src,
                      "run.sh (releases) 告警路径缺少 [SYSTEM_ALERT] 前缀")


class TestToolProxyImportsAlertFilter(unittest.TestCase):
    """验证 tool_proxy.py 正确导入并使用 filter_system_alerts"""

    def test_tool_proxy_imports_filter(self):
        """tool_proxy.py 必须从 proxy_filters 导入 filter_system_alerts"""
        with open("tool_proxy.py") as f:
            src = f.read()
        self.assertIn("filter_system_alerts", src,
                      "tool_proxy.py 未导入 filter_system_alerts")

    def test_tool_proxy_calls_filter_before_truncate(self):
        """tool_proxy.py 必须在 truncate_messages 之前调用 filter_system_alerts
        否则告警可能在截断的'最近保留窗口'里被带进 LLM 上下文"""
        with open("tool_proxy.py") as f:
            src = f.read()
        filter_pos = src.find("filter_system_alerts(msgs")
        truncate_pos = src.find("truncate_messages(msgs")
        self.assertGreater(filter_pos, 0, "tool_proxy.py 未调用 filter_system_alerts")
        self.assertGreater(truncate_pos, 0, "tool_proxy.py 未调用 truncate_messages")
        self.assertLess(filter_pos, truncate_pos,
                        "filter_system_alerts 必须在 truncate_messages 之前调用")


if __name__ == "__main__":
    unittest.main(verbosity=2)
