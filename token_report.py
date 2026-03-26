#!/usr/bin/env python3
"""
token_report.py — Token 用量日报
解析 tool_proxy.log，生成每日 token 消耗报告 + 追加历史趋势文件。

指标：
  - 当日 prompt/completion/total tokens 总消耗
  - 逐小时 token 分布（发现高峰时段）
  - 单请求 token 分布（<10K / 10-50K / 50-100K / 100K+ 四档）
  - 上下文压力事件（>75% / >90% 次数）
  - 与前一天对比（日环比）
  - 追加到 ~/token_history.json 供趋势分析
"""
import os, re, json, sys, subprocess, statistics
from datetime import datetime, timedelta
from collections import defaultdict

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
PROXY_LOG = os.path.expanduser("~/tool_proxy.log")
HISTORY_FILE = os.path.expanduser("~/token_history.json")
REPORT_JSON = os.path.expanduser("~/token_report.json")
PHONE = os.environ.get("OPENCLAW_PHONE", "+85200000000")
OPENCLAW = os.environ.get("OPENCLAW", "/opt/homebrew/bin/openclaw")
CONTEXT_LIMIT = 260000

# ---------------------------------------------------------------------------
# Log patterns
# ---------------------------------------------------------------------------
# [proxy] 2026-03-24 10:05:30 [abc12345] TOKENS: prompt=12,345 total=13,456 (5% of 260K)
RE_TOKENS = re.compile(
    r"\[proxy\] (\d{4}-\d{2}-\d{2}) (\d{2}):\d{2}:\d{2} \[(\w+)\] "
    r"TOKENS: prompt=([\d,]+) total=([\d,]+)"
)
# [proxy] 2026-03-24 10:05:30 [abc12345] Backend: 200 1500b 350ms
RE_BACKEND = re.compile(
    r"\[proxy\] (\d{4}-\d{2}-\d{2}) \S+ \[(\w+)\] Backend: 200"
)


def parse_int(s):
    return int(s.replace(",", ""))


def parse_tokens(target_date):
    """Parse token usage from proxy log for a given date."""
    hourly_prompt = defaultdict(int)     # hour -> total prompt tokens
    hourly_total = defaultdict(int)      # hour -> total tokens
    hourly_requests = defaultdict(int)   # hour -> request count
    prompt_list = []                     # all prompt_tokens values
    total_list = []                      # all total_tokens values
    request_count = 0

    if not os.path.exists(PROXY_LOG):
        return None

    # Count total successful requests for the date
    success_rids = set()
    with open(PROXY_LOG, "r", errors="replace") as f:
        for line in f:
            m = RE_BACKEND.search(line)
            if m and m.group(1) == target_date:
                success_rids.add(m.group(2))

    with open(PROXY_LOG, "r", errors="replace") as f:
        for line in f:
            m = RE_TOKENS.search(line)
            if not m or m.group(1) != target_date:
                continue

            hour = int(m.group(2))
            pt = parse_int(m.group(4))
            tt = parse_int(m.group(5))
            completion = tt - pt

            hourly_prompt[hour] += pt
            hourly_total[hour] += tt
            hourly_requests[hour] += 1
            prompt_list.append(pt)
            total_list.append(tt)
            request_count += 1

    if request_count == 0:
        return None

    # Token distribution buckets
    buckets = {"<10K": 0, "10-50K": 0, "50-100K": 0, "100K+": 0}
    for pt in prompt_list:
        if pt < 10000:
            buckets["<10K"] += 1
        elif pt < 50000:
            buckets["10-50K"] += 1
        elif pt < 100000:
            buckets["50-100K"] += 1
        else:
            buckets["100K+"] += 1

    # Context pressure
    warn_count = sum(1 for pt in prompt_list if pt >= int(CONTEXT_LIMIT * 0.75))
    critical_count = sum(1 for pt in prompt_list if pt >= int(CONTEXT_LIMIT * 0.90))

    # Peak hour
    peak_hour = max(hourly_total, key=hourly_total.get) if hourly_total else 0
    total_prompt = sum(prompt_list)
    total_all = sum(total_list)
    total_completion = total_all - total_prompt

    return {
        "date": target_date,
        "request_count": request_count,
        "total_prompt_tokens": total_prompt,
        "total_completion_tokens": total_completion,
        "total_tokens": total_all,
        "avg_prompt": int(statistics.mean(prompt_list)),
        "max_prompt": max(prompt_list),
        "median_prompt": int(statistics.median(prompt_list)),
        "distribution": buckets,
        "context_pressure": {"warn_75pct": warn_count, "critical_90pct": critical_count},
        "peak_hour": peak_hour,
        "peak_hour_tokens": hourly_total.get(peak_hour, 0),
        "hourly": {str(h): {"prompt": hourly_prompt[h], "total": hourly_total[h], "requests": hourly_requests[h]}
                   for h in sorted(hourly_prompt.keys())},
    }


def load_history():
    """Load historical daily token data."""
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {"days": []}


def save_history(history):
    """Persist history file."""
    try:
        with open(HISTORY_FILE, "w") as f:
            json.dump(history, f, indent=2, ensure_ascii=False)
    except OSError as e:
        print(f"[token_report] WARN: Failed to write history: {e}")


