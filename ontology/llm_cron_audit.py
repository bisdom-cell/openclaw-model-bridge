#!/usr/bin/env python3
"""ontology/llm_cron_audit.py — V37.9.38 LLM cron fail-fast 合规扫描器

血案背景：V37.9.36 rss_blogs 18:00 cron 推送 3 篇全部硬编码占位符
"要点：技术深度文章 / 价值：⭐⭐⭐"，根因是 LLM 双 provider 故障 +
脚本 silent fallback 占位符（MR-4 silent-failure 第 26 次演出）。

V37.9.36 教训：扫所有调 LLM 的 cron job 是否对齐 fail-fast 模式
（[SYSTEM_ALERT] + status:llm_failed + exit 1 + 无占位符 fallback）。
MR-8 兑现：跨 job 不同步 fail-fast 升级 = copy-paste-is-a-bug-class。

本模块两大功能：
  1. ``audit_script(path) -> ComplianceReport``：单脚本合规检查
  2. ``audit_all() -> dict``：批量扫所有 LLM cron 候选，生成报告

CLI 模式：
  python3 ontology/llm_cron_audit.py --report           # markdown 报告
  python3 ontology/llm_cron_audit.py --report --json    # JSON 输出
  python3 ontology/llm_cron_audit.py --check            # MRD 模式 (exit 1 if violations)
  python3 ontology/llm_cron_audit.py --check --strict   # 排除 V37.9.39+ 已知豁免
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field, asdict


# ── 配置：候选脚本列表 ───────────────────────────────────────────────────
LLM_CRON_CANDIDATES = (
    "jobs/arxiv_monitor/run_arxiv.sh",
    "jobs/hf_papers/run_hf_papers.sh",
    "jobs/semantic_scholar/run_semantic_scholar.sh",
    "jobs/dblp/run_dblp.sh",
    "jobs/acl_anthology/run_acl_anthology.sh",
    "jobs/github_trending/run_github_trending.sh",
    "jobs/rss_blogs/run_rss_blogs.sh",
    "jobs/ai_leaders_x/run_ai_leaders_x.sh",
    "jobs/karpathy_x/run_karpathy_x.sh",
    "jobs/freight_watcher/run_freight.sh",
    "jobs/openclaw_official/run.sh",
    "jobs/openclaw_official/run_discussions.sh",
    "jobs/ontology_sources/run_ontology_sources.sh",
    "jobs/chaspark/run_chaspark.sh",
    "jobs/finance_news/run_finance_news.sh",
    "kb_evening.sh",
    "kb_review.sh",
    "kb_deep_dive.sh",
    "kb_inject.sh",
    "kb_dream.sh",
    "run_hn_fixed.sh",
)

# 已知占位符反模式
PLACEHOLDER_PATTERNS = (
    "贡献：AI领域相关研究",
    "价值：⭐⭐⭐⭐⭐",
    "价值：⭐⭐⭐⭐",
    "价值：⭐⭐⭐",
    "要点：技术深度文章",
    "新版本发布，建议关注",
    "技术深度文章",
)

COMPLIANCE_MARKERS = {
    "system_alert_string":  re.compile(r"\[SYSTEM_ALERT\]"),
    "source_notify":        re.compile(r"(?:source|\.)\s+(?:[\$\.\w/~_-]+/)?notify\.sh"),
    "send_alert_helper":    re.compile(r"\bsend_alert\b|\bnotify_alert\b"),
    "status_llm_failed":    re.compile(r'"?status"?\s*[:=]\s*"?(llm_failed|partial_degraded|all_failed_)'),
    "fail_fast_exit1":      re.compile(r"\[SYSTEM_ALERT\][^\n]*\n[^\n]*exit\s+1|exit\s+1\s*#.*alert|\|\|\s*exit\s+1"),
    "calls_llm":            re.compile(r"chat/completions|:5001|:5002|llm_call|call_llm"),
}


@dataclass
class PlaceholderFinding:
    line_no: int
    matched: str
    context: str


@dataclass
class ComplianceReport:
    path: str
    exists: bool = True
    calls_llm: bool = False
    has_system_alert: bool = False
    has_source_notify: bool = False
    has_send_alert: bool = False
    has_status_llm_failed: bool = False
    has_fail_fast_exit1: bool = False
    placeholder_findings: list = field(default_factory=list)
    compliance_score: int = 0
    aligned: bool = False
    aligned_version: str = ""

    def to_dict(self):
        d = asdict(self)
        d["placeholder_findings"] = [asdict(f) for f in self.placeholder_findings]
        return d


ALIGNED_SCRIPTS = {
    "jobs/rss_blogs/run_rss_blogs.sh":   "V37.9.51",  # V37.9.51 Sub-Stage 4b 1/6: 6 字段 + rule_check (V37.9.45 hf_papers / V37.9.50 semantic_scholar 同款模板)
    "kb_evening.sh":                     "V37.8.10",
    "kb_review.sh":                      "V37.5",
    "kb_deep_dive.sh":                   "V37.9.16",
    "jobs/semantic_scholar/run_semantic_scholar.sh": "V37.9.50",  # 6 字段 + alignment + rule_check (V37.9.45 hf_papers PoC 横向 Sub-Stage 4b 模板验证)
    "jobs/dblp/run_dblp.sh":             "V37.9.51",  # V37.9.51 Sub-Stage 4b 2/6: 6 字段 + rule_check (DBLP 无 abstract, rule_content 用 title + venue)
    "jobs/ai_leaders_x/run_ai_leaders_x.sh": "V37.9.51",  # V37.9.51 Sub-Stage 4b 5/6: 6 字段 + rule_check (tweet 场景, rule_content 用 author + text)
    "run_hn_fixed.sh":                   "V37.9.51",  # V37.9.51 Sub-Stage 4b 6/6: 6 字段 + rule_check (HN 场景, rule_content 用 title + desc 清理 HTML)
    "jobs/arxiv_monitor/run_arxiv.sh":   "V37.9.51",  # V37.9.51 Sub-Stage 4b 3/6: 6 字段 + rule_check (rule_content 用 title + abstract, V37.9.43 fallback 同款)
    "jobs/github_trending/run_github_trending.sh": "V37.9.51",  # V37.9.51 Sub-Stage 4b 4/6: 6 字段 + rule_check (repo 场景, rule_content 用 full_name + description + topics)
    "jobs/hf_papers/run_hf_papers.sh":   "V37.9.45",  # 6 字段 PoC (V37.9.43 arxiv 同款 + 加 🎚️ 项目对齐度, Opportunity Radar #2) + 保留 Step 2.5 GitHub repo enrichment
}


# ── 占位符扫描器（V37.9.38 重写：行级启发式 + prompt 模板豁免）───────────
def _is_prompt_template_line(line):
    """识别 LLM prompt 模板行（不是真实 fallback 赋值）

    模板行特征：含 LLM 指令性词组（"第N行" / "输出格式" / "1到5个" / "评估对" 等）
    或 prompt 内的范围说明（`⭐~⭐⭐⭐⭐⭐` 含波浪号表示评分范围）。
    """
    instr_phrases = (
        "第一行", "第二行", "第三行", "第四行", "第五行",
        "输出格式", "1到5个", "1 到 5 个", "保留原始来源",
        "评估对", "1句话", "每篇之间", "5个星", "1个星",
        "升级紧迫度", "(1到", "（1到", "评估其", "关键点评",
        "💡 价值", "💡价值",
    )
    if any(p in line for p in instr_phrases):
        return True
    # 范围说明：⭐~⭐⭐⭐⭐⭐ 或 ⭐～⭐⭐⭐⭐⭐ (含波浪号)
    if re.search(r"⭐\s*[~～]\s*⭐", line):
        return True
    return False


def find_placeholder_findings(src):
    """扫描占位符反模式 — 行级启发式

    策略：
      (1) 跳过纯注释行（# 开头）+ 空行
      (2) 跳过 Python 三引号块内的行（粗状态机）
      (3) 跳过 prompt 模板行（_is_prompt_template_line）
      (4) 命中条件：placeholder 被 quote 包裹（同行 quote+placeholder+quote）
                  OR shell 多行字符串闭合（行尾跟 quote）
                  OR Python 赋值开头形式（=\\s*"..placeholder..)

    设计取舍：宁愿少抓一个真 finding 也不要把整个 prompt 模板报告进来。
    """
    findings = []
    in_triple_dq = False
    in_triple_sq = False

    lines = src.split("\n")
    for i, line in enumerate(lines, 1):
        stripped = line.strip()

        # (1) 跳过纯注释 + 空行
        if not stripped or stripped.startswith("#"):
            continue

        # (2) 三引号状态机
        was_in_triple = in_triple_dq or in_triple_sq
        if line.count('"""') % 2 == 1:
            in_triple_dq = not in_triple_dq
        if line.count("'''") % 2 == 1:
            in_triple_sq = not in_triple_sq
        # 在 triple block 内（开始/中间/结束行都跳过保守处理）
        if was_in_triple or in_triple_dq or in_triple_sq:
            continue

        # (3) prompt 模板行豁免
        if _is_prompt_template_line(line):
            continue

        # (4) 占位符 + 字面量上下文判定
        for pat in PLACEHOLDER_PATTERNS:
            if pat not in line:
                continue
            esc = re.escape(pat)
            ctx_kind = None
            # (a) 同行 quote 包裹：'"..placeholder.."' 或 "'..placeholder..'"
            if re.search(r'"[^"]*' + esc + r'[^"]*"', line):
                ctx_kind = "quoted_inline_dq"
            elif re.search(r"'[^']*" + esc + r"[^']*'", line):
                ctx_kind = "quoted_inline_sq"
            # (b) shell 多行字符串闭合行：placeholder + 末尾的 closing quote
            elif re.search(r"^\s*" + esc + r'.*?["\']\s*$', line):
                ctx_kind = "shell_multiline_close"
            # (c) Python 赋值开头：name = "....placeholder
            elif re.search(r"=\s*[\"\'][^\n]*" + esc, line):
                ctx_kind = "py_assignment_open"
            # (d) shell 赋值开头（多行）：name="\n...\n placeholder
            #    这里靠 (b) 闭合行抓取，开头行单看不够信息
            if ctx_kind is None:
                continue
            findings.append(PlaceholderFinding(
                line_no=i,
                matched=pat,
                context=f"[{ctx_kind}] {stripped[:140]}",
            ))
            break  # 每行最多记一次

    return findings


