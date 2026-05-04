#!/usr/bin/env python3
"""movespeed_incident_monitor.py — V37.9.26 watchdog 集成 helper

监控 ~/.kb/movespeed_incidents.jsonl 的 24h 窗口 incident 累积情况。

V37.9.14 已落地 20 处 rsync fail-loud → movespeed_incident_capture.sh 写
JSONL 取证. V37.9.26 升级被动证据收集为主动告警: 24h 内 ≥N 条 incident
视为 "B 问题（exfat fskit transient EPERM）连续性复发", 推 [SYSTEM_ALERT].

阈值 5 来自 V37.9.4 案例一周内 18 次 rsync 失败的经验值
(24h 平均 ~3 次为噪声基线; ≥5 视为异常爆发).

Output 格式 (single line stdout): "{count}|{threshold_hit}|{callers}"
  count: int — 24h 窗口内 incident 条目数
  threshold_hit: "1" if count >= threshold else "0"
  callers: "/" 分隔的 caller basename 集合 (前 5 个, 去重)

异常路径:
  - 文件不存在 → 调用方 (watchdog) 应在 shell 层 [ -f ... ] 检查, 不调本脚本
  - 文件存在但损坏行 → parse_errors 计数, 其他行继续
  - 缺 timestamp_iso 字段 → 跳过该条, 不抛异常
  - 整体 IO 失败 → 输出 "0|0|file_read_error" 让 watchdog 降级处理 (FAIL-OPEN)

CLI:
  python3 movespeed_incident_monitor.py <incident_file> <now_epoch> <threshold>
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone


def parse_iso_to_epoch(ts_iso: str) -> int:
    """Parse ISO 8601 timestamp to Unix epoch (UTC).

    Handles:
      - Trailing 'Z' (Zulu time)
      - Naive datetime (assumed UTC)
      - Already-aware datetime with offset

    Raises:
        ValueError: if ts_iso is empty / unparseable / not str
    """
    if not isinstance(ts_iso, str) or not ts_iso:
        raise ValueError(f"empty or non-string timestamp: {ts_iso!r}")
    if ts_iso.endswith("Z"):
        ts_iso = ts_iso[:-1] + "+00:00"
    dt = datetime.fromisoformat(ts_iso)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def extract_caller_basename(caller: str) -> str:
    """Extract basename from caller path.

    Examples:
      "/Users/foo/jobs/run_freight.sh" → "run_freight.sh"
      "kb_dream.sh" → "kb_dream.sh"
      "" → "?"
    """
    if not caller:
        return "?"
    if "/" in caller:
        return caller.rsplit("/", 1)[-1]
    return caller


def count_recent_incidents(incident_file: str, now_epoch: int,
                            window_seconds: int = 86400) -> tuple[int, list[str], int]:
    """Count incidents within [now_epoch - window, now_epoch] from JSONL file.

    Args:
        incident_file: path to JSONL file (one record per line)
        now_epoch: current Unix epoch timestamp (caller passes for testability)
        window_seconds: window size, default 86400 (24h)

    Returns:
        (count, recent_callers, parse_errors)
            count: int — number of incidents within window
            recent_callers: list[str] — unique caller basenames in encounter order
            parse_errors: int — count of malformed lines / parse failures

    Raises:
        IOError / OSError: on file read failure (caller decides FAIL-OPEN policy)
    """
    window_start = now_epoch - window_seconds
    count = 0
    recent_callers: list[str] = []
    parse_errors = 0

    with open(incident_file, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except (ValueError, TypeError):
                parse_errors += 1
                continue
            if not isinstance(rec, dict):
                parse_errors += 1
                continue
            ts_iso = rec.get("timestamp_iso", "")
            if not ts_iso:
                continue
            try:
                ts_epoch = parse_iso_to_epoch(ts_iso)
            except (ValueError, TypeError):
                parse_errors += 1
                continue
            if ts_epoch >= window_start:
                count += 1
                caller_bn = extract_caller_basename(rec.get("caller", ""))
                if caller_bn not in recent_callers:
                    recent_callers.append(caller_bn)
    return count, recent_callers, parse_errors


def format_watchdog_output(count: int, threshold: int,
                            callers: list[str], max_callers: int = 5) -> str:
    """Format watchdog-consumable output: '{count}|{threshold_hit}|{callers}'."""
    threshold_hit = "1" if count >= threshold else "0"
    callers_str = "/".join(callers[:max_callers])
    return f"{count}|{threshold_hit}|{callers_str}"


def _cli():
    if len(sys.argv) < 4:
        print("usage: movespeed_incident_monitor.py <file> <now_epoch> <threshold>",
              file=sys.stderr)
        sys.exit(2)
    incident_file = sys.argv[1]
    try:
        now_epoch = int(sys.argv[2])
        threshold = int(sys.argv[3])
    except (ValueError, TypeError):
        print("ERROR: now_epoch and threshold must be integers", file=sys.stderr)
        sys.exit(2)

    try:
        count, callers, _parse_errors = count_recent_incidents(incident_file, now_epoch)
    except (IOError, OSError):
        # FAIL-OPEN: file read failure → 0 count, watchdog continues
        print("0|0|file_read_error")
        sys.exit(0)

    print(format_watchdog_output(count, threshold, callers))


if __name__ == "__main__":
    _cli()
