# openclaw-model-bridge

> Connect any LLM to OpenClaw — production-tested two-layer middleware for Qwen3-235B and beyond.
> 将任意大模型接入 OpenClaw — 基于 Qwen3-235B 生产验证的双层中间件。

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)

## Quick Start
```bash
pip install flask requests
# Edit adapter.py: set API_KEY and REMOTE_ENDPOINT
nohup python3 adapter.py > adapter.log 2>&1 &
nohup python3 tool_proxy.py > tool_proxy.log 2>&1 &
curl http://localhost:5002/health
```

## Architecture

![Architecture Diagram](docs/architecture.svg)
```
WhatsApp <-> OpenClaw Gateway (18789) <-> Tool Proxy (5002) <-> Adapter (5001) <-> Qwen3 API
```

## Files

| File | Description |
|------|-------------|
| tool_proxy.py | Core middleware: tool filtering, schema simplification, SSE conversion |
| adapter.py | API adapter: auth, User-Agent spoofing, param filtering |
| restart.sh | One-command restart script |
| docs/GUIDE.md | Complete bilingual integration guide |

## Key Rules

1. **Tools <= 12** — more tools causes model confusion
2. **Tool calls per task <= 2** — more calls increases timeout risk exponentially  
3. **Request body <= 200KB** — buffer from the 280KB hard limit

## Full Guide

See [docs/GUIDE.md](docs/GUIDE.md) for complete bilingual (CN/EN) walkthrough including 22 hard-won lessons from production.

## License

MIT
