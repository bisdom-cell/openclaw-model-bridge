#!/bin/bash
# job_smoke_test.sh — 全量定时任务 smoke test
# 检查所有 20 个启用的 job：脚本存在性 / crontab 注册 / 最近执行 / 日志健康 / 输出文件
# 用法：bash job_smoke_test.sh          （Mac Mini 上运行）
# 注意：不会真正执行 job，只做被动检查
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:$PATH"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TS=$(date '+%Y-%m-%d %H:%M:%S')
NOW_EPOCH=$(date +%s)

PASS=0
FAIL=0
WARN=0

pass() { echo "  ✅ $1"; PASS=$((PASS + 1)); }
fail() { echo "  ❌ $1"; FAIL=$((FAIL + 1)); }
warn() { echo "  ⚠️  $1"; WARN=$((WARN + 1)); }

echo "╔══════════════════════════════════════════════════════╗"
echo "║     Job Smoke Test — 全量定时任务健康检查            ║"
echo "║     $TS                            ║"
echo "╚══════════════════════════════════════════════════════╝"
echo ""

# ── 从 registry 解析所有启用的 job ──
JOBS=$(python3 - "$SCRIPT_DIR/jobs_registry.yaml" << 'PYEOF'
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(sys.argv[1])))
try:
    import yaml
    with open(sys.argv[1]) as f:
        data = yaml.safe_load(f)
except ImportError:
    sys.path.insert(0, os.path.dirname(sys.argv[1]))
    from check_registry import load_yaml
    data = load_yaml(sys.argv[1])

for j in data.get('jobs', []):
    if j.get('enabled', False):
        # id|entry|log|interval|needs_api_key|description
        log = j.get('log', '').replace('~/', os.path.expanduser('~/'))
        print(f"{j['id']}|{j['entry']}|{log}|{j['interval']}|{j.get('needs_api_key', False)}|{j.get('description', '')}")
PYEOF
)

TOTAL=0
CRONTAB=$(crontab -l 2>/dev/null || echo "")

