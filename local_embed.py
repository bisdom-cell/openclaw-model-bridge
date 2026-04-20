#!/usr/bin/env python3
"""local_embed.py — 本地 Embedding 引擎

基于 sentence-transformers 的本地向量生成，零 API 调用、零限速、零成本。
Mac Mini Apple Silicon 上单条 ~10ms，批量 100 条 ~500ms。

模型：paraphrase-multilingual-MiniLM-L12-v2（471MB，384维，50+语言）
  - 中英文混合内容最佳平衡（KB 场景：ArXiv英文 + 中文笔记）
  - 可通过 LOCAL_EMBED_MODEL 环境变量切换

用法：
  # 作为模块导入
  from local_embed import get_embedder, embed_texts, embed_text, EMBED_DIM

  # 命令行测试
  python3 local_embed.py "测试文本"
  python3 local_embed.py --bench           # 性能基准测试

依赖：pip3 install sentence-transformers
"""

import os
import sys
import time
import numpy as np

# ── 配置 ──────────────────────────────────────────────────────────────
MODEL_NAME = os.environ.get(
    "LOCAL_EMBED_MODEL",
    "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
)
EMBED_DIM = 384  # paraphrase-multilingual-MiniLM-L12-v2 输出维度

# 模型缓存目录（避免每次重新下载）
CACHE_DIR = os.path.expanduser("~/.cache/local_embed")

# ── 单例模型 ──────────────────────────────────────────────────────────
_model = None


def get_embedder():
    """延迟加载并缓存 SentenceTransformer 模型"""
    global _model, EMBED_DIM
    if _model is not None:
        return _model

    try:
        from sentence_transformers import SentenceTransformer
    except ImportError:
        print("ERROR: 请安装 sentence-transformers: pip3 install sentence-transformers")
        sys.exit(1)

    _model = SentenceTransformer(MODEL_NAME, cache_folder=CACHE_DIR)
    EMBED_DIM = _model.get_sentence_embedding_dimension()
    return _model


def embed_texts(texts: list[str], batch_size: int = 64) -> np.ndarray:
    """批量文本 → 向量矩阵 (N × dim)，自动 L2 归一化"""
    model = get_embedder()
    vecs = model.encode(
        texts,
        batch_size=batch_size,
        normalize_embeddings=True,
        show_progress_bar=False,
    )
    return np.array(vecs, dtype=np.float32)


def embed_text(text: str) -> np.ndarray:
    """单条文本 → 向量 (dim,)"""
    return embed_texts([text])[0]


# ── CLI 测试 ──────────────────────────────────────────────────────────
def main():
    args = sys.argv[1:]

    if "--bench" in args:
        print(f"模型: {MODEL_NAME}")
        print(f"加载模型...")
        t0 = time.time()
        get_embedder()
        print(f"  加载耗时: {time.time() - t0:.2f}s")
        print(f"  向量维度: {EMBED_DIM}")

        # 单条测试
        t0 = time.time()
        v = embed_text("这是一个测试句子")
        print(f"  单条耗时: {(time.time() - t0) * 1000:.1f}ms")
        print(f"  向量范数: {np.linalg.norm(v):.4f} (应≈1.0)")

        # 批量测试
        texts = [f"测试句子 {i}" for i in range(100)]
        t0 = time.time()
        vecs = embed_texts(texts)
        elapsed = time.time() - t0
        print(f"  批量100条: {elapsed * 1000:.0f}ms ({elapsed / 100 * 1000:.1f}ms/条)")
        print(f"  输出形状: {vecs.shape}")

        # 相似度测试
        v1 = embed_text("人工智能论文")
        v2 = embed_text("AI research paper")
        v3 = embed_text("今天天气很好")
        sim_12 = float(v1 @ v2)
        sim_13 = float(v1 @ v3)
        print(f"  '人工智能论文' vs 'AI research paper': {sim_12:.3f}")
        print(f"  '人工智能论文' vs '今天天气很好':       {sim_13:.3f}")
        return

    if not args:
        print(f"用法: python3 local_embed.py \"文本\"")
        print(f"      python3 local_embed.py --bench")
        return

    text = " ".join(args)
    vec = embed_text(text)
    print(f"文本: {text}")
    print(f"维度: {len(vec)}")
    print(f"范数: {np.linalg.norm(vec):.4f}")
    print(f"前5维: {vec[:5]}")


if __name__ == "__main__":
    main()
