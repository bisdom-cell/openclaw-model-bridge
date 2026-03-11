#!/usr/bin/env python3
"""
gen_jobs_doc.py — 从 jobs_registry.yaml 自动生成定时任务文档片段
用法：python3 gen_jobs_doc.py [--check]
  默认模式：输出 markdown 表格到 stdout
  --check：对比 docs/config.md 中的任务表格，检测漂移（不修改文件）
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


def main():
    repo_root = os.path.dirname(os.path.abspath(__file__))
    registry_path = os.path.join(repo_root, "jobs_registry.yaml")

    if "--check" in sys.argv:
        config_path = os.path.join(repo_root, "docs", "config.md")
        drift = check_drift(registry_path, config_path)
        sys.exit(1 if drift > 0 else 0)
    else:
        print(generate_table(registry_path))


if __name__ == "__main__":
    main()
