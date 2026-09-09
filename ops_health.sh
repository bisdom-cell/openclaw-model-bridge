#!/bin/bash
# ops agent 健康检查包装脚本
# ops agent 的模型常拒绝直接 curl localhost，但可以执行此脚本（原为 Qwen3 时代的绕行，primary 换模型后保留）
echo "=== Adapter (:5001) ==="
curl -s http://localhost:5001/health
echo ""
echo "=== Tool Proxy (:5002) ==="
curl -s http://localhost:5002/health
echo ""
echo "=== Gateway (:18789) ==="
curl -s http://localhost:18789/health
echo ""
echo "=== Disk ==="
df -h / | tail -1
echo "=== Cron ==="
crontab -l 2>/dev/null | grep -c '^[^#]'
echo "条活跃cron"
echo "=== Canary ==="
cat ~/.cron_canary 2>/dev/null
