#!/bin/bash
# wa_keepalive.sh — WhatsApp session 保活（每30分钟由 crontab 触发）
# 目的：防止 WhatsApp Web session 因手机休眠/网络不活跃而断连
# 原理：向 Gateway 发一个轻量 HTTP 请求，触发 session 保活
# 遵循原则 #33：只检目标组件（Gateway），不走 LLM 链路
# 遵循原则 #31：不重启任何进程，只做连接探测
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:$PATH"

GATEWAY_URL="http://localhost:18789"
LOG="$HOME/wa_keepalive.log"
TS="$(TZ=Asia/Hong_Kong date '+%Y-%m-%d %H:%M:%S')"

# 1. 检查 Gateway 端口是否存活（不走 LLM 链路）
HTTP_CODE=$(curl -s --max-time 5 -o /dev/null -w '%{http_code}' "$GATEWAY_URL" 2>/dev/null)
if [ "$HTTP_CODE" -lt 200 ] || [ "$HTTP_CODE" -ge 400 ]; then
    echo "[$TS] WARN: Gateway 不可达 (HTTP $HTTP_CODE)，跳过 keepalive" >> "$LOG"
    exit 0  # 不报错，Gateway 由 launchd 管理
fi

# 2. 发送一个空的 status 查询保持 WhatsApp session 活跃
# 使用 openclaw 的 health/status 接口（如果有），否则用最轻量的 message dry-run
OPENCLAW="${OPENCLAW:-/opt/homebrew/bin/openclaw}"
if command -v "$OPENCLAW" >/dev/null 2>&1; then
    # dry-run 模式：不实际发送消息，但会触发 Gateway 检查 WhatsApp 连接状态
    RESULT=$("$OPENCLAW" message send \
        --target "${OPENCLAW_PHONE:-+85200000000}" \
        --message "keepalive" \
        --dry-run \
        --json 2>/dev/null || true)

    if echo "$RESULT" | grep -q '"dryRun": true' 2>/dev/null; then
        # dry-run 成功，session 活跃
        echo "[$TS] OK: session active" >> "$LOG"
    else
        echo "[$TS] WARN: dry-run 响应异常: $(echo "$RESULT" | head -1)" >> "$LOG"
    fi
else
    echo "[$TS] WARN: openclaw 未找到，跳过" >> "$LOG"
fi

# 日志保留最近 200 行
if [ -f "$LOG" ] && [ "$(wc -l < "$LOG" | tr -d ' ')" -gt 200 ]; then
    tail -100 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
fi
