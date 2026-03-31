#!/usr/bin/env python3
"""
preference_learner.py — 用户偏好自动学习器（V30.4）

从系统数据中自动推断用户偏好，写入 status.json → SOUL.md → PA 遵守。
不依赖 LLM，不读取用户消息内容（隐私安全）。

数据源：
  1. proxy log → 活跃时段、互动频率、响应时间敏感度
  2. proxy log → 工具使用模式（常用/从不用）
  3. KB notes/tags → 关注领域
  4. status.json feedback → 反馈倾向

用法：
  python3 preference_learner.py              # 分析并展示（不写入）
  python3 preference_learner.py --apply      # 分析并写入 status.json
  python3 preference_learner.py --json       # JSON 输出（供脚本调用）
  python3 preference_learner.py --days 14    # 分析最近14天（默认7天）

设计原则：
  - 只分析行为数据，不分析消息内容（隐私）
  - 自动偏好标记 [auto]，显式偏好标记 [user]，互不覆盖
  - 置信度阈值：只写入有足够数据支撑的偏好
"""
import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

# ── 配置 ──────────────────────────────────────────────────────────
PROXY_LOG = os.path.expanduser("~/tool_proxy.log")
KB_NOTES_DIR = os.path.expanduser("~/.kb/notes")
KB_SOURCES_DIR = os.path.expanduser("~/.kb/sources")
KB_INDEX = os.path.expanduser("~/.kb/index.json")

# 偏好生成的最小数据量阈值
MIN_REQUESTS = 10       # 至少 10 次请求才分析互动模式
MIN_TOOL_CALLS = 5      # 至少 5 次工具调用才分析工具偏好
MIN_KB_NOTES = 10       # 至少 10 条笔记才分析领域偏好


def parse_proxy_log(log_path, days=7):
    """解析 proxy log，提取请求元数据。"""
    if not os.path.exists(log_path):
        return []

    cutoff = datetime.now() - timedelta(days=days)
    entries = []

    with open(log_path, errors="replace") as f:
        for line in f:
            # [proxy] 2026-03-28 22:01:10 [id] ...
            m = re.match(r'\[proxy\]\s+(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})\s+\[(\w+)\]\s+(.*)', line)
            if not m:
                continue
            try:
                ts = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
            except ValueError:
                continue
            if ts < cutoff:
                continue

            rid = m.group(2)
            rest = m.group(3)
            entries.append({"ts": ts, "rid": rid, "line": rest})

    return entries


def analyze_activity(entries):
    """分析活跃时段。"""
    if not entries:
        return None

    hours = Counter()
    days_seen = set()
    for e in entries:
        hours[e["ts"].hour] += 1
        days_seen.add(e["ts"].date())

    if len(days_seen) < 2:
        return None

    # 找活跃区间（占 80% 请求的最小连续小时区间）
    total = sum(hours.values())
    sorted_hours = sorted(hours.items(), key=lambda x: -x[1])
    active_hours = []
    running = 0
    for h, c in sorted_hours:
        active_hours.append(h)
        running += c
        if running >= total * 0.8:
            break

    active_hours.sort()
    if active_hours:
        start = active_hours[0]
        end = active_hours[-1]
        return f"活跃时段 {start:02d}:00-{end + 1:02d}:00（{len(days_seen)}天数据）"
    return None


def analyze_interaction_style(entries):
    """分析互动风格（通过响应特征推断）。"""
    text_sizes = []
    token_counts = []

    for e in entries:
        # TEXT: 419 chars
        m = re.match(r'TEXT:\s+(\d+)\s+chars', e["line"])
        if m:
            text_sizes.append(int(m.group(1)))
        # TOKENS: prompt=10226 total=10457
        m = re.match(r'TOKENS:\s+prompt=([0-9,]+)\s+total=([0-9,]+)', e["line"])
        if m:
            token_counts.append(int(m.group(1).replace(",", "")))

    prefs = []
    if len(text_sizes) >= MIN_REQUESTS:
        avg_text = sum(text_sizes) / len(text_sizes)
        if avg_text < 200:
            prefs.append("用户偏好简洁回复（平均响应 <200 字）")
        elif avg_text > 800:
            prefs.append("用户偏好详细回复（平均响应 >800 字）")

    return prefs


def analyze_tool_usage(entries):
    """分析工具使用模式。"""
    tool_calls = Counter()
    for e in entries:
        m = re.match(r'CALL:\s+(\w+)', e["line"])
        if m:
            tool_calls[m.group(1)] += 1

    if sum(tool_calls.values()) < MIN_TOOL_CALLS:
        return []

    prefs = []
    # 高频工具
    top_tools = tool_calls.most_common(3)
    if top_tools:
        tools_str = "、".join(f"{t}({c}次)" for t, c in top_tools)
        prefs.append(f"常用工具：{tools_str}")

    return prefs