def append_history(data):
    """Append today's summary to history, dedup by date."""
    history = load_history()
    days = history["days"]

    # Remove existing entry for same date (idempotent re-run)
    days = [d for d in days if d.get("date") != data["date"]]

    days.append({
        "date": data["date"],
        "requests": data["request_count"],
        "total_tokens": data["total_tokens"],
        "total_prompt": data["total_prompt_tokens"],
        "total_completion": data["total_completion_tokens"],
        "avg_prompt": data["avg_prompt"],
        "max_prompt": data["max_prompt"],
        "peak_hour": data["peak_hour"],
    })

    # Keep last 90 days
    days.sort(key=lambda d: d["date"])
    if len(days) > 90:
        days = days[-90:]

    history["days"] = days
    history["updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    save_history(history)
    return days


def format_report(data, prev_day):
    """Format token report for WhatsApp."""
    lines = [f"🔢 Token 用量日报 {data['date']}"]
    lines.append("")

    # Totals
    tp = data["total_prompt_tokens"]
    tc = data["total_completion_tokens"]
    tt = data["total_tokens"]
    lines.append(f"📊 总消耗：{tt:,} tokens（{data['request_count']} 次请求）")
    lines.append(f"   Prompt: {tp:,}  Completion: {tc:,}")

    # Day-over-day comparison
    if prev_day:
        prev_tt = prev_day.get("total_tokens", 0)
        if prev_tt > 0:
            change_pct = round((tt - prev_tt) / prev_tt * 100, 1)
            arrow = "📈" if change_pct > 0 else "📉" if change_pct < 0 else "➡️"
            lines.append(f"   {arrow} 日环比：{change_pct:+.1f}%（昨日 {prev_tt:,}）")

    # Per-request stats
    lines.append("")
    lines.append(f"📋 单请求统计：")
    lines.append(f"   avg={data['avg_prompt']:,}  median={data['median_prompt']:,}  max={data['max_prompt']:,}")

    # Distribution
    dist = data["distribution"]
    lines.append(f"   分布：{dist['<10K']}×<10K  {dist['10-50K']}×10-50K  {dist['50-100K']}×50-100K  {dist['100K+']}×100K+")

    # Peak hour
    lines.append("")
    lines.append(f"⏰ 高峰时段：{data['peak_hour']:02d}:00（{data['peak_hour_tokens']:,} tokens）")

    # Context pressure
    cp = data["context_pressure"]
    if cp["warn_75pct"] or cp["critical_90pct"]:
        lines.append("")
        lines.append(f"⚠️ 上下文压力：")
        if cp["warn_75pct"]:
            lines.append(f"   >75% (195K): {cp['warn_75pct']} 次")
        if cp["critical_90pct"]:
            lines.append(f"   >90% (234K): {cp['critical_90pct']} 次")

    # Hourly breakdown (compact)
    hourly = data["hourly"]
    if len(hourly) > 1:
        lines.append("")
        lines.append(f"📊 逐小时（请求数/tokens）：")
        for h in sorted(hourly.keys(), key=int):
            hd = hourly[h]
            bar = "█" * max(1, hd["total"] // (data["total_tokens"] // len(hourly) or 1))
            lines.append(f"   {int(h):02d}h: {hd['requests']}次 {hd['total']:,}t {bar}")

    lines.append("")
    lines.append("✅ Token 日报完毕")
    return "\n".join(lines)


def write_json(data):
    """Write machine-readable report."""
    output = dict(data)
    output["generated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(REPORT_JSON, "w") as f:
            json.dump(output, f, indent=2, ensure_ascii=False)
        print(f"[token_report] JSON written to {REPORT_JSON}")
    except OSError as e:
        print(f"[token_report] WARN: Failed to write JSON: {e}")


def send_whatsapp(report):
    """Push report to WhatsApp."""
    try:
        result = subprocess.run(
            [OPENCLAW, "message", "send", "--target", PHONE, "--message", report, "--json"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            print("[token_report] WhatsApp 推送成功")
        else:
            print(f"[token_report] ERROR: WhatsApp 推送失败 (exit {result.returncode})")
    except (OSError, subprocess.TimeoutExpired) as e:
        print(f"[token_report] ERROR: WhatsApp 推送异常: {e}")


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--today":
        target_date = datetime.now().strftime("%Y-%m-%d")
    elif len(sys.argv) > 1 and re.match(r"\d{4}-\d{2}-\d{2}", sys.argv[1]):
        target_date = sys.argv[1]
    else:
        target_date = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    print(f"[token_report] Analyzing tokens for {target_date}")

    data = parse_tokens(target_date)
    if data is None:
        print("[token_report] No token data found, skipping")
        return

    # Append to history and get previous day for comparison
    days = append_history(data)
    prev_day = None
    for i, d in enumerate(days):
        if d["date"] == target_date and i > 0:
            prev_day = days[i - 1]
            break

    report = format_report(data, prev_day)
    print(report)
    write_json(data)
    send_whatsapp(report)


if __name__ == "__main__":
    main()
