#!/usr/bin/env python3
import http.server, socketserver, json, ssl, sys, os
from urllib.request import Request, urlopen

TARGET_BASE = "https://hkagentx.hkopenlab.com/v1"
API_KEY = os.environ.get("REMOTE_API_KEY", "sk-REPLACE-ME")
REAL_MODEL_ID = "Qwen3-235B-A22B-Instruct-2507-W8A8"
PORT = 5001
ctx = ssl.create_default_context()

ALLOWED_PARAMS = {
    "model", "messages", "max_tokens", "temperature", "top_p",
    "stream", "stop", "tools", "tool_choice", "n",
    "presence_penalty", "frequency_penalty", "seed"
}

def log(msg):
    print(f"[adapter] {msg}", flush=True)

class ProxyHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        log(fmt % args)

    def do_GET(self):
        path = self.path.replace("/v1", "", 1) if self.path.startswith("/v1") else self.path
        url = f"{TARGET_BASE}{path}"
        log(f"GET {url}")
        req = Request(url)
        req.add_header("Authorization", f"Bearer {API_KEY}")
        req.add_header("User-Agent", "curl/8.0")
        try:
            with urlopen(req, timeout=30, context=ctx) as resp:
                body = resp.read()
                self.send_response(resp.status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        except Exception as e:
            log(f"GET error: {e}")
            self.send_error(502, str(e))

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        path = self.path.replace("/v1", "", 1) if self.path.startswith("/v1") else self.path
        url = f"{TARGET_BASE}{path}"
        log(f"POST {url} ({length} bytes)")

        if "/chat/completions" in self.path:
            try:
                body = json.loads(raw)
            except (json.JSONDecodeError, ValueError) as e:
                log(f"Bad JSON: {e}")
                self.send_error(400, "Bad JSON")
                return

            log(f"INCOMING KEYS: {list(body.keys())}")

            # Clean messages - remove unsupported content types
            msgs = body.get("messages", [])
            clean_msgs = []
            for m in msgs:
                role = m.get("role", "")
                content = m.get("content", "")
                # If content is a list (multimodal), extract text only
                if isinstance(content, list):
                    text_parts = []
                    for part in content:
                        if isinstance(part, dict) and part.get("type") == "text":
                            text_parts.append(part.get("text", ""))
                        elif isinstance(part, str):
                            text_parts.append(part)
                    content = "\n".join(text_parts) if text_parts else ""

                clean_msg = {"role": role, "content": content}

                # Preserve tool_calls for assistant messages
                if "tool_calls" in m:
                    clean_msg["tool_calls"] = m["tool_calls"]
                # Preserve tool_call_id for tool messages
                if role == "tool" and "tool_call_id" in m:
                    clean_msg["tool_call_id"] = m["tool_call_id"]
                if role == "tool" and "name" in m:
                    clean_msg["name"] = m["name"]

                clean_msgs.append(clean_msg)

            # Build clean request
            clean = {"model": REAL_MODEL_ID, "messages": clean_msgs}
            if "max_tokens" in body:
                clean["max_tokens"] = body["max_tokens"]
            else:
                clean["max_tokens"] = 4096
            if "temperature" in body:
                clean["temperature"] = body["temperature"]
            if "tools" in body:
                clean["tools"] = body["tools"]
            if "tool_choice" in body:
                clean["tool_choice"] = body["tool_choice"]
            if "stream" in body:
                clean["stream"] = body["stream"]

            log(f"CLEAN KEYS: {list(clean.keys())}")
            log(f"MSG COUNT: {len(clean_msgs)}, ROLES: {[m['role'] for m in clean_msgs]}")
            data = json.dumps(clean).encode()
            log(f"FORWARDING: {len(data)} bytes")
        else:
            data = raw

        req = Request(url, data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("Authorization", f"Bearer {API_KEY}")
        req.add_header("User-Agent", "curl/8.0")
        try:
            with urlopen(req, timeout=180, context=ctx) as resp:
                resp_body = resp.read()
                log(f"RESPONSE: {resp.status} ({len(resp_body)} bytes)")
                self.send_response(resp.status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(resp_body)))
                self.end_headers()
                self.wfile.write(resp_body)
        except Exception as e:
            log(f"FORWARD ERROR: {e}")
            err = json.dumps({"error": str(e)}).encode()
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(err)))
            self.end_headers()
            self.wfile.write(err)

log(f"Starting on :{PORT} -> {TARGET_BASE}")
sys.stdout.flush()
with socketserver.TCPServer(("", PORT), ProxyHandler) as httpd:
    httpd.serve_forever()
