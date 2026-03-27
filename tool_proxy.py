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

            # LLM 可能用 "clean" 代替 "execute"
            if action in ("clean", "cleaning"):
                action = "execute"

            # LLM 可能把操作信息放在 config 参数里
            config = args.get("config", {})
            if isinstance(config, str):
                try:
                    config = json.loads(config)
                except json.JSONDecodeError:
                    config = {}

            # 如果没有 ops 参数，从 config 推断
            if action == "execute" and not args.get("ops"):
                inferred_ops = []
                if config.get("handle_duplicates"):
                    inferred_ops.append("dedup")
                if config.get("date_columns") or config.get("standard_date_format"):
                    inferred_ops.append("fix_dates")
                if config.get("standardize_case") or config.get("text_columns"):
                    inferred_ops.append("fix_case")
                if config.get("missing_value_strategy"):
                    inferred_ops.append("fill_missing")
                # 默认先 trim
                if inferred_ops:
                    inferred_ops = ["trim"] + inferred_ops
                else:
                    inferred_ops = ["trim", "dedup", "fix_dates"]
                args["ops"] = ",".join(inferred_ops)

                # 从 config 提取 fix_case_cols
                if config.get("text_columns") and not args.get("fix_case_cols"):
                    cols = config["text_columns"]
                    if isinstance(cols, list):
                        args["fix_case_cols"] = ",".join(cols)

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
        """检查 LLM 响应中是否有自定义工具调用，如有则本地执行。
        直接将结果格式化为文本返回（跳过 followup LLM 调用，更可靠）。
        返回修改后的响应 JSON，或 None（无自定义工具调用时）。
        """
        for choice in rj.get("choices", []):
            msg = choice.get("message", {})
            tool_calls = msg.get("tool_calls", [])
            if not tool_calls:
                # Qwen3 可能把 tool_call 嵌在文本中作为 XML
                content = msg.get("content", "")
                if isinstance(content, str) and "<tool_call>" in content:
                    tool_calls = self._extract_tool_calls_from_text(content)
                    if tool_calls:
                        msg["tool_calls"] = tool_calls
                if not tool_calls:
                    continue

            # 分离自定义工具和 Gateway 工具
            custom_calls = [tc for tc in tool_calls
                           if tc.get("function", {}).get("name") in CUSTOM_TOOL_NAMES]

            if not custom_calls:
                return None  # 没有自定义工具，正常流转给 Gateway

            # 执行自定义工具
            results_text = []
            for tc in custom_calls:
                fn_name = tc["function"]["name"]
                fn_args = tc["function"].get("arguments", "{}")

                log(f"[{rid}] CUSTOM_TOOL: {fn_name} args={fn_args[:200]}")
                result = self._execute_custom_tool(fn_name, fn_args)
                log(f"[{rid}] CUSTOM_TOOL result: {len(result)} chars")

                # 格式化结果为可读文本
                results_text.append(self._format_tool_result(fn_name, fn_args, result))

            # 直接返回格式化结果（不做 followup LLM 调用）
            formatted = "\n\n".join(results_text)
            choice["message"] = {
                "role": "assistant",
                "content": formatted,
            }
            choice["finish_reason"] = "stop"
            log(f"[{rid}] CUSTOM_TOOL response: {len(formatted)} chars")
            return rj

        return None

    def _extract_tool_calls_from_text(self, content):
        """从文本中提取 <tool_call> XML 格式的工具调用"""
        import re
        tool_calls = []
        pattern = r'<tool_call>\s*(\{.*?\})\s*</tool_call>'
        for match in re.finditer(pattern, content, re.DOTALL):
            try:
                call_data = json.loads(match.group(1))
                tool_calls.append({
                    "id": f"call_{uuid.uuid4().hex[:8]}",
                    "type": "function",
                    "function": {
                        "name": call_data.get("name", ""),
                        "arguments": json.dumps(call_data.get("arguments", {})),
                    }
                })
            except json.JSONDecodeError:
                continue
        return tool_calls

    @staticmethod
    def _format_tool_result(fn_name, fn_args_str, result):
        """将工具执行结果格式化为用户可读的文本"""
        try:
            args = json.loads(fn_args_str) if isinstance(fn_args_str, str) else fn_args_str
            action = args.get("action", "")
        except (json.JSONDecodeError, AttributeError):
            action = ""

        try:
            data = json.loads(result)
        except (json.JSONDecodeError, TypeError):
            return f"工具执行结果:\n{result[:3000]}"

        if "error" in data:
            return f"❌ 错误: {data['error']}"

        if action == "profile":
            # 格式化 profile 结果
            lines = [f"📊 数据质量报告: {data.get('file', '?')}"]
            lines.append(f"行数: {data.get('rows', '?')} | 列数: {data.get('columns', '?')} | 质量评分: {data.get('quality_score', '?')}/100")
            lines.append("")

            issues = data.get("issues", [])
            if issues:
                lines.append("发现的问题:")
                for issue in issues:
                    sev = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(issue.get("severity", ""), "⚪")
                    col = issue.get("column", "")
                    lines.append(f"  {sev} [{col}] {issue.get('type', '?')}: {issue.get('detail', '')}")
            else:
                lines.append("✅ 未发现问题")

            lines.append("")
            lines.append("可用清洗操作: trim(去空格) dedup(去重) fix_dates(统一日期) fix_case(统一大小写) fill_missing(标记缺失) remove_test(去测试数据)")
            lines.append("请告诉我要执行哪些操作。")
            return "\n".join(lines)

        if action in ("execute", "clean"):
            lines = [f"✅ 数据清洗完成: {data.get('input', '?')}"]
            lines.append(f"行数变化: {data.get('original_rows', '?')} → {data.get('final_rows', '?')}")
            lines.append("")
            for step in data.get("steps", []):
                op = step.get("operation", "?")
                detail = ""
                if "rows_removed" in step:
                    detail = f"（删除 {step['rows_removed']} 行）"
                elif "cells_trimmed" in step:
                    detail = f"（修改 {step['cells_trimmed']} 个单元格）"
                elif "dates_fixed" in step:
                    detail = f"（修正 {step['dates_fixed']} 个日期）"
                elif "cells_changed" in step:
                    detail = f"（修改 {step['cells_changed']} 个单元格）"
                elif "cells_marked" in step:
                    detail = f"（标记 {step['cells_marked']} 个单元格）"
                lines.append(f"  ✓ {op} {detail}")
            lines.append("")
            lines.append(f"清洗后文件: {data.get('output', '?')}")
            return "\n".join(lines)

        if action == "list_ops":
            lines = ["可用的清洗操作:"]
            for op in data.get("operations", []):
                lines.append(f"  • {op['name']}: {op['description']} (风险: {op['risk']})")
            return "\n".join(lines)

        # 默认: 返回 JSON 摘要
        return json.dumps(data, ensure_ascii=False, indent=2)[:3000]

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
