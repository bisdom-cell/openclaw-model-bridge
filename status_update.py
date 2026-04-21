#!/usr/bin/env python3
"""
status_update.py — 三方共享意识锚点（原子读写）
所有写入使用 tmpfile + os.replace 原子操作，支持并发安全。

三方宪法：用户提供专业深度 + Claude Code 提供高效设计部署 + OpenClaw 提供数据复利
status.json 是三方的共享意识——"我们现在在哪、要去哪、学到了什么"。

三方协议：
  - Claude Code：开工读全部 → 收工写 session_context / recent_changes / quality
  - OpenClaw PA：每次 session 首先读 → 用户反馈写 feedback → 事件写 incidents
  - Cron 脚本：health / quality 自动更新

用法：
  python3 status_update.py --read                          # 读取完整状态（JSON）
  python3 status_update.py --read --human                  # 读取（人类可读格式）
  python3 status_update.py --set health.services ok        # 设置字段
  python3 status_update.py --set health.last_deploy "abc"  # 嵌套字段用.分隔
  python3 status_update.py --add priorities '{"task":"X","status":"active"}'
  python3 status_update.py --add recent_changes '{"date":"2026-03-25","what":"V29.5","by":"claude_code"}'
  python3 status_update.py --add feedback "趋势报告噪音词需优化"
  python3 status_update.py --add incidents '{"date":"2026-03-28","what":"ArXiv周末无推送","status":"resolved","by":"claude_code"}'
  python3 status_update.py --set session_context.unfinished "数据清洗Phase2设计中"
  python3 status_update.py --set quality.security_score 92
  python3 status_update.py --pop feedback 0                # 取出并删除第N条
  python3 status_update.py --clear feedback                # 清空数组字段
  python3 status_update.py --update-priority "知识图谱" status backlog
"""
import argparse
import json
import os
import sys
import time

# 状态文件路径解析：优先 ~/.kb/status.json（Mac Mini 生产环境），
# 不存在则回退到仓库根目录 status.json（Claude Code dev 环境）。
# 三方宪法要求此文件是唯一实时锚点，git 仓库作为跨环境同步通道。
_KB_STATUS = os.path.expanduser("~/.kb/status.json")
_REPO_STATUS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "status.json")
STATUS_FILE = _KB_STATUS if os.path.exists(_KB_STATUS) else _REPO_STATUS

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

    # 用户偏好（PA 写入，SOUL.md 展示，跨 session 持久化）
    "preferences": [],

    # 系统健康（cron 脚本更新）
    "health": {
        "services": "unknown",
        "last_deploy": "",
        "last_deploy_time": "",
        "last_preflight": "unknown",
        "last_preflight_time": "",
        "last_trend_report": "",
        "model_id": "",
        "kb_stats": "",
        "stale_jobs": "",
        "last_refresh": "",
    },

    # 开发连续性（Claude Code session 间的上下文传递）
    "session_context": {
        "last_session": "",         # 上次 session 日期
        "unfinished": "",           # 未完成的工作描述
        "open_prs": [],             # 待合并的 PR
        "blocked_on": "",           # 当前阻塞项
    },

    # 质量基线（防退化，每次收工写入）
    "quality": {
        "security_score": 0,        # security_score.py 评分（0-100）
        "test_count": 0,            # 单测总数
        "last_regression": "",      # 上次全量回归结果
        "coverage_pct": 0,          # 代码覆盖率
    },

    # 事件与告警（三方都可写入/消费）
    "incidents": [],
    # 格式: {"date":"", "what":"", "status":"open|resolved|monitoring", "by":""}

    # 当前阶段临时约束（区别于永久原则，会随阶段变化）
    "operating_rules": [],
    # 格式: "本周禁止升级Gateway" / "数据清洗优先于新功能"

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


