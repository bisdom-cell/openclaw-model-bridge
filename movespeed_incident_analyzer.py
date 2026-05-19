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

# V37.9.30: daemon process keywords expected to appear in lsof if a daemon
# holds I/O handles on /Volumes/MOVESPEED. Order matters: longer first to
# avoid 'mds' matching 'mds_stores' substring.
LSOF_DAEMON_KEYWORDS = ["mds_stores", "backupd", "fseventsd", "Spotlight", "mds", "mdworker"]


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


def classify_acl_anomaly(acl_str: str) -> str:
    """V37.9.30: classify ACL/xattr field as anomaly bucket.

    macOS `ls -le@` output contains:
      - explicit ACL lines like ' 0: group:everyone deny ...'
      - xattr lines like '\tcom.apple.quarantine     38'
      - just regular `ls -l` rows when no ACL/xattr present
    Returns one of: "acl_deny" / "acl_present" / "xattr_only" / "normal" / "empty" /
                    "sandbox_denied" / "tool_unavailable".

    V37.9.81 B: detect [sandbox_denied] / [tool_unavailable] marker prefix
    written by capture.sh read_file_with_stderr. V37.9.30 had a 6-week blind
    spot where ls -le@ being denied by TCC sandbox produced empty stdout that
    was misclassified as "normal" (the 60-day MOVESPEED EPERM blood case ended
    in V37.9.80 by adding /usr/sbin/cron to FDA). New marker buckets close the
    采集失败 vs 真空 ambiguity.
    """
    if not isinstance(acl_str, str) or not acl_str.strip():
        return "empty"
    # V37.9.81 B: marker takes precedence — 采集器自身被拒, 不分类内容
    stripped = acl_str.lstrip()
    if stripped.startswith("[sandbox_denied]"):
        return "sandbox_denied"
    if stripped.startswith("[tool_unavailable]"):
        return "tool_unavailable"
    lower = acl_str.lower()
    # ACL deny rule = strongest EPERM signal (chown does NOT clear these)
    if " deny " in lower or "\tdeny" in lower or ": group:" in lower or ": user:" in lower:
        # macOS ACL line format: " 0: group:everyone deny add_file"
        # Match either explicit deny or any structured ACL line presence.
        if " deny " in lower or "\tdeny" in lower:
            return "acl_deny"
        return "acl_present"
    # xattr-only: lines starting with tab (xattr indented) but no ACL
    has_xattr = any(line.startswith("\t") and len(line.strip()) > 0
                    for line in acl_str.splitlines())
    if has_xattr:
        return "xattr_only"
    return "normal"


def classify_handle_holders(lsof_str: str) -> str:
    """V37.9.30: classify lsof field as handle-holder pattern.

    Returns one of: "daemon_dominated" / "user_only" / "mixed" / "empty" /
                    "sandbox_denied" / "tool_unavailable".

    daemon_dominated = ≥1 daemon keyword found AND no clearly non-daemon
                       process cmd lines (cmd like rsync/python/etc.)
    mixed            = both daemon and user processes present
    user_only        = only user-side processes (rsync/python/cp/etc.)
    empty            = no records or unparseable

    V37.9.81 B: detect [sandbox_denied] / [tool_unavailable] marker prefix —
    V37.9.30 blind spot where lsof itself was sandbox-denied (lsof needs FDA
    on macOS to read /Volumes/X file handles) produced empty output that was
    classified as "empty" (looked like no contention). New buckets separate
    "采集器自身被拒" from "真没句柄".
    """
    if not isinstance(lsof_str, str) or not lsof_str.strip():
        return "empty"
    # V37.9.81 B: marker takes precedence
    stripped = lsof_str.lstrip()
    if stripped.startswith("[sandbox_denied]"):
        return "sandbox_denied"
    if stripped.startswith("[tool_unavailable]"):
        return "tool_unavailable"
    lines = [l for l in lsof_str.splitlines() if l.strip() and not l.startswith("COMMAND")]
    if not lines:
        return "empty"
    has_daemon = False
    has_user = False
    user_cmds = ("rsync", "python", "cp ", "tar ", "bash", "zsh")
    for line in lines:
        # lsof format: COMMAND PID USER FD TYPE DEVICE SIZE/OFF NODE NAME
        cmd_token = line.split(None, 1)[0] if line.split() else ""
        cmd_lower = cmd_token.lower()
        for kw in LSOF_DAEMON_KEYWORDS:
            if kw.lower() in cmd_lower:
                has_daemon = True
                break
        for uc in user_cmds:
            if uc.strip() in cmd_lower:
                has_user = True
                break
    if has_daemon and not has_user:
        return "daemon_dominated"
    if has_daemon and has_user:
        return "mixed"
    if has_user and not has_daemon:
        return "user_only"
    return "empty"


