# Operational Resilience Report

> Generated: 2026-04-05 | Version: 0.36.0 | Test Suite: reliability_bench.py + gameday.sh

## Executive Summary

This report documents the fault injection experiments and recovery characteristics
of the openclaw-model-bridge agent runtime. All 7 systematic fault scenarios pass
with 47/47 checks. The system demonstrates:

- **Automatic failover** in < 1s when primary LLM provider goes down
- **Timeout enforcement** at 1s granularity with no request leaks
- **Self-healing** via circuit breaker that auto-resets after cooldown
- **Input sanitization** that silently fixes 7 classes of malformed tool arguments
- **Graceful degradation** across all layers when dependencies are unavailable

**Verdict: 7/7 PASS | 47/47 checks | 0 incidents during testing**

---

## Test Environment

| Property | Value |
|----------|-------|
| Test tool | `reliability_bench.py` (mock-based, no live services needed) |
| Supplementary | `gameday.sh` (live services, Mac Mini only) |
| Total scenarios | 7 (bench) + 5 (gameday) = 12 |
| Execution time | ~2.2s (bench), ~5min (gameday) |
| Dependencies mocked | LLM providers, network timeouts, file corruption |

---

## Fault Injection Experiments

### Experiment 1: Provider Unavailable — Failover & Recovery

**Hypothesis**: When the primary LLM provider (Qwen3-235B) becomes unreachable,
the circuit breaker should open, route to fallback (Gemini 2.5 Flash), and
auto-recover when the primary returns.

**Injection**: Simulate 3 consecutive failures (configurable threshold).

**Results**:

| Phase | Expected | Actual | Time |
|-------|----------|--------|------|
| Initial state | Circuit closed | closed | 0ms |
| After 3 failures | Circuit open | open | < 1ms |
| During open state | Requests skip primary | is_open=True | 0ms |
| After reset period (1s) | Half-open (probe allowed) | half-open | 1102ms |
| After successful probe | Circuit closed | closed | < 1ms |

**Recovery characteristics**:
- **Detection time**: Immediate (failure count is synchronous)
- **Failover time**: 0ms (circuit breaker decision is in-memory)
- **Recovery time**: Configurable via `circuit_breaker_reset_seconds` (default: 300s)
- **Data loss**: None — failed request gets fallback response in same HTTP cycle

**Production config** (`config.yaml`):
```
circuit_breaker_threshold: 5       # 5 consecutive failures to open
circuit_breaker_reset_seconds: 300  # 5 min cooldown before retry
fallback_timeout_ms: 60000         # fallback uses shorter timeout
max_retries: 0                     # no retry, direct failover
```

**Fallback matrix** (7 providers registered):
```
Primary: Qwen3-235B (qwen) → Fallback: Gemini 2.5 Flash (gemini)
```

**Verdict**: PASS (10/10 checks)

---

### Experiment 2: Tool Call Timeout — No Hanging Requests

**Hypothesis**: When the backend hangs indefinitely, the request should timeout
within budget and return an error, not block the proxy thread.

**Injection**: Start a TCP server that accepts connections but never responds.
Send request with 1s timeout.

**Results**:

| Metric | Value |
|--------|-------|
| Timeout detected | Yes (URLError/socket.timeout) |
| Actual elapsed | 1007ms |
| Budget ceiling | 3000ms |
| Thread blocked | No (returns to caller) |

**Production timeouts** (`config.yaml`):
```
backend_timeout_seconds: 300        # primary request
fallback_timeout_ms: 60000          # fallback request
followup_llm_timeout_seconds: 60    # search_kb LLM followup
data_clean_timeout_seconds: 60      # data cleaning subprocess
health_check_timeout_seconds: 5     # /health probes
```

**Verdict**: PASS (2/2 checks)

---

### Experiment 3: Malformed Tool Arguments — Auto-Repair

**Hypothesis**: When the LLM returns tool calls with wrong parameter names,
extra parameters, or invalid values, the proxy should silently fix them
rather than failing.

**Injection**: 7 classes of malformed arguments.

| Fault Class | Input | Expected Fix | Result |
|-------------|-------|-------------|--------|
| Wrong param name (read) | `{file_path: "/tmp/f"}` | `{path: "/tmp/f"}` | PASS |
| Extra params (web_search) | `{query: "x", limit: 10, lang: "en"}` | `{query: "x"}` | PASS |
| Wrong param name (exec) | `{cmd: "ls"}` | `{command: "ls"}` | PASS |
| Invalid browser profile | `{profile: "hacker"}` | `{profile: "openclaw"}` | PASS |
| Missing browser profile | `{selector: "#btn"}` | `{selector: "#btn", profile: "openclaw"}` | PASS |
| Invalid JSON arguments | `"not json"` | No crash, graceful handling | PASS |
| Write content alias | `{path: "f", text: "hi"}` | `{path: "f", content: "hi"}` | PASS |

**Repair coverage**: 5 alias mappings (read/exec/write/web_search) + browser profile enforcement + extra param stripping.

**Verdict**: PASS (7/7 checks)

---

### Experiment 4: Oversized Request — Intelligent Truncation

**Hypothesis**: When conversation history exceeds 200KB, the proxy should
truncate old messages while preserving system prompts and recent context.

