#!/usr/bin/env python3
"""
kb_harvest_chat.py — 对话精华提炼器 (V37)

从 tool_proxy 捕获的每日对话日志中提取关键内容，写入 KB。
将用户与 PA 的高质量交互转化为持久化知识。

数据流：
  tool_proxy.py 捕获 → ~/.kb/conversations/YYYYMMDD.jsonl
  本脚本读取 → LLM 提炼关键点 → kb_write.sh 写入 KB notes

设计原则：
  - 离线处理：不在请求热路径上，cron 触发
  - 去重：已处理的日志文件标记跳过
  - 批量：一天的对话合并为一次 LLM 调用
  - 隐私：日志留在本地，仅提炼后的摘要进入 KB

用法：
  python3 kb_harvest_chat.py              # 处理昨天的对话
  python3 kb_harvest_chat.py --date 20260408  # 处理指定日期
  python3 kb_harvest_chat.py --dry-run    # 只展示不写入
  python3 kb_harvest_chat.py --days 3     # 处理最近3天
"""
import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

CHAT_LOG_DIR = os.path.expanduser("~/.kb/conversations")
PROCESSED_MARKER_DIR = os.path.expanduser("~/.kb/conversations/.processed")
KB_WRITE_SCRIPT = os.path.expanduser("~/kb_write.sh")
# Direct adapter call (bypass proxy, no tools needed)
LLM_URL = "http://127.0.0.1:5001/v1/chat/completions"


