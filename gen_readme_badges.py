#!/usr/bin/env python3
"""V37.9.99 README 徽章自动生成 — 事实单一来源 + 防漂移 (外部评审 P0).

═══════════════════════════════════════════════════════════════════════════
背景 (外部评审 2026-04-17 P0):
  README 徽章 (tests 数 / version / invariants+MR) 手工维护 → 每 session 漂移.
  实证: V37.9.52 加 doubao 时只改 README L3 漏改 6 处 "7 providers" (V37.9.70/71 修);
  本脚本上线时 README tests 徽章停在旧值, 实际已变 (test_count 每 session 变).

  本脚本让徽章从权威源**生成**而非手填, --check 模式接入 CI/full_regression
  做漂移守卫 (PR 级捕获), --write 模式一键同步.

═══════════════════════════════════════════════════════════════════════════
权威源 (single source of truth):
  - tests 数        ← status.json quality.test_count (full_regression 回写)
  - invariants/MR   ← ontology/governance_ontology.yaml audit_metadata
  - version 标签     ← CLAUDE.md line 3 "当前版本：vX / semver（date）"
  - semver          ← VERSION 文件
  - providers 数     ← providers.py --json (len) — 仅 --check 顾问式告警 (不重写散文)

管理的 README 行 (3 个可自动同步):
  1. tests 徽章      [![Tests](.../badge/tests-N%20passed-...)]()
  2. governance 徽章 [![Governance](.../badge/invariants-N%2FN%20%2B%20M%20MR-...)]()
  3. version 行       > **Current version:** `vX` / `semver` (date) — ...

用法:
  python3 gen_readme_badges.py            # 默认 --check (CI 用, 漂移则 exit 1)
  python3 gen_readme_badges.py --write    # 同步徽章到 README
  python3 gen_readme_badges.py --check    # 仅检查漂移 (exit 1 if drift)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys

_V37_9_99_MARKER = "V37.9.99 README 徽章自动生成"

_REPO = os.path.dirname(os.path.abspath(__file__))


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def compute_facts(repo_root=_REPO):
    """从权威源采集 README 徽章应显示的事实. 缺源即抛 (不静默产错徽章)."""
    facts = {}

    # version 标签 + date ← CLAUDE.md line 3
    claude = _read(os.path.join(repo_root, "CLAUDE.md"))
    m = re.search(r"当前版本：(v[0-9.]+)\s*/\s*([0-9.]+)（([0-9-]+)）", claude)
    if not m:
        raise ValueError("CLAUDE.md 第 3 行未找到 '当前版本：vX / semver（date）' 格式")
    facts["version_label"] = m.group(1)
    facts["claude_semver"] = m.group(2)
    facts["date"] = m.group(3)

    # semver ← VERSION 文件 (权威), 与 CLAUDE.md 交叉校验
    facts["semver"] = _read(os.path.join(repo_root, "VERSION")).strip()
    if facts["semver"] != facts["claude_semver"]:
        facts.setdefault("warnings", []).append(
            f"VERSION 文件 ({facts['semver']}) 与 CLAUDE.md semver "
            f"({facts['claude_semver']}) 不一致")

    # tests 数 ← status.json quality.test_count
    status = json.loads(_read(os.path.join(repo_root, "status.json")))
    tc = status.get("quality", {}).get("test_count")
    if tc is None:
        raise ValueError("status.json quality.test_count 缺失")
    facts["test_count"] = int(tc)

    # invariants / MR ← governance_ontology.yaml audit_metadata
    facts["invariants"], facts["meta_rules"] = _read_governance_counts(repo_root)

    # providers 数 ← providers.py --json (顾问式, 失败不致命)
    facts["providers"] = _provider_count(repo_root)

    return facts


def _read_governance_counts(repo_root):
    """读 audit_metadata.total_invariants + meta_rules (轻量 yaml 行扫, 不依赖 PyYAML)."""
    text = _read(os.path.join(repo_root, "ontology", "governance_ontology.yaml"))
    # 仅在 audit_metadata: 块内取 total_invariants / meta_rules 标量
    m = re.search(r"^audit_metadata:\s*$(.*?)(?=^\S|\Z)", text, re.MULTILINE | re.DOTALL)
    block = m.group(1) if m else text
    inv = re.search(r"^\s+total_invariants:\s*(\d+)", block, re.MULTILINE)
    mr = re.search(r"^\s+meta_rules:\s*(\d+)", block, re.MULTILINE)
    if not inv or not mr:
        raise ValueError("governance_ontology.yaml audit_metadata 缺 total_invariants/meta_rules")
    return int(inv.group(1)), int(mr.group(1))


def _provider_count(repo_root):
    """providers.py --json 的 provider 数 (顾问式, 失败返回 None 不致命)."""
    try:
        r = subprocess.run([sys.executable, os.path.join(repo_root, "providers.py"), "--json"],
                           capture_output=True, text=True, timeout=30)
        data = json.loads(r.stdout)
        return len(data) if isinstance(data, list) else None
    except Exception:
        return None


# ── 徽章渲染 (regex 替换, 仅改数字保留格式) ────────────────────────────
def _badge_substitutions(facts):
    """返回 [(描述, 正则, 替换函数)] — 每个管理一个 README 徽章."""
    inv = facts["invariants"]
    subs = [
        ("tests 徽章",
         re.compile(r"(badge/tests-)\d+(%20passed)"),
         lambda mm: f"{mm.group(1)}{facts['test_count']}{mm.group(2)}")
        ,
        ("governance 徽章",
         re.compile(r"(badge/invariants-)\d+%2F\d+(%20%2B%20)\d+(%20MR)"),
         lambda mm: f"{mm.group(1)}{inv}%2F{inv}{mm.group(2)}{facts['meta_rules']}{mm.group(3)}")
        ,
        ("version 行",
         re.compile(r"(> \*\*Current version:\*\* `)v[0-9.]+(` / `)[0-9.]+(` \()[0-9-]+(\))"),
         lambda mm: f"{mm.group(1)}{facts['version_label']}{mm.group(2)}{facts['semver']}{mm.group(3)}{facts['date']}{mm.group(4)}")
        ,
    ]
    # providers 徽章 — 仅当 providers.py --json 成功取到计数时管理 (FAIL-OPEN)
    if facts.get("providers"):
        subs.append((
            "providers 徽章",
            re.compile(r"(badge/providers-)\d+(%20supported)"),
            lambda mm: f"{mm.group(1)}{facts['providers']}{mm.group(2)}"))
    return subs


def apply_badges(readme_text, facts):
    """应用所有徽章替换, 返回 (新文本, [(描述, 是否改动)])."""
    out = readme_text
    results = []
    for desc, pat, repl in _badge_substitutions(facts):
        new, n = pat.subn(repl, out)
        if n == 0:
            results.append((desc, "NOT_FOUND"))
        elif new != out:
            results.append((desc, "CHANGED"))
            out = new
        else:
            results.append((desc, "OK"))
    return out, results


def main():
    parser = argparse.ArgumentParser(description="V37.9.99 README 徽章自动生成/漂移检查")
    parser.add_argument("--write", action="store_true", help="同步徽章到 README (默认 --check)")
    parser.add_argument("--check", action="store_true", help="仅检查漂移 (drift→exit 1)")
    parser.add_argument("--repo-root", default=_REPO)
    args = parser.parse_args()

    mode_write = args.write and not args.check

    try:
        facts = compute_facts(args.repo_root)
    except Exception as e:
        print(f"❌ 采集权威源失败: {e}", file=sys.stderr)
        sys.exit(2)

    readme_path = os.path.join(args.repo_root, "README.md")
    readme = _read(readme_path)
    new_readme, results = apply_badges(readme, facts)

    for desc, st in results:
        if st == "NOT_FOUND":
            print(f"⚠️  {desc}: 未在 README 找到 (格式可能变了)", file=sys.stderr)

    for w in facts.get("warnings", []):
        print(f"⚠️  {w}", file=sys.stderr)

    drifted = new_readme != readme

    print(f"事实: tests={facts['test_count']} invariants={facts['invariants']} "
          f"MR={facts['meta_rules']} version={facts['version_label']} "
          f"semver={facts['semver']} date={facts['date']} providers={facts['providers']}")

    if mode_write:
        if drifted:
            with open(readme_path, "w", encoding="utf-8") as f:
                f.write(new_readme)
            print("✅ README 徽章已同步 (--write)")
        else:
            print("✅ README 徽章已是最新 (无需改动)")
        sys.exit(0)

    # 默认 / --check: 漂移则 exit 1
    if drifted:
        print("❌ README 徽章漂移 (与权威源不符). 跑 `python3 gen_readme_badges.py --write` 同步.",
              file=sys.stderr)
        sys.exit(1)
    print("✅ README 徽章与权威源一致 (无漂移)")
    sys.exit(0)


if __name__ == "__main__":
    main()
