#!/bin/bash
# check_upgrade.sh — OpenClaw 升级就绪检查
# 检查最新版本 release notes + WhatsApp plugin 状态
# 用法：bash check_upgrade.sh
set -euo pipefail
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:$PATH"

CURRENT=$(openclaw --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1 || echo "unknown")
echo "=== OpenClaw 升级就绪检查 $(date '+%Y-%m-%d %H:%M:%S') ==="
echo "当前版本: v$CURRENT"
echo ""

# ── 1. 检查最新版本 ──
echo "📋 1/2 检查最新版本"
LATEST_JSON=$(curl -s --max-time 10 "https://api.github.com/repos/openclaw/openclaw/releases/latest" 2>/dev/null || echo "{}")
LATEST_TAG=$(echo "$LATEST_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin).get('tag_name','unknown'))" 2>/dev/null || echo "unknown")
LATEST_VER=$(echo "$LATEST_TAG" | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' || echo "$LATEST_TAG")

if [ "$LATEST_VER" = "$CURRENT" ]; then
    echo "  ✅ 已是最新版本 (v$CURRENT)"
    echo ""
    echo "无需升级。"
    exit 0
fi

echo "  ⬆️  新版本可用: v$LATEST_VER (当前: v$CURRENT)"
echo ""

# 显示 release notes 摘要
echo "── Release Notes (v$LATEST_VER) ──"
echo "$LATEST_JSON" | python3 -c "
import json, sys
d = json.load(sys.stdin)
body = d.get('body', '(无)')
# 截取前 1500 字符
if len(body) > 1500:
    body = body[:1500] + '\n... (截断，完整内容见 GitHub)'
print(body)
" 2>/dev/null || echo "(无法获取)"
echo ""

# ── 2. 检查 WhatsApp plugin 状态 ──
echo "📋 2/2 检查 WhatsApp plugin 状态"

# 检查 npm 是否可用
NPM_CHECK=$(npm view "openclaw@$LATEST_VER" version 2>&1 || echo "unavailable")
if echo "$NPM_CHECK" | grep -q "$LATEST_VER"; then
    echo "  ✅ npm registry 可用"
else
    echo "  ❌ npm registry 不可用或限流"
    echo ""
    echo "结论: ❌ 暂不可升级（npm 不可用）"
    exit 1
fi

# 检查 WhatsApp plugin
WA_STATUS=$(openclaw plugins install whatsapp 2>&1 || true)
if echo "$WA_STATUS" | grep -q "Installed plugin"; then
    echo "  ✅ WhatsApp plugin 可安装（bundled）"
    UPGRADE_OK=true
elif echo "$WA_STATUS" | grep -q "prerelease"; then
    WA_VER=$(echo "$WA_STATUS" | grep -oE '[0-9]+\.[0-9]+\.[0-9]+-[A-Za-z]+' | head -1 || echo "unknown")
    echo "  ⚠️  WhatsApp plugin 仍为预发布版 ($WA_VER)"
    UPGRADE_OK=false
elif echo "$WA_STATUS" | grep -q "429"; then
    echo "  ❌ ClawHub 限流中 (429)"
    UPGRADE_OK=false
else
    echo "  ❓ 未知状态: $(echo "$WA_STATUS" | head -3)"
    UPGRADE_OK=false
fi

echo ""
echo "═══════════════════════════════════════"
if [ "$UPGRADE_OK" = true ]; then
    echo "结论: ✅ 可以升级到 v$LATEST_VER"
    echo ""
    echo "升级命令："
    echo "  npm install -g openclaw@$LATEST_VER"
    echo "  openclaw plugins install whatsapp"
    echo "  bash ~/restart.sh  # 自动检测并打 #48703 补丁"
    echo "  openclaw message send --target \"\$OPENCLAW_PHONE\" --message \"upgrade test\""
else
    echo "结论: ❌ 暂不建议升级"
    echo "  WhatsApp plugin 未就绪，留在 v$CURRENT + hotfix"
    echo "  下次检查: bash ~/openclaw-model-bridge/check_upgrade.sh"
fi
echo "═══════════════════════════════════════"
