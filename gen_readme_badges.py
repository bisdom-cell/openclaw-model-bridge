#!/usr/bin/env python3
"""V37.9.99 README 徽章 + V37.9.125 文档 header 统计 自动同步 — 事实单一来源 + 防漂移.

═══════════════════════════════════════════════════════════════════════════
背景:
  V37.9.99 (外部评审 2026-04-17 P0): README 徽章 (tests 数 / version / invariants+MR)
    手工维护 → 每 session 漂移. 实证: V37.9.52 加 doubao 时只改 README L3 漏改 6 处
    "7 providers" (V37.9.70/71 修); 本脚本上线时 README tests 徽章停在旧值.
  V37.9.125 (日落法 root-cause, 原则 #34): docs/FEATURES.md / config.md /
    ontology_engine_packaging.md 的 header 摘要行统计 (tests/suites/inv/MR/checks/
    MRD/providers/cases/security/governance 版本) 仍是**手维护** — 2026-06-08
    "GitHub 全面文档刷新" 就是手动改这些 drift 数字. 把它们也纳入自动同步,
    root-cause 杀掉递归 doc-drift 维护负担 (手动同步 → 机器同步, 一物一形).

  本脚本让徽章 + doc header 统计从权威源**生成**而非手填, --check 模式接入
  CI/full_regression 做漂移守卫 (PR 级捕获), --write 模式一键同步.

═══════════════════════════════════════════════════════════════════════════
权威源 (single source of truth):
  - tests 数        ← status.json quality.test_count (full_regression 回写)
  - test_suites     ← status.json quality.test_suites
  - security 分      ← status.json quality.security_score
  - invariants/MR   ← governance_ontology.yaml audit_metadata.total_invariants/meta_rules
  - governance 检查   ← audit_metadata.total_checks
  - governance 版本   ← audit_metadata.version
  - MRD 扫描器       ← governance_ontology.yaml meta_rule_discovery 条目数
  - cases 数         ← ontology/docs/cases/*.md 文件数
  - version 标签     ← CLAUDE.md line 3 "当前版本：vX / semver（date）"
  - semver          ← VERSION 文件
  - providers 数     ← providers.py --json (len, 顾问式不致命)

管理的目标 (从权威源自动同步):
  README.md:
    1. tests 徽章 / 2. governance 徽章 / 3. version 行 / 4. providers 徽章
  docs/FEATURES.md L3 摘要行:
    version+date / tests / suites / providers / invariants / meta-rules /
    checks / MRD scanners / security / cases
  docs/config.md L4 摘要行:
    version+date / tests / suites / governance checks / invariants / meta rules /
    MRD scanners / providers / 安全 / VERSION semver / governance_ontology.yaml 版本
  docs/ontology_engine_packaging.md L5 "当前基础" 行:
    invariants / meta rules / governance 版本

  注: FEATURES/packaging 的 point-in-time 标记 (如 chunk 完成版本) 有意不同步
  (那是历史快照非当前统计). 各统计在其摘要行内恰好出现一次 (已核验), token
  替换 bound 到摘要行 (防未来 body 碰撞).

用法:
  python3 gen_readme_badges.py            # 默认 --check (CI 用, 漂移则 exit 1)
  python3 gen_readme_badges.py --write    # 同步徽章 + doc header 到磁盘
  python3 gen_readme_badges.py --check    # 仅检查漂移 (exit 1 if drift)
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import subprocess
import sys

_V37_9_99_MARKER = "V37.9.99 README 徽章自动生成"
_V37_9_125_MARKER = "V37.9.125 文档 header 统计自动同步"

_REPO = os.path.dirname(os.path.abspath(__file__))


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def compute_facts(repo_root=_REPO):
    """从权威源采集 README 徽章 + doc header 应显示的事实. 缺源即抛 (不静默产错)."""
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

    # tests / suites / security ← status.json quality
    status = json.loads(_read(os.path.join(repo_root, "status.json")))
    quality = status.get("quality", {})
    tc = quality.get("test_count")
    if tc is None:
        raise ValueError("status.json quality.test_count 缺失")
    facts["test_count"] = int(tc)
    ts = quality.get("test_suites")
    facts["test_suites"] = int(ts) if ts is not None else None
    sec = quality.get("security_score")
    facts["security_score"] = int(sec) if sec is not None else None

    # invariants / MR / checks / governance 版本 ← governance_ontology.yaml audit_metadata
    gov = _read_governance_meta(repo_root)
    facts["invariants"] = gov["invariants"]
    facts["meta_rules"] = gov["meta_rules"]
    facts["governance_checks"] = gov["checks"]
    facts["governance_version"] = gov["version"]

    # MRD 扫描器数 + cases 数 (FAIL-OPEN: 取不到返回 None, 不管理对应 token)
    facts["mrd_scanners"] = _count_mrd_scanners(repo_root)
    facts["cases"] = _count_cases(repo_root)

    # providers 数 ← providers.py --json (顾问式, 失败不致命)
    facts["providers"] = _provider_count(repo_root)

    return facts


def _read_governance_meta(repo_root):
    """读 audit_metadata.total_invariants/meta_rules/total_checks/version (轻量 yaml 行扫)."""
    text = _read(os.path.join(repo_root, "ontology", "governance_ontology.yaml"))
    # 仅在 audit_metadata: 块内取标量
    m = re.search(r"^audit_metadata:\s*$(.*?)(?=^\S|\Z)", text, re.MULTILINE | re.DOTALL)
    block = m.group(1) if m else text
    inv = re.search(r"^\s+total_invariants:\s*(\d+)", block, re.MULTILINE)
    mr = re.search(r"^\s+meta_rules:\s*(\d+)", block, re.MULTILINE)
    ch = re.search(r"^\s+total_checks:\s*(\d+)", block, re.MULTILINE)
    ver = re.search(r'^\s+version:\s*"?([0-9.]+)"?', block, re.MULTILINE)
    if not inv or not mr:
        raise ValueError("governance_ontology.yaml audit_metadata 缺 total_invariants/meta_rules")
    return {
        "invariants": int(inv.group(1)),
        "meta_rules": int(mr.group(1)),
        "checks": int(ch.group(1)) if ch else None,
        "version": ver.group(1) if ver else None,
    }


def _count_mrd_scanners(repo_root):
    """meta_rule_discovery 段内 '- id:' 条目数 (FAIL-OPEN: 段缺失返回 None)."""
    try:
        text = _read(os.path.join(repo_root, "ontology", "governance_ontology.yaml"))
        m = re.search(r"^meta_rule_discovery:\s*$(.*?)(?=^[a-zA-Z]\w*:|\Z)",
                      text, re.MULTILINE | re.DOTALL)
        if not m:
            return None
        return len(re.findall(r"^\s+-\s+id:", m.group(1), re.MULTILINE))
    except Exception:
        return None


def _count_cases(repo_root):
    """ontology/docs/cases/*.md 文件数 (FAIL-OPEN: 目录缺失返回 None)."""
    try:
        files = glob.glob(os.path.join(repo_root, "ontology", "docs", "cases", "*.md"))
        return len(files) if files else None
    except Exception:
        return None


def _provider_count(repo_root):
    """providers.py --json 的 provider 数 (顾问式, 失败返回 None 不致命)."""
    try:
        r = subprocess.run([sys.executable, os.path.join(repo_root, "providers.py"), "--json"],
                           capture_output=True, text=True, timeout=30)
        data = json.loads(r.stdout)
        return len(data) if isinstance(data, list) else None
    except Exception:
        return None


# ── README 徽章渲染 (regex 替换, 仅改数字保留格式) ──────────────────────
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


# ── doc header 摘要行同步 (V37.9.125) ─────────────────────────────────────
def _doc_header_specs(facts):
    """返回 [(rel_path, anchor_regex, [(描述, token_pattern_str, repl_str)])].

    anchor_regex 匹配整行摘要行 (group(0)=整行); token 在该行内替换 (每个恰好 1 次,
    已核验). repl_str 为字面量 (无 backref) — 用函数包装避免 re 解释. 跳过取不到的
    FAIL-OPEN 事实 (mrd_scanners/cases/providers/security 可能 None).
    """
    vl, date, semver = facts["version_label"], facts["date"], facts["semver"]
    inv, mr = facts["invariants"], facts["meta_rules"]
    checks, gv = facts.get("governance_checks"), facts.get("governance_version")
    tc, ts, sec = facts["test_count"], facts.get("test_suites"), facts.get("security_score")
    prov, cases, mrd = facts.get("providers"), facts.get("cases"), facts.get("mrd_scanners")

    specs = []

    # ── docs/FEATURES.md L3 摘要行 (ASCII 括号) ──
    features_tokens = [
        ("version+date", r"^> v[0-9.]+ \([0-9-]+\)", f"> {vl} ({date})"),
        ("tests",        r"\*\*[0-9]+ tests\*\*",     f"**{tc} tests**"),
        ("invariants",   r"\*\*[0-9]+ governance invariants", f"**{inv} governance invariants"),
        ("meta-rules",   r"[0-9]+ meta-rules",        f"{mr} meta-rules"),
    ]
    if ts is not None:
        features_tokens.append(("suites", r"[0-9]+ suites", f"{ts} suites"))
    if prov:
        features_tokens.append(("providers", r"\*\*[0-9]+ providers\*\*", f"**{prov} providers**"))
    if checks is not None:
        features_tokens.append(("checks", r"[0-9]+ checks", f"{checks} checks"))
    if mrd is not None:
        features_tokens.append(("MRD scanners", r"[0-9]+ MRD scanners", f"{mrd} MRD scanners"))
    if sec is not None:
        features_tokens.append(("security", r"security [0-9]+/100", f"security {sec}/100"))
    if cases is not None:
        features_tokens.append(("cases", r"[0-9]+ blood-lesson case docs", f"{cases} blood-lesson case docs"))
    specs.append((
        "docs/FEATURES.md",
        re.compile(r"^> v[0-9.]+ \([0-9-]+\) \| \*\*[0-9]+ tests\*\*.*$", re.MULTILINE),
        features_tokens))

    # ── docs/config.md L4 摘要行 (全角括号 （）) ──
    config_tokens = [
        ("version+date", r"^> 版本：\*\*v[0-9.]+（[0-9-]+）\*\*", f"> 版本：**{vl}（{date}）**"),
        ("tests",        r"\*\*[0-9]+ tests",        f"**{tc} tests"),
        ("invariants",   r"[0-9]+ invariants",       f"{inv} invariants"),
        ("meta rules",   r"[0-9]+ meta rules",       f"{mr} meta rules"),
        ("VERSION semver", r"VERSION [0-9.]+ 不变",   f"VERSION {semver} 不变"),
    ]
    if ts is not None:
        config_tokens.append(("suites", r"[0-9]+ suites", f"{ts} suites"))
    if checks is not None:
        config_tokens.append(("governance checks", r"[0-9]+ governance checks", f"{checks} governance checks"))
    if mrd is not None:
        config_tokens.append(("MRD scanners", r"[0-9]+ MRD scanners", f"{mrd} MRD scanners"))
    if prov:
        config_tokens.append(("providers", r"[0-9]+ providers", f"{prov} providers"))
    if sec is not None:
        config_tokens.append(("安全", r"安全 [0-9]+/100", f"安全 {sec}/100"))
    if gv:
        config_tokens.append(("governance 版本",
                              r"governance_ontology\.yaml v[0-9.]+",
                              f"governance_ontology.yaml v{gv}"))
    specs.append((
        "docs/config.md",
        re.compile(r"^> 版本：\*\*v[0-9.]+（[0-9-]+）\*\*（\*\*[0-9]+ tests.*$", re.MULTILINE),
        config_tokens))

    # ── docs/ontology_engine_packaging.md L5 "当前基础" 行 ──
    pkg_tokens = [
        ("invariants", r"[0-9]+ 不变式", f"{inv} 不变式"),
        ("meta rules", r"[0-9]+ 元规则", f"{mr} 元规则"),
    ]
    if gv:
        pkg_tokens.append(("governance 版本", r"governance v[0-9.]+", f"governance v{gv}"))
    specs.append((
        "docs/ontology_engine_packaging.md",
        re.compile(r"^> 当前基础: .*[0-9]+ 不变式.*$", re.MULTILINE),
        pkg_tokens))

    return specs


def _apply_one_doc(text, anchor_re, tokens):
    """在 text 内定位 anchor 摘要行, 对该行应用 token 替换. 返回 (新文本, [(描述, 状态)])."""
    m = anchor_re.search(text)
    if not m:
        return text, [("(anchor)", "ANCHOR_NOT_FOUND")]
    line = m.group(0)
    new_line = line
    results = []
    for desc, pat, repl_str in tokens:
        new2, n = re.subn(pat, lambda mm, _r=repl_str: _r, new_line)
        if n == 0:
            results.append((desc, "TOKEN_NOT_FOUND"))
        elif new2 != new_line:
            results.append((desc, "CHANGED"))
            new_line = new2
        else:
            results.append((desc, "OK"))
    new_text = text[:m.start()] + new_line + text[m.end():] if new_line != line else text
    return new_text, results


def apply_doc_headers(repo_root, facts):
    """对 3 个 doc 应用 header 统计同步. 返回 {rel: (新文本或None, [(描述,状态)], 原文或None)}."""
    out = {}
    for rel, anchor_re, tokens in _doc_header_specs(facts):
        path = os.path.join(repo_root, rel)
        if not os.path.isfile(path):
            out[rel] = (None, [("(file)", "FILE_NOT_FOUND")], None)
            continue
        text = _read(path)
        new_text, results = _apply_one_doc(text, anchor_re, tokens)
        out[rel] = (new_text, results, text)
    return out


def main():
    parser = argparse.ArgumentParser(description="V37.9.99 README 徽章 + V37.9.125 doc header 自动同步/漂移检查")
    parser.add_argument("--write", action="store_true", help="同步徽章 + doc header 到磁盘 (默认 --check)")
    parser.add_argument("--check", action="store_true", help="仅检查漂移 (drift→exit 1)")
    parser.add_argument("--repo-root", default=_REPO)
    args = parser.parse_args()

    mode_write = args.write and not args.check

    try:
        facts = compute_facts(args.repo_root)
    except Exception as e:
        print(f"❌ 采集权威源失败: {e}", file=sys.stderr)
        sys.exit(2)

    # ── README 徽章 ──
    readme_path = os.path.join(args.repo_root, "README.md")
    readme = _read(readme_path)
    new_readme, badge_results = apply_badges(readme, facts)
    for desc, st in badge_results:
        if st == "NOT_FOUND":
            print(f"⚠️  README {desc}: 未找到 (格式可能变了)", file=sys.stderr)
    readme_drift = new_readme != readme

    # ── doc header 摘要行 ──
    doc_out = apply_doc_headers(args.repo_root, facts)
    doc_drift_files = []  # [(rel, path, new_text)]
    for rel, (new_text, results, orig) in doc_out.items():
        for desc, st in results:
            if st in ("ANCHOR_NOT_FOUND", "TOKEN_NOT_FOUND", "FILE_NOT_FOUND"):
                print(f"⚠️  {rel} {desc}: {st} (格式可能变了)", file=sys.stderr)
        if new_text is not None and orig is not None and new_text != orig:
            doc_drift_files.append((rel, os.path.join(args.repo_root, rel), new_text))

    for w in facts.get("warnings", []):
        print(f"⚠️  {w}", file=sys.stderr)

    any_drift = readme_drift or bool(doc_drift_files)

    print(f"事实: tests={facts['test_count']} suites={facts.get('test_suites')} "
          f"invariants={facts['invariants']} MR={facts['meta_rules']} "
          f"checks={facts.get('governance_checks')} gov_ver={facts.get('governance_version')} "
          f"mrd={facts.get('mrd_scanners')} cases={facts.get('cases')} "
          f"version={facts['version_label']} semver={facts['semver']} date={facts['date']} "
          f"security={facts.get('security_score')} providers={facts['providers']}")

    if mode_write:
        if readme_drift:
            with open(readme_path, "w", encoding="utf-8") as f:
                f.write(new_readme)
            print("✅ README 徽章已同步")
        for rel, path, new_text in doc_drift_files:
            with open(path, "w", encoding="utf-8") as f:
                f.write(new_text)
            print(f"✅ {rel} header 统计已同步")
        if not any_drift:
            print("✅ 已是最新 (无需改动)")
        sys.exit(0)

    # 默认 / --check: 漂移则 exit 1
    if any_drift:
        targets = []
        if readme_drift:
            targets.append("README.md")
        targets += [rel for rel, _, _ in doc_drift_files]
        print(f"❌ 统计漂移 (与权威源不符): {', '.join(targets)}. "
              f"跑 `python3 gen_readme_badges.py --write` 同步.", file=sys.stderr)
        sys.exit(1)
    print("✅ README 徽章 + doc header 统计与权威源一致 (无漂移)")
    sys.exit(0)


if __name__ == "__main__":
    main()
