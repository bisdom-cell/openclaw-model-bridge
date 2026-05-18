#!/bin/bash
# OpenClaw 系统证据周报 v2.0 — V37.9.78
#
# Changelog from v1.1:
#   - 重定位为"系统证据周报"：单薄状态汇报 → 多维度证据汇总
#   - 新增段: 📊 SLO 趋势 (V36 slo_dashboard) / 🛡 安全评分 (V30.2 security_score) /
#            🏛 治理审计 (V37.1 .audit_metrics.jsonl) / 🛟 MOVESPEED 24h incidents (V37.9.27) /
#            🐦 X 监控质量 (V37.8.4 INV-X-001 zombies)
#   - 移除冗余段: 任务统计 (与 daily_ops_report 重叠) / Session 历史大小 (低价值)
#   - MR-8 single-source-of-truth: 全部走外部脚本不内嵌采集逻辑
#   - MR-11: 诊断日志写 stderr 不污染 REPORT 拼装
#   - 三层 FAIL-OPEN: 工具缺失/timeout/parse 失败 → 降级显示"暂无数据"不阻塞推送
#   - 保留 health_status.json 机器可读契约 (HEALTH_JSON_PATH)
#   - 保留双通道推送契约 (WhatsApp + Discord #日报), 优先 notify.sh 带重试+队列
#
# cron 环境 PATH 极简，必须显式声明（规则 #13）
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:$PATH"

# 配置：优先读取环境变量
PHONE="${OPENCLAW_PHONE:-+85200000000}"
OPENCLAW="$(command -v openclaw 2>/dev/null || echo /opt/homebrew/bin/openclaw)"
REPO_DIR="${OPENCLAW_REPO_DIR:-$HOME/openclaw-model-bridge}"
KB_DIR="${KB_BASE:-$HOME/.kb}"

# === helper: safe_call ===
# V37.9.78 三层 FAIL-OPEN: 工具不存在 / timeout / parse 失败 → 输出 fallback
# 用法: result=$(safe_call "cmd" "fallback_text")
# 契约: 不抛异常, 不阻塞 caller (周报型任务 fail-open 不 fail-fast)
safe_call() {
  local cmd="$1"
  local fallback="$2"
  local result
  result=$(timeout 30 bash -c "$cmd" 2>/dev/null) || result=""
  [ -z "$result" ] && result="$fallback"
  echo "$result"
}

# === 1. 服务健康（保留 v1.1 逻辑，简化 emoji）===
gw=$(lsof -ti :18789 >/dev/null 2>&1 && echo "🟢" || echo "🔴")
ad=$(lsof -ti :5001 >/dev/null 2>&1 && echo "🟢" || echo "🔴")
px=$(lsof -ti :5002 >/dev/null 2>&1 && echo "🟢" || echo "🔴")

