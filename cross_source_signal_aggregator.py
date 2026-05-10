#!/usr/bin/env python3
"""cross_source_signal_aggregator.py — V37.9.46 Opportunity Radar Stage 1 PoC

跨 source 弱信号聚合器 (Cross-Source Weak Signal Aggregator)

输入: ~/.kb/notes/*.md (今日时间窗内的笔记, 文件名前缀 YYYYMMDD)
输出: ~/.kb/radar/daily_signals_{date}.json (跨 source 共振信号)

核心算法:
  scan_today_notes → embed_notes (sentence-transformer) → cluster_dbscan
  → filter_cross_source (unique_sources >= 2) → rank_signals → emit_radar_json

设计契约 (docs/opportunity_radar_design.md 第三节):
  - DBSCAN 参数: min_samples=3 (至少 3 篇 notes 共振) / eps=0.35
    (sentence-transformer all-MiniLM-L6-v2 同主题阈值经验值)
  - 跨 source 契约: 仅保留 unique_source_names >= 2 的 cluster
  - rank 公式: source_count * 2 + note_count + avg_intra_similarity * 5
  - top 10 returned

FAIL-OPEN 契约 (V37.4 同款 lazy import 模式):
  - dev 环境无 numpy/sklearn/sentence-transformers → ImportError
    上层 main() catch → 写空 signals 数组 + log WARN, 下游不阻塞
  - 任意环节失败 → emit_radar_json([]) 而非崩溃
  - cache 失效 → 重算, 不阻塞主流程

Stage 1 范围 (V37.9.45 计划, 此 V37.9.46 实施):
  - 33 单测覆盖 8 测试类
  - 不集成 kb_dream (Stage 4 才做)
  - 不调 LLM 生成 suggested_topic (Stage 1 用 most-common-keyword)
  - 不做 Jaccard fallback (设计文档 11.2 风险表登记, 留 V37.9.47+)
"""

import os
import sys
import json
import glob
import re
import hashlib
from datetime import datetime
from collections import Counter

# V37.9.46 marker (governance source-level guard 字面量)
_V37_9_46_MARKER = "V37.9.46 Opportunity Radar Stage 1"

# ── 算法常量 (设计文档 3.1 锁定值) ───────────────────────────────────────
DBSCAN_MIN_SAMPLES = 3       # 至少 3 篇 notes 才算共振
DBSCAN_EPS = 0.35            # sentence-transformer 同主题阈值经验值
MIN_UNIQUE_SOURCES = 2       # 跨 source 契约: 至少 2 个不同源
TOP_K_SIGNALS = 10           # 输出前 10 个最强信号
TEXT_TRUNCATE_CHARS = 500    # title + abstract 截断长度
TITLE_MAX_CHARS = 200        # 单行 title 上限

# ── 默认路径 ──────────────────────────────────────────────────────────
KB_DIR_DEFAULT = os.path.expanduser(os.environ.get("KB_BASE", "~/.kb"))
RADAR_DIR_DEFAULT = os.path.join(KB_DIR_DEFAULT, "radar")
EMBEDDING_CACHE_DIR_DEFAULT = os.path.join(RADAR_DIR_DEFAULT, "embedding_cache")


def log(msg):
    """V37.9.46: 走 stderr 防 $(...) 命令替换污染 (MR-11)"""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] cross_source_radar: {msg}", file=sys.stderr)


# ── 1. scan_today_notes ──────────────────────────────────────────────
def _parse_frontmatter(text):
    """从 markdown 文本提取 YAML frontmatter (kb_write.sh 同款格式).

    Returns: dict with keys date / tags / source / type (缺失为空字符串)
             plus 'body' (frontmatter 之后的 markdown content)

    向后兼容:
      - 无 frontmatter → 全部空, body=text
      - 损坏 frontmatter → 尽量解析能解析的, body=空
    """
    result = {"date": "", "tags": [], "source": "", "type": "", "body": ""}

    if not text.startswith("---"):
        result["body"] = text.strip()
        return result

    parts = text.split("---", 2)
    if len(parts) < 3:
        # 损坏 frontmatter, 全部当 body
        result["body"] = text.strip()
        return result

    fm_text = parts[1]
    body = parts[2].strip()
    result["body"] = body

    # 简单 line-by-line YAML parse (避新增 PyYAML 依赖, kb_review_collect 同款 fallback)
    for line in fm_text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip()
        val = val.strip()
        if key == "tags":
            # tags: [tag1, tag2] or tags: tag1 (单个)
            if val.startswith("[") and val.endswith("]"):
                inner = val[1:-1]
                result["tags"] = [t.strip() for t in inner.split(",") if t.strip()]
            elif val:
                result["tags"] = [val]
        elif key in ("date", "source", "type"):
            result[key] = val

    return result


