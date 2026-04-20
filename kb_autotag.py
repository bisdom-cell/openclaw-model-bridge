#!/usr/bin/env python3
"""
kb_autotag.py — KB 标签自动化
根据内容关键词自动推断标签，替代硬编码的 "技术/AI"。

用法：
  python3 kb_autotag.py "some content about machine learning"  # 输出标签
  python3 kb_autotag.py --retag                                # 批量重新标记已有 notes
  python3 kb_autotag.py --retag --apply                        # 批量重新标记并写入
  python3 kb_autotag.py --stats                                # 当前标签分布统计
"""
import os, sys, json, re
from collections import Counter
from datetime import datetime

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
KB_BASE = os.environ.get("KB_BASE", os.path.expanduser("~/.kb"))
INDEX_FILE = os.path.join(KB_BASE, "index.json")
NOTES_DIR = os.path.join(KB_BASE, "notes")

# ---------------------------------------------------------------------------
# Tag rules: (tag, keywords_list, weight)
# First match wins within a category; multiple categories can match.
# Output format: "primary/secondary"
# ---------------------------------------------------------------------------
TAG_RULES = [
    # AI & ML
    ("技术/AI", [
        "machine learning", "deep learning", "neural network", "transformer",
        "llm", "gpt", "claude", "gemini", "qwen", "大模型", "大语言模型",
        "ai", "artificial intelligence", "人工智能", "机器学习", "深度学习",
        "embedding", "fine-tune", "finetune", "rag", "prompt", "token",
        "diffusion", "stable diffusion", "midjourney", "dall-e",
        "reinforcement learning", "强化学习", "自然语言处理", "nlp",
        "computer vision", "cv", "计算机视觉",
    ]),
    # Academic / Papers
    ("学术/论文", [
        "arxiv", "paper", "论文", "研究", "research", "abstract",
        "methodology", "experiment", "baseline", "benchmark", "sota",
        "conference", "icml", "neurips", "iclr", "cvpr", "acl", "emnlp",
    ]),
    # Shipping / Freight
    ("物流/货代", [
        "freight", "shipping", "货代", "航运", "集装箱", "container",
        "port", "vessel", "carrier", "运费", "海运", "空运", "物流",
        "importyeti", "supplier", "供应商", "fob", "cif",
        "tariff", "关税", "customs", "报关",
    ]),
    # OpenClaw / Infrastructure
    ("技术/OpenClaw", [
        "openclaw", "gateway", "whatsapp", "proxy", "adapter",
        "plugin", "cron", "session", "launchd", "deploy", "部署",
    ]),
    # Programming / Dev
    ("技术/编程", [
        "python", "javascript", "typescript", "rust", "golang",
        "api", "http", "rest", "graphql", "database", "sql",
        "docker", "kubernetes", "k8s", "git", "github", "cicd",
        "shell", "bash", "linux", "macos", "编程", "代码", "开发",
    ]),
    # Tech News
    ("科技/新闻", [
        "hackernews", "hacker news", "hn", "startup", "创业",
        "funding", "ipo", "acquisition", "科技", "tech news",
        "product hunt", "techcrunch",
    ]),
    # Finance / Crypto
    ("财经/金融", [
        "stock", "股票", "crypto", "bitcoin", "btc", "ethereum",
        "投资", "finance", "金融", "央行", "利率", "通胀",
    ]),
    # Health
    ("生活/健康", [
        "health", "健康", "exercise", "运动", "diet", "饮食",
        "sleep", "睡眠", "mental", "心理",
    ]),
]

# Fallback tag when no rules match
DEFAULT_TAG = "其他/未分类"


def infer_tags(content, max_tags=2):
    """Infer tags from content text. Returns list of tag strings.

    Matches against TAG_RULES using keyword presence.
    Returns up to max_tags, scored by keyword hit count.
    """
    if not content:
        return [DEFAULT_TAG]

    text_lower = content.lower()

    scores = []
    for tag, keywords in TAG_RULES:
        hits = sum(1 for kw in keywords if kw.lower() in text_lower)
        if hits > 0:
            scores.append((tag, hits))

    if not scores:
        return [DEFAULT_TAG]

    # Sort by hit count descending
    scores.sort(key=lambda x: -x[1])
    return [tag for tag, _ in scores[:max_tags]]


def infer_tag_string(content):
    """Return a single tag string for kb_write.sh compatibility.
    If multiple tags, join with comma.
    """
    tags = infer_tags(content)
    return ", ".join(tags)