def _normalize_path(path):
    """归一化候选路径，让 ALIGNED_SCRIPTS lookup 成功

    ALIGNED_SCRIPTS keys 都是仓库相对路径（"kb_review.sh" / "jobs/rss_blogs/...sh"）。
    audit_script() 可能传入：
      - 仓库相对：``jobs/rss_blogs/run_rss_blogs.sh``（直接匹配）
      - ``./`` 前缀：``./jobs/rss_blogs/run_rss_blogs.sh``（剥前缀）
      - 绝对路径：``/home/user/openclaw-model-bridge/jobs/...sh``（用 endswith 后缀匹配）
    """
    if path.startswith("./"):
        path = path[2:]
    if path in ALIGNED_SCRIPTS:
        return path
    # 绝对路径或非常规相对路径：用 endswith 匹配最长的 ALIGNED_SCRIPTS key
    best = None
    for key in ALIGNED_SCRIPTS:
        if path == key or path.endswith("/" + key):
            if best is None or len(key) > len(best):
                best = key
    return best if best else path


def audit_script(path):
    """单脚本合规扫描 → ComplianceReport"""
    rep = ComplianceReport(path=path)
    if not os.path.exists(path):
        rep.exists = False
        return rep
    with open(path, encoding="utf-8", errors="replace") as f:
        src = f.read()

    rep.calls_llm = bool(COMPLIANCE_MARKERS["calls_llm"].search(src))
    rep.has_system_alert = bool(COMPLIANCE_MARKERS["system_alert_string"].search(src))
    rep.has_source_notify = bool(COMPLIANCE_MARKERS["source_notify"].search(src))
    rep.has_send_alert = bool(COMPLIANCE_MARKERS["send_alert_helper"].search(src))
    rep.has_status_llm_failed = bool(COMPLIANCE_MARKERS["status_llm_failed"].search(src))
    rep.has_fail_fast_exit1 = bool(COMPLIANCE_MARKERS["fail_fast_exit1"].search(src))
    rep.placeholder_findings = find_placeholder_findings(src)

    rep.compliance_score = sum([
        rep.has_system_alert,
        rep.has_source_notify,
        rep.has_send_alert,
        rep.has_status_llm_failed,
        rep.has_fail_fast_exit1,
        len(rep.placeholder_findings) == 0,
    ])

    # 已对齐脚本特殊登记（路径归一化后比对）
    norm = _normalize_path(path)
    if norm in ALIGNED_SCRIPTS:
        rep.aligned = True
        rep.aligned_version = ALIGNED_SCRIPTS[norm]
    else:
        rep.aligned = (
            len(rep.placeholder_findings) == 0
            and rep.has_system_alert
            and rep.has_send_alert
            and rep.has_status_llm_failed
        )

    return rep


