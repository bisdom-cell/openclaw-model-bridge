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
TS="$(TZ=${SYSTEM_TZ:-Asia/Hong_Kong} date '+%Y-%m-%d %H:%M:%S')"
WARN_COUNT_FILE="$HOME/.wa_keepalive_warn_count"
OPENCLAW="${OPENCLAW:-/opt/homebrew/bin/openclaw}"
# 连续 2 次 WARN（=1h 不可达）首次升级告警，之后每 6 次（=3h）重复告警
ESCALATE_FIRST=2
ESCALATE_REPEAT=6
# V37.9.162: WhatsApp 频道链接状态监控（2026-06-16 血案：WhatsApp session 被服务端
#   登出 7h，Gateway 全程 HTTP 200、Discord 全程 connected，但 WhatsApp 频道已死。
#   旧逻辑只探 Gateway 端口 → 对频道掉线完全盲 → 零告警。频道掉线独立计数、独立升级。
#   Gateway 健康 ≠ WhatsApp 频道在线——频道是 Gateway 内的 channel，会独立 logged out。）
WA_CHANNEL_WARN_FILE="$HOME/.wa_channel_warn_count"

# 解析 `openclaw channels status` 的 WhatsApp 行，频道掉线时升级 Discord（不走 WhatsApp 自身）
_wa_channel_check() {
    # parser 部署到 $HOME（FILE_MAP），dev 回退到脚本目录
    local parser="$HOME/wa_channel_status.py"
    [ -f "$parser" ] || parser="$(dirname "$0")/wa_channel_status.py"
    [ -f "$parser" ] || return 0  # FAIL-OPEN: parser 缺失不阻塞

    # channels status 是轻量调用（查运行中的 Gateway，CLI 不加载插件、无 staging churn）
    local parsed
    parsed=$("$OPENCLAW" channels status 2>/dev/null | python3 "$parser" 2>/dev/null)
    [ -n "$parsed" ] || return 0  # FAIL-OPEN: 解析失败不告警

    local escalate present reason rest
    escalate="${parsed%%|*}"
    rest="${parsed#*|}"
    present="${rest%%|*}"
    reason="${rest#*|}"

    if [ "$escalate" = "1" ]; then
        echo "[$TS] WARN: WhatsApp 频道异常 ($reason) — Gateway 健康但频道掉线" >> "$LOG"
        local prev
        prev=$(cat "$WA_CHANNEL_WARN_FILE" 2>/dev/null || echo "0")
        prev=$((prev + 1))
        echo "$prev" > "$WA_CHANNEL_WARN_FILE"
        if [ "$prev" -eq "$ESCALATE_FIRST" ] || \
           { [ "$prev" -gt "$ESCALATE_FIRST" ] && [ "$(( (prev - ESCALATE_FIRST) % ESCALATE_REPEAT ))" -eq 0 ]; }; then
            local amsg="[SYSTEM_ALERT]
⚠️ WhatsApp 频道连续 ${prev} 次掉线（Gateway 健康但频道断连）
状态: ${reason}
检查时间: ${TS}
排查: openclaw channels status
恢复: openclaw channels login --channel whatsapp（需手机扫码；会话被服务端登出时必需）"
            # 强制走 Discord（WhatsApp 频道已死，告警链不得依赖失效主体自身 — MR-14）
            "$OPENCLAW" message send --channel discord --target "${DISCORD_CH_ALERTS:-}" --message "$amsg" --json >/dev/null 2>&1 || true
            echo "[$TS] ESCALATED: WhatsApp 频道掉线已推 Discord #alerts (连续 ${prev} 次)" >> "$LOG"
        fi
    else
        # 频道健康 / 不确定 / 不存在 → 重置计数（FAIL-OPEN：不确定不告警）
        echo "0" > "$WA_CHANNEL_WARN_FILE"
        if [ "$present" = "1" ] && [ "$reason" = "connected" ]; then
            echo "[$TS] OK: WhatsApp 频道 connected" >> "$LOG"
        fi
    fi
}

# 检查 Gateway 端口是否存活（不走 LLM 链路，不发送消息）
HTTP_CODE=$(curl -s --max-time 5 -o /dev/null -w '%{http_code}' "$GATEWAY_URL" 2>/dev/null)
if [ "$HTTP_CODE" -ge 200 ] && [ "$HTTP_CODE" -lt 400 ]; then
    echo "[$TS] OK: Gateway reachable (HTTP $HTTP_CODE)" >> "$LOG"
    # 恢复正常，重置计数器
    echo "0" > "$WARN_COUNT_FILE"
    # V37.9.162: Gateway 健康时额外检查 WhatsApp 频道链接状态（堵 2026-06-16 静默 7h 盲区）
    _wa_channel_check
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
