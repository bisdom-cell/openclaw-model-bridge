#!/bin/bash
# auto_deploy.sh — 自动部署脚本
# 轮询 GitHub，自动发现最新 claude/ 分支，有新 commit 就自动部署
# 使用方式：加入系统 crontab，每分钟执行一次

REPO_DIR="$HOME/openclaw-model-bridge"
LOG="$HOME/.openclaw/logs/auto_deploy.log"
RESTART_SCRIPT="$REPO_DIR/restart.sh"

mkdir -p "$(dirname "$LOG")"

cd "$REPO_DIR" || { echo "$(date) ERROR: cannot cd to $REPO_DIR" >> "$LOG"; exit 1; }

# 拉取所有远端分支信息
git fetch origin --quiet 2>&1 || {
    echo "$(date) WARN: git fetch failed" >> "$LOG"
    exit 1
}

# 自动发现最新的 claude/ 分支（按 commit 时间排序，取最新）
LATEST_CLAUDE=$(git branch -r --sort=-committerdate \
    | grep 'origin/claude/' \
    | head -1 \
    | sed 's|.*origin/||' \
    | tr -d ' ')

if [ -z "$LATEST_CLAUDE" ]; then
    echo "$(date) WARN: 未找到 claude/ 分支，跳过" >> "$LOG"
    exit 0
fi

CURRENT=$(git symbolic-ref --short HEAD 2>/dev/null)

# 如果最新 claude/ 分支与当前不同，自动切换
if [ "$CURRENT" != "$LATEST_CLAUDE" ]; then
    echo "$(date) 发现新分支: $CURRENT -> $LATEST_CLAUDE，切换中..." >> "$LOG"
    git checkout -B "$LATEST_CLAUDE" --track "origin/$LATEST_CLAUDE" >> "$LOG" 2>&1
    if [ $? -ne 0 ]; then
        echo "$(date) ERROR: 分支切换失败" >> "$LOG"
        exit 1
    fi
    echo "$(date) ✅ 已切换到 $LATEST_CLAUDE" >> "$LOG"
fi

LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse "origin/$LATEST_CLAUDE")

if [ "$LOCAL" = "$REMOTE" ]; then
    # 无更新，静默退出
    exit 0
fi

echo "$(date) 检测到新 commit: $LOCAL -> $REMOTE，开始部署..." >> "$LOG"

git reset --hard "origin/$LATEST_CLAUDE" >> "$LOG" 2>&1
if [ $? -ne 0 ]; then
    echo "$(date) ERROR: git reset 失败" >> "$LOG"
    exit 1
fi

bash "$RESTART_SCRIPT" >> "$LOG" 2>&1
if [ $? -ne 0 ]; then
    echo "$(date) ERROR: restart.sh 失败" >> "$LOG"
    exit 1
fi

echo "$(date) ✅ 部署完成 (branch: $LATEST_CLAUDE, commit: $REMOTE)" >> "$LOG"
