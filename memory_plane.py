#!/usr/bin/env python3
"""
memory_plane.py — Memory Plane v1 统一接口（V36: V2-P0）

将 5 个散落的记忆组件统一为一个 Memory Plane：
  1. KB 语义搜索   — kb_rag.py + kb_embed.py + local_embed.py
  2. 多媒体记忆     — mm_search.py + mm_index.py
  3. 用户偏好       — preference_learner.py
  4. 运维状态       — status_update.py
  5. 短期对话       — OpenClaw sessions（外部，此处仅声明）

设计原则：
  - 统一入口：一个 query() 搜索所有记忆层
  - 分层可选：每层可独立启用/禁用
  - LLM 友好：get_context() 直接生成可注入 LLM 的上下文
  - 零新依赖：仅编排已有组件，不引入新库
  - 优雅降级：任一层不可用时跳过，不影响其他层

用法：
  python3 memory_plane.py query "Qwen3 模型"          # 统一搜索
  python3 memory_plane.py query --context "AI论文"     # LLM 可注入格式
  python3 memory_plane.py query --json "RAG"           # JSON 输出
  python3 memory_plane.py query --layers kb "向量搜索" # 仅搜索 KB 层
  python3 memory_plane.py stats                        # 各层统计
  python3 memory_plane.py status                       # 运维状态摘要
  python3 memory_plane.py preferences                  # 用户偏好
  python3 memory_plane.py layers                       # 层可用性检查
"""
import json
import os
import sys
import time
from dataclasses import dataclass, field, asdict
from typing import List, Optional, Dict, Any


# ---------------------------------------------------------------------------
# Memory Layer abstractions
# ---------------------------------------------------------------------------
@dataclass
class MemoryResult:
    """单条记忆检索结果。"""
    layer: str           # "kb" | "multimodal" | "preferences" | "status"
    score: float = 0.0   # 相关度 (0-1)，非语义搜索时为 1.0
    text: str = ""       # 结果文本
    source: str = ""     # 来源文件/类型
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class LayerStatus:
    """单层可用性状态。"""
    name: str
    available: bool = False
    reason: str = ""
    stats: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Layer: KB Semantic Search
# ---------------------------------------------------------------------------
def _kb_available():
    """Check if KB RAG layer is available."""
    try:
        from kb_rag import search as _search, show_stats
        return True, ""
    except ImportError as e:
        return False, str(e)
    except Exception as e:
        return False, str(e)


def _kb_search(query, top_k=5, source=None, recent_hours=None):
    """Search KB via kb_rag.py."""
    try:
        from kb_rag import search
        results = search(query, top_k=top_k, output_mode="json",
                        source=source, recent_hours=recent_hours)
        return [
            MemoryResult(
                layer="kb",
                score=r.get("score", 0.0),
                text=r.get("text", ""),
                source=r.get("source_type", r.get("file", "")),
                metadata={
                    "file": r.get("file", ""),
                    "filename": r.get("filename", ""),
                    "chunk_idx": r.get("chunk_idx", 0),
                }
            )
            for r in results
        ]
    except Exception as e:
        return []


def _kb_stats():
    """Get KB index statistics."""
    try:
        from kb_rag import load_meta
        meta = load_meta()
        chunks = meta.get("chunks", [])
        files = set()
        sources = {}
        for c in chunks:
            files.add(c.get("file", ""))
            st = c.get("source_type", "unknown")
            sources[st] = sources.get(st, 0) + 1
        return {
            "total_chunks": len(chunks),
            "total_files": len(files),
            "by_source_type": sources,
            "index_dir": os.path.expanduser("~/.kb/text_index/"),
        }
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Layer: Multimodal Memory
# ---------------------------------------------------------------------------
def _mm_available():
    """Check if multimodal memory layer is available."""
    try:
        meta_path = os.path.expanduser("~/.kb/mm_index/meta.json")
        if os.path.exists(meta_path):
            return True, ""
        return False, "mm_index/meta.json not found"
    except Exception as e:
        return False, str(e)


def _mm_search(query, top_k=5):
    """Search multimodal memory."""
    try:
        from mm_search import search, load_meta, load_vectors, embed_text, cosine_similarity
        import numpy as np
        from google import genai

        api_key = os.environ.get("GEMINI_API_KEY", "")
        if not api_key:
            return []

        client = genai.Client(api_key=api_key)
        meta = load_meta()
        entries = meta.get("entries", [])
        if not entries:
            return []

        vectors = load_vectors(len(entries))
        query_vec = np.array(embed_text(client, query), dtype=np.float32)
        scores = cosine_similarity(query_vec, vectors)

        ranked = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)[:top_k]
        results = []
        for idx, score in ranked:
            if score < 0.2:
                continue
            entry = entries[idx]
            results.append(MemoryResult(
                layer="multimodal",
                score=float(score),
                text=f"[{entry.get('mime', '?')}] {entry.get('filename', '?')}",
                source="multimodal",
                metadata={
                    "path": entry.get("path", ""),
                    "mime": entry.get("mime", ""),
                    "size": entry.get("size", 0),
                    "filename": entry.get("filename", ""),
                }
            ))
        return results
    except Exception:
        return []


