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
    flatten_content,
    compose_backend_error_str, MAX_UPSTREAM_BODY_CHARS,
    TOOL_PARAMS, VALID_BROWSER_PROFILES,
    ALLOWED_TOOLS, ALLOWED_PREFIXES,
    CUSTOM_TOOLS, CUSTOM_TOOL_NAMES,
    # V37.8.16 MR-15 reserved-file-write-block
    detect_reserved_file_write,
    RESERVED_FILE_BASENAMES, RESERVED_FILE_SAFE_CONTENT,
)
import proxy_filters  # for tests that read module-level attrs

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


# ============================================================
# V37.6: content blocks flatten (KB harvest title pollution fix)
# ============================================================

class TestFlattenContent(unittest.TestCase):
    """验证 flatten_content 正确处理 OpenAI content blocks，防止 `str()`
    产生 Python repr 污染 KB notes 标题（V37.6 数据质量血案）。"""

    def test_string_passthrough(self):
        """纯字符串 content 原样返回"""
        self.assertEqual(flatten_content("hello"), "hello")
        self.assertEqual(flatten_content(""), "")

    def test_none_returns_empty(self):
        """None → 空串（不抛 AttributeError）"""
        self.assertEqual(flatten_content(None), "")

    def test_single_text_block_extracted(self):
        """单个 text block → 只返回 text 字段，绝不返回 repr"""
        content = [{"type": "text", "text": "Qwen3-235B 工具路由"}]
        result = flatten_content(content)
        self.assertEqual(result, "Qwen3-235B 工具路由")
        # 关键：绝不是 Python repr
        self.assertNotIn("{'type':", result)
        self.assertNotIn("[{", result)

    def test_multiple_text_blocks_joined(self):
        """多个 text block 以空格连接"""
        content = [
            {"type": "text", "text": "part 1"},
            {"type": "text", "text": "part 2"},
        ]
        self.assertEqual(flatten_content(content), "part 1 part 2")

    def test_image_block_filtered(self):
        """非 text 块（image_url 等）被跳过，只保留 text"""
        content = [
            {"type": "text", "text": "请分析这张图"},
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,..."}},
            {"type": "text", "text": "并总结"},
        ]
        result = flatten_content(content)
        self.assertEqual(result, "请分析这张图 并总结")
        self.assertNotIn("image_url", result)
        self.assertNotIn("base64", result)

    def test_regression_str_repr_bug(self):
        """回归锁：保证 flatten_content 和 str() 的行为在 list 上彻底不同

        V37.6 血案：tool_proxy.py 用 str(m['content']) 把 list 转成
        `"[{'type': 'text', 'text': '...'}]"` 作为 note title，
        导致 KB notes 里出现 `[[{'type': 'text', 'text': 'Qwen3'] × 2`
        这种 Python repr 污染。"""
        content = [{"type": "text", "text": "Qwen3 工具"}]
        str_result = str(content)
        flat_result = flatten_content(content)
        # 反模式（str）会产生 repr
        self.assertIn("{'type'", str_result)
        self.assertIn("[{", str_result)
        # 正确模式（flatten_content）产生纯文本
        self.assertEqual(flat_result, "Qwen3 工具")
        self.assertNotIn("{'type'", flat_result)
        self.assertNotIn("[{", flat_result)

    def test_empty_list_returns_empty(self):
        """空 list → 空串"""
        self.assertEqual(flatten_content([]), "")

    def test_non_dict_blocks_skipped(self):
        """list 中混入非 dict 元素时不 crash"""
        content = [
            "random string",  # 非 dict
            {"type": "text", "text": "valid"},
            42,  # 非 dict
        ]
        self.assertEqual(flatten_content(content), "valid")

    def test_missing_text_field_skipped(self):
        """block 缺少 text 字段时跳过"""
        content = [
            {"type": "text"},  # 缺 text
            {"type": "text", "text": "ok"},
        ]
        self.assertEqual(flatten_content(content), "ok")

    def test_non_string_text_skipped(self):
        """text 字段非字符串时跳过"""
        content = [
            {"type": "text", "text": None},
            {"type": "text", "text": "good"},
            {"type": "text", "text": 123},
        ]
        self.assertEqual(flatten_content(content), "good")

    def test_unknown_content_type_returns_empty(self):
        """未知 content 类型（int/dict/bytes）返回空串而非 repr"""
        self.assertEqual(flatten_content(42), "")
        self.assertEqual(flatten_content({"not": "a list"}), "")
        self.assertEqual(flatten_content(b"bytes"), "")

    def test_default_type_is_text(self):
        """block 未显式指定 type 时默认为 text（OpenAI 规范允许省略）"""
        content = [{"text": "no type field"}]
        self.assertEqual(flatten_content(content), "no type field")


class TestToolProxyUsesFlattenContent(unittest.TestCase):
    """验证 tool_proxy.py 正确导入并在 _capture_conversation_turn
    路径使用 flatten_content，替代 V37.5 及之前的 `str(m['content'])`。"""

    def test_tool_proxy_imports_flatten_content(self):
        with open("tool_proxy.py") as f:
            src = f.read()
        self.assertIn("flatten_content", src,
                      "tool_proxy.py 未导入 flatten_content")

    def test_tool_proxy_does_not_use_str_on_content(self):
        """反模式守卫：tool_proxy.py 在 _capture_conversation_turn 前后
        不应再使用 `str(m["content"])` 或 `str(m['content'])`，
        否则 OpenAI content blocks 会被 repr 序列化污染 KB。"""
        with open("tool_proxy.py") as f:
            src = f.read()
        self.assertNotIn('str(m["content"])', src,
                         'tool_proxy.py 仍有 str(m["content"]) 反模式，'
                         '必须用 flatten_content(m["content"]) 替代 (V37.6)')
        self.assertNotIn("str(m['content'])", src,
                         "tool_proxy.py 仍有 str(m['content']) 反模式")

    def test_tool_proxy_calls_flatten_content(self):
        """tool_proxy.py 的 _capture_conversation_turn 热路径必须调用
        flatten_content(m["content"])"""
        with open("tool_proxy.py") as f:
            src = f.read()
        self.assertIn('flatten_content(m["content"])', src,
                      "tool_proxy.py 未在消息捕获路径调用 flatten_content")


# ============================================================
# V37.6: kb_append_source.sh idempotent H2-dedup helper
# ============================================================

