#!/usr/bin/env bash
# cron 环境 PATH 极简，必须显式声明（规则 #13）
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:$PATH"
set -euo pipefail

ROOT="${ROOT:-$HOME/.openclaw}"
JOB="$ROOT/jobs/openclaw_official"
KB_SRC="${KB_BASE:-$HOME/.kb}/sources/openclaw_official.md"
KB_INBOX="${KB_BASE:-$HOME/.kb}/inbox.md"
CACHE="$JOB/cache"
# V28: discussions.atom 已返回404，改用 HTML 抓取 + Python 解析（无需 gh auth）
DISC_URL="https://github.com/openclaw/openclaw/discussions"
DISC_HTML="$CACHE/discussions.html"
NEW_FILE="$CACHE/discussions_new.txt"
TO="${OPENCLAW_PHONE:-+85200000000}"
TS="$(TZ=Asia/Hong_Kong date '+%Y-%m-%d %H:%M:%S')"
STATUS_FILE="$CACHE/last_run_discussions.json"

log() { echo "[$TS] openclaw_discussions: $1"; }

mkdir -p "$CACHE" "$HOME/.kb/sources"
test -f "$KB_SRC"   || echo "# OpenClaw Official Watcher" > "$KB_SRC"
test -f "$KB_INBOX" || echo "# INBOX" > "$KB_INBOX"

# V28: 抓取 discussions 页面 HTML（公开仓库无需认证）
if ! curl -sSL --max-time 30 \
    -H "User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)" \
    "$DISC_URL" -o "$DISC_HTML" 2>"$CACHE/curl_discussions_page.err"; then
  ERR_MSG="⚠️ Discussions Watcher 抓取失败（$(TZ=Asia/Hong_Kong date '+%H:%M')）: $(head -1 "$CACHE/curl_discussions_page.err" 2>/dev/null)"
  log "ERROR: $ERR_MSG"
  openclaw message send --target "$TO" --message "$ERR_MSG" --json >/dev/null 2>&1 || true
  printf '{"time":"%s","status":"fetch_failed","new":0}\n' "$TS" > "$STATUS_FILE"
  exit 1
fi

# 检查 HTML 是否有效（非空且不是错误页）
if [ ! -s "$DISC_HTML" ] || grep -q '"not-found"' "$DISC_HTML" 2>/dev/null; then
  ERR_MSG="⚠️ Discussions Watcher: 页面返回空或 404（$(TZ=Asia/Hong_Kong date '+%H:%M')）"
  log "ERROR: $ERR_MSG"
  openclaw message send --target "$TO" --message "$ERR_MSG" --json >/dev/null 2>&1 || true
  printf '{"time":"%s","status":"fetch_failed","new":0}\n' "$TS" > "$STATUS_FILE"
  exit 1
fi

# V28: Python 解析 HTML 提取 discussions（标题、URL、日期）
if ! python3 - "$DISC_HTML" << 'PYEOF' > "$CACHE/discussions_raw.txt" 2>"$CACHE/parse_discussions.err"
import sys, re, html

html_file = sys.argv[1]
with open(html_file, encoding="utf-8", errors="replace") as f:
    content = f.read()

# GitHub discussions 页面的链接格式：/openclaw/openclaw/discussions/数字
# 标题在 data-hovercard-type="discussion" 附近的 <a> 标签中
results = []
seen = set()

# 方法1: 匹配 discussion 链接 + 标题
for m in re.finditer(
    r'<a[^>]*href="(/openclaw/openclaw/discussions/\d+)"[^>]*>([^<]+)</a>',
    content
):
    path, title = m.group(1), html.unescape(m.group(2).strip())
    if path not in seen and title and len(title) > 3:
        seen.add(path)
        url = f"https://github.com{path}"
        results.append((title, url))

# 方法2: 备用 — 从 JSON-LD 或 data 属性中提取
if not results:
    for m in re.finditer(
        r'discussions/(\d+)["\s][^>]*?(?:title|aria-label)="([^"]+)"',
        content
    ):
        num, title = m.group(1), html.unescape(m.group(2).strip())
        path = f"/openclaw/openclaw/discussions/{num}"
        if path not in seen and title and len(title) > 3:
            seen.add(path)
            url = f"https://github.com{path}"
            results.append((title, url))

