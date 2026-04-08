#!/usr/bin/env python3
"""
proxy_filters.py — V27 提取自 tool_proxy.py
所有过滤、修复、截断、SSE转换逻辑。纯函数 + 配置数据，无 HTTP/网络依赖。
"""
import base64
import collections
import glob
import json
import os
import threading
import time

from config_loader import (
    MAX_REQUEST_BYTES as _CFG_MAX_REQUEST_BYTES,
    CONTEXT_LIMIT as _CFG_CONTEXT_LIMIT,
    TOKEN_WARN_THRESHOLD as _CFG_TOKEN_WARN,
    TOKEN_CRITICAL_THRESHOLD as _CFG_TOKEN_CRITICAL,
    CONSECUTIVE_ERROR_ALERT as _CFG_CONSECUTIVE_ERROR,
    SIMPLE_MAX_MSGS as _CFG_SIMPLE_MAX_MSGS,
    SIMPLE_MAX_USER_LEN as _CFG_SIMPLE_MAX_USER_LEN,
    COMPLEX_MIN_MSGS as _CFG_COMPLEX_MIN_MSGS,
    STATS_FLUSH_INTERVAL as _CFG_FLUSH_INTERVAL,
    MAX_TOOLS as _CFG_MAX_TOOLS,
)

# ---------------------------------------------------------------------------
# Phase 1: Ontology 特性开关（默认 off = 使用硬编码，完全等价于改动前）
# ONTOLOGY_MODE=on 时从 ontology/tool_ontology.yaml 加载数据
# 任何加载失败自动回退到硬编码，等价于开关 off
# 回退方式：ONTOLOGY_MODE=off 或 rm -rf ontology/
# ---------------------------------------------------------------------------
_ONTOLOGY_MODE = os.environ.get("ONTOLOGY_MODE", "off").lower() == "on"

# ---------------------------------------------------------------------------
# 配置数据（硬编码 — 始终定义，作为基线和回退）
# ---------------------------------------------------------------------------

# 允许通过的工具（白名单）
ALLOWED_TOOLS = {
    "web_search", "web_fetch",
    "read", "write", "edit",
    "exec",
    "memory_search", "memory_get",
    "sessions_spawn", "sessions_send", "sessions_history", "agents_list",
    "cron", "message", "tts",
    "image",
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
        "required": ["action"],
        "additionalProperties": False
    },
    "message": {
        "type": "object",
        "properties": {
            "to": {"type": "string", "description": "Recipient phone number or contact"},
            "text": {"type": "string", "description": "Message text to send"}
        },
        "required": ["to", "text"],
        "additionalProperties": False
    },
    "tts": {
        "type": "object",
        "properties": {"text": {"type": "string", "description": "Text to convert to speech"}},
        "required": ["text"],
        "additionalProperties": False
    },
    "sessions_spawn": {
        "type": "object",
        "properties": {
            "agent": {"type": "string", "description": "Agent name to spawn (e.g. 'ops', 'research')"},
            "message": {"type": "string", "description": "Initial message/instruction to send to the spawned agent"},
        },
        "required": ["agent", "message"],
        "additionalProperties": False
    },
    "sessions_send": {
        "type": "object",
        "properties": {
            "sessionId": {"type": "string", "description": "Session ID of the target sub-agent (returned by sessions_spawn)"},
            "message": {"type": "string", "description": "Message to send to the sub-agent"},
        },
        "required": ["sessionId", "message"],
        "additionalProperties": False
    },
    "sessions_history": {
        "type": "object",
        "properties": {
            "sessionId": {"type": "string", "description": "Session ID to retrieve history for"},
        },
        "required": ["sessionId"],
        "additionalProperties": False
    },
    "agents_list": {
        "type": "object",
        "properties": {},
        "additionalProperties": False
    },
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
    "sessions_spawn": {"agent", "message"},
    "sessions_send": {"sessionId", "message"},
    "sessions_history": {"sessionId"},
    "agents_list": set(),
    "browser_navigate": {"url", "profile", "target"},
    "browser_click": {"selector", "profile", "target"},
    "browser_type": {"selector", "text", "profile", "target"},
    "browser_snapshot": {"profile", "target"},
    "data_clean": {"action", "file", "ops", "fix_case_cols", "fix_date_cols"},
    "search_kb": {"query", "source", "recent_hours"},
}

# 浏览器合法 profile
VALID_BROWSER_PROFILES = {"openclaw", "chrome"}