def _extract_title_and_abstract(body, max_chars=TEXT_TRUNCATE_CHARS):
    """从 markdown body 提取 title (第一行 #) 和 abstract (后续段落).

    title: '# Foo' 行去掉 '# ' 前缀, 截到 TITLE_MAX_CHARS
    abstract: title 之后的非空内容, 截到 max_chars
    """
    if not body:
        return "", ""

    lines = body.splitlines()
    title = ""
    abstract_start = 0

    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("# "):
            title = stripped[2:].strip()[:TITLE_MAX_CHARS]
            abstract_start = i + 1
            break
        elif stripped.startswith("#"):
            # '#' without space prefix
            title = stripped.lstrip("#").strip()[:TITLE_MAX_CHARS]
            abstract_start = i + 1
            break

    abstract_lines = []
    for line in lines[abstract_start:]:
        stripped = line.strip()
        if not stripped:
            continue
        # Skip headers (## 核心要点 / ## 记录时间 etc)
        if stripped.startswith("#"):
            continue
        abstract_lines.append(stripped)

    abstract = " ".join(abstract_lines)[:max_chars]
    return title, abstract


def scan_today_notes(date, kb_dir=None):
    """扫描 ~/.kb/notes/ 中今日 (date 前缀) 的笔记.

    Args:
        date: 'YYYYMMDD' 格式日期字符串
        kb_dir: KB 根目录 (默认 ~/.kb)

    Returns:
        list of dict: [{
            'id': filename basename,
            'source_name': str (tags[0] or 'unknown'),
            'title': str,
            'abstract': str (max 500 chars),
            'url': str (TODO future, 现在为空),
            'date': str,
            'tags': list[str],
        }]

    FAIL-OPEN: 文件读不到 → skip + log WARN, 不抛异
    """
    if kb_dir is None:
        kb_dir = KB_DIR_DEFAULT
    notes_dir = os.path.join(kb_dir, "notes")
    if not os.path.isdir(notes_dir):
        log(f"WARN: notes dir not found: {notes_dir}")
        return []

    if not re.match(r"^\d{8}$", date):
        log(f"WARN: invalid date format (expect YYYYMMDD): {date!r}")
        return []

    notes = []
    pattern = os.path.join(notes_dir, f"{date}*.md")
    for path in sorted(glob.glob(pattern)):
        basename = os.path.basename(path)
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError as e:
            log(f"WARN: read failed {basename}: {e}")
            continue

        fm = _parse_frontmatter(text)
        title, abstract = _extract_title_and_abstract(fm["body"])

        # source_name = tags[0] if any else 'unknown' (向后兼容)
        source_name = fm["tags"][0] if fm["tags"] else "unknown"

        notes.append({
            "id": basename,
            "source_name": source_name,
            "title": title,
            "abstract": abstract,
            "url": "",
            "date": fm["date"],
            "tags": fm["tags"],
        })

    return notes


# ── 2. embed_notes (lazy import sentence_transformers + numpy) ────────
def _content_hash_for_cache(notes):
    """计算 notes 列表的 content hash 作为 embedding cache key.

    V37.4 同款 mtime-stable cache key 模式: hash 内容而非 mtime,
    防止文件被工具触碰但内容未变时 cache miss.
    """
    h = hashlib.md5()
    for note in notes:
        # title + abstract 决定 embedding, 其他字段不影响
        text = (note.get("title", "") + "|" + note.get("abstract", ""))
        h.update(text.encode("utf-8", errors="replace"))
    return h.hexdigest()[:16]