while IFS='|' read -r job_id entry log_path interval needs_key description; do
    [ -z "$job_id" ] && continue
    TOTAL=$((TOTAL + 1))
    echo "━━━ [$TOTAL] $job_id ━━━"
    echo "  📝 $description"
    ISSUES=0

    # ── 1. 脚本文件存在性 ──
    # 检查 FILE_MAP 目标路径和仓库路径
    REPO_PATH="$SCRIPT_DIR/$entry"
    if [ -f "$REPO_PATH" ]; then
        pass "仓库文件存在: $entry"
    else
        fail "仓库文件不存在: $entry"
        ISSUES=$((ISSUES + 1))
    fi

    # ── 2. Crontab 注册检查 ──
    entry_basename=$(basename "$entry")
    if echo "$CRONTAB" | grep -q "$entry_basename"; then
        CRON_LINE=$(echo "$CRONTAB" | grep "$entry_basename" | head -1)
        pass "crontab 已注册"
    else
        fail "crontab 中未找到 $entry_basename"
        ISSUES=$((ISSUES + 1))
    fi

    # ── 3. 运行时脚本存在性（crontab 实际指向的路径）──
    CRON_LINE=""
    if echo "$CRONTAB" | grep -q "$entry_basename"; then
        CRON_LINE=$(echo "$CRONTAB" | grep "$entry_basename" | head -1)
    fi
    if [ -n "$CRON_LINE" ]; then
        # crontab 格式: ... bash -lc 'bash ~/path/script.sh >> log'
        # 提取最后一个 bash 后面的脚本路径（跳过 bash -lc）
        RUNTIME_PATH=$(echo "$CRON_LINE" | grep -oE "bash [^'\"]+\.sh" | tail -1 | sed 's/^bash //' | sed "s|~/|$HOME/|g" | sed "s|\\\$HOME/|$HOME/|g")
        if [ -n "$RUNTIME_PATH" ] && [ -f "$RUNTIME_PATH" ]; then
            pass "运行时文件存在: $RUNTIME_PATH"
        elif [ -n "$RUNTIME_PATH" ]; then
            fail "运行时文件不存在: $RUNTIME_PATH"
            ISSUES=$((ISSUES + 1))
        fi
    fi

    # ── 4. 日志文件检查 ──
    LOG_EXPANDED=$(echo "$log_path" | sed "s|~/|$HOME/|g" | sed "s|\$HOME/|$HOME/|g")
    if [ -f "$LOG_EXPANDED" ]; then
        LOG_SIZE=$(wc -c < "$LOG_EXPANDED" 2>/dev/null | tr -d ' ')
        # 检查日志最后修改时间
        if [ "$(uname)" = "Darwin" ]; then
            LOG_EPOCH=$(stat -f %m "$LOG_EXPANDED" 2>/dev/null || echo "0")
        else
            LOG_EPOCH=$(stat -c %Y "$LOG_EXPANDED" 2>/dev/null || echo "0")
        fi
        LOG_AGE_H=$(( (NOW_EPOCH - LOG_EPOCH) / 3600 ))

        # 根据频率判断日志是否过期
        MAX_AGE=168  # 默认 7 天
        case "$interval" in
            "*/2 * * * *")   MAX_AGE=1 ;;    # 每2分钟
            "*/10 * * * *")  MAX_AGE=1 ;;    # 每10分钟
            "*/30 * * * *")  MAX_AGE=2 ;;    # 每30分钟
            *"*/3 * * *")    MAX_AGE=6 ;;    # 每3小时
            *"*/2 * * *")    MAX_AGE=4 ;;    # 每2小时
            *"*/4 * * *")    MAX_AGE=8 ;;    # 每4小时
            "0 * * * *"|"15 * * * *"|"30 * * * *") MAX_AGE=3 ;;  # 每小时
            *"* * *")        MAX_AGE=48 ;;   # 每天
            *"* * 1"|*"* * 5"|*"* * 6") MAX_AGE=192 ;; # 每周
        esac

        if [ "$LOG_AGE_H" -le "$MAX_AGE" ]; then
            pass "日志活跃（${LOG_AGE_H}h 前, ${LOG_SIZE}B）"
        else
            warn "日志陈旧（${LOG_AGE_H}h 前，预期 <${MAX_AGE}h）"
        fi

        # 检查最近日志中的错误
        RECENT_ERRORS=$(tail -50 "$LOG_EXPANDED" 2>/dev/null | grep -ciE "ERROR|FAIL|traceback" 2>/dev/null || echo "0")
        RECENT_ERRORS=$(echo "$RECENT_ERRORS" | tr -d '[:space:]')
        RECENT_ERRORS=${RECENT_ERRORS:-0}
        if [ "$RECENT_ERRORS" -gt 0 ] 2>/dev/null; then
            warn "最近日志有 $RECENT_ERRORS 处错误"
            tail -50 "$LOG_EXPANDED" 2>/dev/null | grep -iE "ERROR|FAIL" | tail -2 | while read -r err_line; do
                echo "      $(echo "$err_line" | cut -c1-120)"
            done
        else
            pass "最近日志无错误"
        fi
    elif [ -n "$LOG_EXPANDED" ]; then
        warn "日志文件不存在: $LOG_EXPANDED"
    fi

    # ── 5. 状态文件检查（如果有 last_run / status 文件）──
    # 常见的状态文件模式
    for status_file in \
        "$HOME/.openclaw/jobs/$(echo "$job_id" | sed 's/_watcher//')/cache/last_run.json" \
        "$HOME/.openclaw/jobs/${job_id}/cache/last_run.json" \
        "$HOME/.kb/last_run_${job_id}.json"; do
        if [ -f "$status_file" ]; then
            STATUS=$(python3 -c "
import json
try:
    d = json.load(open('$status_file'))
    status = d.get('status', 'unknown')
    time = d.get('time', '')
    print(f'{status}|{time}')
except Exception as e:
    print(f'error|{e}')
" 2>/dev/null || echo "error|parse failed")
            S_STATUS="${STATUS%%|*}"
            S_TIME="${STATUS##*|}"
            case "$S_STATUS" in
                ok)          pass "状态: $S_STATUS ($S_TIME)" ;;
                send_failed) warn "状态: 推送失败 ($S_TIME)" ;;
                fetch_failed) warn "状态: 抓取失败 ($S_TIME)" ;;
                *)           warn "状态: $S_STATUS ($S_TIME)" ;;
            esac
            break
        fi
    done

    # ── 6. 锁文件检查 ──
    LOCK_DIR="/tmp/${job_id}.lockdir"
    if [ -d "$LOCK_DIR" ]; then
        if [ "$(uname)" = "Darwin" ]; then
            LOCK_EPOCH=$(stat -f %m "$LOCK_DIR" 2>/dev/null || echo "0")
        else
            LOCK_EPOCH=$(stat -c %Y "$LOCK_DIR" 2>/dev/null || echo "0")
        fi
        LOCK_AGE=$(( (NOW_EPOCH - LOCK_EPOCH) / 60 ))
        if [ "$LOCK_AGE" -gt 60 ]; then
            fail "陈旧锁: $LOCK_DIR（${LOCK_AGE}min）"
        else
            pass "锁正常: $LOCK_DIR（${LOCK_AGE}min）"
        fi
    fi

    echo ""
