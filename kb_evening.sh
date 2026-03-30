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

if openclaw message send --target "$PHONE" --message "$MSG" --json 2>>"$HOME/kb_evening.log" >/dev/null; then
    log "发送完成: $DATE"
    printf '{"time":"%s","status":"ok","sent":true}\n' "$TS" > "$STATUS_FILE"
else
    log "ERROR: 消息发送失败，请检查 gateway。"
    printf '{"time":"%s","status":"send_failed","sent":false}\n' "$TS" > "$STATUS_FILE"
fi
