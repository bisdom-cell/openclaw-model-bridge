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
    TOOL_PARAMS, VALID_BROWSER_PROFILES,
    ALLOWED_TOOLS, ALLOWED_PREFIXES,
)


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
        self.assertEqual(len(filtered), 2)
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
        self.assertEqual(filtered, [])


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

    def test_non_string_content(self):
        msgs = [{"role": "user", "content": [{"type": "text", "text": "hello"}]}]
        self.assertFalse(should_strip_tools(msgs))


if __name__ == "__main__":
    unittest.main(verbosity=2)
