#!/usr/bin/env python3
"""kb_embed.py — KB 文本向量索引器（100% 全覆盖）

扫描 ~/.kb/ 下所有文本数据（notes/sources/daily/topics/digest/inbox），
分块生成本地 embedding，存储到 ~/.kb/text_index/（meta.json + vectors.bin）。

用法：
  python3 kb_embed.py              # 增量索引（跳过已索引文件）
  python3 kb_embed.py --reindex    # 重建全部索引
  python3 kb_embed.py --stats      # 显示索引统计（含覆盖率）
  python3 kb_embed.py --verify     # 验证覆盖率和内容完整性

依赖：pip3 install sentence-transformers
"""

import os
import sys
import json
import hashlib
import struct
import glob
import time
import fcntl
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
    """SHA256 哈希（用于增量检测 + 完整性校验）"""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def load_meta():
    if os.path.isfile(META_FILE):
        with open(META_FILE) as f:
            return json.load(f)
    return {"version": 2, "model": "", "dim": 0, "chunks": []}


LOCK_FILE = os.path.join(INDEX_DIR, ".lock")


def save_meta(meta):
    os.makedirs(INDEX_DIR, exist_ok=True)
    tmp = META_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(meta, f, ensure_ascii=False)
    os.replace(tmp, META_FILE)


def _acquire_exclusive_lock():
    """获取排他锁（写操作），阻塞直到所有读操作完成"""
    os.makedirs(INDEX_DIR, exist_ok=True)
    lock_fd = open(LOCK_FILE, "w")
    fcntl.flock(lock_fd, fcntl.LOCK_EX)
    return lock_fd


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


import re

# 条目边界模式（source 类文件专用）
# ArXiv/HF/S2/DBLP/ACL: 以 *标题* 开头
# HN: 以 - **[Title] 开头
# Freight: 以 数字. 开头
# 日期头: ## YYYY-MM-DD
_ENTRY_PATTERNS = [
    re.compile(r"^## \d{4}-\d{2}-\d{2}"),     # 日期 header
    re.compile(r"^\*[^*\n]+\*$", re.M),         # *斜体标题*（ArXiv/HF/S2）
    re.compile(r"^- \*\*\["),                    # - **[Title]（HN）
    re.compile(r"^\d+\.\s"),                     # 1. 企业信号（Freight）
    re.compile(r"^### "),                        # ### 子标题
]


def _split_source_entries(text):
    """将 source 类文件按条目边界切分，返回条目列表。

    识别 ArXiv/HF/S2 的 *标题* 行、HN 的 - **[Title] 行、
    Freight 的 数字. 行等作为条目开头。
    """
    lines = text.split("\n")
    entries = []
    current_lines = []

    for line in lines:
        is_boundary = any(p.match(line) for p in _ENTRY_PATTERNS)
        if is_boundary and current_lines:
            entry = "\n".join(current_lines).strip()
            if entry:
                entries.append(entry)
            current_lines = [line]
        else:
            current_lines.append(line)

    # 最后一条
    if current_lines:
        entry = "\n".join(current_lines).strip()
        if entry:
            entries.append(entry)

    return entries


def chunk_text(text, source_file, source_type="note"):
    """将文本分块，返回 [(chunk_text, chunk_idx)] 列表

    策略：
      - source 类文件：按条目边界切分（*标题* / - **[Title] / 数字.）
        每条目保持完整语义，相邻短条目合并到 CHUNK_SIZE
      - note/其他类：按段落(\n\n)切分

    保证零内容丢失：短片段向前/向后合并，不会因 MIN_CHUNK_LEN 丢弃。
    """
    text = strip_frontmatter(text)
    if not text.strip():
        return []

    # source 类文件使用条目感知切分
    if source_type == "source":
        segments = _split_source_entries(text)
    else:
        # note/review/topic/digest: 按双换行分段
        segments = [p.strip() for p in text.split("\n\n") if p.strip()]

    if not segments:
        return []

    chunks = []
    current = ""
    chunk_idx = 0

    for seg in segments:
        # 如果加上这段还在限制内，合并
        if len(current) + len(seg) + 2 <= CHUNK_SIZE:
            current = (current + "\n\n" + seg).strip() if current else seg
        else:
            # 先保存当前块（如果够长）
            if len(current) >= MIN_CHUNK_LEN:
                chunks.append((current, chunk_idx))
                chunk_idx += 1
                current = ""
            # current < MIN_CHUNK_LEN 时不丢弃，保留并向后合并到下一段

            # 如果单段超长，先把残留 current 合并到首块
            if len(seg) > CHUNK_SIZE:
                pos = 0
                while pos < len(seg):
                    end = min(pos + CHUNK_SIZE, len(seg))
                    piece = seg[pos:end]
                    # 首块：合并残留的 current
                    if pos == 0 and current:
                        piece = current + "\n\n" + piece
                        current = ""
                    if len(piece) >= MIN_CHUNK_LEN:
                        chunks.append((piece, chunk_idx))
                        chunk_idx += 1
                    else:
                        # 极短尾片段合并到上一块
                        if chunks:
                            prev_text, prev_idx = chunks[-1]
                            chunks[-1] = (prev_text + "\n\n" + piece, prev_idx)
                    pos += CHUNK_SIZE - CHUNK_OVERLAP
            else:
                # 普通段落：把残留 current 合并进来
                if current:
                    current = current + "\n\n" + seg
                else:
                    # overlap：取上一块尾部
                    if chunks and CHUNK_OVERLAP > 0:
                        tail = chunks[-1][0][-CHUNK_OVERLAP:]
                        current = tail + "\n\n" + seg
                    else:
                        current = seg

    # 最后一块：如果够长直接保存，否则合并到上一块
    if current:
        if len(current) >= MIN_CHUNK_LEN:
            chunks.append((current, chunk_idx))
        elif chunks:
            # 合并到上一块，不丢弃
            prev_text, prev_idx = chunks[-1]
            chunks[-1] = (prev_text + "\n\n" + current, prev_idx)
        else:
            # 整个文件就这点内容，也保留
            chunks.append((current, chunk_idx))

    return chunks


