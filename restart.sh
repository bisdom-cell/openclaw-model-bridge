#!/bin/bash
set -euo pipefail
# restart.sh - 一键重启所有服务 / One-command restart all services

# Ensure Homebrew binaries are in PATH (needed when called from cron)
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:$PATH"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
OPENCLAW="${OPENCLAW:-$(command -v openclaw 2>/dev/null || echo /opt/homebrew/bin/openclaw)}"

echo "[restart] Stopping all services..."
# Stop Gateway via launchctl (preserves plist registration knowledge for restart)
launchctl bootout "gui/$(id -u)/ai.openclaw.gateway" 2>/dev/null || true
# Kill any stray process on gateway port
lsof -ti :18789 2>/dev/null | xargs kill 2>/dev/null || true
lsof -ti :5001 2>/dev/null | xargs kill 2>/dev/null || true
lsof -ti :5002 2>/dev/null | xargs kill 2>/dev/null || true
sleep 2

echo "[restart] Starting Adapter on :5001..."
nohup python3 "$SCRIPT_DIR/adapter.py" > ~/adapter.log 2>&1 &
sleep 1

echo "[restart] Starting Tool Proxy on :5002..."
nohup python3 "$SCRIPT_DIR/tool_proxy.py" > ~/tool_proxy.log 2>&1 &
sleep 1

# ── #48703 hotfix: auto-patch listeners Map if needed ──
OPENCLAW_DIST="/opt/homebrew/lib/node_modules/openclaw/dist"
if [ -d "$OPENCLAW_DIST" ]; then
    UNPATCHED=$(grep -rl 'const listeners = /\* @__PURE__ \*/ new Map()' \
        "$OPENCLAW_DIST" --include="*.js" 2>/dev/null | grep -v ".bak" | wc -l | tr -d ' ' || echo 0)
    if [ "$UNPATCHED" -gt 0 ]; then
        echo "[restart] Applying #48703 hotfix ($UNPATCHED files)..."
        sudo sed -i.bak \
            's|const listeners = /\* @__PURE__ \*/ new Map()|const listeners = globalThis.__openclaw_web_listeners__ ??= /* @__PURE__ */ new Map()|g' \
            "$OPENCLAW_DIST"/*.js "$OPENCLAW_DIST"/plugin-sdk/*.js 2>/dev/null || true
        echo "[restart] #48703 hotfix applied"
    fi
fi

echo "[restart] Starting Gateway via launchd..."
GATEWAY_PLIST="$HOME/Library/LaunchAgents/ai.openclaw.gateway.plist"
if [ -f "$GATEWAY_PLIST" ]; then
    # bootout first (ignore error if not loaded)
    launchctl bootout "gui/$(id -u)/ai.openclaw.gateway" 2>/dev/null || true
    sleep 1
    launchctl bootstrap "gui/$(id -u)" "$GATEWAY_PLIST"
    echo "[restart] Gateway loaded via launchd (KeepAlive enabled)"
else
    echo "[restart] WARNING: launchd plist not found, falling back to nohup (no auto-restart)"
    nohup "$OPENCLAW" gateway --verbose >> ~/gateway.log 2>&1 &
    disown
fi
sleep 3
echo "[restart] Done!"
