#!/usr/bin/env bash
# kb_radar.sh — V37.9.99 Opportunity Radar Stage 5 每日机会点雷达 (06:00 HKT cron)
#
# 设计文档 docs/opportunity_radar_design.md 6.1 节兑现.
# 消费三件套输出 (#1 跨源共振 / #2 项目高对齐 / #3 趋势加速) → 规则化件套交集
# 分 红/黄/蓝 三档机会点 → 早晨双通道推送.
#
# 契约 (镜像 kb_deep_dive.sh V37.9.63):
#   - env-var heredoc 模式 (禁 `echo | python3 -` pipe+heredoc stdin 冲突, V37.5.1 血案)
#   - set -eEuo pipefail + trap ERR fatal handler (V37.9.61 silent abort 防御)
#   - 仅 red+yellow > 0 (有可操作机会点) 才推送, 否则只归档 KB (避免每早噪声, 原则 #32)
#   - radar 是信息性非故障监控: 无信号 = no_data 不报 [SYSTEM_ALERT] (允许明日再试)
#   - collector_failed (部署/代码异常) → [SYSTEM_ALERT] + exit 1 (真故障 fail-fast)
set -eEuo pipefail

export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:$PATH"

TS="$(date '+%Y-%m-%d %H:%M:%S')"
TODAY="$(date '+%Y-%m-%d')"
KB_DIR="${KB_DIR:-$HOME/.kb}"
RADAR_DIR="$KB_DIR/radar"
STATUS_FILE="$KB_DIR/last_run_radar.json"
BRIEFING_FILE="$RADAR_DIR/daily_briefing_${TODAY}.md"
PHONE="${OPENCLAW_PHONE:-}"
mkdir -p "$RADAR_DIR"

log() { echo "[$TS] kb_radar: $1" >&2; }

# ── collector 3-path fallback (dev / Mac Mini $HOME / repo) ──
COLLECTOR="$(cd "$(dirname "$0")" && pwd)/kb_radar_collect.py"
[ -f "$COLLECTOR" ] || COLLECTOR="$HOME/openclaw-model-bridge/kb_radar_collect.py"
[ -f "$COLLECTOR" ] || COLLECTOR="$HOME/kb_radar_collect.py"

# ── notify.sh (统一双通道 + [SYSTEM_ALERT] 通道) ──
NOTIFY_SH=""
for candidate in "$HOME/openclaw-model-bridge/notify.sh" "$HOME/notify.sh"; do
    [ -f "$candidate" ] && { NOTIFY_SH="$candidate"; break; }
done
if [ -n "$NOTIFY_SH" ]; then
    # shellcheck disable=SC1090
    source "$NOTIFY_SH" || true
fi

send_alert() {
    local reason="$1"
    local msg="[SYSTEM_ALERT] kb_radar 失败
时间: $TS
原因: $reason
降级处理: 今日未推送机会点雷达
建议: 检查 kb_radar_collect.py 部署 + 三件套 radar 数据 + 查看 $STATUS_FILE"
    if command -v notify >/dev/null 2>&1; then
        notify "$msg" --topic alerts >/dev/null 2>&1 || true
    elif command -v openclaw >/dev/null 2>&1 && [ -n "$PHONE" ]; then
        openclaw message send --channel whatsapp --target "$PHONE" --message "$msg" --json >/dev/null 2>&1 || true
    else
        echo "$msg" >> "$HOME/.openclaw_alerts.log" 2>/dev/null || true
    fi
}

# ── trap ERR: 任何未预期 abort 主动推 [SYSTEM_ALERT] (V37.9.61 silent abort 防御) ──
# 优先用 V37.9.63 公共 helper, 缺失则 fallback inline send_alert
CRON_FATAL_HELPER=""
for h in "$HOME/openclaw-model-bridge/cron_monitor_fatal_handler.sh" "$HOME/cron_monitor_fatal_handler.sh"; do
    [ -f "$h" ] && { CRON_FATAL_HELPER="$h"; break; }
done
if [ -n "$CRON_FATAL_HELPER" ]; then
    CRON_FATAL_LABEL="kb_radar"
    CRON_FATAL_LOG="$STATUS_FILE"
    CRON_FATAL_BASH_X=""
    CRON_FATAL_REASON="kb_radar.sh 未预期 abort (silent abort 防御)"
    # shellcheck disable=SC1090
    source "$CRON_FATAL_HELPER" || true
    trap '_cron_monitor_fatal_handler $LINENO' ERR
else
    trap 'send_alert "未预期 abort at line $LINENO (silent abort 防御)"' ERR
fi

write_status() {
    local status="$1" red="$2" yellow="$3" blue_a="$4" blue_d="$5"
    status="$status" red="$red" yellow="$yellow" blue_a="$blue_a" blue_d="$blue_d" \
        ts="$TS" sf="$STATUS_FILE" python3 << 'PYEOF'
import json, os
out = {
    "status": os.environ["status"],
    "last_run": os.environ["ts"],
    "red_count": int(os.environ["red"]),
    "yellow_count": int(os.environ["yellow"]),
    "blue_accel_count": int(os.environ["blue_a"]),
    "blue_decel_count": int(os.environ["blue_d"]),
    "job": "kb_radar",
}
tmp = os.environ["sf"] + ".tmp"
with open(tmp, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=2)
os.replace(tmp, os.environ["sf"])
PYEOF
}

