#!/bin/bash
# cron 环境 PATH 极简，必须显式声明
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:$PATH"
# 加载环境变量（cron 环境中 OPENCLAW_PHONE/DISCORD_CH_* 等必须从 profile 获取）
source "$HOME/.bash_profile" 2>/dev/null || source "$HOME/.env_shared" 2>/dev/null || true
# 运维日报 — 合并 conv_quality + token_report 为一条推送
# 每天 08:15 由 system crontab 触发，报告前一天数据
# 替代原来分开推送的两个脚本（每天 2 条 → 1 条）
# V37.9.60 MR-19 err_trap_handler 契约: -E (errtrace) 让 function 内 fail 也触发 ERR trap
set -eEo pipefail

TS="$(TZ=Asia/Hong_Kong date '+%Y-%m-%d %H:%M:%S')"
DATE="$(TZ=Asia/Hong_Kong date '+%Y-%m-%d')"
LOG_PREFIX="[daily_ops_report]"

log() { echo "[$TS] $LOG_PREFIX $1"; }

# ════════════════════════════════════════════════════════════════════
# V37.9.60 MR-19 ERR trap: silent abort 变 loud
# ════════════════════════════════════════════════════════════════════
# 血案模式: daily_ops_report 每日 08:15 cron 跑 set -eo + 无 trap ERR.
# 若 python3 进程 OOM 杀手 / mkdir fail / curl 异常等 set -e abort 整脚本死,
# 用户当日 ops 报告无声丢失. V37.9.60 复用 V37.9.58-hotfix3 watchdog 同款三层 FAIL-OPEN.
OPENCLAW_BIN="${OPENCLAW:-/opt/homebrew/bin/openclaw}"
NOTIFY_SH="${HOME}/notify.sh"
[ -f "$NOTIFY_SH" ] && source "$NOTIFY_SH" 2>/dev/null || true

_daily_ops_fatal_handler() {
    local exit_code=$?
    local line_no="${1:-unknown}"
    local fatal_msg="[SYSTEM_ALERT] daily_ops_report FATAL abort exit=${exit_code} line=${line_no} — 运维日报当日丢失! V37.9.60 MR-19 横向推广. 排查 ~/daily_ops_report.log + bash -x ~/daily_ops_report.sh"
    echo "[daily_ops_report] 🚨 FATAL exit=${exit_code} at line=${line_no} (set -e abort)" >&2
    echo "[$(TZ=Asia/Hong_Kong date '+%Y-%m-%d %H:%M:%S')] daily_ops_report FATAL abort exit=${exit_code} line=${line_no}" >> "$HOME/.openclaw_alerts.log" 2>/dev/null || true
    if command -v notify >/dev/null 2>&1; then
        notify "$fatal_msg" --topic alerts 2>/dev/null || true
    elif [ -x "$OPENCLAW_BIN" ]; then
        "$OPENCLAW_BIN" message send --channel discord --channel-id "${DISCORD_CH_ALERTS:-}" --content "$fatal_msg" 2>/dev/null || true
    fi
}
trap '_daily_ops_fatal_handler $LINENO' ERR

# ── 1. 运行 conv_quality（不推送，仅输出报告）──────────────────────
CONV_REPORT=""
CONV_OUTPUT=$(python3 ~/conv_quality.py --no-push 2>&1) || true
# 提取报告内容（跳过 [conv_quality] 前缀的日志行）
CONV_REPORT=$(echo "$CONV_OUTPUT" | grep -v '^\[conv_quality\]')

# ── 2. 运行 token_report（不推送，仅输出报告）──────────────────────
TOKEN_REPORT=""
TOKEN_OUTPUT=$(python3 ~/token_report.py --no-push 2>&1) || true
TOKEN_REPORT=$(echo "$TOKEN_OUTPUT" | grep -v '^\[token_report\]')

# ── 3. 组装合并消息 ───────────────────────────────────────────────
MSG=""
if [ -n "${CONV_REPORT// }" ]; then
    MSG="$CONV_REPORT"
fi
if [ -n "${TOKEN_REPORT// }" ]; then
    if [ -n "$MSG" ]; then
        MSG="$MSG

━━━━━━━━━━━━━━━━━━━━

$TOKEN_REPORT"
    else
        MSG="$TOKEN_REPORT"
    fi
fi

# ── 4. 推送（一条消息，双通道）─────────────────────────────────────
if [ -z "${MSG// }" ]; then
    log "无数据，跳过推送"
    exit 0
fi

# 截断到 4000 字符（WhatsApp 限制）
MSG="$(echo "$MSG" | head -c 4000)"

NOTIFY_SH="${HOME}/notify.sh"
if [ -f "$NOTIFY_SH" ]; then
    source "$NOTIFY_SH"
    if notify "$MSG" --topic daily; then
        log "推送成功 (WhatsApp + Discord)"
    else
        log "ERROR: 推送失败"
    fi
else
    log "ERROR: notify.sh 不存在"
fi
