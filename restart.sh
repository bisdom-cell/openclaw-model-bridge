#!/bin/bash
set -euo pipefail
# restart.sh - 一键重启所有服务 / One-command restart all services
#
# V37.9.13 架构清理（2026-04-23）:
#   Adapter + Proxy 改用 `launchctl kickstart -k` 统一走 launchd 管理。
#   消除 V37.9.12.1 发现的 manual nohup 进程 + launchd KeepAlive 双管理
#   冲突（两路同时抢占 :5001 / :5002 端口 → launchd 侧持续 crash-loop）。
#   若 plist 不存在（dev 环境 / 未装 plist），fallback 到 nohup（向后兼容）。

# Ensure Homebrew binaries are in PATH (needed when called from cron)
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:$PATH"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
OPENCLAW="${OPENCLAW:-$(command -v openclaw 2>/dev/null || echo /opt/homebrew/bin/openclaw)}"
LAUNCH_AGENTS="$HOME/Library/LaunchAgents"

# Helper: restart a service under launchd management.
#   Signature: restart_via_launchd <label> <port> <plist> <display>
#   Returns: 0 success, 1 launchd failure, 2 launchd unavailable (caller fallback)
# Uses `launchctl kickstart -k` (modern idempotent API). Falls back to
# bootout+bootstrap if the service isn't loaded yet. Applies V37.8.13 post-
# start health verification loop (5×2s curl probe).
restart_via_launchd() {
    local label="$1" port="$2" plist="$3" display="$4"
    if ! command -v launchctl >/dev/null 2>&1 || [ ! -f "$plist" ]; then
        return 2
    fi
    echo "[restart] $display: launchctl kickstart -k gui/$(id -u)/$label"
    if ! launchctl kickstart -k "gui/$(id -u)/$label" 2>/dev/null; then
        # Not loaded — register via bootstrap
        launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
        if ! launchctl bootstrap "gui/$(id -u)" "$plist" 2>/dev/null; then
            echo "[restart] ⚠️ $display bootstrap failed (label=$label)"
            return 1
        fi
    fi
    # Health verification (V37.8.13 pattern extended from Gateway to adapter/proxy)
    local _attempt _code
    for _attempt in 1 2 3 4 5; do
        sleep 2
        _code=$(curl -s -o /dev/null -w "%{http_code}" --connect-timeout 2 --max-time 3 \
            "http://localhost:$port/health" 2>/dev/null || echo "000")
        if [ "$_code" = "200" ]; then
            echo "[restart] $display healthy (HTTP $_code after ${_attempt}×2s)"
            return 0
        fi
    done
    echo "[restart] ⚠️ $display failed to become healthy within 10s"
    return 1
}

echo "[restart] Stopping Gateway..."
# Stop Gateway via launchctl (preserves plist registration knowledge for restart)
launchctl bootout "gui/$(id -u)/ai.openclaw.gateway" 2>/dev/null || true
# Kill any stray process on gateway port (adapter/proxy are handled below via launchd)
lsof -ti :18789 2>/dev/null | xargs kill 2>/dev/null || true
sleep 2

# ── Adapter (:5001) ──────────────────────────────────────────────────
# V37.9.13: single manager (launchd) instead of nohup + launchd double management.
echo "[restart] Restarting Adapter on :5001..."
ADAPTER_PLIST="$LAUNCH_AGENTS/com.openclaw.adapter.plist"
_ad_rc=0
restart_via_launchd "com.openclaw.adapter" 5001 "$ADAPTER_PLIST" "Adapter" || _ad_rc=$?
if [ "$_ad_rc" -eq 2 ]; then
    echo "[restart] Adapter plist not found, fallback to nohup (no auto-restart)"
    lsof -ti :5001 2>/dev/null | xargs kill 2>/dev/null || true
    sleep 1
    nohup python3 "$SCRIPT_DIR/adapter.py" > ~/adapter.log 2>&1 &
    disown
    sleep 1
fi

# ── Tool Proxy (:5002) ───────────────────────────────────────────────
echo "[restart] Restarting Tool Proxy on :5002..."
PROXY_PLIST="$LAUNCH_AGENTS/com.openclaw.proxy.plist"
_px_rc=0
restart_via_launchd "com.openclaw.proxy" 5002 "$PROXY_PLIST" "Tool Proxy" || _px_rc=$?
if [ "$_px_rc" -eq 2 ]; then
    echo "[restart] Tool Proxy plist not found, fallback to nohup (no auto-restart)"
    lsof -ti :5002 2>/dev/null | xargs kill 2>/dev/null || true
    sleep 1
    nohup python3 "$SCRIPT_DIR/tool_proxy.py" > ~/tool_proxy.log 2>&1 &
    disown
    sleep 1
fi

# ── #48703 hotfix: auto-patch listeners Map if needed ──
OPENCLAW_DIST="/opt/homebrew/lib/node_modules/openclaw/dist"
if [ -d "$OPENCLAW_DIST" ]; then
    UNPATCHED=$(grep -rl 'const listeners = /\* @__PURE__ \*/ new Map()' \
        "$OPENCLAW_DIST" --include="*.js" 2>/dev/null | grep -vc ".bak" || true)
    UNPATCHED="${UNPATCHED:-0}"
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

    # V37.8.13: Post-bootstrap health verification (2026-04-16 血案：Gateway bootstrap
    # 成功但 21 秒内崩溃，restart.sh 报 "Done!" 却 Gateway 已死。现在主动等待并验证)
    GATEWAY_HEALTHY=false
    for _gw_attempt in 1 2 3 4 5; do
        sleep 3
        _gw_http=$(curl -s -o /dev/null -w "%{http_code}" --connect-timeout 2 --max-time 3 http://localhost:18789 2>/dev/null || echo "000")
        if [ "$_gw_http" -ge 200 ] 2>/dev/null && [ "$_gw_http" -lt 400 ] 2>/dev/null; then
            echo "[restart] Gateway health verified (HTTP $_gw_http after ${_gw_attempt}×3s)"
            GATEWAY_HEALTHY=true
            break
        fi
        echo "[restart] Gateway not yet healthy (attempt $_gw_attempt/5, HTTP $_gw_http)..."
    done

    if ! $GATEWAY_HEALTHY; then
        echo "[restart] ⚠️ Gateway failed to become healthy within 15s — needs manual investigation"
        echo "[restart] ⚠️ Check: launchctl list | grep gateway ; tail ~/.openclaw/logs/gateway.err.log"
    fi
else
    echo "[restart] WARNING: launchd plist not found, falling back to nohup (no auto-restart)"
    nohup "$OPENCLAW" gateway --verbose >> ~/gateway.log 2>&1 &
    disown
fi
sleep 3
echo "[restart] Done!"
