#!/usr/bin/env python3
"""
conv_quality.py — 对话质量监控
解析 tool_proxy.log + adapter.log，生成每日质量报告，推送 WhatsApp + 写 JSON。

指标：
  - 请求量 / 成功率
  - 响应时间（avg / P50 / P95 / max）
  - 工具调用分布（top 5）
  - Token 用量（avg / max / 上下文压力次数）
  - Fallback 触发次数
  - 错误类型分布
  - 消息截断次数
"""
import os, re, json, sys, subprocess, statistics
from datetime import datetime, timedelta
from collections import Counter

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
PROXY_LOG = os.path.expanduser("~/tool_proxy.log")
ADAPTER_LOG = os.path.expanduser("~/adapter.log")
REPORT_JSON = os.path.expanduser("~/conv_quality.json")
PHONE = os.environ.get("OPENCLAW_PHONE", "+85200000000")
OPENCLAW = os.environ.get("OPENCLAW", "/opt/homebrew/bin/openclaw")

# ---------------------------------------------------------------------------
# Log line patterns (tool_proxy.log)
# ---------------------------------------------------------------------------
# [proxy] 2026-03-24 10:00:00 [abc12345] Backend: 200 1234b 567ms stream=True
RE_BACKEND = re.compile(
    r"\[proxy\] (\d{4}-\d{2}-\d{2}) \S+ \[(\w+)\] Backend: (\d+) (\d+)b (\d+)ms"
)
# [proxy] ... [abc12345] Backend error (567ms): ...
RE_BACKEND_ERR = re.compile(
    r"\[proxy\] (\d{4}-\d{2}-\d{2}) \S+ \[(\w+)\] Backend error \((\d+)ms\): (.+)"
)
# [proxy] ... [abc12345] CALL: web_search (123 bytes)
RE_TOOL_CALL = re.compile(
    r"\[proxy\] (\d{4}-\d{2}-\d{2}) \S+ \[(\w+)\] CALL: (\S+)"
)
# [proxy] ... [abc12345] TEXT: 1234 chars
RE_TEXT_RESP = re.compile(
    r"\[proxy\] (\d{4}-\d{2}-\d{2}) \S+ \[(\w+)\] TEXT: (\d+) chars"
)
# [proxy] ... [abc12345] TOKENS: prompt=12,345 total=13,456 (5% of 260K)
RE_TOKENS = re.compile(
    r"\[proxy\] (\d{4}-\d{2}-\d{2}) \S+ \[(\w+)\] TOKENS: prompt=([\d,]+) total=([\d,]+)"
)
# [proxy] ... [abc12345] WARN: Truncated 3 old messages
RE_TRUNCATED = re.compile(
    r"\[proxy\] (\d{4}-\d{2}-\d{2}) \S+ \[(\w+)\] WARN: Truncated (\d+) old messages"
)

# Adapter log patterns
# [adapter:qwen] 2026-03-24 10:00:00 [abc12345] PRIMARY FAILED ...
RE_PRIMARY_FAIL = re.compile(
    r"\[adapter:\w+\] (\d{4}-\d{2}-\d{2}) \S+ \[(\w+)\] PRIMARY FAILED"
)
# [adapter:qwen] ... [abc12345] FALLBACK OK: ...
RE_FALLBACK_OK = re.compile(
    r"\[adapter:\w+\] (\d{4}-\d{2}-\d{2}) \S+ \[(\w+)\] FALLBACK OK"
)
RE_FALLBACK_FAIL = re.compile(
    r"\[adapter:\w+\] (\d{4}-\d{2}-\d{2}) \S+ \[(\w+)\] FALLBACK ALSO FAILED"
)


def parse_int(s):
    """Parse comma-formatted integer like '12,345'."""
    return int(s.replace(",", ""))


