# Enterprise-Grade AI Middleware on a Mac Mini

> A production WhatsApp AI assistant powered by OpenClaw + Qwen3-235B, running on a single Mac Mini with 393 automated tests and a 98/100 security score.

## What It Does

This middleware connects OpenClaw to open-source LLMs (Qwen3-235B for text, Qwen2.5-VL-72B for vision) via a three-layer proxy architecture, adding:

- **Smart routing**: Simple questions → fast model, complex questions → Qwen3-235B (~40% cost saving)
- **Multimodal**: WhatsApp images auto-routed to vision model for understanding
- **Model fallback**: Qwen3 → Gemini automatic degradation (assistant never goes offline)
- **Knowledge base**: Automated ArXiv/HN monitoring, daily digest, semantic search (local embeddings)
- **Security framework**: 98/100 quantified score across 7 dimensions

## Architecture

```
WhatsApp ←→ Gateway (:18789) ←→ Tool Proxy (:5002) ←→ Adapter (:5001) ←→ Remote GPU
             [OpenClaw]          [policy filter]        [auth/routing]      [Qwen3-235B]
                                 [image injection]      [fallback chain]    [Qwen2.5-VL]
```

## Security Highlights

| Feature | Detail |
|---------|--------|
| Automated tests | 393 cases, 13 suites, 100% pass gate |
| Security score | 98/100, 7 dimensions, auto-tracked |
| Audit log | Chain-hashed JSONL, tamper-detectable |
| Atomic writes | All shared state files use `tmp + os.replace()` |
| Cron protection | 3-layer: prevent + detect + recover |
| Deploy safety | Hourly md5 drift detection + 14-item preflight |

## Key Lesson

A crontab wipe incident (all 18 jobs silently lost) led to a complete security overhaul. Three iron rules:

1. Every `exit 0` path must log — silent success and silent failure look the same
2. Shared files must use atomic writes — `tmp + replace`, no exceptions
3. Monitoring cannot depend on the monitored service — always have a local fallback

## Links

- **Source**: [github.com/bisdom-cell/openclaw-model-bridge](https://github.com/bisdom-cell/openclaw-model-bridge)
- **Full write-up**: [English article](https://github.com/bisdom-cell/openclaw-model-bridge/blob/main/docs/zhihu_article_en.md)
