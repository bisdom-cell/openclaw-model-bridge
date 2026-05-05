#!/usr/bin/env python3
"""movespeed_incident_analyzer.py — V37.9.28 F2 数据驱动诊断工具

V37.9.27 movespeed_rsync_helper.sh 部署后, 用户 5/5 周一观察发现 24h 仍 20 次
rsync 失败 (V37.9.27 承诺 <1/24h, 95%+ 减少). MR-4 silent-failure 第 19 次
演出新形态: 修复看似生效, 实际可能换了一种失败形式 (EPERM → EOF) 或
retry 时间窗 (10s+20s=30s 总) 太短无法跨过 transient 阻塞 (Time Machine
backup 通常 5-10min 锁 SSD I/O).

按原则 #28 三问: 修复前必须先看证据. 本脚本读 ~/.kb/movespeed_incidents.jsonl
(由 V37.9.14 movespeed_incident_capture.sh 写入) 并按多维度汇总:

  (a) 时间覆盖率与 24h/72h/7d 分桶
  (b) 按 caller (job) 分布 — 哪些 cron 受影响最重
  (c) 按 exit_code 分布 — rsync 12=protocol error / 23=partial xfer / 30=timeout / 等
  (d) probe_top vs probe_kb 二维矩阵 — 区分全盘 EPERM 还是仅 KB 子目录 EPERM
  (e) 并发进程统计 — backupd/mds_stores/Spotlight/fseventsd/tmutil 在多少 incident 时活跃
  (f) Time-of-day 分布 — 与 macOS Time Machine 自动备份时段相关性
  (g) Mount 状态 — readonly remount 是否在 incident 时发生

输出: 文本报告 (默认) 或 --json (机器可读)

CLI:
  python3 movespeed_incident_analyzer.py                    # 默认 ~/.kb/movespeed_incidents.jsonl
  python3 movespeed_incident_analyzer.py --file PATH        # 自定义 JSONL 路径
  python3 movespeed_incident_analyzer.py --window 24h       # 仅分析 24h 内 (默认 all)
  python3 movespeed_incident_analyzer.py --json             # JSON 输出
  python3 movespeed_incident_analyzer.py --top-n 10         # 各维度 top-N (默认 5)

作者备注: 此工具是 F2 修复的前置工具, 不直接改变 helper 行为. 决定 helper
修复策略 (e.g. EOF 不重试 / 增长 backoff / Time Machine 检测) 必须基于此
工具的输出, 不能凭推测. 工具本身只读, 零副作用.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any


DEFAULT_INCIDENT_FILE = os.path.expanduser("~/.kb/movespeed_incidents.jsonl")
PROC_KEYWORDS = ["backupd", "mds_stores", "mds", "Spotlight", "fseventsd", "tmutil"]


def parse_iso_to_dt(ts_iso: str) -> datetime | None:
    """Parse ISO timestamp to UTC datetime; None on failure."""
    if not isinstance(ts_iso, str) or not ts_iso:
        return None
    try:
        if ts_iso.endswith("Z"):
            ts_iso = ts_iso[:-1] + "+00:00"
        dt = datetime.fromisoformat(ts_iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def parse_window_to_seconds(window: str | None) -> int | None:
    """Parse '24h' / '72h' / '7d' / 'all' to seconds (None for 'all')."""
    if window is None or window == "all":
        return None
    m = re.match(r"^(\d+)([hd])$", window.strip())
    if not m:
        raise ValueError(f"Invalid window: {window!r}; expected '24h' / '7d' / 'all'")
    n, unit = int(m.group(1)), m.group(2)
    return n * (3600 if unit == "h" else 86400)


def load_records(path: str) -> tuple[list[dict[str, Any]], int]:
    """Load JSONL records; return (valid_records, parse_error_count).

    Skips corrupted lines silently per V37.9.14 incident_capture FAIL-OPEN
    contract — file IO failure raises FileNotFoundError to caller.
    """
    records: list[dict[str, Any]] = []
    errors = 0
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                if isinstance(rec, dict):
                    records.append(rec)
                else:
                    errors += 1
            except (ValueError, TypeError):
                errors += 1
    return records, errors


def filter_window(records: list[dict[str, Any]], window_sec: int | None,
                  now: datetime | None = None) -> list[dict[str, Any]]:
    """Filter records to those within [now - window_sec, now]."""
    if window_sec is None:
        return records
    if now is None:
        now = datetime.now(timezone.utc)
    cutoff = now - timedelta(seconds=window_sec)
    out = []
    for r in records:
        dt = parse_iso_to_dt(r.get("timestamp_iso", ""))
        if dt and dt >= cutoff:
            out.append(r)
    return out


def classify_probe(probe_str: str) -> str:
    """Classify a probe field 'exit=N|stderr_msg' as ok/eperm/other/unknown."""
    if not isinstance(probe_str, str) or not probe_str:
        return "unknown"
    parts = probe_str.split("|", 1)
    rc_part = parts[0].strip() if parts else ""
    err_part = parts[1].strip() if len(parts) > 1 else ""
    if rc_part == "exit=0":
        return "ok"
    if "operation not permitted" in err_part.lower() or "permission denied" in err_part.lower():
        return "eperm"
    if rc_part.startswith("exit=") and rc_part != "exit=0":
        return "other"
    return "unknown"


def classify_caller_failure_mode(rec: dict[str, Any]) -> str:
    """Distinguish 全盘-EPERM vs KB-only-EPERM vs both-OK (EOF candidate) vs unknown.

    全盘-EPERM = top probe + kb probe both EPERM
    KB-only = top OK + kb EPERM
    both-OK = top OK + kb OK (probe success but rsync still failed → likely EOF/transient stream)
    unknown = unparseable
    """
    top = classify_probe(rec.get("probe_top", ""))
    kb = classify_probe(rec.get("probe_kb", ""))
    if top == "ok" and kb == "ok":
        return "probes_ok_likely_eof_or_stream"
    if top == "eperm" and kb == "eperm":
        return "全盘_eperm"
    if top == "ok" and kb == "eperm":
        return "kb_only_eperm"
    if top == "eperm" and kb == "ok":
        return "kb_only_ok_top_eperm_inverted"
    return "mixed_or_unknown"


def extract_concurrent_procs(procs_str: str) -> set[str]:
    """Return set of keywords found in procs field."""
    if not isinstance(procs_str, str):
        return set()
    found = set()
    procs_lower = procs_str.lower()
    for kw in PROC_KEYWORDS:
        if kw.lower() in procs_lower:
            found.add(kw)
    return found


def analyze(records: list[dict[str, Any]], top_n: int = 5) -> dict[str, Any]:
    """Compute aggregate analysis."""
    n = len(records)
    if n == 0:
        return {"count": 0, "summary": "no records"}

    # Time coverage
    timestamps = [parse_iso_to_dt(r.get("timestamp_iso", "")) for r in records]
    timestamps = [t for t in timestamps if t is not None]
    earliest = min(timestamps).isoformat() if timestamps else None
    latest = max(timestamps).isoformat() if timestamps else None

    # Per-caller
    caller_counter: Counter[str] = Counter()
    for r in records:
        caller_counter[r.get("caller", "unknown")] += 1

    # Per-exit-code
    exit_counter: Counter[str] = Counter()
    for r in records:
        exit_counter[str(r.get("exit_code", "unknown"))] += 1

    # Probe failure mode classification
    mode_counter: Counter[str] = Counter()
    for r in records:
        mode_counter[classify_caller_failure_mode(r)] += 1

    # Concurrent process patterns
    proc_counter: Counter[str] = Counter()
    proc_combo_counter: Counter[str] = Counter()
    for r in records:
        procs = extract_concurrent_procs(r.get("procs", ""))
        for p in procs:
            proc_counter[p] += 1
        if procs:
            proc_combo_counter[",".join(sorted(procs))] += 1
        else:
            proc_combo_counter["<none>"] += 1

    # Time-of-day distribution (UTC hour)
    hour_counter: Counter[int] = Counter()
    for r in records:
        dt = parse_iso_to_dt(r.get("timestamp_iso", ""))
        if dt:
            hour_counter[dt.hour] += 1

    # Mount state (readonly check)
    mount_state_counter: Counter[str] = Counter()
    for r in records:
        mount_str = r.get("mount", "")
        if not isinstance(mount_str, str) or not mount_str:
            mount_state_counter["empty"] += 1
            continue
        mount_lower = mount_str.lower()
        if "read-only" in mount_lower or "readonly" in mount_lower or "ro," in mount_lower:
            mount_state_counter["readonly_at_incident"] += 1
        elif "read-write" in mount_lower or "rw," in mount_lower:
            mount_state_counter["readwrite_at_incident"] += 1
        else:
            mount_state_counter["other_or_unmounted"] += 1

    return {
        "count": n,
        "time_coverage": {"earliest": earliest, "latest": latest},
        "by_caller": dict(caller_counter.most_common(top_n)),
        "by_exit_code": dict(exit_counter.most_common(top_n)),
        "by_failure_mode": dict(mode_counter),
        "by_concurrent_proc": dict(proc_counter.most_common(top_n)),
        "by_proc_combo": dict(proc_combo_counter.most_common(top_n)),
        "by_hour_utc": dict(sorted(hour_counter.items())),
        "by_mount_state": dict(mount_state_counter),
    }


def format_text_report(analysis: dict[str, Any], window_label: str) -> str:
    """Format analysis dict as human-readable text."""
    if analysis.get("count", 0) == 0:
        return f"=== MOVESPEED Incident 分析 ({window_label}) ===\n\n  无记录."

    lines = [f"=== MOVESPEED Incident 分析 ({window_label}) ==="]
    lines.append(f"\n总数: {analysis['count']}")
    tc = analysis.get("time_coverage", {})
    lines.append(f"时间范围: {tc.get('earliest', '?')} → {tc.get('latest', '?')}")

    lines.append("\n📊 按 caller (job) 分布:")
    for k, v in analysis.get("by_caller", {}).items():
        lines.append(f"  {v:>3}  {k}")

    lines.append("\n📊 按 exit_code 分布 (rsync exit codes: 11=parent/child / 12=protocol / 23=partial xfer / 30=timeout):")
    for k, v in analysis.get("by_exit_code", {}).items():
        lines.append(f"  {v:>3}  exit={k}")

    lines.append("\n🔍 按失败模式分类 (probe_top / probe_kb 矩阵):")
    mode_explain = {
        "全盘_eperm": "整个 SSD EPERM (V37.9.4 APFS 重建本应解决)",
        "kb_only_eperm": "仅 KB 子目录 EPERM (子目录权限漂移)",
        "probes_ok_likely_eof_or_stream": "probe 都 OK 但 rsync 失败 → 强烈暗示 EOF/stream 中断 (TM 锁?)",
        "kb_only_ok_top_eperm_inverted": "异常: 顶层 EPERM 但 KB OK (诊断 bug?)",
        "mixed_or_unknown": "未知或混合状态",
    }
    for k, v in analysis.get("by_failure_mode", {}).items():
        explain = mode_explain.get(k, "")
        lines.append(f"  {v:>3}  {k}  — {explain}")

    lines.append("\n🔥 并发进程统计 (出现在 incident 时刻的 ps -ax 中):")
    for k, v in analysis.get("by_concurrent_proc", {}).items():
        pct = (v * 100) // max(analysis["count"], 1)
        lines.append(f"  {v:>3} ({pct:>2}%)  {k}")

    lines.append("\n🔥 进程组合 top:")
    for k, v in analysis.get("by_proc_combo", {}).items():
        lines.append(f"  {v:>3}  [{k}]")

    lines.append("\n🕐 Time-of-day (UTC 小时) 分布:")
    by_hour = analysis.get("by_hour_utc", {})
    if by_hour:
        max_count = max(by_hour.values())
        for h in range(24):
            cnt = by_hour.get(h, 0)
            if cnt > 0:
                bar = "█" * max(1, (cnt * 30) // max_count)
                lines.append(f"  {h:02d}:00 UTC  {cnt:>3}  {bar}")

    lines.append("\n💾 Mount state 分布:")
    for k, v in analysis.get("by_mount_state", {}).items():
        lines.append(f"  {v:>3}  {k}")

    # 决策提示
    lines.append("\n---\n💡 决策提示 (基于本输出回答 F2 最小修复方案):")
    fm = analysis.get("by_failure_mode", {})
    procs_top = list(analysis.get("by_concurrent_proc", {}).keys())
    eperm_count = fm.get("全盘_eperm", 0) + fm.get("kb_only_eperm", 0)
    eof_count = fm.get("probes_ok_likely_eof_or_stream", 0)

    if eof_count > eperm_count and "backupd" in procs_top[:3]:
        lines.append(
            "  → 主导失败模式是 EOF + Time Machine backupd 高频出现:\n"
            "     建议 F2 修复方向: helper 在 rsync 前检测 tmutil status, 若 backup\n"
            "     in progress 则跳过/延迟 (非 retry, 因 TM backup 跨度远超 30s)"
        )
    elif eof_count > eperm_count:
        lines.append(
            "  → 主导失败模式是 EOF (probe 都 OK 但 rsync 失败) 但 TM 不在 top procs:\n"
            "     可能是 fseventsd / Spotlight / mds_stores 慢路径锁 SSD\n"
            "     建议 F2 修复方向: 增长 backoff (e.g. 30s/60s/120s) 或 EOF 不 retry"
        )
    elif eperm_count > eof_count:
        lines.append(
            "  → 主导失败模式是 EPERM (V37.9.4 APFS 修复本应解决):\n"
            "     建议 F2 修复方向: 重新审计文件系统状态, 可能 APFS 重建未稳定"
        )
    else:
        lines.append("  → 失败模式分布不显著, 需更多数据 (记录数 < 10)")

    return "\n".join(lines)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="MOVESPEED incident 数据驱动分析工具 (V37.9.28 F2)"
    )
    parser.add_argument("--file", default=DEFAULT_INCIDENT_FILE,
                        help=f"JSONL 路径 (default {DEFAULT_INCIDENT_FILE})")
    parser.add_argument("--window", default="all",
                        help="时间窗 (24h / 72h / 7d / all; default all)")
    parser.add_argument("--json", action="store_true",
                        help="JSON 输出 (机器可读, 略掉决策提示)")
    parser.add_argument("--top-n", type=int, default=5,
                        help="各维度 top-N (default 5)")
    args = parser.parse_args(argv)

    if not os.path.isfile(args.file):
        print(f"❌ 文件不存在: {args.file}", file=sys.stderr)
        print(f"   ~/.kb/movespeed_incidents.jsonl 由 V37.9.14 incident_capture.sh 写入,",
              file=sys.stderr)
        print(f"   仅在 Mac Mini (有 rsync 失败发生) 上才有内容.", file=sys.stderr)
        return 2

    try:
        records, parse_errs = load_records(args.file)
    except OSError as e:
        print(f"❌ 读取失败: {e}", file=sys.stderr)
        return 2

    try:
        window_sec = parse_window_to_seconds(args.window)
    except ValueError as e:
        print(f"❌ {e}", file=sys.stderr)
        return 2

    filtered = filter_window(records, window_sec)
    analysis = analyze(filtered, top_n=args.top_n)
    analysis["_meta"] = {
        "source_file": args.file,
        "raw_record_count": len(records),
        "parse_errors": parse_errs,
        "filtered_count": len(filtered),
        "window": args.window,
    }

    if args.json:
        print(json.dumps(analysis, ensure_ascii=False, indent=2))
    else:
        print(format_text_report(analysis, args.window))
        print(f"\n[meta] file={args.file} | raw_total={len(records)} | "
              f"parse_errors={parse_errs} | filtered={len(filtered)}")

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
