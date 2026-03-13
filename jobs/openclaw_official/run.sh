#!/usr/bin/env bash
# cron 环境 PATH 极简，必须显式声明（规则 #13）
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:$PATH"
set -euo pipefail

# 防重叠执行（flock）
LOCK="/tmp/openclaw_releases.lock"
exec 200>"$LOCK"
flock -n 200 || { echo "[releases] Already running, skip"; exit 0; }

day="$(TZ=Asia/Hong_Kong date "+%Y-%m-%d")"

ROOT="${ROOT:-$HOME/.openclaw}"
JOB_DIR="$ROOT/jobs/openclaw_official"

FETCH="$JOB_DIR/fetch_github_releases.sh"
FORMAT_PY="$JOB_DIR/format_github_releases.py"
STATE="$JOB_DIR/state.json"
CACHE_DIR="$JOB_DIR/cache"
MSG="$CACHE_DIR/system_message.txt"

KB_SRC="${KB_BASE:-$HOME/.kb}/sources/openclaw_official.md"
KB_INBOX="${KB_BASE:-$HOME/.kb}/inbox.md"

TS="$(TZ=Asia/Hong_Kong date '+%Y-%m-%d %H:%M:%S')"
STATUS_FILE="$CACHE_DIR/last_run.json"

log() { echo "[$TS] openclaw_releases: $1"; }

mkdir -p "$CACHE_DIR" "$HOME/.kb/sources" "$ROOT/logs/jobs"

# init files if absent
if [ ! -f "$STATE" ]; then
  printf "%s\n" "{\"github_releases\":{\"last_updated\":null,\"seen_ids\":[]}}" > "$STATE"
fi
if [ ! -f "$KB_SRC" ]; then
  echo "# OpenClaw Official Watcher" > "$KB_SRC"
fi
if [ ! -f "$KB_INBOX" ]; then
  echo "# INBOX" > "$KB_INBOX"
fi

if ! ATOM_PATH="$("$FETCH" 2>"$CACHE_DIR/fetch_releases.err")"; then
  ERR_MSG="⚠️ OpenClaw Releases 抓取失败（$(TZ=Asia/Hong_Kong date '+%H:%M')）: $(head -1 "$CACHE_DIR/fetch_releases.err" 2>/dev/null)"
  log "ERROR: $ERR_MSG"
  TO="${OPENCLAW_PHONE:-+85200000000}"
  openclaw message send --target "$TO" --message "$ERR_MSG" --json >/dev/null 2>&1 || true
  printf '{"time":"%s","status":"fetch_failed","new":0}\n' "$TS" > "$STATUS_FILE"
  exit 1
fi

# JSONL stream (string) -> write to temp file to avoid pipe subshell issues
JSONL_FILE="$(mktemp)"
if [ -n "$ATOM_PATH" ] && [ -f "$ATOM_PATH" ]; then
  "$FORMAT_PY" "$ATOM_PATH" > "$JSONL_FILE" 2>/dev/null || true
fi

last_updated="$(jq -r ".github_releases.last_updated" "$STATE")"
seen_ids="$(jq -c ".github_releases.seen_ids" "$STATE")"

new_count=0
new_last_updated=""
new_ids_file="$(mktemp)"
new_events_file="$(mktemp)"

# Read JSONL from file (no subshell)
while IFS= read -r line; do
  [ -z "${line// }" ] && continue

  eid="$(printf "%s\n" "$line" | jq -r ".id")"
  ts="$(printf "%s\n" "$line" | jq -r ".ts")"

  # already seen?
  if printf "%s\n" "$seen_ids" | jq -e --arg id "$eid" "index(\$id) != null" >/dev/null 2>&1; then
    continue
  fi

  # only strictly newer than last_updated
  if [ "$last_updated" != "null" ] && [ -n "$last_updated" ]; then
    if [[ "$ts" < "$last_updated" || "$ts" == "$last_updated" ]]; then
      continue
    fi
  fi

  printf "%s\n" "$line" >> "$new_events_file"
  printf "%s\n" "$eid" >> "$new_ids_file"
  new_count=$((new_count+1))
  if [ -z "$new_last_updated" ]; then
    new_last_updated="$ts"
  fi
done < "$JSONL_FILE"

rm -f "$JSONL_FILE"

log "releases_new=${new_count}"

if [ "$new_count" -eq 0 ]; then
  log "no new updates."
  printf '{"time":"%s","status":"ok","new":0}\n' "$TS" > "$STATUS_FILE"
  rm -f "$new_ids_file" "$new_events_file"
  exit 0
