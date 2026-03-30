#!/bin/bash
# job_watchdog.sh — 元监控：检查所有定时任务是否按时执行
# 每小时由系统 crontab 触发，检查各 job 的 last_run.json 时间戳
# 如果任何 job 超过预期间隔的 2 倍仍未更新，发送 WhatsApp 告警
# V30: + 陈旧锁文件自动清理 + cron 心跳检测
# cron 环境 PATH 极简，必须显式声明（规则 #13）
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:$PATH"
set -eo pipefail

# 防重叠执行（mkdir 原子锁，macOS 兼容）
# V30: 自身锁文件也加陈旧检测——超过30分钟则强制清理（防止 watchdog 自身被锁死）
LOCK="/tmp/job_watchdog.lockdir"
NOW_EPOCH=$(date +%s)
if [ -d "$LOCK" ]; then
    if [ "$(uname)" = "Darwin" ]; then
        LOCK_EPOCH=$(stat -f %m "$LOCK" 2>/dev/null || echo "0")
    else
        LOCK_EPOCH=$(stat -c %Y "$LOCK" 2>/dev/null || echo "0")
    fi
    LOCK_AGE=$(( NOW_EPOCH - LOCK_EPOCH ))
    if [ "$LOCK_AGE" -gt 1800 ]; then
        echo "[watchdog] Stale self-lock detected (${LOCK_AGE}s old), force clearing"
        rmdir "$LOCK" 2>/dev/null || rm -rf "$LOCK" 2>/dev/null
    fi
fi
mkdir "$LOCK" 2>/dev/null || { echo "[watchdog] Already running, skip"; exit 0; }
trap 'rmdir "$LOCK" 2>/dev/null' EXIT

OPENCLAW="${OPENCLAW:-/opt/homebrew/bin/openclaw}"
TO="${OPENCLAW_PHONE:-+85200000000}"
TS="$(TZ=Asia/Hong_Kong date '+%Y-%m-%d %H:%M:%S')"

# 心跳日志：每次运行都记录，防止"一切正常时日志不更新"导致误报陈旧
echo "[watchdog] $TS heartbeat — checking jobs"

# ── 监控列表：job_id | status_file 路径 | 最大允许静默时间(秒) | 显示名 ──
# 静默时间 = interval × 2 + 缓冲，确保不会因为单次正常跳过就误报
JOBS=(
    # ArXiv: 每3小时 → 最多静默 7 小时（3h×2 + 1h缓冲）
    "arxiv_monitor|$HOME/.openclaw/jobs/arxiv_monitor/cache/last_run.json|25200|ArXiv论文监控"
    # HN: 每3小时 → 最多静默 7 小时
    "run_hn_fixed|$HOME/.openclaw/jobs/hn_watcher/cache/last_run.json|25200|HN热帖抓取"
    # Freight: 每天3次(08/14/20) → 最多静默 14 小时
    "freight_watcher|$HOME/.openclaw/jobs/freight_watcher/cache/last_run.json|50400|货代Watcher"
    # OpenClaw Releases: 每天1次 → 最多静默 50 小时
    "openclaw_run|$HOME/.openclaw/jobs/openclaw_official/cache/last_run.json|180000|OpenClaw版本监控"
    # Discussions: 每小时 → 最多静默 3 小时
    "run_discussions|$HOME/.openclaw/jobs/openclaw_official/cache/last_run_discussions.json|10800|Issues监控"
    # KB Evening: 每天22:00 → 最多静默 50 小时
    "kb_evening|$HOME/.kb/last_run_evening.json|180000|KB晚间整理"
)

ALERTS=()