def audit_all(repo_root="."):
    return [audit_script(os.path.join(repo_root, c)) for c in LLM_CRON_CANDIDATES]


def format_markdown_report(reports):
    aligned = [r for r in reports if r.aligned]
    nonaligned = [r for r in reports if not r.aligned and r.exists]
    missing = [r for r in reports if not r.exists]
    total_findings = sum(len(r.placeholder_findings) for r in reports)

    lines = []
    lines.append("# V37.9.38 LLM Cron Fail-Fast Audit Report")
    lines.append("")
    lines.append("> 血案背景：V37.9.36 rss_blogs LLM 双 provider 故障 + 占位符 silent fallback 推送 3 篇 `要点：技术深度文章 / 价值：⭐⭐⭐` 给用户。本报告扫描所有 LLM cron 候选脚本是否对齐 V37.9.36-37 fail-fast 模式。")
    lines.append("")
    lines.append("## 概览")
    lines.append("")
    lines.append(f"- 候选脚本总数：{len(reports)}")
    lines.append(f"- ✅ 已对齐：{len(aligned)}（V37.5 / V37.8.10 / V37.9.16 / V37.9.36-37）")
    lines.append(f"- ❌ 未对齐：{len(nonaligned)}（含占位符或缺 fail-fast 标志）")
    lines.append(f"- ⚠️ 缺失文件：{len(missing)}")
    lines.append(f"- 📌 占位符 finding 总数：{total_findings}")
    lines.append("")

    lines.append("## ✅ 已对齐脚本（视为合规）")
    lines.append("")
    lines.append("| 脚本 | 对齐版本 | 占位符数 | SYSTEM_ALERT | send_alert | status:failed | exit 1 |")
    lines.append("|---|---|---|---|---|---|---|")
    for r in aligned:
        lines.append(
            f"| `{r.path}` | {r.aligned_version} | "
            f"{len(r.placeholder_findings)} | "
            f"{'✓' if r.has_system_alert else '✗'} | "
            f"{'✓' if r.has_send_alert else '✗'} | "
            f"{'✓' if r.has_status_llm_failed else '✗'} | "
            f"{'✓' if r.has_fail_fast_exit1 else '✗'} |"
        )
    lines.append("")

    lines.append("## ❌ 未对齐脚本（V37.9.38+ 修复目标）")
    lines.append("")
    lines.append("按占位符 finding 数降序排列（高 finding 数 = 高血案风险）。")
    lines.append("")
    lines.append("| 脚本 | LLM | SYSTEM_ALERT | source_notify | send_alert | status:failed | exit 1 | 占位符数 | 评分 |")
    lines.append("|---|:---:|:---:|:---:|:---:|:---:|:---:|---:|---:|")
    for r in sorted(nonaligned, key=lambda x: (-len(x.placeholder_findings), -x.compliance_score, x.path)):
        lines.append(
            f"| `{r.path}` | "
            f"{'✓' if r.calls_llm else '✗'} | "
            f"{'✓' if r.has_system_alert else '✗'} | "
            f"{'✓' if r.has_source_notify else '✗'} | "
            f"{'✓' if r.has_send_alert else '✗'} | "
            f"{'✓' if r.has_status_llm_failed else '✗'} | "
            f"{'✓' if r.has_fail_fast_exit1 else '✗'} | "
            f"{len(r.placeholder_findings)} | "
            f"{r.compliance_score}/6 |"
        )
    lines.append("")

    # 占位符 findings 详情
    has_findings_section = False
    for r in nonaligned:
        if not r.placeholder_findings:
            continue
        if not has_findings_section:
            lines.append("## 占位符反模式 findings 详情")
            lines.append("")
            has_findings_section = True
        lines.append(f"### `{r.path}`")
        lines.append("")
        for f in r.placeholder_findings:
            lines.append(f"- **L{f.line_no}** 命中 `{f.matched}`")
            lines.append(f"  - 上下文: `{f.context}`")
        lines.append("")

    if missing:
        lines.append("## ⚠️ 缺失文件（候选清单需更新）")
        lines.append("")
        for r in missing:
            lines.append(f"- `{r.path}`")
        lines.append("")

    lines.append("## V37.9.38+ 修复路线图")
    lines.append("")
    lines.append("**今日 V37.9.38 完成**：MRD-LLM-PLACEHOLDER-FALLBACK-001 扫描器 + audit 报告 + arxiv_monitor PoC fix + INV-LLMCRON-AUDIT-001。")
    lines.append("")
    lines.append("**V37.9.39+ 候选脚本**（按风险优先级，逐次 1-2 个收敛）：")
    lines.append("")
    p1 = sorted([r for r in nonaligned if len(r.placeholder_findings) >= 2], key=lambda x: -len(x.placeholder_findings))
    p2 = sorted([r for r in nonaligned if len(r.placeholder_findings) == 1], key=lambda x: x.path)
    p3 = sorted([r for r in nonaligned if len(r.placeholder_findings) == 0], key=lambda x: -x.compliance_score)
    if p1:
        lines.append("**P1 — 多 finding 高风险（同款 V37.9.36 血案模式）**：")
        for r in p1:
            lines.append(f"- `{r.path}` — {len(r.placeholder_findings)} findings, score {r.compliance_score}/6")
        lines.append("")
    if p2:
        lines.append("**P2 — 单 finding**：")
        for r in p2:
            lines.append(f"- `{r.path}` — score {r.compliance_score}/6")
        lines.append("")
    if p3:
        lines.append("**P3 — 无占位符 finding 但缺 fail-fast 标志（潜在静默风险）**：")
        for r in p3:
            lines.append(f"- `{r.path}` — score {r.compliance_score}/6")
        lines.append("")

    lines.append("## 合规标准（V37.9.36-37 reference）")
    lines.append("")
    lines.append("1. **`source notify.sh`** + 定义 `send_alert()` helper")
    lines.append("2. **`[SYSTEM_ALERT]`** 前缀（V37.4.3 PA 上下文隔离不变式）")
    lines.append("3. **LLM 三层检测**：HTTP error / JSON parse fail / empty content（任一触发 → fail-fast）")
    lines.append("4. **`status: llm_failed`** 写 last_run JSON（不谎报 `ok`）")
    lines.append("5. **`exit 1`** 在告警之后（让 cron 退出码可观测）")
    lines.append("6. **emit 端禁占位符 fallback**（不能硬编码 `价值：⭐⭐⭐` 等）")
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description="V37.9.38 LLM cron fail-fast 合规扫描器")
    parser.add_argument("--report", action="store_true", help="生成 markdown 报告")
    parser.add_argument("--check", action="store_true", help="MRD 模式（exit 1 if violations）")
    parser.add_argument("--json", action="store_true", help="JSON 输出")
    parser.add_argument("--strict", action="store_true",
                        help="严格模式：未登记脚本未通过即 exit 1")
    parser.add_argument("--repo", default=".", help="仓库根目录（默认当前）")
    args = parser.parse_args(argv)

    reports = audit_all(args.repo)

    if args.json:
        out = [r.to_dict() for r in reports]
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0 if all(r.aligned for r in reports if r.exists) else 1

    if args.report:
        print(format_markdown_report(reports))
        return 0

    if args.check:
        violations = [r for r in reports if r.exists and not r.aligned]
        if args.strict:
            if violations:
                print(f"❌ {len(violations)} script(s) 未对齐 V37.9.36 fail-fast 模式", file=sys.stderr)
                for v in violations:
                    print(f"   - {v.path} ({len(v.placeholder_findings)} placeholders, score {v.compliance_score}/6)", file=sys.stderr)
                return 1
        else:
            with_placeholders = [r for r in violations if r.placeholder_findings]
            if with_placeholders:
                print(f"❌ {len(with_placeholders)} script(s) 含占位符反模式（V37.9.36 血案）", file=sys.stderr)
                for v in with_placeholders:
                    print(f"   - {v.path} ({len(v.placeholder_findings)} findings)", file=sys.stderr)
                return 1
        print(f"✅ {len(reports)} script(s) 合规检查通过（strict={args.strict}）")
        return 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
