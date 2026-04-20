#!/usr/bin/env python3
"""
audit_log.py — 不可篡改操作审计日志

每条日志包含：时间戳、操作者、动作、目标、摘要、前一条的哈希（链式校验）。
日志文件为 append-only，每行一个 JSON 对象（JSONL 格式）。
链式哈希确保任何中间删改都可被检测到。

用法：
  # 作为模块调用
  from audit_log import audit
  audit("claude_code", "set", "health.services", "ok→gw:200/px:200/ad:200")

  # 命令行
  python3 audit_log.py --actor cron --action set --target health.services --summary "ok"
  python3 audit_log.py --verify          # 校验链式哈希完整性
  python3 audit_log.py --tail 20         # 查看最近20条
  python3 audit_log.py --stats           # 统计概览
"""
import argparse
import hashlib
import json
import os
import sys
import time

AUDIT_FILE = os.path.expanduser("~/.kb/audit.jsonl")
HASH_ALGO = "sha256"


def _compute_hash(entry_str: str) -> str:
    """计算单条记录的 SHA256 哈希。"""
    return hashlib.sha256(entry_str.encode("utf-8")).hexdigest()[:16]


def _get_last_hash() -> str:
    """读取最后一条记录的哈希，作为链式指针。"""
    if not os.path.exists(AUDIT_FILE):
        return "0" * 16
    try:
        with open(AUDIT_FILE, "rb") as f:
            # 从文件末尾向前搜索最后一行
            f.seek(0, 2)
            size = f.tell()
            if size == 0:
                return "0" * 16
            pos = size - 1
            while pos > 0:
                f.seek(pos)
                if f.read(1) == b"\n" and pos < size - 1:
                    break
                pos -= 1
            if pos == 0:
                f.seek(0)
            last_line = f.readline().decode("utf-8").strip()
            if not last_line:
                return "0" * 16
            entry = json.loads(last_line)
            return entry.get("hash", "0" * 16)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return "0" * 16