def read_note_content(filepath):
    """Read note content, skip YAML frontmatter."""
    if not os.path.exists(filepath):
        return ""
    try:
        with open(filepath, "r", errors="replace") as f:
            text = f.read()
    except OSError:
        return ""
    if text.startswith("---"):
        end = text.find("---", 3)
        if end != -1:
            text = text[end + 3:].strip()
    return text


def update_note_tags(filepath, new_tags_str):
    """Update the tags field in a note's YAML frontmatter."""
    try:
        with open(filepath, "r", errors="replace") as f:
            text = f.read()
    except OSError:
        return False

    if not text.startswith("---"):
        return False

    end = text.find("---", 3)
    if end == -1:
        return False

    frontmatter = text[3:end]
    body = text[end:]

    # Replace tags line
    new_fm = re.sub(
        r"tags:\s*\[.*?\]",
        f"tags: [{new_tags_str}]",
        frontmatter,
    )

    if new_fm == frontmatter:
        return False

    with open(filepath, "w") as f:
        f.write("---" + new_fm + body)
    return True


def retag_all(apply=False):
    """Re-tag all existing notes based on content."""
    index = load_index()
    entries = index.get("entries", [])

    changes = []
    for entry in entries:
        filepath = os.path.join(KB_BASE, entry.get("file", ""))
        content = read_note_content(filepath)
        if not content:
            continue

        new_tags = infer_tags(content)
        old_tags = entry.get("tags", [])
        new_tags_str = ", ".join(new_tags)

        if set(new_tags) != set(old_tags):
            changes.append({
                "file": entry.get("file", ""),
                "old": old_tags,
                "new": new_tags,
                "summary": entry.get("summary", "")[:50],
            })

            if apply:
                # Update note file frontmatter
                update_note_tags(filepath, new_tags_str)
                # Update index entry
                entry["tags"] = new_tags

    if apply and changes:
        save_index(index)

    return changes


def load_index():
    if not os.path.exists(INDEX_FILE):
        return {"entries": []}
    try:
        with open(INDEX_FILE, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {"entries": []}


def save_index(data):
    tmp = INDEX_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, INDEX_FILE)


def show_stats():
    """Show current tag distribution."""
    index = load_index()
    entries = index.get("entries", [])
    tag_counter = Counter()
    for entry in entries:
        for tag in entry.get("tags", []):
            tag_counter[tag] += 1

    print(f"📊 KB 标签统计（共 {len(entries)} 条 notes）")
    if not tag_counter:
        print("   无标签数据")
        return

    for tag, count in tag_counter.most_common():
        pct = round(count / len(entries) * 100, 1)
        bar = "█" * max(1, count * 20 // max(tag_counter.values()))
        print(f"   {tag:20s} {count:4d} ({pct:5.1f}%) {bar}")


def main():
    if "--stats" in sys.argv:
        show_stats()
        return

    if "--retag" in sys.argv:
        apply = "--apply" in sys.argv
        mode = "APPLY" if apply else "DRY-RUN"
        print(f"[kb_autotag] 批量重新标记模式: {mode}")

        changes = retag_all(apply=apply)
        if not changes:
            print("[kb_autotag] 所有 notes 标签已是最优，无需更改")
            return

        print(f"[kb_autotag] 发现 {len(changes)} 个标签变更：")
        for c in changes[:20]:
            print(f"   {c['file']}: {c['old']} → {c['new']}  ({c['summary']})")
        if len(changes) > 20:
            print(f"   ... 还有 {len(changes) - 20} 个")

        if not apply:
            print(f"\n运行 `python3 kb_autotag.py --retag --apply` 执行变更")
        else:
            print(f"[kb_autotag] 已更新 {len(changes)} 个 notes 的标签")
        return

    # Single content tagging (for kb_write.sh integration)
    if len(sys.argv) > 1 and not sys.argv[1].startswith("--"):
        content = " ".join(a for a in sys.argv[1:] if not a.startswith("--"))
        tag = infer_tag_string(content)
        print(tag)
        return

    print("用法:")
    print("  python3 kb_autotag.py \"content text\"    # 输出推断的标签")
    print("  python3 kb_autotag.py --retag            # 预览批量重新标记")
    print("  python3 kb_autotag.py --retag --apply    # 执行批量重新标记")
    print("  python3 kb_autotag.py --stats            # 标签分布统计")


if __name__ == "__main__":
    main()
