#!/bin/bash
set -euo pipefail
# upgrade_openclaw.sh - OpenClaw Gateway 升级 SOP
# 用法：bash ~/openclaw-model-bridge/upgrade_openclaw.sh
# ⚠️ 必须通过 SSH 直接执行，禁止通过 WhatsApp 让 AI 自我升级

PHONE="${WA_PHONE:-+85200000000}"
OPENCLAW="/opt/homebrew/bin/openclaw"

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
npm install -g openclaw@latest

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
    # 推送通知
    $OPENCLAW message send --channel whatsapp -t "$PHONE" \
        -m "✅ OpenClaw 升级完成: $OLD_VER → $NEW_VER" 2>/dev/null || true
else
    echo ""
    echo "⚠️ Gateway 未启动，请检查日志: /tmp/openclaw/openclaw-$(date +%Y-%m-%d).log"
    exit 1
fi
