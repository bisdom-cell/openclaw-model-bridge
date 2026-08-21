#!/bin/bash
# openclaw_backup.sh — 每日自动备份 OpenClaw state 到外挂 SSD
# 备份内容：config, credentials, sessions, memory（不含 workspace）
# 保留最近 7 天的备份，自动清理过期文件
# V37.9.310 (🔴 2026-08-15~17 备份中断根因): PATH 必须含 /sbin —— macOS 的 mount 在
# /sbin/mount, 旧 PATH 不含 /sbin → 脚本内 `mount` command-not-found → 管道失败 →
# V37.9.304 的挂载检测恒判"未挂载" → 备份连续静默跳过 3 天。
# dev(Linux) 的 mount 在 /usr/bin, 落在旧 PATH 内 → dev 测试全绿 = dev-production 接缝。
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:/usr/bin:/bin:/sbin:/usr/sbin"

BACKUP_DIR="/Volumes/MOVESPEED/openclaw_backup"
LOG="$HOME/openclaw_backup.log"
DATE=$(date '+%Y-%m-%d')
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')
KEEP_DAYS=7
# V37.9.314 (审计余项 h): 保留策略加最少份数下限 —— 纯 age-based 的隐患在 2026-08-15~17
# 中断事件里现形: 清理只在成功备份后运行, 若中断 8 天, 恢复当晚会一次性删光所有 >7 天
# 的档, 只剩当天 1 份 (中断越久剩得越少, 恰好反了)。无论多旧, 最新 MIN_KEEP 份永不删。
MIN_KEEP=3
# V37.9.314 (审计余项 h): 产物校验阈值 —— `openclaw backup create` rc=0 不代表产物完好
# (2026-08-17 实证 rc=0 但产物在别的目录; 同理 0 字节/截断档也会 rc=0 混进备份集)。
# 正常档 ~1.0G, 阈值取 1MB 只挡"明显坏档", 不误伤配置缩水。
MIN_ARCHIVE_BYTES=1048576

# 产物校验: 存在 + 尺寸下限 + gzip/tar 结构可读。失败打 ERROR (匹配 watchdog
# err_pattern) 并让调用方 exit 4 —— 坏档必须当天可见, 不能计入备份集充数。
verify_archive() {
    local f="$1" sz
    if [ ! -f "$f" ]; then
        echo "[$TIMESTAMP] ERROR: verify failed — 产物不存在: $f" >> "$LOG"
        return 1
    fi
    sz=$(wc -c < "$f" | tr -d ' ')
    if [ -z "$sz" ] || [ "$sz" -lt "$MIN_ARCHIVE_BYTES" ]; then
        echo "[$TIMESTAMP] ERROR: verify failed — 产物过小 (${sz:-0} bytes < $MIN_ARCHIVE_BYTES): $f" >> "$LOG"
        return 1
    fi
    if ! tar -tzf "$f" >/dev/null 2>&1; then
        echo "[$TIMESTAMP] ERROR: verify failed — gzip/tar 结构损坏: $f" >> "$LOG"
        return 1
    fi
    return 0
}

# 保留清理: 最新 MIN_KEEP 份无条件保留, 其余按 KEEP_DAYS 天龄删除。stdout 输出删除数。
prune_old_backups() {
    local dir="$1" deleted=0 f
    while IFS= read -r f; do
        [ -z "$f" ] && continue
        if [ -n "$(find "$f" -mtime +"$KEEP_DAYS" 2>/dev/null)" ]; then
            rm -f "$f" && deleted=$((deleted + 1))
        fi
    done <<PRUNE_EOF
$(ls -t "$dir"/openclaw-backup-*.tar.gz 2>/dev/null | tail -n +$((MIN_KEEP + 1)))
PRUNE_EOF
    echo "$deleted"
}

echo "[$TIMESTAMP] === Backup start ===" >> "$LOG"

