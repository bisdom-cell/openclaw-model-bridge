#!/bin/bash
# diagnose.sh — WhatsApp 无响应系统排查脚本
# 用法：ssh bisdom@<mac-mini> 后执行 bash ~/openclaw-model-bridge/diagnose.sh
# cron 环境 PATH 极简，必须显式声明
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:$PATH"
set -eo pipefail

TS="$(TZ=Asia/Hong_Kong date '+%Y-%m-%d %H:%M:%S HKT')"
echo "============================================"
echo "🔍 OpenClaw 系统诊断 — $TS"
echo "============================================"
echo ""

FAIL=0

# ── 1. 三端口存活检查 ──────────────────────────────────────────────
echo "【1/7】服务端口检查"
for port_info in "18789:Gateway" "5001:Adapter" "5002:Proxy"; do
    IFS=':' read -r port name <<< "$port_info"
    pid=$(lsof -ti :$port 2>/dev/null || true)
    if [ -n "$pid" ]; then
        echo "  ✅ $name (:$port) — PID $pid"
    else
        echo "  🔴 $name (:$port) — 未运行！"
        FAIL=1
    fi
done
echo ""

# ── 2. HTTP 健康探测 ──────────────────────────────────────────────
echo "【2/7】HTTP 健康探测"
# Gateway
GW_HTTP=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 http://localhost:18789/ 2>/dev/null || echo "000")
if [ "$GW_HTTP" = "000" ]; then
    echo "  🔴 Gateway HTTP — 无响应（连接超时/拒绝）"
    FAIL=1
else
    echo "  ✅ Gateway HTTP — 状态码 $GW_HTTP"
fi

# Proxy health
PX_HTTP=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 http://localhost:5002/v1/models 2>/dev/null || echo "000")
if [ "$PX_HTTP" = "000" ]; then
    echo "  🔴 Proxy HTTP — 无响应"
    FAIL=1
else
    echo "  ✅ Proxy HTTP — 状态码 $PX_HTTP"
fi

# Adapter health
AD_HTTP=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 http://localhost:5001/v1/models 2>/dev/null || echo "000")
if [ "$AD_HTTP" = "000" ]; then
    echo "  🔴 Adapter HTTP — 无响应"
    FAIL=1
else
    echo "  ✅ Adapter HTTP — 状态码 $AD_HTTP"
fi
echo ""