def scan_kb_files():
    """扫描所有 KB 文本文件，返回 [(path, source_type)] 列表

    覆盖范围：
      - notes/*.md      — 用户笔记
      - sources/*.md     — 每日数据归档（ArXiv/HN/GitHub/论文等）
      - daily/*.md       — KB 回顾报告
      - topics/*.md      — 按标签分组的主题笔记
      - daily_digest.md  — 每日 KB 精华摘要
      - inbox.md         — HN URL 收件箱
    """
    files = []

    # Notes
    if os.path.isdir(NOTES_DIR):
        for f in sorted(glob.glob(os.path.join(NOTES_DIR, "*.md"))):
            files.append((f, "note"))

    # Sources
    if os.path.isdir(SOURCES_DIR):
        for f in sorted(glob.glob(os.path.join(SOURCES_DIR, "*.md"))):
            files.append((f, "source"))

    # Daily reviews
    daily_dir = os.path.join(KB_DIR, "daily")
    if os.path.isdir(daily_dir):
        for f in sorted(glob.glob(os.path.join(daily_dir, "*.md"))):
            files.append((f, "review"))

    # Topics
    topics_dir = os.path.join(KB_DIR, "topics")
    if os.path.isdir(topics_dir):
        for f in sorted(glob.glob(os.path.join(topics_dir, "*.md"))):
            files.append((f, "topic"))

    # Root-level KB files
    digest = os.path.join(KB_DIR, "daily_digest.md")
    if os.path.isfile(digest):
        files.append((digest, "digest"))

    inbox = os.path.join(KB_DIR, "inbox.md")
    if os.path.isfile(inbox):
        files.append((inbox, "inbox"))

    return files


def show_stats():
    meta = load_meta()
    chunks = meta.get("chunks", [])
    if not chunks:
        print("索引为空，请先运行 python3 kb_embed.py")
        return

    # 按来源统计
    by_source = {}
    by_type = {}
    files_seen = set()
    total_chars = 0
    for c in chunks:
        src = c.get("source_type", "?")
        by_type[src] = by_type.get(src, 0) + 1
        fname = os.path.basename(c.get("file", ""))
        by_source[fname] = by_source.get(fname, 0) + 1
        files_seen.add(c.get("file", ""))
        total_chars += c.get("char_len", 0)

    # 向量文件大小
    vec_size = os.path.getsize(VECS_FILE) if os.path.isfile(VECS_FILE) else 0

    # 覆盖率计算：扫描所有可索引文件
    all_files = scan_kb_files()
    unindexed = [f for f, _ in all_files if f not in files_seen]

    print(f"📊 KB Text Index 统计")
    print(f"   模型:     {meta.get('model', '?')}")
    print(f"   向量维度: {meta.get('dim', '?')}")
    print(f"   总 chunks: {len(chunks)}")
    print(f"   总字符数: {total_chars:,}")
    print(f"   源文件数: {len(files_seen)}")
    print(f"   向量文件: {vec_size / 1024:.1f} KB")
    print(f"   类型分布: {', '.join(f'{k}={v}' for k, v in sorted(by_type.items()))}")
    print(f"   覆盖率:   {len(files_seen)}/{len(all_files)} 文件 "
          f"({len(files_seen) / max(len(all_files), 1) * 100:.0f}%)")
    if unindexed:
        print(f"   ⚠️ 未索引文件:")
        for f in unindexed:
            print(f"     {os.path.basename(f)}")
    print(f"   Top 文件:")
    for fname, count in sorted(by_source.items(), key=lambda x: -x[1])[:10]:
        print(f"     {fname}: {count} chunks")


