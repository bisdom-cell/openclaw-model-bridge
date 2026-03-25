#!/usr/bin/env python3
"""
status_update.py — 三方共享项目状态（原子读写）
所有写入使用 tmpfile + os.replace 原子操作，支持并发安全。

三方协议：
  - Claude Code：开工读 → 收工写 priorities / recent_changes
  - OpenClaw PA：用户反馈时写 feedback / 回答时读全部
  - Cron 脚本：health_check / auto_deploy / kb_trend 更新对应字段

用法：
  python3 status_update.py --read                          # 读取完整状态（JSON）
  python3 status_update.py --read --human                  # 读取（人类可读格式）
  python3 status_update.py --set health.services ok        # 设置字段
  python3 status_update.py --set health.last_deploy "abc"  # 嵌套字段用.分隔
  python3 status_update.py --add priorities '{"task":"X","status":"active"}'
  python3 status_update.py --add recent_changes '{"date":"2026-03-25","what":"V29.5","by":"claude_code"}'
  python3 status_update.py --add feedback "趋势报告噪音词需优化"
  python3 status_update.py --pop feedback 0                # 取出并删除第N条
  python3 status_update.py --clear feedback                # 清空数组字段
  python3 status_update.py --update-priority "知识图谱" status backlog
"""
import argparse
import json
import os
import sys
import time

STATUS_FILE = os.path.expanduser("~/.kb/status.json")

# ---------------------------------------------------------------------------
# 默认状态结构
# ---------------------------------------------------------------------------
DEFAULT_STATUS = {
    "updated": "",
    "updated_by": "",

    # 当前优先级（有序）
    "priorities": [],

    # 最近变更（最新在前，保留20条）
    "recent_changes": [],

    # 待处理反馈（PA 写入，Claude Code 消费）
    "feedback": [],

    # 系统健康（cron 脚本更新）
    "health": {
        "services": "unknown",
        "last_deploy": "",
        "last_deploy_time": "",
        "last_preflight": "unknown",
        "last_preflight_time": "",
        "last_trend_report": "",
        "model_id": "",
    },

    # 本周焦点（开工时 Claude Code 设置，PA 可引用）
    "focus": "",

    # 备注（自由文本）
    "notes": "",
}


def load_status():
    """加载 status.json，不存在则返回默认结构。"""
    try:
        with open(STATUS_FILE) as f:
            data = json.load(f)
        # 确保所有默认字段存在（向前兼容）
        for k, v in DEFAULT_STATUS.items():
            if k not in data:
                data[k] = v
            elif isinstance(v, dict):
                for kk, vv in v.items():
                    if kk not in data[k]:
                        data[k][kk] = vv
        return data
    except (OSError, json.JSONDecodeError):
        return dict(DEFAULT_STATUS)


def save_status(data, updated_by="unknown"):
    """原子写入 status.json。"""
    data["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
    data["updated_by"] = updated_by
    os.makedirs(os.path.dirname(STATUS_FILE), exist_ok=True)
    tmp = STATUS_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATUS_FILE)


def format_human(data):
    """人类可读格式输出。"""
    lines = []
    lines.append(f"📋 项目状态 (更新: {data.get('updated', '?')} by {data.get('updated_by', '?')})")
    lines.append("")

    if data.get("focus"):
        lines.append(f"🎯 本周焦点: {data['focus']}")
        lines.append("")

    priorities = data.get("priorities", [])
    if priorities:
        lines.append("📌 优先级:")
        for p in priorities:
            icon = {"active": "🟢", "backlog": "⚪", "done": "✅", "blocked": "🔴"}.get(p.get("status", ""), "⚪")
            note = f" — {p['note']}" if p.get("note") else ""
            lines.append(f"  {icon} [{p.get('status','?')}] {p.get('task','?')}{note}")
        lines.append("")

    feedback = data.get("feedback", [])
    if feedback:
        lines.append(f"💬 待处理反馈 ({len(feedback)}):")
        for i, fb in enumerate(feedback):
            lines.append(f"  {i}. {fb}")
        lines.append("")

    changes = data.get("recent_changes", [])
    if changes:
        lines.append("📝 最近变更:")
        for c in changes[:5]:
            lines.append(f"  [{c.get('date','')}] {c.get('what','')} (by {c.get('by','')})")
        lines.append("")

    h = data.get("health", {})
    lines.append("🏥 系统健康:")
    lines.append(f"  服务: {h.get('services','?')} | 模型: {h.get('model_id','?')}")
    lines.append(f"  部署: {h.get('last_deploy','')} ({h.get('last_deploy_time','')})")
    lines.append(f"  体检: {h.get('last_preflight','?')} ({h.get('last_preflight_time','')})")

    if data.get("notes"):
        lines.append(f"\n📎 备注: {data['notes']}")

    return "\n".join(lines)


