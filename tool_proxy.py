#!/usr/bin/env python3
import http.server, socketserver, json, sys
from urllib.request import Request, urlopen

BACKEND = "http://127.0.0.1:5001"
PORT = 5002

# Expanded allowed tools
ALLOWED_TOOLS = {
    # Web
    "web_search", "web_fetch",
    # File operations
    "read", "write", "edit",
    # Command execution
    "exec",
    # Memory
    "memory_search", "memory_get",
    # Scheduling & messaging
    "cron", "message", "tts",
}

# Prefix match for browser tools
ALLOWED_PREFIXES = ["browser"]

# Simplified schemas for tools Qwen3 tends to mess up
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

# Known required params for response cleanup
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

def log(msg):
    print(f"[proxy] {msg}", flush=True)

def is_allowed(name):
    if name in ALLOWED_TOOLS:
        return True
    for prefix in ALLOWED_PREFIXES:
        if name.startswith(prefix):
            return True
    return False

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
                except:
                    args = {}
                
                # Fix browser profile: replace invalid profiles with "chrome"
                VALID_BROWSER_PROFILES = {"openclaw", "chrome"}
                if name.startswith("browser"):
                    if "profile" in args and args["profile"] not in VALID_BROWSER_PROFILES:
                        log(f"FIX: browser profile '{args['profile']}' -> 'chrome'")
                        args["profile"] = "openclaw"
                        fn["arguments"] = json.dumps(args)
                        modified = True
                    elif "target" in args and args["target"] not in VALID_BROWSER_PROFILES:
                        log(f"FIX: browser target '{args['target']}' -> 'chrome'")
                        args["target"] = "openclaw"
                        fn["arguments"] = json.dumps(args)
                        modified = True
                    # If no profile specified, inject default
                    if "profile" not in args and "target" not in args:
                        args["profile"] = "openclaw"
                        log(f"FIX: browser missing profile, set to 'openclaw'")
                        fn["arguments"] = json.dumps(args)
                        modified = True

                allowed = TOOL_PARAMS.get(name)
                if allowed:
                    # Handle common param name aliases
                    if name == "read" and "path" not in args:
                        for alt in ["file_path", "file", "filepath", "filename"]:
                            if alt in args:
                                args["path"] = args.pop(alt)
                                break
                    if name == "exec" and "command" not in args:
                        for alt in ["cmd", "shell", "bash", "script"]:
                            if alt in args:
                                args["command"] = args.pop(alt)
                                break
                    if name == "write" and "content" not in args:
                        for alt in ["text", "data", "body", "file_content"]:
                            if alt in args:
                                args["content"] = args.pop(alt)
                                break
                    if name == "web_search" and "query" not in args:
                        for alt in ["search_query", "q", "keyword", "search"]:
                            if alt in args:
                                args["query"] = args.pop(alt)
                                break
                    
                    clean = {k: v for k, v in args.items() if k in allowed}
                    if clean != args:
                        log(f"FIX: {name} {list(args.keys())} -> {list(clean.keys())}")
                        fn["arguments"] = json.dumps(clean)
                        modified = True
    return modified

class ProxyHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        log(fmt % args)

    def do_GET(self):
        url = f"{BACKEND}{self.path}"
        req = Request(url)
        try:
            with urlopen(req, timeout=30) as resp:
                body = resp.read()
                self.send_response(resp.status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        except Exception as e:
            self.send_error(502, str(e))

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        
        was_streaming = False
        if "/chat/completions" in self.path:
            try:
                body = json.loads(raw)
                was_streaming = body.get("stream", False)
                body["stream"] = False

                # Limit request size to prevent 500 errors
                MAX_BYTES = 200000  # 200KB safe limit
                msgs = body.get("messages", [])
                system = [m for m in msgs if m.get("role") == "system"]
                others = [m for m in msgs if m.get("role") != "system"]
                total = len(json.dumps(system))
                keep = []
                for m in reversed(others):
                    ms = len(json.dumps(m))
                    if total + ms > MAX_BYTES:
                        break
                    keep.insert(0, m)
                    total += ms
                if len(keep) < len(others):
                    body["messages"] = system + keep
                    log(f"Truncated: {len(msgs)} -> {len(body['messages'])} msgs, ~{total//1024}KB")

                if "tools" in body:
                    orig = len(body["tools"])
                    # Log all tool names on first request
                    all_names = [t.get("function", {}).get("name", "?") for t in body["tools"]]
                    log(f"ALL tools ({orig}): {all_names}")
                    
                    new_tools = []
                    for t in body["tools"]:
                        name = t.get("function", {}).get("name", "")
                        if is_allowed(name):
                            if name in CLEAN_SCHEMAS:
                                t["function"]["parameters"] = CLEAN_SCHEMAS[name]
                            new_tools.append(t)
                    body["tools"] = new_tools
                    kept = [t.get("function", {}).get("name") for t in new_tools]
                    log(f"Kept tools ({len(new_tools)}): {kept}")
                    if not body["tools"]:
                        del body["tools"]
                        if "tool_choice" in body:
                            del body["tool_choice"]

                raw = json.dumps(body).encode()
            except:
                pass

        url = f"{BACKEND}{self.path}"
        req = Request(url, data=raw, method="POST")
        req.add_header("Content-Type", "application/json")
        try:
            with urlopen(req, timeout=180) as resp:
                resp_body = resp.read()
                log(f"Backend: {resp.status} {len(resp_body)}b stream={was_streaming}")

                if "/chat/completions" in self.path and resp_body:
                    try:
                        rj = json.loads(resp_body)
                        fix_tool_args(rj)
                        
                        # Log what model decided
                        for c in rj.get("choices", []):
                            m = c.get("message", {})
                            if m.get("tool_calls"):
                                for tc in m["tool_calls"]:
                                    log(f"CALL: {tc.get('function',{}).get('name')} -> {tc.get('function',{}).get('arguments','')[:200]}")
                            elif m.get("content"):
                                log(f"TEXT: {str(m['content'])[:100]}")
                        
                        if was_streaming:
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
                            sse_body = "".join(chunks_out).encode()
                            self.send_response(200)
                            self.send_header("Content-Type", "text/event-stream")
                            self.send_header("Cache-Control", "no-cache")
                            self.send_header("Content-Length", str(len(sse_body)))
                            self.end_headers()
                            self.wfile.write(sse_body)
                            return
                        else:
                            resp_body = json.dumps(rj).encode()
                    except Exception as e:
                        log(f"Parse error: {e}")

                self.send_response(resp.status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(resp_body)))
                self.end_headers()
                self.wfile.write(resp_body)
        except Exception as e:
            log(f"Backend error: {e}")
            err = json.dumps({"error": str(e)}).encode()
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(err)))
            self.end_headers()
            self.wfile.write(err)

log(f"Starting on :{PORT} -> {BACKEND}")
log(f"Allowed: {ALLOWED_TOOLS} + prefix: {ALLOWED_PREFIXES}")
sys.stdout.flush()
with socketserver.TCPServer(("", PORT), ProxyHandler) as httpd:
    httpd.serve_forever()
