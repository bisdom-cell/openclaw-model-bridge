#!/bin/bash
# movespeed_daily_sync.sh — 每日 KB 全量同步到外挂 SSD
# V37.9.86: 从 inline crontab 升级为标准 job (消除 convergence 盲区)
# 依赖: movespeed_rsync_helper.sh (V37.9.27 jitter+retry+fail-loud)
set -uo pipefail
# V37.9.310 (🔴 2026-08-15~17 备份中断根因): PATH 必须含 /sbin —— macOS 的 mount 在
# /sbin/mount, 旧 PATH 不含 /sbin → 脚本内 `mount` command-not-found → 管道失败 →
# V37.9.304 的挂载检测恒判"未挂载" → 备份连续静默跳过 3 天。
# dev(Linux) 的 mount 在 /usr/bin, 落在旧 PATH 内 → dev 测试全绿 = dev-production 接缝。
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:/usr/bin:/bin:/sbin:/usr/sbin"

LOG="$HOME/movespeed_daily_sync.log"
STATUS_FILE="$HOME/.kb/cache/last_run_movespeed_sync.json"
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')

log() { echo "[$TIMESTAMP] $*" >&2; }

log "=== movespeed_daily_sync start ===" >> "$LOG"

SRC="$HOME/.kb/"
DST="/Volumes/MOVESPEED/KB/"

# V37.9.304 (对抗审计 C7): 挂载检测查 mount 表而非仅 -d — 不洁弹出/卸载竞态可在
# /Volumes/MOVESPEED 留下本地幽灵目录, -d 永真后全量 KB rsync 会写进启动盘
# (真 SSD 再插入被挂到 "/Volumes/MOVESPEED 1"), rc 0/status ok 全绿零 incident。
# 精确匹配 " on /Volumes/MOVESPEED (" 防幽灵目录场景下松匹配误认 "MOVESPEED 1"。
# 非 macOS (dev, 无 mount 表该挂载) 自然落 skip 分支 — 本 job 仅 Mac Mini 运行。
# V37.9.309: 两个条件拆开报, 理由同 openclaw_backup.sh — 合并成一条时日志分不清
# 「SSD 没插」还是「幽灵目录未真挂载」, 定性只能靠人工上机敲 mount。
SSD_SKIP_REASON=""
if [ ! -d "/Volumes/MOVESPEED" ]; then
    SSD_SKIP_REASON="目录 /Volumes/MOVESPEED 不存在 — SSD 未插入或已干净卸载"
elif ! command -v mount >/dev/null 2>&1; then
    # V37.9.310: 工具缺失必须与"没挂载"可区分 —— 正是 8-15~17 中断难定性的原因。
    SSD_SKIP_REASON="mount 命令不可用 (PATH=$PATH) — 无法验证挂载"
elif ! mount | grep -q " on /Volumes/MOVESPEED ("; then
    MOUNT_LINES=$(mount | grep -i movespeed | tr '\n' ';' || true)
    if [ -z "$MOUNT_LINES" ]; then
        MOUNT_LINES="(mount 表中无任何 MOVESPEED 行)"
    fi
    SSD_SKIP_REASON="目录存在但不在 mount 表 — 幽灵目录/不洁弹出; mount 表实况: $MOUNT_LINES"
fi
if [ -n "$SSD_SKIP_REASON" ]; then
    log "WARN: SSD not mounted ($SSD_SKIP_REASON), skip sync" >> "$LOG"
    # V37.9.287: 时间键 ts→time — watchdog/kb_status_refresh/observer 全部读 time,
    # ts 键即使登记也解析不出 (状态文件格式异常)。与全部 last_run 写者对齐。
    printf '{"status":"skipped_no_ssd","time":"%s"}\n' "$TIMESTAMP" > "$STATUS_FILE" 2>/dev/null
    exit 0
fi

mkdir -p "$DST" 2>/dev/null

# V37.9.304 (对抗审计 C1): helper 的默认契约是 fail-open 恒 exit 0 (20 个 set -e
# 内容 job 依赖, V37.9.31) — 本脚本曾用裸 RC=$? 判成败 → status:"failed" 分支死代码,
# rsync 三连败仍写 status:ok + "OK: synced $SIZE" (SIZE 是 du 陈旧 DST, 数字看着
# 完全合理) = MOVESPEED 60 天潜伏血案同形。改用 --strict-exit opt-in 真实退出码
# (0=成功 / 1=重试后失败 / 3=TM 备份进行中跳过), RC 捕获走 || 惯例 (set -e 安全)。
HELPER="$HOME/movespeed_rsync_helper.sh"
RC=0
if [ -f "$HELPER" ]; then
    bash "$HELPER" --strict-exit "$0" -- -av --delete "$SRC" "$DST" >> "$LOG" 2>&1 || RC=$?
else
    rsync -av --delete "$SRC" "$DST" >> "$LOG" 2>&1 || RC=$?
    if [ "$RC" -ne 0 ]; then
        log "WARN: SSD rsync failed (exit=$RC), helper not found" >> "$LOG"
    fi
fi

if [ "$RC" -eq 0 ]; then
    SIZE=$(du -sh "$DST" 2>/dev/null | cut -f1)
    log "OK: synced $SRC → $DST ($SIZE)" >> "$LOG"
    printf '{"status":"ok","time":"%s","size":"%s"}\n' "$TIMESTAMP" "$SIZE" > "$STATUS_FILE" 2>/dev/null
elif [ "$RC" -eq 3 ]; then
    # TM 备份进行中跳过 = 本日未同步, 必须可见 (watchdog catch-all 告警非 ok 状态 —
    # 与 skipped_no_ssd 同语义: "备份不发生本身就该可见", V37.9.287)
    log "WARN: sync skipped (Time Machine backup running) — 本日未同步" >> "$LOG"
    printf '{"status":"skipped_tm_backup","time":"%s"}\n' "$TIMESTAMP" > "$STATUS_FILE" 2>/dev/null
else
    log "WARN: sync failed (exit=$RC)" >> "$LOG"
    printf '{"status":"failed","time":"%s","exit_code":%d}\n' "$TIMESTAMP" "$RC" > "$STATUS_FILE" 2>/dev/null
fi

log "=== movespeed_daily_sync done ===" >> "$LOG"
