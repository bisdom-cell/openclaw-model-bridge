#!/bin/bash
# diagnose.sh — WhatsApp 无响应系统排查脚本
# 用法：ssh bisdom@<mac-mini> 后执行 bash ~/openclaw-model-bridge/diagnose.sh
# cron 环境 PATH 极简，必须显式声明
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:$PATH"
set -eo pipefail

TS="$(TZ=${SYSTEM_TZ:-Asia/Hong_Kong} date '+%Y-%m-%d %H:%M:%S HKT')"
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

# ── 3. 主力模型路由检查（多任务同时失败的第一反应） ─────────────────
# V37.9.365: 读 adapter /health（与 kb_status_refresh / preflight / health_check 同一真理源）。
# 旧【3/7】比对 Qwen3 远端 /models 与 openclaw.json qwen-local 标签——V37.9.222 起主力是 doubao_21,
# 那一步测的是 fallback 链末位端点: 主力宕了它照样 ✅, Qwen3 端点宕了它却判 FAIL = 两个方向都错。
# 断路器 OPEN 才是「主力连续失败、正在走 fallback」的直接信号。不打印 model 字段（doubao_21 的 model
# 是 Volcengine 接入点 ID, 排障输出常被整段贴进聊天, 不该带出去）。
echo "【3/7】主力模型路由检查"
ADAPTER_HEALTH=$(curl -s --max-time 5 http://localhost:5001/health 2>/dev/null || true)
ROUTE_REPORT=$(ADAPTER_HEALTH="$ADAPTER_HEALTH" python3 -c '
import json, os
raw = os.environ.get("ADAPTER_HEALTH", "")
try:
    d = json.loads(raw)
except Exception:
    d = None
if not isinstance(d, dict) or not d.get("ok"):
    print("FAIL")
    print("  🔴 adapter /health 无响应 — 主力模型路由未知")
else:
    chain = [str(x) for x in (d.get("fallback_chain") or [])]
    cb = str(d.get("circuit_breaker") or "")
    print("FAIL" if cb == "open" else "OK")
    print("  主力: " + str(d.get("provider") or "?"))
    print("  fallback 链: " + (" → ".join(chain) if chain else "(未配置)"))
    if cb == "open":
        print("  🔴 断路器 OPEN — 主力连续失败, 当前请求走 fallback")
    elif cb == "half-open":
        print("  🟡 断路器 half-open — 主力恢复探测中")
    elif cb == "closed":
        print("  ✅ 断路器 closed — 主力正常服务")
' 2>/dev/null || true)
if [ -z "$ROUTE_REPORT" ]; then
    echo "  🔴 路由状态解析失败"
    FAIL=1
else
    printf '%s\n' "$ROUTE_REPORT" | sed -n '2,$p'
    if [ "$(printf '%s\n' "$ROUTE_REPORT" | sed -n 1p)" = "FAIL" ]; then
        FAIL=1
    fi
fi
echo ""

# ── 4. Proxy Stats 检查（连续错误 / context 超限） ────────────────
echo "【4/7】Proxy 监控状态"
STATS_FILE="$HOME/proxy_stats.json"
if [ -f "$STATS_FILE" ]; then
    # V37.9.304 (对抗审计 C8): || 守卫 — set -eo pipefail 下 proxy_stats.json 损坏
    # (非原子写读到半截) 曾让排障脚本在 4/7 段自杀, 第 5-7 段与汇总全部不输出,
    # 操作者以为检查只有 4 项且"没报 🔴" = 排障工具自身 fail-plausible
    python3 << 'PYEOF' || echo "  🔴 proxy_stats.json 解析失败（文件损坏/写入中断？）— 继续后续检查"
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
# V37.9.173 PathB-3: source notify.sh，诊断测真实推送管线（微信 + Discord + 重试/队列）
for _ns in "$HOME/openclaw-model-bridge/notify.sh" "$HOME/notify.sh"; do
    [ -f "$_ns" ] && { source "$_ns" 2>/dev/null || true; break; }
done
echo "  使用: $OPENCLAW"
echo "  目标: $PHONE"

if command -v openclaw >/dev/null 2>&1 || [ -x "$OPENCLAW" ]; then
    echo "  正在发送测试消息（经 notify → 微信 + Discord，测真实推送管线）..."
    # V37.9.173 PathB-3: 测真实推送管线 notify；未加载时直发兜底
    if command -v notify >/dev/null 2>&1; then
        SEND_RESULT=$(notify "🔧 诊断测试消息 ($TS)" --topic alerts 2>&1 || true)
    else
        SEND_RESULT=$("$OPENCLAW" message send --channel whatsapp --target "$PHONE" --message "🔧 诊断测试消息 ($TS)" --json 2>&1 || true)
    fi
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
    echo "  3. 尝试重启 Gateway: bash ~/openclaw-model-bridge/restart.sh（launchd 标签 ai.openclaw.gateway）"
else
    echo "🔴 发现问题！建议操作："
    echo ""
    echo "  [服务未运行] → bash ~/openclaw-model-bridge/restart.sh"
    echo "  [断路器 OPEN] → tail -50 ~/adapter.log 看主力失败原因（key/端点/配额），fallback 链在兜底"
    echo "  [adapter /health 无响应] → bash ~/openclaw-model-bridge/restart.sh"
    echo "  [连续错误] → 查看 adapter.log 最近错误详情"
    echo "  [crontab被清空] → 从 docs/config.md 恢复 crontab 条目"
fi
echo "============================================"
