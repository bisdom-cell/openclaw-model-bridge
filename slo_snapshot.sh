#!/bin/bash
# slo_snapshot.sh — SLO 快照 cron wrapper (V37.9.79)
#
# 触发: 2026-05-18 Mac Mini 实测 health_check.sh 周报显示 `History: 0 snapshots`,
#   V36 设计 slo_dashboard.py --snapshot 写 ~/.kb/slo_history.jsonl, 但从未注册 cron.
# 影响: trend_24h/trend_7d 永远空 dict, p95 趋势无数据驱动 → SLO 阈值调整只能靠瞬时值.
# 修复: 注册 jobs_registry.yaml slo_snapshot job (每小时 :05 触发), wrapper 调
#       python3 ~/openclaw-model-bridge/slo_dashboard.py --snapshot 写历史快照.
# 一周后预期: ~/.kb/slo_history.jsonl 含 ~168 条快照, health_check 周报显示真趋势.
#
# cron 环境 PATH 极简，必须显式声明（规则 #13）
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:$PATH"

REPO_DIR="${OPENCLAW_REPO_DIR:-$HOME/openclaw-model-bridge}"

# 调用 slo_dashboard.py --snapshot
# 行为: 读 ~/proxy_stats.json → extract snapshot → append 到 ~/.kb/slo_history.jsonl
# FAIL-OPEN: proxy_stats.json 不存在或损坏时 silently skip, exit 0 不阻塞 cron
if [ ! -f "$REPO_DIR/slo_dashboard.py" ]; then
  echo "[slo_snapshot] $(date '+%Y-%m-%d %H:%M:%S') ERROR: slo_dashboard.py 不存在: $REPO_DIR" >&2
  exit 0
fi

python3 "$REPO_DIR/slo_dashboard.py" --snapshot 2>&1
echo "[slo_snapshot] $(date '+%Y-%m-%d %H:%M:%S') snapshot 完成"