**Injection**: 101 messages totaling 407KB (1 system + 50 user/assistant pairs × 4KB each).

| Metric | Value |
|--------|-------|
| Input size | 407,465 bytes |
| Output size (normal) | 197,067 bytes (< 200KB limit) |
| Messages dropped | 52 |
| System messages preserved | Yes (all) |
| Most recent message kept | Yes (msg #49) |
| Output size (high context, 88%) | 47,348 bytes (aggressive truncation) |

**Dynamic truncation thresholds** (`config.yaml`):
```
> 85% context usage → max 50KB messages (aggressive)
> 70% context usage → max 100KB messages (moderate)
< 70% context usage → default 200KB (no extra trimming)
```

**Verdict**: PASS (6/6 checks)

---

### Experiment 5: KB Miss-Hit — Graceful Empty Result

**Hypothesis**: When a knowledge base search finds no results, the system
should return an informative empty response, not an error.

**Injection**: Verify tool registration, schema correctness, and filter behavior.

| Check | Result |
|-------|--------|
| search_kb registered as custom tool | PASS |
| search_kb NOT in standard whitelist (custom-intercepted) | PASS |
| Schema has required `query` field | PASS |
| Schema has `source` filter (arxiv/hf/hn/etc.) | PASS |
| Schema has `recent_hours` filter | PASS |
| data_clean also registered | PASS |
| Custom tools injected after whitelist filtering | PASS |
| Unknown tools correctly filtered out | PASS |

**Miss-hit response format** (tool_proxy.py):
```
知识库中未找到与「{query}」相关的内容。
知识库包含 ArXiv/HF/S2/DBLP/ACL 论文和 HN 热帖，每日自动更新。
```

**Verdict**: PASS (9/9 checks)

---

### Experiment 6: Cron Drift Detection

**Hypothesis**: When cron heartbeat goes stale or job registry has errors,
the monitoring system should detect it.

**Injection**: Write fresh and stale (2h old) heartbeat timestamps.

| Check | Result |
|-------|--------|
| Fresh heartbeat (0s age) detected as healthy | PASS |
| Stale heartbeat (7200s age) detected as stale | PASS |
| Registry loads (34 job entries) | PASS |
| All registry entries pass validation | PASS |
| 14 jobs have silence timeouts defined | PASS |

**Production monitoring**:
- `cron_canary.sh` writes heartbeat every 10 minutes
- `job_watchdog.sh` checks all job status files every 4 hours
- Stale threshold: 30 minutes (configurable)

**Verdict**: PASS (5/5 checks)

---

### Experiment 7: State Corruption — Detection & Recovery

**Hypothesis**: Corrupted JSON state files should be detected immediately,
never silently consumed. Atomic writes should prevent corruption.

**Injection**: Corrupt JSON, truncated JSON, empty files, missing keys.

| Fault | Detection | Method |
|-------|-----------|--------|
| Invalid JSON (`{invalid]]}`) | JSONDecodeError raised | json.load() |
| Truncated JSON (`{"task":"test"`) | JSONDecodeError raised | json.load() |
| Empty file | JSONDecodeError raised | json.load() |
| Missing required keys | Set difference detected | Schema validation |
| Atomic write (tmp + rename) | Content preserved | os.replace() |
| Tmp file cleanup | No leftover .tmp | Verified post-rename |

**Atomic write pattern** (used by all state files):
```python
tmp = target + ".tmp"
with open(tmp, "w") as f:
    f.write(data)
os.replace(tmp, target)  # atomic on same filesystem
```

**Verdict**: PASS (8/8 checks)

---

## Recovery Time Summary

| Failure Mode | Detection | Recovery | User Impact |
|-------------|-----------|----------|-------------|
| Primary LLM down | Immediate | 0ms (failover) / 300s (auto-heal) | Fallback model used, slightly different quality |
| Backend timeout | 1-300s (configurable) | Immediate error return | Request fails, user can retry |
| Malformed tool args | Immediate | 0ms (auto-repair) | None — transparent fix |
| Oversized request | Immediate | 0ms (truncation) | Old context dropped, recent preserved |
| KB search miss | Immediate | N/A | Informative empty message |
| Cron drift | 10-30 min (heartbeat check) | Manual investigation | Delayed job output |
| State corruption | Immediate (on read) | Manual restore from backup | Temporary state loss |

## Comparison: Dev Bench vs Production GameDay

| Aspect | reliability_bench.py | gameday.sh |
|--------|---------------------|------------|
| Environment | Dev (any machine) | Mac Mini (live services) |
| Scenarios | 7 (mock-based) | 5 (real HTTP calls) |
| Checks | 47 | ~20 |
| Duration | ~2s | ~5min |
| Risk | Zero | Low (brief service impact) |
| Use case | Pre-push validation | Quarterly drill |

## Recommendations

1. **Run `reliability_bench.py` on every push** — already integrated into `full_regression.sh`
2. **Run `gameday.sh --all` quarterly** — validates live service behavior
3. **Add chaos testing for disk full** — `proxy_stats.json` and `status.json` writes could fail
4. **Add network partition test** — simulate DNS failure to LLM providers
5. **Track MTTR (Mean Time To Recovery)** — instrument circuit breaker state transitions with timestamps
