#!/usr/bin/env python3
"""
slo_benchmark.py — SLO Benchmark 实验报告生成器（V35）

将 SLO 从规则变成实验结果。读取 proxy_stats.json 收集的真实运行数据，
生成格式化的 benchmark 报告（Markdown / JSON），包含：
- 延迟分布（p50/p95/p99/max）
- 成功率 / 错误分类
- 降级率 / 恢复率
- 工具调用统计
- SLO 合规判定

用法：
  python3 slo_benchmark.py                # Markdown 报告（stdout）
  python3 slo_benchmark.py --json         # JSON 格式
  python3 slo_benchmark.py --save         # 保存到 docs/slo_benchmark_report.md
  python3 slo_benchmark.py --from FILE    # 从指定文件读取（默认 ~/proxy_stats.json）
"""
import json
import os
import sys
import time

from config_loader import load_config

DEFAULT_STATS = os.path.expanduser("~/proxy_stats.json")


def read_stats(path):
    """读取 proxy_stats.json"""
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def build_report(stats, config):
    """从 proxy_stats 构建 benchmark 报告数据结构"""
    slo_cfg = config.get("slo", {})
    slo_data = stats.get("slo", {})
    latency = slo_data.get("latency", {})
    errors = slo_data.get("errors_by_type", {})
    total = stats.get("total_requests", 0)
    total_errors = stats.get("total_errors", 0)

    report = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "data_source": "proxy_stats.json (live production metrics)",
        "observation_window": {
            "total_requests": total,
            "total_errors": total_errors,
            "success_rate_pct": round((total - total_errors) / total * 100, 2) if total > 0 else 0,
        },
        "latency": {
            "p50_ms": latency.get("p50", 0),
            "p95_ms": latency.get("p95", 0),
            "p99_ms": latency.get("p99", 0),
            "max_ms": latency.get("max", 0),
            "sample_count": latency.get("count", 0),
            "target_p95_ms": slo_cfg.get("latency_p95_ms", 30000),
            "verdict": "PASS" if latency.get("p95", 0) <= slo_cfg.get("latency_p95_ms", 30000) or latency.get("count", 0) < 5 else "FAIL",
        },
        "errors": {
            "timeout": errors.get("timeout", 0),
            "context_overflow": errors.get("context_overflow", 0),
            "backend": errors.get("backend", 0),
            "other": errors.get("other", 0),
            "timeout_rate_pct": slo_data.get("timeout_rate_pct", 0),
            "target_timeout_pct": slo_cfg.get("timeout_rate_pct", 3.0),
            "verdict": "PASS" if slo_data.get("timeout_rate_pct", 0) <= slo_cfg.get("timeout_rate_pct", 3.0) else "FAIL",
        },
        "tools": {
            "total_calls": slo_data.get("tool_calls_total", 0),
            "success_calls": slo_data.get("tool_calls_success", 0),
            "success_rate_pct": slo_data.get("tool_success_rate_pct", 100.0),
            "target_pct": slo_cfg.get("tool_success_rate_pct", 95.0),
            "verdict": "PASS" if slo_data.get("tool_success_rate_pct", 100.0) >= slo_cfg.get("tool_success_rate_pct", 95.0) or slo_data.get("tool_calls_total", 0) == 0 else "FAIL",
        },
        "degradation": {
            "fallback_count": slo_data.get("fallback_count", 0),
            "degradation_rate_pct": slo_data.get("degradation_rate_pct", 0),
            "target_pct": slo_cfg.get("degradation_rate_pct", 5.0),
            "verdict": "PASS" if slo_data.get("degradation_rate_pct", 0) <= slo_cfg.get("degradation_rate_pct", 5.0) else "FAIL",
        },
        "recovery": {
            "recovery_total": slo_data.get("recovery_total", 0),
            "failure_streaks": slo_data.get("failure_streaks", 0),
            "auto_recovery_rate_pct": slo_data.get("auto_recovery_rate_pct", 100.0),
            "target_pct": slo_cfg.get("auto_recovery_rate_pct", 90.0),
            "verdict": "PASS" if slo_data.get("auto_recovery_rate_pct", 100.0) >= slo_cfg.get("auto_recovery_rate_pct", 90.0) else "FAIL",
        },
        "tokens": {
            "prompt_tokens": stats.get("prompt_tokens", 0),
            "total_tokens": stats.get("total_tokens", 0),
        },
    }

    verdicts = [report[k]["verdict"] for k in ("latency", "errors", "tools", "degradation", "recovery")]
    report["overall_verdict"] = "ALL PASS" if all(v == "PASS" for v in verdicts) else "VIOLATIONS DETECTED"
    report["pass_count"] = sum(1 for v in verdicts if v == "PASS")
    report["total_checks"] = len(verdicts)

    return report


