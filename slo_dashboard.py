#!/usr/bin/env python3
"""
slo_dashboard.py — SLO Dashboard with Historical Tracking (V36: V2-P1)

Captures periodic snapshots of proxy_stats.json into a history file,
then generates a dashboard showing current metrics + trends over time.

Designed to be run by cron (e.g., every hour) to accumulate history,
and on-demand for dashboard viewing.

Usage:
  python3 slo_dashboard.py                    # Snapshot + display dashboard
  python3 slo_dashboard.py --snapshot         # Only take snapshot (cron mode)
  python3 slo_dashboard.py --dashboard        # Only display dashboard
  python3 slo_dashboard.py --json             # JSON output
  python3 slo_dashboard.py --history          # Show raw history
  python3 slo_dashboard.py --compact          # Compact one-line status
"""
import json
import os
import sys
import time
from datetime import datetime, timedelta

from config_loader import load_config

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
STATS_FILE = os.path.expanduser("~/proxy_stats.json")
HISTORY_FILE = os.path.expanduser("~/.kb/slo_history.jsonl")
# Dev fallback
_DEV_STATS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "proxy_stats.json")
_DEV_HISTORY = os.path.join(os.path.dirname(os.path.abspath(__file__)), "slo_history.jsonl")

MAX_HISTORY_ENTRIES = 720  # 30 days × 24h = 720 hourly snapshots


def _stats_path():
    return STATS_FILE if os.path.exists(STATS_FILE) else _DEV_STATS


def _history_path():
    if os.path.exists(os.path.dirname(HISTORY_FILE)):
        return HISTORY_FILE
    return _DEV_HISTORY


# ---------------------------------------------------------------------------
# Snapshot: read current stats and append to history
# ---------------------------------------------------------------------------
def read_stats(path=None):
    """Read proxy_stats.json, return dict or None."""
    path = path or _stats_path()
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def extract_snapshot(stats):
    """Extract key SLO metrics from proxy_stats into a compact snapshot."""
    if stats is None:
        return None

    slo = stats.get("slo", {})
    latency = slo.get("latency", {})
    total = stats.get("total_requests", 0)
    errors = stats.get("total_errors", 0)

    return {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "requests": total,
        "errors": errors,
        "success_pct": round((total - errors) / total * 100, 2) if total > 0 else 0,
        "p50_ms": latency.get("p50", 0),
        "p95_ms": latency.get("p95", 0),
        "p99_ms": latency.get("p99", 0),
        "fallback_count": slo.get("fallback_count", 0),
        "degradation_pct": slo.get("degradation_rate_pct", 0),
        "tool_success_pct": slo.get("tool_success_rate_pct", 100),
        "timeout_pct": slo.get("timeout_rate_pct", 0),
        "prompt_tokens": stats.get("prompt_tokens", 0),
        "total_tokens": stats.get("total_tokens", 0),
    }


def take_snapshot(stats_path=None):
    """Read current stats, append snapshot to history. Returns snapshot or None."""
    stats = read_stats(stats_path)
    if not stats:
        return None

    snapshot = extract_snapshot(stats)
    if not snapshot:
        return None

    history_path = _history_path()
    os.makedirs(os.path.dirname(history_path), exist_ok=True)

    # Append to JSONL
    with open(history_path, "a") as f:
        f.write(json.dumps(snapshot, ensure_ascii=False) + "\n")

    # Trim old entries if over limit
    _trim_history(history_path)

    return snapshot


def _trim_history(path):
    """Keep only the last MAX_HISTORY_ENTRIES entries."""
    try:
        with open(path) as f:
            lines = f.readlines()
        if len(lines) > MAX_HISTORY_ENTRIES:
            with open(path, "w") as f:
                f.writelines(lines[-MAX_HISTORY_ENTRIES:])
    except OSError:
        pass


# ---------------------------------------------------------------------------
# History: read and analyze
# ---------------------------------------------------------------------------
def load_history(path=None):
    """Load snapshot history from JSONL file."""
    path = path or _history_path()
    if not os.path.exists(path):
        return []
    entries = []
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
    except OSError:
        pass
    return entries


