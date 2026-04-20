# Enterprise-Grade AI Middleware on a Mac Mini

> A production WhatsApp + Discord AI assistant powered by OpenClaw + Qwen3-235B, running on a single Mac Mini with **1093 automated tests**, **42 governance invariants / 14 meta-rules** and a **93/100 security score** (V37.8.13, 2026-04-16).

## What It Does

This middleware connects OpenClaw to open-source LLMs (Qwen3-235B for text, Qwen2.5-VL-72B for vision) via a three-layer proxy architecture, adding:

- **Smart routing**: Simple questions → fast model, complex questions → Qwen3-235B (~40% cost saving)
- **Multimodal**: WhatsApp images auto-routed to vision model for understanding
- **Model fallback**: Qwen3 → Gemini automatic degradation (assistant never goes offline)
- **Knowledge base**: Automated ArXiv/HN monitoring, daily digest, semantic search (local embeddings)
- **Security framework**: 93/100 quantified score across 7 dimensions (V30.2 baseline 98/100, V37.8 conservative recalibration after broader scope coverage)

## Architecture

```
WhatsApp ←→ Gateway (:18789) ←→ Tool Proxy (:5002) ←→ Adapter (:5001) ←→ Remote GPU
             [OpenClaw]          [policy filter]        [auth/routing]      [Qwen3-235B]
                                 [image injection]      [fallback chain]    [Qwen2.5-VL]
```

## Security Highlights

| Feature | Detail |
|---------|--------|
| Automated tests | 1093 cases, 36 suites, 100% pass gate |
| Security score | 93/100, 7 dimensions, auto-tracked |
| Governance | 42 invariants / 197 checks / 14 meta-rules (ontology-native) |
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