def load_conversations(date_str):
    """Load conversation turns from a daily JSONL file."""
    log_file = os.path.join(CHAT_LOG_DIR, f"{date_str}.jsonl")
    if not os.path.exists(log_file):
        return []
    turns = []
    with open(log_file, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                turns.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return turns


def is_processed(date_str):
    """Check if a date's conversations have already been processed."""
    marker = os.path.join(PROCESSED_MARKER_DIR, f"{date_str}.done")
    return os.path.exists(marker)


def mark_processed(date_str):
    """Mark a date's conversations as processed."""
    os.makedirs(PROCESSED_MARKER_DIR, exist_ok=True)
    marker = os.path.join(PROCESSED_MARKER_DIR, f"{date_str}.done")
    with open(marker, "w") as f:
        f.write(datetime.now().isoformat())


def format_conversations(turns):
    """Format conversation turns for LLM analysis."""
    parts = []
    for i, t in enumerate(turns, 1):
        ts = t.get("ts", "?")
        user = t.get("user", "")[:1500]
        assistant = t.get("assistant", "")[:1500]
        parts.append(f"--- 对话 {i} [{ts}] ---\n用户: {user}\nPA: {assistant}")
    return "\n\n".join(parts)


def extract_key_points(conversations_text, date_str):
    """Use LLM to extract key points from conversations."""
    prompt = f"""你是一个信息提炼器。以下是用户与AI助手(PA)在 {date_str} 的全部对话记录。

请从中提取**值得长期保存**的关键内容。

提取标准（只保留真正有价值的）：
1. 用户做出的**决策或判断**（"我决定..."、"先不做..."、"优先..."）
2. 用户表达的**偏好或需求**（"我希望..."、"以后..."、"不要..."）
3. 用户提供的**专业知识或洞察**（领域见解、经验总结）
4. 用户和PA共同达成的**结论**（分析结果、问题根因、方案选择）
5. 重要的**问题和发现**（bug、异常、趋势）

不要提取：
- 日常寒暄、确认消息
- PA的技术操作细节（代码执行、文件读写）
- 已在其他系统记录的信息（cron状态、系统健康等）

输出格式（每条一行，可以有0-10条）：
- [类型] 内容概要（保留关键细节和上下文）

类型：decision/preference/insight/conclusion/discovery

如果当天对话没有值得保存的内容，输出：无关键内容

---
{conversations_text}
---"""

    try:
        import urllib.request
        req_body = json.dumps({
            "model": "default",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 1500,
            "temperature": 0.3,
        }).encode()
        req = urllib.request.Request(
            LLM_URL,
            data=req_body,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            result = json.loads(resp.read())
            return result["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"[harvest] LLM call failed: {e}", file=sys.stderr)
        return None


def write_to_kb(key_points, date_str):
    """Write extracted key points to KB via kb_write.sh."""
    if not os.path.exists(KB_WRITE_SCRIPT):
        print(f"[harvest] kb_write.sh not found: {KB_WRITE_SCRIPT}", file=sys.stderr)
        return False

    content = f"[{date_str}对话精华] {key_points}"
    try:
        result = subprocess.run(
            ["bash", KB_WRITE_SCRIPT, content, "conversation", "chat_harvest"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            return True
        print(f"[harvest] kb_write.sh failed: {result.stderr}", file=sys.stderr)
        return False
    except Exception as e:
        print(f"[harvest] kb_write.sh error: {e}", file=sys.stderr)
        return False


def process_date(date_str, dry_run=False):
    """Process one day's conversations."""
    if is_processed(date_str):
        print(f"[harvest] {date_str}: already processed, skipping")
        return "skipped"

    turns = load_conversations(date_str)
    if not turns:
        print(f"[harvest] {date_str}: no conversations found")
        return "empty"

    print(f"[harvest] {date_str}: {len(turns)} conversation turns")

    # Format conversations for LLM
    conv_text = format_conversations(turns)
    total_chars = len(conv_text)
    # Truncate to ~60K chars if too long (leave room for prompt)
    if total_chars > 60000:
        conv_text = conv_text[:60000] + "\n\n[... 截断，更多对话未展示 ...]"
        print(f"[harvest] Truncated: {total_chars} -> 60000 chars")

    if dry_run:
        print(f"[harvest] DRY RUN: would process {len(turns)} turns ({total_chars} chars)")
        print(f"[harvest] Sample (first turn):")
        if turns:
            t = turns[0]
            print(f"  User: {t.get('user', '')[:100]}...")
            print(f"  PA: {t.get('assistant', '')[:100]}...")
        return "dry_run"

    # Extract key points via LLM
    print(f"[harvest] Extracting key points via LLM ({total_chars} chars)...")
    key_points = extract_key_points(conv_text, date_str)
    if not key_points:
        print(f"[harvest] {date_str}: LLM extraction failed")
        return "error"

    if "无关键内容" in key_points:
        print(f"[harvest] {date_str}: no key content found by LLM")
        mark_processed(date_str)
        return "no_content"

    print(f"[harvest] Extracted:\n{key_points}")

    # Write to KB
    if write_to_kb(key_points, date_str):
        mark_processed(date_str)
        print(f"[harvest] {date_str}: written to KB")
        return "ok"
    return "error"


def main():
    parser = argparse.ArgumentParser(description="对话精华提炼器")
    parser.add_argument("--date", help="处理指定日期 (YYYYMMDD)")
    parser.add_argument("--days", type=int, default=1,
                        help="处理最近N天（默认1=昨天）")
    parser.add_argument("--dry-run", action="store_true",
                        help="只展示不处理")
    parser.add_argument("--stats", action="store_true",
                        help="展示对话日志统计")
    args = parser.parse_args()

    if args.stats:
        if not os.path.isdir(CHAT_LOG_DIR):
            print("No conversation logs found.")
            return
        total_turns = 0
        for f in sorted(Path(CHAT_LOG_DIR).glob("*.jsonl")):
            turns = load_conversations(f.stem)
            processed = "done" if is_processed(f.stem) else "pending"
            total_chars = sum(len(t.get("user", "")) + len(t.get("assistant", ""))
                              for t in turns)
            print(f"  {f.stem}: {len(turns)} turns, {total_chars//1000}KB [{processed}]")
            total_turns += len(turns)
        print(f"\nTotal: {total_turns} turns across {len(list(Path(CHAT_LOG_DIR).glob('*.jsonl')))} days")
        return

    if args.date:
        dates = [args.date]
    else:
        # Process last N days (default: yesterday)
        dates = []
        for i in range(1, args.days + 1):
            d = datetime.now() - timedelta(days=i)
            dates.append(d.strftime("%Y%m%d"))

    results = {}
    for date_str in dates:
        results[date_str] = process_date(date_str, dry_run=args.dry_run)

    # Summary
    print(f"\n[harvest] Summary: {results}")


if __name__ == "__main__":
    main()
