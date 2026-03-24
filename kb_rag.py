#!/usr/bin/env python3
"""kb_rag.py — KB RAG 语义搜索工具

用自然语言查询 KB 知识库，返回最相关的文本片段。
可直接注入 LLM prompt 实现 Retrieval-Augmented Generation。

用法：
  python3 kb_rag.py "Qwen3 模型架构"              # 语义搜索 top-5
  python3 kb_rag.py --top 10 "shipping rates"     # 返回 top-10
  python3 kb_rag.py --context "最近的AI论文"        # 输出 LLM 可直接使用的上下文
  python3 kb_rag.py --json "RAG pipeline"          # JSON 输出（供脚本调用）
  python3 kb_rag.py --stats                        # 索引统计

依赖：pip3 install sentence-transformers numpy
前置：需先运行 python3 kb_embed.py 建立索引
"""

import os
import sys
import json
import struct
import numpy as np
from datetime import datetime

# ── 配置 ──────────────────────────────────────────────────────────────
KB_DIR = os.path.expanduser(os.environ.get("KB_BASE", "~/.kb"))
INDEX_DIR = os.path.join(KB_DIR, "text_index")
META_FILE = os.path.join(INDEX_DIR, "meta.json")
VECS_FILE = os.path.join(INDEX_DIR, "vectors.bin")
DEFAULT_TOP_K = 5
SIMILARITY_THRESHOLD = 0.25  # 低于此分数的结果不返回


def load_meta():
    if not os.path.isfile(META_FILE):
        return None
    with open(META_FILE) as f:
        return json.load(f)


def load_vectors(count, dim):
    if not os.path.isfile(VECS_FILE) or count == 0:
        return None
    data = np.fromfile(VECS_FILE, dtype=np.float32)
    expected = count * dim
    if len(data) < expected:
        return None
    return data[:expected].reshape(count, dim)


def get_chunk_text(chunk_meta):
    """从源文件中提取 chunk 的完整文本"""
    fpath = chunk_meta.get("file", "")
    chunk_idx = chunk_meta.get("chunk_idx", 0)

    if not os.path.isfile(fpath):
        return chunk_meta.get("preview", "")

    try:
        with open(fpath, encoding="utf-8", errors="ignore") as f:
            text = f.read()

        # 去除 frontmatter
        if text.startswith("---"):
            parts = text.split("---", 2)
            if len(parts) >= 3:
                text = parts[2].strip()

        # 重新分块找到对应 chunk
        from kb_embed import chunk_text
        chunks = chunk_text(text, fpath)
        if chunk_idx < len(chunks):
            return chunks[chunk_idx][0]
    except Exception:
        pass

    return chunk_meta.get("preview", "")