def save_status(data, updated_by="unknown", audit_action="", audit_target="", audit_summary=""):
    """原子写入 status.json，同时写入审计日志。"""
    data["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
    data["updated_by"] = updated_by
    os.makedirs(os.path.dirname(STATUS_FILE), exist_ok=True)
    tmp = STATUS_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATUS_FILE)
    # 审计日志（静默失败，不影响主流程）
    if audit_action:
        try:
            from audit_log import audit
            audit(updated_by, audit_action, audit_target or "status.json", audit_summary)
        except Exception:
            pass


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
            if isinstance(c, dict):
                lines.append(f"  [{c.get('date','')}] {c.get('what','')} (by {c.get('by','')})")
            else:
                # V37.8.13: 防御性处理非 dict 条目（避免 AttributeError 崩溃）
                lines.append(f"  {str(c)[:120]}")
        lines.append("")

    # 开发连续性
    ctx = data.get("session_context", {})
    if ctx.get("unfinished") or ctx.get("blocked_on") or ctx.get("open_prs"):
        lines.append("🔄 开发连续性:")
        if ctx.get("last_session"):
            lines.append(f"  上次 session: {ctx['last_session']}")
        if ctx.get("unfinished"):
            lines.append(f"  未完成: {ctx['unfinished']}")
        if ctx.get("open_prs"):
            for pr in ctx["open_prs"]:
                lines.append(f"  PR: {pr}")
        if ctx.get("blocked_on"):
            lines.append(f"  阻塞: {ctx['blocked_on']}")
        lines.append("")

    # 事件与告警
    incidents = data.get("incidents", [])
    open_incidents = [i for i in incidents if i.get("status") != "resolved"]
    if open_incidents:
        lines.append(f"🚨 未解决事件 ({len(open_incidents)}):")
        for inc in open_incidents[:5]:
            lines.append(f"  [{inc.get('date','')}] {inc.get('what','')} ({inc.get('status','')}, by {inc.get('by','')})")
        lines.append("")

    h = data.get("health", {})
    lines.append("🏥 系统健康:")
    lines.append(f"  服务: {h.get('services','?')} | 模型: {h.get('model_id','?')}")
    lines.append(f"  部署: {h.get('last_deploy','')} ({h.get('last_deploy_time','')})")
    lines.append(f"  体检: {h.get('last_preflight','?')} ({h.get('last_preflight_time','')})")

    # 质量基线
    q = data.get("quality", {})
    if q.get("security_score") or q.get("test_count"):
        lines.append(f"  安全评分: {q.get('security_score', '?')}/100 | 单测: {q.get('test_count', '?')} | 覆盖率: {q.get('coverage_pct', '?')}%")
        if q.get("last_regression"):
            lines.append(f"  回归测试: {q['last_regression']}")

    # 临时约束
    rules = data.get("operating_rules", [])
    if rules:
        lines.append("")
        lines.append("⚠️ 当前约束:")
        for r in rules:
            lines.append(f"  - {r}")

    prefs = data.get("preferences", [])
    if prefs:
        lines.append("")
        lines.append("👤 用户偏好:")
        for p in prefs:
            lines.append(f"  - {p}")

    if data.get("notes"):
        lines.append(f"\n📎 备注: {data['notes']}")

    return "\n".join(lines)


def _parse_cli_value(raw):
    """V37.9.7: CLI --set/--add VALUE 自动检测并解析 JSON 字面量。

    触发条件: 首字符为 '[' '{' 'true' 'false' 'null' 或纯数字
    → json.loads() 得到 list/dict/bool/None/int/float
    失败或非 JSON 形 → 返回原字符串（向后兼容）

    背景: 2026-04-21 bug — `status_update.py --set unfinished '[...]'`
    被当字符串存，`--read --human` 按字符迭代。此函数修复该盲区。
    """
    if not isinstance(raw, str) or not raw:
        return raw
    s = raw.strip()
    # JSON 字面量触发字符
    if s[:1] in "[{" or s in ("true", "false", "null"):
        try:
            return json.loads(s)
        except (ValueError, TypeError):
            pass  # 不是合法 JSON → 当字符串
    # 纯整数/浮点（严格格式，不误报科学记数法外情况）
    if s.lstrip("-").replace(".", "", 1).isdigit():
        try:
            return int(s) if "." not in s else float(s)
        except (ValueError, TypeError):
            pass
    return raw


def set_nested(data, key_path, value):
    """设置嵌套字段，如 'health.services' → data['health']['services']。

    V37.9.7: value 若为 JSON 字面量（'[...]' / '{...}' / true/false/null /
    数字字符串），自动解析为对应 Python 类型。其他字符串保持原样（向后兼容）。
    """
    keys = key_path.split(".")
    obj = data
    for k in keys[:-1]:
        if k not in obj or not isinstance(obj[k], dict):
            obj[k] = {}
        obj = obj[k]
    obj[keys[-1]] = _parse_cli_value(value)


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
        # 各数组的容量上限（防无限增长导致 SOUL.md 膨胀）
        ARRAY_LIMITS = {
            "recent_changes": 20,   # 最新在前
            "incidents": 30,        # 最新在前
            "operating_rules": 10,
            "preferences": 15,
            "priorities": 15,
            "feedback": 20,
        }
        # recent_changes / incidents 插入到开头
        if array_name in ("recent_changes", "incidents"):
            data[array_name].insert(0, item)
        else:
            data[array_name].append(item)
        # 按上限截断
        limit = ARRAY_LIMITS.get(array_name)
        if limit:
            data[array_name] = data[array_name][:limit]
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
        # 构造审计信息
        _audit_action = ""
        _audit_target = "status.json"
        _audit_summary = ""
        if args.set:
            _audit_action = "set"
            _audit_target = args.set[0]
            _audit_summary = f"{args.set[0]}={args.set[1]}"
        elif args.add:
            _audit_action = "add"
            _audit_target = args.add[0]
            _audit_summary = args.add[1][:200]
        elif args.pop:
            _audit_action = "pop"
            _audit_target = args.pop[0]
            _audit_summary = f"index={args.pop[1]}"
        elif args.clear:
            _audit_action = "clear"
            _audit_target = args.clear
        elif args.update_priority:
            _audit_action = "update_priority"
            _audit_target = args.update_priority[0]
            _audit_summary = f"{args.update_priority[1]}={args.update_priority[2]}"
        elif args.focus:
            _audit_action = "set_focus"
            _audit_summary = args.focus[:200]
        elif args.note:
            _audit_action = "set_note"
            _audit_summary = args.note[:200]
        save_status(data, updated_by=args.by,
                    audit_action=_audit_action, audit_target=_audit_target,
                    audit_summary=_audit_summary)
        print("OK", file=sys.stderr)
    elif not args.read:
        parser.print_help()


if __name__ == "__main__":
    main()