def set_nested(data, key_path, value):
    """设置嵌套字段，如 'health.services' → data['health']['services']。"""
    keys = key_path.split(".")
    obj = data
    for k in keys[:-1]:
        if k not in obj or not isinstance(obj[k], dict):
            obj[k] = {}
        obj = obj[k]
    obj[keys[-1]] = value


def main():
    parser = argparse.ArgumentParser(description="三方共享项目状态管理")
    parser.add_argument("--read", action="store_true", help="读取完整状态")
    parser.add_argument("--human", action="store_true", help="人类可读格式")
    parser.add_argument("--set", nargs=2, metavar=("KEY", "VALUE"), help="设置字段（支持嵌套：health.services）")
    parser.add_argument("--add", nargs=2, metavar=("ARRAY", "ITEM"), help="追加到数组字段")
    parser.add_argument("--pop", nargs=2, metavar=("ARRAY", "INDEX"), help="取出并删除数组元素")
    parser.add_argument("--clear", metavar="ARRAY", help="清空数组字段")
    parser.add_argument("--update-priority", nargs=3, metavar=("TASK", "FIELD", "VALUE"),
                        help="更新优先级项的字段")
    parser.add_argument("--by", default="cli", help="操作者标识（claude_code/pa/cron/user）")
    parser.add_argument("--focus", help="设置本周焦点")
    parser.add_argument("--note", help="设置备注")
    args = parser.parse_args()

    if args.read:
        data = load_status()
        if args.human:
            print(format_human(data))
        else:
            print(json.dumps(data, ensure_ascii=False, indent=2))
        return

    data = load_status()
    changed = False

    if args.set:
        key, value = args.set
        set_nested(data, key, value)
        changed = True

    if args.add:
        array_name, item_str = args.add
        if array_name not in data:
            data[array_name] = []
        if not isinstance(data[array_name], list):
            print(f"ERROR: {array_name} is not an array", file=sys.stderr)
            sys.exit(1)
        # 尝试 JSON 解析，失败则当字符串
        try:
            item = json.loads(item_str)
        except (json.JSONDecodeError, ValueError):
            item = item_str
        # recent_changes 插入到开头，保留20条
        if array_name == "recent_changes":
            data[array_name].insert(0, item)
            data[array_name] = data[array_name][:20]
        else:
            data[array_name].append(item)
        changed = True

    if args.pop:
        array_name, idx_str = args.pop
        try:
            idx = int(idx_str)
            if array_name in data and isinstance(data[array_name], list):
                if 0 <= idx < len(data[array_name]):
                    removed = data[array_name].pop(idx)
                    print(json.dumps(removed, ensure_ascii=False) if isinstance(removed, dict) else removed)
                    changed = True
        except (ValueError, IndexError):
            pass

    if args.clear:
        if args.clear in data and isinstance(data[args.clear], list):
            data[args.clear] = []
            changed = True

    if args.update_priority:
        task_name, field, value = args.update_priority
        for p in data.get("priorities", []):
            if p.get("task") == task_name:
                p[field] = value
                changed = True
                break
        else:
            # 不存在则新增
            data.setdefault("priorities", []).append({"task": task_name, "status": value if field == "status" else "active", field: value})
            changed = True

    if args.focus:
        data["focus"] = args.focus
        changed = True

    if args.note:
        data["notes"] = args.note
        changed = True

    if changed:
        save_status(data, updated_by=args.by)
        print("OK", file=sys.stderr)
    elif not args.read:
        parser.print_help()


if __name__ == "__main__":
    main()