def _mm_stats():
    """Get multimodal index statistics."""
    try:
        meta_path = os.path.expanduser("~/.kb/mm_index/meta.json")
        if not os.path.exists(meta_path):
            return {}
        with open(meta_path) as f:
            meta = json.load(f)
        entries = meta.get("entries", [])
        mimes = {}
        total_size = 0
        for e in entries:
            mime = e.get("mime", "unknown")
            mimes[mime] = mimes.get(mime, 0) + 1
            total_size += e.get("size", 0)
        return {
            "total_entries": len(entries),
            "by_mime": mimes,
            "total_size_mb": round(total_size / 1024 / 1024, 1),
            "index_dir": os.path.expanduser("~/.kb/mm_index/"),
        }
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Layer: User Preferences
# ---------------------------------------------------------------------------
def _preferences_available():
    try:
        from status_update import load_status
        return True, ""
    except ImportError as e:
        return False, str(e)


def _get_preferences():
    """Get current user preferences."""
    try:
        from status_update import load_status
        data = load_status()
        prefs = data.get("preferences", [])
        return [
            MemoryResult(
                layer="preferences",
                score=1.0,
                text=p,
                source="status.json",
            )
            for p in prefs
        ]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Layer: Operational Status
# ---------------------------------------------------------------------------
def _status_available():
    try:
        from status_update import load_status
        return True, ""
    except ImportError as e:
        return False, str(e)


def _get_status():
    """Get current operational status."""
    try:
        from status_update import load_status
        data = load_status()
        results = []

        # Health summary
        health = data.get("health", {})
        if health:
            results.append(MemoryResult(
                layer="status",
                score=1.0,
                text=f"Services: {health.get('services', '?')}, "
                     f"Model: {health.get('model_id', '?')}, "
                     f"KB: {health.get('kb_stats', '?')}, "
                     f"Security: {health.get('security_score', '?')}",
                source="health",
                metadata=health,
            ))

        # Active priorities
        for p in data.get("priorities", []):
            if p.get("status") in ("active", "in_progress"):
                results.append(MemoryResult(
                    layer="status",
                    score=1.0,
                    text=f"[{p.get('status')}] {p.get('task', '?')}: {p.get('note', '')}",
                    source="priorities",
                ))

        # Recent incidents
        for inc in data.get("incidents", [])[:3]:
            if inc.get("status") != "resolved":
                results.append(MemoryResult(
                    layer="status",
                    score=0.8,
                    text=f"[{inc.get('status', '?')}] {inc.get('what', '?')}",
                    source="incidents",
                ))

        return results
    except Exception:
        return []


def _status_stats():
    """Get operational status statistics."""
    try:
        from status_update import load_status
        data = load_status()
        priorities = data.get("priorities", [])
        return {
            "total_priorities": len(priorities),
            "active": sum(1 for p in priorities if p.get("status") in ("active", "in_progress")),
            "done": sum(1 for p in priorities if p.get("status") == "done"),
            "incidents_total": len(data.get("incidents", [])),
            "incidents_open": sum(1 for i in data.get("incidents", []) if i.get("status") != "resolved"),
            "preferences_count": len(data.get("preferences", [])),
            "last_updated": data.get("updated", "?"),
        }
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Unified interface
# ---------------------------------------------------------------------------
LAYERS = {
    "kb": {
        "name": "KB Semantic Search",
        "description": "Text knowledge base with local embeddings (notes, papers, sources)",
        "available_fn": _kb_available,
        "search_fn": _kb_search,
        "stats_fn": _kb_stats,
    },
    "multimodal": {
        "name": "Multimodal Memory",
        "description": "Image/audio/video semantic search via Gemini embeddings",
        "available_fn": _mm_available,
        "search_fn": _mm_search,
        "stats_fn": _mm_stats,
    },
    "preferences": {
        "name": "User Preferences",
        "description": "Auto-learned user preferences from interaction patterns",
        "available_fn": _preferences_available,
        "search_fn": lambda q, **kw: _get_preferences(),
        "stats_fn": lambda: {"count": len(_get_preferences())},
    },
    "status": {
        "name": "Operational Status",
        "description": "System health, priorities, incidents, and project state",
        "available_fn": _status_available,
        "search_fn": lambda q, **kw: _get_status(),
        "stats_fn": _status_stats,
    },
}


