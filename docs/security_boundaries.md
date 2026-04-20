# Security Boundaries

> Version: V36 (2026-04-05) | Security Score: 93/100

## Architecture Security Model

```
Internet
    │
    ▼
WhatsApp Cloud API ──────► OpenClaw Gateway (:18789)
                              │  [launchd managed]
                              │  [no direct internet exposure]
                              ▼
                         Tool Proxy (:5002) ◄──── localhost only
                              │  [request filtering]
                              │  [tool whitelist]
                              │  [size truncation]
                              ▼
                         Adapter (:5001) ◄──────── localhost only
                              │  [auth injection]
                              │  [circuit breaker]
                              ▼
                         LLM Providers (HTTPS)
                         [7 providers, all TLS]
```

**All inter-service communication is localhost-only.** No port is exposed to the network.

## 1. Authentication & Authorization

### API Key Management

| Principle | Implementation |
|-----------|---------------|
| No hardcoded secrets | All API keys via `os.environ.get()` |
| Per-provider isolation | Each provider has its own env var (`REMOTE_API_KEY`, `OPENAI_API_KEY`, etc.) |
| Fallback sentinel | Missing key defaults to `"sk-REPLACE-ME"` (fails auth, never works) |
| Automated scanning | `full_regression.sh` scans for `sk-*` and `BSA*` patterns before push |

### Auth Styles

| Provider | Auth Header | Format |
|----------|-------------|--------|
| Qwen, OpenAI, Gemini, Kimi, MiniMax, GLM | `Authorization` | `Bearer <key>` |
| Claude (Anthropic) | `x-api-key` | Raw key + `anthropic-version` header |

### What Is NOT Authenticated

- **Inter-service calls** (Proxy → Adapter → Gateway): localhost-only, no auth required
- **Health endpoints** (`/health` on all three ports): public by design, returns status only
- **Stats endpoint** (`/stats` on Proxy): returns aggregate metrics, no PII

**Rationale**: These services run on a single machine behind NAT. Adding mTLS between localhost services would add complexity without meaningful security gain.

## 2. Network Boundaries

### Binding

| Service | Bind Address | Port | Configurable |
|---------|-------------|------|-------------|
| Adapter | `127.0.0.1` | 5001 | `BIND_ADDR` env |
| Tool Proxy | `127.0.0.1` | 5002 | `BIND_ADDR` env |
| Gateway | `127.0.0.1` | 18789 | OpenClaw config |

All services bind to `127.0.0.1` by default. **Never set `BIND_ADDR=0.0.0.0` in production.**

### Outbound Connections

| Destination | Protocol | Purpose |
|-------------|----------|---------|
| LLM Providers (7) | HTTPS | Chat completions |
| WhatsApp Cloud API | HTTPS | Message send/receive (via Gateway) |
| GitHub API | HTTPS | Release monitoring, issues |
| ArXiv/HF/S2/DBLP/ACL APIs | HTTPS | Paper monitoring |
| Discord Webhook | HTTPS | Alert notifications |

All outbound connections use TLS. No plaintext HTTP to external services.

### URL Scheme Validation

Both `adapter.py` and `tool_proxy.py` implement `_safe_urlopen()` which rejects non-http(s) URL schemes, preventing SSRF via `file://`, `ftp://`, etc.

## 3. Input Validation & Filtering

### Tool Whitelist (Proxy Layer)

The proxy enforces a strict tool whitelist. LLM-requested tools not on the list are silently dropped.

```
Allowed (14): web_search, web_fetch, read, write, edit, exec,
              memory_search, memory_get, sessions_spawn, sessions_send,
              sessions_history, agents_list, cron, message, tts, image
Prefix-match: browser_*
Custom (2):   data_clean, search_kb (proxy-intercepted, never reach Gateway)
```

**Max tools per request**: 12 (config.yaml). Excess causes model hallucination.

### Request Size Limits

| Limit | Value | Enforcement |
|-------|-------|-------------|
| Max request body | 200KB | `truncate_messages()` drops old messages |
| Hard limit | 280KB | Buffer for system prompts |
| Tool result truncation | 3,000 chars | Per-tool result |
| Error message truncation | 200 chars | Prevent verbose leak |
| Image size | 10MB | `inject_media_into_messages()` |
| Media freshness | 5 minutes | `MEDIA_MAX_AGE_SECONDS` — stale images not injected |

### Parameter Sanitization

`fix_tool_args()` in `proxy_filters.py`:
- Strips unknown parameters from tool calls
- Maps common aliases (`file_path` → `path`, `cmd` → `command`)
- Forces browser profile to `"openclaw"` (prevents profile injection)
- Handles malformed JSON arguments without crashing

### No-Tools Mode

Messages containing `[NO_TOOLS]` marker trigger `should_strip_tools()`, which removes all tools from the request. Used for pure reasoning tasks (e.g., customer profiling) where tool calls are unwanted.

## 4. Data Protection

### Sensitive Files (Never in Git)

