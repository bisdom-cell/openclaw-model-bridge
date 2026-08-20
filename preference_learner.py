#!/usr/bin/env python3
"""
preference_learner.py — 用户偏好自动学习器（V30.4）

从系统数据中自动推断用户偏好，写入 status.json → SOUL.md → PA 遵守。
不依赖 LLM，不读取用户消息内容（隐私安全）。

数据源：
  1. proxy log → 活跃时段（最小连续窗口）
  2. proxy log → 工具使用模式（常用/从不用）
  3. KB notes/tags → 关注领域

V37.9.323 对抗审计（本模块此前零单测、从未被审，却每天 07:30 写进 PA 宪法级 prompt）：
  退役 analyze_feedback  —— 它把 feedback[0]（数组是 append 序 = 最老的一条）标成"最新"，
    并 json.dumps(...)[:50] 硬截断 → 产出不闭合的 JSON 残片进 SOUL.md；且"提交了几条反馈"
    是状态事实不是偏好（status.json 自己的 feedback 段已展示）。
  退役 analyze_interaction_style —— 它统计的 `TEXT: N chars` 是 **assistant 自己回复**的
    长度（tool_proxy.py 从后端 assistant message 记的），却据此断言"用户偏好简洁回复"并
    写进"必须遵守"清单 → 模型照自己过去的输出给自己下指令 = 自我强化回路，且全程没有
    测量过用户的任何东西。

用法：
  python3 preference_learner.py              # 分析并展示（不写入）
  python3 preference_learner.py --apply      # 分析并写入 status.json
  python3 preference_learner.py --json       # JSON 输出（供脚本调用）
  python3 preference_learner.py --days 14    # 分析最近14天（默认7天）

设计原则：
  - 只分析行为数据，不分析消息内容（隐私）
  - 自动偏好标记 [auto]，显式偏好标记 [user]，互不覆盖
  - 置信度阈值：只写入有足够数据支撑的偏好
  - **诚实边界**：本模块产出的是【系统观察】不是【用户指令】。kb_status_refresh.sh 把
    [auto] 条目渲染在"系统观察（供参考）"段，只有用户显式偏好进"必须遵守"段。
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
MIN_REQUESTS = 10       # 至少 10 次请求才分析活跃时段（INV-JOB-PREFLEARN-001 的实际兑现点）
MIN_TOOL_CALLS = 5      # 至少 5 次工具调用才分析工具偏好
MIN_KB_NOTES = 10       # 至少 10 条笔记才分析领域偏好

# 活跃时段可报告的最大跨度：窗口超过半天等于"全天都活跃"= 零信息，宁可不报
# （V37.9.323：旧实现取"高频小时集合的 min..max"而非连续窗口，00 点和 20 点两簇流量
#   会被报成"活跃时段 00:00-21:00"—— 生产 status.json 里就是这一条）
MAX_ACTIVE_SPAN_HOURS = 12
ACTIVE_COVERAGE = 0.8   # 连续窗口需覆盖的请求占比


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


def smallest_active_window(hour_counts, coverage=ACTIVE_COVERAGE):
    """覆盖 >= coverage 比例请求的【最小连续小时窗口】(环形, 跨午夜合法)。

    返回 (start_hour, span_hours) 或 None。纯函数, 可单测。

    V37.9.323 血案: 旧实现按频次降序取小时【集合】再报 min..max, 集合不必连续 ——
    00 点与 20 点两簇流量被报成 "活跃时段 00:00-21:00"(21 小时跨度, 其中 19 小时零请求),
    而这句话是以"用户偏好"的身份进 PA 宪法 prompt 的。
    """
    total = sum(hour_counts.values())
    if total <= 0:
        return None
    need = total * coverage
    best = None
    for start in range(24):
        running = 0
        for span in range(1, 25):
            running += hour_counts.get((start + span - 1) % 24, 0)
            if running >= need:
                if best is None or span < best[1]:
                    best = (start, span)
                break
    return best


def analyze_activity(entries):
    """分析活跃时段（最小连续窗口；样本不足或窗口退化为"几乎全天"时不报）。"""
    if not entries:
        return None

    # INV-JOB-PREFLEARN-001 的实际兑现点: 旧代码 MIN_REQUESTS 只守 analyze_interaction_style,
    # 而最显眼的活跃时段【完全无样本门】—— 治理的 file_contains 检查照样过 (V37.9.320 家族)。
    if len(entries) < MIN_REQUESTS:
        return None

    hours = Counter()
    days_seen = set()
    for e in entries:
        hours[e["ts"].hour] += 1
        days_seen.add(e["ts"].date())

    if len(days_seen) < 2:
        return None

    win = smallest_active_window(hours)
    if win is None:
        return None
    start, span = win
    if span > MAX_ACTIVE_SPAN_HOURS:
        # "活跃时段 00:00-21:00" 这类零信息断言不进 PA 上下文
        return None

    end = (start + span) % 24
    return f"活跃时段 {start:02d}:00-{end:02d}:00（{len(days_seen)}天数据）"


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
                    # V37.9.323: 旧代码 `pass` 会让【无 date / date 格式不符】的条目
                    # 绕过窗口过滤照样计入 —— 400 天前的条目能被报成"最近 7 天关注领域"。
                    # 不能从没有日期的条目推断"最近的关注", 排除。
                    continue
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


def run_analysis(days=7):
    """运行全部分析，返回自动发现的偏好列表。"""
    entries = parse_proxy_log(PROXY_LOG, days)

    preferences = []

    # 1. 活跃时段
    activity = analyze_activity(entries)
    if activity:
        preferences.append(activity)

    # 2. 工具使用
    preferences.extend(analyze_tool_usage(entries))

    # 3. KB 关注领域
    preferences.extend(analyze_kb_interests(KB_NOTES_DIR, KB_INDEX, days))

    # V37.9.323 退役: 互动风格(循环推断) + 反馈倾向(截断 JSON + 最老当最新), 见头部注释
    return preferences


def apply_preferences(auto_prefs):
    """将自动偏好写入 status.json，保留用户显式偏好。"""
    from status_update import load_status, save_status
    # V37.9.238: RMW 在跨写者锁内（finding C lost-update）。部署窗口 fallback。
    try:
        from status_update import status_lock
    except ImportError:
        from contextlib import nullcontext as status_lock

    with status_lock():
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
