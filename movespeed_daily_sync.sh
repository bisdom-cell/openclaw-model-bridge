#!/bin/bash
# movespeed_daily_sync.sh — 每日 KB 全量同步到外挂 SSD
# V37.9.86: 从 inline crontab 升级为标准 job (消除 convergence 盲区)
# 依赖: movespeed_rsync_helper.sh (V37.9.27 jitter+retry+fail-loud)
set -uo pipefail
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:/usr/bin:/bin"

LOG="$HOME/movespeed_daily_sync.log"
STATUS_FILE="$HOME/.kb/cache/last_run_movespeed_sync.json"
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')

log() { echo "[$TIMESTAMP] $*" >&2; }

log "=== movespeed_daily_sync start ===" >> "$LOG"

SRC="$HOME/.kb/"
DST="/Volumes/MOVESPEED/KB/"

if [ ! -d "/Volumes/MOVESPEED" ]; then
    log "WARN: SSD not mounted, skip sync" >> "$LOG"
    printf '{"status":"skipped_no_ssd","ts":"%s"}\n' "$TIMESTAMP" > "$STATUS_FILE" 2>/dev/null
    exit 0
fi

mkdir -p "$DST" 2>/dev/null

HELPER="$HOME/movespeed_rsync_helper.sh"
if [ -f "$HELPER" ]; then
    bash "$HELPER" "$0" -- -av --delete "$SRC" "$DST" >> "$LOG" 2>&1
    RC=$?
else
    rsync -av --delete "$SRC" "$DST" >> "$LOG" 2>&1
    RC=$?
    if [ "$RC" -ne 0 ]; then
        log "WARN: SSD rsync failed (exit=$RC), helper not found" >> "$LOG"
    fi
fi

if [ "$RC" -eq 0 ]; then
    SIZE=$(du -sh "$DST" 2>/dev/null | cut -f1)
    log "OK: synced $SRC → $DST ($SIZE)" >> "$LOG"
    printf '{"status":"ok","ts":"%s","size":"%s"}\n' "$TIMESTAMP" "$SIZE" > "$STATUS_FILE" 2>/dev/null
else
    log "WARN: sync failed (exit=$RC)" >> "$LOG"
    printf '{"status":"failed","ts":"%s","exit_code":%d}\n' "$TIMESTAMP" "$RC" > "$STATUS_FILE" 2>/dev/null
fi

log "=== movespeed_daily_sync done ===" >> "$LOG"
