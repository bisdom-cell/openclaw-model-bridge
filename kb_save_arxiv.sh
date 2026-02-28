#!/bin/bash
# 动态查找arxiv监控任务ID，无需硬编码，任务重建后自动适配
ARXIV_JOB_ID=$(python3 -c "
import json
with open('/Users/bisdom/.openclaw/cron/jobs.json') as f: d=json.load(f)
for j in d.get('jobs',[]):
    if 'monitor-arxiv' in j['name']:
        print(j['id'])
        break
")

if [ -z "$ARXIV_JOB_ID" ]; then
  echo "❌ 找不到monitor-arxiv任务，退出"
  exit 1
fi

SUMMARY=$(/opt/homebrew/bin/openclaw cron runs --id "$ARXIV_JOB_ID" --limit 1 2>/dev/null \
  | python3 -c "
import json,sys
data=json.load(sys.stdin)
entries=data.get('entries',[])
if not entries: sys.exit(0)
s=entries[0].get('summary','')
if not s or '暂无' in s: sys.exit(0)
print(s)
")

if [ -z "$SUMMARY" ]; then
  echo "无新内容，跳过KB写入"
  exit 0
fi

DATE=$(date '+%Y-%m-%d %H:%M')
CONTENT="# ArXiv AI论文监控 ${DATE}

${SUMMARY}"

bash /Users/bisdom/kb_write.sh "$CONTENT" "arxiv-ai-models" "note"
echo "✅ KB写入完成 ${DATE}"

# 同步备份到外挂SSD
if [ -d "/Volumes/MOVESPEED" ]; then
  rsync -a --delete ~/.kb/ /Volumes/MOVESPEED/KB/
  echo "✅ 已同步备份到 /Volumes/MOVESPEED/KB/"
else
  echo "⚠️ 外挂SSD未挂载，跳过备份"
fi