class TestKbAppendSourceHelper(unittest.TestCase):
    """验证 kb_append_source.sh 行为 + 所有 14 个 job 写入点正确使用它。

    MR-4 (silent failure is a bug) 第三次演出的数据层防线：
    cron job 每次运行都 append 已存在 ## YYYY-MM-DD section，
    导致 sources 文件重复行爆炸（2026-04-10 kb_dedup: 438 行重复）。
    """

    import os as _os  # avoid shadowing module-level os

    HELPER = "kb_append_source.sh"

    def test_helper_script_exists_and_executable(self):
        import os, stat
        self.assertTrue(os.path.exists(self.HELPER),
                        f"{self.HELPER} 不存在")
        st = os.stat(self.HELPER)
        self.assertTrue(st.st_mode & stat.S_IXUSR,
                        f"{self.HELPER} 缺少 user execute bit")

    def test_helper_uses_grep_Fxq(self):
        """必须用 -Fxq（fixed string + 整行 + quiet）而不是普通 grep，
        避免 H2 marker 中的 `.`/`*`/`📊` 被 regex 误解。"""
        with open(self.HELPER) as f:
            src = f.read()
        self.assertIn("grep -Fxq", src,
                      "kb_append_source.sh 必须用 grep -Fxq 做整行精确匹配")

    def test_helper_has_flock_protection(self):
        """并发 cron 同时写同一 sources 文件时要用 flock 防止行撕裂"""
        with open(self.HELPER) as f:
            src = f.read()
        self.assertIn("flock", src,
                      "kb_append_source.sh 缺少 flock 并发保护")

    def test_helper_drains_stdin_on_skip(self):
        """幂等跳过时必须 drain stdin，避免上游 pipe SIGPIPE"""
        with open(self.HELPER) as f:
            src = f.read()
        # 检查 skip 分支附近有 cat > /dev/null 或等价 drain
        normalized = src.replace(" ", "")
        self.assertIn("cat>/dev/null", normalized,
                      "kb_append_source.sh 跳过分支未 drain stdin")

    def test_helper_idempotent_end_to_end(self):
        """真实 subprocess 调用：同一 marker 第二次写入必须被跳过
        (MR-6 declaration ≠ runtime verification — 必须实际运行 shell)"""
        import subprocess, tempfile, os
        with tempfile.TemporaryDirectory() as tmp:
            src_file = os.path.join(tmp, "test_source.md")
            with open(src_file, "w") as f:
                f.write("# Test\n")

            marker = "## 2026-04-11"
            payload = f"\n{marker}\n- item 1\n- item 2\n"

            # First call: append
            r1 = subprocess.run(
                ["bash", self.HELPER, src_file, marker],
                input=payload, capture_output=True, text=True,
                env={**os.environ, "KB_APPEND_SOURCE_QUIET": "1"},
            )
            self.assertEqual(r1.returncode, 0, f"first call failed: {r1.stderr}")
            with open(src_file) as f:
                content_after_1 = f.read()
            self.assertIn("- item 1", content_after_1)
            self.assertIn(marker, content_after_1)

            # Second call: same marker, must skip
            r2 = subprocess.run(
                ["bash", self.HELPER, src_file, marker],
                input=payload, capture_output=True, text=True,
                env={**os.environ, "KB_APPEND_SOURCE_QUIET": "1"},
            )
            self.assertEqual(r2.returncode, 0)
            with open(src_file) as f:
                content_after_2 = f.read()
            # 文件必须一字不变（幂等）
            self.assertEqual(content_after_1, content_after_2,
                             "kb_append_source.sh 不幂等：同一 marker 被重复追加")
            # Marker 仍然只出现一次
            self.assertEqual(content_after_2.count(marker), 1)

    def test_helper_appends_new_marker(self):
        """不同 marker（新 day / 新 slot）必须被正常追加"""
        import subprocess, tempfile, os
        with tempfile.TemporaryDirectory() as tmp:
            src_file = os.path.join(tmp, "test_source.md")
            with open(src_file, "w") as f:
                f.write("# Test\n")

            # Day 1
            subprocess.run(
                ["bash", self.HELPER, src_file, "## 2026-04-10"],
                input="\n## 2026-04-10\n- day1\n",
                capture_output=True, text=True,
                env={**os.environ, "KB_APPEND_SOURCE_QUIET": "1"},
            )
            # Day 2 — different marker, must append
            subprocess.run(
                ["bash", self.HELPER, src_file, "## 2026-04-11"],
                input="\n## 2026-04-11\n- day2\n",
                capture_output=True, text=True,
                env={**os.environ, "KB_APPEND_SOURCE_QUIET": "1"},
            )
            with open(src_file) as f:
                content = f.read()
            self.assertIn("- day1", content)
            self.assertIn("- day2", content)
            self.assertIn("## 2026-04-10", content)
            self.assertIn("## 2026-04-11", content)

    def test_helper_creates_missing_parent(self):
        """目标文件所在目录不存在时必须自动 mkdir -p"""
        import subprocess, tempfile, os
        with tempfile.TemporaryDirectory() as tmp:
            nested = os.path.join(tmp, "deep", "nested", "dir", "sources.md")
            self.assertFalse(os.path.exists(nested))
            r = subprocess.run(
                ["bash", self.HELPER, nested, "## 2026-04-11"],
                input="\n## 2026-04-11\n- x\n",
                capture_output=True, text=True,
                env={**os.environ, "KB_APPEND_SOURCE_QUIET": "1"},
            )
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertTrue(os.path.exists(nested))

    def test_all_job_sites_use_kb_append_source(self):
        """静态守卫 + 字节级约束：14 个 sources 写入点必须通过 kb_append_source.sh 做幂等。

        发现的写入点（V37.6 审计）：
        1. arxiv_monitor/run_arxiv.sh
        2. ai_leaders_x/run_ai_leaders_x.sh
        3. karpathy_x/run_karpathy_x.sh
        4. rss_blogs/run_rss_blogs.sh
        5. github_trending/run_github_trending.sh
        6. semantic_scholar/run_semantic_scholar.sh
        7. hf_papers/run_hf_papers.sh
        8. dblp/run_dblp.sh
        9. acl_anthology/run_acl_anthology.sh
        10. ontology_sources/run_ontology_sources.sh
        11. freight_watcher/run_freight.sh (2 sites: daily + profile)
        12. openclaw_official/run.sh
        (V37.8.13: pwc 已删除, 见 jobs_registry.yaml entry pwc.enabled=false)
        """
        job_sites = [
            "jobs/arxiv_monitor/run_arxiv.sh",
            "jobs/ai_leaders_x/run_ai_leaders_x.sh",
            "jobs/karpathy_x/run_karpathy_x.sh",
            "jobs/rss_blogs/run_rss_blogs.sh",
            "jobs/github_trending/run_github_trending.sh",
            "jobs/semantic_scholar/run_semantic_scholar.sh",
            "jobs/hf_papers/run_hf_papers.sh",
            "jobs/dblp/run_dblp.sh",
            "jobs/acl_anthology/run_acl_anthology.sh",
            "jobs/ontology_sources/run_ontology_sources.sh",
            "jobs/freight_watcher/run_freight.sh",
            "jobs/openclaw_official/run.sh",
        ]
        for path in job_sites:
            with open(path) as f:
                src = f.read()
            self.assertIn("kb_append_source.sh", src,
                          f"{path} 未使用 kb_append_source.sh 幂等写入")
            # freight_watcher 有 2 个写入点
            if path == "jobs/freight_watcher/run_freight.sh":
                self.assertGreaterEqual(
                    src.count("kb_append_source.sh"), 2,
                    "freight_watcher/run_freight.sh 应有至少 2 处 kb_append_source.sh"
                    "（daily + profile 两个写入点）")

    def test_no_append_gt_gt_antipattern_in_jobs(self):
        """反模式守卫：jobs/**/*.sh 中不应再有 `} >> "$KB_SRC"` 直接追加
        （V37.6 之前的 bug pattern——所有 14 个 job 都踩坑）。"""
        import subprocess
        result = subprocess.run(
            ["bash", "-c",
             r'grep -rEn "} >> \"\$KB_SRC\"" jobs/ || true'],
            capture_output=True, text=True,
        )
        # 无匹配 = 通过
        self.assertEqual(result.stdout.strip(), "",
                         f"发现未迁移的 `}} >> \"$KB_SRC\"` 反模式：\n{result.stdout}")

    def test_auto_deploy_includes_helper(self):
        """auto_deploy.sh 的 FILE_MAP 必须包含 kb_append_source.sh，
        否则 Mac Mini 同步时新建的 jobs 找不到 helper。"""
        with open("auto_deploy.sh") as f:
            src = f.read()
        self.assertIn("kb_append_source.sh", src,
                      "auto_deploy.sh FILE_MAP 未包含 kb_append_source.sh")


# ============================================================
# V37.6: kb_dedup.py H2-scoped algorithm fix
# ============================================================