def check_layers():
    """Check availability of all memory layers."""
    results = []
    for key, layer in LAYERS.items():
        available, reason = layer["available_fn"]()
        stats = layer["stats_fn"]() if available else {}
        results.append(LayerStatus(
            name=key,
            available=available,
            reason=reason,
            stats=stats,
        ))
    return results


def query(text, layers=None, top_k=5, source=None, recent_hours=None):
    """Unified search across memory layers.

    Args:
        text: Search query
        layers: List of layer names to search (None = all available)
        top_k: Max results per layer
        source: KB source filter (only for KB layer)
        recent_hours: Time-based filter (only for KB layer)

    Returns:
        List of MemoryResult sorted by score (descending)
    """
    target_layers = layers or list(LAYERS.keys())
    all_results = []

    for layer_key in target_layers:
        layer = LAYERS.get(layer_key)
        if not layer:
            continue

        available, _ = layer["available_fn"]()
        if not available:
            continue

        try:
            if layer_key == "kb":
                results = layer["search_fn"](text, top_k=top_k,
                                             source=source,
                                             recent_hours=recent_hours)
            elif layer_key == "multimodal":
                results = layer["search_fn"](text, top_k=top_k)
            else:
                results = layer["search_fn"](text)
            all_results.extend(results)
        except Exception:
            continue

    # Sort by score descending
    all_results.sort(key=lambda r: r.score, reverse=True)
    return all_results


def get_context(text, layers=None, top_k=5, max_chars=4000):
    """Generate LLM-injectable context from memory search.

    Returns a formatted string suitable for system prompt injection.
    """
    results = query(text, layers=layers, top_k=top_k)
    if not results:
        return ""

    parts = []
    total_chars = 0
    for r in results:
        entry = f"[{r.layer}:{r.source}] (score={r.score:.2f}) {r.text}"
        if total_chars + len(entry) > max_chars:
            break
        parts.append(entry)
        total_chars += len(entry)

    if not parts:
        return ""

    header = f"--- Memory Context for: {text} ---"
    return header + "\n" + "\n".join(parts) + "\n--- End Memory Context ---"


def stats():
    """Get unified statistics across all layers."""
    result = {}
    for key, layer in LAYERS.items():
        available, reason = layer["available_fn"]()
        if available:
            result[key] = {"available": True, **layer["stats_fn"]()}
        else:
            result[key] = {"available": False, "reason": reason}
    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _cli():
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        return

    cmd = args[0]

    if cmd == "layers":
        layers_status = check_layers()
        for ls in layers_status:
            icon = "OK" if ls.available else "UNAVAIL"
            detail = ""
            if ls.stats:
                detail = f" — {json.dumps(ls.stats, ensure_ascii=False)}"
            elif ls.reason:
                detail = f" — {ls.reason}"
            print(f"  [{icon}] {ls.name}{detail}")

    elif cmd == "stats":
        s = stats()
        if "--json" in args:
            print(json.dumps(s, indent=2, ensure_ascii=False))
        else:
            for layer_name, layer_stats in s.items():
                avail = layer_stats.pop("available", False)
                icon = "OK" if avail else "UNAVAIL"
                print(f"\n[{icon}] {layer_name}")
                for k, v in layer_stats.items():
                    print(f"  {k}: {v}")

    elif cmd == "status":
        results = _get_status()
        for r in results:
            print(f"  [{r.source}] {r.text}")

    elif cmd == "preferences":
        results = _get_preferences()
        for r in results:
            print(f"  {r.text}")

    elif cmd == "query":
        # Parse query args
        query_text = ""
        layer_filter = None
        top_k = 5
        output_mode = "text"

        i = 1
        while i < len(args):
            if args[i] == "--layers" and i + 1 < len(args):
                i += 1
                layer_filter = args[i].split(",")
            elif args[i] == "--top" and i + 1 < len(args):
                i += 1
                top_k = int(args[i])
            elif args[i] == "--context":
                output_mode = "context"
            elif args[i] == "--json":
                output_mode = "json"
            elif not args[i].startswith("--"):
                query_text = args[i]
            i += 1

        if not query_text:
            print("Usage: memory_plane.py query [--context|--json] [--layers kb,status] \"query\"")
            return

        if output_mode == "context":
            ctx = get_context(query_text, layers=layer_filter, top_k=top_k)
            print(ctx if ctx else "(no results)")
        elif output_mode == "json":
            results = query(query_text, layers=layer_filter, top_k=top_k)
            print(json.dumps([asdict(r) for r in results], indent=2, ensure_ascii=False))
        else:
            results = query(query_text, layers=layer_filter, top_k=top_k)
            if not results:
                print("(no results)")
            for r in results:
                print(f"  [{r.layer}:{r.source}] (score={r.score:.2f}) {r.text[:120]}")

    else:
        print(f"Unknown command: {cmd}")
        print("Commands: query, stats, status, preferences, layers")


if __name__ == "__main__":
    _cli()
