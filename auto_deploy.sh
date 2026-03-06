#!/bin/bash
# auto_deploy.sh — 自动部署脚本
# 轮询 GitHub，有新 commit 就自动 git pull + restart
# 使用方式：加入系统 crontab，每分钟执行一次

REPO_DIR="$HOME/openclaw-model-bridge"
LOG="$HOME/.openclaw/logs/auto_deploy.log"
RESTART_SCRIPT="$REPO_DIR/restart.sh"

mkdir -p "$(dirname "$LOG")"

cd "$REPO_DIR" || { echo "$(date) ERROR: cannot cd to $REPO_DIR" >> "$LOG"; exit 1; }

BRANCH=$(git symbolic-ref --short HEAD 2>/dev/null)
if [ -z "$BRANCH" ]; then
    echo "$(date) ERROR: cannot determine branch" >> "$LOG"
    exit 1
fi

# 拉取远端最新状态（不改变本地代码）
git fetch origin "$BRANCH" --quiet 2>&1 || {
    echo "$(date) WARN: git fetch failed" >> "$LOG"
    exit 1
}

LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse "origin/$BRANCH")

if [ "$LOCAL" = "$REMOTE" ]; then
    # 无更新，静默退出
    exit 0
fi

echo "$(date) 检测到新 commit: $LOCAL -> $REMOTE，开始部署..." >> "$LOG"

git pull --rebase origin "$BRANCH" >> "$LOG" 2>&1
if [ $? -ne 0 ]; then
    echo "$(date) ERROR: git pull 失败" >> "$LOG"
    exit 1
fi

bash "$RESTART_SCRIPT" >> "$LOG" 2>&1
if [ $? -ne 0 ]; then
    echo "$(date) ERROR: restart.sh 失败" >> "$LOG"
    exit 1
fi

echo "$(date) ✅ 部署完成 (branch: $BRANCH, commit: $REMOTE)" >> "$LOG"