# 请求体大小上限（从 config.yaml 加载）
MAX_REQUEST_BYTES = _CFG_MAX_REQUEST_BYTES

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
    注入 proxy 自定义工具（如 data_clean）。
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

    # 注入 proxy 自定义工具
    for custom_tool in CUSTOM_TOOLS:
        new_tools.append(custom_tool)

    # V36.2: 硬性工具数量上限（CLAUDE.md: "工具数量 <= 12，超出导致模型混乱"）
    # 如果超限，保留 custom tools，截断 gateway tools
    if len(new_tools) > _CFG_MAX_TOOLS:
        if log_fn:
            log_fn(f"WARN: tool count {len(new_tools)} > {_CFG_MAX_TOOLS}, truncating")
        custom_names = {t.get("function", {}).get("name") for t in CUSTOM_TOOLS}
        custom = [t for t in new_tools if t.get("function", {}).get("name") in custom_names]
        gateway = [t for t in new_tools if t.get("function", {}).get("name") not in custom_names]
        new_tools = gateway[:_CFG_MAX_TOOLS - len(custom)] + custom

    kept_names = [t.get("function", {}).get("name") for t in new_tools]

    # 语义观察：ontology 对每个通过的工具提供语义分类
    # 不改变过滤行为，只产生语义信号供日志和未来策略使用
    if _onto_engine and log_fn:
        try:
            high_risk = []
            for name in kept_names:
                cl = _onto_engine.classify_tool_call(name)
                if cl.get("risk_level") == "high":
                    high_risk.append(name)
            if high_risk:
                log_fn(f"ONTO: {len(high_risk)} high-risk tools: {','.join(high_risk)}")
        except Exception:
            pass  # 语义观察失败不影响主流程

    return new_tools, all_names, kept_names


# ---------------------------------------------------------------------------
# Proxy 自定义工具（由 proxy 拦截执行，不经过 Gateway）
# ---------------------------------------------------------------------------

CUSTOM_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "data_clean",
            "description": "分析和清洗数据文件。支持 CSV/TSV/JSON/JSONL/Excel。"
                           "用户上传的文件在 ~/.openclaw/media/inbound/ 目录下。",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["profile", "execute", "list_ops"],
                        "description": "profile=数据质量诊断, execute=执行清洗, list_ops=可用操作列表"
                    },
                    "file": {
                        "type": "string",
                        "description": "数据文件的完整路径（如 ~/.openclaw/media/inbound/xxx.xlsx）"
                    },
                    "ops": {
                        "type": "string",
                        "description": "execute时的清洗操作，逗号分隔: trim,dedup,dedup_near,fix_dates,fix_case,fill_missing,remove_test"
                    },
                    "fix_case_cols": {
                        "type": "string",
                        "description": "fix_case操作的目标列名，逗号分隔"
                    },
                    "fix_date_cols": {
                        "type": "string",
                        "description": "fix_dates操作的目标列名，逗号分隔"
                    },
                },
                "required": ["action"],
                "additionalProperties": False,
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_kb",
            "description": "搜索用户的知识库。当用户提到论文、文档、文章、最近的XX、今天有什么、找一下、有没有关于XX时，必须调用此工具。"
                           "知识库包含：ArXiv/HuggingFace/SemanticScholar/DBLP/ACL论文、HackerNews热帖、货代动态、用户笔记。"
                           "当用户问'今天/最近有什么新内容'时，设置 recent_hours=24。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索关键词（如 'DeepSeek'、'大模型'、'RAG'）。当使用 recent_hours 时可为空或描述性文字"
                    },
                    "source": {
                        "type": "string",
                        "enum": ["all", "arxiv", "hf", "semantic_scholar", "dblp", "acl", "hn", "notes"],
                        "description": "搜索范围。默认 all 搜索全部来源"
                    },
                    "recent_hours": {
                        "type": "integer",
                        "description": "返回最近N小时内更新的内容（按时间倒序）。用于'今天有什么新内容'类查询，设24即可"
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            }
        }
    },
]

# 自定义工具名称集合（用于 proxy 拦截判断）
CUSTOM_TOOL_NAMES = {t["function"]["name"] for t in CUSTOM_TOOLS}


