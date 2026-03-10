#!/usr/bin/env bash
# cron 环境 PATH 极简，必须显式声明（规则 #13）
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:$PATH"
set -euo pipefail

TS="$(TZ=Asia/Hong_Kong date '+%Y-%m-%d %H:%M:%S')"

ROOT="${ROOT:-$HOME/.openclaw}"
JOB="$ROOT/jobs/openclaw_official"
KB_SRC="$HOME/.kb/sources/openclaw_official.md"
KB_INBOX="$HOME/.kb/inbox.md"
CACHE="$JOB/cache"
STATUS_FILE="$CACHE/last_run_blog.json"

log() { echo "[$TS] openclaw_blog: $1"; }

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
  log "no new posts."
  printf '{"time":"%s","status":"ok","new":0}\n' "$TS" > "$STATUS_FILE"
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
TO="${OPENCLAW_PHONE:-+85200000000}"
{
  while IFS= read -r ev; do
    date="$(printf "%s\n" "$ev" | jq -r ".ts // empty" | cut -dT -f1)"
    title="$(printf "%s\n" "$ev" | jq -r ".title // empty")"
    url="$(printf "%s\n" "$ev" | jq -r ".url // empty")"
    summary="$(printf "%s\n" "$ev" | jq -r ".summary // empty")"
    PROMPT="你是OpenClaw官方博客的技术编辑。请严格输出四行，不要输出其他内容：
第一行：直接输出中文标题（翻译或意译原标题，≤20字，不要加任何前缀标签）
第二行：贡献：[1句话≤40字]
第三行：价值：⭐（1到5个星）
第四行：价值说明：[1句话≤40字]

原标题：${title}
日期：${date}
链接：${url}
摘要：${summary}"
    # 规则 #8: 纯推理直接 curl adapter，禁止用 openclaw agent（#94教训）
    ENRICH="$(curl -sS --max-time 30 http://localhost:5001/v1/chat/completions \
      -H 'Content-Type: application/json' \
      -d "$(jq -nc --arg p "$PROMPT" '{model:"any",messages:[{role:"user",content:$p}],max_tokens:200}')" \
      2>/dev/null | jq -r '.choices[0].message.content // empty' 2>/dev/null || true)"
    # 429限流检测 + 空输出均fallback
    if [ -z "${ENRICH// }" ] || echo "$ENRICH" | grep -q "429"; then
      TITLE_CN="$title"
      ENRICH="贡献：${summary}
价值：⭐⭐⭐
价值说明：官方更新，建议关注。"
    else
      # 从LLM输出提取中文标题（第一行）
      TITLE_CN="$(echo "$ENRICH" | sed -n '1p' | tr -d '[]')"
      # 去掉第一行，保留贡献+价值+说明
      ENRICH="$(echo "$ENRICH" | tail -n +2)"
    fi
    echo "${TITLE_CN} | ${date}"
    echo "链接：${url}"
    echo "$ENRICH"
    echo ""
  done < "$BLOG_NEW"
} > "$MSG"

if openclaw message send --target "$TO" --message "$(cat "$MSG")" --json >/dev/null 2>&1; then
    log "已推送 ${cnt} 篇博客。"
    printf '{"time":"%s","status":"ok","new":%d,"sent":true}\n' "$TS" "$cnt" > "$STATUS_FILE"
else
    log "ERROR: 推送失败（${cnt} 篇待发），请检查 gateway。"
    printf '{"time":"%s","status":"send_failed","new":%d,"sent":false}\n' "$TS" "$cnt" > "$STATUS_FILE"
fi
rsync -a --quiet "$HOME/.kb/" "/Volumes/MOVESPEED/KB/" 2>/dev/null || true
