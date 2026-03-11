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

# 2. 真实发送测试消息验证 WhatsApp 通道可用性
# dry-run 无法检测 session 断连，必须做真实发送才能验证
OPENCLAW="${OPENCLAW:-/opt/homebrew/bin/openclaw}"
TO="${OPENCLAW_PHONE:-+85200000000}"
SEND_ERR=$(mktemp)

if ! command -v "$OPENCLAW" >/dev/null 2>&1; then
    echo "[$TS] WARN: openclaw 未找到，跳过" >> "$LOG"
    rm -f "$SEND_ERR"
    exit 0
fi

# 静默 keepalive：发送不可见的零宽字符消息（不打扰用户）
if "$OPENCLAW" message send --target "$TO" --message "​" --json >/dev/null 2>"$SEND_ERR"; then
    echo "[$TS] OK: send verified" >> "$LOG"
else
    ERR_DETAIL=$(head -3 "$SEND_ERR" 2>/dev/null)
    echo "[$TS] FAIL: WhatsApp 发送失败: $ERR_DETAIL" >> "$LOG"
    # 尝试通过 Gateway 日志获取更多信息
    GATEWAY_LOG="/tmp/openclaw/openclaw-$(TZ=Asia/Hong_Kong date +%Y-%m-%d).log"
    if [ -f "$GATEWAY_LOG" ]; then
        WA_ERR=$(tail -20 "$GATEWAY_LOG" 2>/dev/null | grep -i "whatsapp\|session\|disconnect" | tail -3)
        [ -n "$WA_ERR" ] && echo "[$TS] Gateway日志: $WA_ERR" >> "$LOG"
    fi
fi
rm -f "$SEND_ERR"

# 日志保留最近 200 行
if [ -f "$LOG" ] && [ "$(wc -l < "$LOG" | tr -d ' ')" -gt 200 ]; then
    tail -100 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
fi