# ---------------------------------------------------------------------------
# Phase 1: Ontology 数据覆盖（仅 ONTOLOGY_MODE=on 时生效）
# 回退逻辑：加载失败 → 保留上方硬编码不变 → 等价于开关 off
# ---------------------------------------------------------------------------
if _ONTOLOGY_MODE:
    try:
        import importlib.util as _imp_util
        _onto_engine_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "ontology", "engine.py")
        if os.path.exists(_onto_engine_path):
            _spec = _imp_util.spec_from_file_location("_onto_engine", _onto_engine_path)
            _mod = _imp_util.module_from_spec(_spec)
            _spec.loader.exec_module(_mod)
            _onto = _mod.ToolOntology()
            _data = _onto.generate_proxy_data()

            # 覆盖硬编码（只有全部生成成功才覆盖）
            ALLOWED_TOOLS = _data["ALLOWED_TOOLS"]
            ALLOWED_PREFIXES = _data["ALLOWED_PREFIXES"]
            CLEAN_SCHEMAS = _data["CLEAN_SCHEMAS"]
            TOOL_PARAMS = _data["TOOL_PARAMS"]
            CUSTOM_TOOLS = _data["CUSTOM_TOOLS"]
            CUSTOM_TOOL_NAMES = _data["CUSTOM_TOOL_NAMES"]
            VALID_BROWSER_PROFILES = _data["VALID_BROWSER_PROFILES"]

            import logging as _log
            _log.getLogger("proxy_filters").info(
                "ONTOLOGY_MODE=on: loaded %d tools from ontology/tool_ontology.yaml",
                len(ALLOWED_TOOLS))

            # 保留引擎实例供语义观察使用
            _onto_engine = _onto

            # 清理其他临时变量
            del _spec, _mod, _data
    except Exception as _e:
        import logging as _log
        _log.getLogger("proxy_filters").warning(
            "ONTOLOGY_MODE=on but load failed, falling back to hardcoded: %s", _e)
        # 硬编码变量未被修改，安全回退

# 语义引擎实例（ONTOLOGY_MODE=on 时可用，否则 None）
_onto_engine = locals().get("_onto_engine") if _ONTOLOGY_MODE else None


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


def truncate_messages(messages, max_bytes=MAX_REQUEST_BYTES, last_prompt_tokens=0):
    """截断旧消息以控制请求体大小和 token 用量。

    保留所有 system 消息 + 最近的非 system 消息。
    V31: 当上一次 prompt_tokens 已接近 context limit 时，动态缩减 max_bytes
    以主动削减历史消息，防止 context 溢出。
    返回 (truncated_messages, dropped_count)。
    """
    # ── V31: 基于上一次 prompt_tokens 的动态裁剪 ──
    # 原理：prompt_tokens 反映 LLM 实际消耗，包括 system prompt、tool schema、
    # KV cache 等 messages bytes 无法衡量的开销。
    # 当 prompt_tokens 已高时，主动压缩 messages 给 LLM 更多呼吸空间。
    if last_prompt_tokens > 0 and CONTEXT_LIMIT > 0:
        usage_pct = last_prompt_tokens / CONTEXT_LIMIT
        if usage_pct >= 0.85:
            # 临界：只保留最近 ~50KB 消息（大幅裁剪）
            max_bytes = min(max_bytes, 50000)
        elif usage_pct >= 0.70:
            # 预警：保留最近 ~100KB 消息（适度裁剪）
            max_bytes = min(max_bytes, 100000)
        # < 70%: 使用默认 200KB，不额外裁剪

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


# ---------------------------------------------------------------------------
# 多模态媒体注入
# ---------------------------------------------------------------------------

MEDIA_DIR = os.path.expanduser("~/.openclaw/media/inbound")
MEDIA_TAG = "<media:image>"
# 图片过期时间：5分钟内的图片才注入（避免注入很久以前的图片）
MEDIA_MAX_AGE_SECONDS = 300

def _find_recent_image():
    """找到 MEDIA_DIR 中最近修改的图片文件，返回路径或 None。"""
    if not os.path.isdir(MEDIA_DIR):
        return None
    candidates = []
    for ext in ("*.jpg", "*.jpeg", "*.png", "*.gif", "*.webp"):
        candidates.extend(glob.glob(os.path.join(MEDIA_DIR, ext)))
    if not candidates:
        return None
    newest = max(candidates, key=os.path.getmtime)
    age = time.time() - os.path.getmtime(newest)
    if age > MEDIA_MAX_AGE_SECONDS:
        return None
    return newest


