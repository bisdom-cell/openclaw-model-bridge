#!/bin/bash
set -euo pipefail
# 动态查找arxiv监控任务ID，无需硬编码，任务重建后自动适配
OPENCLAW_CFG="${OPENCLAW_CFG:-$HOME/.openclaw}"
KB_WRITE_SCRIPT="${KB_WRITE_SCRIPT:-$(dirname "$0")/kb_write.sh}"
OPENCLAW="$(command -v openclaw 2>/dev/null || echo /opt/homebrew/bin/openclaw)"

ARXIV_JOB_ID=$(python3 - "$OPENCLAW_CFG/cron/jobs.json" << 'PYEOF'
import json, sys
try:
    with open(sys.argv[1]) as f:
        d = json.load(f)
    for j in d.get('jobs', []):
        if 'monitor-arxiv' in j.get('name', ''):
            print(j['id'])
            break
except (OSError, json.JSONDecodeError) as e:
    print(f"[kb_save_arxiv] ERROR reading jobs.json: {e}", file=sys.stderr)
PYEOF
)

if [ -z "$ARXIV_JOB_ID" ]; then
  echo "[kb_save_arxiv] ERROR: 找不到monitor-arxiv任务，退出"
  exit 1
fi

SUMMARY=$("$OPENCLAW" cron runs --id "$ARXIV_JOB_ID" --limit 1 2>/dev/null \
  | python3 -c "
import json, sys
try:
    data = json.load(sys.stdin)
    entries = data.get('entries', [])
    if not entries:
        sys.exit(0)
    s = entries[0].get('summary', '')
    if not s or '暂无' in s:
        sys.exit(0)
    print(s)
except (json.JSONDecodeError, KeyError) as e:
    print(f'[kb_save_arxiv] WARN: 解析runs结果失败: {e}', file=sys.stderr)
" || true)

if [ -z "$SUMMARY" ]; then
  echo "[kb_save_arxiv] 无新内容，跳过KB写入"
  exit 0
fi

DATE=$(date '+%Y-%m-%d %H:%M')
CONTENT="# ArXiv AI论文监控 ${DATE}

${SUMMARY}"

bash "$KB_WRITE_SCRIPT" "$CONTENT" "arxiv-ai-models" "note"
echo "[kb_save_arxiv] KB写入完成 ${DATE}"

# 同步备份到外挂SSD
if [ -d "/Volumes/MOVESPEED" ]; then
  rsync -a --delete ~/.kb/ /Volumes/MOVESPEED/KB/
  echo "[kb_save_arxiv] 已同步备份到 /Volumes/MOVESPEED/KB/"
else
  echo "[kb_save_arxiv] 外挂SSD未挂载，跳过备份"
fi