# 模型 ID 检查（V27 现有逻辑，加 try/except 健壮性）
CURRENT_MODEL=$(curl -s --max-time 10 https://hkagentx.hkopenlab.com/v1/models \
  -H "Authorization: Bearer ${REMOTE_API_KEY}" 2>/dev/null \
  | python3 -c "
import json,sys
try:
  d=json.load(sys.stdin)
  models=[m['id'] for m in d['data'] if 'Qwen3' in m['id']]
  print(models[0][:30] if models else '')
except Exception:
  pass
" 2>/dev/null)

LOCAL_MODEL=$(python3 -c "
import json, os
try:
  with open(os.path.expanduser('~/.openclaw/openclaw.json')) as f: d=json.load(f)
  print(d['models']['providers']['qwen-local']['models'][0]['id'][:30])
except Exception:
  pass
" 2>/dev/null)

if [ -z "$CURRENT_MODEL" ] || [ -z "$LOCAL_MODEL" ]; then
  model_line="🤖 模型: ❓ (检查不可用)"
elif [ "$CURRENT_MODEL" = "$LOCAL_MODEL" ]; then
  model_line="🤖 模型: 🟢 ${CURRENT_MODEL}"
else
  model_line="🤖 模型: 🔴 远端 ${CURRENT_MODEL} ≠ 本地 ${LOCAL_MODEL}"
fi

# === 2. SLO 趋势 (V36 slo_dashboard.py) ===
# JSON 真实字段(V37.9.78 校对): current.p95_ms / current.success_pct / current.tool_success_pct
# trend_24h.avg_p95_ms / trend_24h.avg_success_pct / overall (HEALTHY/NO DATA)
SLO_BLOCK=$(safe_call "python3 '$REPO_DIR/slo_dashboard.py' --dashboard --json 2>/dev/null | python3 -c '
import json, sys
try:
  d = json.load(sys.stdin)
  overall = d.get(\"overall\", \"?\")
  hist_n = d.get(\"history_entries\", 0)
  cur = d.get(\"current\") or {}
  trend_24h = d.get(\"trend_24h\") or {}
  if overall == \"NO DATA\" or (not cur and hist_n == 0):
    print(\"📊 SLO: 暂无历史快照 (proxy 未活跃)\")
  else:
    p95 = cur.get(\"p95_ms\", \"?\")
    success = cur.get(\"success_pct\", \"?\")
    tool_succ = cur.get(\"tool_success_pct\", \"?\")
    avg_p95 = trend_24h.get(\"avg_p95_ms\", \"?\")
    print(f\"📊 SLO: p95={p95}ms (24h均 {avg_p95}ms) | 成功={success}% | 工具={tool_succ}% | {overall}\")
except Exception as e:
  print(f\"📊 SLO: 解析失败 ({type(e).__name__})\")
'" "📊 SLO: 工具不可用 (slo_dashboard.py 缺失)")

# === 3. 安全评分 (V30.2 security_score.py) ===
SEC_BLOCK=$(safe_call "python3 '$REPO_DIR/security_score.py' --json 2>/dev/null | python3 -c '
import json, sys
try:
  d = json.load(sys.stdin)
  total = d.get(\"total\", \"?\")
  mx = d.get(\"max\", 100)
  pct = d.get(\"percentage\", \"?\")
  weak = [dim[\"name\"] for dim in d.get(\"dimensions\", []) if dim.get(\"score\", mx) < dim.get(\"max\", mx)]
  weak_txt = (\" | 弱项: \" + \",\".join(weak[:3])) if weak else \"\"
  print(f\"🛡 安全评分: {total}/{mx} ({pct}%){weak_txt}\")
except Exception:
  print(\"🛡 安全评分: 解析失败\")
'" "🛡 安全评分: 工具不可用 (security_score.py 缺失)")

# === 4. 治理审计趋势 (V37.1 .audit_metrics.jsonl) ===
GOV_BLOCK=$(safe_call "python3 -c '
import json, os
metrics = \"$REPO_DIR/ontology/.audit_metrics.jsonl\"
if not os.path.exists(metrics):
  print(\"🏛 治理审计: 历史不可用 (.audit_metrics.jsonl 缺失)\")
else:
  with open(metrics) as f:
    lines = [json.loads(l) for l in f if l.strip()]
  if not lines:
    print(\"🏛 治理审计: 暂无 audit 历史\")
  else:
    cur = lines[-1]
    inv = cur.get(\"total_invariants\", \"?\")
    fail = cur.get(\"fail_count\", 0)
    err = cur.get(\"error_count\", 0)
    pass_count = cur.get(\"pass_count\", \"?\")
    wall = cur.get(\"wall_time_ms\", \"?\")
    status = \"🟢\" if fail == 0 and err == 0 else \"🔴\"
    print(f\"🏛 治理审计: {status} {inv} 不变式 / pass={pass_count} fail={fail} error={err} / {wall}ms\")
'" "🏛 治理审计: 工具不可用")

# === 5. MOVESPEED 24h incidents (V37.9.27 取证机制) ===
INCIDENT_FILE="$KB_DIR/movespeed_incidents.jsonl"
INCIDENT_MONITOR="$REPO_DIR/movespeed_incident_monitor.py"
INC_BLOCK="🛟 MOVESPEED 24h: ❓ (取证脚本不可用)"
if [ -f "$INCIDENT_MONITOR" ]; then
  if [ -f "$INCIDENT_FILE" ]; then
    inc_result=$(python3 "$INCIDENT_MONITOR" "$INCIDENT_FILE" "$(date +%s)" 5 2>/dev/null || echo "?|?|file_read_error")
    IFS='|' read -r inc_count inc_hit inc_callers <<< "$inc_result"
    if [ "$inc_count" = "0" ]; then
      INC_BLOCK="🛟 MOVESPEED 24h: 🟢 0 incidents"
    elif [ "$inc_hit" = "1" ]; then
      INC_BLOCK="🛟 MOVESPEED 24h: ⚠️ ${inc_count} incidents (≥5 阈值) callers: ${inc_callers}"
    else
      INC_BLOCK="🛟 MOVESPEED 24h: 🟡 ${inc_count} incidents (阈值未达)"
    fi
  else
    INC_BLOCK="🛟 MOVESPEED 24h: 🟢 0 incidents (无取证累积)"
  fi
fi

# === 6. X 监控质量 (V37.8.4 INV-X-001 zombies) ===
ZOMBIE_BLOCK="🐦 X 监控质量: ❓ (工作目录不存在)"
ZOMBIE_DIR="$HOME/.openclaw/jobs/finance_news/cache"
if [ -d "$ZOMBIE_DIR" ]; then
  zombie_count=$(find "$ZOMBIE_DIR" -name "zombies_*.txt" -mtime -7 2>/dev/null \
    | xargs cat 2>/dev/null \
    | sort -u \
    | grep -v '^$' \
    | wc -l \
    | tr -d ' ' || echo 0)
  if [ "${zombie_count:-0}" -eq 0 ]; then
    ZOMBIE_BLOCK="🐦 X 监控质量: 🟢 0 僵尸账号 (近 7 天)"
  else
    ZOMBIE_BLOCK="🐦 X 监控质量: ⚠️ ${zombie_count} 僵尸嫌疑 (近 7 天累积去重)"
  fi
fi

# === 7. 知识库（保留 + 简化）===
KB_TOTAL=$(find "$KB_DIR/notes/" -name "*.md" 2>/dev/null | wc -l | tr -d ' ' || echo 0)
KB_WEEK=$(find "$KB_DIR/notes/" -name "*.md" -mtime -7 2>/dev/null | wc -l | tr -d ' ' || echo 0)

# === 8. 外挂 SSD ===
if [ -d "/Volumes/MOVESPEED" ]; then
  ssd_status="🟢 在线"
else
  ssd_status="🟡 未挂载"
fi

# === 组装报告 ===
DATE=$(date '+%Y-%m-%d')
REPORT="📊 OpenClaw 系统证据周报 ${DATE}

🖥 服务: Gateway ${gw} | Adapter ${ad} | Proxy ${px}
${model_line}

${SLO_BLOCK}
${SEC_BLOCK}
${GOV_BLOCK}

🛟 韧性证据:
${INC_BLOCK}
${ZOMBIE_BLOCK}

📚 知识库: 本周 +${KB_WEEK} / 共 ${KB_TOTAL} 条
💾 外挂 SSD: ${ssd_status}

✅ 周报完毕 (V37.9.78)"

echo "$REPORT"

# === V27: 输出机器可读 JSON（保留契约）===
HEALTH_JSON="${HEALTH_JSON_PATH:-$HOME/health_status.json}"
python3 << JSONEOF > "$HEALTH_JSON" 2>/dev/null || true
import json, datetime
data = {
  "timestamp": datetime.datetime.now().isoformat(),
  "version": "v37.9.78",
  "services": {
    "gateway":  {"port": 18789, "status": "ok" if "$gw" == "🟢" else "down"},
    "adapter":  {"port": 5001,  "status": "ok" if "$ad" == "🟢" else "down"},
    "proxy":    {"port": 5002,  "status": "ok" if "$px" == "🟢" else "down"},
  },
  "model": {
    "remote": "$CURRENT_MODEL",
    "local": "$LOCAL_MODEL",
    "match": "$CURRENT_MODEL" == "$LOCAL_MODEL",
  },
  "kb": {"new_this_week": int("$KB_WEEK" or "0"), "total": int("$KB_TOTAL" or "0")},
  "ssd": "$ssd_status",
}
print(json.dumps(data, indent=2, ensure_ascii=False))
JSONEOF

# === V37.9.78 推送改造: 优先 notify.sh (双通道独立 + 重试 + 队列), fallback openclaw 直推 ===
PUSHED=false
if [ -f "$HOME/notify.sh" ]; then
  # shellcheck source=/dev/null
  source "$HOME/notify.sh" 2>/dev/null || true
  if type notify >/dev/null 2>&1; then
    if notify "$REPORT" --topic daily 2>>"$HOME/health_check.log"; then
      PUSHED=true
      echo "[health] notify.sh 推送成功 (--topic daily)" >&2
    fi
  fi
fi

if [ "$PUSHED" = "false" ]; then
  # Fallback: openclaw 直推 (双通道独立, V37.8.13 教训告警链不依赖失效主体自身)
  echo "[health] notify.sh 不可用, fallback openclaw 直推" >&2
  $OPENCLAW message send --channel discord --target "${DISCORD_CH_DAILY:-}" --message "$REPORT" --json >/dev/null 2>&1 || true
  $OPENCLAW message send --channel whatsapp --target "$PHONE" --message "$REPORT" --json 2>>"$HOME/health_check.log" || true
fi
