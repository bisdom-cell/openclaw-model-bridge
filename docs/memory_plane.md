# Memory Plane v1 Architecture

> Version: V36 (2026-04-05) | Status: Production

## Overview

The Memory Plane unifies 5 independent memory components into a single queryable interface.
Before v1, these were scattered scripts with no shared API. Now `memory_plane.py` provides
one entry point for all memory operations.

```
                        memory_plane.py
                     (unified interface)
                            │
            ┌───────────────┼───────────────┐
            │               │               │
     ┌──────▼──────┐ ┌─────▼─────┐ ┌───────▼───────┐
     │  KB Semantic │ │Multimodal │ │  Preferences  │
     │    Search    │ │  Memory   │ │  + Status     │
     └──────┬──────┘ └─────┬─────┘ └───────┬───────┘
            │               │               │
     ┌──────▼──────┐ ┌─────▼─────┐ ┌───────▼───────┐
     │  kb_rag.py  │ │mm_search  │ │preference_    │
     │  kb_embed   │ │mm_index   │ │learner.py     │
     │  local_embed│ │           │ │status_update  │
     └─────────────┘ └───────────┘ └───────────────┘
     Local embedding   Gemini API    Pure JSON ops
     (384-dim, free)   (768-dim)     (no ML needed)
```

## Memory Layers

| Layer | Components | Embedding | Storage | Availability |
|-------|-----------|-----------|---------|-------------|
| **KB Semantic** | kb_rag + kb_embed + local_embed | Local (384-dim, free) | `~/.kb/text_index/` | Requires numpy + sentence-transformers |
| **Multimodal** | mm_search + mm_index | Gemini API (768-dim) | `~/.kb/mm_index/` | Requires GEMINI_API_KEY |
| **Preferences** | preference_learner + status_update | None | `~/.kb/status.json` | Always available |
| **Status** | status_update + kb_status_refresh | None | `~/.kb/status.json` | Always available |

**Note**: Short-term conversation memory is handled by OpenClaw sessions (external to this codebase).

## API

### Unified Query

```python
from memory_plane import query, get_context, stats

# Search across all layers
results = query("Qwen3 模型", top_k=5)
# Returns: [MemoryResult(layer="kb", score=0.87, text="...", source="arxiv"), ...]

# LLM-injectable context
context = get_context("AI论文", max_chars=4000)
# Returns: formatted string with header/footer for prompt injection

# Layer filter
results = query("test", layers=["kb", "status"])

# KB-specific filters
results = query("RAG", source="arxiv", recent_hours=24)
```

### Layer Management

```python
from memory_plane import check_layers, stats

# Check availability
for layer in check_layers():
    print(f"{layer.name}: {'OK' if layer.available else layer.reason}")

# Unified statistics
s = stats()
# {"kb": {"available": true, "total_chunks": 13925, ...}, ...}
```

### CLI

```bash
# Search
python3 memory_plane.py query "Qwen3"
python3 memory_plane.py query --context "AI论文"    # LLM format
python3 memory_plane.py query --json "RAG"          # JSON output
python3 memory_plane.py query --layers kb "向量"    # KB only

# Inspect
python3 memory_plane.py layers                       # Layer availability
python3 memory_plane.py stats                        # All statistics
python3 memory_plane.py stats --json                 # JSON format
python3 memory_plane.py status                       # Operational status
python3 memory_plane.py preferences                  # User preferences
```

## Design Principles

1. **Zero new dependencies** — Orchestrates existing components only
2. **Graceful degradation** — Any layer can be unavailable without affecting others
3. **LLM-friendly output** — `get_context()` produces ready-to-inject prompt text
4. **Score-ranked fusion** — Results from all layers sorted by relevance score
5. **Layer isolation** — Each layer handles its own errors internally

## Data Flow

```
User Query
    │
    ▼
query("Qwen3", layers=["kb", "status"])
    │
    ├─► KB Layer: kb_rag.search() → local_embed → cosine similarity → scored results
    ├─► Status Layer: load_status() → filter active priorities/incidents → score=1.0
    │
    ▼
Merge + Sort by score (descending)
    │
    ▼
[MemoryResult(kb, 0.87, "Qwen3-235B paper..."),
 MemoryResult(status, 1.0, "[active] Provider Compatibility Layer")]
```

## Testing

```bash
python3 -m unittest test_memory_plane -v     # 45 unit tests
python3 memory_plane.py layers               # Smoke test
```

## Relation to V2/V3 Roadmap

- **V2**: This v1 provides the unified interface. Next: conflict resolution, confidence scoring, cross-layer deduplication.
- **V3**: Plugin interface — custom memory layers can register via `LAYERS` dict pattern.