# 检查 SSD 是否挂载
# V37.9.304 (对抗审计 C7): 查 mount 表而非仅 -d — 不洁弹出可留本地幽灵目录,
# -d 永真后备份写进启动盘 (真 SSD 再插入被挂 "/Volumes/MOVESPEED 1"), 离线备份
# 实际不在离线介质上且全绿。精确匹配 " on /Volumes/MOVESPEED (" 防松匹配误认。
# V37.9.309: 两个条件拆开报 —— V37.9.304 合并成一条 "(dir or mount-table check failed)",
# 看日志根本分不清是「SSD 没插」还是「有幽灵目录但没真挂载」, 2026-08-15 起连续告警时
# 只能让人手动去 Mac Mini 敲 mount 命令才能定性。诊断信息属于告警本身的一部分。
if [ ! -d "/Volumes/MOVESPEED" ]; then
    echo "[$TIMESTAMP] ERROR: SSD not mounted (目录 /Volumes/MOVESPEED 不存在 — SSD 未插入或已干净卸载), skip backup" >> "$LOG"
    exit 1
fi
# V37.9.310: 工具缺失必须与"没挂载"可区分 —— 这正是 8-15~17 中断难定性的原因:
# mount 找不到时管道失败, 读起来和"SSD 未挂载"一模一样 (同 V37.9.288「搜索坏了 ≠ 没搜到」)。
if ! command -v mount >/dev/null 2>&1; then
    echo "[$TIMESTAMP] ERROR: mount 命令不可用 (PATH=$PATH) — 无法验证挂载, skip backup" >> "$LOG"
    exit 1
fi
if ! mount | grep -q " on /Volumes/MOVESPEED ("; then
    MOUNT_LINES=$(mount | grep -i movespeed | tr '\n' ';' || true)
    if [ -z "$MOUNT_LINES" ]; then
        MOUNT_LINES="(mount 表中无任何 MOVESPEED 行)"
    fi
    echo "[$TIMESTAMP] ERROR: SSD not mounted (目录存在但不在 mount 表 — 幽灵目录/不洁弹出), skip backup" >> "$LOG"
    echo "[$TIMESTAMP] ERROR: mount 表实况: $MOUNT_LINES" >> "$LOG"
    exit 1
fi

# 创建备份目录
mkdir -p "$BACKUP_DIR"

# 执行 openclaw backup create
BACKUP_FILE="$BACKUP_DIR/openclaw-backup-${DATE}.tar.gz"
if openclaw backup create --no-include-workspace --output "$BACKUP_FILE" >> "$LOG" 2>&1; then
    # V37.9.314 (h): rc=0 ≠ 产物完好, 校验后才打 OK
    if ! verify_archive "$BACKUP_FILE"; then
        exit 4
    fi
    SIZE=$(du -h "$BACKUP_FILE" 2>/dev/null | cut -f1)
    echo "[$TIMESTAMP] OK: $BACKUP_FILE ($SIZE)" >> "$LOG"