`.gitignore` excludes:
- `openclaw.json` (contains phone numbers, session config)
- `jobs.json` (cron payloads)
- `*.plist` (launchd configs with paths)
- `proxy_stats.json` (runtime metrics)
- `~/.kb/` (knowledge base with user content)

### Phone Number Masking

All documentation and code use `+85200000000` as placeholder. Real phone numbers exist only in runtime config files excluded from git.

### Audit Trail

`audit_log.py` provides append-only JSONL logging with SHA256 chain hashing:
- Every `status.json` write is logged
- Chain integrity is verifiable (`--verify`)
- Tampering/deletion is detectable (hash chain breaks)

### Atomic Writes

All state files use atomic write pattern (write to `.tmp`, then `os.replace()`):
- `status.json`
- `daily_digest.md`
- `proxy_stats.json`
- KB index files

This prevents corruption from crashes or concurrent writes.

## 5. LLM-Specific Security

### Prompt Injection Mitigation

| Layer | Defense |
|-------|---------|
| Tool whitelist | LLM cannot call arbitrary tools |
| Parameter stripping | Extra params in tool calls are removed |
| Size truncation | Oversized prompts are trimmed, preventing context stuffing |
| No-tools mode | Pure reasoning tasks have zero tool access |
| SOUL.md hierarchy | System prompt layering: SOUL.md (constitutional) > CLAUDE.md (operational) |

### Tool Call Limits

- **Max tool calls per task**: 2 (config.yaml)
- **Timeout per tool**: 60s for search_kb, 60s for data_clean
- **Circuit breaker**: 5 consecutive failures → skip primary, direct fallback

### What We Do NOT Defend Against

- **LLM-generated content quality**: The system forwards LLM responses as-is. Content moderation is the LLM provider's responsibility.
- **Authorized user actions**: A user with WhatsApp access can instruct the PA to execute any whitelisted tool. There is no per-tool authorization beyond the whitelist.
- **Provider-side data handling**: Queries sent to LLM providers are subject to their data policies.

## 6. Operational Security

### Process Management

| Process | Manager | Restart Policy |
|---------|---------|---------------|
| Gateway | launchd | KeepAlive (auto-restart) |
| Proxy | launchd | KeepAlive |
| Adapter | launchd | KeepAlive |

Single owner (launchd) prevents conflicting process managers.

### Deployment Pipeline

```
Claude Code → claude/* branch → GitHub PR → main → auto_deploy.sh → Mac Mini
```

- `auto_deploy.sh` polls every 2 minutes
- Runs unit tests on proxy_filters changes before deploying
- MD5 drift detection compares deployed vs repo files hourly
- `preflight_check.sh --full` runs 16+ checks after each deploy

### Monitoring & Alerting

| Monitor | Interval | Alert Channel |
|---------|----------|---------------|
| `wa_keepalive.sh` | 30 min | Log (WhatsApp session health) |
| `job_watchdog.sh` | 4 hours | WhatsApp + Discord |
| `cron_canary.sh` | 10 min | Heartbeat file |
| `proxy_stats` | Real-time | In-process (threshold alerts) |
| Health endpoints | On-demand | HTTP 200/502 |

### Cron Safety

After a 2026-03-25 incident where `echo | crontab -` wiped all cron jobs:
- `crontab_safe.sh`: mandatory backup before any crontab edit
- Entry count validation: rejects writes that would reduce entries below threshold
- Automatic rollback on validation failure

## 7. Security Scoring

`security_score.py` evaluates 7 dimensions (100 points total):

| Dimension | Max Score | Current |
|-----------|-----------|---------|
| Key Management | 15 | 15 |
| Test Coverage | 15 | 15 |
| Data Integrity | 15 | 13 |
| Deployment Safety | 15 | 15 |
| Transport Security | 15 | 15 |
| Audit Trail | 15 | 10 |
| Availability | 10 | 10 |
| **Total** | **100** | **93** |

Run: `python3 security_score.py`

## 8. Known Limitations & Accepted Risks

| Risk | Severity | Mitigation | Status |
|------|----------|-----------|--------|
| No TLS between localhost services | Low | All services bind 127.0.0.1, single-machine deployment | Accepted |
| No per-user auth on WhatsApp | Medium | Single-user system, phone number is the identity | Accepted |
| LLM can call `exec` tool | Medium | Whitelisted by design — needed for cron/ops tasks, sandboxed by OpenClaw | Accepted |
| `proxy_stats.json` not encrypted | Low | Contains only aggregate metrics, no PII | Accepted |
| No rate limiting on proxy | Low | Single-user system, localhost-only | Accepted |
| KB files stored plaintext | Low | Mac Mini disk encryption (FileVault) is the boundary | Accepted |

## Checklist for New Contributors

Before deploying changes:

1. `bash full_regression.sh` — all tests pass
2. `grep -r "sk-[A-Za-z0-9]{15,}" --include="*.py" --include="*.sh"` — no leaked keys
3. `python3 security_score.py` — score ≥ 90
4. API keys via env vars only — never in code or config files committed to git
5. New tools must be added to `ALLOWED_TOOLS` whitelist explicitly
6. New outbound connections must use HTTPS
7. Bind addresses must default to `127.0.0.1`
