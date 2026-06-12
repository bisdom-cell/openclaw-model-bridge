# SLO Benchmark Report

> Generated: 2026-06-12 10:58:03
> Source: proxy_stats.json (live production metrics)
> Verdict: **OBSERVING (insufficient samples — 观察中, 样本不足不判定)** (0/5 passed, 4 observing, 0 fail, 1 n/a; min samples ≥200)

## Traffic Summary

| Metric | Value |
|--------|-------|
| Total Requests | 25 |
| Total Errors | 0 |
| Overall Success Rate | 100.0% |

## Latency Distribution

| Percentile | Value | Target | Verdict |
|------------|-------|--------|---------|
| p50 | 24751ms | — | — |
| **p95** | **33630ms** | **≤50000ms** | **OBSERVING** |
| p99 | 39306ms | — | — |
| max | 39306ms | — | — |
| samples | 25 | ≥200 | OBSERVING |

## Error Classification

| Type | Count |
|------|-------|
| Timeout | 0 |
| Context Overflow | 0 |
| Backend (502/503) | 0 |
| Other | 0 |
| **Timeout Rate** | **0.0%** (target: ≤3.0%) → **OBSERVING** |

## SLO Compliance Matrix

| SLO Metric | Actual | Target | Verdict |
|------------|--------|--------|---------|
| Latency p95 | 33630ms | ≤50000ms | OBSERVING |
| Tool Success Rate | 0.0% | ≥95.0% | N_A_NO_TOOL_CALLS |
| Degradation Rate | 0.0% | ≤5.0% | OBSERVING |
| Timeout Rate | 0.0% | ≤3.0% | OBSERVING |
| Auto Recovery Rate | 100.0% | ≥90.0% | OBSERVING |

## Trend Windows (24h / 7d)

> History: 595 snapshots (`~/.kb/slo_history.jsonl`, hourly via `slo_snapshot.sh`)

| Window | Snapshots | Requests | Errors | Avg Success | Avg p95 | Max p95 | Avg Degradation |
|--------|-----------|----------|--------|-------------|---------|---------|-----------------|
| Last 24h | 24 | 576 | 0 | 100.0% | 42785.0ms | 82810ms | 0.0% |
| Last 7d | 168 | 11348 | 183 | 97.94% | 47899.8ms | 82810ms | 0.0% |

## Token Usage

| Metric | Value |
|--------|-------|
| Prompt Tokens (today) | 0 |
| Total Tokens (today) | 0 |
| Avg Tokens/Request | 0 |

## Threshold Rationale

- **Latency p95 target = 50000ms（非 V36 原始 30000ms）**: V37.9.79 (2026-05-18) 基于
  Mac Mini 实测调整 — proxy_stats.json 显示 p50=26.3s / p95=37.5s / p99=53.3s（整体 baseline
  而非 outlier），proxy.log 单次 backend 29.7s 直接证据。根因: 远端 Qwen3 真实性能 baseline
  ~30-40s p95，比 V36 设计假设慢一倍。**调阈值是承认当前 LLM provider 真实性能，不是掩盖问题**。
- **恢复 30000ms 的条件**: multi-provider routing（doubao 试水 V37.9.55+）或更快 LLM backend
  稳定后恢复（V37.9.80+ 候选）。当前值是 short-term realistic baseline。
- 其余阈值（tool success ≥ / degradation ≤ / timeout ≤ / recovery ≥）为 V33 阈值中心化原始值，
  未调整。全部定义于 `config.yaml` `slo:` 段（单一真理源），本报告动态读取。

## Methodology

- **Data source**: `~/proxy_stats.json` — live production metrics collected by Tool Proxy
- **Latency**: Measured end-to-end from proxy request start to LLM response (includes network + inference)
- **Rolling buffer**: Last 200 requests for latency percentiles; daily reset at midnight for counters
- **SLO targets**: Defined in `config.yaml`, evaluated by `slo_checker.py`
- **Thresholds**: latency p95 ≤50s, tool success ≥95%, degradation ≤5%, timeout ≤3%, recovery ≥90%
- **Verdict states (V37.9.143)**: PASS / FAIL / OBSERVING (样本 < min_sample_count 不判定) / N_A_NO_TOOL_CALLS (无工具调用流量不可评判)

