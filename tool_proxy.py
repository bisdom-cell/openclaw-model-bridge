#!/usr/bin/env python3
"""
tool_proxy.py — V27 HTTP 层
策略逻辑已提取到 proxy_filters.py，本文件只负责 HTTP 收发和日志。
V28: + token/error 监控（proxy_stats）
"""
import http.server, socketserver, json, sys, subprocess, os, threading, uuid, time
from datetime import datetime
from urllib.request import Request, urlopen

from proxy_filters import (
    ALLOWED_TOOLS, ALLOWED_PREFIXES,
    is_allowed, filter_tools, truncate_messages,
    fix_tool_args, build_sse_response, should_strip_tools,
    proxy_stats,
)

BACKEND = "http://127.0.0.1:5001"
PORT = 5002

def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[proxy] {ts} {msg}", flush=True)


def _send_alert(msg):
    """后台发送 WhatsApp 告警（不阻塞请求处理）。"""
    try:
        openclaw = os.environ.get("OPENCLAW", "/opt/homebrew/bin/openclaw")
        phone = os.environ.get("OPENCLAW_PHONE", "+85200000000")
        subprocess.Popen(
            [openclaw, "message", "send", "--target", phone, "--message", msg, "--json"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except OSError:
        log(f"WARN: Failed to send alert: {msg[:80]}")


class ProxyHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        log(fmt % args)

    def do_GET(self):
        # /stats 端点：返回 proxy 监控数据
        if self.path == "/stats":
            stats = json.dumps(proxy_stats.get_stats_dict(), indent=2, ensure_ascii=False).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(stats)))
            self.end_headers()
            self.wfile.write(stats)
            return

        # /health 端点：检查 proxy 自身 + adapter 连通性
        if self.path == "/health":
            adapter_ok = False
            try:
                with urlopen(f"{BACKEND}/health", timeout=5) as resp:
                    adapter_ok = resp.status == 200
            except Exception:
                pass
            status = {"ok": adapter_ok, "proxy": True, "adapter": adapter_ok}
            code = 200 if adapter_ok else 503
            body = json.dumps(status).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

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
        rid = uuid.uuid4().hex[:8]
        t0 = time.monotonic()
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
                    log(f"[{rid}] WARN: Truncated {dropped} old messages ({len(msgs)} -> {len(truncated)} msgs)")

                # [NO_TOOLS] 标记：强制清空工具（纯推理模式）
                if should_strip_tools(body.get("messages", [])):
                    if "tools" in body:
                        log(f"[{rid}] [NO_TOOLS] Stripping all {len(body['tools'])} tools (pure inference mode)")
                        del body["tools"]
                    if "tool_choice" in body:
                        del body["tool_choice"]
                # Filter tools
                elif "tools" in body:
                    orig = len(body["tools"])
                    body["tools"], all_names, kept_names = filter_tools(body["tools"])
                    log(f"[{rid}] ALL tools ({orig}): {all_names}")
                    log(f"[{rid}] Kept tools ({len(body['tools'])}): {kept_names}")
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
        req.add_header("X-Request-ID", rid)
        try:
            with urlopen(req, timeout=300) as resp:
                resp_body = resp.read()
                elapsed = int((time.monotonic() - t0) * 1000)
                log(f"[{rid}] Backend: {resp.status} {len(resp_body)}b {elapsed}ms stream={was_streaming}")

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
                                    log(f"[{rid}] CALL: {fn_name} ({len(fn_args)} bytes)")
                            elif m.get("content"):
                                log(f"[{rid}] TEXT: {len(str(m['content']))} chars")

                        # Token 监控：记录 usage
                        usage = rj.get("usage", {})
                        if usage:
                            pt = usage.get("prompt_tokens", 0)
                            tt = usage.get("total_tokens", 0)
                            log(f"[{rid}] TOKENS: prompt={pt:,} total={tt:,} ({pt*100//260000}% of 260K)")
                            proxy_stats.record_success(usage)
                        else:
                            proxy_stats.record_success({})

                        # 发送待处理告警
                        for alert in proxy_stats.pop_alerts():
                            log(f"ALERT: {alert}")
                            _send_alert(alert)

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
                        log(f"[{rid}] Parse error: {e}")

                self.send_response(resp.status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(resp_body)))
                self.end_headers()
                self.wfile.write(resp_body)
        except Exception as e:
            elapsed = int((time.monotonic() - t0) * 1000)
            log(f"[{rid}] Backend error ({elapsed}ms): {e}")
            # 记录错误到监控
            error_code = 502
            error_str = str(e)
            if "403" in error_str:
                error_code = 403
            proxy_stats.record_error(error_code, error_str)
            for alert in proxy_stats.pop_alerts():
                log(f"ALERT: {alert}")
                _send_alert(alert)

            err = json.dumps({"error": error_str}).encode()
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(err)))
            self.end_headers()
            self.wfile.write(err)


log(f"Starting on :{PORT} -> {BACKEND}")
log(f"Allowed: {ALLOWED_TOOLS} + prefix: {ALLOWED_PREFIXES}")
sys.stdout.flush()
class ThreadedServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    daemon_threads = True
    allow_reuse_address = True

with ThreadedServer(("", PORT), ProxyHandler) as httpd:
    httpd.serve_forever()
