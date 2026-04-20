#!/usr/bin/env python3
"""
kb_harvest_chat.py — 对话精华提炼器 (V37.1)

从 tool_proxy 捕获的每日对话日志中提取关键内容，写入 KB。
将用户与 PA 的高质量交互转化为持久化知识。

数据流：
  tool_proxy.py 捕获 → ~/.kb/conversations/YYYYMMDD.jsonl
  本脚本读取 → LLM 提炼关键点 → kb_write.sh 写入 KB notes

设计原则：
  - 离线处理：不在请求热路径上，cron 触发
  - 去重：已处理的日志文件标记跳过
  - MapReduce：大对话量分块提取 + 合并去重，零数据丢失
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
# MapReduce: chunk size for map phase (leave room for prompt ~3K)
CHUNK_MAX_CHARS = 45000


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



def chunk_conversations(turns):
    """Split formatted conversation turns into chunks of ~CHUNK_MAX_CHARS.

    Splits at turn boundaries to preserve conversation integrity.
    Returns list of (chunk_text, turn_range_str) tuples.
    """
    chunks = []
    current_parts = []
    current_size = 0
    chunk_start = 1

    for i, t in enumerate(turns, 1):
        ts = t.get("ts", "?")
        user = t.get("user", "")[:1500]
        assistant = t.get("assistant", "")[:1500]
        part = f"--- 对话 {i} [{ts}] ---\n用户: {user}\nPA: {assistant}"
        part_size = len(part)

        if current_size + part_size > CHUNK_MAX_CHARS and current_parts:
            chunk_text = "\n\n".join(current_parts)
            chunks.append((chunk_text, f"{chunk_start}-{i - 1}"))
            current_parts = []
            current_size = 0
            chunk_start = i

        current_parts.append(part)
        current_size += part_size + 2  # +2 for "\n\n" join

    if current_parts:
        chunk_text = "\n\n".join(current_parts)
        end_idx = chunk_start + len(current_parts) - 1
        chunks.append((chunk_text, f"{chunk_start}-{end_idx}"))

    return chunks


def _build_extract_prompt(conversations_text, date_str, chunk_info=None):
    """Build the extraction prompt for a (chunk of) conversations."""
    chunk_note = ""
    if chunk_info:
        chunk_note = f"\n注意：这是当天对话的第 {chunk_info} 部分，请完整提取本段中的所有关键内容。\n"

    return f"""你是一个信息提炼器。以下是用户与AI助手(PA)在 {date_str} 的对话记录。
{chunk_note}
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

输出格式（每条一行，可以有0-20条）：
- [类型] 内容概要（保留关键细节和上下文）

类型：decision/preference/insight/conclusion/discovery

如果对话没有值得保存的内容，输出：无关键内容

---
{conversations_text}
---"""


def _llm_call(prompt, max_tokens=1500, timeout=120):
    """Single LLM call, returns content string or None."""
    try:
        import urllib.request
        req_body = json.dumps({
            "model": "default",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": 0.3,
        }).encode()
        req = urllib.request.Request(
            LLM_URL,
            data=req_body,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            result = json.loads(resp.read())
            return result["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"[harvest] LLM call failed: {e}", file=sys.stderr)
        return None


def _reduce_key_points(all_points, date_str):
    """Merge and deduplicate key points from multiple map chunks."""
    prompt = f"""你是一个信息整合器。以下是从 {date_str} 的对话中**分段提取**的关键内容。
由于对话量大，分成了多段独立提取，可能存在重复或重叠。

请完成以下工作：
1. **去重**：合并语义相同的条目（保留更完整的版本）
2. **保留全部独特信息**：不同的洞察/决策/偏好必须全部保留
3. **统一格式**：保持 [类型] 前缀

输入（各段提取结果）：
{all_points}

输出格式（每条一行）：
- [类型] 内容概要

类型：decision/preference/insight/conclusion/discovery"""

    return _llm_call(prompt, max_tokens=2000, timeout=120)


def extract_key_points(turns, date_str):
    """Extract key points using MapReduce for large conversations.

    - Single chunk (<=CHUNK_MAX_CHARS): one LLM call (same as before)
    - Multiple chunks: Map (extract per chunk) → Reduce (merge + dedup)
    """
    chunks = chunk_conversations(turns)
    total_chars = sum(len(c[0]) for c in chunks)

    if len(chunks) == 1:
        # Single chunk: direct extraction (backward compatible)
        prompt = _build_extract_prompt(chunks[0][0], date_str)
        return _llm_call(prompt)

    # MapReduce: multiple chunks
    print(f"[harvest] MapReduce mode: {len(chunks)} chunks "
          f"({total_chars} chars total)")

    # Map phase: extract from each chunk
    map_results = []
    for i, (chunk_text, turn_range) in enumerate(chunks, 1):
        chunk_info = f"{i}/{len(chunks)} (对话 {turn_range})"
        print(f"[harvest]   Map {chunk_info} ({len(chunk_text)} chars)...")
        prompt = _build_extract_prompt(chunk_text, date_str, chunk_info)
        result = _llm_call(prompt)
        if result and "无关键内容" not in result:
            map_results.append(f"=== 第{i}段 (对话 {turn_range}) ===\n{result}")

    if not map_results:
        return "无关键内容"

    if len(map_results) == 1:
        # Only one chunk had content, no reduce needed
        # Strip the segment header
        return map_results[0].split("\n", 1)[1] if "\n" in map_results[0] else map_results[0]

    # Reduce phase: merge and deduplicate
    all_points = "\n\n".join(map_results)
    print(f"[harvest]   Reduce: merging {len(map_results)} segments...")
    reduced = _reduce_key_points(all_points, date_str)
    return reduced


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

    # Estimate total size for reporting
    total_chars = sum(
        len(t.get("user", "")[:1500]) + len(t.get("assistant", "")[:1500]) + 30
        for t in turns
    )
    chunks = chunk_conversations(turns)

    if dry_run:
        print(f"[harvest] DRY RUN: would process {len(turns)} turns "
              f"({total_chars} chars, {len(chunks)} chunk(s))")
        print(f"[harvest] Sample (first turn):")
        if turns:
            t = turns[0]
            print(f"  User: {t.get('user', '')[:100]}...")
            print(f"  PA: {t.get('assistant', '')[:100]}...")
        return "dry_run"

    # Extract key points via MapReduce (auto: single chunk or multi-chunk)
    print(f"[harvest] Extracting key points via LLM "
          f"({total_chars} chars, {len(chunks)} chunk(s))...")
    key_points = extract_key_points(turns, date_str)
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

    # V37.8.13: 写 last_run.json 供 watchdog 观察
    if not args.dry_run and not args.stats:
        try:
            status_file = os.path.join(os.path.expanduser("~/.kb"), "last_run_harvest_chat.json")
            # 整体状态：任一 date=ok 则 ok，全部 empty/error 则 error
            statuses = list(results.values())
            if any(s == "ok" for s in statuses):
                overall = "ok"
            elif any(s == "error" for s in statuses):
                overall = "error"
            else:
                overall = "empty"
            status_data = {
                "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "status": overall,
                "new": sum(1 for s in statuses if s == "ok"),
                "dates": results,
            }
            with open(status_file, "w", encoding="utf-8") as f:
                json.dump(status_data, f, ensure_ascii=False)
        except Exception as e:
            print(f"[harvest] WARN: last_run.json write failed: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
