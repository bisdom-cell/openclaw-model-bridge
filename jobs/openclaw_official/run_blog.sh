#!/usr/bin/env bash
set -eo pipefail
ROOT="${ROOT:-$HOME/.openclaw}"
JOB="$ROOT/jobs/openclaw_official"
KB_SRC="$HOME/.kb/sources/openclaw_official.md"
KB_INBOX="$HOME/.kb/inbox.md"
CACHE="$JOB/cache"
mkdir -p "$CACHE" "$HOME/.kb/sources"
test -f "$KB_SRC" || echo "# OpenClaw Official Watcher" > "$KB_SRC"
test -f "$KB_INBOX" || echo "# INBOX" > "$KB_INBOX"

BLOG_HTML="$("$JOB/fetch_official_blog.sh")"
BLOG_NEW="$CACHE/blog_new.jsonl"
PARSE_TMP="$CACHE/blog_parse.jsonl"
: > "$BLOG_NEW"

# 先落盘，避免pipe子进程变量丢失（bug #76）
python3 "$JOB/parse_official_blog.py" "$BLOG_HTML" > "$PARSE_TMP" 2>/dev/null || true

while IFS= read -r ev; do
  url="$(printf "%s\n" "$ev" | jq -r ".url // empty")"
  [ -z "$url" ] && continue
  if ! grep -Fq "$url" "$KB_INBOX" 2>/dev/null; then
    printf "%s\n" "$ev" >> "$BLOG_NEW"
  fi
done < "$PARSE_TMP"

cnt="$(wc -l < "$BLOG_NEW" | tr -d " ")"
if [ "$cnt" -eq 0 ]; then
  echo "openclaw_official/blog: no new posts."
  exit 0
fi

day="$(TZ=Asia/Hong_Kong date "+%Y-%m-%d")"

# 写入KB归档
{
  echo "## ${day}"
  echo "### Blog"
  while IFS= read -r ev; do
    ts="$(printf "%s\n" "$ev" | jq -r ".ts // empty")"
    title="$(printf "%s\n" "$ev" | jq -r ".title // empty")"
    url="$(printf "%s\n" "$ev" | jq -r ".url // empty")"
    echo "- **${title}**"
    echo "  - Time: ${ts}"
    echo "  - URL: ${url}"
  done < "$BLOG_NEW"
} >> "$KB_SRC"

# 写入INBOX去重
while IFS= read -r ev; do
  title="$(printf "%s\n" "$ev" | jq -r ".title // empty")"
  url="$(printf "%s\n" "$ev" | jq -r ".url // empty")"
  printf "\n- [ ] (%s) openclaw blog | %s | %s\n" "$day" "$title" "$url" >> "$KB_INBOX"
done < "$BLOG_NEW"

# 生成WhatsApp消息
MSG="$CACHE/system_message_blog.txt"
TO="+85256190187"
{
  while IFS= read -r ev; do
    date="$(printf "%s\n" "$ev" | jq -r ".ts // empty" | cut -dT -f1)"
    title="$(printf "%s\n" "$ev" | jq -r ".title // empty")"
    url="$(printf "%s\n" "$ev" | jq -r ".url // empty")"
    summary="$(printf "%s\n" "$ev" | jq -r ".summary // empty")"
    TITLE_CN="$title"
    case "$url" in
      "https://openclaw.ai/blog/virustotal-partnership") TITLE_CN="OpenClaw 与 VirusTotal 合作：提升技能安全" ;;
      "https://openclaw.ai/blog/introducing-openclaw")   TITLE_CN="OpenClaw 项目介绍" ;;
    esac
    PROMPT="你是OpenClaw官方博客的技术编辑。请输出三行：\n1) 贡献：<=40字\n2) 价值：⭐⭐⭐⭐⭐（只输出星号）\n3) 价值说明：<=40字\n\n标题：${title}\n日期：${date}\n链接：${url}\n摘要：${summary}\n"
    ENRICH="$(openclaw agent --to "$TO" --message "$PROMPT" --thinking minimal 2>/dev/null || true)"
    if [ -z "${ENRICH// }" ]; then
      ENRICH="贡献：${summary}\n价值：⭐⭐⭐\n价值说明：官方更新，建议关注。"
    fi
    echo "[${TITLE_CN}] | ${date}"
    echo "链接：${url}"
    echo "$ENRICH"
    echo ""
  done < "$BLOG_NEW"
} > "$MSG"

openclaw message send --target "$TO" --message "$(cat "$MSG")" --json 2>&1 || echo "⚠️ 发送失败 $(date)" >>~/.openclaw/logs/jobs/openclaw_blog.log
echo "openclaw_official/blog: sent ${cnt} new post(s). msg=${MSG}"
rsync -a --quiet "$HOME/.kb/" "/Volumes/MOVESPEED/KB/" 2>/dev/null || true
