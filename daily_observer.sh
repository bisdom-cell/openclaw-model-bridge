#!/usr/bin/env bash
# daily_observer.sh — V37.9.84 Daily Self-Critique thin wrapper
#
# 每日 06:30 cron 调用 daily_observer.py, 推送到 Discord #daily (不推 WhatsApp 避免噪声).
# Observer 是给 operator 看的质量审计, 不是给终端用户的推送.
#
# V37.5.1 同款 env-var heredoc 模式 (禁 pipe+heredoc stdin 冲突).
# V37.9.63 同款 cron_monitor_fatal_handler helper (ERR trap 主动告警).

set -eEuo pipefail

# ── 路径自发现 ──
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
OBSERVER_PY=""
for candidate in "$HOME/daily_observer.py" "$SCRIPT_DIR/daily_observer.py"; do
    if [ -f "$candidate" ]; then
        OBSERVER_PY="$candidate"
        break
    fi
done
if [ -z "$OBSERVER_PY" ]; then
    echo "[observer] FATAL: daily_observer.py not found" >&2
    exit 1
fi

# ── 日志 ──
log() { echo "[observer] $(date '+%H:%M:%S') $*" >&2; }

# ── 通知 ──
NOTIFY_SH=""
for candidate in "$HOME/notify.sh" "$SCRIPT_DIR/notify.sh"; do
    if [ -f "$candidate" ]; then
        NOTIFY_SH="$candidate"
        break
    fi
done
NOTIFY_LOADED=false
if [ -n "$NOTIFY_SH" ]; then
    # shellcheck disable=SC1090
    source "$NOTIFY_SH" && NOTIFY_LOADED=true
fi

send_alert() {
    local msg="[SYSTEM_ALERT] daily_observer $1"
    log "ALERT: $msg"
    if $NOTIFY_LOADED; then
        notify "$msg" --topic alerts 2>/dev/null || true
    fi
}

# ── ERR trap (V37.9.63 helper 模式) ──
CRON_FATAL_LABEL="daily_observer"
CRON_FATAL_LOG="${HOME}/daily_observer.log"
CRON_FATAL_BASH_X="bash -x ~/daily_observer.sh"
CRON_FATAL_REASON="Daily Self-Critique observer crash"
HELPER_SH=""
for candidate in "$HOME/cron_monitor_fatal_handler.sh" "$SCRIPT_DIR/cron_monitor_fatal_handler.sh"; do
    if [ -f "$candidate" ]; then
        HELPER_SH="$candidate"
        break
    fi
done
if [ -n "$HELPER_SH" ]; then
    # shellcheck disable=SC1090
    source "$HELPER_SH"
    trap '_cron_monitor_fatal_handler $LINENO' ERR
else
    trap 'send_alert "FATAL exit=$? line=$LINENO"' ERR
fi

# ── 日期参数 (默认昨日) ──
TARGET_DATE="${1:-$(date -d 'yesterday' '+%Y%m%d' 2>/dev/null || date -v-1d '+%Y%m%d' 2>/dev/null || '')}"
if [ -z "$TARGET_DATE" ]; then
    log "WARN: cannot compute yesterday date, using default"
    TARGET_DATE=""
fi

DATE_ARG=""
if [ -n "$TARGET_DATE" ]; then
    DATE_ARG="--date $TARGET_DATE"
fi

# ── 运行 observer ──
log "starting daily_observer.py ${DATE_ARG:-'(default yesterday)'}"

OBSERVER_OUTPUT=""
# V37.5.1 env-var heredoc 模式: 不用 pipe, 用环境变量传参
OBSERVER_OUTPUT=$(python3 "$OBSERVER_PY" --json $DATE_ARG 2>&1) || {
    _rc=$?
    log "observer failed (exit=$_rc)"
    send_alert "observer.py 执行失败 (exit=$_rc)"
    echo "$OBSERVER_OUTPUT" >&2
    exit 1
}

# ── 解析结果 ──
STATUS=$(echo "$OBSERVER_OUTPUT" | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    print(d.get('status', 'unknown'))
except: print('parse_error')
" 2>/dev/null || echo "parse_error")

DISCORD_SUMMARY=$(echo "$OBSERVER_OUTPUT" | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    print(d.get('discord_summary', ''))
except: print('')
" 2>/dev/null || echo "")

OVERALL_SCORE=$(echo "$OBSERVER_OUTPUT" | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    s = d.get('overall_score')
    print(s if s is not None else 'N/A')
except: print('N/A')
" 2>/dev/null || echo "N/A")

log "status=$STATUS overall_score=$OVERALL_SCORE"

# ── 保存报告 (read-only: 只写到自己的目录) ──
CRITIQUE_DIR="${KB_DIR:-$HOME/.kb}/self_critique"
mkdir -p "$CRITIQUE_DIR"

REPORT_DATE="${TARGET_DATE:-$(date -d 'yesterday' '+%Y%m%d' 2>/dev/null || date -v-1d '+%Y%m%d' 2>/dev/null || date '+%Y%m%d')}"
REPORT_FILE="$CRITIQUE_DIR/daily_critique_${REPORT_DATE}.md"

# 从 JSON 提取完整 report markdown
python3 "$OBSERVER_PY" $DATE_ARG > "$REPORT_FILE" 2>/dev/null || {
    log "WARN: failed to write report file, continuing with push"
}

# ── 推送到 Discord only (不推 WhatsApp — observer 是 operator 工具) ──
if [ -n "$DISCORD_SUMMARY" ] && $NOTIFY_LOADED; then
    log "pushing to Discord #daily"
    notify "$DISCORD_SUMMARY" --topic daily 2>/dev/null || {
        log "WARN: Discord push failed (non-fatal)"
    }
fi

# ── status file ──
STATUS_FILE="${KB_DIR:-$HOME/.kb}/last_run_self_critique.json"
python3 -c "
import json, sys
d = {
    'time': '$(date -u '+%Y-%m-%dT%H:%M:%SZ')',
    'status': '$STATUS',
    'overall_score': '$OVERALL_SCORE',
    'report_file': '$REPORT_FILE',
    'version': 'V37.9.84'
}
json.dump(d, sys.stdout, ensure_ascii=False)
" > "$STATUS_FILE" 2>/dev/null || true

# ── 结果处理 ──
case "$STATUS" in
    ok)
        log "✅ critique complete (score=$OVERALL_SCORE)"
        ;;
    no_outputs)
        log "⚠️ no outputs found for target date (normal on quiet days)"
        ;;
    llm_failed)
        send_alert "LLM critique 失败 — 规则检测部分已完成, LLM 评分不可用"
        ;;
    *)
        log "status=$STATUS"
        ;;
esac

log "done"