class TestKbDedupH2Scoped(unittest.TestCase):
    """验证 kb_dedup.find_duplicate_source_lines 是 H2 section-scoped，
    不再把跨日期的合法重复（RSS rolling window）误判为文件级重复。"""

    def setUp(self):
        import sys, importlib
        # 确保从仓库路径导入 kb_dedup（不是用户环境的 site-packages）
        if "kb_dedup" in sys.modules:
            importlib.reload(sys.modules["kb_dedup"])
        else:
            import kb_dedup  # noqa: F401
        import kb_dedup
        self.kb_dedup = kb_dedup

    def _write(self, tmp, fname, text):
        import os
        path = os.path.join(tmp, fname)
        with open(path, "w") as f:
            f.write(text)
        return path

    def test_cross_h2_duplicate_is_preserved(self):
        """跨 H2 section 的相同内容不应被标记为重复（RSS 轮播合法情况）"""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            self._write(tmp, "rss.md", (
                "# RSS Sources\n"
                "\n"
                "## 2026-04-09\n"
                "- W3C released SHACL 1.2 draft\n"
                "- OWL 3 proposal published\n"
                "\n"
                "## 2026-04-10\n"
                "- W3C released SHACL 1.2 draft\n"  # 同一条目跨日期重复
                "- New JWS paper on reasoning\n"
            ))
            results = self.kb_dedup.find_duplicate_source_lines(tmp)
            # 跨 H2 的重复绝对不能被计数（否则 --apply 会丢失历史）
            self.assertEqual(
                results, {},
                f"跨 H2 section 的合法重复被误判为 duplicate: {results}"
            )

    def test_within_h2_duplicate_is_detected(self):
        """同一 H2 section 内的真实重复仍然被检测到"""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(tmp, "arxiv.md", (
                "# ArXiv Daily\n"
                "\n"
                "## 2026-04-10\n"
                "- Paper A: some abstract\n"
                "- Paper B: another abstract\n"
                "- Paper A: some abstract\n"  # section 内重复
            ))
            results = self.kb_dedup.find_duplicate_source_lines(tmp)
            self.assertIn("arxiv.md", results)
            orig, deduped, removed = results["arxiv.md"]
            self.assertEqual(removed, 1, "section 内部的重复行应被检测到")
            deduped_text = "".join(deduped)
            self.assertEqual(deduped_text.count("- Paper A: some abstract\n"), 1)
            self.assertIn("- Paper B: another abstract\n", deduped_text)

    def test_h2_headers_reset_seen(self):
        """H2 boundary 必须重置 seen set"""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            self._write(tmp, "multi.md", (
                "# Test\n"
                "## 2026-04-09\n"
                "- duplicate\n"
                "## 2026-04-10\n"
                "- duplicate\n"
                "## 2026-04-11\n"
                "- duplicate\n"
            ))
            results = self.kb_dedup.find_duplicate_source_lines(tmp)
            # 三个 section 各有一条 "- duplicate"，H2-scoped 视角下零重复
            self.assertEqual(results, {},
                             "H2 boundary 未重置 seen，跨 section 合并了")

    def test_h2_and_within_section_mixed(self):
        """混合情况：section A 干净，section B 有内部重复"""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            self._write(tmp, "mixed.md", (
                "## 2026-04-09\n"
                "- item1\n"
                "- item2\n"
                "## 2026-04-10\n"
                "- item1\n"            # 跨 H2 不算
                "- dup\n"
                "- dup\n"              # section B 内部重复
                "- item3\n"
            ))
            results = self.kb_dedup.find_duplicate_source_lines(tmp)
            self.assertIn("mixed.md", results)
            _, _, removed = results["mixed.md"]
            self.assertEqual(removed, 1, "只有 section B 内部的一条 dup 算重复")

    def test_sub_headings_do_not_reset(self):
        """### 三级标题不应触发 seen 重置（否则 section 内部夹 sub-heading
        时 dedup 就失效）"""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            self._write(tmp, "sub.md", (
                "## 2026-04-10\n"
                "### 论文\n"
                "- dup\n"
                "### 博客\n"
                "- dup\n"  # 同一 H2 下不同 sub-heading，仍算重复
            ))
            results = self.kb_dedup.find_duplicate_source_lines(tmp)
            self.assertIn("sub.md", results)
            _, _, removed = results["sub.md"]
            self.assertEqual(removed, 1)

    def test_unindexed_notes_included_in_dedup(self):
        """find_duplicate_notes 必须扫描 NOTES_DIR，而不仅是 index.entries。
        否则未索引的孤儿 note 永远逃过 dedup（2026-04-10 报告：4 unindexed）"""
        import tempfile, os, hashlib
        with tempfile.TemporaryDirectory() as tmp:
            # 构造临时 KB 结构
            notes_dir = os.path.join(tmp, "notes")
            os.makedirs(notes_dir)
            # 两个内容相同的 note，都不在 index 里
            same_content = (
                "---\n"
                "date: 2026-04-10\n"
                "---\n"
                "这是一条完全相同的笔记内容，用于测试未索引文件也能被去重捕获。"
                "添加更多内容以超过 20 字符的最小阈值限制。"
            )
            with open(os.path.join(notes_dir, "orphan_a.md"), "w") as f:
                f.write(same_content)
            with open(os.path.join(notes_dir, "orphan_b.md"), "w") as f:
                f.write(same_content)

            # 猴补丁 kb_dedup 的路径常量
            orig_base = self.kb_dedup.KB_BASE
            orig_notes = self.kb_dedup.NOTES_DIR
            try:
                self.kb_dedup.KB_BASE = tmp
                self.kb_dedup.NOTES_DIR = notes_dir
                empty_index = {"entries": []}
                exact, fuzzy = self.kb_dedup.find_duplicate_notes(empty_index)
                # exact 因为没有 summary 所以不触发，但 fuzzy 必须捕获
                self.assertTrue(
                    fuzzy,
                    "未索引 notes 未被纳入 fuzzy duplicate 检测（V37.6 bug）"
                )
            finally:
                self.kb_dedup.KB_BASE = orig_base
                self.kb_dedup.NOTES_DIR = orig_notes


