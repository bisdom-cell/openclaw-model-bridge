#!/bin/bash
# cron 环境 PATH 极简，必须显式声明
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:$PATH"
# 加载环境变量（cron 环境中 OPENCLAW_PHONE/DISCORD_CH_* 等必须从 profile 获取）
source "$HOME/.bash_profile" 2>/dev/null || source "$HOME/.env_shared" 2>/dev/null || true
# 运维日报 — 合并 conv_quality + token_report 为一条推送
# 每天 08:15 由 system crontab 触发，报告前一天数据
# 替代原来分开推送的两个脚本（每天 2 条 → 1 条）
set -eo pipefail

TS="$(TZ=Asia/Hong_Kong date '+%Y-%m-%d %H:%M:%S')"
DATE="$(TZ=Asia/Hong_Kong date '+%Y-%m-%d')"
LOG_PREFIX="[daily_ops_report]"

log() { echo "[$TS] $LOG_PREFIX $1"; }

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
