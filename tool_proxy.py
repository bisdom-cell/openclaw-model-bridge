#!/usr/bin/env python3
"""
tool_proxy.py — V27 HTTP 层
策略逻辑已提取到 proxy_filters.py，本文件只负责 HTTP 收发和日志。
V28: + token/error 监控（proxy_stats）
"""
import http.server, socketserver, json, sys, subprocess, os, threading, uuid, time
from datetime import datetime
from urllib.request import Request, urlopen
from urllib.parse import urlparse, parse_qs

from proxy_filters import (
    ALLOWED_TOOLS, ALLOWED_PREFIXES, CUSTOM_TOOL_NAMES,
    is_allowed, filter_tools, truncate_messages,
    fix_tool_args, build_sse_response, should_strip_tools,
    inject_media_into_messages,
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

        # /data_clean/* 端点：数据清洗服务
        if self.path.startswith("/data_clean/"):
            self._handle_data_clean()
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

    def _json_response(self, code, data):
        """返回 JSON 响应"""
        body = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _handle_data_clean(self):
        """处理 /data_clean/* 请求，内部调用 data_clean.py"""
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        action = parsed.path.replace("/data_clean/", "").strip("/")

        # 获取参数
        file_path = params.get("file", [None])[0]

        if action == "help":
            self._json_response(200, {
                "endpoints": {
                    "/data_clean/profile?file=<path>": "数据画像（质量报告）",
                    "/data_clean/execute?file=<path>&ops=trim,dedup,fix_dates": "执行清洗",
                    "/data_clean/execute?file=<path>&ops=fix_case&fix_case_cols=status,email": "执行清洗（带列参数）",
                    "/data_clean/validate?original=<path>&cleaned=<path>": "清洗前后验证",
                    "/data_clean/list-ops": "列出可用操作",
                    "/data_clean/report": "读取最近的清洗报告",
                },
                "supported_formats": "CSV, TSV, JSON, JSONL, Excel (.xlsx)",
            })
            return

        if action == "list-ops":
            result = subprocess.run(
                [sys.executable, self._data_clean_path(), "list-ops"],
                capture_output=True, text=True, timeout=10,
            )
            try:
                self._json_response(200, json.loads(result.stdout))
            except json.JSONDecodeError:
                self._json_response(500, {"error": result.stderr or result.stdout})
            return

        if action == "report":
            report_path = os.path.expanduser("~/.data_clean/workspace/report.md")
            if os.path.exists(report_path):
                with open(report_path, "r", encoding="utf-8") as f:
                    content = f.read()
                self._json_response(200, {"report": content})
            else:
                self._json_response(404, {"error": "尚无清洗报告"})
            return

        if not file_path:
            self._json_response(400, {"error": "缺少 file 参数"})
            return

        # 展开 ~ 路径
        file_path = os.path.expanduser(file_path)

        if not os.path.exists(file_path):
            self._json_response(404, {"error": f"文件不存在: {file_path}"})
            return

        if action == "profile":
            cmd = [sys.executable, self._data_clean_path(), "profile", file_path]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            try:
                self._json_response(200, json.loads(result.stdout))
            except json.JSONDecodeError:
                self._json_response(500, {"error": result.stderr or result.stdout or "profile 解析失败"})
            return

        if action == "execute":
            ops = params.get("ops", [""])[0].split(",")
            ops = [o.strip() for o in ops if o.strip()]
            if not ops:
                self._json_response(400, {"error": "缺少 ops 参数（如 ops=trim,dedup,fix_dates）"})
                return

            cmd = [sys.executable, self._data_clean_path(), "execute", file_path, "--ops"] + ops

            # 可选列参数
            fix_case_cols = params.get("fix_case_cols", [""])[0]
            if fix_case_cols:
                cmd += ["--fix-case-cols"] + fix_case_cols.split(",")
            fix_date_cols = params.get("fix_date_cols", [""])[0]
            if fix_date_cols:
                cmd += ["--fix-date-cols"] + fix_date_cols.split(",")

            log(f"[data_clean] execute: {' '.join(cmd)}")
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            try:
                self._json_response(200, json.loads(result.stdout))
            except json.JSONDecodeError:
                self._json_response(500, {"error": result.stderr or result.stdout or "execute 解析失败"})
            return

        if action == "validate":
            cleaned = params.get("cleaned", [None])[0]
            if not cleaned:
                self._json_response(400, {"error": "缺少 cleaned 参数"})
                return
            cleaned = os.path.expanduser(cleaned)
            cmd = [sys.executable, self._data_clean_path(), "validate", file_path, cleaned]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            try:
                self._json_response(200, json.loads(result.stdout))
            except json.JSONDecodeError:
                self._json_response(500, {"error": result.stderr or result.stdout or "validate 解析失败"})
            return

        self._json_response(404, {"error": f"未知操作: {action}，访问 /data_clean/help 查看可用端点"})

    @staticmethod
    def _data_clean_path():
        """返回 data_clean.py 路径"""
        # 优先 HOME 目录（auto_deploy 同步），回退到仓库目录
        home_path = os.path.expanduser("~/data_clean.py")
        if os.path.exists(home_path):
            return home_path
        return os.path.join(os.path.dirname(os.path.abspath(__file__)), "data_clean.py")

    def _execute_custom_tool(self, name, arguments):
        """执行 proxy 自定义工具，返回结果字符串"""
        if name == "data_clean":
            try:
                args = json.loads(arguments) if isinstance(arguments, str) else arguments
            except json.JSONDecodeError:
                return json.dumps({"error": f"参数解析失败: {arguments}"})

            action = args.get("action", "")
            file_path = args.get("file", "")

            if action == "list_ops":
                cmd = [sys.executable, self._data_clean_path(), "list-ops"]
            elif action == "profile":
                if not file_path:
                    return json.dumps({"error": "缺少 file 参数"})
                file_path = os.path.expanduser(file_path)
                if not os.path.exists(file_path):
                    return json.dumps({"error": f"文件不存在: {file_path}"})
                cmd = [sys.executable, self._data_clean_path(), "profile", file_path]
            elif action == "execute":
                if not file_path:
                    return json.dumps({"error": "缺少 file 参数"})
                file_path = os.path.expanduser(file_path)
                if not os.path.exists(file_path):
                    return json.dumps({"error": f"文件不存在: {file_path}"})
                ops = args.get("ops", "trim,dedup")
                cmd = [sys.executable, self._data_clean_path(), "execute", file_path,
                       "--ops"] + [o.strip() for o in ops.split(",") if o.strip()]
                fix_case_cols = args.get("fix_case_cols", "")
                if fix_case_cols:
                    cmd += ["--fix-case-cols"] + [c.strip() for c in fix_case_cols.split(",")]
            else:
                return json.dumps({"error": f"未知操作: {action}"})

            try:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
                return result.stdout or result.stderr or "（无输出）"
            except subprocess.TimeoutExpired:
                return json.dumps({"error": "执行超时（60秒）"})
            except Exception as e:
                return json.dumps({"error": str(e)})

        return json.dumps({"error": f"未知自定义工具: {name}"})

    def _handle_custom_tool_calls(self, rj, original_body, rid):
        """检查 LLM 响应中是否有自定义工具调用，如有则本地执行并重新查询 LLM。
        返回最终的 LLM 响应 JSON，或 None（无自定义工具调用时）。
        """
        for choice in rj.get("choices", []):
            msg = choice.get("message", {})
            tool_calls = msg.get("tool_calls", [])
            if not tool_calls:
                continue

            # 分离自定义工具和 Gateway 工具
            custom_calls = [tc for tc in tool_calls
                           if tc.get("function", {}).get("name") in CUSTOM_TOOL_NAMES]

            if not custom_calls:
                return None  # 没有自定义工具，正常流转给 Gateway

            # 执行自定义工具
            tool_results = []
            for tc in custom_calls:
                fn_name = tc["function"]["name"]
                fn_args = tc["function"].get("arguments", "{}")
                tc_id = tc.get("id", f"call_{uuid.uuid4().hex[:8]}")

                log(f"[{rid}] CUSTOM_TOOL: {fn_name} args={fn_args[:200]}")
                result = self._execute_custom_tool(fn_name, fn_args)
                log(f"[{rid}] CUSTOM_TOOL result: {len(result)} chars")

                tool_results.append({
                    "role": "tool",
                    "tool_call_id": tc_id,
                    "content": result,
                })

            # 构建跟进请求：原始消息 + 助手的 tool_call + 工具结果
            followup_msgs = list(original_body.get("messages", []))
            # 添加助手消息（包含 tool_calls）
            followup_msgs.append({
                "role": "assistant",
                "content": msg.get("content") or None,
                "tool_calls": custom_calls,
            })
            # 添加工具结果
            followup_msgs.extend(tool_results)

            # 重新查询 LLM（不带工具，让它生成最终文本回复）
            followup_body = {
                "model": original_body.get("model", "any"),
                "messages": followup_msgs,
                "stream": False,
            }

            log(f"[{rid}] CUSTOM_TOOL followup: sending {len(followup_msgs)} messages to LLM")

            try:
                followup_raw = json.dumps(followup_body).encode()
                followup_req = Request(
                    f"{BACKEND}/v1/chat/completions",
                    data=followup_raw, method="POST",
                )
                followup_req.add_header("Content-Type", "application/json")
                with urlopen(followup_req, timeout=300) as followup_resp:
                    followup_body = json.loads(followup_resp.read())
                    log(f"[{rid}] CUSTOM_TOOL followup: got response")
                    return followup_body
            except Exception as e:
                log(f"[{rid}] CUSTOM_TOOL followup error: {e}")
                # 回退：直接返回工具结果作为文本
                choice["message"] = {
                    "role": "assistant",
                    "content": f"数据清洗结果：\n{tool_results[0]['content'][:3000]}",
                }
                choice["message"].pop("tool_calls", None)
                return rj

        return None

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

                # 多模态媒体注入：检测 <media:image> 并注入 base64 图片
                msgs = body.get("messages", [])
                msgs, media_injected = inject_media_into_messages(msgs, log_fn=lambda m: log(f"[{rid}] {m}"))
                if media_injected:
                    body["messages"] = msgs

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

                        # 自定义工具拦截：LLM 调用 data_clean 等自定义工具时，
                        # proxy 本地执行并将结果喂回 LLM 获取最终回复
                        custom_result = self._handle_custom_tool_calls(rj, body, rid)
                        if custom_result is not None:
                            rj = custom_result

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


BIND_ADDR = os.environ.get("BIND_ADDR", "127.0.0.1")
log(f"Starting on {BIND_ADDR}:{PORT} -> {BACKEND}")
log(f"Allowed: {ALLOWED_TOOLS} + prefix: {ALLOWED_PREFIXES}")
sys.stdout.flush()
class ThreadedServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    daemon_threads = True
    allow_reuse_address = True

with ThreadedServer((BIND_ADDR, PORT), ProxyHandler) as httpd:
    httpd.serve_forever()
