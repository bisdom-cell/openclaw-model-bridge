#!/bin/bash
set -euo pipefail
# upgrade_openclaw.sh - OpenClaw Gateway 升级 SOP
# 用法：bash ~/openclaw-model-bridge/upgrade_openclaw.sh <目标版本, 如 2026.4.27>
# V37.9.138: 禁止隐式 @latest — 2026-06-11 升级 4.27 时发现本脚本装 @latest 会
# 拉到 2026.6.x (SQLite migration 动荡版本), 目标版本必须显式传参 (eval doc 13.9.4)
# ⚠️ 必须通过 SSH 直接执行，禁止通过 WhatsApp 让 AI 自我升级

PHONE="${OPENCLAW_PHONE:-+85200000000}"
OPENCLAW="/opt/homebrew/bin/openclaw"
# V37.9.173 PathB-3: source notify.sh 让升级通知走微信 + Discord（FAIL-OPEN 兜底）
for _ns in "$HOME/openclaw-model-bridge/notify.sh" "$HOME/notify.sh"; do
    [ -f "$_ns" ] && { source "$_ns" 2>/dev/null || true; break; }
done

echo "=== OpenClaw 升级脚本 ==="
echo "$(date '+%Y-%m-%d %H:%M:%S')"

# 0. 前置检查
echo ""
echo "[1/6] 当前版本..."
OLD_VER=$($OPENCLAW --version 2>/dev/null || echo "未知")
echo "  当前版本: $OLD_VER"

# 1. 停止 Gateway（Adapter 和 Proxy 不受影响）
echo ""
echo "[2/6] 停止 Gateway..."
$OPENCLAW gateway stop 2>/dev/null || true
lsof -ti :18789 2>/dev/null | xargs kill 2>/dev/null || true
sleep 2

# 2. 执行 npm 升级
echo ""
echo "[3/6] 执行 npm 升级..."
TARGET_VERSION="${1:?用法: bash upgrade_openclaw.sh <目标版本如 2026.4.27> — 禁止隐式 @latest (V37.9.138)}"
npm install -g "openclaw@${TARGET_VERSION}"

# 3. 确认新版本
echo ""
echo "[4/6] 确认新版本..."
NEW_VER=$($OPENCLAW --version 2>/dev/null || echo "升级失败")
echo "  新版本: $NEW_VER"

if [ "$NEW_VER" = "升级失败" ]; then
    echo "❌ 升级失败！openclaw 命令不可用"
    exit 1
fi

# 4. 重启 Gateway
echo ""
echo "[5/6] 重启 Gateway..."
$OPENCLAW gateway --verbose &
sleep 5

# 5. 验证
echo ""
echo "[6/6] 验证服务状态..."
GW=$(lsof -ti :18789 >/dev/null 2>&1 && echo "UP" || echo "DOWN")
AD=$(lsof -ti :5001 >/dev/null 2>&1 && echo "UP" || echo "DOWN")
PX=$(lsof -ti :5002 >/dev/null 2>&1 && echo "UP" || echo "DOWN")
HEALTH=$(curl -s --max-time 5 http://localhost:5002/health 2>/dev/null || echo "无响应")

echo "  Gateway(18789): $GW"
echo "  Adapter(5001):  $AD"
echo "  Proxy(5002):    $PX"
echo "  Health:         $HEALTH"

if [ "$GW" = "UP" ]; then
    echo ""
    echo "✅ 升级成功: $OLD_VER → $NEW_VER"
    # 推送通知 (V37.9.173 PathB-3: 走 notify → 微信 + Discord #alerts + 重试/队列)
    if command -v notify >/dev/null 2>&1; then
        notify "✅ OpenClaw 升级完成: $OLD_VER → $NEW_VER" --topic alerts >/dev/null 2>&1 || true
    else
        $OPENCLAW message send --channel whatsapp -t "$PHONE" \
            -m "✅ OpenClaw 升级完成: $OLD_VER → $NEW_VER" 2>/dev/null || true
        $OPENCLAW message send --channel discord -t "${DISCORD_CH_ALERTS:-}" \
            -m "✅ OpenClaw 升级完成: $OLD_VER → $NEW_VER" 2>/dev/null || true
    fi
else
    echo ""
    echo "⚠️ Gateway 未启动，请检查日志: /tmp/openclaw/openclaw-$(date +%Y-%m-%d).log"
    exit 1
fi
