#!/bin/bash
set -euo pipefail
# restart.sh - 一键重启所有服务 / One-command restart all services

OPENCLAW=$(command -v openclaw 2>/dev/null || echo "/opt/homebrew/bin/openclaw")

echo "[restart] Stopping all services..."
$OPENCLAW gateway stop 2>/dev/null || true
# Kill any process on port 18789; ignore error if none running
lsof -ti :18789 2>/dev/null | xargs kill 2>/dev/null || true
sleep 1

if ! lsof -ti :5001 > /dev/null 2>&1; then
    echo "[restart] Starting Adapter on :5001..."
    nohup python3 adapter.py > adapter.log 2>&1 &
    sleep 1
fi

if ! lsof -ti :5002 > /dev/null 2>&1; then
    echo "[restart] Starting Tool Proxy on :5002..."
    nohup python3 tool_proxy.py > tool_proxy.log 2>&1 &
    sleep 1
fi

echo "[restart] Starting Gateway..."
$OPENCLAW gateway --verbose &
echo "[restart] Done!"