def inject_media_into_messages(messages, log_fn=None):
    """检测用户消息中的 <media:image> 标记，注入 base64 图片数据。
    返回 (messages, injected: bool)。
    """
    # 找到包含 <media:image> 的最后一条用户消息的索引
    target_idx = None
    for i in range(len(messages) - 1, -1, -1):
        m = messages[i]
        if m.get("role") != "user":
            continue
        content = m.get("content", "")
        if isinstance(content, str) and MEDIA_TAG in content:
            target_idx = i
            break
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and MEDIA_TAG in part.get("text", ""):
                    target_idx = i
                    break
                elif isinstance(part, str) and MEDIA_TAG in part:
                    target_idx = i
                    break
            if target_idx is not None:
                break

    if target_idx is None:
        return messages, False

    img_path = _find_recent_image()
    if not img_path:
        if log_fn:
            log_fn("MEDIA: <media:image> found but no recent image file")
        return messages, False

    # 读取并 base64 编码
    try:
        with open(img_path, "rb") as f:
            img_data = f.read()
        if len(img_data) > 10 * 1024 * 1024:  # 10MB 上限
            if log_fn:
                log_fn(f"MEDIA: Image too large ({len(img_data)} bytes), skipping")
            return messages, False
        b64 = base64.b64encode(img_data).decode("ascii")
    except OSError as e:
        if log_fn:
            log_fn(f"MEDIA: Failed to read {img_path}: {e}")
        return messages, False

    # 判断 MIME 类型
    ext = os.path.splitext(img_path)[1].lower()
    mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
            "gif": "image/gif", "webp": "image/webp"}.get(ext.lstrip("."), "image/jpeg")

    # 替换目标消息的 content 为多模态格式
    m = messages[target_idx]
    old_content = m.get("content", "")
    if isinstance(old_content, str):
        text_part = old_content.replace(MEDIA_TAG, "").strip()
    else:
        text_part = ""

    # 同时查找后续紧邻的纯文本用户消息（用户先发图片，再发文字提问）
    if not text_part:
        for j in range(target_idx + 1, min(target_idx + 3, len(messages))):
            nxt = messages[j]
            if nxt.get("role") == "user":
                nxt_content = nxt.get("content", "")
                if isinstance(nxt_content, str) and nxt_content.strip() and MEDIA_TAG not in nxt_content:
                    text_part = nxt_content.strip()
                    break

    new_content = [
        {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
    ]
    if text_part:
        new_content.append({"type": "text", "text": text_part})
    else:
        new_content.append({"type": "text", "text": "请描述这张图片的内容。"})

    messages[target_idx]["content"] = new_content

    if log_fn:
        log_fn(f"MEDIA: Injected {os.path.basename(img_path)} ({len(img_data)} bytes, {mime}) into msg[{target_idx}]")

    return messages, True


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

# 从 config.yaml 加载（阈值中心化 V32）
CONTEXT_LIMIT = _CFG_CONTEXT_LIMIT
TOKEN_WARN_THRESHOLD = _CFG_TOKEN_WARN
TOKEN_CRITICAL_THRESHOLD = _CFG_TOKEN_CRITICAL
CONSECUTIVE_ERROR_ALERT = _CFG_CONSECUTIVE_ERROR

STATS_FILE = os.path.expanduser("~/proxy_stats.json")


class ProxyStats:
    """请求统计跟踪器 + SLO 指标收集（进程内单例，线程安全）。

    V32 新增：延迟百分位、错误分类、工具成功率、降级率、自动恢复率。
    """

    FLUSH_INTERVAL = _CFG_FLUSH_INTERVAL
    LATENCY_WINDOW = 200  # 保留最近 N 个请求延迟用于百分位计算

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

        # --- SLO 指标（V32 新增）---
        self._latencies = collections.deque(maxlen=self.LATENCY_WINDOW)
        self.errors_by_type = {"timeout": 0, "context_overflow": 0, "backend": 0, "other": 0}
        self.tool_calls_total = 0
        self.tool_calls_success = 0
        self.fallback_count = 0        # 降级次数
        self._recovery_total = 0       # 连续错误后恢复的次数
        self._failure_streaks = 0      # 曾发生的连续错误事件数

    def _check_day_reset(self):
        today = time.strftime("%Y-%m-%d")
        if today != self._today:
            self.max_prompt_tokens_today = 0
            self.total_requests = 0
            self.total_errors = 0
            self.errors_by_type = {"timeout": 0, "context_overflow": 0, "backend": 0, "other": 0}
            self.tool_calls_total = 0
            self.tool_calls_success = 0
            self.fallback_count = 0
            self._recovery_total = 0
            self._failure_streaks = 0
            self._latencies.clear()
            self._today = today

    def record_success(self, usage: dict, latency_ms: int = 0):
        """记录一次成功请求的 token 用量和延迟。"""
        with self._lock:
            self._check_day_reset()
            self.total_requests += 1

            # 自动恢复追踪：从连续错误中恢复
            if self.consecutive_errors > 0:
                self._recovery_total += 1
            self.consecutive_errors = 0
            self.last_success_time = time.time()

            # 延迟追踪
            if latency_ms > 0:
                self._latencies.append(latency_ms)

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

    def record_error(self, status_code: int, error_msg: str = "", latency_ms: int = 0):
        """记录一次错误响应，自动分类错误类型。"""
        with self._lock:
            self._check_day_reset()
            self.total_requests += 1
            self.total_errors += 1
            self.consecutive_errors += 1
            self.last_error_code = status_code
            self.last_error_msg = error_msg[:200]
            self.last_error_time = time.time()

            # 延迟追踪
            if latency_ms > 0:
                self._latencies.append(latency_ms)

            # 错误分类
            err_lower = error_msg.lower()
            if "timeout" in err_lower or "timed out" in err_lower or status_code == 504:
                self.errors_by_type["timeout"] += 1
            elif status_code == 403 or "context" in err_lower:
                self.errors_by_type["context_overflow"] += 1
            elif status_code in (502, 503):
                self.errors_by_type["backend"] += 1
            else:
                self.errors_by_type["other"] += 1

            # 连续错误事件计数（首次达到阈值时+1）
            if self.consecutive_errors == CONSECUTIVE_ERROR_ALERT:
                self._failure_streaks += 1

            # 连续错误告警
            if self.consecutive_errors >= CONSECUTIVE_ERROR_ALERT:
                self.alerts.append(
                    f"🔴 Proxy 连续 {self.consecutive_errors} 次错误！"
                    f"\n最近错误: HTTP {status_code} — {error_msg[:100]}"
                    f"\n可能原因: context 超限(260K) / 远端模型下线 / 网络故障"
                )

            self._maybe_flush()

    def record_tool_call(self, success: bool):
        """记录一次工具调用结果。"""
        with self._lock:
            self.tool_calls_total += 1
            if success:
                self.tool_calls_success += 1

    def record_fallback(self):
        """记录一次降级（使用 fallback provider）。"""
        with self._lock:
            self.fallback_count += 1

    def get_latency_percentiles(self) -> dict:
        """计算延迟百分位（调用方需持锁或在 _lock 内调用）。"""
        if not self._latencies:
            return {"p50": 0, "p95": 0, "p99": 0, "max": 0, "count": 0}
        s = sorted(self._latencies)
        n = len(s)
        return {
            "p50": s[int(n * 0.50)] if n > 0 else 0,
            "p95": s[min(int(n * 0.95), n - 1)] if n > 0 else 0,
            "p99": s[min(int(n * 0.99), n - 1)] if n > 0 else 0,
            "max": s[-1] if n > 0 else 0,
            "count": n,
        }

    def get_slo_status(self) -> dict:
        """评估当前 SLO 达标情况。"""
        with self._lock:
            lp = self.get_latency_percentiles()
            total = self.total_requests or 1
            tool_total = self.tool_calls_total or 1
            streaks = self._failure_streaks or 1

            return {
                "latency_p95_ms": lp["p95"],
                "latency_p95_ok": lp["p95"] <= _CFG_CONTEXT_LIMIT,  # placeholder, use SLO
                "tool_success_rate_pct": round(self.tool_calls_success * 100 / tool_total, 1),
                "degradation_rate_pct": round(self.fallback_count * 100 / total, 1),
                "timeout_rate_pct": round(self.errors_by_type["timeout"] * 100 / total, 1),
                "auto_recovery_rate_pct": round(self._recovery_total * 100 / streaks, 1) if self._failure_streaks > 0 else 100.0,
                "errors_by_type": dict(self.errors_by_type),
                "latency_percentiles": lp,
            }

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
            lp = self.get_latency_percentiles()
            total = self.total_requests or 1
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
                # SLO 指标（V32）
                "slo": {
                    "latency": lp,
                    "errors_by_type": dict(self.errors_by_type),
                    "tool_calls_total": self.tool_calls_total,
                    "tool_success_rate_pct": round(self.tool_calls_success * 100 / (self.tool_calls_total or 1), 1),
                    "degradation_rate_pct": round(self.fallback_count * 100 / total, 1),
                    "timeout_rate_pct": round(self.errors_by_type["timeout"] * 100 / total, 1),
                    "auto_recovery_rate_pct": round(self._recovery_total * 100 / (self._failure_streaks or 1), 1) if self._failure_streaks > 0 else 100.0,
                },
            }
            tmp = STATS_FILE + ".tmp"
            with open(tmp, "w") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            os.replace(tmp, STATS_FILE)
        except OSError:
            pass

    def get_stats_dict(self) -> dict:
        """返回当前统计数据（用于 /stats 端点，含 SLO 指标）。"""
        with self._lock:
            self._check_day_reset()
            lp = self.get_latency_percentiles()
            total = self.total_requests or 1
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
                "slo": {
                    "latency": lp,
                    "errors_by_type": dict(self.errors_by_type),
                    "tool_calls_total": self.tool_calls_total,
                    "tool_success_rate_pct": round(self.tool_calls_success * 100 / (self.tool_calls_total or 1), 1),
                    "degradation_rate_pct": round(self.fallback_count * 100 / total, 1),
                    "timeout_rate_pct": round(self.errors_by_type["timeout"] * 100 / total, 1),
                    "auto_recovery_rate_pct": round(self._recovery_total * 100 / (self._failure_streaks or 1), 1) if self._failure_streaks > 0 else 100.0,
                },
            }


