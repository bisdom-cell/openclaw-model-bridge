#!/usr/bin/env python3
"""
proxy_filters.py — V27 提取自 tool_proxy.py
所有过滤、修复、截断、SSE转换逻辑。纯函数 + 配置数据，无 HTTP/网络依赖。
"""
import json
import os
import threading
import time

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
    "browser_navigate": {"url", "profile", "target"},
    "browser_click": {"selector", "profile", "target"},
    "browser_type": {"selector", "text", "profile", "target"},
    "browser_snapshot": {"profile", "target"},
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


# [NO_TOOLS] 标记：消息中包含此标记时，proxy 强制清空工具列表
NO_TOOLS_MARKER = "[NO_TOOLS]"


def should_strip_tools(messages):
    """检查消息中是否包含 [NO_TOOLS] 标记，用于纯推理任务（如客户画像生成）。
    支持 content 为字符串或数组格式（OpenAI content blocks）。
    """
    for m in messages:
        content = m.get("content", "")
        if isinstance(content, str):
            if NO_TOOLS_MARKER in content:
                return True
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    text = block.get("text", "")
                    if isinstance(text, str) and NO_TOOLS_MARKER in text:
                        return True
    return False


def truncate_messages(messages, max_bytes=MAX_REQUEST_BYTES):
    """截断旧消息以控制请求体大小。保留所有 system 消息 + 最近的非 system 消息。
    返回 (truncated_messages, dropped_count)。
    """
    system = [m for m in messages if m.get("role") == "system"]
    others = [m for m in messages if m.get("role") != "system"]
    # 截断超大 system 消息（保留前 max_bytes/2 字符）
    sys_limit = max_bytes // 2
    for m in system:
        content = m.get("content", "")
        if isinstance(content, str) and len(content.encode()) > sys_limit:
            m["content"] = content[:sys_limit // 2] + "\n...[truncated]..."
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
        msg = choice.get("message") or {}
        tcs = msg.get("tool_calls")
        if tcs:
            for tc in tcs:
                fn = tc.get("function") or {}
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
        msg = choice.get("message") or {}
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


# ---------------------------------------------------------------------------
# Token / Error 监控
# ---------------------------------------------------------------------------

# Qwen3-235B context window limit (tokens)
CONTEXT_LIMIT = 260000
# 告警阈值：prompt_tokens 超过此值时触发预警
TOKEN_WARN_THRESHOLD = int(CONTEXT_LIMIT * 0.75)  # 195K
TOKEN_CRITICAL_THRESHOLD = int(CONTEXT_LIMIT * 0.90)  # 234K
# 连续错误告警阈值
CONSECUTIVE_ERROR_ALERT = 3

STATS_FILE = os.path.expanduser("~/proxy_stats.json")


class ProxyStats:
    """轻量级请求统计跟踪器（进程内单例，线程安全）。"""

    FLUSH_INTERVAL = 10  # 最多每10秒刷盘一次

    def __init__(self):
        self._lock = threading.Lock()
        self.total_requests = 0
        self.total_errors = 0
        self.consecutive_errors = 0
        self.last_prompt_tokens = 0
        self.last_total_tokens = 0
        self.max_prompt_tokens_today = 0
        self.last_error_code = 0
        self.last_error_msg = ""
        self.last_success_time = 0
        self.last_error_time = 0
        self.alerts = []  # 待发送告警列表
        self._today = time.strftime("%Y-%m-%d")
        self._last_flush = 0.0

    def _check_day_reset(self):
        today = time.strftime("%Y-%m-%d")
        if today != self._today:
            self.max_prompt_tokens_today = 0
            self.total_requests = 0
            self.total_errors = 0
            self._today = today

    def record_success(self, usage: dict):
        """记录一次成功请求的 token 用量。"""
        with self._lock:
            self._check_day_reset()
            self.total_requests += 1
            self.consecutive_errors = 0
            self.last_success_time = time.time()

            prompt_tokens = usage.get("prompt_tokens", 0)
            total_tokens = usage.get("total_tokens", 0)
            self.last_prompt_tokens = prompt_tokens
            self.last_total_tokens = total_tokens
            if prompt_tokens > self.max_prompt_tokens_today:
                self.max_prompt_tokens_today = prompt_tokens

            # Token 阈值告警
            if prompt_tokens >= TOKEN_CRITICAL_THRESHOLD:
                self.alerts.append(
                    f"🔴 Qwen context 临界！prompt_tokens={prompt_tokens:,} "
                    f"(limit={CONTEXT_LIMIT:,}, 已用{prompt_tokens*100//CONTEXT_LIMIT}%)"
                    f"\n下一次请求大概率触发 403/502，建议立即重置 session"
                )
            elif prompt_tokens >= TOKEN_WARN_THRESHOLD:
                self.alerts.append(
                    f"🟡 Qwen context 预警：prompt_tokens={prompt_tokens:,} "
                    f"(limit={CONTEXT_LIMIT:,}, 已用{prompt_tokens*100//CONTEXT_LIMIT}%)"
                )

            self._maybe_flush()

    def record_error(self, status_code: int, error_msg: str = ""):
        """记录一次错误响应。"""
        with self._lock:
            self._check_day_reset()
            self.total_requests += 1
            self.total_errors += 1
            self.consecutive_errors += 1
            self.last_error_code = status_code
            self.last_error_msg = error_msg[:200]
            self.last_error_time = time.time()

            # 连续错误告警
            if self.consecutive_errors >= CONSECUTIVE_ERROR_ALERT:
                self.alerts.append(
                    f"🔴 Proxy 连续 {self.consecutive_errors} 次错误！"
                    f"\n最近错误: HTTP {status_code} — {error_msg[:100]}"
                    f"\n可能原因: context 超限(260K) / 远端模型下线 / 网络故障"
                )

            self._maybe_flush()

    def pop_alerts(self) -> list:
        """取出并清空待发送告警。"""
        with self._lock:
            alerts = self.alerts[:]
            self.alerts = []
            return alerts

    def _maybe_flush(self):
        """节流刷盘：最多每 FLUSH_INTERVAL 秒写一次文件。"""
        now = time.time()
        if now - self._last_flush >= self.FLUSH_INTERVAL:
            self._last_flush = now
            self._write_stats()

    def _write_stats(self):
        """写入统计文件供 watchdog 读取（调用方已持锁）。"""
        try:
            data = {
                "updated": time.strftime("%Y-%m-%d %H:%M:%S"),
                "total_requests": self.total_requests,
                "total_errors": self.total_errors,
                "consecutive_errors": self.consecutive_errors,
                "last_prompt_tokens": self.last_prompt_tokens,
                "last_total_tokens": self.last_total_tokens,
                "max_prompt_tokens_today": self.max_prompt_tokens_today,
                "context_limit": CONTEXT_LIMIT,
                "context_usage_pct": round(self.last_prompt_tokens * 100 / CONTEXT_LIMIT, 1) if CONTEXT_LIMIT else 0,
                "last_error": {
                    "code": self.last_error_code,
                    "msg": self.last_error_msg,
                    "time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.last_error_time)) if self.last_error_time else "",
                },
                "last_success_time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.last_success_time)) if self.last_success_time else "",
            }
            with open(STATS_FILE, "w") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except OSError:
            pass

    def get_stats_dict(self) -> dict:
        """返回当前统计数据（用于 /stats 端点）。"""
        with self._lock:
            self._check_day_reset()
            return {
                "updated": time.strftime("%Y-%m-%d %H:%M:%S"),
                "total_requests": self.total_requests,
                "total_errors": self.total_errors,
                "consecutive_errors": self.consecutive_errors,
                "last_prompt_tokens": self.last_prompt_tokens,
                "last_total_tokens": self.last_total_tokens,
                "max_prompt_tokens_today": self.max_prompt_tokens_today,
                "context_limit": CONTEXT_LIMIT,
                "context_usage_pct": round(self.last_prompt_tokens * 100 / CONTEXT_LIMIT, 1) if CONTEXT_LIMIT else 0,
                "last_error_code": self.last_error_code,
                "last_success_time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.last_success_time)) if self.last_success_time else "",
            }


# 进程级单例
proxy_stats = ProxyStats()