def analyze_kb_interests(notes_dir, index_path, days=7):
    """分析知识库关注领域。"""
    tags = Counter()
    cutoff = datetime.now() - timedelta(days=days)

    # 从 index.json 读取标签统计
    if os.path.exists(index_path):
        try:
            with open(index_path) as f:
                index = json.load(f)
            for entry in index.get("entries", []):
                try:
                    ts = datetime.strptime(entry.get("date", ""), "%Y-%m-%d %H:%M:%S")
                    if ts < cutoff:
                        continue
                except (ValueError, TypeError):
                    pass
                for tag in entry.get("tags", []):
                    tags[tag] += 1
        except (json.JSONDecodeError, OSError):
            pass

    # 也扫描 notes 目录的文件名时间戳
    if os.path.isdir(notes_dir):
        for f in os.listdir(notes_dir):
            if f.endswith(".md"):
                # 文件名格式: YYYYMMDDHHMMSS.md
                try:
                    ts = datetime.strptime(f[:14], "%Y%m%d%H%M%S")
                    if ts >= cutoff:
                        tags["active_notes"] += 1
                except (ValueError, IndexError):
                    pass

    if sum(tags.values()) < MIN_KB_NOTES:
        return []

    # 排除通用标签
    generic = {"feedback", "active_notes", "note", "general"}
    top_tags = [(t, c) for t, c in tags.most_common(10) if t not in generic][:5]

    if top_tags:
        topics = "、".join(t for t, _ in top_tags)
        return [f"关注领域：{topics}"]
    return []


def analyze_feedback(status_path):
    """分析 status.json 中的反馈倾向。"""
    try:
        with open(status_path) as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return []

    feedback = data.get("feedback", [])
    if not feedback:
        return []

    return [f"用户已提交 {len(feedback)} 条反馈，最新: {feedback[0] if isinstance(feedback[0], str) else json.dumps(feedback[0], ensure_ascii=False)[:50]}"]


def run_analysis(days=7):
    """运行全部分析，返回自动发现的偏好列表。"""
    entries = parse_proxy_log(PROXY_LOG, days)

    preferences = []

    # 1. 活跃时段
    activity = analyze_activity(entries)
    if activity:
        preferences.append(activity)

    # 2. 互动风格
    preferences.extend(analyze_interaction_style(entries))

    # 3. 工具使用
    preferences.extend(analyze_tool_usage(entries))

    # 4. KB 关注领域
    preferences.extend(analyze_kb_interests(KB_NOTES_DIR, KB_INDEX, days))

    # 5. 反馈倾向
    from status_update import STATUS_FILE
    preferences.extend(analyze_feedback(STATUS_FILE))

    return preferences


def apply_preferences(auto_prefs):
    """将自动偏好写入 status.json，保留用户显式偏好。"""
    from status_update import load_status, save_status

    data = load_status()
    existing = data.get("preferences", [])

    # 分离: [user] 标记的是用户显式偏好，[auto] 是系统分析的
    user_prefs = [p for p in existing if not p.startswith("[auto] ")]
    new_auto = [f"[auto] {p}" for p in auto_prefs]

    # 合并：用户偏好在前，自动偏好在后
    data["preferences"] = user_prefs + new_auto

    save_status(data, updated_by="preference_learner",
                audit_action="update_preferences",
                audit_target="preferences",
                audit_summary=f"{len(new_auto)} auto preferences detected")

    return data["preferences"]


def main():
    parser = argparse.ArgumentParser(description="用户偏好自动学习器")
    parser.add_argument("--apply", action="store_true", help="写入 status.json")
    parser.add_argument("--json", action="store_true", help="JSON 输出")
    parser.add_argument("--days", type=int, default=7, help="分析天数（默认7）")
    args = parser.parse_args()

    auto_prefs = run_analysis(args.days)

    if args.json:
        print(json.dumps({"auto_preferences": auto_prefs, "days": args.days},
                         ensure_ascii=False, indent=2))
        return

    if not auto_prefs:
        print("📊 数据不足，暂无法推断偏好（需要更多互动数据）")
        return

    print(f"📊 自动偏好分析（最近 {args.days} 天）:\n")
    for i, p in enumerate(auto_prefs, 1):
        print(f"  {i}. {p}")

    if args.apply:
        all_prefs = apply_preferences(auto_prefs)
        print(f"\n✅ 已写入 status.json（共 {len(all_prefs)} 条偏好）")
        print("   下次 kb_status_refresh 运行后，PA 将在 SOUL.md 中看到这些偏好")
    else:
        print(f"\n💡 使用 --apply 写入 status.json")


if __name__ == "__main__":
    main()
