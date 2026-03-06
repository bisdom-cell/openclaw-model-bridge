#!/usr/bin/env python3
"""
proxy_filters.py — V27 提取自 tool_proxy.py
所有过滤、修复、截断、SSE转换逻辑。纯函数 + 配置数据，无 HTTP/网络依赖。
"""
import json

# ---------------------------------------------------------------------------
# 配置数据
# ---------------------------------------------------------------------------

# 允许通过的工具（白名单）
ALLOWED_TOOLS = {
    "web_search", "web_fetch",
    "read", "write", "edit",
    "exec",
    "memory_search", "memory_get",
    "cron", "message", "tts",
}

# 前缀匹配的工具（browser_navigate, browser_click 等）
ALLOWED_PREFIXES = ["browser"]

# 简化后的 schema（修正 Qwen3 参数幻觉）
CLEAN_SCHEMAS = {
    "web_search": {
        "type": "object",
        "properties": {"query": {"type": "string", "description": "Search query string"}},
        "required": ["query"],
        "additionalProperties": False
    },
    "web_fetch": {
        "type": "object",
        "properties": {"url": {"type": "string", "description": "URL to fetch"}},
        "required": ["url"],
        "additionalProperties": False
    },
    "read": {
        "type": "object",
        "properties": {"path": {"type": "string", "description": "Absolute file path to read"}},
        "required": ["path"],
        "additionalProperties": False
    },
    "write": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute file path to write"},
            "content": {"type": "string", "description": "Content to write to file"}
        },
        "required": ["path", "content"],
        "additionalProperties": False
    },
    "edit": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute file path to edit"},
            "old_text": {"type": "string", "description": "Existing text to find and replace"},
            "new_text": {"type": "string", "description": "New text to replace with"}
        },
        "required": ["path", "old_text", "new_text"],
        "additionalProperties": False
    },
    "exec": {
        "type": "object",
        "properties": {"command": {"type": "string", "description": "Shell command to execute"}},
        "required": ["command"],
        "additionalProperties": False
    },
    "memory_search": {
        "type": "object",
        "properties": {"query": {"type": "string", "description": "Search query for memory"}},
        "required": ["query"],
        "additionalProperties": False
    },
    "memory_get": {
        "type": "object",
        "properties": {"key": {"type": "string", "description": "Memory key to retrieve"}},
        "required": ["key"],
        "additionalProperties": False
    },
    "cron": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "description": "Action: add, list, remove"},
            "name": {"type": "string", "description": "Name of the cron job"},
            "schedule": {"type": "object", "description": "Schedule object with kind, expr, tz fields. Example: {kind: cron, expr: 0 9 * * *, tz: Asia/Hong_Kong}"},
            "sessionTarget": {"type": "string", "description": "Session target, use 'current' for current session"},
            "payload": {"type": "string", "description": "The message/instruction to execute when triggered"},
            "id": {"type": "string", "description": "Cron job ID for remove action"}
        },
        "required": ["action"]
    },
    "message": {
        "type": "object",
        "properties": {
            "to": {"type": "string", "description": "Recipient phone number or contact"},
            "text": {"type": "string", "description": "Message text to send"}
        },
        "required": ["to", "text"]
    },
    "tts": {
        "type": "object",
        "properties": {"text": {"type": "string", "description": "Text to convert to speech"}},
        "required": ["text"],
        "additionalProperties": False
    }
}

# 每个工具的合法参数集（用于响应清理）
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

# 浏览器合法 profile
VALID_BROWSER_PROFILES = {"openclaw", "chrome"}

# 请求体大小上限
MAX_REQUEST_BYTES = 200000  # 200KB safe limit

# ---------------------------------------------------------------------------
# 过滤函数
# ---------------------------------------------------------------------------

def is_allowed(name):
    """检查工具名是否在白名单中（精确匹配 + 前缀匹配）"""
    if name in ALLOWED_TOOLS:
        return True
    for prefix in ALLOWED_PREFIXES:
        if name.startswith(prefix):
            return True
    return False


def filter_tools(tools, log_fn=None):
    """过滤工具列表，只保留白名单内的工具，并替换为简化 schema。
    返回 (filtered_tools, all_names, kept_names)。
    """
    all_names = [t.get("function", {}).get("name", "?") for t in tools]
    new_tools = []
    for t in tools:
        name = t.get("function", {}).get("name", "")
        if is_allowed(name):
            if name in CLEAN_SCHEMAS:
                t["function"]["parameters"] = CLEAN_SCHEMAS[name]
            new_tools.append(t)
    kept_names = [t.get("function", {}).get("name") for t in new_tools]
    return new_tools, all_names, kept_names


def truncate_messages(messages, max_bytes=MAX_REQUEST_BYTES):
    """截断旧消息以控制请求体大小。保留所有 system 消息 + 最近的非 system 消息。
    返回 (truncated_messages, dropped_count)。
    """
    system = [m for m in messages if m.get("role") == "system"]
    others = [m for m in messages if m.get("role") != "system"]
    total = len(json.dumps(system))
    keep = []
    for m in reversed(others):
        ms = len(json.dumps(m))
        if total + ms > max_bytes:
            break
        keep.insert(0, m)
        total += ms
    dropped = len(others) - len(keep)
    return system + keep, dropped


def fix_tool_args(rj):
    """修复模型返回的工具调用参数：
    1. 浏览器 profile 校验/注入
    2. 参数名别名映射
    3. 多余参数剥离
    返回 bool 表示是否有修改。
    """
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
                except (json.JSONDecodeError, ValueError):
                    args = {}

                # Fix browser profile
                if name.startswith("browser"):
                    if "profile" in args and args["profile"] not in VALID_BROWSER_PROFILES:
                        args["profile"] = "openclaw"
                        fn["arguments"] = json.dumps(args)
                        modified = True
                    elif "target" in args and args["target"] not in VALID_BROWSER_PROFILES:
                        args["target"] = "openclaw"
                        fn["arguments"] = json.dumps(args)
                        modified = True
                    if "profile" not in args and "target" not in args:
                        args["profile"] = "openclaw"
                        fn["arguments"] = json.dumps(args)
                        modified = True

                # Param alias + extra param stripping
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
                        fn["arguments"] = json.dumps(clean)
                        modified = True
    return modified


def build_sse_response(rj):
    """将标准 chat completion 响应转换为 SSE 格式字节流。"""
    chunks_out = []
    for choice in rj.get("choices", []):
        msg = choice.get("message", {})
        delta = {}
        if msg.get("role"):
            delta["role"] = msg["role"]
        if msg.get("content"):
            delta["content"] = msg["content"]
        if msg.get("tool_calls"):
            delta["tool_calls"] = msg["tool_calls"]
        chunk = {
            "id": rj.get("id", ""),
            "object": "chat.completion.chunk",
            "created": rj.get("created", 0),
            "model": rj.get("model", ""),
            "choices": [{
                "index": choice.get("index", 0),
                "delta": delta,
                "finish_reason": choice.get("finish_reason")
            }]
        }
        chunks_out.append(f"data: {json.dumps(chunk)}\n\n")
    chunks_out.append("data: [DONE]\n\n")
    return "".join(chunks_out).encode()