def filter_history(entries, hours=24):
    """Filter entries to last N hours."""
    cutoff = (datetime.now() - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%S")
    return [e for e in entries if e.get("ts", "") >= cutoff]


def compute_trends(entries):
    """Compute trend metrics from a list of snapshots."""
    if not entries:
        return {}

    p95_values = [e["p95_ms"] for e in entries if e.get("p95_ms", 0) > 0]
    success_values = [e["success_pct"] for e in entries if e.get("requests", 0) > 0]
    degradation_values = [e["degradation_pct"] for e in entries]

    total_requests = sum(e.get("requests", 0) for e in entries)
    total_errors = sum(e.get("errors", 0) for e in entries)

    trend = {
        "period_snapshots": len(entries),
        "period_start": entries[0].get("ts", "?"),
        "period_end": entries[-1].get("ts", "?"),
        "total_requests": total_requests,
        "total_errors": total_errors,
        "avg_success_pct": round(sum(success_values) / len(success_values), 2) if success_values else 0,
        "avg_p95_ms": round(sum(p95_values) / len(p95_values), 1) if p95_values else 0,
        "max_p95_ms": max(p95_values) if p95_values else 0,
        "min_p95_ms": min(p95_values) if p95_values else 0,
        "avg_degradation_pct": round(sum(degradation_values) / len(degradation_values), 2) if degradation_values else 0,
    }

    # Sparkline for p95 latency (last 12 entries)
    spark_data = p95_values[-12:] if p95_values else []
    if spark_data:
        trend["p95_sparkline"] = _sparkline(spark_data)
    else:
        trend["p95_sparkline"] = "(no data)"

    # Sparkline for success rate
    spark_success = success_values[-12:] if success_values else []
    if spark_success:
        trend["success_sparkline"] = _sparkline(spark_success)
    else:
        trend["success_sparkline"] = "(no data)"

    return trend


def _sparkline(values):
    """Generate a text sparkline from a list of numbers."""
    if not values:
        return ""
    blocks = " ▁▂▃▄▅▆▇█"
    lo, hi = min(values), max(values)
    if hi == lo:
        return blocks[4] * len(values)
    scale = (len(blocks) - 1) / (hi - lo)
    return "".join(blocks[int((v - lo) * scale)] for v in values)


# ---------------------------------------------------------------------------
# Dashboard: format output
# ---------------------------------------------------------------------------
def build_dashboard(stats=None, history=None, config=None):
    """Build complete dashboard data structure."""
    config = config or load_config()
    slo_cfg = config.get("slo", {})

    # Current snapshot
    stats = stats or read_stats()
    current = extract_snapshot(stats) if stats else None

    # History
    history = history if history is not None else load_history()
    last_24h = filter_history(history, hours=24)
    last_7d = filter_history(history, hours=168)

    dashboard = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "version": _read_version(),
        "current": current,
        "targets": {
            "p95_ms": slo_cfg.get("latency_p95_ms", 30000),
            "success_pct": 100 - slo_cfg.get("timeout_rate_pct", 3.0),
            "tool_success_pct": slo_cfg.get("tool_success_rate_pct", 95.0),
            "degradation_pct": slo_cfg.get("degradation_rate_pct", 5.0),
            "recovery_pct": slo_cfg.get("auto_recovery_rate_pct", 90.0),
        },
        "trend_24h": compute_trends(last_24h),
        "trend_7d": compute_trends(last_7d),
        "history_entries": len(history),
    }

    # SLO verdicts (current)
    if current:
        dashboard["verdicts"] = {
            "latency": "PASS" if current["p95_ms"] <= slo_cfg.get("latency_p95_ms", 30000) or current["p95_ms"] == 0 else "FAIL",
            "success": "PASS" if current["success_pct"] >= (100 - slo_cfg.get("timeout_rate_pct", 3.0)) or current["requests"] == 0 else "FAIL",
            "tools": "PASS" if current["tool_success_pct"] >= slo_cfg.get("tool_success_rate_pct", 95.0) or current["requests"] < 5 else "FAIL",
            "degradation": "PASS" if current["degradation_pct"] <= slo_cfg.get("degradation_rate_pct", 5.0) else "FAIL",
        }
        verdicts = dashboard["verdicts"]
        dashboard["overall"] = "ALL PASS" if all(v == "PASS" for v in verdicts.values()) else "VIOLATIONS"
    else:
        dashboard["verdicts"] = {}
        dashboard["overall"] = "NO DATA"

    return dashboard


def _read_version():
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "VERSION")) as f:
            return f.read().strip()
    except OSError:
        return "unknown"