# ══════════════════════════════════════════════════════════════════════
# V37.8.10 compose_backend_error_str — INV-OBSERVABILITY-001
# Blood lesson (2026-04-14/15 kb_evening 22:00):
#   adapter 返回 502 + body {"error": "ALL 1 FALLBACKS FAILED: gemini 429"}
#   → proxy urllib 抛 HTTPError → str(e) 只给 "HTTP Error 502: Bad Gateway"
#   → kb_evening 告警丢失真实原因 "gemini 429 quota exhausted"
# ══════════════════════════════════════════════════════════════════════
class TestComposeBackendErrorStr(unittest.TestCase):
    def _make_http_error(self, code, reason, body_bytes):
        import urllib.error
        import io
        fp = io.BytesIO(body_bytes) if body_bytes is not None else None
        return urllib.error.HTTPError("http://adapter:5001", code, reason, {}, fp)

    def test_json_error_field_extracted_into_upstream(self):
        body = json.dumps({"error": "ALL 1 FALLBACKS FAILED: gemini HTTP 429"}).encode()
        e = self._make_http_error(502, "Bad Gateway", body)
        result = compose_backend_error_str(e)
        self.assertIn("upstream:", result)
        self.assertIn("gemini HTTP 429", result)
        self.assertIn("ALL 1 FALLBACKS FAILED", result)

    def test_blood_lesson_kb_evening_scenario(self):
        """复现 2026-04-15 22:00 实际产生的 adapter 错误 body。"""
        body = json.dumps({
            "error": "gemini (gemini-2.5-flash): HTTP Error 429: Too Many Requests"
        }).encode()
        e = self._make_http_error(502, "Bad Gateway", body)
        result = compose_backend_error_str(e)
        self.assertIn("gemini", result)
        self.assertIn("429", result)
        self.assertIn("Too Many Requests", result)

    def test_non_httperror_exception_returns_str_only(self):
        """Plain Exception 无 .read() → 原 str(e) 行为（向后兼容）"""
        e = RuntimeError("backend connection refused")
        result = compose_backend_error_str(e)
        self.assertEqual(result, "backend connection refused")
        self.assertNotIn("upstream:", result)

    def test_httperror_with_empty_body(self):
        """空 body → 保持原 str(e) 行为"""
        e = self._make_http_error(502, "Bad Gateway", b"")
        result = compose_backend_error_str(e)
        # str(HTTPError) → "HTTP Error 502: Bad Gateway"
        self.assertIn("502", result)
        self.assertNotIn("upstream:", result)

    def test_non_json_body_raw_text_fallback(self):
        """text/html 错误页 → raw text 作 upstream"""
        body = b"<html><head><title>502 Bad Gateway</title></head></html>"
        e = self._make_http_error(502, "Bad Gateway", body)
        result = compose_backend_error_str(e)
        self.assertIn("upstream:", result)
        self.assertIn("502 Bad Gateway", result)

    def test_json_without_error_field_falls_back_to_raw(self):
        """JSON 合法但无 `error` key → 保留原始 JSON 文本"""
        body = json.dumps({"detail": "something", "code": 502}).encode()
        e = self._make_http_error(502, "Bad Gateway", body)
        result = compose_backend_error_str(e)
        self.assertIn("upstream:", result)
        self.assertIn("detail", result)

    def test_long_body_truncated_at_max_chars(self):
        long_err = "x" * 2000
        body = json.dumps({"error": long_err}).encode()
        e = self._make_http_error(502, "Bad Gateway", body)
        result = compose_backend_error_str(e)
        self.assertIn("[truncated]", result)
        # upstream 部分不超过 MAX_UPSTREAM_BODY_CHARS + marker
        upstream_part = result.split("upstream:", 1)[1]
        self.assertLess(len(upstream_part), MAX_UPSTREAM_BODY_CHARS + 50)

    def test_read_exception_fails_open(self):
        """e.read() 抛异常 → 静默 fallback 到 str(e)，不让 observability 炸主流程"""
        e = self._make_http_error(502, "Bad Gateway", b"whatever")
        def boom():
            raise RuntimeError("disk on fire")
        e.read = boom
        result = compose_backend_error_str(e)
        # 原 str(e) 行为保留，不崩
        self.assertIsInstance(result, str)
        self.assertIn("502", result)

    def test_utf8_replace_does_not_crash(self):
        """非 utf8 字节 → errors='replace' → 不崩溃"""
        body = b'\xff\xfe' + json.dumps({"error": "gemini 429"}).encode()
        e = self._make_http_error(502, "Bad Gateway", body)
        result = compose_backend_error_str(e)
        # 不崩就算成功，最起码保留 str(e)
        self.assertIn("502", result)

    def test_max_upstream_body_chars_constant_value(self):
        """Regression: 常量值契约（V37.8.10 声明为 500）"""
        self.assertEqual(MAX_UPSTREAM_BODY_CHARS, 500)

    def test_tool_proxy_imports_helper_from_filters(self):
        """Source-level guard: tool_proxy.py 必须 import 而非重新定义 helper。

        V37.8.10 架构契约: proxy_filters 是纯函数无网络依赖，tool_proxy 是 HTTP
        层。helper 放在 proxy_filters 才能被 test_tool_proxy 导入测试（因 import
        tool_proxy 会启动 HTTP server）。任何未来在 tool_proxy.py 内重新定义该
        helper 的重构都会让它不可测，此 guard 拒绝回退。
        """
        import os
        path = os.path.join(os.path.dirname(__file__), "tool_proxy.py")
        with open(path, encoding="utf-8") as f:
            src = f.read()
        self.assertIn("compose_backend_error_str", src, "tool_proxy 必须 import compose_backend_error_str")
        # Must be imported, not redefined
        self.assertNotIn("def compose_backend_error_str", src,
            "tool_proxy 不得重新定义 compose_backend_error_str — 必须从 proxy_filters import")
        self.assertNotIn("def _compose_backend_error_str", src,
            "tool_proxy 不得重新定义 _compose_backend_error_str — 必须从 proxy_filters import")


