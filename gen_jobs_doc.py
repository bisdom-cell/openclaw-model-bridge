#!/usr/bin/env python3
"""
gen_jobs_doc.py — 从 jobs_registry.yaml 自动生成定时任务文档片段
用法：python3 gen_jobs_doc.py [--check] [--fix]
  默认模式：输出 markdown 表格到 stdout
  --check：对比 docs/config.md 中的任务表格，检测漂移（不修改文件）
  --fix：检测漂移后自动修复 docs/config.md 中的任务表格
"""
import os
import sys

# Reuse the existing YAML loader from check_registry
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from check_registry import load_yaml


def generate_table(registry_path="jobs_registry.yaml"):
    """Generate a markdown table from jobs_registry.yaml."""
    data = load_yaml(registry_path)
    if not data:
        return "ERROR: 无法解析 jobs_registry.yaml"

    jobs = data.get("jobs", [])
    if not jobs:
        return "ERROR: 注册表中无任务"

    lines = []
    lines.append("| ID | 调度器 | 触发时间 | 脚本 | 状态 | 说明 |")
    lines.append("|------|--------|----------|------|------|------|")

    for j in jobs:
        jid = j.get("id", "?")
        scheduler = j.get("scheduler", "?")
        interval = j.get("interval", "-")
        entry = j.get("entry", "-")
        enabled = j.get("enabled", False)
        desc = j.get("description", "-")

        status = "✅" if enabled else "❌ 已废弃"
        lines.append(f"| {jid} | {scheduler} | `{interval}` | `{entry}` | {status} | {desc} |")

    return "\n".join(lines)


def check_drift(registry_path="jobs_registry.yaml", config_path="docs/config.md"):
    """Check if docs/config.md job list is out of sync with registry."""
    if not os.path.exists(config_path):
        print(f"WARN: {config_path} 不存在，跳过漂移检测")
        return 0

    data = load_yaml(registry_path)
    if not data:
        print("ERROR: 无法解析 jobs_registry.yaml")
        return 1

    with open(config_path) as f:
        config_content = f.read()

    jobs = data.get("jobs", [])
    drift_count = 0

    for j in jobs:
        jid = j.get("id", "")
        entry = j.get("entry", "")
        enabled = j.get("enabled", False)
        script_name = os.path.basename(entry) if entry else ""

        # Check if enabled jobs are mentioned in config
        if enabled and script_name and script_name not in config_content:
            print(f"  DRIFT: [{jid}] 脚本 '{script_name}' 在 registry 中已启用但 config.md 未提及")
            drift_count += 1

        # Check if disabled jobs are still marked as active in config
        if not enabled and jid and f"| {jid}" in config_content:
            # Look for the job being marked active (✅) in config when it's disabled
            import re
            pattern = rf"\|\s*{re.escape(jid)}.*?✅"
            if re.search(pattern, config_content):
                print(f"  DRIFT: [{jid}] 在 registry 中已废弃但 config.md 仍标记为 ✅")
                drift_count += 1

    if drift_count == 0:
        print("OK: 注册表与 config.md 任务列表一致")
    else:
        print(f"\nDRIFT: 发现 {drift_count} 处不一致")

    return drift_count


def fix_drift(registry_path="jobs_registry.yaml", config_path="docs/config.md"):
    """Detect drift and auto-fix docs/config.md job table."""
    drift = check_drift(registry_path, config_path)
    if drift == 0:
        return 0

    data = load_yaml(registry_path)
    if not data:
        return 1

    with open(config_path, encoding="utf-8") as f:
        content = f.read()

    jobs = data.get("jobs", [])

    # Build set of enabled jobs from registry
    enabled_jobs = {}
    for j in jobs:
        if j.get("enabled", False):
            entry = j.get("entry", "")
            script_name = os.path.basename(entry) if entry else ""
            enabled_jobs[j.get("id", "")] = {
                "id": j.get("id", ""),
                "interval": j.get("interval", "-"),
                "entry": entry,
                "script_name": script_name,
                "description": j.get("description", "-"),
                "log": j.get("log", ""),
            }

    # Find missing jobs and append to the system crontab table
    import re
    lines = content.split("\n")
    # Find the last row of the system crontab table (lines starting with |)
    insert_idx = None
    for i, line in enumerate(lines):
        if line.startswith("| kb-") or line.startswith("| mm-") or line.startswith("|"):
            # Track the last table row in the system crontab section
            if "✅" in line or "❌" in line:
                insert_idx = i

    if insert_idx is None:
        print("WARN: 无法定位 config.md 中的任务表格，跳过自动修复")
        return drift

    added = 0
    for jid, info in enabled_jobs.items():
        sn = info["script_name"]
        if sn and sn not in content:
            # Append new row after the last table row
            entry_path = info["entry"].replace("jobs/", "~/").replace("/", "/")
            # Use the log path or derive from id
            log_path = info["log"] if info["log"] else f"~/{jid.replace('-', '_')}.log"
            new_row = f"| {jid} | {info['interval']} | `{info['entry']}` | `{log_path}` | ✅ 自动添加 | "
            insert_idx += 1
            lines.insert(insert_idx, new_row)
            print(f"  FIX: 添加 [{jid}] {sn} 到 config.md")
            added += 1

    # Fix disabled jobs still marked as ✅
    for j in jobs:
        if not j.get("enabled", False):
            jid = j.get("id", "")
            if jid:
                pattern = rf"(\|\s*{re.escape(jid)}.*?)✅"
                replacement = rf"\g<1>❌ 已废弃"
                new_content = "\n".join(lines)
                if re.search(pattern, new_content):
                    new_content = re.sub(pattern, replacement, new_content)
                    lines = new_content.split("\n")
                    print(f"  FIX: [{jid}] 状态改为 ❌ 已废弃")
                    added += 1

    if added > 0:
        # Atomic write
        tmp = config_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        os.replace(tmp, config_path)
        print(f"\n✅ 已自动修复 {added} 处漂移")
    else:
        print("\n⚠️ 检测到漂移但无法自动修复（需手动更新）")

    return 0 if added > 0 else drift


def main():
    repo_root = os.path.dirname(os.path.abspath(__file__))
    registry_path = os.path.join(repo_root, "jobs_registry.yaml")

    if "--fix" in sys.argv:
        config_path = os.path.join(repo_root, "docs", "config.md")
        result = fix_drift(registry_path, config_path)
        sys.exit(result)
    elif "--check" in sys.argv:
        config_path = os.path.join(repo_root, "docs", "config.md")
        drift = check_drift(registry_path, config_path)
        sys.exit(1 if drift > 0 else 0)
    else:
        print(generate_table(registry_path))


if __name__ == "__main__":
    main()
