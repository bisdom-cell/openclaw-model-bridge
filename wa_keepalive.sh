#!/bin/bash
# wa_keepalive.sh — WhatsApp session 保活（每30分钟由 crontab 触发）
# 目的：防止 WhatsApp Web session 因手机休眠/网络不活跃而断连
# 原理：向 Gateway 发一个轻量 HTTP 请求，触发 session 保活
# 注意：不发送真实消息（零宽字符在WhatsApp中仍显示为空消息气泡，会打扰用户）
# 端到端推送失败由 job_watchdog.sh 的日志扫描覆盖，无需在此重复验证
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:$PATH"

GATEWAY_URL="http://localhost:18789"
LOG="$HOME/wa_keepalive.log"
TS="$(TZ=Asia/Hong_Kong date '+%Y-%m-%d %H:%M:%S')"

# 检查 Gateway 端口是否存活（不走 LLM 链路，不发送消息）
HTTP_CODE=$(curl -s --max-time 5 -o /dev/null -w '%{http_code}' "$GATEWAY_URL" 2>/dev/null)
if [ "$HTTP_CODE" -ge 200 ] && [ "$HTTP_CODE" -lt 400 ]; then
    echo "[$TS] OK: Gateway reachable (HTTP $HTTP_CODE)" >> "$LOG"
else
    echo "[$TS] WARN: Gateway 不可达 (HTTP ${HTTP_CODE:-000})" >> "$LOG"
    # 不报错退出，Gateway 由 launchd KeepAlive 管理
fi

# 日志保留最近 200 行
if [ -f "$LOG" ] && [ "$(wc -l < "$LOG" | tr -d ' ')" -gt 200 ]; then
    tail -100 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
fi