def classify_snapshot_count(snap_str: str) -> str:
    """V37.9.30: classify TM snapshot count as bucket.

    Returns one of: "snap_0" / "snap_1_5" / "snap_6_plus" / "empty" /
                    "sandbox_denied" / "tool_unavailable".

    Each snapshot line on macOS has format:
      'com.apple.TimeMachine.YYYY-MM-DD-HHMMSS.local'

    V37.9.81 B: detect [sandbox_denied] / [tool_unavailable] marker prefix —
    tmutil listlocalsnapshots may itself be sandbox-denied or unavailable on
    non-macOS systems. New buckets prevent misclassification as "snap_0"
    (which would falsely confirm "TM exclude fully working").
    """
    if not isinstance(snap_str, str) or not snap_str.strip():
        return "empty"
    # V37.9.81 B: marker takes precedence
    stripped = snap_str.lstrip()
    if stripped.startswith("[sandbox_denied]"):
        return "sandbox_denied"
    if stripped.startswith("[tool_unavailable]"):
        return "tool_unavailable"
    lines = snap_str.splitlines()
    snap_lines = [l for l in lines if "com.apple.TimeMachine" in l]
    n = len(snap_lines)
    if n == 0:
        return "snap_0"
    if n <= 5:
        return "snap_1_5"
    return "snap_6_plus"


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

    # Ownership distribution — V37.9.29 (b)
    # Records the real UID:GID at top + /KB at incident time. Backward compat:
    # records before V37.9.29 (b) lack these fields → counted as "empty".
    # Misalignment pattern (e.g. top=0:0 root + kb=99:99 _unknown) was the real
    # cause of 60-day silent failure (V37.9.29 path D' closed it).
    ownership_pair_counter: Counter[str] = Counter()
    for r in records:
        top = r.get("ownership_top", "")
        kb = r.get("ownership_kb", "")
        if not isinstance(top, str):
            top = ""
        if not isinstance(kb, str):
            kb = ""
        if not top and not kb:
            ownership_pair_counter["empty (pre-V37.9.29(b) records)"] += 1
            continue
        pair = f"top={top or '?'} kb={kb or '?'}"
        ownership_pair_counter[pair] += 1

    # V37.9.30: ACL/xattr anomaly distribution (9th dimension)
    # V37.9.29 假说部分证伪后, 寻找 EPERM 真因. ACL deny 是 chown 不能清的
    # 强阻塞模式 (chown 改 owner 但 ACL/xattr 留下). xattr_only = 弱信号.
    # Backward compat: pre-V37.9.30 records lack acl_top/_kb → 'empty' bucket.
    acl_anomaly_counter: Counter[str] = Counter()
    for r in records:
        # Use top + kb merged: any side showing acl_deny is enough
        cls_top = classify_acl_anomaly(r.get("acl_top", ""))
        cls_kb = classify_acl_anomaly(r.get("acl_kb", ""))
        # Strongest signal across the two probes wins.
        # V37.9.81 B: sandbox_denied takes top priority — 采集器自身被拒是直接 EPERM
        # 证据 (V37.9.80 TCC sandbox 真因), 比 acl_deny 假说更直接.
        # tool_unavailable 比 empty 更高 (已知工具问题, 不是"没采到").
        priority = {
            "sandbox_denied": 6,   # V37.9.81 B: direct EPERM evidence
            "acl_deny": 4,
            "acl_present": 3,
            "xattr_only": 2,
            "tool_unavailable": 1,  # V37.9.81 B: known tool failure (e.g. non-macOS)
            "normal": 1,
            "empty": 0,
        }
        winner = cls_top if priority.get(cls_top, 0) >= priority.get(cls_kb, 0) else cls_kb
        if winner == "empty":
            acl_anomaly_counter["empty (pre-V37.9.30 records)"] += 1
        else:
            acl_anomaly_counter[winner] += 1

    # V37.9.30: lsof handle holder pattern (10th dimension)
    # 5 daemon 100% 共现 (V37.9.29 数据) 但不知 daemon 是否真持有 SSD I/O 句柄.
    # daemon_dominated = 强信号支持 daemon contention 假说 → path B 调度避峰
    # user_only / mixed = daemon 共现但不持有句柄 → 真因可能在文件系统层
    handle_pattern_counter: Counter[str] = Counter()
    for r in records:
        handle_pattern_counter[classify_handle_holders(r.get("lsof", ""))] += 1

    # V37.9.30: TM local snapshot count bucket (11th dimension)
    # 即使 TM exclude 也可能有 local snapshots 锁 metadata. snap_6_plus = 强信号.
    snapshot_bucket_counter: Counter[str] = Counter()
    for r in records:
        snapshot_bucket_counter[classify_snapshot_count(r.get("snapshots", ""))] += 1

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
        "by_ownership": dict(ownership_pair_counter.most_common(top_n)),
        "by_acl_anomaly": dict(acl_anomaly_counter),
        "by_handle_pattern": dict(handle_pattern_counter),
        "by_snapshot_bucket": dict(snapshot_bucket_counter),
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

    # V37.9.29 (b): Ownership distribution (real UID:GID, bypasses noowners mask)
    by_ownership = analysis.get("by_ownership", {})
    if by_ownership:
        lines.append("\n🔐 Ownership 分布 (V37.9.29 b — 真实 UID:GID, 绕过 noowners 显示):")
        for k, v in by_ownership.items():
            lines.append(f"  {v:>3}  {k}")

    # V37.9.30: ACL/xattr anomaly distribution (9th dimension)
    by_acl = analysis.get("by_acl_anomaly", {})
    if by_acl:
        lines.append("\n🛡️ ACL/xattr 异常分布 (V37.9.30 — chown 不能清的强阻塞模式):")
        acl_explain = {
            "sandbox_denied": "🚨 采集器自身被 TCC sandbox 拒绝 (V37.9.81 B — 这是 V37.9.80 TCC 真因的直接证据)",
            "acl_deny": "ACL deny 规则 (chown 改 owner 但 ACL 留下, 强 EPERM 信号)",
            "acl_present": "ACL 存在 (非 deny, 可能仍阻塞特定操作)",
            "xattr_only": "仅 xattr (com.apple.quarantine 等, 弱信号)",
            "tool_unavailable": "ls -le@ 工具不可用 (非 macOS / 缺权限)",
            "normal": "无 ACL/xattr (正常)",
            "empty (pre-V37.9.30 records)": "旧记录无 acl_top/_kb 字段",
        }
        for k, v in by_acl.items():
            explain = acl_explain.get(k, "")
            lines.append(f"  {v:>3}  {k}  — {explain}")

    # V37.9.30: lsof handle holder pattern (10th dimension)
    by_handle = analysis.get("by_handle_pattern", {})
    if by_handle:
        lines.append("\n📂 句柄持有者模式 (V37.9.30 — 谁在持有 SSD I/O):")
        handle_explain = {
            "sandbox_denied": "🚨 lsof 自身被 TCC sandbox 拒绝 (V37.9.81 B — 之前 V37.9.30 6 周误判 empty 为正常)",
            "daemon_dominated": "macOS daemon 主导 (mds_stores/backupd/etc), 强 daemon contention 信号",
            "user_only": "仅用户进程 (rsync/python), 真因不在 daemon",
            "mixed": "daemon + 用户混合, 部分 daemon 持有句柄",
            "tool_unavailable": "lsof 工具不可用 (非 macOS / 缺权限)",
            "empty": "lsof 空或缺失 (旧记录或工具不可用)",
        }
        for k, v in by_handle.items():
            explain = handle_explain.get(k, "")
            lines.append(f"  {v:>3}  {k}  — {explain}")

    # V37.9.30: TM local snapshot count bucket (11th dimension)
    by_snap = analysis.get("by_snapshot_bucket", {})
    if by_snap:
        lines.append("\n📸 TM Snapshot 分布 (V37.9.30 — 本地快照锁 metadata 候选):")
        snap_explain = {
            "sandbox_denied": "🚨 tmutil 自身被 TCC sandbox 拒绝 (V37.9.81 B)",
            "snap_0": "0 个本地快照 (TM exclude 完全生效)",
            "snap_1_5": "1-5 个 (轻度积累)",
            "snap_6_plus": "6+ 个 (强信号: snapshot 锁 metadata 候选)",
            "tool_unavailable": "tmutil 工具不可用 (非 macOS)",
            "empty": "snapshots 字段缺失 (旧记录或非 macOS)",
        }
        for k, v in by_snap.items():
            explain = snap_explain.get(k, "")
            lines.append(f"  {v:>3}  {k}  — {explain}")

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

    # V37.9.29 (b): Ownership misalignment alert
    # If any incident shows non-cron-user UIDs (root 0:0 / _unknown 99:99),
    # warn about V37.9.29 path D' regression — the fix should have left only
    # one UID:GID pair matching the cron user.
    suspicious_pairs = [
        p for p in by_ownership
        if "top=0:0" in p or "kb=0:0" in p
        or "top=99:99" in p or "kb=99:99" in p
    ]
    if suspicious_pairs:
        lines.append(
            "\n  ⚠️ Ownership 警告 (V37.9.29 b): 检测到非业务用户 UID\n"
            "     - root (0:0) / _unknown (99:99) 在 incident 时刻出现\n"
            "     - V37.9.29 path D' 修复后理应仅看 cron user (e.g. 501:20)\n"
            "     - 若修复后仍有此 pattern → 假说错或回退, 立即 R1 回滚:\n"
            "       sudo mount -u -o noowners /Volumes/MOVESPEED\n"
            "       sudo diskutil disableOwnership /Volumes/MOVESPEED"
        )

    # V37.9.81 B: Sandbox-denied 警告 — 采集器自身被拒是 V37.9.80 TCC 真因的直接证据
    # 任一维度 (ACL/lsof/snapshots) 出现 sandbox_denied 都触发, 因为这意味着
    # cron 派生进程访问 /Volumes/MOVESPEED 在 kernel 层被拒, FDA 未生效或被回退.
    by_acl_local = analysis.get("by_acl_anomaly", {})
    by_handle_local = analysis.get("by_handle_pattern", {})
    by_snap_local = analysis.get("by_snapshot_bucket", {})
    sandbox_total = (by_acl_local.get("sandbox_denied", 0)
                     + by_handle_local.get("sandbox_denied", 0)
                     + by_snap_local.get("sandbox_denied", 0))
    if sandbox_total > 0:
        lines.append(
            f"\n  🚨 Sandbox 拒绝警告 (V37.9.81 B): {sandbox_total} 个维度检测到采集器自身被 TCC sandbox 拒绝\n"
            "     - 这是 V37.9.80 真因 (macOS TCC Sandbox 拒绝 cron 派生进程访问外置卷) 的直接证据\n"
            "     - 之前 V37.9.30 6 周误判: lsof/ACL 空内容被当 'normal/empty', 实为采集失败\n"
            "     - 修复路径: 系统设置 → 隐私与安全性 → 完全磁盘访问权限 → 添加 /usr/sbin/cron\n"
            "     - 验证: 添加后 24h 重跑 incident_analyzer, sandbox_denied 应降至 0"
        )

    # V37.9.30: ACL deny alert (chown 不能清, 强 EPERM 信号)
    acl_deny_count = by_acl_local.get("acl_deny", 0)
    if acl_deny_count > 0:
        lines.append(
            f"\n  🛡️ ACL deny 警告 (V37.9.30): {acl_deny_count} incidents 检测到 ACL deny 规则\n"
            "     - chown 修复 owner 但 ACL 不会被 chown 改变, 这是 V37.9.29 假说\n"
            "       证伪后的强候选根因 (chown 真生效但 EPERM 100% 持平)\n"
            "     - 修复建议: sudo ls -le@ /Volumes/MOVESPEED 看完整 ACL\n"
            "       然后 sudo chmod -RN /Volumes/MOVESPEED 清除所有 ACL"
        )

    # V37.9.30: Handle holder pattern hint (daemon contention 假说判定)
    # by_handle_local already defined above for V37.9.81 sandbox alert
    daemon_count = by_handle_local.get("daemon_dominated", 0)
    user_count = by_handle_local.get("user_only", 0)
    if daemon_count > user_count and daemon_count >= 3:
        lines.append(
            f"\n  📂 句柄持有 (V37.9.30): {daemon_count}/{analysis['count']} incidents daemon 主导\n"
            "     - 验证 V37.9.29 数据揭示的 5 daemon 100% 共现是 contention 而非相关\n"
            "     - 修复建议 (path B): cron 调度避峰 (12:00 UTC = HKT 20:00 是峰值)\n"
            "       将主要 cron job 移到非 daemon 维护时段 (避开 HKT 8/14/20 点)"
        )
    elif user_count > daemon_count and user_count >= 3:
        lines.append(
            f"\n  📂 句柄持有 (V37.9.30): {user_count}/{analysis['count']} incidents 仅用户进程持有\n"
            "     - 反证 daemon contention 假说 — 真因更可能在文件系统层 (APFS/ACL)"
        )

    # V37.9.30: Snapshot accumulation hint (TM 本地快照锁 metadata 候选)
    # by_snap_local already defined above for V37.9.81 sandbox alert
    snap_high = by_snap_local.get("snap_6_plus", 0)
    if snap_high > 0:
        lines.append(
            f"\n  📸 Snapshot 警告 (V37.9.30): {snap_high} incidents 时有 6+ TM 本地快照\n"
            "     - 即使 TM exclude /Volumes/MOVESPEED, macOS 仍可能创建本地快照\n"
            "     - 修复建议: sudo tmutil deletelocalsnapshots / 清除积累\n"
            "       并设置 sudo tmutil disablelocal 关闭本地快照机制"
        )

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