def embed_notes(notes, cache_dir=None, force_recompute=False):
    """批量 embedding notes (lazy import sentence-transformers + numpy).

    Args:
        notes: list of dict (from scan_today_notes)
        cache_dir: embedding cache directory
        force_recompute: skip cache, always recompute

    Returns:
        np.ndarray of shape (N, dim) — N = len(notes), dim usually 384

    Raises:
        ImportError: sentence-transformers / numpy 未装 (FAIL-OPEN by upper layer)

    缓存策略:
      cache key = content_hash (V37.4 stable mtime-independent)
      cache file = {cache_dir}/{date}_{cache_key}.npz
      如 cache_dir 不可写 → 静默跳过缓存
    """
    if not notes:
        # Empty input: lazy import numpy still needed for empty array shape
        try:
            import numpy as np
        except ImportError as e:
            raise ImportError(f"numpy required for embed_notes: {e}")
        return np.zeros((0, 384), dtype="float32")

    # Lazy import (FAIL-OPEN by raising ImportError to caller)
    try:
        import numpy as np
    except ImportError as e:
        raise ImportError(f"numpy required: {e}")

    # cache key
    cache_key = _content_hash_for_cache(notes)
    cache_path = None
    if cache_dir is not None and not force_recompute:
        os.makedirs(cache_dir, exist_ok=True)
        cache_path = os.path.join(cache_dir, f"embeddings_{cache_key}.npz")
        if os.path.isfile(cache_path):
            try:
                with np.load(cache_path) as data:
                    cached = data["embeddings"]
                if cached.shape[0] == len(notes):
                    log(f"embedding cache hit: {os.path.basename(cache_path)}")
                    return cached
            except (OSError, KeyError, ValueError) as e:
                log(f"WARN: cache load failed, recomputing: {e}")

    # Lazy import local_embed (which imports sentence-transformers internally)
    try:
        from local_embed import embed_texts
    except ImportError as e:
        raise ImportError(f"local_embed unavailable: {e}")

    texts = [
        (n.get("title", "") + " " + n.get("abstract", ""))[:TEXT_TRUNCATE_CHARS]
        for n in notes
    ]
    embeddings = embed_texts(texts)

    # Save cache
    if cache_path is not None:
        try:
            np.savez_compressed(cache_path, embeddings=embeddings)
        except OSError as e:
            log(f"WARN: cache save failed: {e}")

    return embeddings


# ── 3. cluster_dbscan (lazy import sklearn) ──────────────────────────
def cluster_dbscan(embeddings, min_samples=DBSCAN_MIN_SAMPLES, eps=DBSCAN_EPS):
    """对 embeddings 跑 DBSCAN 聚类 (lazy import sklearn).

    Args:
        embeddings: np.ndarray of shape (N, dim) or empty array
        min_samples: DBSCAN min_samples (default 3)
        eps: DBSCAN eps (default 0.35, sentence-transformer 同主题阈值)

    Returns:
        list[int]: cluster labels of length N (-1 = noise)

    Raises:
        ImportError: sklearn 未装 (FAIL-OPEN by upper layer)
    """
    # Empty input: return empty labels
    try:
        n_samples = len(embeddings)
    except TypeError:
        n_samples = 0
    if n_samples == 0:
        return []

    # Short-circuit: N < min_samples → 全部 noise (V37.9.46: 短路在 lazy import 之前
    # 避免 dev 环境无 sklearn 时小输入也抛 ImportError)
    if n_samples < min_samples:
        return [-1] * n_samples

    # Lazy import (FAIL-OPEN by raising ImportError to caller)
    try:
        from sklearn.cluster import DBSCAN
    except ImportError as e:
        raise ImportError(f"sklearn required for DBSCAN: {e}")

    db = DBSCAN(eps=eps, min_samples=min_samples, metric="cosine")
    labels = db.fit_predict(embeddings)
    return [int(x) for x in labels]


# ── 4. filter_cross_source ───────────────────────────────────────────
def _suggest_topic_keyword(notes_in_cluster):
    """从 cluster 内 notes 的 title 提取最高频关键词作为 suggested_topic.

    Stage 1 简化版 (Stage 4 集成 kb_dream 时改为 LLM 一句话总结).
    """
    if not notes_in_cluster:
        return ""
    # Tokenize titles, lowercase, drop short tokens / digits-only
    tokens = []
    for note in notes_in_cluster:
        title = note.get("title", "").lower()
        # 简单分词: split on whitespace + punctuation
        for tok in re.findall(r"[a-z一-鿿]+", title):
            if len(tok) >= 3:
                tokens.append(tok)
    if not tokens:
        return ""
    counter = Counter(tokens)
    most_common = counter.most_common(3)
    return " ".join(t for t, _ in most_common)