done <<< "$JOBS"

# ── 额外检查：KB 数据完整性 ──
echo "━━━ KB 数据完整性 ━━━"
for kb_file in \
    "$HOME/.kb/index.json" \
    "$HOME/.kb/status.json" \
    "$HOME/.kb/daily_digest.md" \
    "$HOME/.kb/sources/arxiv_daily.md" \
    "$HOME/.kb/sources/hn_daily.md" \
    "$HOME/.kb/sources/freight_daily.md" \
    "$HOME/.kb/sources/openclaw_official.md"; do
    if [ -f "$kb_file" ]; then
        SIZE=$(wc -c < "$kb_file" 2>/dev/null | tr -d ' ')
        if [ "$SIZE" -gt 0 ]; then
            pass "$(basename "$kb_file") (${SIZE}B)"
        else
            warn "$(basename "$kb_file") 为空"
        fi
    else
        warn "$(basename "$kb_file") 不存在"
    fi
done

echo ""

# ── KB 来源新鲜度检查 ──
echo "━━━ KB 来源新鲜度 ━━━"
for src_entry in \
    "arxiv_daily.md|48|ArXiv 论文" \
    "hn_daily.md|48|HN 热帖" \
    "freight_daily.md|48|货代动态" \
    "openclaw_official.md|168|OpenClaw 更新"; do
    IFS='|' read -r src_file max_hours src_label <<< "$src_entry"
    src_path="$HOME/.kb/sources/$src_file"
    if [ -f "$src_path" ]; then
        if [ "$(uname)" = "Darwin" ]; then
            SRC_EPOCH=$(stat -f %m "$src_path" 2>/dev/null || echo "0")
        else
            SRC_EPOCH=$(stat -c %Y "$src_path" 2>/dev/null || echo "0")
        fi
        SRC_AGE_H=$(( (NOW_EPOCH - SRC_EPOCH) / 3600 ))
        if [ "$SRC_AGE_H" -le "$max_hours" ]; then
            pass "$src_label: ${SRC_AGE_H}h 前更新"
        else
            warn "$src_label: ${SRC_AGE_H}h 未更新（预期 <${max_hours}h）"
        fi
    else
        warn "$src_label: 来源文件不存在"
    fi
done

echo ""

# ── KB JSON 结构验证 ──
echo "━━━ KB JSON 结构验证 ━━━"
# index.json 解析 + 条目数
if [ -f "$HOME/.kb/index.json" ]; then
    IDX_CHECK=$(python3 -c "
import json
try:
    with open('$HOME/.kb/index.json') as f:
        d = json.load(f)
    entries = d.get('entries', [])
    if len(entries) > 0:
        # 抽检最新条目有必要字段
        latest = entries[-1]
        has_fields = all(k in latest for k in ['date', 'tags'])
        print(f'OK|{len(entries)} 条，结构正常' if has_fields else f'WARN|{len(entries)} 条，最新条目缺少字段')
    else:
        print('WARN|entries 为空')
except json.JSONDecodeError as e:
    print(f'FAIL|JSON 解析失败: {e}')
except Exception as e:
    print(f'FAIL|{e}')
" 2>/dev/null || echo "FAIL|Python 执行失败")
    case "${IDX_CHECK%%|*}" in
        OK) pass "index.json: ${IDX_CHECK#*|}" ;;
        WARN) warn "index.json: ${IDX_CHECK#*|}" ;;
        FAIL) fail "index.json: ${IDX_CHECK#*|}" ;;
    esac
fi

# status.json 解析 + 必要字段
if [ -f "$HOME/.kb/status.json" ]; then
    STS_CHECK=$(python3 -c "
import json
try:
    with open('$HOME/.kb/status.json') as f:
        d = json.load(f)
    required = ['priorities', 'recent_changes', 'feedback', 'health']
    missing = [k for k in required if k not in d]
    if missing:
        print(f'WARN|缺少字段: {\", \".join(missing)}')
    else:
        print(f'OK|{len(required)} 个必要字段完整')
except json.JSONDecodeError as e:
    print(f'FAIL|JSON 损坏: {e}')
except Exception as e:
    print(f'FAIL|{e}')
" 2>/dev/null || echo "FAIL|Python 执行失败")
    case "${STS_CHECK%%|*}" in
        OK) pass "status.json: ${STS_CHECK#*|}" ;;
        WARN) warn "status.json: ${STS_CHECK#*|}" ;;
        FAIL) fail "status.json: ${STS_CHECK#*|}" ;;
    esac
