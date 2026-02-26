#!/bin/bash
# restart.sh - 一键重启所有服务 / One-command restart all services

echo "🔄 Stopping all services..."
openclaw gateway stop 2>/dev/null
kill $(lsof -ti :18789) 2>/dev/null
sleep 1

if ! lsof -ti :5001 > /dev/null 2>&1; then
    echo "🔧 Starting Adapter..."
    nohup python3 adapter.py > adapter.log 2>&1 &
    sleep 1
fi

if ! lsof -ti :5002 > /dev/null 2>&1; then
    echo "🔧 Starting Tool Proxy..."
    nohup python3 tool_proxy.py > tool_proxy.log 2>&1 &
    sleep 1
fi

echo "🚀 Starting Gateway..."
openclaw gateway --verbose &
echo "✅ Done!"