def format_markdown(report):
    """格式化为 Markdown 报告"""
    lines = []
    lines.append("# SLO Benchmark Report")
    lines.append("")
    lines.append(f"> Generated: {report['generated_at']}")
    lines.append(f"> Source: {report['data_source']}")
    lines.append(f"> Verdict: **{report['overall_verdict']}** ({report['pass_count']}/{report['total_checks']} checks passed)")
    lines.append("")

    # Observation window
    obs = report["observation_window"]
    lines.append("## Traffic Summary")
    lines.append("")
    lines.append(f"| Metric | Value |")
    lines.append(f"|--------|-------|")
    lines.append(f"| Total Requests | {obs['total_requests']} |")
    lines.append(f"| Total Errors | {obs['total_errors']} |")
    lines.append(f"| Overall Success Rate | {obs['success_rate_pct']}% |")
    lines.append("")

    # Latency
    lat = report["latency"]
    lines.append("## Latency Distribution")
    lines.append("")
    lines.append(f"| Percentile | Value | Target | Verdict |")
    lines.append(f"|------------|-------|--------|---------|")
    lines.append(f"| p50 | {lat['p50_ms']}ms | — | — |")
    lines.append(f"| **p95** | **{lat['p95_ms']}ms** | **≤{lat['target_p95_ms']}ms** | **{lat['verdict']}** |")
    lines.append(f"| p99 | {lat['p99_ms']}ms | — | — |")
    lines.append(f"| max | {lat['max_ms']}ms | — | — |")
    lines.append(f"| samples | {lat['sample_count']} | ≥5 | — |")
    lines.append("")

    # Error breakdown
    err = report["errors"]
    lines.append("## Error Classification")
    lines.append("")
    lines.append(f"| Type | Count |")
    lines.append(f"|------|-------|")
    lines.append(f"| Timeout | {err['timeout']} |")
    lines.append(f"| Context Overflow | {err['context_overflow']} |")
    lines.append(f"| Backend (502/503) | {err['backend']} |")
    lines.append(f"| Other | {err['other']} |")
    lines.append(f"| **Timeout Rate** | **{err['timeout_rate_pct']}%** (target: ≤{err['target_timeout_pct']}%) → **{err['verdict']}** |")
    lines.append("")

    # SLO Summary Table
    tools = report["tools"]
    deg = report["degradation"]
    rec = report["recovery"]
    lines.append("## SLO Compliance Matrix")
    lines.append("")
    lines.append(f"| SLO Metric | Actual | Target | Verdict |")
    lines.append(f"|------------|--------|--------|---------|")
    lines.append(f"| Latency p95 | {lat['p95_ms']}ms | ≤{lat['target_p95_ms']}ms | {lat['verdict']} |")
    lines.append(f"| Tool Success Rate | {tools['success_rate_pct']}% | ≥{tools['target_pct']}% | {tools['verdict']} |")
    lines.append(f"| Degradation Rate | {deg['degradation_rate_pct']}% | ≤{deg['target_pct']}% | {deg['verdict']} |")
    lines.append(f"| Timeout Rate | {err['timeout_rate_pct']}% | ≤{err['target_timeout_pct']}% | {err['verdict']} |")
    lines.append(f"| Auto Recovery Rate | {rec['auto_recovery_rate_pct']}% | ≥{rec['target_pct']}% | {rec['verdict']} |")
    lines.append("")

    # Token usage
    tok = report["tokens"]
    lines.append("## Token Usage")
    lines.append("")
    lines.append(f"| Metric | Value |")
    lines.append(f"|--------|-------|")
    lines.append(f"| Prompt Tokens (today) | {tok['prompt_tokens']:,} |")
    lines.append(f"| Total Tokens (today) | {tok['total_tokens']:,} |")
    if report["observation_window"]["total_requests"] > 0:
        avg = tok["total_tokens"] // report["observation_window"]["total_requests"]
        lines.append(f"| Avg Tokens/Request | {avg:,} |")
    lines.append("")

    # Methodology
    lines.append("## Methodology")
    lines.append("")
    lines.append("- **Data source**: `~/proxy_stats.json` — live production metrics collected by Tool Proxy")
    lines.append("- **Latency**: Measured end-to-end from proxy request start to LLM response (includes network + inference)")
    lines.append("- **Rolling buffer**: Last 200 requests for latency percentiles; daily reset at midnight for counters")
    lines.append("- **SLO targets**: Defined in `config.yaml`, evaluated by `slo_checker.py`")
    lines.append("- **Thresholds**: latency p95 ≤30s, tool success ≥95%, degradation ≤5%, timeout ≤3%, recovery ≥90%")
    lines.append("")

    return "\n".join(lines)


def main():
    stats_path = DEFAULT_STATS
    if "--from" in sys.argv:
        idx = sys.argv.index("--from")
        if idx + 1 < len(sys.argv):
            stats_path = sys.argv[idx + 1]

    stats = read_stats(stats_path)
    if not stats:
        print(f"Error: Cannot read {stats_path}", file=sys.stderr)
        return 1

    if "slo" not in stats:
        print("Error: No SLO data in stats (proxy may need restart with V32+ code)", file=sys.stderr)
        return 1

    config = load_config()
    report = build_report(stats, config)

    if "--json" in sys.argv:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0

    md = format_markdown(report)

    if "--save" in sys.argv:
        out_path = os.path.join(os.path.dirname(__file__), "docs", "slo_benchmark_report.md")
        with open(out_path, "w") as f:
            f.write(md + "\n")
        print(f"Report saved to {out_path}")
        return 0

    print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