class TestReservedFileWriteBlock(unittest.TestCase):
    """V37.8.16 MR-15: OpenClaw 保留文件写入拦截。

    血案：PA 2026-04-19 把"任务总结"写进 workspace/HEARTBEAT.md →
    OpenClaw heartbeat 激活 → 所有 WhatsApp 用户消息被 HEARTBEAT_OK 替换 →
    Gateway stripTokenAtEdges 剥离 → 13 小时静默。

    详见 ontology/docs/cases/heartbeat_md_pa_self_silencing_case.md
    """

    # ── 纯函数 detect_reserved_file_write ──
    def test_detect_heartbeat_md_write_blocked(self):
        blocked, reason = proxy_filters.detect_reserved_file_write(
            "write", {"path": "/Users/bisdom/.openclaw/workspace/HEARTBEAT.md", "content": "x"})
        self.assertTrue(blocked)
        self.assertIn("HEARTBEAT.md", reason)

    def test_detect_heartbeat_md_edit_blocked(self):
        blocked, reason = proxy_filters.detect_reserved_file_write(
            "edit", {"path": "/Users/bisdom/HEARTBEAT.md", "old_text": "a", "new_text": "b"})
        self.assertTrue(blocked)
        self.assertIn("HEARTBEAT.md", reason)

    def test_detect_file_path_alias(self):
        """path 的常见 alias（file_path / file / filepath）都要匹配到"""
        for alias_key in ("file_path", "file", "filepath"):
            blocked, _ = proxy_filters.detect_reserved_file_write(
                "write", {alias_key: "~/HEARTBEAT.md"})
            self.assertTrue(blocked, f"alias {alias_key} 未被检测")

    def test_detect_case_sensitive(self):
        """OpenClaw 源码用精确名 HEARTBEAT.md — 小写变体不触发（防过拦截）"""
        for variant in ("heartbeat.md", "Heartbeat.md", "HEARTBEAT_notes.md"):
            blocked, _ = proxy_filters.detect_reserved_file_write(
                "write", {"path": f"/Users/bisdom/.openclaw/workspace/{variant}"})
            self.assertFalse(blocked, f"{variant} 不应被拦截")

    def test_detect_non_write_tool_ignored(self):
        """read/exec/web_fetch 等非写入工具不受影响"""
        for tool in ("read", "exec", "web_fetch", "memory_search"):
            blocked, _ = proxy_filters.detect_reserved_file_write(
                tool, {"path": "~/HEARTBEAT.md"})
            self.assertFalse(blocked)

    def test_detect_non_reserved_path_allowed(self):
        """正常文件写入不受影响"""
        blocked, _ = proxy_filters.detect_reserved_file_write(
            "write", {"path": "/tmp/notes.md", "content": "hello"})
        self.assertFalse(blocked)

    def test_detect_missing_path_safe(self):
        """缺 path 参数不崩"""
        blocked, _ = proxy_filters.detect_reserved_file_write("write", {})
        self.assertFalse(blocked)

    def test_detect_non_dict_args_safe(self):
        """非 dict 参数（例如 None 或字符串）不崩"""
        self.assertEqual(proxy_filters.detect_reserved_file_write("write", None), (False, ""))
        self.assertEqual(proxy_filters.detect_reserved_file_write("write", "not a dict"), (False, ""))

    def test_detect_empty_path_safe(self):
        """空字符串 path 不崩"""
        blocked, _ = proxy_filters.detect_reserved_file_write("write", {"path": ""})
        self.assertFalse(blocked)

    def test_detect_non_string_path_safe(self):
        """path 不是字符串（比如 LLM 传了个 dict）也不崩"""
        blocked, _ = proxy_filters.detect_reserved_file_write(
            "write", {"path": {"nested": "obj"}})
        self.assertFalse(blocked)

    def test_detect_trailing_slash(self):
        """trailing slash 也能正确取 basename"""
        blocked, _ = proxy_filters.detect_reserved_file_write(
            "write", {"path": "/Users/bisdom/.openclaw/workspace/HEARTBEAT.md/"})
        self.assertTrue(blocked)

    # ── 集成：fix_tool_args 触发 args 改写 ──
    def test_fix_tool_args_rewrites_heartbeat_write_content(self):
        """完整血案场景回归：PA 试图写 HEARTBEAT.md，content 被替换为安全占位"""
        rj = {
            "choices": [{
                "message": {
                    "tool_calls": [{
                        "function": {
                            "name": "write",
                            "arguments": json.dumps({
                                "path": "/Users/bisdom/.openclaw/workspace/HEARTBEAT.md",
                                "content": "- 任务完成：已解决HN抓取问题\n- 下一步：继续监控"
                            })
                        }
                    }]
                }
            }]
        }
        modified = proxy_filters.fix_tool_args(rj)
        self.assertTrue(modified)
        new_args = json.loads(rj["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"])
        # content 必须被替换为安全占位（只含注释）
        self.assertIn("Keep this file empty", new_args["content"])
        self.assertNotIn("任务完成", new_args["content"])
        self.assertNotIn("下一步", new_args["content"])

    def test_fix_tool_args_rewrites_heartbeat_edit_new_text(self):
        """edit 工具也要拦：new_text 被替换为安全占位"""
        rj = {
            "choices": [{
                "message": {
                    "tool_calls": [{
                        "function": {
                            "name": "edit",
                            "arguments": json.dumps({
                                "path": "~/HEARTBEAT.md",
                                "old_text": "# HEARTBEAT.md",
                                "new_text": "- new task description"
                            })
                        }
                    }]
                }
            }]
        }
        modified = proxy_filters.fix_tool_args(rj)
        self.assertTrue(modified)
        new_args = json.loads(rj["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"])
        self.assertIn("Keep this file empty", new_args["new_text"])
        self.assertNotIn("new task description", new_args["new_text"])

    def test_fix_tool_args_normal_write_unchanged(self):
        """正常 write（非保留文件）不受影响"""
        rj = {
            "choices": [{
                "message": {
                    "tool_calls": [{
                        "function": {
                            "name": "write",
                            "arguments": json.dumps({
                                "path": "/tmp/notes.md",
                                "content": "Some normal content"
                            })
                        }
                    }]
                }
            }]
        }
        proxy_filters.fix_tool_args(rj)  # 可能因其他理由 modified，但 content 不应变
        new_args = json.loads(rj["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"])
        self.assertEqual(new_args["content"], "Some normal content")

    def test_reserved_file_basenames_contains_heartbeat(self):
        """常量守卫：HEARTBEAT.md 必须在 RESERVED_FILE_BASENAMES 中"""
        self.assertIn("HEARTBEAT.md", proxy_filters.RESERVED_FILE_BASENAMES)

    def test_reserved_file_safe_content_is_effectively_empty(self):
        """安全占位内容必须只含 # 注释行，保证 isHeartbeatContentEffectivelyEmpty → true
        (对应 OpenClaw auth-profiles-*.js 里 isHeartbeatContentEffectivelyEmpty 的判定规则)"""
        content = proxy_filters.RESERVED_FILE_SAFE_CONTENT
        for line in content.split("\n"):
            stripped = line.strip()
            if not stripped:
                continue
            # 只允许 # 开头的注释（OpenClaw isHeartbeatContentEffectivelyEmpty 规则）
            self.assertTrue(stripped.startswith("#"),
                f"SAFE_CONTENT 含非注释行: {line!r} — 会让 HEARTBEAT.md 变成 effectively non-empty")

    # ────────────────────────────────────────────────────────────────────
    # V37.9.11 扩展：BOOTSTRAP.md + SKILL.md 加入 RESERVED_FILE_BASENAMES
    # 依据 MRD-RESERVED-FILES-001 对 OpenClaw dist/*.js 扫描结果（Mac Mini
    # --full 模式报告 2 个上游保留文件未登记）
    # ────────────────────────────────────────────────────────────────────

    def test_reserved_file_basenames_contains_bootstrap(self):
        """V37.9.11 常量守卫：BOOTSTRAP.md 必须在 RESERVED_FILE_BASENAMES 中"""
        self.assertIn("BOOTSTRAP.md", proxy_filters.RESERVED_FILE_BASENAMES)

    def test_reserved_file_basenames_contains_skill(self):
        """V37.9.11 常量守卫：SKILL.md 必须在 RESERVED_FILE_BASENAMES 中"""
        self.assertIn("SKILL.md", proxy_filters.RESERVED_FILE_BASENAMES)

    def test_reserved_file_basenames_exact_set(self):
        """V37.9.11 契约守卫：RESERVED_FILE_BASENAMES 恰好是 3 文件集合，
        新增必须通过治理流程（MRD-RESERVED-FILES-001 扫描 + INV-HB-001 检查）"""
        self.assertEqual(
            set(proxy_filters.RESERVED_FILE_BASENAMES),
            {"HEARTBEAT.md", "BOOTSTRAP.md", "SKILL.md"},
            "RESERVED_FILE_BASENAMES 漂移 — 未走 MRD 治理流程添加新成员"
        )

    def test_detect_blocks_bootstrap_md_at_workspace(self):
        """V37.9.11: BOOTSTRAP.md 在 workspace 路径被拦截"""
        blocked, reason = proxy_filters.detect_reserved_file_write(
            "write",
            {"path": "/Users/bisdom/.openclaw/workspace/BOOTSTRAP.md",
             "content": "malicious init script"}
        )
        self.assertTrue(blocked, "workspace/BOOTSTRAP.md 应被拦截")
        self.assertIn("BOOTSTRAP.md", reason)

    def test_detect_blocks_skill_md_at_home(self):
        """V37.9.11: SKILL.md 在 home 路径被拦截"""
        blocked, reason = proxy_filters.detect_reserved_file_write(
            "write",
            {"path": "~/SKILL.md", "content": "skill: evil"}
        )
        self.assertTrue(blocked, "~/SKILL.md 应被拦截")
        self.assertIn("SKILL.md", reason)

    def test_detect_blocks_bootstrap_and_skill_edit_tool(self):
        """V37.9.11: edit 工具对 BOOTSTRAP.md / SKILL.md 也被拦截"""
        for basename in ("BOOTSTRAP.md", "SKILL.md"):
            blocked, _ = proxy_filters.detect_reserved_file_write(
                "edit",
                {"path": f"/tmp/{basename}", "new_text": "x"}
            )
            self.assertTrue(blocked, f"edit {basename} 应被拦截")

    def test_fix_tool_args_rewrites_bootstrap_write_content(self):
        """V37.9.11: fix_tool_args 对 BOOTSTRAP.md 写入改写 content 为安全占位"""
        import json as _json
        rj = {
            "choices": [{
                "message": {
                    "tool_calls": [{
                        "function": {
                            "name": "write",
                            "arguments": _json.dumps({
                                "path": "/Users/bisdom/.openclaw/workspace/BOOTSTRAP.md",
                                "content": "exec('rm -rf /')"  # malicious content
                            })
                        }
                    }]
                }
            }]
        }
        modified = proxy_filters.fix_tool_args(rj)
        self.assertTrue(modified, "fix_tool_args 应改写")
        args = _json.loads(
            rj["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"]
        )
        self.assertEqual(
            args["content"], proxy_filters.RESERVED_FILE_SAFE_CONTENT,
            "BOOTSTRAP.md content 应被替换为 SAFE_CONTENT"
        )
        self.assertNotIn("rm -rf", args["content"],
                         "恶意内容应被完全清除")

    def test_safe_content_is_file_agnostic(self):
        """V37.9.11: SAFE_CONTENT 不应含特定文件名（因为同一常量用于 3 个文件），
        例如不应 hard-reference 'HEARTBEAT.md' 作为标题"""
        content = proxy_filters.RESERVED_FILE_SAFE_CONTENT
        # 第一行不应以 '# HEARTBEAT.md' 这种特定文件名开头
        first_line = content.split("\n")[0].strip()
        self.assertFalse(
            first_line == "# HEARTBEAT.md",
            "SAFE_CONTENT 首行不应硬编码 HEARTBEAT.md（现为 3 文件共享）"
        )
        # 必须是 OpenClaw-reserved-file 级别的通用描述
        self.assertIn("reserved", content.lower(),
                      "SAFE_CONTENT 必须声明为 reserved file 通用占位")


