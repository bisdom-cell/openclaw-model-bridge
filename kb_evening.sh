#!/bin/bash
# cron 环境 PATH 极简，必须显式声明（规则 #13）
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:$PATH"
set -euo pipefail
DATE=$(date +%Y%m%d)
KB_DIR="${KB_BASE:-/Users/bisdom/.kb}"
PHONE="${OPENCLAW_PHONE:-+85200000000}"
TS="$(TZ=Asia/Hong_Kong date '+%Y-%m-%d %H:%M:%S')"
STATUS_FILE="$KB_DIR/last_run_evening.json"

log() { echo "[$TS] kb_evening: $1"; }

TODAY_FILES=$(ls "$KB_DIR/notes/" 2>/dev/null | grep "^$DATE" || true)

if [ -z "$TODAY_FILES" ]; then
    MSG="今日暂无新增知识记录"
else
    TOTAL=$(echo "$TODAY_FILES" | wc -l | tr -d ' ')
    FIRST_FILE=$(echo "$TODAY_FILES" | head -1)
    CONTENT=$(head -20 "$KB_DIR/notes/$FIRST_FILE" | { grep -v '^---' || true; } | { grep -v '^#' || true; } | { grep -v '^$' || true; } | head -3 | tr '\n' ' ')
    FILE_LIST=$(echo "$TODAY_FILES" | head -5 | while read -r f; do echo "  · $f"; done)
    MSG="[kb_evening] 今日知识摘要 $DATE
新增笔记：$TOTAL 条
摘要：${CONTENT:0:100}
文件列表：
$FILE_LIST"
fi

if openclaw message send --channel whatsapp -t "$PHONE" -m "$MSG" >/dev/null 2>&1; then
    log "发送完成: $DATE"
    printf '{"time":"%s","status":"ok","sent":true}\n' "$TS" > "$STATUS_FILE"
else
    log "ERROR: 消息发送失败，请检查 gateway。"
    printf '{"time":"%s","status":"send_failed","sent":false}\n' "$TS" > "$STATUS_FILE"
fi
