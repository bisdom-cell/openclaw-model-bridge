#!/usr/bin/env python3
"""kb_embed.py — KB 文本向量索引器

扫描 ~/.kb/notes/ + ~/.kb/sources/，分块生成本地 embedding，
存储到 ~/.kb/text_index/（meta.json + vectors.bin）。

用法：
  python3 kb_embed.py              # 增量索引（跳过已索引文件）
  python3 kb_embed.py --reindex    # 重建全部索引
  python3 kb_embed.py --stats      # 显示索引统计

依赖：pip3 install sentence-transformers
"""

import os
import sys
import json
import hashlib
import struct
import glob
import time
from datetime import datetime

# ── 配置 ──────────────────────────────────────────────────────────────
KB_DIR = os.path.expanduser(os.environ.get("KB_BASE", "~/.kb"))
INDEX_DIR = os.path.join(KB_DIR, "text_index")
META_FILE = os.path.join(INDEX_DIR, "meta.json")
VECS_FILE = os.path.join(INDEX_DIR, "vectors.bin")

# 分块参数
CHUNK_SIZE = 400       # 每块目标字符数
CHUNK_OVERLAP = 80     # 块间重叠字符数
MIN_CHUNK_LEN = 50     # 太短的块丢弃（噪声）

# 索引目录
NOTES_DIR = os.path.join(KB_DIR, "notes")
SOURCES_DIR = os.path.join(KB_DIR, "sources")


def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] kb_embed: {msg}")


def file_hash(path):
    """快速 MD5（用于增量检测）"""
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def load_meta():
    if os.path.isfile(META_FILE):
        with open(META_FILE) as f:
            return json.load(f)
    return {"version": 2, "model": "", "dim": 0, "chunks": []}


def save_meta(meta):
    os.makedirs(INDEX_DIR, exist_ok=True)
    tmp = META_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(meta, f, ensure_ascii=False)
    os.replace(tmp, META_FILE)


def append_vectors(vecs, dim):
    """追加多个向量到二进制文件"""
    os.makedirs(INDEX_DIR, exist_ok=True)
    with open(VECS_FILE, "ab") as f:
        for v in vecs:
            f.write(struct.pack(f"{dim}f", *v[:dim]))


def strip_frontmatter(text):
    """去除 YAML frontmatter"""
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            return parts[2].strip()
    return text.strip()


def chunk_text(text, source_file):
    """将文本分块，返回 [(chunk_text, chunk_idx)] 列表

    策略：按段落优先切分，超长段落按字符数切分。
    每块带来源文件信息，方便 RAG 结果溯源。
    """
    text = strip_frontmatter(text)
    if len(text) < MIN_CHUNK_LEN:
        return []

    # 按双换行分段
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]

    chunks = []
    current = ""
    chunk_idx = 0

    for para in paragraphs:
        # 如果加上这段还在限制内，合并
        if len(current) + len(para) + 2 <= CHUNK_SIZE:
            current = (current + "\n\n" + para).strip() if current else para
        else:
            # 先保存当前块
            if len(current) >= MIN_CHUNK_LEN:
                chunks.append((current, chunk_idx))
                chunk_idx += 1

            # 如果单段超长，按字符切分
            if len(para) > CHUNK_SIZE:
                pos = 0
                while pos < len(para):
                    end = min(pos + CHUNK_SIZE, len(para))
                    piece = para[pos:end]
                    if len(piece) >= MIN_CHUNK_LEN:
                        chunks.append((piece, chunk_idx))
                        chunk_idx += 1
                    pos += CHUNK_SIZE - CHUNK_OVERLAP
                current = ""
            else:
                # overlap：取上一块尾部
                if chunks and CHUNK_OVERLAP > 0:
                    tail = chunks[-1][0][-CHUNK_OVERLAP:]
                    current = tail + "\n\n" + para
                else:
                    current = para

    # 最后一块
    if len(current) >= MIN_CHUNK_LEN:
        chunks.append((current, chunk_idx))

    return chunks


