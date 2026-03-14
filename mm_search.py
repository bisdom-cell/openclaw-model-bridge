#!/usr/bin/env python3
"""mm_search.py — Multimodal Memory 搜索工具
用文本查询搜索已索引的媒体文件（图片/音频/视频/PDF）。

用法：
  python3 mm_search.py "猫的照片"          # 文本语义搜索
  python3 mm_search.py --stats             # 索引统计
  python3 mm_search.py --top 5 "会议录音"   # 返回 top-5

依赖：pip3 install google-genai numpy
环境变量：GEMINI_API_KEY
"""

import os
import sys
import json
import struct
from datetime import datetime

INDEX_DIR = os.path.expanduser("~/.kb/mm_index")
META_FILE = os.path.join(INDEX_DIR, "meta.json")
VECS_FILE = os.path.join(INDEX_DIR, "vectors.bin")
EMBED_DIM = 768
MODEL_ID = "gemini-embedding-2-preview"
DEFAULT_TOP_K = 5


def load_meta():
    if not os.path.isfile(META_FILE):
        return None
    with open(META_FILE) as f:
        return json.load(f)


def load_vectors(count):
    if not os.path.isfile(VECS_FILE) or count == 0:
        return None
    import numpy as np
    data = np.fromfile(VECS_FILE, dtype=np.float32)
    return data.reshape(count, EMBED_DIM)


def embed_text(client, text):
    from google.genai import types
    result = client.models.embed_content(
        model=MODEL_ID,
        contents=[text],
        config=types.EmbedContentConfig(output_dimensionality=EMBED_DIM),
    )
    return result.embeddings[0].values


def cosine_similarity(query_vec, matrix):
    """计算 query 向量与矩阵中每行的余弦相似度"""
    import numpy as np
    q = np.array(query_vec, dtype=np.float32)
    q_norm = q / (np.linalg.norm(q) + 1e-10)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True) + 1e-10
    m_norm = matrix / norms
    return m_norm @ q_norm


def format_size(size):
    if size < 1024:
        return f"{size}B"
    elif size < 1024 * 1024:
        return f"{size / 1024:.1f}KB"
    else:
        return f"{size / (1024 * 1024):.1f}MB"


def show_stats(meta):
    entries = meta.get("entries", [])
    if not entries:
        print("索引为空，请先运行 python3 mm_index.py")
        return

    # 按类型统计
    by_mime = {}
    total_size = 0
    for e in entries:
        mime = e.get("mime", "unknown")
        by_mime[mime] = by_mime.get(mime, 0) + 1
        total_size += e.get("size", 0)

    print(f"📊 Multimodal Memory 索引统计")
    print(f"   总文件数: {len(entries)}")
    print(f"   总大小:   {format_size(total_size)}")
    print(f"   向量维度: {meta.get('dim', EMBED_DIM)}")
    print(f"   类型分布:")
    for mime, count in sorted(by_mime.items(), key=lambda x: -x[1]):
        category = mime.split("/")[0]
        print(f"     {category:8s} ({mime}): {count} 个")

    # 最近索引的文件
    recent = sorted(entries, key=lambda e: e.get("indexed_at", ""), reverse=True)[:5]
    if recent:
        print(f"   最近索引:")
        for e in recent:
            ts = e.get("indexed_at", "?")[:16]
            print(f"     [{ts}] {e.get('filename', '?')} ({format_size(e.get('size', 0))})")


def search(query, top_k):
    import numpy as np

    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        print("ERROR: GEMINI_API_KEY 未设置")
        sys.exit(1)

    try:
        from google import genai
    except ImportError:
        print("ERROR: 请安装 google-genai: pip3 install google-genai")
        sys.exit(1)

    meta = load_meta()
    if not meta or not meta.get("entries"):
        print("索引为空，请先运行 python3 mm_index.py")
        sys.exit(1)

    entries = meta["entries"]
    vectors = load_vectors(len(entries))
    if vectors is None:
        print("向量文件损坏或不存在")
        sys.exit(1)

    # 生成查询向量
    client = genai.Client(api_key=api_key)
    query_vec = embed_text(client, query)

    # 计算相似度
    scores = cosine_similarity(query_vec, vectors)
    top_indices = np.argsort(scores)[::-1][:top_k]

    print(f"🔍 查询: \"{query}\" (top {top_k})")
    print(f"{'─' * 60}")

    for rank, idx in enumerate(top_indices, 1):
        e = entries[idx]
        score = scores[idx]
        category = e.get("mime", "?").split("/")[0]
        exists = "✅" if os.path.isfile(e.get("path", "")) else "❌"
        print(f"  {rank}. [{score:.3f}] {exists} {e.get('filename', '?')}")
        print(f"     类型: {category} | 大小: {format_size(e.get('size', 0))} | 路径: {e.get('path', '?')}")


def main():
    args = sys.argv[1:]

    if "--stats" in args:
        meta = load_meta()
        if meta:
            show_stats(meta)
        else:
            print("索引不存在，请先运行 python3 mm_index.py")
        return

    top_k = DEFAULT_TOP_K
    if "--top" in args:
        idx = args.index("--top")
        if idx + 1 < len(args):
            top_k = int(args[idx + 1])
            args = [a for i, a in enumerate(args) if i != idx and i != idx + 1]
        else:
            args = [a for i, a in enumerate(args) if i != idx]

    # 剩余参数作为查询
    query_parts = [a for a in args if not a.startswith("--")]
    if not query_parts:
        print("用法: python3 mm_search.py \"查询文本\"")
        print("      python3 mm_search.py --stats")
        print("      python3 mm_search.py --top 10 \"关键词\"")
        return

    query = " ".join(query_parts)
    search(query, top_k)


if __name__ == "__main__":
    main()