# 提取日期（如果有 relative-time 或 datetime 属性，提取最近的）
# 简化处理：使用今天的日期（discussions 页面默认按最近活跃排序）
from datetime import datetime, timezone, timedelta
today = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")

for title, url in results[:20]:
    print(f"{title}|{url}|{today}")

print(f"[discussions] 解析完成: {len(results[:20])} 条", file=sys.stderr)
PYEOF
then
  ERR_MSG="⚠️ Discussions Watcher 解析失败（$(TZ=Asia/Hong_Kong date '+%H:%M')）: $(head -1 "$CACHE/parse_discussions.err" 2>/dev/null)"
  log "ERROR: $ERR_MSG"
  openclaw message send --target "$TO" --message "$ERR_MSG" --json >/dev/null 2>&1 || true
  printf '{"time":"%s","status":"parse_failed","new":0}\n' "$TS" > "$STATUS_FILE"
  exit 1
fi

: > "$CACHE/discussions_send.txt"
day="$(TZ=Asia/Hong_Kong date '+%Y-%m-%d')"

while IFS='|' read -r title url date; do
    [ -z "$url" ] && continue
    if ! grep -Fq "$url" "$KB_INBOX" 2>/dev/null; then
        echo "- [ ] ($day) openclaw discussions | $title | $url" >> "$KB_INBOX"
        echo "- **[$title]($url)** | $date" >> "$KB_SRC"
        echo "$title|$url|$date" >> "$CACHE/discussions_send.txt"
    fi
done < "$CACHE/discussions_raw.txt"

cnt="$(wc -l < "$CACHE/discussions_send.txt" | tr -d ' ')"
if [ "$cnt" -eq 0 ]; then
    log "暂无新讨论。"
    printf '{"time":"%s","status":"ok","new":0}\n' "$TS" > "$STATUS_FILE"
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

    # 规则 #27: 纯推理直接 curl proxy:5002，禁止用 openclaw agent（#94教训）
    ENRICH="$(curl -sS --max-time 30 http://localhost:5002/v1/chat/completions \
      -H 'Content-Type: application/json' \
      -d "$(jq -nc --arg p "$PROMPT" '{model:"any",messages:[{role:"user",content:$p}],max_tokens:200}')" \
      2>"$CACHE/curl_discussions.err" | jq -r '.choices[0].message.content // empty' 2>/dev/null || true)"

    # fallback：LLM失败或429限流时用原标题
    if [ -z "${ENRICH// }" ] || echo "$ENRICH" | grep -q "429"; then
        log "WARN: LLM enrichment failed for: $title (err: $(cat "$CACHE/curl_discussions.err" 2>/dev/null | head -1))"
        ENRICH="[${title}]
贡献：社区讨论，建议关注。
价值：⭐⭐⭐"
    fi

    # 提取三行
    CN_TITLE="$(echo "$ENRICH" | sed -n '1p' | tr -d '[]')"
    CONTRIB="$(echo "$ENRICH" | grep '贡献：' | head -1)"
    STARS="$(echo "$ENRICH" | grep '价值：' | head -1)"

    echo "*${CN_TITLE}* | ${date}" >> "$MSG"
    echo "链接：${url}" >> "$MSG"
    echo "${CONTRIB}" >> "$MSG"
    echo "${STARS}" >> "$MSG"
    echo "" >> "$MSG"

done < "$CACHE/discussions_send.txt"

if openclaw message send --target "$TO" --message "$(cat "$MSG")" --json >/dev/null 2>&1; then
    log "已推送 ${cnt} 条新讨论（含LLM富摘要）。"
    printf '{"time":"%s","status":"ok","new":%d,"sent":true}\n' "$TS" "$cnt" > "$STATUS_FILE"
else
    log "ERROR: 推送失败（${cnt} 条待发），请检查 gateway。"
    printf '{"time":"%s","status":"send_failed","new":%d,"sent":false}\n' "$TS" "$cnt" > "$STATUS_FILE"
fi
rsync -a --quiet "$HOME/.kb/" "/Volumes/MOVESPEED/KB/" 2>/dev/null || true
