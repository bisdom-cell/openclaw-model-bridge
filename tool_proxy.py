#!/usr/bin/env python3
"""
tool_proxy.py — V27 HTTP 层
策略逻辑已提取到 proxy_filters.py，本文件只负责 HTTP 收发和日志。
"""
import http.server, socketserver, json, sys
from urllib.request import Request, urlopen

from proxy_filters import (
    ALLOWED_TOOLS, ALLOWED_PREFIXES,
    is_allowed, filter_tools, truncate_messages,
    fix_tool_args, build_sse_response,
)

BACKEND = "http://127.0.0.1:5001"
PORT = 5002

def log(msg):
    print(f"[proxy] {msg}", flush=True)


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

                # Truncate old messages
                msgs = body.get("messages", [])
                truncated, dropped = truncate_messages(msgs)
                if dropped:
                    body["messages"] = truncated
                    log(f"WARN: Truncated {dropped} old messages ({len(msgs)} -> {len(truncated)} msgs)")

                # Filter tools
                if "tools" in body:
                    orig = len(body["tools"])
                    body["tools"], all_names, kept_names = filter_tools(body["tools"])
                    log(f"ALL tools ({orig}): {all_names}")
                    log(f"Kept tools ({len(body['tools'])}): {kept_names}")
                    if not body["tools"]:
                        del body["tools"]
                        if "tool_choice" in body:
                            del body["tool_choice"]

                raw = json.dumps(body).encode()
            except (json.JSONDecodeError, ValueError, KeyError) as e:
                log(f"Request preprocessing error: {e}")

        url = f"{BACKEND}{self.path}"
        req = Request(url, data=raw, method="POST")
        req.add_header("Content-Type", "application/json")
        try:
            with urlopen(req, timeout=300) as resp:
                resp_body = resp.read()
                log(f"Backend: {resp.status} {len(resp_body)}b stream={was_streaming}")

                if "/chat/completions" in self.path and resp_body:
                    try:
                        rj = json.loads(resp_body)
                        fix_tool_args(rj)

                        # Log model decision
                        for c in rj.get("choices", []):
                            m = c.get("message", {})
                            if m.get("tool_calls"):
                                for tc in m["tool_calls"]:
                                    fn_name = tc.get('function', {}).get('name', '?')
                                    fn_args = tc.get('function', {}).get('arguments', '')
                                    log(f"CALL: {fn_name} ({len(fn_args)} bytes)")
                            elif m.get("content"):
                                log(f"TEXT: {len(str(m['content']))} chars")

                        if was_streaming:
                            sse_body = build_sse_response(rj)
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