def _avg_intra_similarity(embeddings, indices):
    """计算 cluster 内 embeddings 的平均成对 cosine similarity.

    Args:
        embeddings: np.ndarray (N, dim) — embeddings 已 L2 归一化 (local_embed 默认)
        indices: list[int] — cluster 内 note 索引

    Returns: float in [0, 1] (单 note cluster 返回 1.0)
    """
    if len(indices) < 2:
        return 1.0
    try:
        import numpy as np
    except ImportError:
        # Pure-python fallback (慢但可用) — 仅在 dev 走 FAIL-OPEN 路径不用
        return 0.0

    sub = embeddings[indices]
    # cosine since L2-normalized = dot product
    sims = sub @ sub.T
    n = len(indices)
    # 取上三角 (不含对角)
    total = 0.0
    count = 0
    for i in range(n):
        for j in range(i + 1, n):
            total += float(sims[i, j])
            count += 1
    return total / count if count else 1.0


def filter_cross_source(cluster_labels, notes, embeddings=None,
                         min_unique_sources=MIN_UNIQUE_SOURCES):
    """过滤出跨 source 的 cluster (unique_sources >= min_unique_sources).

    Args:
        cluster_labels: list[int] from cluster_dbscan
        notes: list[dict] from scan_today_notes (顺序与 labels 对齐)
        embeddings: np.ndarray (可选, 计算 avg_intra_similarity)
        min_unique_sources: 跨 source 阈值 (default 2)

    Returns:
        list[Signal]: [{
            'cluster_id': int,
            'notes': list (note dicts),
            'sources': list[str] (sorted unique),
            'source_count': int,
            'note_count': int,
            'avg_intra_similarity': float,
            'suggested_topic': str,
        }]

    契约:
      - 仅 cluster_id != -1 (noise) 才考虑
      - unique_sources < min_unique_sources 的 cluster 被过滤
      - 输入 empty → 返回空 list
    """
    if not cluster_labels or not notes:
        return []
    if len(cluster_labels) != len(notes):
        log(f"WARN: labels/notes length mismatch: {len(cluster_labels)} vs {len(notes)}")
        return []

    # Group notes by cluster_id (skip noise -1)
    cluster_to_indices = {}
    for idx, label in enumerate(cluster_labels):
        if label == -1:
            continue
        cluster_to_indices.setdefault(label, []).append(idx)

    signals = []
    for cluster_id, indices in cluster_to_indices.items():
        cluster_notes = [notes[i] for i in indices]
        unique_sources = sorted(set(n.get("source_name", "") for n in cluster_notes
                                    if n.get("source_name")))
        if len(unique_sources) < min_unique_sources:
            continue

        avg_sim = (_avg_intra_similarity(embeddings, indices)
                   if embeddings is not None else 0.0)

        signals.append({
            "cluster_id": int(cluster_id),
            "notes": cluster_notes,
            "sources": unique_sources,
            "source_count": len(unique_sources),
            "note_count": len(cluster_notes),
            "avg_intra_similarity": float(avg_sim),
            "suggested_topic": _suggest_topic_keyword(cluster_notes),
        })

    return signals


# ── 5. rank_signals ──────────────────────────────────────────────────
def rank_signals(signals, top_k=TOP_K_SIGNALS):
    """按 score 公式排序 signals 并截取 top_k.

    score = source_count * 2 + note_count + avg_intra_similarity * 5

    Args:
        signals: list[Signal] from filter_cross_source
        top_k: 截取上限 (default 10)

    Returns:
        list[Signal] sorted desc by score
    """
    if not signals:
        return []

    def _score(s):
        return (s.get("source_count", 0) * 2
                + s.get("note_count", 0)
                + s.get("avg_intra_similarity", 0.0) * 5)

    sorted_signals = sorted(signals, key=_score, reverse=True)
    # Add score field for downstream visibility
    for s in sorted_signals:
        s["score"] = round(_score(s), 4)
    return sorted_signals[:top_k]


# ── 6. emit_radar_json ───────────────────────────────────────────────
def emit_radar_json(signals, date, output_dir=None):
    """写 daily_signals_{date}.json 到 radar 输出目录.

    Args:
        signals: list[Signal] (可空)
        date: 'YYYYMMDD' 字符串
        output_dir: 输出目录 (default ~/.kb/radar/)

    Returns:
        str: 写入的文件路径
    """
    if output_dir is None:
        output_dir = RADAR_DIR_DEFAULT
    os.makedirs(output_dir, exist_ok=True)

    out_path = os.path.join(output_dir, f"daily_signals_{date}.json")

    # 简化 notes 字段 (只保留可序列化的核心信息, drop 大对象)
    serializable = []
    for s in signals:
        simplified_notes = []
        for n in s.get("notes", []):
            simplified_notes.append({
                "id": n.get("id", ""),
                "source_name": n.get("source_name", ""),
                "title": n.get("title", "")[:TITLE_MAX_CHARS],
                "abstract": n.get("abstract", "")[:300],  # short for JSON
            })
        serializable.append({
            "cluster_id": s.get("cluster_id", -1),
            "score": s.get("score", 0.0),
            "source_count": s.get("source_count", 0),
            "note_count": s.get("note_count", 0),
            "sources": s.get("sources", []),
            "avg_intra_similarity": s.get("avg_intra_similarity", 0.0),
            "suggested_topic": s.get("suggested_topic", ""),
            "notes": simplified_notes,
        })

    payload = {
        "date": date,
        "version": _V37_9_46_MARKER,
        "generated_at": datetime.now().isoformat(),
        "signal_count": len(serializable),
        "signals": serializable,
    }

    tmp = out_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, out_path)

    return out_path


