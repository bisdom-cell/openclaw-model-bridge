#!/usr/bin/env python3
"""
kb_dedup.py — KB 智能去重
扫描 ~/.kb/notes/ 和 ~/.kb/sources/ 中的重复内容，合并或标记。

策略（无外部依赖，纯 Python）：
  1. Notes 精确去重：index.json 中 summary 完全相同 → 保留最新，删除旧的
  2. Notes 模糊去重：内容前 200 字相同 → 标记为疑似重复
  3. Sources 行去重：同一 source 文件中完全重复的行 → 去除
  4. 统计报告 + WhatsApp 推送

用法：
  python3 kb_dedup.py              # dry-run（只报告，不删除）
  python3 kb_dedup.py --apply      # 执行去重（删除重复 notes，去除重复 source 行）
  python3 kb_dedup.py --stats      # 仅输出统计
"""
import os, json, sys, subprocess, hashlib
from datetime import datetime
from collections import defaultdict

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
KB_BASE = os.environ.get("KB_BASE", os.path.expanduser("~/.kb"))
NOTES_DIR = os.path.join(KB_BASE, "notes")
SOURCES_DIR = os.path.join(KB_BASE, "sources")
INDEX_FILE = os.path.join(KB_BASE, "index.json")
REPORT_JSON = os.path.expanduser("~/kb_dedup.json")
PHONE = os.environ.get("OPENCLAW_PHONE", "+85200000000")
OPENCLAW = os.environ.get("OPENCLAW", "/opt/homebrew/bin/openclaw")


def load_index():
    """Load KB index.json."""
    if not os.path.exists(INDEX_FILE):
        return {"entries": []}
    try:
        with open(INDEX_FILE, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {"entries": []}


def save_index(data):
    """Write KB index.json atomically."""
    tmp = INDEX_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, INDEX_FILE)


def read_note_content(filepath):
    """Read note file content, skip YAML frontmatter."""
    full_path = os.path.join(KB_BASE, filepath) if not os.path.isabs(filepath) else filepath
    if not os.path.exists(full_path):
        return ""
    try:
        with open(full_path, "r", errors="replace") as f:
            text = f.read()
    except OSError:
        return ""
    # Strip YAML frontmatter (between --- markers)
    if text.startswith("---"):
        end = text.find("---", 3)
        if end != -1:
            text = text[end + 3:].strip()
    return text


def content_hash(text, length=200):
    """Hash first N chars of content for fuzzy matching."""
    normalized = " ".join(text[:length].split()).lower()
    return hashlib.md5(normalized.encode()).hexdigest()


def find_dangling_index_entries(index):
    """Return index entries whose `file` no longer exists on disk.

    V37.7: dangling refs are a separate class from duplicates — they
    represent index drift after file cleanup / manual removal / crash.
    `apply_note_dedup` silently ignored these (os.path.exists check) so
    they never got cleaned and kept polluting exact/fuzzy passes below.
    Split them out so they're reported independently (not hidden inside
    "duplicates" count) and can be cleaned via a dedicated path.
    """
    dangling = []
    for e in index.get("entries", []):
        filepath = e.get("file", "")
        if not filepath:
            continue
        full = os.path.join(KB_BASE, filepath) if not os.path.isabs(filepath) else filepath
        if not os.path.exists(full):
            dangling.append(e)
    return dangling