def parse_logs(target_date):
    """Parse proxy + adapter logs for a given date string (YYYY-MM-DD).
    Returns a dict of collected metrics.
    """
    date_str = target_date

    # Per-request tracking
    latencies = []        # ms
    error_latencies = []  # ms (failed requests)
    status_codes = Counter()
    tool_calls = Counter()
    prompt_tokens = []
    total_tokens = []
    truncation_count = 0
    text_responses = 0
    tool_responses = 0     # requests that include at least one tool call
    error_messages = []
    request_ids_seen = set()
    tool_call_rids = set()

    # --- Parse tool_proxy.log ---
    if os.path.exists(PROXY_LOG):
        with open(PROXY_LOG, "r", errors="replace") as f:
            for line in f:
                # Success response
                m = RE_BACKEND.search(line)
                if m and m.group(1) == date_str:
                    rid = m.group(2)
                    status = int(m.group(3))
                    elapsed = int(m.group(5))
                    request_ids_seen.add(rid)
                    status_codes[status] += 1
                    latencies.append(elapsed)
                    continue

                # Error response
                m = RE_BACKEND_ERR.search(line)
                if m and m.group(1) == date_str:
                    rid = m.group(2)
                    elapsed = int(m.group(3))
                    err_msg = m.group(4).strip()
                    request_ids_seen.add(rid)
                    # Classify error
                    if "403" in err_msg:
                        status_codes[403] += 1
                    else:
                        status_codes[502] += 1
                    error_latencies.append(elapsed)
                    error_messages.append(err_msg[:120])
                    continue

                # Tool call
                m = RE_TOOL_CALL.search(line)
                if m and m.group(1) == date_str:
                    rid = m.group(2)
                    tool_name = m.group(3)
                    tool_calls[tool_name] += 1
                    tool_call_rids.add(rid)
                    continue

                # Text response
                m = RE_TEXT_RESP.search(line)
                if m and m.group(1) == date_str:
                    text_responses += 1
                    continue

                # Tokens
                m = RE_TOKENS.search(line)
                if m and m.group(1) == date_str:
                    pt = parse_int(m.group(3))
                    tt = parse_int(m.group(4))
                    prompt_tokens.append(pt)
                    total_tokens.append(tt)
                    continue

                # Truncation
                m = RE_TRUNCATED.search(line)
                if m and m.group(1) == date_str:
                    truncation_count += 1
                    continue

    tool_responses = len(tool_call_rids)

    # --- Parse adapter.log ---
    fallback_triggered = 0
    fallback_success = 0
    fallback_failed = 0

    if os.path.exists(ADAPTER_LOG):
        with open(ADAPTER_LOG, "r", errors="replace") as f:
            for line in f:
                if RE_PRIMARY_FAIL.search(line) and date_str in line:
                    fallback_triggered += 1
                elif RE_FALLBACK_OK.search(line) and date_str in line:
                    fallback_success += 1
                elif RE_FALLBACK_FAIL.search(line) and date_str in line:
                    fallback_failed += 1

    # --- Compute metrics ---
    total_requests = len(request_ids_seen)
    success_count = status_codes.get(200, 0)
    error_count = total_requests - success_count

    # Error type breakdown
    error_types = {}
    for code, count in sorted(status_codes.items()):
        if code != 200:
            label = {403: "auth/context_overflow", 502: "backend_error"}.get(code, f"http_{code}")
            error_types[label] = count

    # Latency percentiles (success only)
    latency_stats = {}
    if latencies:
        latencies_sorted = sorted(latencies)
        latency_stats = {
            "avg": int(statistics.mean(latencies)),
            "p50": int(statistics.median(latencies)),
            "p95": latencies_sorted[int(len(latencies_sorted) * 0.95)] if len(latencies_sorted) >= 2 else latencies_sorted[-1],
            "max": max(latencies),
            "min": min(latencies),
        }

    # Token stats
    token_stats = {}
    context_pressure_count = 0  # times prompt_tokens > 75% of 260K
    if prompt_tokens:
        context_pressure_count = sum(1 for pt in prompt_tokens if pt >= 195000)
        token_stats = {
            "avg_prompt": int(statistics.mean(prompt_tokens)),
            "max_prompt": max(prompt_tokens),
            "avg_total": int(statistics.mean(total_tokens)) if total_tokens else 0,
            "max_total": max(total_tokens) if total_tokens else 0,
            "context_pressure_count": context_pressure_count,
        }

    # Top 5 tools
    top_tools = tool_calls.most_common(5)

    return {
        "date": date_str,
        "total_requests": total_requests,
        "success_count": success_count,
        "error_count": error_count,
        "success_rate": round(success_count / total_requests * 100, 1) if total_requests else 0,
        "latency": latency_stats,
        "error_types": error_types,
        "tool_calls_total": sum(tool_calls.values()),
        "tool_responses": tool_responses,
        "text_responses": text_responses,
        "top_tools": top_tools,
        "token_stats": token_stats,
        "truncation_count": truncation_count,
        "fallback": {
            "triggered": fallback_triggered,
            "success": fallback_success,
            "failed": fallback_failed,
        },
    }