def scan_kb_files():
    """扫描所有 KB 文本文件，返回 [(path, source_type)] 列表"""
    files = []

    # Notes
    if os.path.isdir(NOTES_DIR):
        for f in sorted(glob.glob(os.path.join(NOTES_DIR, "*.md"))):
            files.append((f, "note"))

    # Sources
    if os.path.isdir(SOURCES_DIR):
        for f in sorted(glob.glob(os.path.join(SOURCES_DIR, "*.md"))):
            files.append((f, "source"))

    return files


def show_stats():
    meta = load_meta()
    chunks = meta.get("chunks", [])
    if not chunks:
        print("索引为空，请先运行 python3 kb_embed.py")
        return

    # 按来源统计
    by_source = {}
    by_type = {"note": 0, "source": 0}
    files_seen = set()
    for c in chunks:
        src = c.get("source_type", "?")
        by_type[src] = by_type.get(src, 0) + 1
        fname = os.path.basename(c.get("file", ""))
        by_source[fname] = by_source.get(fname, 0) + 1
        files_seen.add(c.get("file", ""))

    # 向量文件大小
    vec_size = os.path.getsize(VECS_FILE) if os.path.isfile(VECS_FILE) else 0

    print(f"📊 KB Text Index 统计")
    print(f"   模型:     {meta.get('model', '?')}")
    print(f"   向量维度: {meta.get('dim', '?')}")
    print(f"   总 chunks: {len(chunks)}")
    print(f"   源文件数: {len(files_seen)}")
    print(f"   向量文件: {vec_size / 1024:.1f} KB")
    print(f"   类型分布: notes={by_type.get('note', 0)}, sources={by_type.get('source', 0)}")
    print(f"   Top 文件:")
    for fname, count in sorted(by_source.items(), key=lambda x: -x[1])[:10]:
        print(f"     {fname}: {count} chunks")


