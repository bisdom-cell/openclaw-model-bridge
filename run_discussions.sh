#!/usr/bin/env bash
set -euo pipefail

ROOT="${ROOT:-$HOME/.openclaw}"
JOB="$ROOT/jobs/openclaw_official"
KB_SRC="${KB_BASE:-$HOME/.kb}/sources/openclaw_official.md"
KB_INBOX="${KB_BASE:-$HOME/.kb}/inbox.md"
CACHE="$JOB/cache"
FEED_URL="https://github.com/openclaw/openclaw/discussions.atom"
FEED_FILE="$CACHE/discussions.atom"
NEW_FILE="$CACHE/discussions_new.txt"
TO="${OPENCLAW_PHONE:-+85200000000}"

mkdir -p "$CACHE" "$HOME/.kb/sources"
test -f "$KB_SRC"   || echo "# OpenClaw Official Watcher" > "$KB_SRC"
test -f "$KB_INBOX" || echo "# INBOX" > "$KB_INBOX"

curl -fsSL "$FEED_URL" > "$FEED_FILE"

python3 - "$FEED_FILE" << 'PYEOF' > "$NEW_FILE"
import sys, xml.etree.ElementTree as ET

NS = {"a": "http://www.w3.org/2005/Atom"}
tree = ET.parse(sys.argv[1])
root = tree.getroot()

for entry in root.findall("a:entry", NS):
    title = (entry.findtext("a:title", "", NS) or "").strip()
    url   = ""
    for link in entry.findall("a:link", NS):
        if link.get("type") == "text/html":
            url = link.get("href", "")
    date  = (entry.findtext("a:updated", "", NS) or "")[:10]
    if title and url:
        print(f"{title}|{url}|{date}")
PYEOF

: > "$CACHE/discussions_send.txt"
day="$(TZ=Asia/Hong_Kong date '+%Y-%m-%d')"

while IFS='|' read -r title url date; do
    [ -z "$url" ] && continue
    if ! grep -Fq "$url" "$KB_INBOX" 2>/dev/null; then
        echo "- [ ] ($day) openclaw discussions | $title | $url" >> "$KB_INBOX"
        echo "- **[$title]($url)** | $date" >> "$KB_SRC"
        echo "$title|$url|$date" >> "$CACHE/discussions_send.txt"
    fi
done < "$NEW_FILE"

cnt="$(wc -l < "$CACHE/discussions_send.txt" | tr -d ' ')"
if [ "$cnt" -eq 0 ]; then
    echo "openclaw_official/discussions: 暂无新讨论。"
    exit 0
fi

# 逐条 LLM 富摘要，组装消息
MSG="$CACHE/system_message_discussions.txt"
: > "$MSG"

echo "🦞 OpenClaw 社区新动态 (${day})" >> "$MSG"
echo "" >> "$MSG"

while IFS='|' read -r title url date; do
    PROMPT="你是OpenClaw社区的技术编辑。请严格输出三行，不要输出其他内容：
第一行：直接输出中文标题（翻译或意译原标题，≤20字，不要加任何前缀标签）
第二行：贡献：[1句话≤40字，说明这个讨论的核心价值或问题]
第三行：价值：⭐（1到5个星，评估对OpenClaw用户的参考价值）

原标题：${title}
链接：${url}"

    ENRICH="$(openclaw agent --to "$TO" --session-id "$(date +%s%N)" --message "$PROMPT" --thinking minimal 2>/dev/null || true)"

    # fallback：LLM失败时用原标题
    if [ -z "${ENRICH// }" ]; then
        ENRICH="[${title}]
贡献：社区讨论，建议关注。
价值：⭐⭐⭐"
    fi

    # 提取三行
    CN_TITLE="$(echo "$ENRICH" | sed -n '1p' | tr -d '[]')"
    CONTRIB="$(echo "$ENRICH" | grep '贡献：' | head -1)"
    STARS="$(echo "$ENRICH" | grep '价值：' | head -1)"

    echo "${CN_TITLE} | ${date}" >> "$MSG"
    echo "链接：${url}" >> "$MSG"
    echo "${CONTRIB}" >> "$MSG"
    echo "${STARS}" >> "$MSG"
    echo "" >> "$MSG"

done < "$CACHE/discussions_send.txt"

openclaw message send --target "$TO" --message "$(cat "$MSG")" --json >/dev/null 2>&1 || true
echo "openclaw_official/discussions: 已推送 ${cnt} 条新讨论（含LLM富摘要）。"
rsync -a --quiet "$HOME/.kb/" "/Volumes/MOVESPEED/KB/" 2>/dev/null || true
