#!/usr/bin/env python3
"""
governance_checker.py v2 — Ontology-Native 治理不变式执行引擎

从 governance_ontology.yaml 读取不变式和可执行检查，直接运行。
不依赖 adversarial_audit.py — 本体自身就是检查的完整来源。

检查类型：
  python_assert    — 在项目根目录执行 Python 代码，无异常 = pass
  file_contains    — 文件包含 pattern（正则）= pass
  file_not_contains — 文件不包含 pattern = pass
  env_var_exists   — bash -lc 环境变量非空 = pass（需 --full）
  command_succeeds — shell 命令 exit 0 = pass（需 --full）

用法：
  python3 ontology/governance_checker.py              # dev 模式
  python3 ontology/governance_checker.py --full        # Mac Mini
  python3 ontology/governance_checker.py --json        # JSON 输出
  python3 ontology/governance_checker.py --invariant INV-TOOL-001  # 单个
"""
import json
import os
import re
import subprocess
import sys
import textwrap

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ONTOLOGY_DIR = os.path.dirname(os.path.abspath(__file__))

FULL_MODE = "--full" in sys.argv
JSON_MODE = "--json" in sys.argv
SINGLE = None
for i, a in enumerate(sys.argv):
    if a == "--invariant" and i + 1 < len(sys.argv):
        SINGLE = sys.argv[i + 1]

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML required (pip install pyyaml)")
    sys.exit(1)


def _load():
    with open(os.path.join(_ONTOLOGY_DIR, "governance_ontology.yaml"), "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


# ═══════════════════════════════════════════════════════════════════════
# Check executors — one per check_type
# ═══════════════════════════════════════════════════════════════════════

def _exec_python_assert(check):
    """Execute Python code in project root context. No exception = pass."""
    code = check.get("code", "")
    old_cwd = os.getcwd()
    old_path = sys.path[:]
    try:
        os.chdir(_PROJECT_ROOT)
        if _PROJECT_ROOT not in sys.path:
            sys.path.insert(0, _PROJECT_ROOT)
        exec(compile(textwrap.dedent(code), f"<{check.get('name', 'check')}>", "exec"))
        return "pass", ""
    except AssertionError as e:
        return "fail", str(e)
    except Exception as e:
        return "error", f"{type(e).__name__}: {e}"
    finally:
        os.chdir(old_cwd)
        sys.path[:] = old_path


def _exec_file_contains(check):
    """Check that file contains pattern (regex)."""
    filepath = os.path.join(_PROJECT_ROOT, check.get("file", ""))
    pattern = check.get("pattern", "")
    if not os.path.exists(filepath):
        return "fail", f"文件不存在: {check.get('file')}"
    with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
        content = f.read()
    if re.search(pattern, content):
        return "pass", ""
    return "fail", f"'{pattern}' 不在 {check.get('file')} 中"


def _exec_file_not_contains(check):
    """Check that file does NOT contain pattern."""
    filepath = os.path.join(_PROJECT_ROOT, check.get("file", ""))
    pattern = check.get("pattern", "")
    if not os.path.exists(filepath):
        return "pass", ""  # file doesn't exist = pattern not in it
    with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
        content = f.read()
    if re.search(pattern, content):
        return "fail", f"'{pattern}' 不应出现在 {check.get('file')} 中但存在"
    return "pass", ""


def _exec_env_var_exists(check):
    """Check environment variable is set and non-empty via bash -lc."""
    if not FULL_MODE:
        return "skip", "需要 --full 模式"
    var = check.get("var", "")
    try:
        result = subprocess.run(
            ["bash", "-lc", f"echo ${{{var}:-}}"],
            capture_output=True, text=True, timeout=5
        )
        if result.stdout.strip():
            return "pass", ""
        return "fail", f"${var} 为空或未设置"
    except Exception as e:
        return "error", str(e)


def _exec_command_succeeds(check):
    """Run shell command, exit 0 = pass."""
    if not FULL_MODE and check.get("requires_full"):
        return "skip", "需要 --full 模式"
    cmd = check.get("command", "")
    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True,
            timeout=30, cwd=_PROJECT_ROOT
        )
        if result.returncode == 0:
            return "pass", ""
        return "fail", f"exit {result.returncode}: {result.stderr[:200]}"
    except Exception as e:
        return "error", str(e)


