#!/bin/bash
set -euo pipefail
# restart.sh - 一键重启所有服务 / One-command restart all services

# Ensure Homebrew binaries are in PATH (needed when called from cron)
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:$PATH"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
OPENCLAW="${OPENCLAW:-$(command -v openclaw 2>/dev/null || echo /opt/homebrew/bin/openclaw)}"

echo "[restart] Stopping all services..."
"$OPENCLAW" gateway stop 2>/dev/null || true
# Kill any process on ports; ignore error if none running
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

echo "[restart] Starting Gateway..."
nohup "$OPENCLAW" gateway --verbose >> ~/gateway.log 2>&1 &
disown
sleep 3
echo "[restart] Done!"
