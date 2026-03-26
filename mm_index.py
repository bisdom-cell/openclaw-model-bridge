#!/usr/bin/env python3
"""mm_index.py — Multimodal Memory 索引器
扫描 OpenClaw Gateway 媒体目录，调用 Gemini Embedding 2 生成向量，
增量写入本地索引。

用法：
  python3 mm_index.py              # 增量索引新文件
  python3 mm_index.py --reindex    # 重建全部索引

依赖：pip3 install google-genai numpy
环境变量：GEMINI_API_KEY
"""

import os
import sys
import json
import time
import hashlib
import struct
import glob
from datetime import datetime

# ── 配置 ──────────────────────────────────────────────────────────────
MEDIA_DIRS = [
    os.path.expanduser("~/.openclaw/workspace/media/inbound"),
    os.path.expanduser("~/.openclaw/media/inbound"),
    os.path.expanduser("~/.openclaw/attachments"),
]
INDEX_DIR = os.path.expanduser("~/.kb/mm_index")
META_FILE = os.path.join(INDEX_DIR, "meta.json")
VECS_FILE = os.path.join(INDEX_DIR, "vectors.bin")
EMBED_DIM = 768  # 最小推荐维度，节省存储
MODEL_ID = "gemini-embedding-2-preview"
BATCH_PAUSE = 1.0  # 秒，避免超过 60 RPM

# 支持的媒体类型
MIME_MAP = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".mp3": "audio/mp3",
    ".wav": "audio/wav",
    ".ogg": "audio/ogg",
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".pdf": "application/pdf",
}
MAX_FILE_SIZE = 20 * 1024 * 1024  # 20MB，Gemini API 限制


def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] mm_index: {msg}")


def file_hash(path):
    """快速 MD5 哈希（用于去重）"""
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def load_meta():
    """加载已索引文件的元数据（含损坏恢复）"""
    if os.path.isfile(META_FILE):
        try:
            with open(META_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, ValueError):
            # meta.json 损坏（可能是 crash 导致的半写文件），重建索引
            print(f"WARNING: {META_FILE} 损坏，将重建索引")
            backup = META_FILE + ".corrupted"
            try:
                os.replace(META_FILE, backup)
            except OSError:
                pass
    return {"version": 1, "dim": EMBED_DIM, "entries": []}


def save_meta(meta):
    """原子写入 meta.json（tmp + replace，防 crash 损坏）"""
    os.makedirs(INDEX_DIR, exist_ok=True)
    tmp = META_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    os.replace(tmp, META_FILE)


def append_vector(vec):
    """追加一个向量到二进制文件（float32 × dim）"""
    os.makedirs(INDEX_DIR, exist_ok=True)
    with open(VECS_FILE, "ab") as f:
        f.write(struct.pack(f"{EMBED_DIM}f", *vec[:EMBED_DIM]))


def load_vectors(count):
    """加载所有向量"""
    if not os.path.isfile(VECS_FILE) or count == 0:
        return []
    import numpy as np
    data = np.fromfile(VECS_FILE, dtype=np.float32)
    return data.reshape(count, EMBED_DIM)


def scan_media_files():
    """扫描所有媒体目录，返回 (path, ext, size) 列表"""
    files = []
    for d in MEDIA_DIRS:
        if not os.path.isdir(d):
            continue
        # 直接文件
        for name in os.listdir(d):
            path = os.path.join(d, name)
            if not os.path.isfile(path):
                # 子目录（attachments/<uuid>/）
                if os.path.isdir(path):
                    for sub in os.listdir(path):
                        subpath = os.path.join(path, sub)
                        if os.path.isfile(subpath) and not sub.startswith("."):
                            ext = os.path.splitext(sub)[1].lower()
                            if ext in MIME_MAP:
                                size = os.path.getsize(subpath)
                                if 0 < size <= MAX_FILE_SIZE:
                                    files.append((subpath, ext, size))
                continue
            ext = os.path.splitext(name)[1].lower()
            if ext in MIME_MAP:
                size = os.path.getsize(path)
                if 0 < size <= MAX_FILE_SIZE:
                    files.append((path, ext, size))
    return files


def embed_file(client, path, mime_type):
    """调用 Gemini Embedding 2 生成向量"""
    from google.genai import types

    with open(path, "rb") as f:
        data = f.read()

    result = client.models.embed_content(
        model=MODEL_ID,
        contents=[
            types.Part.from_bytes(data=data, mime_type=mime_type),
        ],
        config=types.EmbedContentConfig(output_dimensionality=EMBED_DIM),
    )
    return result.embeddings[0].values


def main():
    reindex = "--reindex" in sys.argv

    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        log("ERROR: GEMINI_API_KEY 未设置")
        sys.exit(1)

    # 延迟导入（pip 依赖）
    try:
        from google import genai
    except ImportError:
        log("ERROR: 请安装 google-genai: pip3 install google-genai")
        sys.exit(1)

    client = genai.Client(api_key=api_key)

    # 加载现有索引
    meta = load_meta()
    if reindex:
        log("重建模式：清除现有索引")
        meta = {"version": 1, "dim": EMBED_DIM, "entries": []}
        if os.path.isfile(VECS_FILE):
            os.remove(VECS_FILE)

    indexed_hashes = {e["hash"] for e in meta["entries"]}

    # 扫描媒体文件
    all_files = scan_media_files()
    log(f"扫描到 {len(all_files)} 个媒体文件，已索引 {len(meta['entries'])} 个")

    new_count = 0
    skip_count = 0
    error_count = 0

    for path, ext, size in all_files:
        fhash = file_hash(path)
        if fhash in indexed_hashes:
            skip_count += 1
            continue

        mime = MIME_MAP[ext]
        try:
            vec = embed_file(client, path, mime)
            entry = {
                "path": path,
                "hash": fhash,
                "mime": mime,
                "size": size,
                "indexed_at": datetime.now().isoformat(),
                "filename": os.path.basename(path),
            }
            meta["entries"].append(entry)
            indexed_hashes.add(fhash)
            append_vector(vec)
            new_count += 1
            log(f"  ✅ {os.path.basename(path)} ({mime}, {size} bytes)")

            # 限流
            time.sleep(BATCH_PAUSE)

        except Exception as e:
            error_count += 1
            log(f"  ❌ {os.path.basename(path)}: {e}")
            # API 限流错误时等久一点
            if "429" in str(e) or "RATE" in str(e).upper():
                log("  ⏳ 限流，等待 10 秒...")
                time.sleep(10)

    # 保存元数据
    save_meta(meta)
    log(f"完成: 新增 {new_count}, 跳过 {skip_count}, 失败 {error_count}, 总计 {len(meta['entries'])}")


if __name__ == "__main__":
    main()
