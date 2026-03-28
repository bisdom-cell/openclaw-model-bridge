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

# ── 5. 刷新 workspace CLAUDE.md 中的状态快照（PA 实时感知）─────────
# kb_inject.sh 每天只运行一次，但 status.json 每小时更新
# 这里用 sed 替换快照区域，确保 PA 拿到的状态不超过 1 小时
WORKSPACE_MD="$HOME/.openclaw/workspace/.openclaw/CLAUDE.md"
if [ -f "$WORKSPACE_MD" ]; then
    NEW_SNAPSHOT=$(python3 "$STATUS_UPDATE" --read --human 2>/dev/null)
    if [ -n "$NEW_SNAPSHOT" ]; then
        python3 - "$WORKSPACE_MD" "$NEW_SNAPSHOT" << 'PYEOF'
import sys, re

ws_file, snapshot = sys.argv[1], sys.argv[2]
with open(ws_file) as f:
    content = f.read()

# 替换快照区域：从 "## 三方共享意识（实时快照）" 到下一个 "## " 标题
pattern = r'(## 三方共享意识（实时快照）\n).*?(\n## )'
header = "## 三方共享意识（实时快照）\n"
header += "以下是当前系统状态，每小时自动刷新。\n"
header += "回答用户关于项目进展、系统状态、优先级等问题时，直接参考此快照。\n"
header += "如需最新数据，用 exec 工具执行：`python3 ~/status_update.py --read --human`\n\n"
header += snapshot + "\n"

new_content = re.sub(pattern, header + r'\2', content, count=1, flags=re.DOTALL)
if new_content != content:
    tmp = ws_file + '.tmp'
    with open(tmp, 'w') as f:
        f.write(new_content)
    import os; os.replace(tmp, ws_file)
    print(f"[status_refresh] workspace CLAUDE.md snapshot updated", file=sys.stderr)
PYEOF
    fi
fi

# ── 6. 刷新 SOUL.md 中的项目状态（PA 最高优先级上下文）───────────
SOUL_MD="$HOME/.openclaw/workspace/SOUL.md"
if [ -f "$SOUL_MD" ]; then
    # 从 status.json 提取关键信息，生成 SOUL.md 状态区段
    SOUL_STATUS=$(python3 - "$HOME/.kb/status.json" << 'PYEOF'
import json, sys
try:
    with open(sys.argv[1]) as f:
        s = json.load(f)
except Exception:
    sys.exit(0)

lines = []
lines.append(f"**本周焦点**：{s.get('focus', '未设定')}")
lines.append("")
lines.append("**进行中的任务：**")
for p in s.get("priorities", []):
    if p.get("status") == "active":
        note = f"（{p['note']}）" if p.get("note") else ""
        lines.append(f"- {p['task']}{note}")

backlog = [p for p in s.get("priorities", []) if p.get("status") == "backlog"]
if backlog:
    lines.append("")
    lines.append("**待规划：** " + "、".join(p["task"] for p in backlog))

lines.append("")
lines.append("**最近完成：**")
for c in s.get("recent_changes", [])[:3]:
    lines.append(f"- {c.get('date', '')}: {c.get('what', '')}")

rules = s.get("operating_rules", [])
if rules:
    lines.append("")
    lines.append("**当前约束：**")
    for r in rules:
        lines.append(f"- {r}")

h = s.get("health", {})
svc = h.get("services", "unknown")
model = h.get("model_id", "unknown")
kb = h.get("kb_stats", "unknown")
jobs = h.get("stale_jobs", "unknown")
job_str = "全部Job运行正常" if jobs == "all_ok" else f"过期Job: {jobs}"
lines.append("")
lines.append(f"**系统健康：** 服务{'正常' if svc == 'ok' else svc} | 模型: {model} | KB: {kb} | {job_str}")

prefs = s.get("preferences", [])
if prefs:
    lines.append("")
    lines.append("**用户偏好：**")
    for p in prefs:
        lines.append(f"- {p}")

print("\n".join(lines))
PYEOF
    )

    if [ -n "$SOUL_STATUS" ]; then
        python3 - "$SOUL_MD" "$SOUL_STATUS" "$HOME/.kb/status.json" << 'PYEOF'
import sys, re, json

soul_file, status_block, status_json_path = sys.argv[1], sys.argv[2], sys.argv[3]
with open(soul_file) as f:
    content = f.read()

# 1. 替换项目状态区段
pattern = r'(## 当前项目状态（每小时自动刷新）\n).*?(> 用户问项目)'
header = "## 当前项目状态（每小时自动刷新）\n\n"
header += status_block + "\n\n"
new_content = re.sub(pattern, header + r'\2', content, count=1, flags=re.DOTALL)

# 2. 替换用户偏好区段（SOUL.md 顶部，紧跟身份定义之后）
try:
    with open(status_json_path) as f:
        prefs = json.load(f).get("preferences", [])
except Exception:
    prefs = []

if prefs:
    pref_lines = "\n".join(f"- {p}" for p in prefs)
else:
    pref_lines = "（暂无，系统每天自动分析行为数据生成偏好）"

pref_pattern = r'(## 用户偏好（必须遵守）\n).*?(> 以上偏好)'
pref_header = "## 用户偏好（必须遵守）\n\n"
pref_header += pref_lines + "\n\n"
new_content = re.sub(pref_pattern, pref_header + r'\2', new_content, count=1, flags=re.DOTALL)

if new_content != content:
    tmp = soul_file + '.tmp'
    with open(tmp, 'w') as f:
        f.write(new_content)
    import os; os.replace(tmp, soul_file)
    print(f"[status_refresh] SOUL.md status updated", file=sys.stderr)
PYEOF
    fi
fi

# ── 7. 同步 status.json + SOUL.md 到 git 仓库（三方宪法跨环境锚点）
# Claude Code dev 通过 git 读取此文件，因此每次刷新后推送到仓库
REPO_DIR="$HOME/openclaw-model-bridge"
REPO_STATUS="$REPO_DIR/status.json"
if [ -d "$REPO_DIR/.git" ] && [ -f "$HOME/.kb/status.json" ]; then
    cp "$HOME/.kb/status.json" "$REPO_STATUS"
    cd "$REPO_DIR"
    # 同步 SOUL.md 到仓库（仅当运行时版本比仓库版本新时）
    SOUL_RT="$HOME/.openclaw/workspace/SOUL.md"
    SOUL_REPO="$REPO_DIR/SOUL.md"
    if [ -f "$SOUL_RT" ]; then
        cp "$SOUL_RT" "$SOUL_REPO"
    fi

    # 仅在内容有变化时才提交（避免空commit噪音）
    if ! git diff --quiet "$REPO_STATUS" 2>/dev/null || ! git diff --quiet "$SOUL_REPO" 2>/dev/null; then
        git add status.json SOUL.md
        git commit -m "auto: sync status.json + SOUL.md from kb_status_refresh" --no-gpg-sign 2>/dev/null || true
        git push origin main 2>/dev/null || echo "[$TS] WARN: push failed (will retry next hour)"
    fi
fi
