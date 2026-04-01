# openclaw-model-bridge

> Connect any LLM to [OpenClaw](https://github.com/openclaw/openclaw) — production-tested middleware with multimodal support on Mac Mini.
> 将任意大模型接入 OpenClaw — 支持多模态（图片理解）的生产中间件，运行于 Mac Mini。

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)
[![Tests](https://img.shields.io/badge/tests-396%20passed-brightgreen.svg)]()
[![Jobs](https://img.shields.io/badge/cron%20jobs-32%20(28%20active)-blue.svg)]()
[![CI](https://img.shields.io/badge/CI-GitHub%20Actions-green.svg)]()

## Architecture / 系统架构

![Architecture Diagram](docs/architecture.svg)

<details>
<summary>Text version / 文本版本</summary>

```
┌─────────────────────────────────────────────────────────────────┐
│                     用户层 (WhatsApp)                            │
│                 文本 / 图片 / 语音消息                            │
└────────────────────────┬────────────────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────────────────┐
│  ① 核心数据通路（实时对话 + 多模态）                               │
│                                                                  │
│  WhatsApp ←→ Gateway (:18789) ←→ Proxy (:5002) ←→ Adapter (:5001) ←→ Remote GPU       │
│              [launchd]           [策略过滤+监控]    [认证+Fallback]    [Qwen3-235B]      │
│              [媒体存储]          [图片base64注入]   [VL模型路由]       [Qwen2.5-VL-72B]  │
│                                 [自定义工具注入]    [→Gemini降级]                        │
│                                 data_clean(清洗)                                       │
│                                 search_kb(混合检索)                                    │
│                                                                  │
│  search_kb流程：用户问论文 → PA调search_kb → Proxy拦截           │
│    → ①语义搜索(embedding cosine) + ②关键词补充                   │
│    → 支持source过滤(arxiv/hf/hn等) + 时间过滤(recent_hours)      │
│    → 结果注入对话 → followup LLM调用 → 自然语言回答              │
└──────────────────┬──────────────────┬───────────────────────────┘
                   │                  │
┌──────────────────▼──────────────────▼───────────────────────────┐
│  ② 知识库 + 本地 AI（零 API 调用）                                │
│                                                                  │
│  KB Notes + Sources ──→ kb_embed.py ──→ 本地 Embedding (384维)   │
│                          (sentence-transformers, 每4h增量)        │
│                                ↓                                 │
│                         ~/.kb/text_index/ ──→ kb_rag.py (RAG)    │
│                                                                  │
│  媒体文件 ──→ mm_index.py ──→ Gemini Embedding 2 (768维)         │
│                     ↓                                            │
│              ~/.kb/mm_index/ ──→ mm_search.py (语义搜索)          │
└──────────────────────────────────────────────────────────────────┘
                   │
┌──────────────────▼──────────────────────────────────────────────┐
│  ③ 定时任务层（32 个 system cron jobs，28 active）                │
│                                                                  │
│  论文监控矩阵（5源）：                                            │
│    ArXiv(每3h) + HF Papers(10:00) + S2(11:00)                   │
│    + DBLP(12:00) + ACL(09:30) ──→ KB + WhatsApp推送             │
│  每3h   HN热帖抓取 ──→ KB + WhatsApp推送                         │
│  每天×3 货代Watcher ──→ LLM分析 + KB + WhatsApp推送              │
│  每天   OpenClaw Releases ──→ LLM摘要 + KB + WhatsApp推送        │
│  每小时 Issues监控 ──→ KB + WhatsApp推送                         │
│  每天   KB每日摘要 / 晚间整理 / 智能去重                          │
│  每4h   KB 向量索引（本地 embedding）                             │
│  每2h   多媒体索引（Gemini Embedding 2）                          │
│  每天   对话质量日报 / Token用量日报                              │
│  每周   KB深度回顾 / 健康周报 / AI趋势报告                        │
│  每天   Gateway state 备份（外挂 SSD）                            │
│  每30m  WhatsApp 保活                                            │
│  每4h   Job Watchdog（元监控告警）                                │
│  每2m   auto_deploy（Git→运行时自动同步）                         │
└──────────────────────────────────────────────────────────────────┘
                   │
┌──────────────────▼──────────────────────────────────────────────┐
│  ④ DevOps（自动部署 + 17项体检 + pre-commit + GitHub Actions CI） │
│                                                                  │
│  Claude Code → claude/分支 → PR → main → auto_deploy → Mac Mini  │
│       auto_deploy: 文件同步(31个) + 漂移检测 + 按需restart        │
│       preflight: 17项检查（单测+注册表+语法+部署+安全+E2E）       │
│       pre-commit: API key/手机号泄漏+语法检查                     │
│       GitHub Actions CI: 8套单测+注册表+安全扫描+bandit           │
└──────────────────────────────────────────────────────────────────┘
                   │
┌──────────────────▼──────────────────────────────────────────────┐
│  ⑤ 三方共享状态（~/.kb/status.json 实时同步）                     │
│                                                                  │
│  用户(WhatsApp) ←→ PA ←→ status.json ←→ Claude Code ←→ Cron    │
│  反馈+决策          写入    优先级/反馈    读/写       自动更新    │
│                             健康/焦点                             │
│                                                                  │
│  宪法：用户专业深度 + Claude Code设计部署 + OpenClaw数据复利       │
│        三者合一 = 有生命的闭环系统                                 │
└──────────────────────────────────────────────────────────────────┘
```

</details>

| Component | Port | Files | Role |
|-----------|------|-------|------|
| OpenClaw Gateway | 18789 | npm global | WhatsApp integration, media storage, tool execution, session management |
| Tool Proxy | 5002 | `tool_proxy.py` + `proxy_filters.py` | Tool filtering (24→12), **custom tools** (data_clean + search_kb hybrid search), **image base64 injection**, SSE conversion, truncation, token monitoring |
| Adapter | 5001 | `adapter.py` | Multi-provider forwarding, auth, **multimodal routing** (text→Qwen3, image→Qwen2.5-VL), fallback degradation |
| Local Embedding | — | `local_embed.py` | sentence-transformers (384-dim, 50+ languages), zero API calls |
| Remote GPU | — | hkagentx.hkopenlab.com | Qwen3-235B (text, 262K context) + **Qwen2.5-VL-72B** (vision) |

## Quick Start

```bash
# Core services — no third-party dependencies (stdlib only)
export REMOTE_API_KEY="your-key-here"
bash restart.sh
curl http://localhost:5002/health
# → {"ok":true,"proxy":true,"adapter":true}

# Optional: KB RAG semantic search
pip3 install -r requirements-rag.txt
python3 kb_embed.py && python3 kb_rag.py "AI papers"

# Optional: Multimodal memory (images/audio/video)
export GEMINI_API_KEY="your-gemini-key"
pip3 install -r requirements-mm.txt
python3 mm_index.py && python3 mm_search.py "cat photos"
```

## Project Structure

### Core Services

| File | Description |
|------|-------------|
| `tool_proxy.py` | HTTP layer — request/response routing, **custom tool execution** (data_clean + search_kb), **media injection**, followup LLM calls, logging, health cascade |
| `proxy_filters.py` | Policy layer — tool filtering, **custom tool injection** (data_clean + search_kb), **image base64 injection** (`<media:image>` → `image_url`), param fixing, truncation, SSE conversion |
| `adapter.py` | API adapter — multi-provider forwarding, auth, **multimodal routing** (text→Qwen3, image→Qwen2.5-VL), fallback degradation |

### Knowledge Base & Local AI

| File | Description |
|------|-------------|
| `local_embed.py` | **V29.3** Local embedding engine — sentence-transformers (multilingual-MiniLM, 384-dim, 50+ languages), zero API calls |
| `kb_embed.py` | **V29.3** KB text vector indexer — notes+sources → chunking (400 chars, 80 overlap) → local embedding → `~/.kb/text_index/` |
| `kb_rag.py` | **V29.3** RAG semantic search — `--context` (LLM injection), `--json` (scripting), `--top N`, `--source` (filter by origin), `--recent N` (time-based) |
| `mm_index.py` | **V29.1** Multimodal memory indexer — Gemini Embedding 2 for images/audio/video/PDF |
| `mm_search.py` | **V29.1** Multimodal semantic search — text query → cosine similarity → matched media |
| `kb_search.sh` | **V29** KB full-text search — keyword/tag/date/source filtering, `--summary` stats |
| `kb_inject.sh` | **V29** Daily KB digest generator — `~/.kb/daily_digest.md` for LLM context |
| `kb_review.sh` | **V29** Weekly KB deep review — LLM cross-note analysis + WhatsApp push |
| `kb_write.sh` | KB write utility — directory lock + atomic write |
| `kb_dedup.py` | **V29.2** KB deduplication — exact/fuzzy note dedup + source line dedup |
| `kb_trend.py` | **V29.5** Weekly AI trend report — this week vs last week keywords + LLM analysis + prediction backtest |
| `status_update.py` | **V29.5** Three-party shared status — atomic read/write of `~/.kb/status.json` (Claude Code + PA + cron) |
| `data_clean.py` | **V30.3** Data cleaning CLI — 7 operations (dedup/trim/fix_dates/etc), 5 formats (CSV/TSV/JSON/JSONL/Excel), version chain + audit log |
| `SOUL.md` | **V30.4** PA constitutional system prompt — identity (Wei), three-party constitution, behavior directives, live project status |

### Monitoring & Quality

| File | Description |
|------|-------------|
| `conv_quality.py` | Daily conversation quality report — response time, success rate, tool distribution, token usage |
| `token_report.py` | Daily token usage report — consumption, hourly distribution, context pressure, multi-day trends |
| `job_watchdog.sh` | Meta-monitor — checks all job status + log scanning → WhatsApp alerts on timeout/failure |
| `wa_keepalive.sh` | WhatsApp session keepalive — Gateway HTTP probe every 30 min |

### Operations

| File | Description |
|------|-------------|
| `restart.sh` | One-command restart all services (with PATH fix for cron) |
| `auto_deploy.sh` | Auto-deployment — git pull + file sync (31 files) + drift detection + smart restart + post-deploy preflight |
| `preflight_check.sh` | Pre-flight check — **17 automated checks** (tests, registry, syntax, deploy consistency, env vars, connectivity, security scan, data flow, crontab) |
| `health_check.sh` | Weekly health report + JSON output |
| `openclaw_backup.sh` | **V29.1** Daily Gateway state backup to external SSD (7-day retention) |
| `upgrade_openclaw.sh` | Gateway upgrade SOP (must run via SSH, never via WhatsApp) |
| `smoke_test.sh` | End-to-end smoke test (unit tests + registry + doc drift + connectivity) |

### Scheduled Jobs (32 registered, 28 active)

All jobs registered in `jobs_registry.yaml`. Validate: `python3 check_registry.py`

| File | Schedule | Description |
|------|----------|-------------|
| `jobs/arxiv_monitor/run_arxiv.sh` | Every 3h | ArXiv AI paper monitoring + KB + WhatsApp |
| `jobs/hf_papers/run_hf_papers.sh` | Daily 10:00 | **V30.5** HuggingFace Daily Papers + KB + WhatsApp |
| `jobs/semantic_scholar/run_semantic_scholar.sh` | Daily 11:00 | **V30.5** Semantic Scholar papers (citation-ranked) + KB + WhatsApp |
| `jobs/dblp/run_dblp.sh` | Daily 12:00 | **V30.5** DBLP CS papers (multi-keyword, free API) + KB + WhatsApp |
| `jobs/acl_anthology/run_acl_anthology.sh` | Daily 09:30 | **V30.5** ACL Anthology NLP top-venue papers + KB + WhatsApp |
| `run_hn_fixed.sh` | Every 3h:45 | HackerNews hot posts scraper |
| `jobs/freight_watcher/run_freight.sh` | 08/14/20:00 | Freight intelligence — scraping + LLM analysis |
| `jobs/openclaw_official/run.sh` | Daily 08:00 | OpenClaw releases watcher + LLM summary |
| `jobs/openclaw_official/run_discussions.sh` | Hourly:15 | GitHub Issues monitor (REST API + ETag) |
| `kb_inject.sh` | Daily 07:00 | KB daily digest for LLM context |
| `kb_embed.py` | Every 4h:30 | KB text vector indexing (local embedding) |
| `kb_evening.sh` | Daily 22:00 | Evening KB cleanup |
| `kb_dedup.py` | Daily 23:00 | KB deduplication (dry-run) |
| `kb_review.sh` | Fri 21:00 | Weekly KB deep review (LLM analysis) |
| `mm_index_cron.sh` | Every 2h | Multimodal memory indexing (Gemini) |
| `conv_quality.py` | Daily 08:15 | Conversation quality report |
| `token_report.py` | Daily 08:20 | Token usage report |
| `health_check.sh` | Mon 09:00 | Weekly health report |
| `openclaw_backup.sh` | Daily 03:00 | Gateway state backup |
| `auto_deploy.sh` | Every 2 min | Git → runtime auto-sync |
| `job_watchdog.sh` | Every 4h:30 | Job health monitoring |
| `wa_keepalive.sh` | Every 30 min | WhatsApp session probe |
| `kb_trend.py` | Sat 09:00 | Weekly AI trend report (keyword trends + LLM analysis) |
| `jobs/github_trending/run_github_trending.sh` | Daily 14:00 | **V31** GitHub Trending ML/AI repos |
| `jobs/rss_blogs/run_rss_blogs.sh` | 08:00/18:00 | **V31** RSS blog subscriptions (科学空间 etc.) |
| `kb_status_refresh.sh` | Hourly | Status.json health refresh (three-party sync) |
| `preference_learner.py` | Daily 07:30 | User preference auto-learning |
| `cron_canary.sh` | Every 10 min | Cron heartbeat canary |
| `kb_integrity.py` | (on-demand) | KB file integrity checker (SHA256) |

### Configuration & Testing

| File | Description |
|------|-------------|
| `jobs_registry.yaml` | Unified job registry — 32 jobs (28 active, 4 disabled), system cron |
| `check_registry.py` | Registry validator — ID uniqueness, paths, fields |
| `gen_jobs_doc.py` | Auto-generate job docs from registry + drift detection |
| `test_tool_proxy.py` | Unit tests for proxy_filters (67 cases) |
| `test_check_registry.py` | Unit tests for check_registry (18 cases) |
| `test_data_clean.py` | Unit tests for data_clean (80 cases) |
| `test_adapter.py` | Unit tests for adapter (36 cases) |
| `test_kb_business.py` | Unit tests for KB business logic (44 cases) |
| `test_cron_health.py` | Unit tests for cron health (94 cases) |
| `test_status_update.py` | Unit tests for status_update (33 cases) |
| `test_audit_log.py` | Unit tests for audit_log (19 cases) |
| `full_regression.sh` | **V30.1** Full regression runner — 396 tests, all must pass |
| `.githooks/pre-commit` | **V32** Pre-commit hook — API key/phone leak + syntax checks |
| `.github/workflows/ci.yml` | **V32** GitHub Actions CI — 8 test suites + security scan |
| `CLAUDE.md` | Project context for AI-assisted development |

### Documentation

| File | Description |
|------|-------------|
| `docs/GUIDE.md` | Complete bilingual (CN/EN) integration guide with 26 lessons learned |
| `docs/config.md` | Full system configuration + historical changelog |
| `docs/openclaw_architecture.md` | OpenClaw upstream architecture reference (synced to v2026.3.23) |
| `docs/importyeti_sop.md` | ImportYeti manual query SOP for freight research |
| `ROLLBACK.md` | Rollback guide — 30-second recovery to v26 |

## V32 Methodology: Control Plane First

> "The stronger capabilities get, the harder the system is to control — governance must lead, not follow."

**Three-Plane Architecture**:
- **Control Plane** (70%): SLO monitoring, threshold centralization, E2E journey tests, incident snapshots
- **Capability Plane** (85%): Data cleaning, search_kb hybrid retrieval, multimodal understanding
- **Memory Plane** (60%): KB RAG, trend analysis, preference learning, long-term memory (pending model upgrade)

**Current Focus**: P0 SLO system + threshold centralization + E2E into CI + incident snapshots (1-2 weeks)

## Key Rules

1. **Tools <= 12** — more causes model confusion
2. **Tool calls per task <= 2** — timeout risk increases exponentially
3. **Request body <= 200KB** — buffer from the 280KB hard limit
4. **`--thinking` values** — `off, minimal, low, medium, high, adaptive` (never use `none`)
5. **Model ID in openclaw.json** — must include `qwen-local/` prefix
6. **API keys via env vars only** — never hardcode in source files

## Local AI Capabilities (V29.3)

### KB RAG Semantic Search (Zero API Calls)

```bash
# Build index (first time, or --reindex to rebuild)
python3 kb_embed.py                        # 4339 chunks in ~8s on Mac Mini

# Search
python3 kb_rag.py "Qwen3 模型"             # Top-5 results
python3 kb_rag.py --context "AI论文"        # LLM-injectable format
python3 kb_rag.py --json "shipping"         # JSON for scripting
python3 kb_rag.py --top 10 "RAG pipeline"  # Custom top-K
python3 kb_rag.py --source arxiv "LLM"     # Filter by source (arxiv/hf/dblp/acl/hn/notes)
python3 kb_rag.py --recent 24              # What's new in last 24 hours

# Stats
python3 kb_embed.py --stats
```

**Model**: `paraphrase-multilingual-MiniLM-L12-v2` (384-dim, 50+ languages)
**Performance on Mac Mini M-series**: single ~10ms, batch 100 ~500ms, full index 137 files in 8.1s

### Multimodal Memory Search (Gemini API)

```bash
python3 mm_index.py                    # Index media files
python3 mm_search.py "猫的照片"         # Semantic search
python3 mm_search.py --stats           # Index stats
```

## Auto-Deployment

```
Claude Code → claude/branch → PR → main → auto_deploy (2 min) → Mac Mini
                                                ↓
                               git pull → test → file sync (31 files) → smart restart
                                                ↓
                               preflight_check.sh --full (17 checks)
```

The `auto_deploy.sh` script maps 31 repo files to runtime locations and only restarts services when core files change. Hourly drift detection via md5 checksums with WhatsApp alerts.

## Testing

```bash
# Full regression (396 tests — must pass before push)
bash full_regression.sh

# Individual test suites
python3 test_tool_proxy.py              # 67 proxy_filters tests
python3 test_check_registry.py          # 18 registry tests
python3 test_data_clean.py              # 80 data cleaning tests
python3 test_adapter.py                 # 36 adapter tests
python3 test_kb_business.py             # 44 KB business logic tests
python3 test_cron_health.py             # 94 cron health tests
python3 test_status_update.py           # 33 status update tests
python3 test_audit_log.py               # 19 audit log tests

# Registry validation
python3 check_registry.py               # Validate all 32 jobs

# Doc drift detection
python3 gen_jobs_doc.py --check          # Compare registry vs docs

# End-to-end smoke test
bash smoke_test.sh                       # Tests + registry + connectivity

# Full pre-flight check (on Mac Mini)
bash preflight_check.sh --full           # 17 automated checks

# Security score
python3 security_score.py               # 7-dimension score (100 points)

# Local embedding benchmark
python3 local_embed.py --bench           # Performance test
```

## Security

Run before every `git push`:

```bash
grep -r "sk-[A-Za-z0-9]\{15,\}" . --include="*.py" --include="*.sh" --include="*.md" | grep -v ".git"
grep -r "BSA[A-Za-z0-9]\{15,\}" . --include="*.py" --include="*.sh" --include="*.md" | grep -v ".git"
# All output must be empty
```

## Full Guide

See [docs/GUIDE.md](docs/GUIDE.md) for the complete bilingual walkthrough including 26 hard-won production lessons.

## License

MIT