def format_dashboard_md(dashboard):
    """Format dashboard as Markdown."""
    lines = []
    lines.append("# SLO Dashboard")
    lines.append(f"\n> Generated: {dashboard['generated_at']} | Version: {dashboard['version']}")
    lines.append(f"> Status: **{dashboard['overall']}** | History: {dashboard['history_entries']} snapshots")
    lines.append("")

    cur = dashboard.get("current")
    targets = dashboard["targets"]

    if not cur:
        lines.append("**No current data available.** Run proxy to generate metrics.")
        return "\n".join(lines)

    # Current metrics
    lines.append("## Current Metrics")
    lines.append("")
    lines.append("| Metric | Value | Target | Status |")
    lines.append("|--------|-------|--------|--------|")

    v = dashboard.get("verdicts", {})
    lines.append(f"| Latency p95 | {cur['p95_ms']}ms | ≤{targets['p95_ms']}ms | {v.get('latency', '?')} |")
    lines.append(f"| Success Rate | {cur['success_pct']}% | ≥{targets['success_pct']}% | {v.get('success', '?')} |")
    lines.append(f"| Tool Success | {cur['tool_success_pct']}% | ≥{targets['tool_success_pct']}% | {v.get('tools', '?')} |")
    lines.append(f"| Degradation | {cur['degradation_pct']}% | ≤{targets['degradation_pct']}% | {v.get('degradation', '?')} |")
    lines.append(f"| Requests | {cur['requests']} | — | — |")
    lines.append(f"| Errors | {cur['errors']} | — | — |")
    lines.append(f"| Fallbacks | {cur['fallback_count']} | — | — |")
    lines.append("")

    # Trends
    for label, key in [("Last 24 Hours", "trend_24h"), ("Last 7 Days", "trend_7d")]:
        trend = dashboard.get(key, {})
        if not trend or trend.get("period_snapshots", 0) == 0:
            continue

        lines.append(f"## {label} ({trend['period_snapshots']} snapshots)")
        lines.append("")
        lines.append(f"| Metric | Value |")
        lines.append(f"|--------|-------|")
        lines.append(f"| Requests | {trend['total_requests']} |")
        lines.append(f"| Errors | {trend['total_errors']} |")
        lines.append(f"| Avg Success | {trend['avg_success_pct']}% |")
        lines.append(f"| Avg p95 | {trend['avg_p95_ms']}ms |")
        lines.append(f"| Min/Max p95 | {trend['min_p95_ms']}/{trend['max_p95_ms']}ms |")
        lines.append(f"| Avg Degradation | {trend['avg_degradation_pct']}% |")
        lines.append(f"| p95 Trend | {trend.get('p95_sparkline', '')} |")
        lines.append(f"| Success Trend | {trend.get('success_sparkline', '')} |")
        lines.append("")

    return "\n".join(lines)


def format_compact(dashboard):
    """One-line compact status for cron/alerting."""
    cur = dashboard.get("current")
    if not cur:
        return "SLO: NO_DATA"
    v = dashboard.get("overall", "?")
    return (f"SLO: {v} | p95={cur['p95_ms']}ms "
            f"success={cur['success_pct']}% "
            f"degrade={cur['degradation_pct']}% "
            f"reqs={cur['requests']}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    args = sys.argv[1:]

    do_snapshot = "--snapshot" in args or not any(a.startswith("--") for a in args)
    do_dashboard = "--dashboard" in args or not any(a.startswith("--") for a in args)
    output_json = "--json" in args
    show_history = "--history" in args
    compact = "--compact" in args

    if do_snapshot and not show_history:
        snap = take_snapshot()
        if snap and not output_json and not compact:
            pass  # Silently snapshot in cron mode

    if show_history:
        history = load_history()
        if output_json:
            print(json.dumps(history, indent=2, ensure_ascii=False))
        else:
            for entry in history[-20:]:
                print(f"  {entry.get('ts', '?')} | reqs={entry.get('requests', 0)} "
                      f"p95={entry.get('p95_ms', 0)}ms "
                      f"success={entry.get('success_pct', 0)}%")
            print(f"\n  Total: {len(history)} snapshots")
        return

    if compact:
        dashboard = build_dashboard()
        print(format_compact(dashboard))
        return

    if do_dashboard:
        dashboard = build_dashboard()
        if output_json:
            print(json.dumps(dashboard, indent=2, ensure_ascii=False))
        else:
            print(format_dashboard_md(dashboard))


if __name__ == "__main__":
    main()