for entry in "${JOBS[@]}"; do
    IFS='|' read -r job_id status_file max_silence display_name <<< "$entry"

    # 状态文件不存在 → 可能从未成功运行过
    if [ ! -f "$status_file" ]; then
        ALERTS+=("$display_name: 状态文件不存在（从未成功执行？）")
        continue
    fi

    # 读取 last_run.json 中的时间戳
    LAST_TIME=$(python3 -c "
import json, sys
from datetime import datetime, timezone
try:
    with open('$status_file') as f:
        d = json.load(f)
    t = d.get('time', '')
    # 解析 'YYYY-MM-DD HH:MM:SS' 格式（HKT）
    dt = datetime.strptime(t, '%Y-%m-%d %H:%M:%S')
    # 转为 UTC epoch（HKT = UTC+8）
    from datetime import timedelta
    dt_utc = dt - timedelta(hours=8)
    print(int(dt_utc.replace(tzinfo=timezone.utc).timestamp()))
except Exception as e:
    print(0)
" 2>/dev/null)

    if [ "$LAST_TIME" -eq 0 ]; then
        ALERTS+=("$display_name: 状态文件格式异常")
        continue
    fi

    ELAPSED=$((NOW_EPOCH - LAST_TIME))
    if [ "$ELAPSED" -gt "$max_silence" ]; then
        HOURS=$((ELAPSED / 3600))
        ALERTS+=("$display_name: 已 ${HOURS}h 未更新（阈值 $((max_silence / 3600))h）")
    fi

    # 额外检查：最近一次状态是否为失败
    LAST_STATUS=$(python3 -c "
import json
try:
    with open('$status_file') as f:
        print(json.load(f).get('status', 'unknown'))
except Exception:
    print('unknown')
" 2>/dev/null)

    case "$LAST_STATUS" in
        fetch_failed|parse_failed|send_failed)
            ALERTS+=("$display_name: 最近一次执行状态异常 ($LAST_STATUS)")
            ;;
    esac
done

# ── 日志扫描：检查最近1小时内的推送失败（不依赖 status_file）─────────
LOG_DIR="$HOME/.openclaw/logs/jobs"
if [ -d "$LOG_DIR" ]; then
    ONE_HOUR_AGO=$(date -v-1H '+%Y-%m-%d %H' 2>/dev/null || date -d '1 hour ago' '+%Y-%m-%d %H' 2>/dev/null || echo "")
    if [ -n "$ONE_HOUR_AGO" ]; then
        for logfile in "$LOG_DIR"/*.log; do
            [ -f "$logfile" ] || continue
            job_name=$(basename "$logfile" .log)
            # 查找最近1小时内的推送失败记录
            fail_count=$(grep -c "推送失败\|send_failed\|ERROR.*推送" "$logfile" 2>/dev/null | tail -1)
            # 只看最近修改的文件中的最后几行（避免重复告警历史错误）
            recent_fails=$(tail -20 "$logfile" 2>/dev/null | grep -c "推送失败\|send_failed" || true)
            if [ "$recent_fails" -gt 0 ]; then
                last_err=$(tail -20 "$logfile" 2>/dev/null | grep "推送失败\|send_failed" | tail -1)
                ALERTS+=("$job_name: 最近有推送失败 → $last_err")
            fi
        done
    fi
fi

# ── 陈旧锁文件检测 + 自动清理（V30新增）──────────────────────────────
# 锁文件超过1小时 = 对应 job 进程已死但锁未释放（kill -9/系统崩溃）
# 自动清理后下次 cron 触发时 job 才能正常执行
STALE_LOCK_DIRS=(
    "/tmp/arxiv_monitor.lockdir|ArXiv监控"
    "/tmp/hn_watcher.lockdir|HN抓取"
    "/tmp/freight_watcher.lockdir|货代Watcher"
    "/tmp/auto_deploy.lockdir|自动部署"
    "/tmp/openclaw_run.lockdir|OpenClaw版本"
    "/tmp/run_discussions.lockdir|Issues监控"
    "/tmp/kb_review.lockdir|KB回顾"
    "/tmp/kb_evening.lockdir|KB晚间"
)

STALE_CLEANED=0
for entry in "${STALE_LOCK_DIRS[@]}"; do
    IFS='|' read -r lock_path name <<< "$entry"
    if [ -d "$lock_path" ]; then
        if [ "$(uname)" = "Darwin" ]; then
            LOCK_EPOCH=$(stat -f %m "$lock_path" 2>/dev/null || echo "0")
        else
            LOCK_EPOCH=$(stat -c %Y "$lock_path" 2>/dev/null || echo "0")
        fi
        LOCK_AGE=$(( NOW_EPOCH - LOCK_EPOCH ))
        if [ "$LOCK_AGE" -gt 3600 ]; then
            LOCK_HOURS=$(( LOCK_AGE / 3600 ))
            rmdir "$lock_path" 2>/dev/null || rm -rf "$lock_path" 2>/dev/null
            ALERTS+=("$name: 陈旧锁文件已清理（存在 ${LOCK_HOURS}h，进程已死）")
            STALE_CLEANED=$((STALE_CLEANED + 1))
        fi
    fi
done

if [ "$STALE_CLEANED" -gt 0 ]; then
    ALERTS+=("🔧 共清理 $STALE_CLEANED 个陈旧锁文件，对应 job 将在下次 cron 触发时恢复")
fi

# ── Cron 心跳检测（V30新增）───────────────────────────────────────────
# cron_canary.sh 每10分钟写一次心跳；超过30分钟 = cron daemon 可能停止
CANARY_FILE="$HOME/.cron_canary"
if [ -f "$CANARY_FILE" ]; then
    CANARY_EPOCH=$(head -1 "$CANARY_FILE" 2>/dev/null | tr -d '[:space:]')
    if [[ "$CANARY_EPOCH" =~ ^[0-9]+$ ]]; then
        CANARY_AGE=$(( NOW_EPOCH - CANARY_EPOCH ))
        if [ "$CANARY_AGE" -gt 1800 ]; then
            CANARY_MINS=$(( CANARY_AGE / 60 ))
            ALERTS+=("⚠️ Cron 心跳已 ${CANARY_MINS}m 未更新（cron daemon 可能已停止！）")
        fi
    fi
fi

# ── Proxy 监控：token 用量 + 错误率 ──────────────────────────────────
PROXY_STATS="$HOME/proxy_stats.json"
if [ -f "$PROXY_STATS" ]; then
    PROXY_CHECK=$(python3 -c "
import json, time
try:
    with open('$PROXY_STATS') as f:
        s = json.load(f)
    alerts = []
    # 连续错误检查
    ce = s.get('consecutive_errors', 0)
    if ce >= 3:
        last_err = s.get('last_error', {})
        alerts.append(f'Proxy 连续 {ce} 次错误 (HTTP {last_err.get(\"code\",\"?\")}: {last_err.get(\"msg\",\"\")[:60]})')
    # Context 用量检查
    pct = s.get('context_usage_pct', 0)
    pt = s.get('last_prompt_tokens', 0)
    if pct >= 90:
        alerts.append(f'Qwen context 临界: {pt:,} tokens ({pct}% of 260K)')
    elif pct >= 75:
        alerts.append(f'Qwen context 预警: {pt:,} tokens ({pct}% of 260K)')
    # stats 文件本身过期（proxy 可能挂了）
    updated = s.get('updated', '')
    if updated:
        from datetime import datetime, timedelta
        try:
            ut = datetime.strptime(updated, '%Y-%m-%d %H:%M:%S')
            if datetime.now() - ut > timedelta(hours=2):
                alerts.append(f'proxy_stats.json 超过2小时未更新（proxy可能已停止）')
        except ValueError:
            pass
    print('\\n'.join(alerts))
except Exception as e:
    print(f'proxy_stats.json 读取失败: {e}')
" 2>/dev/null)

    while IFS= read -r line; do
        [ -n "$line" ] && ALERTS+=("$line")
    done <<< "$PROXY_CHECK"
fi

# ── 汇总告警 ────────────────────────────────────────────────────────
if [ ${#ALERTS[@]} -eq 0 ]; then
    echo "[$TS] watchdog: 全部 ${#JOBS[@]} 个任务 + Proxy 监控正常"
    exit 0
fi

# 组装告警消息
ALERT_MSG="🚨 任务监控告警 ($TS)

以下任务需要关注：
"
for a in "${ALERTS[@]}"; do
    ALERT_MSG+="• $a
"
done
ALERT_MSG+="
排查建议：
1. bash cron_doctor.sh  # 全面诊断
2. rmdir /tmp/*.lockdir  # 清除残留锁
3. crontab -l  # 确认调度条目
4. 检查对应日志文件"

echo "$ALERT_MSG"

# 推送 WhatsApp（V30: 失败时写本地告警文件，打破 WhatsApp↔Gateway 循环依赖）
ALERT_LOG="$HOME/.openclaw_alerts.log"
"$OPENCLAW" message send --target "$TO" --message "$ALERT_MSG" --json >/dev/null 2>&1 || {
    echo "[$TS] watchdog: ⚠️ WhatsApp 推送失败，写入本地告警文件"
    echo "=== UNDELIVERED ALERT [$TS] ===" >> "$ALERT_LOG"
    echo "$ALERT_MSG" >> "$ALERT_LOG"
    echo "================================" >> "$ALERT_LOG"
}

# 本地告警文件始终写入（供 cron_doctor / SSH 检查时查看）
echo "[$TS] ALERT: ${#ALERTS[@]} issues" >> "$ALERT_LOG"
# 保留最近 500 行
if [ -f "$ALERT_LOG" ] && [ "$(wc -l < "$ALERT_LOG" | tr -d ' ')" -gt 500 ]; then
    tail -300 "$ALERT_LOG" > "$ALERT_LOG.tmp" && mv "$ALERT_LOG.tmp" "$ALERT_LOG"
fi