def find_duplicate_notes(index):
    """Find duplicate notes in index.

    V37.6: Previously only scanned `index.entries`, which missed unindexed
    notes (files written directly to ~/.kb/notes/ without being added to
    index.json — e.g. from scripts bypassing kb_write.sh, or index drift
    after a crash). Now also walks NOTES_DIR and synthesizes "virtual
    entries" for any .md file not already in the index, so the dedup pass
    can catch them too.

    V37.7: Pre-filter dangling entries (file no longer exists on disk).
    Previously these would be grouped by summary, fed into `apply_note_dedup`,
    and silently no-op'd by `os.remove` on missing files — leaving drift
    undetected. Dangling refs are now reported separately by
    `find_dangling_index_entries` and never participate in duplicate passes.
    This is INV-DEDUP-002 / MR-4.

    Returns:
      exact_dupes: list of (kept_entry, removed_entries) — same summary
      fuzzy_dupes: list of (entry_a, entry_b) — similar content
    """
    raw_entries = list(index.get("entries", []))

    # V37.7: drop dangling index refs BEFORE grouping. They are a different
    # failure class (index drift, not duplicate content) and must not pollute
    # the exact/fuzzy groupings or silently no-op at apply time.
    entries = []
    for e in raw_entries:
        filepath = e.get("file", "")
        if not filepath:
            entries.append(e)
            continue
        full = os.path.join(KB_BASE, filepath) if not os.path.isabs(filepath) else filepath
        if os.path.exists(full):
            entries.append(e)

    # V37.6: augment with unindexed .md files under NOTES_DIR so they also
    # participate in dedup. We build a "notes/<fname>" file path to match
    # the convention used by index.entries.
    indexed_paths = {e.get("file", "") for e in entries}
    if os.path.isdir(NOTES_DIR):
        for fname in os.listdir(NOTES_DIR):
            if not fname.endswith(".md"):
                continue
            rel = os.path.join("notes", fname)
            if rel in indexed_paths:
                continue
            # Synthesize a minimal entry. summary is left empty so this file
            # only participates in the fuzzy-hash pass (content-based), never
            # the summary-based exact pass — we can't invent a summary.
            entries.append({"file": rel, "summary": "", "__unindexed__": True})

    # --- Exact duplicates by summary (indexed only, since unindexed have
    #     empty summary and we don't want to merge "" groups) ---
    summary_groups = defaultdict(list)
    for entry in entries:
        summary = entry.get("summary", "").strip()
        if summary:
            summary_groups[summary].append(entry)

    exact_dupes = []
    for summary, group in summary_groups.items():
        if len(group) > 1:
            # Keep the newest (first in index, since index is newest-first)
            kept = group[0]
            removed = group[1:]
            exact_dupes.append((kept, removed))

    # --- Fuzzy duplicates by content hash (now includes unindexed files) ---
    hash_groups = defaultdict(list)
    for entry in entries:
        filepath = entry.get("file", "")
        if not filepath:
            continue
        text = read_note_content(filepath)
        if len(text) < 20:
            continue
        h = content_hash(text)
        hash_groups[h].append((entry, filepath))

    fuzzy_dupes = []
    for h, group in hash_groups.items():
        if len(group) > 1:
            # Check they aren't already in exact_dupes
            files = {g[1] for g in group}
            exact_files = set()
            for kept, removed_list in exact_dupes:
                for r in removed_list:
                    exact_files.add(r.get("file", ""))
            new_files = files - exact_files
            if len(new_files) > 1:
                fuzzy_dupes.append([(e, fp) for e, fp in group if fp in new_files])

    return exact_dupes, fuzzy_dupes


def find_duplicate_source_lines(sources_dir):
    """Find duplicate lines within each source file, **scoped to H2 section**.

    V37.6: Previously used a file-level `seen` set, which collapsed legitimate
    cross-date recurrences (e.g. RSS item appearing in `## 2026-04-09` AND
    `## 2026-04-10` due to rolling window) into "duplicates" and deleted them
    on --apply, causing history loss.

    Now: reset `seen` whenever a new H2 boundary is crossed. Duplicate content
    is only removed if it appears twice *within the same H2 section*. This is
    the correct contract given V37.6 kb_append_source.sh guarantees each H2
    section is written at most once at the source.

    Returns: dict of {filename: (original_lines, deduped_lines, removed_count)}
    """
    results = {}
    if not os.path.isdir(sources_dir):
        return results

    for fname in os.listdir(sources_dir):
        if not fname.endswith(".md"):
            continue
        fpath = os.path.join(sources_dir, fname)
        try:
            with open(fpath, "r", errors="replace") as f:
                lines = f.readlines()
        except OSError:
            continue

        # H2-scoped dedup. `seen` is reset at each `## ...` boundary so we
        # never collapse the same content line across different dated sections.
        seen = set()
        deduped = []
        removed = 0
        for line in lines:
            stripped = line.strip()

            # H2 boundary → reset per-section seen set
            # (use startswith("## ") not "##" so "###" sub-headings do NOT reset
            # and are still dedup'd within their parent H2)
            if stripped.startswith("## "):
                seen = set()
                deduped.append(line)
                continue

            # H1 and empty lines preserved verbatim, no dedup participation
            if not stripped or stripped.startswith("# "):
                deduped.append(line)
                continue

            # Sub-heading or content line: dedup inside current H2 section
            if stripped in seen:
                removed += 1
                continue
            seen.add(stripped)
            deduped.append(line)

        if removed > 0:
            results[fname] = (lines, deduped, removed)

    return results