def verify_coverage():
    """验证索引覆盖率和内容完整性"""
    meta = load_meta()
    chunks = meta.get("chunks", [])
    indexed_files = {c.get("file", "") for c in chunks}
    indexed_hashes = {}
    for c in chunks:
        indexed_hashes[c.get("file", "")] = c.get("file_hash", "")

    all_files = scan_kb_files()
    total_files = len(all_files)
    indexed_count = 0
    stale_count = 0
    missing_count = 0
    total_source_chars = 0
    total_indexed_chars = 0
    issues = []

    for path, source_type in all_files:
        if not os.path.isfile(path):
            issues.append(f"  ❌ {os.path.basename(path)} — 文件不存在")
            missing_count += 1
            continue

        try:
            with open(path, encoding="utf-8", errors="ignore") as f:
                text = f.read()
            source_chars = len(strip_frontmatter(text))
            total_source_chars += source_chars
        except OSError:
            issues.append(f"  ❌ {os.path.basename(path)} — 读取失败")
            missing_count += 1
            continue

        if path in indexed_files:
            indexed_count += 1
            # 检查是否过期
            fhash = file_hash(path)
            if indexed_hashes.get(path) != fhash:
                stale_count += 1
                issues.append(f"  ⚠️  {os.path.basename(path)} — 索引过期（文件已变更）")

            # 统计已索引字符数
            file_chunks = [c for c in chunks if c.get("file") == path]
            file_indexed_chars = sum(c.get("char_len", 0) for c in file_chunks)
            total_indexed_chars += file_indexed_chars

            # 检查内容覆盖比（允许因 overlap 超过 100%）
            if source_chars > 0:
                ratio = file_indexed_chars / source_chars
                if ratio < 0.5:
                    issues.append(
                        f"  ⚠️  {os.path.basename(path)} — "
                        f"内容覆盖率低: {file_indexed_chars}/{source_chars} "
                        f"({ratio:.0%}), {len(file_chunks)} chunks"
                    )
        else:
            issues.append(f"  ❌ {os.path.basename(path)} [{source_type}] — 未索引 ({source_chars} 字符)")

    # 向量文件一致性
    vec_ok = True
    if os.path.isfile(VECS_FILE) and chunks:
        dim = meta.get("dim", 384)
        expected_bytes = len(chunks) * dim * 4  # float32 = 4 bytes
        actual_bytes = os.path.getsize(VECS_FILE)
        if actual_bytes != expected_bytes:
            vec_ok = False
            issues.append(
                f"  ❌ vectors.bin 大小不一致: 期望 {expected_bytes} bytes, "
                f"实际 {actual_bytes} bytes"
            )

    # 报告
    coverage_pct = indexed_count / max(total_files, 1) * 100
    char_pct = total_indexed_chars / max(total_source_chars, 1) * 100

    print(f"🔍 KB 索引覆盖率验证")
    print(f"   文件覆盖: {indexed_count}/{total_files} ({coverage_pct:.0f}%)")
    print(f"   字符覆盖: {total_indexed_chars:,}/{total_source_chars:,} ({char_pct:.0f}%)")
    print(f"   过期索引: {stale_count}")
    print(f"   向量一致: {'✅' if vec_ok else '❌'}")
    print(f"   总 chunks: {len(chunks)}")

    if issues:
        print(f"\n   问题 ({len(issues)}):")
        for issue in issues:
            print(issue)
    else:
        print(f"\n   ✅ 所有文件已索引且为最新状态")

    return len(issues) == 0


def main():
    if "--stats" in sys.argv:
        show_stats()
        return

    if "--verify" in sys.argv:
        ok = verify_coverage()
        sys.exit(0 if ok else 1)

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

        chunks = chunk_text(text, path, source_type=source_type)
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
                "text": chunk_text_str,
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

    # 追加新 chunks 和向量（排他锁保护，防止搜索读到半写数据）
    lock_fd = _acquire_exclusive_lock()
    try:
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
    finally:
        lock_fd.close()
    log(f"完成: 新增 {new_chunks} chunks, 跳过 {skip_count} 文件, "
        f"失败 {error_count}, 总计 {len(meta['chunks'])} chunks")


def _rebuild_vectors(meta, dim):
    """重建向量文件：重新 embed 所有 chunks（文件变更时触发）

    直接使用 meta 中存储的完整 chunk 文本（自包含，不依赖源文件）。
    """
    from local_embed import embed_texts

    all_texts = []
    for c in meta["chunks"]:
        # 优先用存储的完整文本，兼容旧版索引（无 text 字段）回退到 preview
        all_texts.append(c.get("text", c.get("preview", "")))

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
