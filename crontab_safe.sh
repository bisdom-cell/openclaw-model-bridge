#!/bin/bash
# crontab_safe.sh — 安全的 crontab 操作工具（V30新增）
# 目的：杜绝 `echo ... | crontab -` 意外清空 crontab 的事故
#
# 用法：
#   bash crontab_safe.sh add '*/10 * * * * bash ~/cron_canary.sh'   # 安全添加一行
#   bash crontab_safe.sh remove '<固定字符串 pattern>'                # V37.9.65 新增 — 安全删除匹配行
#   bash crontab_safe.sh backup                                      # 手动备份
#   bash crontab_safe.sh restore                                     # 从最新备份恢复
#   bash crontab_safe.sh restore 2026-03-25                          # 从指定日期恢复
#   bash crontab_safe.sh verify                                      # 验证条目数正常
#
# 安全机制：
#   1. 每次修改前自动备份到 ~/.crontab_backups/
#   2. add 后验证条目数 = 修改前 + 1, remove 后验证 = 修改前 - matched (不一致自动回滚)
#   3. remove 拒绝全清空操作 (pattern 匹配所有行时拒绝执行)
#   4. 保留最近 30 天备份
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:$PATH"
set -uo pipefail

BACKUP_DIR="$HOME/.crontab_backups"
mkdir -p "$BACKUP_DIR"

TS="$(TZ=Asia/Hong_Kong date '+%Y-%m-%d_%H%M%S')"
MIN_ENTRIES=5  # 低于此数量发出警告

# ── 备份当前 crontab ─────────────────────────────────────────────
do_backup() {
    local backup_file="$BACKUP_DIR/crontab_${TS}.bak"
    if crontab -l > "$backup_file" 2>/dev/null; then
        local count
        count=$(grep -v '^#' "$backup_file" | grep -v '^$' | wc -l | tr -d ' ')
        echo "[crontab_safe] 已备份到 ${backup_file} (${count} 条活跃条目)"
        # 同时维护一个 latest 软链接
        ln -sf "$backup_file" "$BACKUP_DIR/latest.bak"
        return 0
    else
        echo "[crontab_safe] 当前 crontab 为空或不可读，跳过备份"
        return 1
    fi
}

# ── 清理旧备份（保留 30 天）───────────────────────────────────────
cleanup_old_backups() {
    find "$BACKUP_DIR" -name "crontab_*.bak" -mtime +30 -delete 2>/dev/null || true
}

# ── 计算活跃条目数 ───────────────────────────────────────────────
count_entries() {
    crontab -l 2>/dev/null | grep -v '^#' | grep -v '^$' | wc -l | tr -d ' '
}

# ── add: 安全添加一行 ────────────────────────────────────────────
cmd_add() {
    local new_line="$1"

    if [ -z "$new_line" ]; then
        echo "❌ 用法: bash crontab_safe.sh add '<cron表达式>'"
        exit 1
    fi

    # 检查是否已存在（避免重复）
    if crontab -l 2>/dev/null | grep -qF "$new_line"; then
        echo "[crontab_safe] 条目已存在，跳过添加"
        crontab -l 2>/dev/null | grep -F "$new_line"
        return 0
    fi

    # 备份当前状态
    local count_before
    count_before=$(count_entries)
    do_backup

    # 安全添加：先写临时文件，验证后再安装
    local tmp_file
    tmp_file=$(mktemp /tmp/crontab_safe.XXXXXX)
    crontab -l > "$tmp_file" 2>/dev/null || true
    echo "$new_line" >> "$tmp_file"

    # V37.9.18: 安装新 crontab — 严格检查退出码（kb_deep_dive 血案修复）
    # 之前: crontab 拒绝（如 "bad minute"）后退出码未检查，count 比较用 < 让 35→35 漏过 → 谎报 ✅
    if ! crontab "$tmp_file" 2>&1; then
        local rc=$?
        rm -f "$tmp_file"
        echo "❌ crontab 安装失败（语法错误或 cron 拒绝）— 退出码: $rc"
        echo "   原始尝试添加: $new_line"
        echo "   提示: cron 时间字段必须是 'min hour day month weekday'，例如 '30 22 * * *'"
        exit 1
    fi
    rm -f "$tmp_file"

    # V37.9.18: 严格相等验证（之前用 -lt 让 35→35 仍打 ✅，谎报成功）
    local count_after
    count_after=$(count_entries)
    local expected=$((count_before + 1))

    if [ "$count_after" -ne "$expected" ]; then
        echo "❌ 严重错误：预期 $expected 条但实际 $count_after 条（之前 $count_before 条），自动回滚！"
        cmd_restore
        exit 1
    fi

    echo "✅ 已添加（$count_before → $count_after 条）："
    echo "   $new_line"
}