def apply_note_dedup(exact_dupes, index):
    """Remove duplicate notes: delete files + remove from index."""
    removed_files = set()
    for kept, removed_list in exact_dupes:
        for entry in removed_list:
            filepath = entry.get("file", "")
            full_path = os.path.join(KB_BASE, filepath)
            if os.path.exists(full_path):
                os.remove(full_path)
                removed_files.add(filepath)

    # Rebuild index without removed entries
    entries = index.get("entries", [])
    index["entries"] = [e for e in entries if e.get("file", "") not in removed_files]
    save_index(index)
    return len(removed_files)


def apply_source_dedup(source_results, sources_dir):
    """Write deduped source files."""
    for fname, (orig, deduped, count) in source_results.items():
        fpath = os.path.join(sources_dir, fname)
        with open(fpath, "w") as f:
            f.writelines(deduped)
    return sum(r[2] for r in source_results.values())


def generate_stats():
    """Quick KB stats without dedup analysis."""
    note_count = 0
    if os.path.isdir(NOTES_DIR):
        note_count = len([f for f in os.listdir(NOTES_DIR) if f.endswith(".md")])
    source_count = 0
    source_size = 0
    if os.path.isdir(SOURCES_DIR):
        for f in os.listdir(SOURCES_DIR):
            if f.endswith(".md"):
                source_count += 1
                source_size += os.path.getsize(os.path.join(SOURCES_DIR, f))
    index = load_index()
    index_entries = len(index.get("entries", []))
    return {
        "note_files": note_count,
        "source_files": source_count,
        "source_size_kb": round(source_size / 1024, 1),
        "index_entries": index_entries,
    }


def format_report(stats, exact_dupes, fuzzy_dupes, source_results, applied):
    """Format dedup report for WhatsApp."""
    lines = [f"🧹 KB 去重报告 {datetime.now().strftime('%Y-%m-%d')}"]
    lines.append("")

    # Stats
    lines.append(f"📊 KB 概览：")
    lines.append(f"   Notes: {stats['note_files']} 个文件 / Index: {stats['index_entries']} 条")
    lines.append(f"   Sources: {stats['source_files']} 个文件 ({stats['source_size_kb']} KB)")

    # Exact duplicates
    exact_count = sum(len(r) for _, r in exact_dupes)
    if exact_count:
        lines.append("")
        lines.append(f"🔴 精确重复 Notes: {exact_count} 个")
        for kept, removed in exact_dupes[:5]:
            lines.append(f"   [{kept.get('summary', '?')[:40]}] × {len(removed) + 1}")
        if len(exact_dupes) > 5:
            lines.append(f"   ... 还有 {len(exact_dupes) - 5} 组")

    # Fuzzy duplicates
    fuzzy_count = sum(len(g) - 1 for g in fuzzy_dupes) if fuzzy_dupes else 0
    if fuzzy_count:
        lines.append("")
        lines.append(f"🟡 疑似重复 Notes: {fuzzy_count} 个")
        for group in fuzzy_dupes[:3]:
            summaries = [e.get("summary", "?")[:30] for e, _ in group]
            lines.append(f"   [{' ≈ '.join(summaries[:2])}]")

    # Source line dedup
    source_total = sum(r[2] for r in source_results.values())
    if source_total:
        lines.append("")
        lines.append(f"📄 Sources 重复行: {source_total} 行")
        for fname, (_, _, count) in sorted(source_results.items(), key=lambda x: -x[1][2])[:5]:
            lines.append(f"   {fname}: {count} 行重复")

    # Action taken
    if exact_count == 0 and source_total == 0:
        lines.append("")
        lines.append("✅ 无重复，KB 健康")
    elif applied:
        lines.append("")
        lines.append(f"✅ 已清理：{exact_count} 个重复 notes + {source_total} 行重复 source")
    else:
        lines.append("")
        lines.append("💡 运行 `python3 kb_dedup.py --apply` 执行清理")

    return "\n".join(lines)