# ── CLI orchestrator ─────────────────────────────────────────────────
def run(date=None, kb_dir=None, output_dir=None, cache_dir=None):
    """主 orchestrator: scan → embed → cluster → filter → rank → emit.

    Returns: dict {'status': 'ok'|'no_notes'|'fail_open_no_deps'|'failed',
                   'signal_count': int, 'output_path': str, 'reason': str}

    FAIL-OPEN 契约: 缺依赖 → 写空 signals + status='fail_open_no_deps' (退化模式)
    """
    if date is None:
        date = datetime.now().strftime("%Y%m%d")
    if kb_dir is None:
        kb_dir = KB_DIR_DEFAULT
    if output_dir is None:
        output_dir = os.path.join(kb_dir, "radar")
    if cache_dir is None:
        cache_dir = os.path.join(output_dir, "embedding_cache")

    log(f"start scan_today_notes date={date}")
    notes = scan_today_notes(date, kb_dir=kb_dir)
    log(f"scanned notes: {len(notes)}")

    if not notes:
        path = emit_radar_json([], date, output_dir=output_dir)
        return {"status": "no_notes", "signal_count": 0,
                "output_path": path, "reason": "no notes today"}

    # Embed (lazy import; fail-open on missing deps)
    try:
        embeddings = embed_notes(notes, cache_dir=cache_dir)
    except ImportError as e:
        log(f"FAIL-OPEN: missing dependency for embedding: {e}")
        path = emit_radar_json([], date, output_dir=output_dir)
        return {"status": "fail_open_no_deps", "signal_count": 0,
                "output_path": path, "reason": str(e)}

    # Cluster (lazy import; fail-open on missing deps)
    try:
        labels = cluster_dbscan(embeddings)
    except ImportError as e:
        log(f"FAIL-OPEN: missing dependency for DBSCAN: {e}")
        path = emit_radar_json([], date, output_dir=output_dir)
        return {"status": "fail_open_no_deps", "signal_count": 0,
                "output_path": path, "reason": str(e)}

    # Filter + rank + emit (pure python, never raises)
    raw_signals = filter_cross_source(labels, notes, embeddings=embeddings)
    log(f"raw cross-source clusters: {len(raw_signals)}")

    signals = rank_signals(raw_signals)
    log(f"ranked signals (top {TOP_K_SIGNALS}): {len(signals)}")

    path = emit_radar_json(signals, date, output_dir=output_dir)
    return {"status": "ok", "signal_count": len(signals),
            "output_path": path, "reason": ""}


def main():
    """CLI entry."""
    import argparse
    parser = argparse.ArgumentParser(
        description="Cross-source weak signal aggregator (V37.9.46 Stage 1)")
    parser.add_argument("--date", default=None,
                        help="Date YYYYMMDD (default today)")
    parser.add_argument("--kb-dir", default=None,
                        help="KB root dir (default ~/.kb)")
    parser.add_argument("--output-dir", default=None,
                        help="Output dir (default <kb-dir>/radar)")
    parser.add_argument("--json", action="store_true",
                        help="Print result as JSON to stdout")
    args = parser.parse_args()

    result = run(date=args.date, kb_dir=args.kb_dir, output_dir=args.output_dir)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"Status: {result['status']}")
        print(f"Signals: {result['signal_count']}")
        print(f"Output:  {result['output_path']}")
        if result.get("reason"):
            print(f"Reason:  {result['reason']}")

    # Exit codes:
    #   0 = ok / no_notes / fail_open_no_deps (FAIL-OPEN 不阻塞下游)
    #   1 = unexpected failure (bug, should fail-fast)
    return 0 if result["status"] in ("ok", "no_notes", "fail_open_no_deps") else 1


if __name__ == "__main__":
    sys.exit(main())
