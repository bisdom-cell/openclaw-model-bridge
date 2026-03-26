#!/bin/bash
# kb_status_refresh.sh — 每小时自动刷新 status.json 系统健康字段
# 三方宪法要求实时同步，但 status.json 之前仅在 Claude Code 开/收工 + 部署时更新
# 本脚本补齐"自动感知"能力：每小时聚合系统状态写入 status.json
# cron: 0 * * * *（每小时整点）
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:$PATH"

STATUS_UPDATE="${STATUS_UPDATE:-$HOME/status_update.py}"
TS="$(TZ=Asia/Hong_Kong date '+%Y-%m-%d %H:%M')"

# ── 1. 三层服务连通性 ─────────────────────────────────────────────
GW=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 http://localhost:18789/health 2>/dev/null)
PX=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 http://localhost:5002/health 2>/dev/null)
AD=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 http://localhost:5001/health 2>/dev/null)

if [ "$GW" = "200" ] && [ "$PX" = "200" ] && [ "$AD" = "200" ]; then
    SVC_STATUS="ok"
else
    SVC_STATUS="degraded (GW:${GW} PX:${PX} AD:${AD})"
fi

python3 "$STATUS_UPDATE" --set health.services "$SVC_STATUS" --by cron 2>/dev/null || true

# ── 2. 模型 ID（从 adapter /health 获取）─────────────────────────
MODEL_ID=$(curl -s --max-time 5 http://localhost:5001/health 2>/dev/null | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    print(d.get('model_id', d.get('model', '')))
except Exception:
    print('')
" 2>/dev/null)

if [ -n "$MODEL_ID" ]; then
    python3 "$STATUS_UPDATE" --set health.model_id "$MODEL_ID" --by cron 2>/dev/null || true
fi

# ── 3. KB 统计快照 ────────────────────────────────────────────────
KB_DIR="$HOME/.kb"
if [ -d "$KB_DIR" ]; then
    KB_STATS=$(python3 -c "
import json, os, glob
from datetime import datetime, timedelta
kb = os.path.expanduser('~/.kb')
idx = os.path.join(kb, 'index.json')
total = 0
today = 0
try:
    with open(idx) as f:
        entries = json.load(f).get('entries', [])
    total = len(entries)
    cutoff = (datetime.now() - timedelta(days=1)).strftime('%Y%m%d')
    today = sum(1 for e in entries if e.get('date', '') >= cutoff)
except Exception:
    pass
# sources 文件大小
src_size = 0
for f in glob.glob(os.path.join(kb, 'sources', '*.md')):
    src_size += os.path.getsize(f)
print(f'{total} notes, {today} today, {src_size // 1024}KB sources')
" 2>/dev/null)
    if [ -n "$KB_STATS" ]; then
        python3 "$STATUS_UPDATE" --set health.kb_stats "$KB_STATS" --by cron 2>/dev/null || true
    fi
fi

# ── 4. 最近 job 执行状态汇总 ──────────────────────────────────────
STALE_JOBS=$(python3 -c "
import json, os, time
jobs = {
    'arxiv': os.path.expanduser('~/.openclaw/jobs/arxiv_monitor/cache/last_run.json'),
    'hn': os.path.expanduser('~/.openclaw/jobs/hn_watcher/cache/last_run.json'),
    'freight': os.path.expanduser('~/.openclaw/jobs/freight_watcher/cache/last_run.json'),
    'discussions': os.path.expanduser('~/.openclaw/jobs/openclaw_official/cache/last_run_discussions.json'),
}
stale = []
now = time.time()
for name, path in jobs.items():
    try:
        with open(path) as f:
            d = json.load(f)
        t = d.get('time', '')
        from datetime import datetime, timedelta, timezone
        dt = datetime.strptime(t, '%Y-%m-%d %H:%M:%S')
        dt_utc = dt - timedelta(hours=8)
        epoch = int(dt_utc.replace(tzinfo=timezone.utc).timestamp())
        if now - epoch > 25200:  # 7h
            stale.append(name)
    except Exception:
        stale.append(name)
print(','.join(stale) if stale else 'all_ok')
" 2>/dev/null)

python3 "$STATUS_UPDATE" --set health.stale_jobs "${STALE_JOBS:-unknown}" --by cron 2>/dev/null || true
python3 "$STATUS_UPDATE" --set health.last_refresh "$TS" --by cron 2>/dev/null || true

echo "[$TS] kb_status_refresh: services=$SVC_STATUS stale_jobs=${STALE_JOBS:-unknown}"