def search(query, top_k, output_mode="text"):
    """执行语义搜索

    output_mode: "text" | "context" | "json"
    """
    meta = load_meta()
    if not meta or not meta.get("chunks"):
        print("索引为空，请先运行: python3 kb_embed.py", file=sys.stderr)
        sys.exit(1)

    chunks = meta["chunks"]
    dim = meta.get("dim", 384)
    vectors = load_vectors(len(chunks), dim)
    if vectors is None:
        print("向量文件损坏或不存在", file=sys.stderr)
        sys.exit(1)

    # 本地 embedding 生成查询向量
    try:
        from local_embed import embed_text
    except ImportError:
        print("ERROR: 无法导入 local_embed.py", file=sys.stderr)
        sys.exit(1)

    query_vec = embed_text(query)

    # cosine similarity（向量已归一化，但防御零向量和NaN）
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms = np.where(norms < 1e-10, 1.0, norms)
    safe_vectors = vectors / norms
    scores = safe_vectors @ query_vec
    scores = np.nan_to_num(scores, nan=0.0, posinf=0.0, neginf=0.0)
    top_indices = np.argsort(scores)[::-1][:top_k]

    results = []
    for idx in top_indices:
        score = float(scores[idx])
        if score < SIMILARITY_THRESHOLD:
            break
        c = chunks[idx]
        full_text = get_chunk_text(c)
        results.append({
            "score": score,
            "text": full_text,
            "file": c.get("file", ""),
            "filename": os.path.basename(c.get("file", "")),
            "source_type": c.get("source_type", ""),
            "chunk_idx": c.get("chunk_idx", 0),
            "char_len": len(full_text),
        })

    # 输出
    if output_mode == "json":
        print(json.dumps({"query": query, "results": results}, ensure_ascii=False, indent=2))

    elif output_mode == "context":
        # LLM 可直接使用的上下文格式
        if not results:
            print("（未找到相关内容）")
            return results

        print(f"# 知识库检索结果（查询：{query}）\n")
        for i, r in enumerate(results, 1):
            src = r["filename"]
            print(f"## [{i}] {src} (相关度: {r['score']:.2f})")
            print(r["text"])
            print()

    else:  # text
        if not results:
            print(f"未找到与 \"{query}\" 相关的内容")
            return results

        print(f"🔍 查询: \"{query}\" (top {top_k}, 阈值 {SIMILARITY_THRESHOLD})")
        print(f"{'─' * 60}")
        for i, r in enumerate(results, 1):
            src_icon = "📝" if r["source_type"] == "note" else "📰"
            print(f"  {i}. [{r['score']:.3f}] {src_icon} {r['filename']}")
            # 显示预览（前 150 字符）
            preview = r["text"][:150].replace("\n", " ")
            print(f"     {preview}...")
            print()

    return results


def show_stats():
    meta = load_meta()
    if not meta or not meta.get("chunks"):
        print("索引为空，请先运行: python3 kb_embed.py")
        return

    chunks = meta["chunks"]
    dim = meta.get("dim", 0)
    vec_size = os.path.getsize(VECS_FILE) if os.path.isfile(VECS_FILE) else 0
    files = set(c.get("file", "") for c in chunks)
    by_type = {}
    for c in chunks:
        t = c.get("source_type", "?")
        by_type[t] = by_type.get(t, 0) + 1

    print(f"📊 KB RAG Index 统计")
    print(f"   模型:     {meta.get('model', '?')}")
    print(f"   维度:     {dim}")
    print(f"   Chunks:   {len(chunks)}")
    print(f"   源文件:   {len(files)}")
    print(f"   向量大小: {vec_size / 1024:.1f} KB")
    print(f"   Notes:    {by_type.get('note', 0)} chunks")
    print(f"   Sources:  {by_type.get('source', 0)} chunks")


def main():
    args = sys.argv[1:]

    if "--stats" in args:
        show_stats()
        return

    # 解析参数
    top_k = DEFAULT_TOP_K
    output_mode = "text"

    if "--top" in args:
        idx = args.index("--top")
        if idx + 1 < len(args):
            top_k = int(args[idx + 1])
            args = [a for i, a in enumerate(args) if i != idx and i != idx + 1]
        else:
            args = [a for i, a in enumerate(args) if i != idx]

    if "--context" in args:
        output_mode = "context"
        args = [a for a in args if a != "--context"]

    if "--json" in args:
        output_mode = "json"
        args = [a for a in args if a != "--json"]

    query_parts = [a for a in args if not a.startswith("--")]
    if not query_parts:
        print("用法: python3 kb_rag.py \"查询文本\"")
        print("      python3 kb_rag.py --context \"查询\"     # LLM 上下文格式")
        print("      python3 kb_rag.py --json \"查询\"        # JSON 输出")
        print("      python3 kb_rag.py --top 10 \"查询\"      # top-K")
        print("      python3 kb_rag.py --stats              # 索引统计")
        return

    query = " ".join(query_parts)
    search(query, top_k, output_mode)


if __name__ == "__main__":
    main()