fi
TO="${OPENCLAW_PHONE:-+85200000000}"
now_hkt="$(TZ=Asia/Hong_Kong date "+%Y-%m-%d %H:%M HKT")"
{
  echo "🦞 OpenClaw 版本更新 (${now_hkt})"
  echo ""
  while IFS= read -r ev; do
    title="$(printf "%s\n" "$ev" | jq -r ".title")"
    url="$(printf "%s\n" "$ev" | jq -r ".url")"
    ts="$(printf "%s\n" "$ev" | jq -r ".ts" | cut -dT -f1)"

    # LLM 富摘要（对齐 discussions/blog watcher 风格）
    PROMPT="你是OpenClaw项目的技术编辑。请严格输出三行，不要输出其他内容：
第一行：直接输出中文标题（翻译或意译原标题，≤20字，不要加任何前缀标签）
第二行：贡献：[1句话≤40字，说明这个版本的核心变更或价值]
第三行：价值：⭐（1到5个星，评估对用户的升级紧迫度）

原标题：${title}
链接：${url}"

    # 规则 #27: 纯推理直接 curl proxy:5002，禁止用 openclaw agent（#94教训）
    ENRICH="$(curl -sS --max-time 30 http://localhost:5002/v1/chat/completions \
      -H 'Content-Type: application/json' \
      -d "$(jq -nc --arg p "$PROMPT" '{model:"any",messages:[{role:"user",content:$p}],max_tokens:200}')" \
      2>"$CACHE_DIR/curl_releases.err" | jq -r '.choices[0].message.content // empty' 2>/dev/null || true)"

    # fallback：LLM失败或429限流时用原标题
    if [ -z "${ENRICH// }" ] || echo "$ENRICH" | grep -q "429"; then
      log "WARN: LLM enrichment failed for: $title (err: $(cat "$CACHE_DIR/curl_releases.err" 2>/dev/null | head -1))"
      ENRICH="${title}
贡献：新版本发布，建议关注。
价值：⭐⭐⭐"
    fi

    CN_TITLE="$(echo "$ENRICH" | sed -n '1p' | tr -d '[]')"
    CONTRIB="$(echo "$ENRICH" | grep '贡献：' | head -1)"
    STARS="$(echo "$ENRICH" | grep '价值：' | head -1)"

    echo "${CN_TITLE} | ${ts}"
    echo "链接：${url}"
    echo "${CONTRIB}"
    echo "${STARS}"
    echo ""
  done < "$new_events_file"

} > "$MSG"

day="$(TZ=Asia/Hong_Kong date "+%Y-%m-%d")"
{
  echo ""
  echo "## ${day}"
  while IFS= read -r ev; do
    title="$(printf "%s\n" "$ev" | jq -r ".title")"
    url="$(printf "%s\n" "$ev" | jq -r ".url")"
    ts="$(printf "%s\n" "$ev" | jq -r ".ts")"
    id="$(printf "%s\n" "$ev" | jq -r ".id")"
    fp="$(printf "%s\n" "$ev" | jq -r ".fingerprint")"
    echo "- **${title}**"
    echo "  - 时间: ${ts}"
    echo "  - 链接: ${url}"
    echo "  - ID: ${id}"
    echo "  - Fingerprint: ${fp}"
  done < "$new_events_file"
} >> "$KB_SRC"

# INBOX append with de-dup by URL
while IFS= read -r ev; do
  title="$(printf "%s
" "$ev" | jq -r ".title")"
  url="$(printf "%s
" "$ev" | jq -r ".url")"
  line="- [ ] (${day}) openclaw release | ${title} | ${url}"
  if ! grep -Fq "$url" "$KB_INBOX" 2>/dev/null; then
    printf "
%s
" "$line" >> "$KB_INBOX"
  fi
done < "$new_events_file"

add_json="$(jq -R . < "$new_ids_file" | jq -s .)"
updated_seen="$(jq -c --argjson add "$add_json" --argjson seen "$seen_ids" "(\$add + \$seen)[:200]" <<< "{}")"

tmp="$(mktemp)"
jq --arg last "$new_last_updated" --argjson seen "$updated_seen" \
  ".github_releases.last_updated=\$last | .github_releases.seen_ids=\$seen" "$STATE" > "$tmp"
mv "$tmp" "$STATE"

rm -f "$new_ids_file" "$new_events_file"

echo "openclaw_official/github_releases: new=${new_count}, last_updated=${new_last_updated}"
echo "system_message_saved=${MSG}"
echo "kb_source_saved=${KB_SRC}"
echo "kb_inbox_saved=${KB_INBOX}"
echo "---- SYSTEM MESSAGE ----"
cat "$MSG"

# ── rsync备份 ────────────────────────────────────────────────────────
rsync -a --quiet "$HOME/.kb/" "/Volumes/MOVESPEED/KB/" 2>/dev/null || true

# OPTIONAL: announce hook (adapt to your environment)
# "$ROOT/bin/announce.sh" < "$MSG"
SEND_ERR=$(mktemp)
if openclaw message send --target "$TO" --message "$(cat "$MSG")" --json >/dev/null 2>"$SEND_ERR"; then
    log "已推送 ${new_count} 条 releases 更新。"
    printf '{"time":"%s","status":"ok","new":%d,"sent":true}\n' "$TS" "$new_count" > "$STATUS_FILE"
else
    log "ERROR: 推送失败（${new_count} 条待发）: $(cat "$SEND_ERR" | head -3)"
    printf '{"time":"%s","status":"send_failed","new":%d,"sent":false}\n' "$TS" "$new_count" > "$STATUS_FILE"
fi
