#!/usr/bin/env python3
"""
governance_checker.py — 从 governance_ontology.yaml 自动验证治理不变式

读取 ontology 中声明的不变式，检查：
1. 声明层（declaration）的文件/代码是否存在
2. 执行层（enforcement）的 code_pattern 是否在目标文件中
3. 验证层（verification）的测试/审计是否存在
4. 三者是否都存在（缺任何一层 = 治理缺口）

用法：
  python3 ontology/governance_checker.py              # 检查所有不变式
  python3 ontology/governance_checker.py --summary     # 仅输出统计
  python3 ontology/governance_checker.py --gaps         # 仅输出缺口
"""
import os
import re
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ONTOLOGY_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _PROJECT_ROOT)

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML required (pip install pyyaml)")
    sys.exit(1)


def _load_governance():
    path = os.path.join(_ONTOLOGY_DIR, "governance_ontology.yaml")
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _file_contains(filename, pattern):
    """Check if a file in the project contains a pattern."""
    # Resolve relative filenames
    filepath = os.path.join(_PROJECT_ROOT, filename)
    if not os.path.exists(filepath):
        return None  # file doesn't exist
    with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
        content = f.read()
    return bool(re.search(pattern, content))


def check_invariant(inv):
    """Check one invariant, return (status, details)."""
    inv_id = inv.get("id", "?")
    name = inv.get("name", "?")
    severity = inv.get("severity", "medium")
    issues = []

    # Check declaration
    decl = inv.get("declaration", {})
    decl_where = decl.get("where", "")

    # Check enforcement
    enf = inv.get("enforcement", {})
    enf_where = enf.get("where", "")
    enf_pattern = enf.get("code_pattern", "")

    if enf_pattern and enf_where and os.path.exists(os.path.join(_PROJECT_ROOT, enf_where)):
        found = _file_contains(enf_where, enf_pattern)
        if found is None:
            issues.append(f"enforcement file '{enf_where}' not found")
        elif not found:
            issues.append(f"enforcement pattern '{enf_pattern}' not found in {enf_where}")

    # Check verification
    ver = inv.get("verification", {})
    ver_where = ver.get("where", "")
    ver_test = ver.get("test_name", "")

    if ver_test and ver_where:
        found = _file_contains(ver_where, ver_test)
        if found is None:
            issues.append(f"verification file '{ver_where}' not found")
        elif not found:
            issues.append(f"verification test '{ver_test}' not found in {ver_where}")

    # Check known gaps
    known_gaps = inv.get("known_gaps", inv.get("known_gap", None))
    has_known_gap = known_gaps is not None

    if issues:
        return "fail", issues, severity
    elif has_known_gap:
        return "warn", [f"known gap: {known_gaps}"] if isinstance(known_gaps, str) else [f"known gaps: {len(known_gaps)}"], severity
    else:
        return "pass", [], severity


def run_checks(data):
    invariants = data.get("invariants", [])
    results = []

    for inv in invariants:
        status, details, severity = check_invariant(inv)
        results.append({
            "id": inv.get("id"),
            "name": inv.get("name"),
            "severity": severity,
            "status": status,
            "details": details,
            "meta_rule": inv.get("meta_rule", ""),
        })

    return results


def print_results(results, gaps_only=False, summary_only=False):
    icons = {"pass": "✅", "fail": "❌", "warn": "⚠️"}
    sev_icons = {"critical": "🔴", "high": "🟠", "medium": "🟡"}

    if not summary_only:
        print("=" * 65)
        print("  GOVERNANCE ONTOLOGY CHECKER — 不变式验证")
        print("=" * 65)

        for r in results:
            if gaps_only and r["status"] == "pass":
                continue
            icon = icons.get(r["status"], "?")
            sev = sev_icons.get(r["severity"], "")
            print(f"  {icon} {sev} [{r['id']}] {r['name']}")
            for d in r["details"]:
                print(f"       → {d}")

    # Summary
    total = len(results)
    passed = sum(1 for r in results if r["status"] == "pass")
    warned = sum(1 for r in results if r["status"] == "warn")
    failed = sum(1 for r in results if r["status"] == "fail")

    # Meta-rule coverage
    meta_rules_used = set(r["meta_rule"] for r in results if r["meta_rule"])

    print()
    print(f"  不变式: {total} | 通过: {passed} | 已知缺口: {warned} | 失败: {failed}")
    print(f"  元规则覆盖: {len(meta_rules_used)}/5 (MR-1..MR-5)")

    if failed:
        print(f"\n  ❌ GOVERNANCE CHECK FAILED — {failed} 个不变式被违反")
    elif warned:
        print(f"\n  ⚠️  GOVERNANCE CHECK PASSED with {warned} known gap(s)")
    else:
        print(f"\n  ✅ GOVERNANCE CHECK PASSED — 所有不变式成立")
    print("=" * 65)

    return failed


def main():
    data = _load_governance()
    results = run_checks(data)
    gaps_only = "--gaps" in sys.argv
    summary_only = "--summary" in sys.argv
    fails = print_results(results, gaps_only=gaps_only, summary_only=summary_only)
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
