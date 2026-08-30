#!/bin/bash
# windows/sync_from_macmini.sh — Mac Mini 项目数据 → Windows E 盘 每日镜像（V37.9.335）
#
# 运行环境: Windows WSL (Ubuntu) + rsync，由 Windows 任务计划程序每天 05:00 调用。
# 05:00 是 Mac 端夜间任务的干净窗口（03:00 备份 / 03:30 kb_embed / 04:00 SSD 同步已收尾，
# 06:00 radar 未开始），拉到的数据最新且无写入竞争。
#
# 数据清单（与 Mac 端生产路径的对应关系由 test_v37_9_335_windows_sync.py 跨文件契约钉死）:
#   kb             ~/.kb/                              → E:\openclaw-model-bridge\kb\
#   home_state     ~ 顶层 *.log + proxy_stats.json
#                  + .cron_canary + .crontab_backups/  → ...\home\
#   openclaw_logs  ~/.openclaw/logs/                   → ...\openclaw\logs\
#   job_caches     ~/.openclaw/jobs/                   → ...\openclaw\jobs\
#   media          ~/.openclaw/media/                  → ...\openclaw\media\
#   ssd_backup     /Volumes/MOVESPEED/openclaw_backup/ → ...\movespeed_backup\（每周日最新一份，全部长期保留）
#   sync_tool      ~/openclaw-model-bridge/windows/    → ...\_sync\upstream\（脚本自身更新通道）
#
# 刻意不拉:
#   - ~/openclaw-model-bridge 代码仓库（代码的家在 GitHub，不属于"生成的数据"）
#   - /Volumes/MOVESPEED/KB/（movespeed_daily_sync 产出的 ~/.kb 副本，拉原件不拉复制品）
#   - ~/.openclaw 顶层凭据/会话活文件（credentials/sessions —— 已完整包含在 ssd_backup
#     的每日 tar.gz 里，不让活凭据文件散落；⚠️ 注意 tar.gz 本身含 Gateway 凭据，
#     E 盘访问控制由所有者负责）
#   - ~/.notify_queue（瞬态重放队列，无归档价值）
#
# 防灾难镜像（V37.9.324 血案教训: 每日重设基线会把灾难吸收进基线）:
#   每个 --delete 都配 --max-delete 保险丝 —— Mac 端事故性大量删除/目录清空时
#   rsync 以 rc=25 中止删除，E 盘副本受保护，人工核实后再放行。
set -uo pipefail

HOSTS_DEFAULT="bisdom@10.102.0.23 bisdom@10.120.230.23"
HOSTS="${OPENCLAW_SYNC_HOSTS:-$HOSTS_DEFAULT}"
DEST="${OPENCLAW_SYNC_DEST:-/mnt/e/openclaw-model-bridge}"
SSH_OPTS="-o BatchMode=yes -o ConnectTimeout=6 -o StrictHostKeyChecking=accept-new"
RSYNC_TIMEOUT="${OPENCLAW_SYNC_RSYNC_TIMEOUT:-300}"

LOGDIR="$DEST/_sync"
LOG="$LOGDIR/sync.log"

log() { printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" | tee -a "$LOG"; }

if ! mkdir -p "$LOGDIR" 2>/dev/null; then
    echo "FATAL: 无法创建 $LOGDIR — E: 盘未挂载或 WSL 无 /mnt/e 访问权" >&2
    exit 2
fi

if ! command -v rsync >/dev/null 2>&1; then
    log "FATAL: WSL 内无 rsync — 执行: sudo apt-get update && sudo apt-get install -y rsync"
    exit 3
fi

# 单实例锁: 中继带宽下周日备份拉取可达数小时，防手动运行与 05:00 定时任务重叠互踩。
# 锁放 WSL 原生 /tmp（drvfs /mnt/e 上 flock 语义不可靠）；同一台机器单实例语义足够。
exec 9>/tmp/openclaw_sync.lock
if ! flock -n 9; then
    log "另一次同步仍在运行（/tmp/openclaw_sync.lock 被占用），本次退出"
    exit 0
fi

write_status() {
    printf '{"time":"%s","host":"%s","modules":{%s},"failed":"%s","ok":%s}\n' \
        "$(date '+%Y-%m-%d %H:%M:%S')" "$1" "${MODULE_RESULTS%,}" "$2" "$3" \
        > "$LOGDIR/last_sync.json"
}

MODULE_RESULTS=""
FAILED=""

HOST=""
for h in $HOSTS; do
    if ssh $SSH_OPTS "$h" exit 2>/dev/null; then
        HOST="$h"
        break
    fi
done
if [ -z "$HOST" ]; then
    log "FATAL: 无可达 Mac Mini host（尝试: ${HOSTS}）— 检查 网络/ZeroTier/Mac 开机状态/SSH key"
    write_status "none" "no_reachable_host" "false"
    exit 4
fi

log "════ 同步开始 host=$HOST dest=$DEST ════"

run_rsync() {
    local name="$1" maxdel="$2" src="$3" sub="$4"
    shift 4
    local dst="$DEST/$sub"
    mkdir -p "$dst"
    log "── [$name] $HOST:$src → $sub/"
    # V37.9.335-hotfix: E 盘经 WSL drvfs 挂载不接受 Unix 权限位/属主/目录时间戳——
    # 首跑实证 rsync -a 的 mkstemp(0600) 全部 EPERM → 每个文件都写不进（rc=23 且 du=0）。
    # 改 Windows 盘兼容模式: -rtz 保留递归/文件时间戳(增量判定依据)/压缩，--inplace 直写
    # 目标文件绕开 mkstemp 临时文件，--no-perms/--no-owner/--no-group/--omit-dir-times
    # 跳过 NTFS/exFAT 上无意义且报 EPERM 的属性操作。
    rsync -rtz --inplace --no-perms --no-owner --no-group --omit-dir-times \
          --partial --timeout="$RSYNC_TIMEOUT" \
          --delete --max-delete="$maxdel" \
          -e "ssh $SSH_OPTS" "$@" \
          "$HOST:$src" "$dst/" >> "$LOG" 2>&1
    local rc=$?
    if [ "$rc" -eq 0 ]; then
        log "   [$name] OK"
    elif [ "$rc" -eq 24 ]; then
        log "   [$name] OK (rc=24: 部分源文件传输中消失 — 活系统日志/缓存正常现象)"
        rc=0
    elif [ "$rc" -eq 25 ]; then
        log "   [$name] ABORT (rc=25: 触发 --max-delete=$maxdel 删除保险丝 — Mac 端疑似大量删除或目录清空, E 盘副本已保护; 若 Mac 端删除是合法的, 人工核实后临时调高该模块保险丝重跑)"
        FAILED="$FAILED $name"
    else
        log "   [$name] FAILED rc=$rc"
        FAILED="$FAILED $name"
    fi
    MODULE_RESULTS="$MODULE_RESULTS\"$name\":$rc,"
}

run_rsync kb            300 '.kb/'                               kb
run_rsync home_state    100 './'                                 home \
    --include='*.log' --include='proxy_stats.json' --include='.cron_canary' \
    --include='.crontab_backups/***' --exclude='*'
run_rsync openclaw_logs 100 '.openclaw/logs/'                    openclaw/logs
run_rsync job_caches    300 '.openclaw/jobs/'                    openclaw/jobs
run_rsync media         500 '.openclaw/media/'                   openclaw/media

# V37.9.335-relay: 办公室网络封锁对外 UDP（dig@223.5.5.5 超时 + zerotier-cli TUNNELED
# + tcpFallbackActive:true 三重实证）→ ZeroTier 只能走 TCP 中继，实测 ~36KB/s 天花板。
# 每日备份档是全新 1GB tar.gz（文件名含日期，rsync 无法增量）= 中继下 ~8h/天结构性不可行。
# 改每周日只拉最新一份（kb 等其余模块每日增量几十 MB，中继速度够，保持每日）。
# 保留策略（用户决策 2026-08-30）: E 盘归档**全部长期保留**，不做本地剪枝（~+52GB/年由
# 所有者接受）。单文件拉取形态同时是保留的结构保证——绝不对 movespeed_backup 目录跑
# --delete 整目录镜像，否则超出 Mac 端 7 天轮转窗口的历史档会被删掉。
if [ "${OPENCLAW_SYNC_FORCE_BACKUP:-0}" = "1" ] || [ "$(date +%u)" = "7" ]; then
    LATEST_BACKUP=$(ssh $SSH_OPTS "$HOST" 'ls -t /Volumes/MOVESPEED/openclaw_backup/*.tar.gz 2>/dev/null | head -1' 2>/dev/null)
    if [ -n "$LATEST_BACKUP" ]; then
        run_rsync ssd_backup 10 "$LATEST_BACKUP" movespeed_backup
    else
        log "── [ssd_backup] 跳过: Mac 端未找到备份归档（/Volumes/MOVESPEED 可能未挂载）"
    fi
else
    log "── [ssd_backup] 跳过（每周日拉最新一份; OPENCLAW_SYNC_FORCE_BACKUP=1 可强制）"
fi

run_rsync sync_tool      20 'openclaw-model-bridge/windows/'     _sync/upstream

if [ -z "$FAILED" ]; then
    if ssh $SSH_OPTS "$HOST" "printf '{\"job\":\"windows_sync\",\"time\":\"%s\",\"status\":\"ok\"}\n' \"\$(date '+%Y-%m-%d %H:%M:%S')\" > ~/.kb/last_windows_sync.json" 2>/dev/null; then
        log "成功标记已回写 Mac (~/.kb/last_windows_sync.json)"
    else
        log "WARN: 成功标记回写 Mac 失败（不影响本地副本完整性）"
    fi
    write_status "$HOST" "" "true"
    log "════ 同步完成: 全部模块 OK ════"
    exit 0
else
    write_status "$HOST" "${FAILED# }" "false"
    log "════ 同步完成: 以下模块失败 —${FAILED} （详见本日志上方 rsync 输出）════"
    exit 1
fi
