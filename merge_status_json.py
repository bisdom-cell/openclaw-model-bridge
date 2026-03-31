#!/usr/bin/env python3
"""
merge_status_json.py — JSON-aware merge driver for status.json

Git 自定义合并驱动：当 status.json 冲突时，进行 JSON 级别合并而非文本行合并。

原理：
  - cron（main）更新的字段：updated, updated_by, health, kb_stats, stale_jobs, last_refresh
  - Claude Code（分支）更新的字段：priorities, recent_changes, quality, incidents, focus
  - 两者写不同的 JSON key，文本合并会冲突，但 JSON 合并可以无损合并

用法（git merge driver）：
  git config merge.json-status.driver "python3 merge_status_json.py %O %A %B"
  echo 'status.json merge=json-status' >> .gitattributes

也可手动调用：
  python3 merge_status_json.py ancestor.json ours.json theirs.json
  → 结果写入 ours.json（%A，in-place）

退出码：0=成功合并，1=合并失败（需手动解决）
"""
import json
import sys
import os


# cron 优先的字段（Mac Mini 实时数据比分支上的快照更新鲜）
CRON_PRIORITY_KEYS = {
    "updated", "updated_by", "health",
}

# Claude Code 优先的字段（开发决策比 cron 自动写入更权威）
CLAUDE_PRIORITY_KEYS = {
    "priorities", "quality", "incidents", "operating_rules",
    "methodology", "focus", "notes", "session_context",
}


def load_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, FileNotFoundError):
        return {}


def merge_arrays(ours_arr, theirs_arr, key_func=None):
    """合并两个数组，去重。"""
    if key_func is None:
        # 默认用 JSON 序列化去重
        key_func = lambda x: json.dumps(x, sort_keys=True, ensure_ascii=False)

    seen = {}
    result = []
    # theirs 先入（保持顺序）
    for item in theirs_arr:
        k = key_func(item)
        if k not in seen:
            seen[k] = True
            result.append(item)
    # ours 补充
    for item in ours_arr:
        k = key_func(item)
        if k not in seen:
            seen[k] = True
            result.append(item)
    return result


def merge_status(ancestor, ours, theirs):
    """JSON 级别合并 status.json。

    策略：
    1. cron 优先字段 → 取 theirs（main，最新实时数据）
    2. Claude Code 优先字段 → 取 ours（分支，开发决策）
    3. recent_changes → 合并去重（两边的变更记录都保留）
    4. feedback, preferences → 合并去重
    5. 其他字段 → 取 theirs（默认信任 main）
    """
    result = dict(theirs)  # 以 main 为基础

    # Claude Code 优先字段：用 ours 覆盖
    for key in CLAUDE_PRIORITY_KEYS:
        if key in ours:
            result[key] = ours[key]

    # cron 优先字段：保持 theirs（已在 result 基础中）

    # 数组合并字段
    for array_key in ("recent_changes", "feedback", "preferences"):
        ours_arr = ours.get(array_key, [])
        theirs_arr = theirs.get(array_key, [])
        if not ours_arr and not theirs_arr:
            continue

        if array_key == "recent_changes":
            # 按 date+what 去重，按日期倒序
            merged = merge_arrays(
                ours_arr, theirs_arr,
                key_func=lambda x: f"{x.get('date','')}__{x.get('what','')[:50]}"
            )
            result[array_key] = sorted(
                merged,
                key=lambda x: x.get("date", ""),
                reverse=True
            )[:15]  # 保留最近 15 条
        else:
            result[array_key] = merge_arrays(ours_arr, theirs_arr)

    return result


def main():
    if len(sys.argv) != 4:
        print(f"Usage: {sys.argv[0]} <ancestor> <ours> <theirs>", file=sys.stderr)
        print("  Git merge driver: merges status.json at JSON level", file=sys.stderr)
        sys.exit(1)

    ancestor_path, ours_path, theirs_path = sys.argv[1], sys.argv[2], sys.argv[3]

    ancestor = load_json(ancestor_path)
    ours = load_json(ours_path)
    theirs = load_json(theirs_path)

    try:
        merged = merge_status(ancestor, ours, theirs)

        # 写回 ours 文件（git merge driver 约定）
        with open(ours_path, "w") as f:
            json.dump(merged, f, indent=2, ensure_ascii=False)
            f.write("\n")

        sys.exit(0)  # 成功
    except Exception as e:
        print(f"JSON merge failed: {e}", file=sys.stderr)
        sys.exit(1)  # 失败，git 回退到文本合并


if __name__ == "__main__":
    main()
