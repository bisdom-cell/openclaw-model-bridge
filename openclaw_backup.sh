#!/bin/bash
# openclaw_backup.sh — 每日自动备份 OpenClaw state 到外挂 SSD
# 备份内容：config, credentials, sessions, memory（不含 workspace）
# 保留最近 7 天的备份，自动清理过期文件
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:/usr/bin:/bin"

BACKUP_DIR="/Volumes/MOVESPEED/openclaw_backup"
LOG="$HOME/openclaw_backup.log"
DATE=$(date '+%Y-%m-%d')
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')
KEEP_DAYS=7

echo "[$TIMESTAMP] === Backup start ===" >> "$LOG"

# 检查 SSD 是否挂载
if [ ! -d "/Volumes/MOVESPEED" ]; then
    echo "[$TIMESTAMP] ERROR: SSD not mounted, skip backup" >> "$LOG"
    exit 1
fi

# 创建备份目录
mkdir -p "$BACKUP_DIR"

# 执行 openclaw backup create
BACKUP_FILE="$BACKUP_DIR/openclaw-backup-${DATE}.tar.gz"
if openclaw backup create --no-include-workspace --output "$BACKUP_FILE" >> "$LOG" 2>&1; then
    SIZE=$(du -h "$BACKUP_FILE" 2>/dev/null | cut -f1)
    echo "[$TIMESTAMP] OK: $BACKUP_FILE ($SIZE)" >> "$LOG"
else
    # fallback: 如果 --output 不支持，用默认位置再拷贝
    echo "[$TIMESTAMP] WARN: --output failed, trying default location" >> "$LOG"
    if openclaw backup create --no-include-workspace >> "$LOG" 2>&1; then
        # 找到最新的备份文件并移动到 SSD
        LATEST=$(ls -t ~/.openclaw/backups/*.tar.gz 2>/dev/null | head -1)
        if [ -n "$LATEST" ]; then
            cp "$LATEST" "$BACKUP_FILE"
            SIZE=$(du -h "$BACKUP_FILE" 2>/dev/null | cut -f1)
            echo "[$TIMESTAMP] OK (copied): $BACKUP_FILE ($SIZE)" >> "$LOG"
        else
            echo "[$TIMESTAMP] ERROR: backup created but file not found" >> "$LOG"
            exit 2
        fi
    else
        echo "[$TIMESTAMP] ERROR: openclaw backup create failed" >> "$LOG"
        exit 3
    fi
fi

# 清理过期备份（保留最近 N 天）
DELETED=$(find "$BACKUP_DIR" -name "openclaw-backup-*.tar.gz" -mtime +${KEEP_DAYS} -delete -print 2>/dev/null | wc -l | tr -d ' ')
if [ "$DELETED" -gt 0 ]; then
    echo "[$TIMESTAMP] Cleaned $DELETED old backup(s)" >> "$LOG"
fi

echo "[$TIMESTAMP] === Backup done ===" >> "$LOG"