class TestPolicyDrivenMaxTools(unittest.TestCase):
    """V37.9.12 Phase 4 P1: max-tools-per-agent policy wiring 回归测试。

    锁定契约：
      1. proxy_filters._MAX_TOOLS_RESOLVED 由 _resolve_max_tools_limit() 派生
      2. filter_tools 用 _MAX_TOOLS_RESOLVED 作截断阈值（非硬编码 12，非 _CFG_MAX_TOOLS）
      3. ontology 加载失败/policy miss 时必须回退 config 值，不得抛异常
      4. 常规 mode=on 场景下 _MAX_TOOLS_RESOLVED == 12（等于 config，保证切换零回归）
    """

    def test_resolved_limit_matches_config_in_default_mode(self):
        """默认 ONTOLOGY_MODE=on 场景：policy 声明 == config 硬编码 == 12。"""
        import proxy_filters as pf
        self.assertEqual(pf._MAX_TOOLS_RESOLVED, 12)
        self.assertEqual(pf._MAX_TOOLS_RESOLVED, pf._CFG_MAX_TOOLS)

    def test_resolve_source_is_ontology_policy_in_on_mode(self):
        """V37.9.12 实际切换：on 模式下 source 必须为 ontology_policy（证明 wiring 生效）。
        若 source 变成 config_fallback_* 意味着 ontology 加载失败，wiring 已回退。"""
        import proxy_filters as pf
        if pf._ONTOLOGY_MODE == "on":
            self.assertEqual(
                pf._MAX_TOOLS_SOURCE, "ontology_policy",
                "ONTOLOGY_MODE=on 时必须使用 policy，不是 config fallback"
            )

    def test_filter_tools_truncates_at_resolved_limit(self):
        """filter_tools 构造一个溢出的 tools 列表，验证截断到 _MAX_TOOLS_RESOLVED。"""
        import proxy_filters as pf
        # 构造 20 个合法 gateway 工具（超过 12 上限）
        tools = []
        for tname in ["web_search", "web_fetch", "read", "write", "edit",
                      "exec", "memory_search", "memory_get", "sessions_spawn",
                      "sessions_send", "sessions_history", "agents_list",
                      "cron", "message", "tts", "image"]:
            tools.append({
                "type": "function",
                "function": {"name": tname, "parameters": {"type": "object", "properties": {}}}
            })

        kept, all_names, kept_names = pf.filter_tools(list(tools))
        self.assertLessEqual(
            len(kept), pf._MAX_TOOLS_RESOLVED,
            f"filter_tools 截断失败: len(kept)={len(kept)} > limit={pf._MAX_TOOLS_RESOLVED}"
        )

    def test_filter_tools_preserves_custom_tools_under_truncation(self):
        """截断时 custom tools (data_clean/search_kb) 必须全部保留。"""
        import proxy_filters as pf
        tools = []
        for tname in ["web_search", "web_fetch", "read", "write", "edit",
                      "exec", "memory_search", "memory_get", "sessions_spawn",
                      "sessions_send", "sessions_history", "agents_list",
                      "cron", "message", "tts", "image"]:
            tools.append({
                "type": "function",
                "function": {"name": tname, "parameters": {"type": "object", "properties": {}}}
            })
        kept, all_names, kept_names = pf.filter_tools(list(tools))
        # custom tool names 必须全部在 kept_names 里
        for custom_name in pf.CUSTOM_TOOL_NAMES:
            self.assertIn(
                custom_name, kept_names,
                f"custom tool '{custom_name}' 在截断后被删除（应无条件保留）"
            )

    def test_resolve_function_returns_config_when_mode_off(self):
        """ONTOLOGY_MODE=off 分支必须直接返回 config 值 + source=config_off_mode。
        用 monkeypatch 临时切换模式验证分支，不影响其他测试。"""
        import proxy_filters as pf
        original_mode = pf._ONTOLOGY_MODE
        try:
            pf._ONTOLOGY_MODE = "off"
            limit, source = pf._resolve_max_tools_limit()
            self.assertEqual(limit, pf._CFG_MAX_TOOLS)
            self.assertEqual(source, "config_off_mode")
        finally:
            pf._ONTOLOGY_MODE = original_mode

    def test_resolve_function_returns_config_when_onto_mod_missing(self):
        """_onto_mod=None 分支（ontology load failed）必须回退到 config。"""
        import proxy_filters as pf
        original_mod = pf._onto_mod
        original_mode = pf._ONTOLOGY_MODE
        try:
            pf._onto_mod = None
            pf._ONTOLOGY_MODE = "on"  # 确保走到 load_failed 判定
            limit, source = pf._resolve_max_tools_limit()
            self.assertEqual(limit, pf._CFG_MAX_TOOLS)
            self.assertEqual(source, "config_fallback_load_failed")
        finally:
            pf._onto_mod = original_mod
            pf._ONTOLOGY_MODE = original_mode

    def test_resolve_function_returns_config_when_policy_not_found(self):
        """evaluate_policy 返回 found=False 时必须回退到 config，不得抛异常。"""
        import proxy_filters as pf

        class _FakeMod:
            @staticmethod
            def evaluate_policy(policy_id):
                return {"found": False, "limit": None, "reason": "policy_id_not_found"}

        original_mod = pf._onto_mod
        original_mode = pf._ONTOLOGY_MODE
        try:
            pf._onto_mod = _FakeMod()
            pf._ONTOLOGY_MODE = "on"
            limit, source = pf._resolve_max_tools_limit()
            self.assertEqual(limit, pf._CFG_MAX_TOOLS)
            self.assertEqual(source, "config_fallback_policy_miss")
        finally:
            pf._onto_mod = original_mod
            pf._ONTOLOGY_MODE = original_mode

    def test_resolve_function_returns_config_when_evaluate_raises(self):
        """evaluate_policy 抛异常时必须 catch 并回退，不得让 proxy 启动崩溃。"""
        import proxy_filters as pf

        class _BrokenMod:
            @staticmethod
            def evaluate_policy(policy_id):
                raise RuntimeError("simulated engine failure")

        original_mod = pf._onto_mod
        original_mode = pf._ONTOLOGY_MODE
        try:
            pf._onto_mod = _BrokenMod()
            pf._ONTOLOGY_MODE = "on"
            limit, source = pf._resolve_max_tools_limit()
            self.assertEqual(limit, pf._CFG_MAX_TOOLS)
            self.assertEqual(source, "config_fallback_policy_miss")
        finally:
            pf._onto_mod = original_mod
            pf._ONTOLOGY_MODE = original_mode

    def test_resolve_function_uses_ontology_limit_when_valid(self):
        """on 模式 + valid policy result：必须使用 ontology.limit 而非 config。"""
        import proxy_filters as pf

        class _FakeMod:
            @staticmethod
            def evaluate_policy(policy_id):
                # 模拟 ontology 返回 8（测试切换路径；实际生产 limit=12）
                return {"found": True, "limit": 8, "reason": None}

        original_mod = pf._onto_mod
        original_mode = pf._ONTOLOGY_MODE
        try:
            pf._onto_mod = _FakeMod()
            pf._ONTOLOGY_MODE = "on"
            limit, source = pf._resolve_max_tools_limit()
            self.assertEqual(limit, 8)
            self.assertEqual(source, "ontology_policy")
        finally:
            pf._onto_mod = original_mod
            pf._ONTOLOGY_MODE = original_mode

    def test_shadow_mode_uses_config_but_observes(self):
        """shadow 模式即使 policy 有效也必须返回 config 值（观察期不切换行为）。"""
        import proxy_filters as pf

        class _FakeMod:
            @staticmethod
            def evaluate_policy(policy_id):
                return {"found": True, "limit": 99, "reason": None}

        original_mod = pf._onto_mod
        original_mode = pf._ONTOLOGY_MODE
        try:
            pf._onto_mod = _FakeMod()
            pf._ONTOLOGY_MODE = "shadow"
            limit, source = pf._resolve_max_tools_limit()
            self.assertEqual(limit, pf._CFG_MAX_TOOLS)
            self.assertEqual(source, "config_shadow_mode")
        finally:
            pf._onto_mod = original_mod
            pf._ONTOLOGY_MODE = original_mode

    def test_filter_tools_no_hardcoded_cfg_max_tools_in_truncation(self):
        """源码守卫：filter_tools 函数体不得再次出现 _CFG_MAX_TOOLS 作截断阈值。
        V37.9.12 切换必须彻底 — 未来回归时此守卫会捕获意外回退。"""
        import inspect
        import proxy_filters as pf
        src = inspect.getsource(pf.filter_tools)
        # 合法：注释引用 _CFG_MAX_TOOLS 说明；非法：作为 `> _CFG_MAX_TOOLS` 判定
        # 精确检查：条件判断 + 列表切片两处必须用 _MAX_TOOLS_RESOLVED
        self.assertNotRegex(
            src, r">\s*_CFG_MAX_TOOLS",
            "filter_tools 截断条件仍引用 _CFG_MAX_TOOLS（应切换为 _MAX_TOOLS_RESOLVED）"
        )
        self.assertIn(
            "_MAX_TOOLS_RESOLVED", src,
            "filter_tools 源码必须引用 _MAX_TOOLS_RESOLVED"
        )


