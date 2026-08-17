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

# 清理过期备份（保留最近 N 天）
DELETED=$(find "$BACKUP_DIR" -name "openclaw-backup-*.tar.gz" -mtime +${KEEP_DAYS} -delete -print 2>/dev/null | wc -l | tr -d ' ')
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

# ── KB 完整性指纹更新（每日备份后自动刷新基线）──
if [ -f "$HOME/kb_integrity.py" ]; then
    python3 "$HOME/kb_integrity.py" --update >> "$LOG" 2>&1 || true
fi

echo "[$TIMESTAMP] === Backup done ===" >> "$LOG"