# ── remove: 安全删除匹配行 (V37.9.65 — convergence framework 双向 sync 下半截) ──
# 用 grep -F (固定字符串) 匹配, pattern 出现在行内任何位置都会被删
# 安全机制: backup + 严格 count 验证 (count_before - matched) + 拒绝全清空 + 失败自动回滚
cmd_remove() {
    local pattern="$1"

    if [ -z "$pattern" ]; then
        echo "❌ 用法: bash crontab_safe.sh remove '<固定字符串 pattern>'"
        echo "   示例: bash crontab_safe.sh remove \"0 8 * * * bash -lc 'bash ~/jobs/freight_watcher/run_freight.sh\""
        echo "   注意: pattern 用 grep -F (固定字符串) 匹配, 出现在行内任何位置都被删除"
        echo "         所有匹配行都会被删除 — pattern 必须精确以避免误删"
        echo "         拒绝全清空操作 (pattern 匹配所有行时拒绝执行)"
        exit 1
    fi

    # 计算匹配行数 (grep -c 已输出 0 当无匹配, || true 仅安抚 pipefail 不再额外 echo)
    local matched_count
    matched_count=$(crontab -l 2>/dev/null | grep -cF -- "$pattern" || true)
    matched_count=$(echo "$matched_count" | tr -d ' \n')

    if [ "$matched_count" -eq 0 ]; then
        echo "[crontab_safe] 未找到匹配 '$pattern' 的行, 跳过"
        return 0
    fi

    echo "[crontab_safe] 匹配到 $matched_count 行将被删除:"
    crontab -l 2>/dev/null | grep -F -- "$pattern" | sed 's/^/   - /'

    # 备份当前状态
    local count_before
    count_before=$(count_entries)
    do_backup

    # 安全删除: 写临时文件
    local tmp_file
    tmp_file=$(mktemp /tmp/crontab_safe.XXXXXX)
    crontab -l 2>/dev/null | grep -vF -- "$pattern" > "$tmp_file" || true

    # 强制保护: 拒绝全清空 (pattern 匹配所有行)
    local new_active_count
    new_active_count=$(grep -v '^#' "$tmp_file" | grep -v '^$' | wc -l | tr -d ' ')
    if [ "$new_active_count" -eq 0 ]; then
        rm -f "$tmp_file"
        echo "❌ 拒绝操作: 删除后 crontab 将完全清空 (pattern '$pattern' 匹配所有活跃行)"
        echo "   提示: 用更精确的 pattern 只匹配目标行"
        exit 1
    fi

    # 安装 + 严格退出码检查
    if ! crontab "$tmp_file" 2>&1; then
        local rc=$?
        rm -f "$tmp_file"
        echo "❌ crontab 安装失败 — 退出码: $rc"
        exit 1
    fi
    rm -f "$tmp_file"

    # 严格相等验证 (cmd_add 同款契约 — 防 35→35 谎报 ✅ 类血案)
    local count_after
    count_after=$(count_entries)
    local expected=$((count_before - matched_count))

    if [ "$count_after" -ne "$expected" ]; then
        echo "❌ 严重错误: 预期 $expected 条但实际 $count_after 条 (之前 $count_before, 应删 $matched_count), 自动回滚！"
        cmd_restore
        exit 1
    fi

    echo "✅ 已删除 $matched_count 条 (${count_before} → ${count_after} 条)"
}

# ── backup: 手动备份 ─────────────────────────────────────────────
cmd_backup() {
    do_backup
    cleanup_old_backups

    local count
    count=$(count_entries)
    if [ "$count" -lt "$MIN_ENTRIES" ]; then
        echo "⚠️  警告：当前只有 ${count} 条活跃条目 (预期 >= ${MIN_ENTRIES})"
    fi
}

# ── restore: 从备份恢复 ──────────────────────────────────────────
cmd_restore() {
    local target_date="${1:-}"
    local restore_file=""

    if [ -n "$target_date" ]; then
        # 查找指定日期的最新备份
        restore_file=$(ls -t "$BACKUP_DIR"/crontab_${target_date}*.bak 2>/dev/null | head -1)
    else
        # 使用最新备份
        restore_file="$BACKUP_DIR/latest.bak"
        if [ -L "$restore_file" ]; then
            restore_file=$(readlink "$restore_file")
        fi
    fi

    if [ ! -f "$restore_file" ]; then
        echo "❌ 找不到备份文件"
        echo "可用备份："
        ls -lt "$BACKUP_DIR"/*.bak 2>/dev/null | head -10 | awk '{print "  " $NF}'
        exit 1
    fi

    local restore_count
    restore_count=$(grep -v '^#' "$restore_file" | grep -v '^$' | wc -l | tr -d ' ')

    echo "[crontab_safe] 从 $restore_file 恢复（$restore_count 条活跃条目）"
    crontab "$restore_file"
    echo "✅ 已恢复"
}

# ── verify: 验证条目数 ──────────────────────────────────────────
cmd_verify() {
    local count
    count=$(count_entries)

    echo "[crontab_safe] 当前 $count 条活跃条目"

    if [ "$count" -eq 0 ]; then
        echo "❌ crontab 为空！"
        echo "恢复：bash crontab_safe.sh restore"
        exit 1
    elif [ "$count" -lt "$MIN_ENTRIES" ]; then
        echo "⚠️  条目数过少 (预期 >= ${MIN_ENTRIES})，可能被意外清空"
        echo "最新备份："
        ls -lt "$BACKUP_DIR"/*.bak 2>/dev/null | head -3 | awk '{print "  " $NF}'
        exit 1
    else
        echo "✅ 条目数正常"
    fi
}

# ── 主入口 ───────────────────────────────────────────────────────
case "${1:-help}" in
    add)
        cmd_add "${2:-}"
        ;;
    remove)
        cmd_remove "${2:-}"
        ;;
    backup)
        cmd_backup
        ;;
    restore)
        cmd_restore "${2:-}"
        ;;
    verify)
        cmd_verify
        ;;
    *)
        echo "crontab_safe.sh — 安全的 crontab 操作工具"
        echo ""
        echo "用法："
        echo "  bash crontab_safe.sh add '<cron行>'                 安全添加（自动备份+验证）"
        echo "  bash crontab_safe.sh remove '<固定字符串 pattern>'  V37.9.65 安全删除匹配行（拒绝全清空）"
        echo "  bash crontab_safe.sh backup                         手动备份"
        echo "  bash crontab_safe.sh restore [日期]                 从备份恢复"
        echo "  bash crontab_safe.sh verify                         验证条目数"
        echo ""
        echo "⚠️  禁止使用 'echo ... | crontab -'（会清空所有条目）"
        echo "    始终使用本工具操作 cron 条目"
        ;;
esac
