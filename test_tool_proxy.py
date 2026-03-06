#!/usr/bin/env python3
"""
Unit tests for tool_proxy.py core logic.
Run: python3 -m pytest test_tool_proxy.py -v
  or: python3 test_tool_proxy.py
"""
import json
import sys
import unittest

# Inline the functions under test so we don't import the running server
# (tool_proxy.py calls socketserver.TCPServer at module level)

TOOL_PARAMS = {
    "web_search": {"query"},
    "web_fetch": {"url"},
    "read": {"path"},
    "write": {"path", "content"},
    "edit": {"path", "old_text", "new_text"},
    "exec": {"command"},
    "memory_search": {"query"},
    "memory_get": {"key"},
    "cron": {"action", "schedule", "command", "id", "name", "sessionTarget", "payload", "job"},
    "message": {"to", "text"},
    "tts": {"text"},
}

VALID_BROWSER_PROFILES = {"openclaw", "chrome"}

_log_messages = []

def log(msg):
    _log_messages.append(msg)

def fix_tool_args(rj):
    modified = False
    for choice in rj.get("choices", []):
        msg = choice.get("message", {})
        tcs = msg.get("tool_calls")
        if tcs:
            for tc in tcs:
                fn = tc.get("function", {})
                name = fn.get("name", "")
                args_str = fn.get("arguments", "{}")
                try:
                    args = json.loads(args_str) if isinstance(args_str, str) else args_str
                except (json.JSONDecodeError, ValueError) as e:
                    log(f"FIX: failed to parse args for {name}: {e}")
                    args = {}

                if name.startswith("browser"):
                    if "profile" in args and args["profile"] not in VALID_BROWSER_PROFILES:
                        log(f"FIX: browser profile '{args['profile']}' -> 'openclaw'")
                        args["profile"] = "openclaw"
                        fn["arguments"] = json.dumps(args)
                        modified = True
                    elif "target" in args and args["target"] not in VALID_BROWSER_PROFILES:
                        log(f"FIX: browser target '{args['target']}' -> 'openclaw'")
                        args["target"] = "openclaw"
                        fn["arguments"] = json.dumps(args)
                        modified = True
                    if "profile" not in args and "target" not in args:
                        args["profile"] = "openclaw"
                        log(f"FIX: browser missing profile, set to 'openclaw'")
                        fn["arguments"] = json.dumps(args)
                        modified = True

                allowed = TOOL_PARAMS.get(name)
                if allowed:
                    alias_changed = False
                    if name == "read" and "path" not in args:
                        for alt in ["file_path", "file", "filepath", "filename"]:
                            if alt in args:
                                args["path"] = args.pop(alt)
                                alias_changed = True
                                break
                    if name == "exec" and "command" not in args:
                        for alt in ["cmd", "shell", "bash", "script"]:
                            if alt in args:
                                args["command"] = args.pop(alt)
                                alias_changed = True
                                break
                    if name == "write" and "content" not in args:
                        for alt in ["text", "data", "body", "file_content"]:
                            if alt in args:
                                args["content"] = args.pop(alt)
                                alias_changed = True
                                break
                    if name == "web_search" and "query" not in args:
                        for alt in ["search_query", "q", "keyword", "search"]:
                            if alt in args:
                                args["query"] = args.pop(alt)
                                alias_changed = True
                                break

                    clean = {k: v for k, v in args.items() if k in allowed}
                    if clean != args or alias_changed:
                        log(f"FIX: {name} {list(args.keys())} -> {list(clean.keys())}")
                        fn["arguments"] = json.dumps(clean)
                        modified = True
    return modified


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


class TestBrowserProfileFix(unittest.TestCase):

    def setUp(self):
        _log_messages.clear()

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
        # target is valid, no profile either so profile gets injected
        fix_tool_args(rj)
        self.assertEqual(get_args(rj)["target"], "openclaw")


class TestParamAliases(unittest.TestCase):

    def setUp(self):
        _log_messages.clear()

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

    def setUp(self):
        _log_messages.clear()

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

    def setUp(self):
        _log_messages.clear()

    def test_invalid_json_args_become_empty(self):
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
        fix_tool_args(rj)
        # Should not raise; log should contain parse error
        self.assertTrue(any("failed to parse args" in m for m in _log_messages))

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
