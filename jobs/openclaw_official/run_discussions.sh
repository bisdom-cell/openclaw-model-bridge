#!/usr/bin/env bash
# cron 环境 PATH 极简，必须显式声明（规则 #13）
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:$PATH"
set -euo pipefail

# 防重叠执行（mkdir 原子锁，macOS 兼容）
LOCK="/tmp/openclaw_discussions.lockdir"
mkdir "$LOCK" 2>/dev/null || { echo "[discussions] Already running, skip"; exit 0; }
trap 'rmdir "$LOCK" 2>/dev/null' EXIT

ROOT="${ROOT:-$HOME/.openclaw}"
JOB="$ROOT/jobs/openclaw_official"
KB_SRC="${KB_BASE:-$HOME/.kb}/sources/openclaw_official.md"
KB_INBOX="${KB_BASE:-$HOME/.kb}/inbox.md"
CACHE="$JOB/cache"
# V28.1: Discussions 已禁用(404)，改用 GitHub REST API 监控 Issues
# V28.3: 加 GITHUB_TOKEN 认证(5000 req/hr) + ETag 缓存避免限流
API_URL="https://api.github.com/repos/openclaw/openclaw/issues?state=open&sort=created&direction=desc&per_page=20"
TO="${OPENCLAW_PHONE:-+85200000000}"
TS="$(TZ=Asia/Hong_Kong date '+%Y-%m-%d %H:%M:%S')"
STATUS_FILE="$CACHE/last_run_discussions.json"
ETAG_FILE="$CACHE/issues_etag.txt"

log() { echo "[$TS] openclaw_issues: $1"; }

mkdir -p "$CACHE" "$HOME/.kb/sources"
test -f "$KB_SRC"   || echo "# OpenClaw Official Watcher" > "$KB_SRC"
test -f "$KB_INBOX" || echo "# INBOX" > "$KB_INBOX"

# V28.3: 构建认证 + ETag 请求头
AUTH_HEADERS=(-H "Accept: application/vnd.github+json" -H "User-Agent: openclaw-watcher/1.0")
if [ -n "${GITHUB_TOKEN:-}" ]; then
  AUTH_HEADERS+=(-H "Authorization: Bearer $GITHUB_TOKEN")
fi
if [ -f "$ETAG_FILE" ]; then
  AUTH_HEADERS+=(-H "If-None-Match: $(cat "$ETAG_FILE")")
fi

API_JSON="$CACHE/issues_api.json"
HTTP_CODE="$(curl -sSL --max-time 30 -w '%{http_code}' \
    -D "$CACHE/issues_headers.txt" \
    "${AUTH_HEADERS[@]}" \
    "$API_URL" -o "$CACHE/issues_api_new.json" 2>"$CACHE/curl_issues_api.err")"

# 304 Not Modified → 无新数据，直接复用缓存（不消耗限额）
if [ "$HTTP_CODE" -eq 304 ]; then
  log "304 Not Modified, 无新 issue。"
  printf '{"time":"%s","status":"ok","new":0,"cached":true}\n' "$TS" > "$STATUS_FILE"
  exit 0
fi

if [ "$HTTP_CODE" -lt 200 ] || [ "$HTTP_CODE" -ge 300 ]; then
  ERR_MSG="⚠️ Issues Watcher API 请求失败 HTTP ${HTTP_CODE}（$(TZ=Asia/Hong_Kong date '+%H:%M')）: $(head -1 "$CACHE/curl_issues_api.err" 2>/dev/null)"
  log "ERROR: $ERR_MSG"
  openclaw message send --target "$TO" --message "$ERR_MSG" --json >/dev/null 2>&1 || true
  printf '{"time":"%s","status":"fetch_failed","http":%s,"new":0}\n' "$TS" "$HTTP_CODE" > "$STATUS_FILE"
  exit 1
fi

# 成功：保存 ETag + 更新缓存
mv "$CACHE/issues_api_new.json" "$API_JSON"
grep -i '^etag:' "$CACHE/issues_headers.txt" 2>/dev/null | sed 's/^[Ee][Tt][Aa][Gg]: *//' | tr -d '\r' > "$ETAG_FILE" || true

# 解析 JSON → 标题|URL|日期（过滤掉 pull_request 条目，只保留纯 issue）
if ! python3 -c "
import json, sys
with open(sys.argv[1]) as f:
    data = json.load(f)
if not isinstance(data, list):
    print(f'API error: {data.get(\"message\", \"unknown\")}', file=sys.stderr)
    sys.exit(1)
count = 0
for item in data:
    # GitHub REST API 的 /issues 端点也返回 PR，用 pull_request 字段区分
    if 'pull_request' in item:
        continue
    title = item['title']
    url = item['html_url']
    date = item['created_at'][:10]
    print(f'{title}|{url}|{date}')
    count += 1
print(f'[issues] 解析完成: {count} 条 issues', file=sys.stderr)
" "$API_JSON" > "$CACHE/discussions_raw.txt" 2>"$CACHE/parse_issues.err"; then
  ERR_MSG="⚠️ Issues Watcher 解析失败（$(TZ=Asia/Hong_Kong date '+%H:%M')）: $(head -1 "$CACHE/parse_issues.err" 2>/dev/null)"
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
        echo "- [ ] ($day) openclaw issues | $title | $url" >> "$KB_INBOX"
        echo "- **[$title]($url)** | $date" >> "$KB_SRC"
        echo "$title|$url|$date" >> "$CACHE/discussions_send.txt"
    fi
done < "$CACHE/discussions_raw.txt"

cnt="$(wc -l < "$CACHE/discussions_send.txt" | tr -d ' ')"
if [ "$cnt" -eq 0 ]; then
    log "暂无新 issue。"
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
第二行：贡献：[1句话≤40字，说明这个 issue 的核心价值或问题]
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
贡献：社区 issue，建议关注。
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

SEND_ERR=$(mktemp)
if openclaw message send --target "$TO" --message "$(cat "$MSG")" --json >/dev/null 2>"$SEND_ERR"; then
    log "已推送 ${cnt} 条新 issue（含LLM富摘要）。"
    printf '{"time":"%s","status":"ok","new":%d,"sent":true}\n' "$TS" "$cnt" > "$STATUS_FILE"
else
    log "ERROR: 推送失败（${cnt} 条待发）: $(cat "$SEND_ERR" | head -3)"
    printf '{"time":"%s","status":"send_failed","new":%d,"sent":false}\n' "$TS" "$cnt" > "$STATUS_FILE"
fi
rsync -a --quiet "$HOME/.kb/" "/Volumes/MOVESPEED/KB/" 2>/dev/null || true
