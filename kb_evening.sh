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
    # 提取每个笔记的实际正文标题（跳过 YAML frontmatter）
    SUMMARIES=$(python3 - "$KB_DIR/notes" "$DATE" << 'PYEOF'
import os, sys, glob

notes_dir = sys.argv[1]
date_prefix = sys.argv[2]
items = []
for f in sorted(glob.glob(os.path.join(notes_dir, f"{date_prefix}*.md")), reverse=True):
    try:
        with open(f) as fh:
            content = fh.read().strip()
        # 跳过 YAML frontmatter
        if content.startswith('---'):
            parts = content.split('---', 2)
            if len(parts) >= 3:
                content = parts[2].strip()
        # 提取第一个有意义的行
        for line in content.split('\n'):
            line = line.strip()
            if line and not line.startswith('#'):
                items.append(f"  · {line[:80]}")
                break
    except OSError:
        continue
    if len(items) >= 5:
        break
print('\n'.join(items) if items else '  （无可读摘要）')
PYEOF
)
    MSG="[kb_evening] 今日知识摘要 $DATE
新增笔记：$TOTAL 条
内容概览：
$SUMMARIES"
fi

# ── KB 去重（原 kb_dedup 23:00 独立任务，现合并到晚间整理）──────────
DEDUP_REPORT=""
DEDUP_OUTPUT=$(python3 ~/kb_dedup.py --no-push 2>&1) || true
DEDUP_REPORT=$(echo "$DEDUP_OUTPUT" | grep -v '^\[kb_dedup\]')
if [ -n "${DEDUP_REPORT// }" ]; then
    MSG="$MSG

━━━━━━━━━━━━━━━━━━━━

$DEDUP_REPORT"
fi

# ── 推送（一条消息，双通道）──────────────────────────────────────────
MSG="$(echo "$MSG" | head -c 4000)"

NOTIFY_SH="${HOME}/notify.sh"
if [ -f "$NOTIFY_SH" ]; then
    source "$NOTIFY_SH"
    if notify "$MSG" --topic daily; then
        log "发送完成: $DATE"
        printf '{"time":"%s","status":"ok","sent":true}\n' "$TS" > "$STATUS_FILE"
    else
        log "ERROR: 推送失败"
        printf '{"time":"%s","status":"send_failed","sent":false}\n' "$TS" > "$STATUS_FILE"
    fi
else
    # fallback: 直接调用 openclaw
    if openclaw message send --channel whatsapp --target "$PHONE" --message "$MSG" --json 2>>"$HOME/kb_evening.log" >/dev/null; then
        log "发送完成: $DATE"
        openclaw message send --channel discord --target "${DISCORD_CH_DAILY:-}" --message "$MSG" --json >/dev/null 2>&1 || true
        printf '{"time":"%s","status":"ok","sent":true}\n' "$TS" > "$STATUS_FILE"
    else
        log "ERROR: 消息发送失败，请检查 gateway。"
        printf '{"time":"%s","status":"send_failed","sent":false}\n' "$TS" > "$STATUS_FILE"
    fi
fi

# ── 日志轮转 ─────────────────────────────────────────────────────────────────
# 对超过 100KB 的 job 日志文件截断到最后 200 行（无压缩，无备份编号）
LOG_ROTATE_COUNT=0
LOG_ROTATE_LIMIT=$((100 * 1024))  # 100KB in bytes

_rotate_if_large() {
    local f="$1"
    # 展开 ~ 并跳过不存在的文件
    f="${f/#\~/$HOME}"
    [ -f "$f" ] || return 0
    local size
    size=$(wc -c < "$f" 2>/dev/null || echo 0)
    if [ "$size" -gt "$LOG_ROTATE_LIMIT" ]; then
        local tmp
        tmp=$(mktemp)
        tail -200 "$f" > "$tmp" && mv "$tmp" "$f"
        LOG_ROTATE_COUNT=$((LOG_ROTATE_COUNT + 1))
    fi
}

# 固定路径日志
for _log_file in \
    ~/conv_quality.log \
    ~/token_report.log \
    ~/kb_dedup.log \
    ~/kb_evening.log \
    ~/kb_embed.log \
    ~/kb_dream.log \
    ~/job_watchdog.log
do
    _rotate_if_large "$_log_file"
done

# ~/.openclaw/logs/jobs/*.log（通配）
for _log_file in "$HOME/.openclaw/logs/jobs/"*.log; do
    _rotate_if_large "$_log_file"
done

log "日志轮转: $LOG_ROTATE_COUNT 个文件已清理"
# ─────────────────────────────────────────────────────────────────────────────
