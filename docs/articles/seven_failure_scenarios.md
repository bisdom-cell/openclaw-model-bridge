# 7 Failure Scenarios Every Agent System Should Survive

> We broke our own system 47 different ways. Here's what we learned.

---

## Why This Matters

Agent systems are evaluated on what they can do: how many tools, how many models, how smart the routing. Nobody asks: **what happens when things go wrong?**

But "things go wrong" is not an edge case in production. It's the default state. LLM providers go down. Models hallucinate invalid tool calls. Conversations grow until they overflow context windows. Cron jobs silently stop. State files get corrupted.

We built an agent runtime ([openclaw-model-bridge](https://github.com/bisdom-cell/openclaw-model-bridge)) that connects 7 LLM providers to a WhatsApp-based AI assistant. After 37 versions and 729 tests, we stopped asking "does it work?" and started asking: **"what breaks it?"**

This article documents 7 systematic failure scenarios, the fault injection results, and the architectural patterns that made survival possible. Every number in this article comes from automated benchmarks that run before every code push.

---

## The Bench: How We Test Failure

We built `reliability_bench.py` — a mock-based fault injection tool that simulates 7 categories of production failure without touching live services. It runs in 2 seconds on any dev machine. No infrastructure needed.

```
$ python3 reliability_bench.py

Scenarios: 7 PASS / 0 FAIL
Checks:    47/47 passed
Time:      ~2.2s
```

Each scenario follows the same structure:
1. **Hypothesis**: What should happen when X fails?
2. **Injection**: Simulate the failure condition
3. **Verification**: Assert the system behaves correctly
4. **Production config**: How this maps to real deployment

---

## Scenario 1: Your LLM Provider Goes Down

**The problem**: You're calling Qwen3-235B. The GPU cluster restarts. Every request returns 502 for the next 5 minutes.

**What most systems do**: Return errors to users. Retry indefinitely. Maybe crash.

**What a control plane does**: Circuit breaker.

```
Normal:     User → Proxy → Adapter → Qwen3    ← response
After 3x 502: User → Proxy → Adapter → Gemini  ← fallback response
After 5min:    User → Proxy → Adapter → Qwen3  ← auto-recovered
```

**Our bench results**:

| Phase | Expected | Actual |
|-------|----------|--------|
| Initial state | Circuit closed | closed |
| After 3 consecutive failures | Circuit open | open |
| Requests during open state | Skip primary, use fallback | is_open=True |
| After reset period (300s) | Half-open, probe allowed | half-open |
| After successful probe | Circuit closed, primary restored | closed |

**Key design decision**: Zero-retry failover. When the circuit opens, the *same HTTP request* gets routed to the fallback provider. No user-visible delay. No retry storm.

**The pattern**:

```python
if circuit_breaker.is_open():
    response = call_fallback_provider(request)
else:
    response = call_primary_provider(request)
    if response.failed:
        circuit_breaker.record_failure()
```

**Lesson**: Failover speed matters more than retry logic. A user waiting 30 seconds for retries to exhaust will leave. A user getting a slightly different model in 0ms won't notice.

---

## Scenario 2: The Backend Hangs Forever

**The problem**: The LLM backend accepts your TCP connection but never responds. Your proxy thread blocks. More requests pile up. Eventually everything is stuck.

**What most systems do**: Use a generous timeout (5 minutes? 10 minutes?), then wonder why the system feels sluggish.

**What a control plane does**: Layered timeouts with strict budgets.

**Our bench results**:

| Metric | Value |
|--------|-------|
| Timeout detected | Yes |
| Actual elapsed | 1,008ms |
| Budget ceiling | 3,000ms |
| Thread blocked after timeout | No |

**Production timeout hierarchy**:

```
Primary request:           300s (LLM can be slow for complex reasoning)
Fallback request:           60s (fallback should be fast or fail)
search_kb LLM followup:     60s (secondary LLM call, strict)
data_clean subprocess:       60s (local processing, should be quick)
Health check probe:           5s (monitoring, must be fast)
```

**Lesson**: Every external call needs its own timeout. One "default timeout" for the whole system means either too generous (hanging threads) or too strict (killing valid long-running requests). Layer them.

---

## Scenario 3: The LLM Hallucinates Tool Calls

**The problem**: You give the LLM a tool called `read` with parameter `path`. The LLM calls it with `file_path`. Or `filepath`. Or `filename`. Or adds random extra parameters like `encoding: "utf-8"`.

This isn't a bug. It's the *nature of LLM tool calling*. Models don't follow schemas perfectly. They generalize from training data, and different training examples used different parameter names.

**What most systems do**: The tool call fails. The user sees an error. The developer adds a special case. Repeat for every tool.

**What a control plane does**: Systematic parameter healing.

**Our bench results — 7 classes of malformed input, all auto-repaired**:

| Fault Class | Input | Auto-Fixed To |
|-------------|-------|---------------|
| Wrong param name (read) | `{file_path: "/tmp/f"}` | `{path: "/tmp/f"}` |
| Wrong param name (exec) | `{cmd: "ls"}` | `{command: "ls"}` |
| Wrong param name (write) | `{text: "hi"}` | `{content: "hi"}` |
| Extra params (web_search) | `{query: "x", limit: 10}` | `{query: "x"}` |
| Invalid browser profile | `{profile: "hacker"}` | `{profile: "openclaw"}` |
| Missing browser profile | `{selector: "#btn"}` | `{..., profile: "openclaw"}` |
| Invalid JSON arguments | `"not json"` | Graceful handling, no crash |

**The architecture**: A proxy layer sits between the LLM and tool execution. It intercepts every tool call and applies three transformations:

1. **Alias resolution**: Map variant parameter names to canonical names
2. **Extra parameter stripping**: Remove anything not in the tool's parameter whitelist
3. **Default injection**: Add required defaults (like browser profile) when missing

```python
# Declarative alias definitions (from tool_ontology.yaml)
read:
  aliases: {path: [file_path, file, filepath, filename]}
exec:
  aliases: {command: [cmd, shell, bash, script]}
```

**Lesson**: Don't trust LLM tool calls to be well-formed. Build a healing layer. Make it declarative (YAML, not if-else chains) so it's easy to extend for new tools.

---

## Scenario 4: The Conversation Overflows

**The problem**: A user has been chatting for 2 hours. The message history is 400KB. You send it all to the LLM. The request times out. Or exceeds the context window. Or costs 10x the normal token budget.

**What most systems do**: Limit conversation length. Or let it fail and tell users to start a new chat.

**What a control plane does**: Intelligent truncation with context awareness.

**Our bench results**:

| Metric | Value |
|--------|-------|
| Input size | 407,465 bytes |
| Output size (normal mode) | 197,067 bytes |
| Messages dropped | 52 (oldest first) |
| System messages preserved | All |
| Most recent message kept | Yes |
| Output size (high context, 88%) | 47,348 bytes (aggressive mode) |

**Dynamic truncation strategy**:

```
Context usage < 70%  → default limit (200KB)
Context usage 70-85% → moderate (100KB)
Context usage > 85%  → aggressive (50KB)
```

**What gets preserved** (in priority order):
1. System messages (always kept — they contain identity and instructions)
2. The most recent user message (what the user just asked)
3. Recent assistant messages (continuity of conversation)
4. Older messages (dropped first)

**Lesson**: Truncation is not just "limit the size." It's a priority system. System prompts are constitutional. Recent context is essential. Old context is expendable. The truncation policy *is* the conversation memory policy.

---

## Scenario 5: Knowledge Base Returns Nothing

**The problem**: The user asks "What papers were published about quantum computing last week?" Your KB search finds nothing. The LLM gets an empty result and either hallucinates an answer or says "I don't know" without context.

**What most systems do**: Return a generic error. Or worse, the LLM fills in the gap with fabricated references.

**What a control plane does**: Structured empty results with scope context.

**Our bench results**:

| Check | Result |
|-------|--------|
| search_kb registered as custom tool | PASS |
| Schema has required `query` field | PASS |
| Schema has `source` filter (arxiv/hf/hn/etc.) | PASS |
| Schema has `recent_hours` filter for time-based queries | PASS |
| Custom tools injected after whitelist filtering | PASS |
| Unknown tools correctly filtered out | PASS |

**The miss-hit response**:

```
Knowledge base has no results for "quantum computing".
KB contains ArXiv/HF/S2/DBLP/ACL papers and HN posts, updated daily.
```

This tells the LLM *what the KB covers*, so it can give an honest answer: "The KB I have access to covers AI/ML papers and tech news, but not quantum computing specifically."

**Lesson**: Empty results need to be *informative* empty results. Tell the LLM (and the user) what was searched, what the KB covers, and why nothing was found. This prevents hallucination better than any prompt engineering.

---

## Scenario 6: Your Cron Jobs Silently Die

**The problem**: You have 28 cron jobs monitoring papers, syncing KB, sending health reports. One of them silently stops. The cron daemon is fine. The script exists. But the job hasn't produced output in 12 hours.

**What most systems do**: Notice days later when someone asks "why haven't we gotten any ArXiv updates?"

**What a control plane does**: Multi-layer monitoring.

**Our bench results**:

| Check | Result |
|-------|--------|
| Fresh heartbeat (0s age) detected as healthy | PASS |
| Stale heartbeat (7200s age) detected as stale | PASS |
| Registry loads (37 job entries) | PASS |
| All registry entries pass validation | PASS |
| 14 jobs have silence timeouts defined | PASS |

**Three-layer cron monitoring**:

```
Layer 1: Heartbeat canary
  → cron_canary.sh runs every 10 min, writes timestamp
  → If timestamp > 30 min old, cron daemon itself is broken

Layer 2: Job watchdog
  → job_watchdog.sh runs every 4 hours
  → Checks each job's status file against its silence_timeout
  → Alert if any job exceeds its expected interval

Layer 3: Registry validation
  → jobs_registry.yaml is the single source of truth
  → check_registry.py validates ID uniqueness, paths, fields
  → Must pass before any new job can be added
```

**Lesson**: Cron monitoring needs to be *hierarchical*. First check if cron itself is alive (heartbeat). Then check if individual jobs are on schedule (watchdog). Then check if the job definitions are valid (registry). Each layer catches a different class of failure.

---

## Scenario 7: State Files Get Corrupted

**The problem**: Your system writes JSON state files (status.json, proxy_stats.json). A process crashes mid-write. The file contains half a JSON object. The next read crashes. Or worse — silently loads partial data.

**What most systems do**: Try/except JSONDecodeError, return empty defaults, lose the state.

**What a control plane does**: Prevention + detection.

**Our bench results**:

| Fault | Detection Method | Result |
|-------|-----------------|--------|
| Invalid JSON | JSONDecodeError raised | PASS |
| Truncated JSON | JSONDecodeError raised | PASS |
| Empty file | JSONDecodeError raised | PASS |
| Missing required keys | Schema validation | PASS |
| Atomic write (tmp + rename) | Content preserved | PASS |
| Tmp file cleanup | No leftover files | PASS |

**Prevention — atomic writes**:

```python
tmp = target + ".tmp"
with open(tmp, "w") as f:
    json.dump(data, f)
os.replace(tmp, target)  # atomic on same filesystem
```

The key insight: `os.replace()` is atomic on POSIX filesystems. Either the old file exists or the new file exists. Never a half-written file.

**Detection — schema validation on read**:

```python
data = json.load(f)
required = {"priorities", "health", "recent_changes"}
missing = required - set(data.keys())
if missing:
    raise ValueError(f"Corrupt state: missing {missing}")
```

**Lesson**: Atomic writes are table stakes. But you also need read-time validation. A file can be valid JSON but missing critical fields (e.g., after a schema migration). Validate structure, not just syntax.

---

## The Meta-Lesson: Failure Is a Feature

After building these 7 scenarios into an automated bench that runs before every push, we learned something unexpected: **designing for failure improved our normal-case architecture**.

- Circuit breakers forced us to abstract provider interfaces, which made adding new providers trivial
- Parameter healing forced us to define canonical schemas, which became the basis for an ontology engine
- Truncation policies forced us to think about information priority, which improved our prompt engineering
- State corruption tests forced us to adopt atomic writes everywhere, eliminating a whole class of race conditions

The bench itself is the artifact. It's 47 assertions that encode our operational knowledge. When a new failure mode is discovered in production, it becomes scenario 8. The bench grows. The system gets harder to break.

---

## How to Build Your Own

If you're building an agent system, here's the minimum viable fault injection bench:

1. **Provider failover**: Mock your LLM client to return errors. Assert fallback triggers.
2. **Timeout enforcement**: Start a TCP server that never responds. Assert your request returns within budget.
3. **Input sanitization**: Feed malformed tool calls through your proxy. Assert they get fixed.
4. **Size limits**: Generate oversized requests. Assert truncation preserves critical context.
5. **Empty results**: Call your retrieval with nonsense queries. Assert informative empty responses.

You don't need Chaos Monkey. You don't need a staging environment. Mock-based tests running on your laptop in 2 seconds will catch 80% of the failures that take down production systems.

The key is to run them *automatically, before every push*. Not quarterly. Not when someone remembers. Every. Single. Push.

```bash
# Our full regression includes fault injection
$ bash full_regression.sh
  ...
  Reliability Bench: 7/7 PASS, 47/47 checks
  ...
  Result: 729 tests passed
```

---

## Evidence Summary

| Metric | Value |
|--------|-------|
| Failure scenarios | 7 |
| Individual checks | 47 |
| Bench execution time | ~2.2 seconds |
| Total test suite | 729 tests across 26 suites |
| LLM providers with failover | 7 |
| Governance invariants | 22 (auto-audited daily) |
| Production uptime pattern | Failover in 0ms, auto-recovery in 300s |
| Security score | 93/100 (7 dimensions) |

All numbers are auto-generated by CI. None are manually maintained.

---

*This article is part of the [openclaw-model-bridge](https://github.com/bisdom-cell/openclaw-model-bridge) project — an open-source agent runtime control plane. The reliability bench, governance engine, and all evidence referenced here are available in the repository.*