def main():
    if "--stats" in sys.argv:
        show_stats()
        return

    reindex = "--reindex" in sys.argv

    # 延迟导入（避免无依赖时报错不友好）
    try:
        from local_embed import get_embedder, embed_texts, EMBED_DIM, MODEL_NAME
    except ImportError:
        log("ERROR: 无法导入 local_embed.py，请确保在同目录或 PYTHONPATH 中")
        sys.exit(1)

    # 预加载模型
    log(f"加载模型: {MODEL_NAME}")
    t0 = time.time()
    get_embedder()
    # 重新导入更新后的 EMBED_DIM
    from local_embed import EMBED_DIM as dim
    log(f"模型就绪 ({time.time() - t0:.1f}s)，维度: {dim}")

    # 加载或重建索引
    meta = load_meta()
    if reindex or meta.get("model") != MODEL_NAME:
        if meta.get("model") and meta["model"] != MODEL_NAME:
            log(f"模型变更 ({meta['model']} → {MODEL_NAME})，强制重建")
        log("重建模式：清除现有索引")
        meta = {"version": 2, "model": MODEL_NAME, "dim": dim, "chunks": []}
        if os.path.isfile(VECS_FILE):
            os.remove(VECS_FILE)

    meta["model"] = MODEL_NAME
    meta["dim"] = dim

    # 已索引文件的 hash 集合
    indexed_hashes = {}  # file_path → hash
    for c in meta["chunks"]:
        indexed_hashes[c.get("file", "")] = c.get("file_hash", "")

    # 扫描 KB 文件
    all_files = scan_kb_files()
    log(f"扫描到 {len(all_files)} 个 KB 文件，已索引 {len(indexed_hashes)} 个文件")

    new_chunks = 0
    skip_count = 0
    error_count = 0

    # 批量处理：收集所有新 chunk，最后一次性 embed
    batch_texts = []
    batch_meta = []

    for path, source_type in all_files:
        try:
            fhash = file_hash(path)
        except OSError:
            error_count += 1
            continue

        # 增量：文件未变则跳过
        if path in indexed_hashes and indexed_hashes[path] == fhash:
            skip_count += 1
            continue

        # 文件变更：先删除旧 chunks
        if path in indexed_hashes:
            old_count = len(meta["chunks"])
            meta["chunks"] = [c for c in meta["chunks"] if c.get("file") != path]
            log(f"  ♻️  {os.path.basename(path)} 已变更，移除 {old_count - len(meta['chunks'])} 旧 chunks")

        try:
            with open(path, encoding="utf-8", errors="ignore") as f:
                text = f.read()
        except OSError:
            error_count += 1
            continue

        chunks = chunk_text(text, path)
        if not chunks:
            skip_count += 1
            continue

        for chunk_text_str, chunk_idx in chunks:
            batch_texts.append(chunk_text_str)
            batch_meta.append({
                "file": path,
                "file_hash": fhash,
                "source_type": source_type,
                "chunk_idx": chunk_idx,
                "preview": chunk_text_str[:100].replace("\n", " "),
                "char_len": len(chunk_text_str),
                "indexed_at": datetime.now().isoformat(),
            })

        new_chunks += len(chunks)
        log(f"  📄 {os.path.basename(path)} → {len(chunks)} chunks")

    if not batch_texts:
        log(f"无新内容需要索引 (跳过 {skip_count}, 失败 {error_count})")
        return

    # 批量 embedding（本地模型，无限速）
    log(f"生成 {len(batch_texts)} 个 chunk 的 embedding...")
    t0 = time.time()
    vectors = embed_texts(batch_texts, batch_size=64)
    elapsed = time.time() - t0
    log(f"embedding 完成: {elapsed:.1f}s ({elapsed / len(batch_texts) * 1000:.1f}ms/chunk)")

    # 写入：如果有文件变更导致旧 chunks 被删除，需要重写向量文件
    remaining_old = [c for c in meta["chunks"]]
    if len(remaining_old) != len(meta["chunks"]):
        # 有删除，需要完整重写（罕见路径）
        pass

    # 追加新 chunks 和向量
    meta["chunks"].extend(batch_meta)
    append_vectors(vectors, dim)

    # 如果有文件变更（删除了旧 chunks），需要重建向量文件
    old_file_set = {c.get("file") for c in remaining_old}
    changed_files = set()
    for m in batch_meta:
        if m["file"] in indexed_hashes:
            changed_files.add(m["file"])

    if changed_files:
        log(f"检测到 {len(changed_files)} 个文件变更，重建向量文件...")
        _rebuild_vectors(meta, dim)

    save_meta(meta)
    log(f"完成: 新增 {new_chunks} chunks, 跳过 {skip_count} 文件, "
        f"失败 {error_count}, 总计 {len(meta['chunks'])} chunks")


def _rebuild_vectors(meta, dim):
    """重建向量文件：重新 embed 所有 chunks（文件变更时触发）"""
    from local_embed import embed_texts

    all_texts = [c["preview"] for c in meta["chunks"]]
    # 用完整文本重新 embed 更准确，但 preview 只有 100 字符
    # 需要重新读取源文件获取完整 chunk 文本
    all_texts = []
    for c in meta["chunks"]:
        try:
            with open(c["file"], encoding="utf-8", errors="ignore") as f:
                text = f.read()
            text = strip_frontmatter(text)
            chunks = chunk_text(text, c["file"])
            idx = c.get("chunk_idx", 0)
            if idx < len(chunks):
                all_texts.append(chunks[idx][0])
            else:
                all_texts.append(c["preview"])
        except (OSError, IndexError):
            all_texts.append(c["preview"])

    if not all_texts:
        return

    vectors = embed_texts(all_texts, batch_size=64)

    # 原子写入
    tmp = VECS_FILE + ".tmp"
    with open(tmp, "wb") as f:
        for v in vectors:
            f.write(struct.pack(f"{dim}f", *v[:dim]))
    os.replace(tmp, VECS_FILE)


if __name__ == "__main__":
    main()
