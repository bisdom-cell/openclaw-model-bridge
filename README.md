# openclaw-model-bridge

> Connect any LLM to [OpenClaw](https://github.com/nicepkg/openclaw) — production-tested two-layer middleware for Qwen3-235B and beyond.
> 将任意大模型接入 OpenClaw — 基于 Qwen3-235B 生产验证的双层中间件。

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)
[![Tests](https://img.shields.io/badge/tests-43%20passed-brightgreen.svg)]()

## Architecture

![Architecture Diagram](docs/architecture.svg)

```
WhatsApp <-> OpenClaw Gateway (:18789) <-> Tool Proxy (:5002) <-> Adapter (:5001) <-> Remote GPU API
                                              │
                                     ┌────────┴────────┐
                                     │  HTTP Layer      │  tool_proxy.py
                                     │  (routing, log)  │
                                     ├─────────────────┤
                                     │  Policy Layer    │  proxy_filters.py
                                     │  (filter, trunc, │
                                     │   SSE, fix_args) │
                                     └─────────────────┘
```

| Component | Port | Files | Role |
|-----------|------|-------|------|
| OpenClaw Gateway | 18789 | npm global install | WhatsApp integration, tool execution |
| Tool Proxy — HTTP Layer | 5002 | `tool_proxy.py` | Request/response routing, logging, error handling |
| Tool Proxy — Policy Layer | ↑ | `proxy_filters.py` | Tool filtering (24→12), message truncation, SSE conversion, param alias mapping (pure functions, no network) |
| Adapter | 5001 | `adapter.py` | API forwarding, auth (`$REMOTE_API_KEY`), param filtering |
| Remote GPU | — | hkagentx.hkopenlab.com | Qwen3-235B inference |

## Quick Start

```bash
pip install flask requests

# Set your remote API key
export REMOTE_API_KEY="your-key-here"

# One-command start
bash restart.sh

# Verify
curl http://localhost:5002/health
```

## Project Structure

### Core Services

| File | Description |
|------|-------------|
| `tool_proxy.py` | HTTP layer — request/response routing, logging |
| `proxy_filters.py` | Policy layer — tool filtering, param fixing, truncation, SSE conversion (pure functions, no network) |
| `adapter.py` | API adapter — auth via `$REMOTE_API_KEY`, User-Agent spoofing, param filtering |

### Operations

| File | Description |
|------|-------------|
| `restart.sh` | One-command restart all services (kills ports 18789/5001/5002, then restarts) |
| `auto_deploy.sh` | Auto-deployment — git pull + file sync + smart restart + drift detection (md5 compare + WhatsApp alert) |
| `upgrade_openclaw.sh` | Gateway upgrade SOP (must run via SSH, never via WhatsApp) |
| `health_check.sh` | Weekly health report + JSON output for automation |

### Scheduled Jobs

| File | Schedule | Description |
|------|----------|-------------|
| `jobs/arxiv_monitor/run_arxiv.sh` | Daily 09:00 | ArXiv AI paper monitoring + KB write + rsync (replaces kb_save_arxiv.sh) |
| `kb_evening.sh` | Daily 22:00 | Evening KB summary + WhatsApp push |
| `kb_review.sh` | Fri 21:00 | Cross-note KB review |
| `kb_write.sh` | — | KB write utility (directory lock + atomic write) |
| `run_hn_fixed.sh` | Every 3h:45 | HackerNews AI/Tech hot posts scraper |
| `run_discussions.sh` | Hourly:15 | Community discussions scraper |
| `jobs/freight_watcher/run_freight.sh` | 08/14/20:00 | Freight watcher — 3-layer intelligence funnel |
| `jobs/openclaw_official/run.sh` | Hourly:30 | OpenClaw releases watcher |
| `jobs/openclaw_official/run_blog.sh` | Hourly:00 | Blog monitor (LLM-generated Chinese titles) |
| `jobs/openclaw_official/run_discussions.sh` | Hourly:15 | GitHub Discussions monitor |
| ~~`kb_save_arxiv.sh`~~ | ~~Daily 08:30~~ | ~~Deprecated (V28: merged into arxiv_monitor)~~ |

### Configuration & Testing

| File | Description |
|------|-------------|
| `jobs_registry.yaml` | Unified job registry — all system + openclaw cron jobs |
| `check_registry.py` | Registry validator — checks ID uniqueness, file paths, field completeness |
| `test_tool_proxy.py` | Unit tests for proxy_filters (43 test cases) |
| `CLAUDE.md` | Project context for AI-assisted development |

### Documentation

| File | Description |
|------|-------------|
| `docs/GUIDE.md` | Complete bilingual (CN/EN) integration guide |
| `docs/config.md` | Full system configuration + historical changelog |
| `docs/importyeti_sop.md` | ImportYeti manual query SOP for freight research |
| `docs/architecture.svg` | Architecture diagram (dark theme SVG) |
| `ROLLBACK.md` | Rollback guide — 30-second recovery to v26 |

## Key Rules

1. **Tools <= 12** — more tools causes model confusion
2. **Tool calls per task <= 2** — timeout risk increases exponentially
3. **Request body <= 200KB** — buffer from the 280KB hard limit
4. **`--thinking` values** — `off, minimal, low, medium, high, adaptive` (never use `none`)

## Dual Cron System

All scheduled jobs are registered in `jobs_registry.yaml` with two schedulers:

| Scheduler | Managed by | Goes through LLM? | Use for |
|-----------|-----------|-------------------|---------|
| `system` | macOS `crontab -e` | No | Deterministic scripts (cleanup, backup, scraping) |
| `openclaw` | `openclaw cron add` | Yes | Tasks requiring LLM understanding/generation |

Validate the registry: `python3 check_registry.py`

## Auto-Deployment

Changes flow from development to production automatically:

```
Vibe coding → git push → main branch → Mac Mini auto_deploy (every 2 min)
                                              ↓
                                    git pull → test → file sync → smart restart
```

The `auto_deploy.sh` script maps 16 repo files to their runtime locations and only restarts services when core files (`proxy_filters.py`, `tool_proxy.py`, `adapter.py`) change. It also performs hourly drift detection via md5 checksums, sending WhatsApp alerts if deployed files diverge from the repo.

## Testing

```bash
# Run all 43 unit tests
python3 -m pytest test_tool_proxy.py -v

# Or without pytest
python3 test_tool_proxy.py

# Validate job registry
python3 check_registry.py
```

## Full Guide

See [docs/GUIDE.md](docs/GUIDE.md) for the complete bilingual walkthrough including production lessons learned.

## License

MIT
