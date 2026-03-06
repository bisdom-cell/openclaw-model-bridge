#!/usr/bin/env python3
"""
check_registry.py — V27 任务注册表校验器
用法：python3 check_registry.py [path_to_yaml]

检查项：
  1. YAML 可解析
  2. 所有 id 唯一
  3. 所有 entry 路径存在（相对于仓库根目录）
  4. 启用任务有 scheduler / interval / log / description
  5. scheduler 值合法
"""
import os
import sys

# 尝试用 PyYAML，回退到手动解析
try:
    import yaml
    def load_yaml(path):
        with open(path) as f:
            return yaml.safe_load(f)
except ImportError:
    # 最小 YAML 解析器（仅处理本项目的简单格式）
    import json
    import re

    def load_yaml(path):
        """极简 YAML 解析，仅支持 jobs_registry.yaml 的扁平格式。"""
        with open(path) as f:
            lines = f.readlines()

        result = {"version": 1, "jobs": []}
        current = None
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if stripped.startswith("- id:"):
                if current:
                    result["jobs"].append(current)
                current = {"id": stripped.split(":", 1)[1].strip()}
            elif current and ":" in stripped:
                key, val = stripped.split(":", 1)
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                if val.lower() == "true":
                    val = True
                elif val.lower() == "false":
                    val = False
                current[key] = val
            elif stripped.startswith("version:"):
                result["version"] = int(stripped.split(":")[1].strip())
        if current:
            result["jobs"].append(current)
        return result


VALID_SCHEDULERS = {"system", "openclaw"}
REQUIRED_FIELDS = {"id", "scheduler", "entry", "enabled"}
REQUIRED_WHEN_ENABLED = {"log", "description"}


def validate(path):
    errors = []
    warnings = []

    # 1. Parse
    try:
        data = load_yaml(path)
    except Exception as e:
        errors.append(f"YAML parse error: {e}")
        return errors, warnings

    jobs = data.get("jobs", [])
    if not jobs:
        errors.append("No jobs found in registry")
        return errors, warnings

    # 2. Unique IDs
    ids = [j.get("id", "<missing>") for j in jobs]
    seen = set()
    for jid in ids:
        if jid in seen:
            errors.append(f"Duplicate ID: {jid}")
        seen.add(jid)

    # Resolve repo root (directory containing this script)
    repo_root = os.path.dirname(os.path.abspath(path))

    for j in jobs:
        jid = j.get("id", "<unknown>")
        prefix = f"[{jid}]"

        # 3. Required fields
        for field in REQUIRED_FIELDS:
            if field not in j:
                errors.append(f"{prefix} missing field: {field}")

        # 4. Scheduler valid
        sched = j.get("scheduler", "")
        if sched and sched not in VALID_SCHEDULERS:
            errors.append(f"{prefix} invalid scheduler: {sched!r} (valid: {VALID_SCHEDULERS})")

        # 5. Entry path exists
        entry = j.get("entry", "")
        if entry:
            entry_path = os.path.join(repo_root, entry)
            if not os.path.exists(entry_path):
                warnings.append(f"{prefix} entry not found: {entry} (checked: {entry_path})")

        # 6. Enabled jobs need extra fields
        if j.get("enabled"):
            for field in REQUIRED_WHEN_ENABLED:
                if not j.get(field):
                    warnings.append(f"{prefix} enabled but missing: {field}")

            # system scheduler should have interval
            if sched == "system" and not j.get("interval"):
                warnings.append(f"{prefix} system job without interval")

    return errors, warnings


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "jobs_registry.yaml"
    if not os.path.exists(path):
        print(f"ERROR: {path} not found")
        sys.exit(1)

    errors, warnings = validate(path)

    if warnings:
        for w in warnings:
            print(f"  WARN: {w}")
    if errors:
        for e in errors:
            print(f"  ERROR: {e}")
        print(f"\nFAILED: {len(errors)} error(s), {len(warnings)} warning(s)")
        sys.exit(1)
    else:
        total = len(load_yaml(path).get("jobs", []))
        print(f"OK: {total} jobs validated, {len(warnings)} warning(s)")
        sys.exit(0)


if __name__ == "__main__":
    main()