def send_notification(report):
    """Push report via notify.sh (dual-channel + retry)."""
    import tempfile
    try:
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
            f.write(report)
            tmp = f.name
        result = subprocess.run(
            ["bash", "-c", f'source ~/notify.sh && notify "$(cat "{tmp}")" --topic daily; rm -f "{tmp}"'],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode == 0:
            print("[kb_dedup] 推送成功 (WhatsApp + Discord)")
        else:
            print(f"[kb_dedup] ERROR: 推送失败 (exit {result.returncode}): {result.stderr[:200]}")
    except (OSError, subprocess.TimeoutExpired) as e:
        print(f"[kb_dedup] ERROR: 推送异常: {e}")


def write_json(stats, exact_dupes, fuzzy_dupes, source_results, applied):
    """Write machine-readable report."""
    output = {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "stats": stats,
        "exact_duplicates": len(exact_dupes),
        "exact_duplicate_notes": sum(len(r) for _, r in exact_dupes),
        "fuzzy_duplicates": len(fuzzy_dupes),
        "source_duplicate_lines": sum(r[2] for r in source_results.values()),
        "applied": applied,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    try:
        with open(REPORT_JSON, "w") as f:
            json.dump(output, f, indent=2, ensure_ascii=False)
        print(f"[kb_dedup] JSON written to {REPORT_JSON}")
    except OSError as e:
        print(f"[kb_dedup] WARN: Failed to write JSON: {e}")


def main():
    apply_mode = "--apply" in sys.argv
    no_push = "--no-push" in sys.argv
    stats_only = "--stats" in sys.argv

    stats = generate_stats()

    if stats_only:
        print(json.dumps(stats, indent=2, ensure_ascii=False))
        return

    print(f"[kb_dedup] Scanning KB at {KB_BASE}")
    print(f"[kb_dedup] Mode: {'APPLY' if apply_mode else 'DRY-RUN'}")

    index = load_index()
    dangling = find_dangling_index_entries(index)
    exact_dupes, fuzzy_dupes = find_duplicate_notes(index)
    source_results = find_duplicate_source_lines(SOURCES_DIR)

    applied = False
    if apply_mode and (exact_dupes or source_results or dangling):
        if dangling:
            # V37.7: clean dangling refs from index (not file-system ops,
            # so this always succeeds; separate from apply_note_dedup which
            # tries to delete files on disk)
            dangling_paths = {e.get("file", "") for e in dangling}
            index["entries"] = [
                e for e in index.get("entries", [])
                if e.get("file", "") not in dangling_paths
            ]
            save_index(index)
            print(f"[kb_dedup] Removed {len(dangling)} dangling index entries")
        if exact_dupes:
            n = apply_note_dedup(exact_dupes, index)
            print(f"[kb_dedup] Removed {n} duplicate note files")
        if source_results:
            n = apply_source_dedup(source_results, SOURCES_DIR)
            print(f"[kb_dedup] Removed {n} duplicate source lines")
        applied = True

    if dangling and not apply_mode:
        print(f"[kb_dedup] ⚠️  {len(dangling)} dangling index entries "
              f"(file missing on disk) — run --apply to clean")

    report = format_report(stats, exact_dupes, fuzzy_dupes, source_results, applied)
    print(report)

    write_json(stats, exact_dupes, fuzzy_dupes, source_results, applied)

    if no_push:
        return
    total_dupes = sum(len(r) for _, r in exact_dupes) + sum(r[2] for r in source_results.values())
    if total_dupes > 0:
        send_notification(report)
    else:
        print("[kb_dedup] No duplicates, skipping push")


if __name__ == "__main__":
    main()