else
    # fallback: --output 失败时改用默认落盘位置
    # V37.9.311 (2026-08-17 Mac Mini 实证): 4.27 的 `openclaw backup create` 不带
    # --output 时写到**当前工作目录**, 文件名 `<ISO8601>-openclaw-backup.tar.gz` ——
    # 不是旧代码假设的 ~/.openclaw/backups/。该错误假设造成两个真实后果:
    #   (1) fallback 永远落到 "backup created but file not found" + exit 2 = 假失败告警,
    #       而备份其实已经生成了;
    #   (2) 文件落在 cron 的 CWD (= 仓库目录), 留下 1GB 未被 gitignore 的孤儿档, 且内含
    #       ~/.openclaw 凭据 → 误提交即凭据泄漏。
    # 修法: 让 CLI 直接在 SSD 的 BACKUP_DIR 里生成 (subshell cd, 不影响主流程), 再 mv
    # 成规范名 —— 同盘 mv 是秒级改名, 不占双份空间, 且永不污染 CWD。
    # glob 用 `*-openclaw-backup.tar.gz` 精确匹配 CLI 的时间戳命名, 不会撞上规范名
    # `openclaw-backup-<date>.tar.gz` (后者结尾是 -<date>.tar.gz)。
    echo "[$TIMESTAMP] WARN: --output failed, trying default location" >> "$LOG"
    if (cd "$BACKUP_DIR" && openclaw backup create --no-include-workspace) >> "$LOG" 2>&1; then
        LATEST=$(ls -t "$BACKUP_DIR"/*-openclaw-backup.tar.gz 2>/dev/null | head -1)
        if [ -n "$LATEST" ]; then
            # V37.9.304 (对抗审计 C6): 退出码检查 — 失败时曾无条件打 "OK (copied)"
            # (SIZE 为空照样 OK), 备份静默未发生而日志说发生了
            if ! mv "$LATEST" "$BACKUP_FILE"; then
                echo "[$TIMESTAMP] ERROR: mv to canonical name failed (disk full/IO error?)" >> "$LOG"
                exit 2
            fi
            # V37.9.314 (h): fallback 产物同样校验
            if ! verify_archive "$BACKUP_FILE"; then
                exit 4
            fi
            SIZE=$(du -h "$BACKUP_FILE" 2>/dev/null | cut -f1)
            echo "[$TIMESTAMP] OK (fallback): $BACKUP_FILE ($SIZE)" >> "$LOG"
        else
            echo "[$TIMESTAMP] ERROR: backup created but file not found in $BACKUP_DIR" >> "$LOG"
            exit 2
        fi
    else
        echo "[$TIMESTAMP] ERROR: openclaw backup create failed" >> "$LOG"
        exit 3
    fi
fi

# 清理过期备份（保留最近 N 天, 且最新 MIN_KEEP 份永不删 — V37.9.314 h）
DELETED=$(prune_old_backups "$BACKUP_DIR")
if [ "$DELETED" -gt 0 ]; then
    echo "[$TIMESTAMP] Cleaned $DELETED old backup(s)" >> "$LOG"
fi

# ── status.json 独立版本备份（三方共享状态是核心，单独保留 30 天历史）──
STATUS_SRC="$HOME/.kb/status.json"
STATUS_BACKUP_DIR="$BACKUP_DIR/status_history"
if [ -f "$STATUS_SRC" ]; then
    mkdir -p "$STATUS_BACKUP_DIR"
    # V37.9.304 (对抗审计 C6): cp 退出码检查 — 失败曾无条件打 "backed up",
    # 三方共享状态 30 天历史静默断档数周 (ERROR 行匹配 watchdog err_pattern 可见)
    if cp "$STATUS_SRC" "$STATUS_BACKUP_DIR/status_${DATE}.json"; then
        echo "[$TIMESTAMP] status.json backed up to $STATUS_BACKUP_DIR/" >> "$LOG"
    else
        echo "[$TIMESTAMP] ERROR: status.json backup cp failed (disk full/IO error?)" >> "$LOG"
    fi
    # 清理超过 30 天的历史
    find "$STATUS_BACKUP_DIR" -name "status_*.json" -mtime +30 -delete 2>/dev/null || true
fi

# ── KB 完整性校验 + 指纹更新（V37.9.324 对抗审计）────────────────────
# 🔴 顺序是 load-bearing: 先 verify（比对昨天的基线）再 --update（重设基线）。
# 血案: 生产唯一的调用一直只有 --update —— 检测那一半从未运行过，而每日重设基线
# 会把灾难静默吸收进新基线。探针实证 notes/ 2627 → 0 的当晚跑完 --update 后，
# 次日 verify 报「✅ 所有关键文件完好」exit 0。7 个既有单测全绿，因为它们断言的
# 是「源码里有『已消失』字样」而不是「这段代码会被执行」(V37.9.320 SS-1 家族)。
# 可见性: verify 自身输出（含 ❌/🚨 明细）刻意不匹配 watchdog err_pattern，避免每日
# 「📝 已变更」正常行误报；只有 rc≠0 时显式打一行 ERROR: 让 openclaw_backup.log 的
# 既有错误扫描接住（零新机器，镜像 V37.9.292 per-file 不匹配 / run 级匹配的契约）。
# 备份自身的退出码不受影响 —— 备份成功与 KB 有完整性告警是两件事、两种处置。
if [ -f "$HOME/kb_integrity.py" ]; then
    KBI_RC=0
    python3 "$HOME/kb_integrity.py" >> "$LOG" 2>&1 || KBI_RC=$?
    if [ "$KBI_RC" -ne 0 ]; then
        echo "[$TIMESTAMP] ERROR: KB 完整性校验告警 (rc=$KBI_RC) — 详见上方校验段" >> "$LOG"
    fi
    python3 "$HOME/kb_integrity.py" --update >> "$LOG" 2>&1 || true
fi

echo "[$TIMESTAMP] === Backup done ===" >> "$LOG"
