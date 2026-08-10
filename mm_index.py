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


def verify_alignment(meta):
    """对齐校验 + 自愈（V37.9.292，对抗审计 B-F1 机制 c）。

    不变式: filesize(vectors.bin) == len(entries) × EMBED_DIM × 4。
    提交顺序是逐文件 append_vector + 收尾一次 save_meta，中途崩溃（历史触发器:
    file_hash TOCTOU）会让 vectors.bin 超前 meta 永久错位 → mm_search reshape
    ValueError 被 memory_plane 吞成 [] = 多模态层静默死亡，此前仅 --reindex 能修。
    自愈到一致前缀（向量按 entry 顺序追加，前缀一一对应）:
      - vectors 超前: 截断孤儿尾部向量，对应文件 hash 未入 meta，本轮自然重新
        embed → 收敛；meta 不变。
      - vectors 短缺（磁盘截断/撕裂写）: 先把 vectors.bin 对齐到整行边界，再把
        meta 截到实际向量数，被丢弃的尾部文件本轮重新索引。
    返回 True 表示 meta 被修改（调用方须落盘）。
    """
    n = len(meta.get("entries", []))
    row = EMBED_DIM * 4
    expected = n * row
    actual = os.path.getsize(VECS_FILE) if os.path.isfile(VECS_FILE) else 0
    if actual == expected:
        return False
    if actual > expected:
        with open(VECS_FILE, "r+b") as f:
            f.truncate(expected)
        log(f"⚠️ 对齐自愈: vectors.bin 超前 meta ({actual}B > {expected}B), 已截断孤儿向量")
        return False
    keep = actual // row
    if os.path.isfile(VECS_FILE) and actual != keep * row:
        with open(VECS_FILE, "r+b") as f:
            f.truncate(keep * row)
    dropped = n - keep
    meta["entries"] = meta["entries"][:keep]
    log(f"⚠️ 对齐自愈: vectors.bin 短缺 (保留前 {keep} 条, 丢弃 {dropped} 条 meta, 相应文件将重新索引)")
    return True


def _try_size(path):
    """TOCTOU 防御（V37.9.292）: listdir 与 getsize 之间文件可被删除。失败返回 -1（调用方按大小过滤自然跳过）"""
    try:
        return os.path.getsize(path)
    except OSError:
        return -1


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
                                size = _try_size(subpath)
                                if 0 < size <= MAX_FILE_SIZE:
                                    files.append((subpath, ext, size))
                continue
            ext = os.path.splitext(name)[1].lower()
            if ext in MIME_MAP:
                size = _try_size(path)
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

    # V37.9.292 (B-F1 c): 启动即对齐校验自愈 — 永久错位从"仅 --reindex 能修"变每 2h 自愈
    heal_modified = verify_alignment(meta)

    indexed_hashes = {e["hash"] for e in meta["entries"]}

    # 扫描媒体文件
    all_files = scan_media_files()
    log(f"扫描到 {len(all_files)} 个媒体文件，已索引 {len(meta['entries'])} 个")

    new_count = 0
    skip_count = 0
    error_count = 0
    vanished_count = 0

    for path, ext, size in all_files:
        # V37.9.292 (B-F1 c): hash 入 try — scan 与 hash 之间文件可被删除 (TOCTOU)。
        # 此前 FileNotFoundError 直接炸掉整轮 → 本轮已 append 的向量成孤儿 →
        # vectors.bin 超前 meta 永久错位。OSError 归 vanished（良性竞态，刻意不算
        # 系统性失败，不触发 all-fail 告警；embed 阶段网络错误 ConnectionError∈OSError
        # 故网络类失败仍走下方 error_count 路径）。
        try:
            fhash = file_hash(path)
        except OSError as e:
            vanished_count += 1
            log(f"  ⚠️ {os.path.basename(path)}: 读取失败(可能已被删除): {e}")
            continue
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

    # V37.9.292 (B-F1 b): 条件写 — 无变更不刷 meta.json mtime（镜像 kb_embed 无新内容
    # 早退）。此前每 2h 无条件重写 → mtime 永远新鲜 → 任何基于 mtime 的新鲜度判断
    # 结构失效。META 缺失/损坏恢复（load_meta 已把损坏文件挪走）也须落盘。
    meta_dirty = reindex or heal_modified or new_count > 0 or not os.path.isfile(META_FILE)
    if meta_dirty:
        save_meta(meta)
    log(f"完成: 新增 {new_count}, 跳过 {skip_count}, 失败 {error_count}, 消失 {vanished_count}, "
        f"总计 {len(meta['entries'])}" + ("" if meta_dirty else " (无变更, meta 未重写)"))

    # V37.9.292 (B-F1 d): 全部新文件 embed 失败 = run 级系统性故障（key/配额/模型退役，
    # 如 gemini-embedding-2-preview 退役将在此现形）。fail-loud（V37.9.227/282 全源失败
    # 惯例）: ERROR: 行匹配 watchdog err_pattern + exit 2 → cron FAILED: 行。单 poison
    # 文件的持续告警是 feature 非噪声 — 该文件 hash 永不入 meta 每 2h 重试烧配额本就
    # 该被看见。per-file ❌/⚠️ 保持不匹配 err_pattern（FAIL-OPEN 惯例）。
    if error_count > 0 and new_count == 0:
        log(f"ERROR: 全部 {error_count} 个待索引文件失败 (API/配额/模型退役?), 索引未推进")
        sys.exit(2)


if __name__ == "__main__":
    main()