# ── 3. 远端模型ID检查（多任务同时失败的第一反应） ──────────────────
echo "【3/7】远端模型ID检查"
REMOTE_MODEL=$(curl -s --max-time 10 https://hkagentx.hkopenlab.com/v1/models \
    -H "Authorization: Bearer ${REMOTE_API_KEY}" 2>/dev/null \
    | python3 -c "
import json,sys
try:
    d=json.load(sys.stdin)
    models=[m['id'] for m in d['data'] if 'Qwen3' in m['id']]
    print(models[0] if models else 'NOT_FOUND')
except Exception as e:
    print(f'ERROR: {e}')
" 2>/dev/null || echo "NETWORK_ERROR")

LOCAL_MODEL=$(python3 -c "
import json
try:
    with open('/Users/bisdom/.openclaw/openclaw.json') as f: d=json.load(f)
    print(d['models']['providers']['qwen-local']['models'][0]['id'])
except Exception as e:
    print(f'ERROR: {e}')
" 2>/dev/null || echo "READ_ERROR")

echo "  远端: $REMOTE_MODEL"
echo "  本地: $LOCAL_MODEL"
if [ "$REMOTE_MODEL" = "$LOCAL_MODEL" ]; then
    echo "  ✅ 模型ID一致"
elif [[ "$REMOTE_MODEL" == ERROR* ]] || [[ "$REMOTE_MODEL" == "NETWORK_ERROR" ]]; then
    echo "  🔴 无法连接远端API！（网络故障或API Key失效）"
    FAIL=1
elif [ "$REMOTE_MODEL" = "NOT_FOUND" ]; then
    echo "  🔴 远端已无 Qwen3 模型！可能已下线或更换"
    FAIL=1
else
    echo "  🔴 模型ID不匹配！需要更新本地配置"
    FAIL=1
fi
echo ""

# ── 4. Proxy Stats 检查（连续错误 / context 超限） ────────────────
echo "【4/7】Proxy 监控状态"
STATS_FILE="$HOME/proxy_stats.json"
if [ -f "$STATS_FILE" ]; then
    python3 << 'PYEOF'
import json, time
from datetime import datetime, timedelta

with open("$HOME/proxy_stats.json".replace("$HOME", __import__("os").path.expanduser("~"))) as f:
    s = json.load(f)

updated = s.get("updated", "未知")
print(f"  最后更新: {updated}")
print(f"  今日请求: {s.get('total_requests', 0)} / 错误: {s.get('total_errors', 0)}")
print(f"  连续错误: {s.get('consecutive_errors', 0)}")
print(f"  最近 prompt_tokens: {s.get('last_prompt_tokens', 0):,} ({s.get('context_usage_pct', 0)}% of 260K)")
print(f"  今日最大 prompt_tokens: {s.get('max_prompt_tokens_today', 0):,}")

last_err = s.get("last_error", {})
if last_err.get("code"):
    print(f"  最近错误: HTTP {last_err['code']} @ {last_err.get('time', '?')} — {last_err.get('msg', '')[:80]}")

ce = s.get("consecutive_errors", 0)
if ce >= 3:
    print(f"  🔴 连续 {ce} 次错误！后端可能已不可用")

# 检查 stats 文件是否过期
try:
    ut = datetime.strptime(updated, "%Y-%m-%d %H:%M:%S")
    age = datetime.now() - ut
    if age > timedelta(hours=2):
        print(f"  🔴 proxy_stats.json 已 {age.total_seconds()/3600:.1f}h 未更新（Proxy 可能已停止）")
    elif age > timedelta(minutes=30):
        print(f"  🟡 proxy_stats.json {age.total_seconds()/60:.0f}min 前更新（可能无流量）")
except ValueError:
    pass
PYEOF
else
    echo "  🟡 proxy_stats.json 不存在（Proxy 可能从未成功处理请求）"
fi
echo ""

# ── 5. 最近日志分析 ───────────────────────────────────────────────
echo "【5/7】最近日志分析"

echo "  --- Proxy 日志最后 10 行 ---"
if [ -f "$HOME/tool_proxy.log" ]; then
    tail -10 "$HOME/tool_proxy.log" 2>/dev/null | sed 's/^/    /'
else
    echo "    (文件不存在)"
fi
echo ""

echo "  --- Adapter 日志最后 10 行 ---"
if [ -f "$HOME/adapter.log" ]; then
    tail -10 "$HOME/adapter.log" 2>/dev/null | sed 's/^/    /'
else
    echo "    (文件不存在)"
fi
echo ""

echo "  --- Gateway 今日日志最后 10 行 ---"
TODAY_LOG="/tmp/openclaw/openclaw-$(date '+%Y-%m-%d').log"
if [ -f "$TODAY_LOG" ]; then
    tail -10 "$TODAY_LOG" 2>/dev/null | sed 's/^/    /'
else
    echo "    (文件不存在: $TODAY_LOG)"
fi
echo ""

# ── 6. Crontab 完整性检查 ────────────────────────────────────────
echo "【6/7】Crontab 条目数"
CRON_COUNT=$(crontab -l 2>/dev/null | grep -v '^#' | grep -v '^$' | wc -l | tr -d ' ')
echo "  活跃条目数: $CRON_COUNT"
if [ "$CRON_COUNT" -lt 5 ]; then
    echo "  🔴 crontab 条目过少（预期 >= 7）！可能被意外清空"
    FAIL=1
fi
echo ""

# ── 7. WhatsApp 消息发送测试 ──────────────────────────────────────
echo "【7/7】WhatsApp 消息发送测试"
OPENCLAW="${OPENCLAW:-$(command -v openclaw 2>/dev/null || echo /opt/homebrew/bin/openclaw)}"
PHONE="${OPENCLAW_PHONE:-+85200000000}"
echo "  使用: $OPENCLAW"
echo "  目标: $PHONE"

if command -v openclaw >/dev/null 2>&1 || [ -x "$OPENCLAW" ]; then
    echo "  正在发送测试消息..."
    SEND_RESULT=$("$OPENCLAW" message send --target "$PHONE" --message "🔧 诊断测试消息 ($TS)" --json 2>&1 || true)
    echo "  发送结果: $SEND_RESULT" | head -5 | sed 's/^/    /'
else
    echo "  🔴 openclaw 命令未找到！"
    FAIL=1
fi
echo ""

# ── 汇总 ──────────────────────────────────────────────────────────
echo "============================================"
if [ "$FAIL" -eq 0 ]; then
    echo "✅ 所有基础检查通过"
    echo ""
    echo "如果仍无法收到 WhatsApp 消息，进一步检查："
    echo "  1. WhatsApp Web 是否已断开（手机打开 WhatsApp → Linked Devices）"
    echo "  2. Gateway 日志中是否有 'session' 或 'auth' 错误"
    echo "  3. 尝试重启 Gateway: launchctl unload/load com.openclaw.gateway.plist"
else
    echo "🔴 发现问题！建议操作："
    echo ""
    echo "  [服务未运行] → bash ~/openclaw-model-bridge/restart.sh"
    echo "  [模型ID不匹配] → 参考 docs/config.md 模型ID变更应急流程"
    echo "  [远端API不可达] → 检查网络/VPN，或等待远端恢复"
    echo "  [连续错误] → 查看 adapter.log 最近错误详情"
    echo "  [crontab被清空] → 从 docs/config.md 恢复 crontab 条目"
fi
echo "============================================"
