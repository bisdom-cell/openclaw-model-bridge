#!/bin/bash
# cron_canary.sh — Cron 心跳金丝雀（V30新增）
# 用途：最小化 cron 存活探测 — 写一个 epoch 时间戳到文件
# 注册：*/10 * * * * bash ~/openclaw-model-bridge/cron_canary.sh
# 消费者：job_watchdog.sh + cron_doctor.sh 读取心跳检测 cron 是否存活
# 设计原则：零依赖（不需要 python3/curl/openclaw），零锁文件，原子写入
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:$PATH"

CANARY_FILE="${HOME}/.cron_canary"
CANARY_LOG="${HOME}/.cron_canary_log"

# 原子写入：先写临时文件，再 mv（避免读写竞争）
EPOCH=$(date +%s)
HUMAN=$(TZ=Asia/Hong_Kong date '+%Y-%m-%d %H:%M:%S')
TMP="${CANARY_FILE}.tmp.$$"

printf '%s\n%s\n' "$EPOCH" "$HUMAN" > "$TMP" && mv "$TMP" "$CANARY_FILE"

# 保留最近 50 行心跳日志（供排查"cron 何时停过"）
echo "$HUMAN $EPOCH" >> "$CANARY_LOG"
if [ -f "$CANARY_LOG" ] && [ "$(wc -l < "$CANARY_LOG" | tr -d ' ')" -gt 50 ]; then
    tail -30 "$CANARY_LOG" > "$CANARY_LOG.tmp" && mv "$CANARY_LOG.tmp" "$CANARY_LOG"
fi