EXECUTORS = {
    "python_assert": _exec_python_assert,
    "file_contains": _exec_file_contains,
    "file_not_contains": _exec_file_not_contains,
    "env_var_exists": _exec_env_var_exists,
    "command_succeeds": _exec_command_succeeds,
}


# ═══════════════════════════════════════════════════════════════════════
# Runner
# ═══════════════════════════════════════════════════════════════════════

def run_invariant(inv):
    """Run all checks for one invariant. Return (status, check_results)."""
    checks = inv.get("checks", [])
    check_results = []
    worst = "pass"

    for check in checks:
        ct = check.get("check_type", "")
        if check.get("requires_full") and not FULL_MODE:
            check_results.append({"name": check.get("name"), "status": "skip", "message": "需要 --full"})
            continue

        executor = EXECUTORS.get(ct)
        if not executor:
            check_results.append({"name": check.get("name"), "status": "error", "message": f"未知 check_type: {ct}"})
            worst = "error"
            continue

        status, message = executor(check)
        check_results.append({"name": check.get("name"), "status": status, "message": message})

        if status == "fail" and worst != "error":
            worst = "fail"
        elif status == "error":
            worst = "error"

    return worst, check_results


def run_all(data):
    invariants = data.get("invariants", [])
    results = []

    for inv in invariants:
        inv_id = inv.get("id", "?")
        if SINGLE and inv_id != SINGLE:
            continue

        status, check_results = run_invariant(inv)
        results.append({
            "id": inv_id,
            "name": inv.get("name", "?"),
            "severity": inv.get("severity", "medium"),
            "declaration": inv.get("declaration", ""),
            "status": status,
            "checks": check_results,
            "total_checks": len(check_results),
            "passed_checks": sum(1 for c in check_results if c["status"] == "pass"),
            "meta_rule": inv.get("meta_rule", ""),
        })

    return results


def print_results(results):
    if JSON_MODE:
        print(json.dumps(results, indent=2, ensure_ascii=False))
        return 0

    sev_icons = {"critical": "🔴", "high": "🟠", "medium": "🟡"}
    status_icons = {"pass": "✅", "fail": "❌", "skip": "⏭ ", "error": "💥"}

    print("=" * 70)
    print("  GOVERNANCE CHECKER v2 — Ontology-Native 执行引擎")
    print(f"  模式: {'FULL (Mac Mini)' if FULL_MODE else 'DEV (repo only)'}")
    print("=" * 70)

    total_checks = 0
    passed_checks = 0
    failed_invs = 0

    for r in results:
        icon = status_icons.get(r["status"], "?")
        sev = sev_icons.get(r["severity"], "")
        print(f"\n  {icon} {sev} [{r['id']}] {r['name']}")
        print(f"     声明: {r['declaration'][:70]}")

        for c in r["checks"]:
            ci = status_icons.get(c["status"], "?")
            total_checks += 1
            if c["status"] == "pass":
                passed_checks += 1
                print(f"       {ci} {c['name']}")
            elif c["status"] == "skip":
                print(f"       {ci} {c['name']} ({c['message']})")
            else:
                print(f"       {ci} {c['name']}")
                if c["message"]:
                    print(f"          → {c['message']}")

        if r["status"] == "fail":
            failed_invs += 1

    # Summary
    mr_used = set(r["meta_rule"] for r in results if r["meta_rule"])
    skipped = sum(1 for r in results for c in r["checks"] if c["status"] == "skip")
    executed = total_checks - skipped

    print()
    print("─" * 70)
    print(f"  不变式: {len(results)} | 检查: {executed} 执行, {skipped} 跳过")
    print(f"  通过: {passed_checks}/{executed} checks | 元规则: {len(mr_used)}/5")

    if failed_invs:
        print(f"\n  ❌ {failed_invs} 个不变式被违反")
    else:
        print(f"\n  ✅ 所有不变式成立")
    print("=" * 70)

    return failed_invs


if __name__ == "__main__":
    data = _load()
    results = run_all(data)
    fails = print_results(results)
    sys.exit(1 if fails else 0)
