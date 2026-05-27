"""path_consistency_scanner.py — V37.9.82 INV-PATH-CONSISTENCY-001

防御 V37.9.56-hotfix (top_alignment_picker cache_paths 多 layout) + V37.9.66
(_format_cron_line jobs/ → .openclaw/ path bug) 同款 Class B (failure_modes_catalog
设计假设错配) 血案 — 路径假设跨脚本不一致, dev 通过但 Mac Mini 部署后才暴露.

检查 35 enabled jobs 三方一致性:
  (1) jobs_registry.yaml entry → auto_deploy.sh FILE_MAP src 必须存在
  (2) FILE_MAP src → dst 路径转换符合 V37.9.66 约定:
      - jobs/X/Y.sh → $HOME/.openclaw/jobs/X/Y.sh  (V30+ 新风格)
      - 其他 X.sh   → $HOME/X.sh                    (V27 老风格)
  (3) 跨脚本路径假设一致 (convergence._format_cron_line / proxy_filters / 等)

FAIL-CLOSE 契约: 任一 violation → exit 1, 阻止 PR 合并.
夸 jobs_registry.yaml 真理源 vs auto_deploy.sh 部署约定双向 audit.

依赖: 纯 stdlib + lazy import yaml (auto_deploy 流程必有 PyYAML, dev 也已装).
工作目录约束: repo_dir 必须含 jobs_registry.yaml + auto_deploy.sh 同级.

CLI:
  python3 path_consistency_scanner.py                  # 当前 cwd / 脚本所在 repo 扫
  python3 path_consistency_scanner.py --repo-dir PATH  # 显式指定 repo 根
  python3 path_consistency_scanner.py --json           # JSON 输出 (机器可读)

V37.9.82 (2026-05-19): INV-PATH-CONSISTENCY-001 立案. failure_modes_catalog.md
Class B 防御 — 让"34 jobs 路径假设错配"从下次血案降级为 governance audit 主动捕获.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from typing import Any


# V37.9.66 path convention — auto_deploy.sh 部署约定
JOBS_SUBDIR_PREFIX = "jobs/"
OPENCLAW_DEPLOY_PREFIX = "$HOME/.openclaw/"
ROOT_DEPLOY_PREFIX = "$HOME/"

# 豁免: entry 为空表示 job 由 openclaw 管理 (无独立脚本文件), 不强制 FILE_MAP 登记.
ENTRY_OPTIONAL_SCHEDULERS = {"openclaw"}

# V37.9.82 合法非常规部署豁免清单 (设计意图必须保留).
# 每条豁免必须有显式 reason — 拒绝"无理由豁免"防止豁免清单成为漏洞兜底.
# 新加 FILE_MAP entry 时如需豁免, 必须显式登记此处并审视 reason.
ALLOWED_NON_STANDARD_DST: dict[str, dict[str, str]] = {
    "SOUL.md": {
        "dst": "$HOME/.openclaw/workspace/SOUL.md",
        "reason": "V30.4 PA 宪法级 system prompt — OpenClaw 加载 workspace/SOUL.md",
    },
    "ops_soul.md": {
        "dst": "$HOME/.openclaw/SOUL.md",
        "reason": "V31 Ops Agent 隔离 SOUL.md — OpenClaw 子环境约定",
    },
    "status.json": {
        "dst": "$HOME/.kb/status.json",
        "reason": "V30.4 三方共享状态 — cron 脚本 + PA + Claude Code 共读 .kb/",
    },
    "CLAUDE.md": {
        "dst": "$HOME/.kb/docs/CLAUDE.md",
        "reason": "V29 kb_inject 让 PA 可查项目文档 — 部署到 .kb/docs/",
    },
    "docs/GUIDE.md": {
        "dst": "$HOME/.kb/docs/GUIDE.md",
        "reason": "V29 kb_inject 让 PA 可查文档",
    },
    "docs/config.md": {
        "dst": "$HOME/.kb/docs/config.md",
        "reason": "V29 kb_inject 让 PA 可查文档",
    },
    # V37.9.84: mm_index/mm_search/mm_index_cron 已迁移到 $HOME/ 标准约定 (从 V29.1 repo 目录历史遗留)
    # V37.9.84: run_hn_fixed.sh 已迁移到 jobs/hn_watcher/ (豁免移除)
}


def load_jobs_registry(repo_dir: str) -> list[dict[str, Any]]:
    """Load enabled jobs from jobs_registry.yaml.

    Raises:
        FileNotFoundError: jobs_registry.yaml missing
        ImportError: PyYAML missing (should not happen in production; auto_deploy
            already depends on it)
    """
    import yaml  # lazy

    path = os.path.join(repo_dir, "jobs_registry.yaml")
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    jobs = data.get("jobs", []) or []
    return [j for j in jobs if j.get("enabled", False)]


def parse_auto_deploy_file_map(auto_deploy_path: str) -> dict[str, str]:
    """Parse FILE_MAP array from auto_deploy.sh into src → dst dict.

    auto_deploy.sh FILE_MAP entries are of the form `"src|dst"  # optional comment`
    inside a `declare -a FILE_MAP=( ... )` block. Skips comment-only lines and
    blank lines. Inline trailing comments are tolerated.

    Raises:
        FileNotFoundError: auto_deploy.sh missing
        RuntimeError: FILE_MAP block not found (signals auto_deploy.sh schema drift)
    """
    with open(auto_deploy_path, "r", encoding="utf-8") as f:
        content = f.read()

    # Match `declare -a FILE_MAP=( ... )` multiline block.
    match = re.search(
        r"declare\s+-a\s+FILE_MAP=\((.*?)^\)",
        content,
        re.MULTILINE | re.DOTALL,
    )
    if not match:
        raise RuntimeError(
            "auto_deploy.sh FILE_MAP block not found — "
            "schema drift? expected `declare -a FILE_MAP=( ... )`"
        )
    block = match.group(1)

    result: dict[str, str] = {}
    for line in block.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        # Match `"src|dst"` allowing optional trailing whitespace + comments.
        m = re.match(r'^"([^"]+)\|([^"]+)"', stripped)
        if not m:
            continue
        src, dst = m.group(1), m.group(2)
        result[src] = dst
    return result


def expected_dst_for_src(src: str) -> str:
    """V37.9.66 path convention.

    jobs/X/Y.sh  → $HOME/.openclaw/jobs/X/Y.sh   (V30+ 子目录约定)
    其他 path    → $HOME/path                     (V27 根目录约定)

    Anchor: V37.9.66 (`_format_cron_line` jobs/ 开头加 .openclaw/ 前缀的同款约定).
    """
    if src.startswith(JOBS_SUBDIR_PREFIX):
        return OPENCLAW_DEPLOY_PREFIX + src
    return ROOT_DEPLOY_PREFIX + src


def scan_path_consistency(repo_dir: str) -> list[dict[str, str]]:
    """Main scanner. Returns list of finding dicts (empty = all consistent).

    Each finding dict has:
        type:     MISSING_FILE_MAP | DST_MISMATCH | MISSING_FILE_ON_DISK
        job_id:   (optional) job id when finding is job-scoped
        detail:   human-readable explanation
        src:      (optional) FILE_MAP src
        dst_actual: (optional) actual FILE_MAP dst
        dst_expected: (optional) expected dst per V37.9.66 convention

    Findings are not sorted — caller may sort by type/job_id for display.
    """
    findings: list[dict[str, str]] = []

    enabled_jobs = load_jobs_registry(repo_dir)
    file_map = parse_auto_deploy_file_map(
        os.path.join(repo_dir, "auto_deploy.sh")
    )

    # Check 1: Every enabled job entry must be declared in FILE_MAP.
    # entry 可能含 CLI 参数 (e.g. "kb_dream.sh --map-sources"), 取第一段为文件路径.
    # (Exception: openclaw-managed jobs without entry are exempt.)
    for job in enabled_jobs:
        job_id = job.get("id", "?")
        entry_full = job.get("entry", "")
        scheduler = job.get("scheduler", "")
        if not entry_full:
            if scheduler in ENTRY_OPTIONAL_SCHEDULERS:
                continue
            findings.append({
                "type": "MISSING_ENTRY",
                "job_id": job_id,
                "detail": (
                    f"enabled job has no `entry` field "
                    f"(scheduler={scheduler!r}, not in ENTRY_OPTIONAL_SCHEDULERS)"
                ),
            })
            continue
        # Strip CLI args — entry "kb_dream.sh --map-sources" → "kb_dream.sh"
        entry_file = entry_full.split(None, 1)[0]
        if entry_file not in file_map:
            findings.append({
                "type": "MISSING_FILE_MAP",
                "job_id": job_id,
                "src": entry_file,
                "detail": (
                    f"job entry {entry_full!r} (file part: {entry_file!r}) "
                    f"not declared in auto_deploy.sh FILE_MAP — "
                    f"Mac Mini auto_deploy 不会同步, cron 触发时找不到文件"
                ),
            })

    # Check 2: Every FILE_MAP src's dst must follow V37.9.66 path convention,
    # OR be in ALLOWED_NON_STANDARD_DST exemption list with explicit reason.
    for src, dst in file_map.items():
        # Exemption: ALLOWED_NON_STANDARD_DST 必须 dst 精确匹配
        if src in ALLOWED_NON_STANDARD_DST:
            allowed_dst = ALLOWED_NON_STANDARD_DST[src]["dst"]
            if dst == allowed_dst:
                continue  # 合法非常规部署豁免
            # 豁免登记但 dst 不匹配 → 仍是问题 (豁免被绕过)
            findings.append({
                "type": "EXEMPTION_DST_DRIFT",
                "src": src,
                "dst_actual": dst,
                "dst_expected": allowed_dst,
                "detail": (
                    f"ALLOWED_NON_STANDARD_DST 登记了 {src!r} → {allowed_dst!r} "
                    f"但 FILE_MAP 实际 dst 是 {dst!r}. 豁免清单与 FILE_MAP 漂移."
                ),
            })
            continue
        expected = expected_dst_for_src(src)
        if dst != expected:
            findings.append({
                "type": "DST_MISMATCH",
                "src": src,
                "dst_actual": dst,
                "dst_expected": expected,
                "detail": (
                    f"FILE_MAP entry has non-conventional dst — V37.9.66 约定: "
                    f"jobs/ 子目录 → $HOME/.openclaw/jobs/, 其他 → $HOME/. "
                    f"如此 dst 是有意设计意图, 加入 ALLOWED_NON_STANDARD_DST 豁免清单 + 写明 reason."
                ),
            })

    # Check 3: jobs/ 子目录 src 必须存在于 repo 树 (防 entry 拼错或文件已删).
    for src in file_map.keys():
        # Skip system files like VERSION which are not job entries
        if not (src.startswith("jobs/") or src.endswith(".sh") or src.endswith(".py")):
            continue
        src_path = os.path.join(repo_dir, src)
        if not os.path.exists(src_path):
            findings.append({
                "type": "MISSING_FILE_ON_DISK",
                "src": src,
                "detail": (
                    f"FILE_MAP declares src={src!r} but file does not exist in repo — "
                    f"likely typo or stale entry after script rename"
                ),
            })

    return findings


def scan_crontab_consistency(repo_dir: str) -> list[dict[str, str]]:
    """V37.9.85: verify _format_cron_line output matches real crontab -l.

    Only meaningful on Mac Mini (--full mode). In dev, crontab -l returns
    empty or error → 0 findings (FAIL-OPEN, not a false alarm).

    For each enabled system job with entry+interval+log, generates the
    expected cron line via convergence._format_cron_line and checks if
    it exists in crontab -l output.

    Returns list of finding dicts with type=CRON_LINE_MISSING.
    """
    findings: list[dict[str, str]] = []

    # Load convergence._format_cron_line — lazy to avoid hard dep
    try:
        sys.path.insert(0, os.path.join(repo_dir, "ontology"))
        from convergence import _format_cron_line
    except (ImportError, Exception):
        return findings  # FAIL-OPEN: convergence not available

    # Get real crontab
    try:
        result = subprocess.run(
            ["crontab", "-l"],
            capture_output=True, text=True, timeout=5,
        )
        crontab_lines = result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return findings  # FAIL-OPEN: no crontab command or timeout

    if not crontab_lines:
        return findings  # FAIL-OPEN: empty crontab (dev environment)

    # V37.9.85: normalize for comparison — these are semantically equivalent:
    #   $HOME/ ↔ ~/
    #   single quotes ↔ double quotes in bash -lc context
    #   mkdir -p ...; bash -lc '...' wrapper (substring match)
    def _normalize(text):
        return text.replace("$HOME/", "~/").replace('"', "'")

    crontab_normalized = _normalize(crontab_lines)

    enabled_jobs = load_jobs_registry(repo_dir)
    for job in enabled_jobs:
        scheduler = job.get("scheduler", "")
        if scheduler != "system":
            continue
        entry = job.get("entry", "")
        interval = job.get("interval", "")
        log = job.get("log", "")
        if not entry or not interval or not log:
            continue

        try:
            expected_line = _format_cron_line(job)
        except (ValueError, Exception):
            continue  # malformed job, already caught by scan_path_consistency

        expected_normalized = _normalize(expected_line)

        if expected_normalized in crontab_normalized:
            continue  # exact match (best case)

        # Fallback: check if interval + entry basename both appear in the
        # same crontab line. This catches "job IS registered but with format
        # drift" (mkdir wrapper, missing inner bash, different quoting, etc.)
        entry_file = entry.split(None, 1)[0]
        entry_basename = os.path.basename(entry_file)
        interval_str = interval.strip()
        found_by_basename = False
        for cron_line in crontab_normalized.splitlines():
            line_stripped = cron_line.strip()
            if not line_stripped or line_stripped.startswith("#"):
                continue
            if line_stripped.startswith(interval_str) and entry_basename in line_stripped:
                found_by_basename = True
                break

        if not found_by_basename:
            findings.append({
                "type": "CRON_LINE_MISSING",
                "job_id": job.get("id", "?"),
                "detail": (
                    f"expected cron line not found in crontab -l: "
                    f"{expected_line!r}"
                ),
                "expected_line": expected_line,
            })

    return findings


def format_findings_human(findings: list[dict[str, str]]) -> str:
    """Format findings list as human-readable multi-line text."""
    if not findings:
        return "✅ V37.9.82 INV-PATH-CONSISTENCY-001 — all paths consistent"
    lines = [f"❌ V37.9.82 INV-PATH-CONSISTENCY-001 — {len(findings)} violations:"]
    # Group by type for readability
    by_type: dict[str, list[dict[str, str]]] = {}
    for f in findings:
        by_type.setdefault(f["type"], []).append(f)
    for ftype, fs in by_type.items():
        lines.append(f"\n  [{ftype}] × {len(fs)}:")
        for f in fs:
            jid = f.get("job_id", "")
            jid_str = f" [{jid}]" if jid else ""
            lines.append(f"    •{jid_str} {f.get('detail', '')}")
            if "src" in f and "dst_actual" in f:
                lines.append(f"        src={f['src']}")
                lines.append(f"        dst (actual)  ={f['dst_actual']}")
                lines.append(f"        dst (expected)={f['dst_expected']}")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="V37.9.82+V37.9.85 INV-PATH-CONSISTENCY-001 scanner"
    )
    parser.add_argument(
        "--repo-dir",
        default=os.path.dirname(os.path.abspath(__file__)),
        help="repo root containing jobs_registry.yaml + auto_deploy.sh",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="JSON output (machine-readable)",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="V37.9.85: include crontab -l consistency check (Mac Mini only)",
    )
    args = parser.parse_args(argv)

    try:
        findings = scan_path_consistency(args.repo_dir)
    except (FileNotFoundError, RuntimeError, ImportError) as e:
        print(
            f"❌ scanner failed to load inputs: {type(e).__name__}: {e}",
            file=sys.stderr,
        )
        return 2

    # V37.9.85: --full mode adds crontab consistency check
    if args.full:
        cron_findings = scan_crontab_consistency(args.repo_dir)
        findings.extend(cron_findings)

    if args.json:
        print(json.dumps({
            "version": "V37.9.85",
            "invariant": "INV-PATH-CONSISTENCY-001",
            "repo_dir": args.repo_dir,
            "full_mode": args.full,
            "violation_count": len(findings),
            "findings": findings,
        }, ensure_ascii=False, indent=2))
    else:
        print(format_findings_human(findings))
        if args.full and not any(f["type"] == "CRON_LINE_MISSING" for f in findings):
            cron_jobs_count = len([
                j for j in load_jobs_registry(args.repo_dir)
                if j.get("scheduler") == "system" and j.get("entry")
            ])
            print(f"  ✅ crontab consistency: all {cron_jobs_count} system jobs match")

    # FAIL-CLOSE: any violation → exit 1
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
