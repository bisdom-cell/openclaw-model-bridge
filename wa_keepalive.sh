#!/bin/bash
# wa_keepalive.sh — WhatsApp session 保活 + Gateway 健康守护（每30分钟由 crontab 触发）
# 目的：防止 WhatsApp Web session 因手机休眠/网络不活跃而断连
# 原理：向 Gateway 发一个轻量 HTTP 请求，触发 session 保活
# 注意：不发送真实消息（零宽字符在WhatsApp中仍显示为空消息气泡，会打扰用户）
# V37.8.13: 连续 N 次 WARN 自动升级到 Discord #alerts 告警（2026-04-16 血案：
#   Gateway 宕 9h，wa_keepalive 只写日志不告警，job_watchdog 也未有效检测。
#   告警链不得依赖失效主体自身——Gateway 宕时 WhatsApp 也不通，必须走 Discord）
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:$PATH"
source "$HOME/.bash_profile" 2>/dev/null || source "$HOME/.env_shared" 2>/dev/null || true

GATEWAY_URL="http://localhost:18789"
LOG="$HOME/wa_keepalive.log"
TS="$(TZ=Asia/Hong_Kong date '+%Y-%m-%d %H:%M:%S')"
WARN_COUNT_FILE="$HOME/.wa_keepalive_warn_count"
OPENCLAW="${OPENCLAW:-/opt/homebrew/bin/openclaw}"
# 连续 2 次 WARN（=1h 不可达）首次升级告警，之后每 6 次（=3h）重复告警
ESCALATE_FIRST=2
ESCALATE_REPEAT=6

# 检查 Gateway 端口是否存活（不走 LLM 链路，不发送消息）
HTTP_CODE=$(curl -s --max-time 5 -o /dev/null -w '%{http_code}' "$GATEWAY_URL" 2>/dev/null)
if [ "$HTTP_CODE" -ge 200 ] && [ "$HTTP_CODE" -lt 400 ]; then
    echo "[$TS] OK: Gateway reachable (HTTP $HTTP_CODE)" >> "$LOG"
    # 恢复正常，重置计数器
    echo "0" > "$WARN_COUNT_FILE"
else
    echo "[$TS] WARN: Gateway 不可达 (HTTP ${HTTP_CODE:-000})" >> "$LOG"
    # V37.8.13: 递增计数器 + 条件升级告警
    PREV_COUNT=$(cat "$WARN_COUNT_FILE" 2>/dev/null || echo "0")
    PREV_COUNT=$((PREV_COUNT + 1))
    echo "$PREV_COUNT" > "$WARN_COUNT_FILE"

    if [ "$PREV_COUNT" -eq "$ESCALATE_FIRST" ] || \
       { [ "$PREV_COUNT" -gt "$ESCALATE_FIRST" ] && [ "$(( (PREV_COUNT - ESCALATE_FIRST) % ESCALATE_REPEAT ))" -eq 0 ]; }; then
        ALERT_MSG="[SYSTEM_ALERT]
⚠️ Gateway 连续 ${PREV_COUNT} 次不可达 (HTTP ${HTTP_CODE:-000})
检查时间: $TS
排查: launchctl list | grep gateway ; curl localhost:18789/health
恢复: launchctl bootstrap gui/\$(id -u) ~/Library/LaunchAgents/ai.openclaw.gateway.plist"
        # 强制走 Discord（WhatsApp 在 Gateway 宕时必不可用）
        "$OPENCLAW" message send --channel discord --target "${DISCORD_CH_ALERTS:-}" --message "$ALERT_MSG" --json >/dev/null 2>&1 || true
        echo "[$TS] ESCALATED: 已推送 Discord #alerts (连续 ${PREV_COUNT} 次不可达)" >> "$LOG"
    fi
fi

# 日志保留最近 200 行
if [ -f "$LOG" ] && [ "$(wc -l < "$LOG" | tr -d ' ')" -gt 200 ]; then
    tail -100 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
fi
