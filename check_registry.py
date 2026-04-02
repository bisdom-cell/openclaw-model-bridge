#!/usr/bin/env python3
"""
check_registry.py — V28 任务注册表校验器
用法：python3 check_registry.py [path_to_yaml]
      python3 check_registry.py --check-crontab   # 校验实际 crontab

检查项：
  1. YAML 可解析
  2. 所有 id 唯一
  3. 所有 entry 路径存在（相对于仓库根目录）
  4. 启用任务有 scheduler / interval / log / description
  5. scheduler 值合法
  6. [V28] --check-crontab: 对比实际 crontab 与 registry，发现缺失/引号错误
  7. [V28] FILE_MAP 完整性：检查仓库中可部署文件是否全部在 auto_deploy.sh 的 FILE_MAP 中
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
                val = val.strip()
                # 移除行内注释（# 后面的内容）
                if "#" in val:
                    val = val[:val.index("#")].strip()
                val = val.strip('"').strip("'")
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
VALID_TIERS = {"core", "auxiliary", "experiment"}
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

        # 4b. Tier valid (V32: Job 分层治理)
        tier = j.get("tier", "")
        if tier and tier not in VALID_TIERS:
            errors.append(f"{prefix} invalid tier: {tier!r} (valid: {VALID_TIERS})")
        if j.get("enabled") and not tier:
            warnings.append(f"{prefix} enabled but missing tier (default: auxiliary)")

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


def check_crontab(registry_path):
    """V28: 对比实际 crontab 与 registry，检查缺失条目和语法错误。"""
    import subprocess
    errors = []
    warnings = []

    # 读取实际 crontab
    try:
        result = subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=5)
        crontab_lines = result.stdout.strip().split("\n") if result.stdout.strip() else []
    except Exception as e:
        errors.append(f"无法读取 crontab: {e}")
        return errors, warnings

    # 读取 registry
    try:
        data = load_yaml(registry_path)
    except Exception as e:
        errors.append(f"YAML parse error: {e}")
        return errors, warnings

    active_lines = [l.strip() for l in crontab_lines if l.strip() and not l.strip().startswith("#")]

    # 检查1: 引号配对（所有 bash -lc 行必须有闭合单引号）
    for i, line in enumerate(active_lines):
        if "bash -lc" in line:
            # 提取 bash -lc 后面的部分
            after_lc = line.split("bash -lc", 1)[1]
            single_quotes = after_lc.count("'")
            if single_quotes % 2 != 0:
                errors.append(f"crontab 第{i+1}行: 单引号未闭合 — {line[:80]}...")

    # 检查2: registry 中 enabled=true 且 scheduler=system 的任务在 crontab 中有对应条目
    crontab_text = "\n".join(active_lines)
    for job in data.get("jobs", []):
        if not job.get("enabled") or job.get("scheduler") != "system":
            continue
        jid = job.get("id", "?")
        entry = job.get("entry", "")

        # 用脚本文件名作为匹配关键字
        script_name = os.path.basename(entry) if entry else ""
        if script_name and script_name not in crontab_text:
            # 也检查完整路径
            if entry not in crontab_text:
                warnings.append(f"[{jid}] registry 已启用但 crontab 中未找到 '{script_name}'")

    # 检查3: 重复条目检测
    seen_scripts = {}
    for line in active_lines:
        # 提取脚本名
        for part in line.split():
            if part.endswith(".sh") or part.endswith(".py"):
                base = os.path.basename(part)
                if base in seen_scripts:
                    warnings.append(f"crontab 疑似重复: '{base}' 出现在多个条目中")
                seen_scripts[base] = seen_scripts.get(base, 0) + 1
                break

    return errors, warnings


def check_filemap_completeness(registry_path):
    """V28: 检查仓库中可部署的脚本是否全部在 auto_deploy.sh FILE_MAP 中。"""
    import re
    errors = []
    warnings = []
    repo_root = os.path.dirname(os.path.abspath(registry_path))

    # 读取 auto_deploy.sh 中的 FILE_MAP
    deploy_script = os.path.join(repo_root, "auto_deploy.sh")
    if not os.path.exists(deploy_script):
        warnings.append("auto_deploy.sh 不存在，跳过 FILE_MAP 检查")
        return errors, warnings

    with open(deploy_script) as f:
        deploy_content = f.read()

    # 提取 FILE_MAP 中的源文件路径
    mapped_sources = set()
    for match in re.finditer(r'"([^"|]+)\|', deploy_content):
        mapped_sources.add(match.group(1))

    # 读取 registry 中所有 entry
    try:
        data = load_yaml(registry_path)
    except Exception:
        return errors, warnings

    for job in data.get("jobs", []):
        if not job.get("enabled"):
            continue
        entry = job.get("entry", "")
        jid = job.get("id", "?")
        if not entry:
            continue

        entry_path = os.path.join(repo_root, entry)
        if os.path.exists(entry_path) and entry not in mapped_sources:
            errors.append(f"[{jid}] '{entry}' 存在于仓库但不在 auto_deploy FILE_MAP 中（不会自动部署！）")

        # 检查 jobs/ 子目录下的关联文件（只检查子目录，跳过仓库根目录的开发工具）
        entry_dir = os.path.dirname(entry_path)
        entry_rel_dir = os.path.dirname(entry)
        if entry_rel_dir and entry_rel_dir != "." and os.path.isdir(entry_dir):
            for fname in os.listdir(entry_dir):
                rel_path = os.path.join(entry_rel_dir, fname)
                full_path = os.path.join(entry_dir, fname)
                if os.path.isfile(full_path) and rel_path not in mapped_sources:
                    if fname.endswith((".py", ".sh")) and not fname.startswith("."):
                        warnings.append(f"[{jid}] '{rel_path}' 未在 FILE_MAP 中（不会自动部署）")

    return errors, warnings


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "jobs_registry.yaml"

    # V28: --check-crontab 模式
    if "--check-crontab" in sys.argv:
        if not os.path.exists(path):
            path = "jobs_registry.yaml"
        print("=== Crontab 校验 ===")
        errors, warnings = check_crontab(path)
        for w in warnings:
            print(f"  WARN: {w}")
        for e in errors:
            print(f"  ERROR: {e}")
        if not errors and not warnings:
            print("  OK: crontab 与 registry 一致")

        print("\n=== FILE_MAP 完整性 ===")
        e2, w2 = check_filemap_completeness(path)
        for w in w2:
            print(f"  WARN: {w}")
        for e in e2:
            print(f"  ERROR: {e}")
        if not e2 and not w2:
            print("  OK: 所有可部署文件已在 FILE_MAP 中")

        total_errors = len(errors) + len(e2)
        if total_errors:
            print(f"\nFAILED: {total_errors} error(s)")
            sys.exit(1)
        else:
            print(f"\nOK: crontab + FILE_MAP 检查通过")
            sys.exit(0)

    # 原有 registry 校验逻辑
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