def audit(actor: str, action: str, target: str, summary: str = ""):
    """
    写入一条审计记录。

    Args:
        actor: 操作者 (claude_code / pa / cron / user / system)
        action: 动作 (set / add / delete / deploy / restart / backup / login / verify)
        target: 操作目标 (health.services / status.json / crontab / ...)
        summary: 摘要说明
    """
    prev_hash = _get_last_hash()
    entry = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "actor": actor,
        "action": action,
        "target": target,
        "summary": summary[:500],  # 限制长度
        "prev": prev_hash,
    }
    # 计算当前记录的哈希（不含 hash 字段本身）
    entry_str = json.dumps(entry, ensure_ascii=False, sort_keys=True)
    entry["hash"] = _compute_hash(entry_str)

    os.makedirs(os.path.dirname(AUDIT_FILE), exist_ok=True)
    with open(AUDIT_FILE, "a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def verify_chain() -> dict:
    """
    校验审计日志链式哈希完整性。

    V37.7: 当某行 JSON 解析失败时，`prev_hash` 标记为 None（chain broken
    from here），后续有效行跳过 prev 指针检查（我们无法知道该是什么），
    但仍然独立验证 entry 自身 hash。这避免了单个 parse error 引发 cascade
    错误把无辜的后续行全部误报。

    Returns:
        {"ok": bool, "total": int, "errors": [{"line": int, "expected": str, "actual": str}]}
    """
    if not os.path.exists(AUDIT_FILE):
        return {"ok": True, "total": 0, "errors": []}

    errors = []
    total = 0
    # prev_hash = None 表示"链式已在上游断裂，下一行不要再和前值比对"
    prev_hash: "str | None" = "0" * 16

    with open(AUDIT_FILE) as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            total += 1
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                errors.append({"line": i, "expected": "valid JSON", "actual": "parse error"})
                # Chain 已断裂，下游无法验证 prev 指针
                prev_hash = None
                continue

            # 检查链式指针（仅当上游未断裂时）
            if prev_hash is not None and entry.get("prev") != prev_hash:
                errors.append({
                    "line": i,
                    "expected": f"prev={prev_hash}",
                    "actual": f"prev={entry.get('prev', '?')}"
                })

            # 重算哈希（独立验证，不依赖 prev_hash 状态）
            stored_hash = entry.pop("hash", "")
            entry_str = json.dumps(entry, ensure_ascii=False, sort_keys=True)
            computed = _compute_hash(entry_str)
            if stored_hash != computed:
                errors.append({
                    "line": i,
                    "expected": f"hash={computed}",
                    "actual": f"hash={stored_hash}"
                })
            entry["hash"] = stored_hash
            # 只要本行 hash 有效就可以把链续上（即使上游断过）
            prev_hash = stored_hash

    return {"ok": len(errors) == 0, "total": total, "errors": errors}


def tail(n: int = 20) -> list:
    """返回最近 n 条审计记录。"""
    if not os.path.exists(AUDIT_FILE):
        return []
    entries = []
    with open(AUDIT_FILE) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return entries[-n:]


def stats() -> dict:
    """统计概览。"""
    if not os.path.exists(AUDIT_FILE):
        return {"total": 0, "actors": {}, "actions": {}, "first": "", "last": ""}

    from collections import Counter
    actors = Counter()
    actions = Counter()
    first_ts = ""
    last_ts = ""
    total = 0

    with open(AUDIT_FILE) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            total += 1
            actors[entry.get("actor", "?")] += 1
            actions[entry.get("action", "?")] += 1
            ts = entry.get("ts", "")
            if not first_ts:
                first_ts = ts
            last_ts = ts

    return {
        "total": total,
        "actors": dict(actors.most_common()),
        "actions": dict(actions.most_common()),
        "first": first_ts,
        "last": last_ts,
    }


def main():
    parser = argparse.ArgumentParser(description="审计日志管理")
    parser.add_argument("--actor", help="操作者")
    parser.add_argument("--action", help="动作")
    parser.add_argument("--target", help="目标")
    parser.add_argument("--summary", default="", help="摘要")
    parser.add_argument("--verify", action="store_true", help="校验链式哈希完整性")
    parser.add_argument("--tail", type=int, nargs="?", const=20, help="查看最近 N 条（默认20）")
    parser.add_argument("--stats", action="store_true", help="统计概览")
    parser.add_argument("--json", action="store_true", help="JSON 输出")
    args = parser.parse_args()

    if args.verify:
        result = verify_chain()
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            if result["ok"]:
                print(f"✅ 审计日志完整（{result['total']} 条记录，链式哈希全部校验通过）")
            else:
                print(f"❌ 审计日志异常（{result['total']} 条记录，{len(result['errors'])} 处错误）")
                for e in result["errors"]:
                    print(f"  第 {e['line']} 行：期望 {e['expected']}，实际 {e['actual']}")
        sys.exit(0 if result["ok"] else 1)

    if args.tail is not None:
        entries = tail(args.tail)
        if args.json:
            print(json.dumps(entries, ensure_ascii=False, indent=2))
        else:
            for e in entries:
                print(f"[{e.get('ts', '?')}] {e.get('actor', '?'):12s} {e.get('action', '?'):8s} "
                      f"{e.get('target', '?'):25s} {e.get('summary', '')}")
        return

    if args.stats:
        s = stats()
        if args.json:
            print(json.dumps(s, ensure_ascii=False, indent=2))
        else:
            print(f"📊 审计日志统计")
            print(f"  总记录数: {s['total']}")
            print(f"  时间范围: {s['first']} → {s['last']}")
            print(f"  操作者: {s['actors']}")
            print(f"  动作: {s['actions']}")
        return

    if args.actor and args.action and args.target:
        audit(args.actor, args.action, args.target, args.summary)
        print("OK", file=sys.stderr)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