fi

echo ""

# ── 备份健康检查 ──
echo "━━━ 备份健康检查 ━━━"
SSD_BACKUP="/Volumes/MOVESPEED/openclaw_backup"
KB_BACKUP="/Volumes/MOVESPEED/KB"

# SSD 挂载检查
if [ -d "/Volumes/MOVESPEED" ]; then
    pass "外挂 SSD 已挂载"

    # SSD 可用空间
    SSD_AVAIL=$(df -h /Volumes/MOVESPEED 2>/dev/null | tail -1 | awk '{print $4}')
    SSD_USAGE=$(df /Volumes/MOVESPEED 2>/dev/null | tail -1 | awk '{print $5}' | tr -d '%')
    if [ -n "$SSD_USAGE" ] && [ "$SSD_USAGE" -lt 90 ] 2>/dev/null; then
        pass "SSD 可用空间: ${SSD_AVAIL}（使用 ${SSD_USAGE}%）"
    elif [ -n "$SSD_USAGE" ]; then
        warn "SSD 空间紧张: ${SSD_AVAIL}（使用 ${SSD_USAGE}%）"
    fi

    # Gateway state 备份新鲜度
    if [ -d "$SSD_BACKUP" ]; then
        LATEST_BK=$(ls -t "$SSD_BACKUP"/*.tar.gz 2>/dev/null | head -1)
        if [ -n "$LATEST_BK" ]; then
            if [ "$(uname)" = "Darwin" ]; then
                BK_EPOCH=$(stat -f %m "$LATEST_BK" 2>/dev/null || echo "0")
            else
                BK_EPOCH=$(stat -c %Y "$LATEST_BK" 2>/dev/null || echo "0")
            fi
            BK_AGE_H=$(( (NOW_EPOCH - BK_EPOCH) / 3600 ))
            BK_SIZE=$(du -h "$LATEST_BK" 2>/dev/null | cut -f1)
            if [ "$BK_AGE_H" -le 26 ]; then
                pass "Gateway 备份: ${BK_AGE_H}h 前（${BK_SIZE}）"
            else
                warn "Gateway 备份过期: ${BK_AGE_H}h 前（预期 <26h）"
            fi
        else
            warn "无 Gateway 备份文件"
        fi
    else
        warn "Gateway 备份目录不存在: $SSD_BACKUP"
    fi

    # KB 备份新鲜度
    if [ -d "$KB_BACKUP" ]; then
        if [ "$(uname)" = "Darwin" ]; then
            KB_BK_EPOCH=$(stat -f %m "$KB_BACKUP/index.json" 2>/dev/null || echo "0")
        else
            KB_BK_EPOCH=$(stat -c %Y "$KB_BACKUP/index.json" 2>/dev/null || echo "0")
        fi
        KB_BK_AGE_H=$(( (NOW_EPOCH - KB_BK_EPOCH) / 3600 ))
        if [ "$KB_BK_AGE_H" -le 26 ]; then
            pass "KB 备份: ${KB_BK_AGE_H}h 前"
        else
            warn "KB 备份过期: ${KB_BK_AGE_H}h 前（预期 <26h）"
        fi
    else
        warn "KB 备份目录不存在: $KB_BACKUP"
    fi
else
    warn "外挂 SSD 未挂载（/Volumes/MOVESPEED）"
fi

echo ""

# ── 额外检查：crontab 条目数 ──
echo "━━━ Crontab 完整性 ━━━"
CRON_COUNT=$(echo "$CRONTAB" | grep -c '[^ ]' || echo "0")
if [ "$CRON_COUNT" -ge 15 ]; then
    pass "crontab 条目数: $CRON_COUNT（健康）"
else
    fail "crontab 条目数: $CRON_COUNT（预期 >= 15，可能被清空）"
fi

echo ""
echo "══════════════════════════════════════════════════════"
echo "  任务数: $TOTAL | 通过: $PASS | 失败: $FAIL | 警告: $WARN"
echo "══════════════════════════════════════════════════════"

if [ $FAIL -gt 0 ]; then
    echo ""
    echo "❌ SMOKE TEST FAILED: $FAIL 项需要修复"
    exit 1
elif [ $WARN -gt 0 ]; then
    echo ""
    echo "⚠️  PASSED WITH WARNINGS: 建议检查 $WARN 条警告"
    exit 0
else
    echo ""
    echo "✅ ALL JOBS HEALTHY"
    exit 0
fi