# ---------------------------------------------------------------------------
# 智能路由：消息复杂度分类
# ---------------------------------------------------------------------------

# 复杂度阈值（从 config.yaml 加载）
_SIMPLE_MAX_MSGS = _CFG_SIMPLE_MAX_MSGS
_SIMPLE_MAX_USER_LEN = _CFG_SIMPLE_MAX_USER_LEN
_COMPLEX_MIN_MSGS = _CFG_COMPLEX_MIN_MSGS


def classify_complexity(messages, has_tools=False):
    """根据消息内容判断请求复杂度。

    返回:
        "simple"  — 短问答、闲聊、简单查询（可路由到快速模型）
        "complex" — 长对话、需要工具、多步推理（使用主模型）

    判断依据（纯函数，无副作用）：
    - 有工具 → complex（需要 tool calling 能力）
    - 对话轮数多 → complex
    - 最后一条用户消息很长 → complex（通常是复杂问题）
    - 系统消息包含 NO_TOOLS 标记 → simple（纯推理）
    """
    # 有工具注入 → 需要强模型的 tool calling
    if has_tools:
        return "complex"

    # [NO_TOOLS] 标记明确表示纯推理
    if should_strip_tools(messages):
        return "simple"

    # 非 system 消息数量
    non_sys = [m for m in messages if m.get("role") != "system"]
    if len(non_sys) >= _COMPLEX_MIN_MSGS:
        return "complex"

    # 最后一条用户消息长度
    last_user = ""
    for m in reversed(messages):
        if m.get("role") == "user":
            content = m.get("content", "")
            if isinstance(content, str):
                last_user = content
            elif isinstance(content, list):
                # 多模态消息 → complex
                return "complex"
            break

    if len(last_user) > _SIMPLE_MAX_USER_LEN:
        return "complex"

    if len(non_sys) <= _SIMPLE_MAX_MSGS and len(last_user) <= _SIMPLE_MAX_USER_LEN:
        return "simple"

    return "complex"


# 进程级单例
proxy_stats = ProxyStats()


# ---------------------------------------------------------------------------
# 本体一致性校验（启动时）
# ---------------------------------------------------------------------------

def _check_ontology_consistency():
    """启动时校验 tool_ontology.yaml 与硬编码规则的一致性。

    渐进迁移安全网：如果 YAML 和 Python 代码不一致，记录警告。
    不阻塞启动，仅日志提醒。
    """
    try:
        # ontology 是独立子项目，导入路径指向 ontology/engine.py
        import importlib.util
        _onto_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ontology", "engine.py")
        if os.path.exists(_onto_path):
            spec = importlib.util.spec_from_file_location("ontology_engine", _onto_path)
            _onto_mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(_onto_mod)
            onto = _onto_mod.ToolOntology()
            issues = onto.check_consistency(ALLOWED_TOOLS, CLEAN_SCHEMAS, TOOL_PARAMS)
            if issues:
                import logging
                logger = logging.getLogger("proxy_filters")
                for issue in issues:
                    logger.warning(f"Ontology consistency: {issue}")
    except Exception:
        pass  # ontology 子项目不存在或格式错误时静默跳过


_check_ontology_consistency()
