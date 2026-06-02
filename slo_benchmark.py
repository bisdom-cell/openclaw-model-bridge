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


# V37.9.99 (外部评审 P0): SLO 样本门槛 — 样本不足时 verdict=OBSERVING (观察中, 不判定 PASS).
# 修 V35 golden trace samples=1 却标 ALL PASS 的统计无意义问题 (1 个样本的 p95 不是 SLO).
# 默认 200 (= 延迟 rolling buffer 满). 可经 config slo.min_sample_count 调低 (低流量个人系统).
MIN_SAMPLE_THRESHOLD = 200


def _verdict(meets_target, sample_count, min_samples):
    """三态 SLO 判定: 样本不足→OBSERVING (不判定), 达标→PASS, 否则→FAIL.

    sample_count < min_samples → OBSERVING (统计样本不足, 既不报 PASS 也不报 FAIL).
    防止低流量/demo trace 误标 PASS (外部评审 P0: samples=1 标 PASS 是过度声明).
    """
    if sample_count < min_samples:
        return "OBSERVING"
    return "PASS" if meets_target else "FAIL"


def build_report(stats, config):
    """从 proxy_stats 构建 benchmark 报告数据结构"""
    slo_cfg = config.get("slo", {})
    slo_data = stats.get("slo", {})
    latency = slo_data.get("latency", {})
    errors = slo_data.get("errors_by_type", {})
    total = stats.get("total_requests", 0)
    total_errors = stats.get("total_errors", 0)

    # V37.9.99: 样本门槛 (config 可调, 默认 200). 每个 check 用各自的样本基数判定.
    min_samples = slo_cfg.get("min_sample_count", MIN_SAMPLE_THRESHOLD)
    lat_samples = latency.get("count", 0)
    tool_total = slo_data.get("tool_calls_total", 0)
    rec_streaks = slo_data.get("failure_streaks", 0)

    lat_verdict = _verdict(
        latency.get("p95", 0) <= slo_cfg.get("latency_p95_ms", 30000),
        lat_samples, min_samples)
    err_verdict = _verdict(
        slo_data.get("timeout_rate_pct", 0) <= slo_cfg.get("timeout_rate_pct", 3.0),
        total, min_samples)
    tool_verdict = _verdict(
        slo_data.get("tool_success_rate_pct", 100.0) >= slo_cfg.get("tool_success_rate_pct", 95.0),
        tool_total, min_samples)
    deg_verdict = _verdict(
        slo_data.get("degradation_rate_pct", 0) <= slo_cfg.get("degradation_rate_pct", 5.0),
        total, min_samples)
    rec_verdict = _verdict(
        slo_data.get("auto_recovery_rate_pct", 100.0) >= slo_cfg.get("auto_recovery_rate_pct", 90.0),
        rec_streaks, min_samples)

    report = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "data_source": "proxy_stats.json (live production metrics)",
        "min_sample_threshold": min_samples,
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
            "sample_count": lat_samples,
            "target_p95_ms": slo_cfg.get("latency_p95_ms", 30000),
            "verdict": lat_verdict,
        },
        "errors": {
            "timeout": errors.get("timeout", 0),
            "context_overflow": errors.get("context_overflow", 0),
            "backend": errors.get("backend", 0),
            "other": errors.get("other", 0),
            "timeout_rate_pct": slo_data.get("timeout_rate_pct", 0),
            "target_timeout_pct": slo_cfg.get("timeout_rate_pct", 3.0),
            "verdict": err_verdict,
        },
        "tools": {
            "total_calls": tool_total,
            "success_calls": slo_data.get("tool_calls_success", 0),
            "success_rate_pct": slo_data.get("tool_success_rate_pct", 100.0),
            "target_pct": slo_cfg.get("tool_success_rate_pct", 95.0),
            "verdict": tool_verdict,
        },
        "degradation": {
            "fallback_count": slo_data.get("fallback_count", 0),
            "degradation_rate_pct": slo_data.get("degradation_rate_pct", 0),
            "target_pct": slo_cfg.get("degradation_rate_pct", 5.0),
            "verdict": deg_verdict,
        },
        "recovery": {
            "recovery_total": slo_data.get("recovery_total", 0),
            "failure_streaks": rec_streaks,
            "auto_recovery_rate_pct": slo_data.get("auto_recovery_rate_pct", 100.0),
            "target_pct": slo_cfg.get("auto_recovery_rate_pct", 90.0),
            "verdict": rec_verdict,
        },
        "tokens": {
            "prompt_tokens": stats.get("prompt_tokens", 0),
            "total_tokens": stats.get("total_tokens", 0),
        },
    }

    verdicts = [lat_verdict, err_verdict, tool_verdict, deg_verdict, rec_verdict]
    # V37.9.99 三态汇总优先级: FAIL > OBSERVING > PASS (有 FAIL 报违规, 否则有样本不足报观察中)
    if any(v == "FAIL" for v in verdicts):
        report["overall_verdict"] = "VIOLATIONS DETECTED"
    elif any(v == "OBSERVING" for v in verdicts):
        report["overall_verdict"] = "OBSERVING (insufficient samples — 观察中, 样本不足不判定)"
    else:
        report["overall_verdict"] = "ALL PASS"
    report["pass_count"] = sum(1 for v in verdicts if v == "PASS")
    report["observing_count"] = sum(1 for v in verdicts if v == "OBSERVING")
    report["fail_count"] = sum(1 for v in verdicts if v == "FAIL")
    report["total_checks"] = len(verdicts)

    return report


def format_markdown(report):
    """格式化为 Markdown 报告"""
    lines = []
    lines.append("# SLO Benchmark Report")
    lines.append("")
    lines.append(f"> Generated: {report['generated_at']}")
    lines.append(f"> Source: {report['data_source']}")
    lines.append(f"> Verdict: **{report['overall_verdict']}** ({report['pass_count']}/{report['total_checks']} passed, {report.get('observing_count', 0)} observing, {report.get('fail_count', 0)} fail; min samples ≥{report.get('min_sample_threshold', 200)})")
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
    lines.append(f"| samples | {lat['sample_count']} | ≥{report.get('min_sample_threshold', 200)} | {'OBSERVING' if lat['sample_count'] < report.get('min_sample_threshold', 200) else '✓'} |")
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