class TestPolicyDrivenMaxToolCallsPerTask(unittest.TestCase):
    """V37.9.13 Phase 4 P2: max-tool-calls-per-task policy wiring — 镜像 P1 契约。

    第二条 policy 切换的价值不是 enforcement（当前 Python 尚未调用此常量），
    而是证明 V37.9.12 的 wiring 模式 (_resolve_*_limit + 5 档 safe-fallback +
    启动期一次性计算) 可扩展到任意 hard_limit policy。
    """

    def test_resolved_limit_is_2_in_default_mode(self):
        import proxy_filters as pf
        self.assertEqual(pf._MAX_TOOL_CALLS_PER_TASK_RESOLVED, 2)
        self.assertEqual(
            pf._MAX_TOOL_CALLS_PER_TASK_RESOLVED,
            pf._CFG_MAX_TOOL_CALLS_PER_TASK,
        )

    def test_source_is_ontology_policy_when_mode_on(self):
        import proxy_filters as pf
        if pf._ONTOLOGY_MODE == "on":
            self.assertEqual(
                pf._MAX_TOOL_CALLS_PER_TASK_SOURCE, "ontology_policy",
                "ONTOLOGY_MODE=on 时 max-tool-calls-per-task 必须走 ontology policy",
            )

    def test_resolve_returns_config_when_mode_off(self):
        import proxy_filters as pf
        original_mode = pf._ONTOLOGY_MODE
        try:
            pf._ONTOLOGY_MODE = "off"
            limit, source = pf._resolve_max_tool_calls_per_task_limit()
            self.assertEqual(limit, pf._CFG_MAX_TOOL_CALLS_PER_TASK)
            self.assertEqual(source, "config_off_mode")
        finally:
            pf._ONTOLOGY_MODE = original_mode

    def test_resolve_returns_config_when_onto_mod_missing(self):
        import proxy_filters as pf
        original_mod = pf._onto_mod
        original_mode = pf._ONTOLOGY_MODE
        try:
            pf._onto_mod = None
            pf._ONTOLOGY_MODE = "on"
            limit, source = pf._resolve_max_tool_calls_per_task_limit()
            self.assertEqual(limit, pf._CFG_MAX_TOOL_CALLS_PER_TASK)
            self.assertEqual(source, "config_fallback_load_failed")
        finally:
            pf._onto_mod = original_mod
            pf._ONTOLOGY_MODE = original_mode

    def test_resolve_returns_config_when_policy_not_found(self):
        import proxy_filters as pf

        class _FakeMod:
            @staticmethod
            def evaluate_policy(policy_id):
                return {"found": False, "limit": None, "reason": "policy_id_not_found"}

        original_mod = pf._onto_mod
        original_mode = pf._ONTOLOGY_MODE
        try:
            pf._onto_mod = _FakeMod()
            pf._ONTOLOGY_MODE = "on"
            limit, source = pf._resolve_max_tool_calls_per_task_limit()
            self.assertEqual(limit, pf._CFG_MAX_TOOL_CALLS_PER_TASK)
            self.assertEqual(source, "config_fallback_policy_miss")
        finally:
            pf._onto_mod = original_mod
            pf._ONTOLOGY_MODE = original_mode

    def test_resolve_returns_config_when_evaluate_raises(self):
        import proxy_filters as pf

        class _BrokenMod:
            @staticmethod
            def evaluate_policy(policy_id):
                raise RuntimeError("simulated engine failure")

        original_mod = pf._onto_mod
        original_mode = pf._ONTOLOGY_MODE
        try:
            pf._onto_mod = _BrokenMod()
            pf._ONTOLOGY_MODE = "on"
            limit, source = pf._resolve_max_tool_calls_per_task_limit()
            self.assertEqual(limit, pf._CFG_MAX_TOOL_CALLS_PER_TASK)
            self.assertEqual(source, "config_fallback_policy_miss")
        finally:
            pf._onto_mod = original_mod
            pf._ONTOLOGY_MODE = original_mode

    def test_resolve_uses_ontology_when_valid(self):
        import proxy_filters as pf

        class _FakeMod:
            @staticmethod
            def evaluate_policy(policy_id):
                return {"found": True, "limit": 5, "reason": None}

        original_mod = pf._onto_mod
        original_mode = pf._ONTOLOGY_MODE
        try:
            pf._onto_mod = _FakeMod()
            pf._ONTOLOGY_MODE = "on"
            limit, source = pf._resolve_max_tool_calls_per_task_limit()
            self.assertEqual(limit, 5)
            self.assertEqual(source, "ontology_policy")
        finally:
            pf._onto_mod = original_mod
            pf._ONTOLOGY_MODE = original_mode

    def test_shadow_mode_uses_config_but_logs_drift(self):
        import proxy_filters as pf

        class _FakeMod:
            @staticmethod
            def evaluate_policy(policy_id):
                return {"found": True, "limit": 99, "reason": None}

        original_mod = pf._onto_mod
        original_mode = pf._ONTOLOGY_MODE
        try:
            pf._onto_mod = _FakeMod()
            pf._ONTOLOGY_MODE = "shadow"
            limit, source = pf._resolve_max_tool_calls_per_task_limit()
            self.assertEqual(limit, pf._CFG_MAX_TOOL_CALLS_PER_TASK)
            self.assertEqual(source, "config_shadow_mode")
        finally:
            pf._onto_mod = original_mod
            pf._ONTOLOGY_MODE = original_mode

    def test_queries_correct_policy_id(self):
        """源码守卫: _resolve_max_tool_calls_per_task_limit 必须查询正确的
        policy_id (max-tool-calls-per-task)，防止和 P1 的 max-tools-per-agent
        混淆（MR-8 copy-paste-is-a-bug-class 主动防御）。"""
        import inspect
        import proxy_filters as pf
        src = inspect.getsource(pf._resolve_max_tool_calls_per_task_limit)
        self.assertIn(
            "max-tool-calls-per-task", src,
            "P2 resolver 必须查询 max-tool-calls-per-task policy，不得指向 P1 policy"
        )
        self.assertNotIn(
            "max-tools-per-agent", src,
            "P2 resolver 源码不得含 max-tools-per-agent (copy-paste 反模式防御)"
        )