def format_report(data):
    """Format metrics dict into a WhatsApp-friendly text report."""
    lines = [f"📊 对话质量日报 {data['date']}"]
    lines.append("")

    # Request summary
    total = data["total_requests"]
    if total == 0:
        lines.append("今日无请求记录。")
        return "\n".join(lines)

    lines.append(f"📈 请求概览：{total} 次请求，成功率 {data['success_rate']}%")
    lines.append(f"   成功 {data['success_count']} / 失败 {data['error_count']}")

    # Latency
    lat = data["latency"]
    if lat:
        lines.append("")
        lines.append(f"⏱ 响应时间：")
        lines.append(f"   avg={lat['avg']}ms  P50={lat['p50']}ms  P95={lat['p95']}ms  max={lat['max']}ms")

    # Token usage
    ts = data["token_stats"]
    if ts:
        lines.append("")
        lines.append(f"🔢 Token用量：")
        lines.append(f"   avg_prompt={ts['avg_prompt']:,}  max_prompt={ts['max_prompt']:,}")
        if ts["context_pressure_count"]:
            lines.append(f"   ⚠️ 上下文压力(>75%)：{ts['context_pressure_count']} 次")

    # Tool calls
    if data["top_tools"]:
        lines.append("")
        lines.append(f"🔧 工具调用：{data['tool_calls_total']} 次 / {data['tool_responses']} 个请求含工具")
        for name, count in data["top_tools"]:
            lines.append(f"   {name}: {count}")

    # Text vs tool
    lines.append("")
    lines.append(f"💬 纯文本回复：{data['text_responses']} 次")

    # Truncation
    if data["truncation_count"]:
        lines.append(f"✂️ 消息截断：{data['truncation_count']} 次")

    # Errors
    if data["error_types"]:
        lines.append("")
        lines.append(f"❌ 错误分布：")
        for etype, count in data["error_types"].items():
            lines.append(f"   {etype}: {count}")

    # Fallback
    fb = data["fallback"]
    if fb["triggered"]:
        lines.append("")
        lines.append(f"🔄 Fallback降级：触发 {fb['triggered']} 次")
        lines.append(f"   成功 {fb['success']} / 失败 {fb['failed']}")

    lines.append("")
    lines.append("✅ 日报完毕")
    return "\n".join(lines)


def write_json(data):
    """Write machine-readable JSON report."""
    # Convert top_tools from list of tuples to dict for JSON
    output = dict(data)
    output["top_tools"] = {name: count for name, count in data["top_tools"]}
    output["generated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(REPORT_JSON, "w") as f:
            json.dump(output, f, indent=2, ensure_ascii=False)
        print(f"[conv_quality] JSON written to {REPORT_JSON}")
    except OSError as e:
        print(f"[conv_quality] WARN: Failed to write JSON: {e}")


def send_notification(report):
    """Push report via notify.sh (dual-channel + retry)."""
    import tempfile
    try:
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
            f.write(report)
            tmp = f.name
        result = subprocess.run(
            ["bash", "-c", f'source ~/notify.sh && notify "$(cat "{tmp}")" --topic daily; rm -f "{tmp}"'],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode == 0:
            print("[conv_quality] 推送成功 (WhatsApp + Discord)")
        else:
            print(f"[conv_quality] ERROR: 推送失败 (exit {result.returncode}): {result.stderr[:200]}")
    except (OSError, subprocess.TimeoutExpired) as e:
        print(f"[conv_quality] ERROR: 推送异常: {e}")


def main():
    no_push = "--no-push" in sys.argv
    args = [a for a in sys.argv[1:] if a != "--no-push"]

    # Default: report on yesterday (cron runs at 08:15, reports on previous day)
    if args and args[0] == "--today":
        target_date = datetime.now().strftime("%Y-%m-%d")
    elif args and re.match(r"\d{4}-\d{2}-\d{2}", args[0]):
        target_date = args[0]
    else:
        target_date = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    print(f"[conv_quality] Analyzing logs for {target_date}")

    data = parse_logs(target_date)
    report = format_report(data)
    print(report)

    write_json(data)

    if no_push:
        return
    if data["total_requests"] > 0:
        send_notification(report)
    else:
        print("[conv_quality] No requests found, skipping push")


if __name__ == "__main__":
    main()