# ── 0. 部署完整性 ──
if [ ! -f "$COLLECTOR" ]; then
    log "FATAL: kb_radar_collect.py 不存在 (部署文件缺失)"
    send_alert "kb_radar_collect.py 不存在 (部署文件缺失)"
    write_status "collector_failed" 0 0 0 0
    exit 1
fi

# ── 1. 运行 collector ──
OUT=$(KB_DIR="$KB_DIR" python3 "$COLLECTOR" --json 2>&1) || {
    EC=$?
    log "FATAL: collector exited $EC"
    send_alert "collector exited $EC: $(echo "$OUT" | head -3 | tr '\n' ' ')"
    write_status "collector_failed" 0 0 0 0
    exit 1
}

STATUS=$(echo "$OUT" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("status","parse_error"))' 2>/dev/null || echo "parse_error")
if [ "$STATUS" = "parse_error" ] || [ "$STATUS" = "collector_failed" ]; then
    log "FATAL: collector 输出非法/失败 status=$STATUS"
    send_alert "collector 输出 status=$STATUS: $(echo "$OUT" | head -3 | tr '\n' ' ')"
    write_status "collector_failed" 0 0 0 0
    exit 1
fi

# ── 2. 解析计数 + 内容 ──
RED=$(echo "$OUT" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("red_count",0))')
YELLOW=$(echo "$OUT" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("yellow_count",0))')
BLUE_A=$(echo "$OUT" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("blue_accel_count",0))')
BLUE_D=$(echo "$OUT" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("blue_decel_count",0))')

# briefing markdown 写 KB 归档 (始终归档, 无论是否推送)
echo "$OUT" | python3 -c 'import json,sys; sys.stdout.write(json.load(sys.stdin).get("briefing_markdown",""))' > "$BRIEFING_FILE" 2>/dev/null || true
log "briefing 已归档: $BRIEFING_FILE (red=$RED yellow=$YELLOW blue_accel=$BLUE_A blue_decel=$BLUE_D)"

# ── 3. 推送决策: 仅 red+yellow > 0 (有可操作机会点) 才推送, 避免每早噪声 ──
ACTIONABLE=$((RED + YELLOW))
if [ "$ACTIONABLE" -gt 0 ]; then
    WA_MSG=$(echo "$OUT" | python3 -c 'import json,sys; sys.stdout.write(json.load(sys.stdin).get("wa_message",""))')
    DC_MSG=$(echo "$OUT" | python3 -c 'import json,sys; sys.stdout.write(json.load(sys.stdin).get("discord_message",""))')
    SENT=0
    if command -v notify >/dev/null 2>&1; then
        # notify.sh 统一双通道 (WhatsApp + Discord #daily)
        notify "$WA_MSG" --topic daily >/dev/null 2>&1 && SENT=1 || log "WARN: notify --topic daily 推送失败"
    elif command -v openclaw >/dev/null 2>&1 && [ -n "$PHONE" ]; then
        openclaw message send --channel whatsapp --target "$PHONE" --message "$WA_MSG" --json >/dev/null 2>&1 && SENT=1 || log "WARN: openclaw WhatsApp 推送失败"
        [ -n "${DISCORD_CH_DAILY:-}" ] && openclaw message send --channel discord --channel-id "$DISCORD_CH_DAILY" --message "$DC_MSG" --json >/dev/null 2>&1 || true
    else
        log "WARN: 无 notify/openclaw, 跳过推送 (briefing 已归档)"
    fi
    log "推送完成 (actionable=$ACTIONABLE, sent=$SENT)"
    write_status "ok" "$RED" "$YELLOW" "$BLUE_A" "$BLUE_D"
else
    # 无可操作机会点 (仅趋势观察或全空) → 只归档不推送 (原则 #32 低噪声)
    log "无可操作机会点 (red+yellow=0), 只归档不推送"
    write_status "no_actionable" "$RED" "$YELLOW" "$BLUE_A" "$BLUE_D"
fi

# ── 4. rsync 备份 (MOVESPEED, V37.9.27 helper 错峰+retry+fail-loud) ──
RSYNC_HELPER=""
for h in "$HOME/openclaw-model-bridge/movespeed_rsync_helper.sh" "$HOME/movespeed_rsync_helper.sh"; do
    [ -f "$h" ] && { RSYNC_HELPER="$h"; break; }
done
if [ -n "$RSYNC_HELPER" ] && [ -d /Volumes/MOVESPEED/KB ]; then
    bash "$RSYNC_HELPER" "$0" -- -a "$RADAR_DIR/" /Volumes/MOVESPEED/KB/radar/ || true
fi

log "完成"
