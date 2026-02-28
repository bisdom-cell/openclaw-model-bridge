#!/bin/bash
# OpenClaw 每周健康检查脚本 v1.0

PHONE="+85256190187"
OPENCLAW="/opt/homebrew/bin/openclaw"

# === 服务状态 ===
gw=$(lsof -ti :18789 >/dev/null 2>&1 && echo "🟢 正常" || echo "🔴 异常")
ad=$(lsof -ti :5001 >/dev/null 2>&1 && echo "🟢 正常" || echo "🔴 异常")
px=$(lsof -ti :5002 >/dev/null 2>&1 && echo "🟢 正常" || echo "🔴 异常")

# === 模型ID检查 ===
CURRENT_MODEL=$(curl -s --max-time 10 https://hkagentx.hkopenlab.com/v1/models \
  -H "Authorization: Bearer ${REMOTE_API_KEY}" \
  | python3 -c "import json,sys; d=json.load(sys.stdin); models=[m['id'] for m in d['data'] if 'Qwen3' in m['id']]; print(models[0][:30] if models else 'NOT_FOUND')" 2>/dev/null)
LOCAL_MODEL=$(python3 -c "
import json
with open('/Users/bisdom/.openclaw/openclaw.json') as f: d=json.load(f)
print(d['models']['providers']['qwen-local']['models'][0]['id'][:30])
" 2>/dev/null)

if [ "$CURRENT_MODEL" = "$LOCAL_MODEL" ]; then
  model_status="🟢 未变更 (${CURRENT_MODEL})"
else
  model_status="🔴 已变更！远端:${CURRENT_MODEL} 本地:${LOCAL_MODEL}"
fi

# === 任务统计（过去7天）===
TASK_STATS=$(python3 << 'PYEOF'
import json, time, subprocess, sys

try:
    with open('/Users/bisdom/.openclaw/cron/jobs.json') as f:
        jobs = json.load(f).get('jobs', [])
except:
    print("无法读取任务配置")
    sys.exit(0)

lines = []
for j in jobs:
    if not j.get('enabled'): continue
    name = j['name']
    jid = j['id']
    try:
        result = subprocess.run(
            ['/opt/homebrew/bin/openclaw', 'cron', 'runs', '--id', jid, '--limit', '14'],
            capture_output=True, text=True, timeout=15
        )
        data = json.loads(result.stdout)
        entries = data.get('entries', [])
        total = len(entries)
        success = sum(1 for e in entries if e.get('status') in ('ok', 'success'))
        lines.append(f"  {name}：{success}/{total} 成功")
    except:
        lines.append(f"  {name}：无法获取记录")

print('\n'.join(lines))
PYEOF
)

# === 知识库统计 ===
KB_COUNT=$(find ~/.kb/notes/ -name "*.md" -newer ~/.kb/notes/.last_check 2>/dev/null | wc -l | tr -d ' ')
TOTAL_KB=$(find ~/.kb/notes/ -name "*.md" 2>/dev/null | wc -l | tr -d ' ')
touch ~/.kb/notes/.last_check 2>/dev/null

# === Session历史大小 ===
SESSION_SIZE=$(du -sh ~/.openclaw/agents/main/sessions/ 2>/dev/null | cut -f1 || echo "0")

# === 外挂SSD状态 ===
if [ -d "/Volumes/MOVESPEED" ]; then
  ssd_status="🟢 在线"
else
  ssd_status="🟡 未挂载"
fi

# === 组装报告 ===
DATE=$(date '+%Y-%m-%d')
REPORT="📊 OpenClaw 周报 ${DATE}

🖥 服务状态：
  Gateway：${gw}
  Adapter：${ad}
  Proxy：${px}

🤖 模型ID：${model_status}

📋 任务统计（近7天）：
${TASK_STATS}

🗂 知识库：本周新增 ${KB_COUNT} 条 / 共 ${TOTAL_KB} 条
💾 外挂SSD：${ssd_status}
📁 Session历史：${SESSION_SIZE}

✅ 周报完毕"

echo "$REPORT"

# === 推送到WhatsApp ===
$OPENCLAW message send --channel whatsapp -t "$PHONE" -m "$REPORT"