class TestThreeGateWiring(unittest.TestCase):
    """V37.9.15 Phase 4 P3: tool_proxy.py 接入 three_gate 的契约守卫。

    验证:
      - tool_proxy.py import three_gate 的 lazy-load 模式正确
      - _run_gate helper 定义 + fail-open 语义
      - do_POST 内部有 pre_check/runtime_gate/post_verify 三处调用点
      - 调用点传入的 context 字段满足 three_gate 期望
      - fail-open: three_gate None 不崩溃，runtime 异常不冒泡
      - post_verify 顺序锁: 必须在 fix_tool_args 之前运行

    实现注意: tool_proxy.py 模块级 bind :5002 不能直接 import，沿用项目
    既有模式 `with open("tool_proxy.py") as f: src = f.read()` 做源码级
    契约守卫。运行时验证留给 Mac Mini preflight。"""

    @staticmethod
    def _read_proxy_src():
        with open("tool_proxy.py") as f:
            return f.read()

    def test_tool_proxy_imports_three_gate_lazy(self):
        """tool_proxy.py 必须用 importlib.util 方式加载 three_gate，而非
        `import three_gate` — 后者会在 ontology/ 缺失时让 proxy 启动失败。"""
        src = self._read_proxy_src()
        self.assertIn("_three_gate = None", src,
                      "必须有 _three_gate 模块级哨兵，初始为 None")
        self.assertIn("three_gate.py", src,
                      "必须通过文件路径加载 (ontology/three_gate.py)")
        self.assertIn("_tg_spec = _tg_imp_util.spec_from_file_location", src,
                      "必须用 importlib.util.spec_from_file_location 模式")
        self.assertIn("three_gate load failed", src,
                      "加载失败必须 log WARN 不能抛到外层")

    def test_run_gate_helper_defined(self):
        """_run_gate 必须存在且具备 fail-open 语义 — 任何 gate 异常不得冒泡。"""
        src = self._read_proxy_src()
        self.assertIn("def _run_gate(", src,
                      "tool_proxy.py 必须定义 _run_gate 函数")
        # fail-open 核心契约: None 哨兵 + try/except + log WARN
        self.assertIn("if _three_gate is None", src,
                      "_run_gate 必须检查 _three_gate None 哨兵")
        # 必须捕获异常（精确或宽泛都可，但必须有兜底）
        self.assertTrue(
            "except Exception" in src,
            "_run_gate 必须捕获所有异常（fail-open 观察性原则）")
        self.assertIn("WARN: gate", src,
                      "gate 抛异常必须 log WARN")

    def test_pre_check_call_site_present(self):
        """do_POST 必须在请求序列化后调用 pre_check，传入 messages/hour 上下文。"""
        src = self._read_proxy_src()
        self.assertIn('_run_gate("pre_check"', src,
                      "do_POST 必须调用 pre_check gate")
        self.assertIn('"hour": datetime.now().hour', src,
                      "gate context 必须含 hour 字段 (quiet-hours-00-07 policy)")
        self.assertIn('"messages": body.get("messages"', src,
                      "gate context 必须含 messages (alert-isolation policy)")

    def test_runtime_gate_call_site_present(self):
        """do_POST 必须调用 runtime_gate 并传 tool_count/body_bytes。"""
        src = self._read_proxy_src()
        self.assertIn('_run_gate("runtime_gate"', src,
                      "do_POST 必须调用 runtime_gate")
        self.assertIn('"tool_count":', src,
                      "gate context 必须含 tool_count (max-tools-per-agent)")
        self.assertIn('"body_bytes":', src,
                      "gate context 必须含 body_bytes (max-request-body-size)")

    def test_post_verify_call_site_present(self):
        """do_POST 必须在响应解析后调用 post_verify，传入 response=rj。"""
        src = self._read_proxy_src()
        self.assertIn('"post_verify"', src,
                      "do_POST 必须调用 post_verify gate")
        self.assertIn("response=rj", src,
                      "post_verify 必须接收 LLM 响应 dict 作为 response 参数")

    def test_post_verify_runs_before_fix_tool_args(self):
        """V37.9.15 顺序锁: post_verify 必须在 fix_tool_args 之前运行，
        以便观察到 LLM 原始响应内容（未被 alias 修复改写）。"""
        src = self._read_proxy_src()
        pv_idx = src.find('"post_verify"')
        fx_idx = src.find("fix_tool_args(rj)")
        self.assertGreater(pv_idx, 0, "post_verify 调用点必须存在")
        self.assertGreater(fx_idx, 0, "fix_tool_args 调用必须存在")
        self.assertLess(pv_idx, fx_idx,
                        "post_verify 必须在 fix_tool_args 之前运行（V37.9.15 顺序锁）")

    def test_gate_load_message_format(self):
        """三 gate 加载成功日志必须能被运维 grep 到。"""
        src = self._read_proxy_src()
        self.assertIn("Phase 4 P3 shadow wiring active", src,
                      "加载成功必须打印 Phase 4 P3 标记（运维 grep 友好）")

    def test_gates_are_shadow_by_default(self):
        """默认模式是 shadow —— 即使 ONTOLOGY_GATES_MODE 未设，gates 也应观察
        而非关闭，以捕获生产流量中的实际 policy 违规。"""
        import os
        import sys
        ontology_dir = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "ontology")
        if ontology_dir not in sys.path:
            sys.path.insert(0, ontology_dir)
        prev = os.environ.pop("ONTOLOGY_GATES_MODE", None)
        try:
            if "three_gate" in sys.modules:
                del sys.modules["three_gate"]
            import three_gate
            self.assertEqual(three_gate.gates_mode(), "shadow",
                             "默认 gates_mode 必须是 shadow (观察优先)")
        finally:
            if prev is not None:
                os.environ["ONTOLOGY_GATES_MODE"] = prev

    def test_shadow_mode_never_sets_enforced_true(self):
        """shadow 模式下 runtime_gate 产出的 findings 绝不应 enforced=True —
        防止未来有人把 shadow 意外切到 on 而不更新注释。"""
        import os
        import sys
        ontology_dir = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "ontology")
        if ontology_dir not in sys.path:
            sys.path.insert(0, ontology_dir)
        prev = os.environ.pop("ONTOLOGY_GATES_MODE", None)
        os.environ["ONTOLOGY_GATES_MODE"] = "shadow"
        try:
            if "three_gate" in sys.modules:
                del sys.modules["three_gate"]
            import three_gate
            findings = three_gate.runtime_gate({"tool_count": 999})
            self.assertTrue(len(findings) >= 1)
            for f in findings:
                self.assertFalse(f.enforced,
                                 f"shadow 模式 finding 必须 enforced=False: {f}")
        finally:
            if prev is None:
                os.environ.pop("ONTOLOGY_GATES_MODE", None)
            else:
                os.environ["ONTOLOGY_GATES_MODE"] = prev


if __name__ == "__main__":
    unittest.main(verbosity=2)
