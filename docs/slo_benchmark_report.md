# SLO Benchmark Report

> Generated: 2026-04-05 10:04:12
> Source: proxy_stats.json (live production metrics)
> Verdict: **ALL PASS** (5/5 checks passed)

## Traffic Summary

| Metric | Value |
|--------|-------|
| Total Requests | 1 |
| Total Errors | 0 |
| Overall Success Rate | 100.0% |

## Latency Distribution

| Percentile | Value | Target | Verdict |
|------------|-------|--------|---------|
| p50 | 459ms | — | — |
| **p95** | **459ms** | **≤30000ms** | **PASS** |
| p99 | 459ms | — | — |
| max | 459ms | — | — |
| samples | 1 | ≥5 | — |

## Error Classification

| Type | Count |
|------|-------|
| Timeout | 0 |
| Context Overflow | 0 |
| Backend (502/503) | 0 |
| Other | 0 |
| **Timeout Rate** | **0.0%** (target: ≤3.0%) → **PASS** |

## SLO Compliance Matrix

| SLO Metric | Actual | Target | Verdict |
|------------|--------|--------|---------|
| Latency p95 | 459ms | ≤30000ms | PASS |
| Tool Success Rate | 0.0% | ≥95.0% | PASS |
| Degradation Rate | 0.0% | ≤5.0% | PASS |
| Timeout Rate | 0.0% | ≤3.0% | PASS |
| Auto Recovery Rate | 100.0% | ≥90.0% | PASS |

## Token Usage

| Metric | Value |
|--------|-------|
| Prompt Tokens (today) | 0 |
| Total Tokens (today) | 0 |
| Avg Tokens/Request | 0 |

## Methodology

- **Data source**: `~/proxy_stats.json` — live production metrics collected by Tool Proxy
- **Latency**: Measured end-to-end from proxy request start to LLM response (includes network + inference)
- **Rolling buffer**: Last 200 requests for latency percentiles; daily reset at midnight for counters
- **SLO targets**: Defined in `config.yaml`, evaluated by `slo_checker.py`
- **Thresholds**: latency p95 ≤30s, tool success ≥95%, degradation ≤5%, timeout ≤3%, recovery ≥90%
