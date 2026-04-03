#!/bin/bash
# job_watchdog.sh — 元监控：全方位系统健康检查
# 每小时由系统 crontab 触发，检查 8 大维度：
#   1. 定时任务执行状态（15个job，时间戳+状态字段）
#   2. 日志错误扫描（所有job日志，最近50行）
#   3. 三层服务健康（Gateway/Proxy/Adapter HTTP探测）
#   4. 陈旧锁文件检测+自动清理
#   5. Cron 健康（心跳+crontab条目数）
#   6. Proxy 运行时监控（连续错误+context用量+吞吐量）
#   7. 磁盘空间检查
#   8. KB 数据新鲜度
# V31: 从6个job扩展到15个 + 服务健康 + 磁盘 + KB新鲜度 + crontab条目数 + 日志扫描加强
# cron 环境 PATH 极简，必须显式声明（规则 #13）
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:$PATH"
set -eo pipefail

# 防重叠执行（mkdir 原子锁，macOS 兼容）
# 自身锁文件加陈旧检测——超过30分钟则强制清理（防止 watchdog 自身被锁死）
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

# 心跳日志：每次运行都记录
echo "[watchdog] $TS heartbeat — 8-dimension health check"

ALERTS=()
STATS_PASS=0
STATS_WARN=0

# ════════════════════════════════════════════════════════════════════
# 1/8 定时任务执行状态（15个job，时间戳+状态字段）
# ════════════════════════════════════════════════════════════════════
# 格式：job_id | status_file 路径 | 最大允许静默时间(秒) | 显示名 | tier
# 静默时间 = interval × 2 + 缓冲
# tier: core（失败立即告警）/ auxiliary（失败记录警告）/ experiment（失败仅记录）
JOBS=(
    # ── 论文监控矩阵（5源）──
    # ArXiv: 每天4次(04/10/16/22) → 最多静默 14h
    "arxiv_monitor|$HOME/.openclaw/jobs/arxiv_monitor/cache/last_run.json|50400|ArXiv论文监控|core"
    # HF Papers: 每天2次(10/20) → 最多静默 28h
    "hf_papers|$HOME/.openclaw/jobs/hf_papers/cache/last_run.json|100800|HF论文监控|core"
    # Semantic Scholar: 每天1次(08:00) → 最多静默 50h
    "semantic_scholar|$HOME/.openclaw/jobs/semantic_scholar/cache/last_run.json|180000|S2论文监控|core"
    # DBLP: 每天1次(12:00) → 最多静默 50h
    "dblp|$HOME/.openclaw/jobs/dblp/cache/last_run.json|180000|DBLP论文监控|auxiliary"
    # ACL: 每周三(09:30) → 最多静默 192h（8天）
    "acl_anthology|$HOME/.openclaw/jobs/acl_anthology/cache/last_run.json|691200|ACL论文监控|auxiliary"

    # ── 应用监控 ──
    # HN: 每3小时 → 最多静默 7h
    "run_hn_fixed|$HOME/.openclaw/jobs/hn_watcher/cache/last_run.json|25200|HN热帖抓取|core"
    # Freight: 每天3次(08/14/20) → 最多静默 14h
    "freight_watcher|$HOME/.openclaw/jobs/freight_watcher/cache/last_run.json|50400|货代Watcher|core"
    # OpenClaw Releases: 每天1次(08:00) → 最多静默 50h
    "openclaw_run|$HOME/.openclaw/jobs/openclaw_official/cache/last_run.json|180000|OpenClaw版本监控|core"
    # Discussions: 每小时 → 最多静默 3h
    "run_discussions|$HOME/.openclaw/jobs/openclaw_official/cache/last_run_discussions.json|10800|Issues监控|auxiliary"

    # ── KB 处理 ──
    # KB Evening: 每天22:00 → 最多静默 50h
    "kb_evening|$HOME/.kb/last_run_evening.json|180000|KB晚间整理|core"
    # KB Review: 每周五21:00 → 最多静默 192h（8天）
    "kb_review|$HOME/.kb/last_run_review.json|691200|KB周回顾|auxiliary"
)

CORE_ALERTS=()
AUX_ALERTS=()
EXP_ALERTS=()

for entry in "${JOBS[@]}"; do
    IFS='|' read -r job_id status_file max_silence display_name tier <<< "$entry"
    tier="${tier:-auxiliary}"  # 默认 auxiliary

    # 状态文件不存在 → 可能从未成功运行过
    if [ ! -f "$status_file" ]; then
        case "$tier" in
            core) CORE_ALERTS+=("🔴 $display_name: 状态文件不存在（从未成功执行？）") ;;
            auxiliary) AUX_ALERTS+=("🟡 $display_name: 状态文件不存在") ;;
            *) EXP_ALERTS+=("⚪ $display_name: 状态文件不存在") ;;
        esac
        ALERTS+=("$display_name: 状态文件不存在（从未成功执行？）")
        continue
    fi

    # 读取 last_run.json 中的时间戳和状态
    JOB_INFO=$(python3 -c "
import json, sys
from datetime import datetime, timezone, timedelta
try:
    with open('$status_file') as f:
        d = json.load(f)
    t = d.get('time', '')
    dt = datetime.strptime(t, '%Y-%m-%d %H:%M:%S')
    dt_utc = dt - timedelta(hours=8)
    epoch = int(dt_utc.replace(tzinfo=timezone.utc).timestamp())
    status = d.get('status', 'unknown')
    # 额外字段
    http_code = d.get('http_code', '')
    deep_dive = d.get('deep_dive', '')
    new_count = d.get('new', '')
    print(f'{epoch}|{status}|{http_code}|{deep_dive}|{new_count}')
except Exception as e:
    print(f'0|error|||||{e}')
" 2>/dev/null)

    IFS='|' read -r LAST_TIME LAST_STATUS HTTP_CODE DEEP_DIVE NEW_COUNT <<< "$JOB_INFO"

    if [ "$LAST_TIME" -eq 0 ] 2>/dev/null; then
        ALERTS+=("$display_name: 状态文件格式异常")
        continue
    fi

    # 时间戳超时检查
    ELAPSED=$((NOW_EPOCH - LAST_TIME))
    if [ "$ELAPSED" -gt "$max_silence" ]; then
        HOURS=$((ELAPSED / 3600))
        _msg="$display_name: 已 ${HOURS}h 未更新（阈值 $((max_silence / 3600))h）"
        ALERTS+=("$_msg")
        case "$tier" in
            core) CORE_ALERTS+=("🔴 $_msg") ;;
            auxiliary) AUX_ALERTS+=("🟡 $_msg") ;;
            *) EXP_ALERTS+=("⚪ $_msg") ;;
        esac
    else
        STATS_PASS=$((STATS_PASS + 1))
    fi

    # 状态字段检查（扩展匹配：任何非 ok/unknown 的失败状态）
    _status_msg=""
    case "$LAST_STATUS" in
        ok|unknown)
            ;;  # 正常
        fetch_failed)
            _status_msg="$display_name: 最近一次抓取失败 (HTTP $HTTP_CODE)"
            ;;
        parse_failed|parse_quality_low)
            _status_msg="$display_name: 最近一次解析异常 ($LAST_STATUS)"
            ;;
        send_failed)
            _status_msg="$display_name: 最近一次推送失败"
            ;;
        no_volumes)
            _status_msg="$display_name: 外挂存储不可用 ($LAST_STATUS)"
            ;;
        *)
            # 捕获所有未知的非 ok 状态
            if [ "$LAST_STATUS" != "ok" ] && [ "$LAST_STATUS" != "unknown" ]; then
                _status_msg="$display_name: 异常状态 ($LAST_STATUS)"
            fi
            ;;
    esac
    if [ -n "$_status_msg" ]; then
        ALERTS+=("$_status_msg")
        case "$tier" in
            core) CORE_ALERTS+=("🔴 $_status_msg") ;;
            auxiliary) AUX_ALERTS+=("🟡 $_status_msg") ;;
            *) EXP_ALERTS+=("⚪ $_status_msg") ;;
        esac
    fi

    # 货代 deep_dive 特殊检查
    if [ "$job_id" = "freight_watcher" ] && [ -n "$DEEP_DIVE" ]; then
        case "$DEEP_DIVE" in
            ok) ;;
            no_data)
                ALERTS+=("$display_name: ImportYeti 无数据返回（deep_dive=no_data）")
                ;;
            skipped|missing)
                STATS_WARN=$((STATS_WARN + 1))
                ;;
        esac
    fi
done

# ════════════════════════════════════════════════════════════════════
# 2/8 日志错误扫描（全部 job 日志，最近50行）
# ════════════════════════════════════════════════════════════════════
# 扫描两个日志目录：~/.openclaw/logs/jobs/ 和 ~/（部分 job 日志在 home 目录）
scan_logs() {
    local logfile="$1"
    local job_name="$2"
    [ -f "$logfile" ] || return

    # 检查日志文件最后修改时间（超过预期间隔的3倍 → 日志可能已停更）
    if [ "$(uname)" = "Darwin" ]; then
        LOG_MOD=$(stat -f %m "$logfile" 2>/dev/null || echo "0")
    else
        LOG_MOD=$(stat -c %Y "$logfile" 2>/dev/null || echo "0")
    fi

    # 扫描最后50行中的错误（比原来的20行覆盖更多）
    local recent_fails
    recent_fails=$(tail -50 "$logfile" 2>/dev/null | grep -ciE "推送失败|send_failed|fetch_failed|FAIL(ED)?:|ERROR[: ]|Traceback|HTTP[/ ](4[0-9]{2}|5[0-9]{2})" || true)
    if [ "$recent_fails" -gt 0 ]; then
        local last_err
        last_err=$(tail -50 "$logfile" 2>/dev/null | grep -iE "推送失败|send_failed|fetch_failed|FAIL(ED)?:|ERROR[: ]|Traceback|HTTP[/ ](4[0-9]{2}|5[0-9]{2})" | tail -1 | head -c 120)
        ALERTS+=("$job_name 日志: ${recent_fails}条错误 → $last_err")
    fi
}

# 扫描 ~/.openclaw/logs/jobs/ 下的所有日志
LOG_DIR="$HOME/.openclaw/logs/jobs"
if [ -d "$LOG_DIR" ]; then
    for logfile in "$LOG_DIR"/*.log; do
        [ -f "$logfile" ] || continue
        job_name=$(basename "$logfile" .log)
        scan_logs "$logfile" "$job_name"
    done
fi

# 扫描 home 目录下的 job 日志（部分 job 日志不在 .openclaw/logs/jobs/ 下）
HOME_LOGS=(
    "$HOME/kb_evening.log|kb_evening"
    "$HOME/kb_inject.log|kb_inject"
    "$HOME/kb_review.log|kb_review"
    "$HOME/kb_embed.log|kb_embed"
    "$HOME/kb_trend.log|kb_trend"
    "$HOME/kb_dedup.log|kb_dedup"
    "$HOME/conv_quality.log|conv_quality"
    "$HOME/token_report.log|token_report"
    "$HOME/openclaw_backup.log|openclaw_backup"
    "$HOME/preference_learner.log|preference_learner"
)
for entry in "${HOME_LOGS[@]}"; do
    IFS='|' read -r logfile job_name <<< "$entry"
    scan_logs "$logfile" "$job_name"
done

# Adapter 日志特殊扫描（FALLBACK ALSO FAILED = 双路径全挂）
ADAPTER_LOG="$HOME/adapter.log"
if [ -f "$ADAPTER_LOG" ]; then
    adapter_critical=$(tail -100 "$ADAPTER_LOG" 2>/dev/null | grep -c "FALLBACK ALSO FAILED" || true)
    if [ "$adapter_critical" -gt 0 ]; then
        ALERTS+=("Adapter: 最近 ${adapter_critical} 次主备双失败（LLM 完全不可用）")
    fi
fi

# ════════════════════════════════════════════════════════════════════
# 3/8 三层服务健康（HTTP 探测）
# ════════════════════════════════════════════════════════════════════
check_service() {
    local name="$1"
    local url="$2"
    local timeout="${3:-5}"

    local http_code
    http_code=$(curl -s -o /dev/null -w "%{http_code}" --connect-timeout "$timeout" --max-time "$timeout" "$url" 2>/dev/null || echo "000")

    if [ "$http_code" = "000" ]; then
        ALERTS+=("$name: 服务无响应（连接超时）")
    elif [ "$http_code" -ge 500 ] 2>/dev/null; then
        ALERTS+=("$name: 服务异常 (HTTP $http_code)")
    else
        STATS_PASS=$((STATS_PASS + 1))
    fi
}

check_service "Adapter(:5001)" "http://localhost:5001/health" 5
check_service "Proxy(:5002)" "http://localhost:5002/health" 5
check_service "Gateway(:18789)" "http://localhost:18789" 5

# ════════════════════════════════════════════════════════════════════
# 4/8 陈旧锁文件检测 + 自动清理
# ════════════════════════════════════════════════════════════════════
# 锁文件超过1小时 = 对应 job 进程已死但锁未释放
STALE_LOCK_DIRS=(
    "/tmp/arxiv_monitor.lockdir|ArXiv监控"
    "/tmp/hf_papers.lockdir|HF论文"
    "/tmp/semantic_scholar.lockdir|S2论文"
    "/tmp/dblp.lockdir|DBLP论文"
    "/tmp/acl_anthology.lockdir|ACL论文"
    "/tmp/hn_watcher.lockdir|HN抓取"
    "/tmp/freight_watcher.lockdir|货代Watcher"
    "/tmp/auto_deploy.lockdir|自动部署"
    "/tmp/openclaw_releases.lockdir|OpenClaw版本"
    "/tmp/openclaw_discussions.lockdir|Issues监控"
    "/tmp/run_discussions.lockdir|Issues监控(alt)"
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

# ════════════════════════════════════════════════════════════════════
# 5/8 Cron 健康（心跳 + crontab 条目数）
# ════════════════════════════════════════════════════════════════════

# 5a. Cron 心跳金丝雀（cron_canary.sh 每10分钟写一次）
CANARY_FILE="$HOME/.cron_canary"
if [ -f "$CANARY_FILE" ]; then
    CANARY_EPOCH=$(head -1 "$CANARY_FILE" 2>/dev/null | tr -d '[:space:]')
    if [[ "$CANARY_EPOCH" =~ ^[0-9]+$ ]]; then
        CANARY_AGE=$(( NOW_EPOCH - CANARY_EPOCH ))
        if [ "$CANARY_AGE" -gt 1800 ]; then
            CANARY_MINS=$(( CANARY_AGE / 60 ))
            ALERTS+=("⚠️ Cron 心跳已 ${CANARY_MINS}m 未更新（cron daemon 可能已停止！）")
        else
            STATS_PASS=$((STATS_PASS + 1))
        fi
    fi
else
    ALERTS+=("⚠️ Cron 心跳文件不存在（cron_canary.sh 未配置？）")
fi

# 5b. Crontab 条目数检查（V30 事故防护：crontab 被意外清空）
CRON_COUNT=$(crontab -l 2>/dev/null | grep -cvE "^#|^$" || echo "0")
if [ "$CRON_COUNT" -lt 10 ]; then
    ALERTS+=("⚠️ Crontab 仅 $CRON_COUNT 条有效条目（应≥15，可能被意外清空！）")
elif [ "$CRON_COUNT" -lt 15 ]; then
    STATS_WARN=$((STATS_WARN + 1))
else
    STATS_PASS=$((STATS_PASS + 1))
fi

# ════════════════════════════════════════════════════════════════════
# 6/8 Proxy 运行时监控（连续错误 + context 用量 + 吞吐量）
# ════════════════════════════════════════════════════════════════════
PROXY_STATS="$HOME/proxy_stats.json"
if [ -f "$PROXY_STATS" ]; then
    PROXY_CHECK=$(python3 -c "
import json, time
from datetime import datetime, timedelta
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
        try:
            ut = datetime.strptime(updated, '%Y-%m-%d %H:%M:%S')
            age_hours = (datetime.now() - ut).total_seconds() / 3600
            if age_hours > 4:
                alerts.append(f'proxy_stats.json 超过{int(age_hours)}h未更新（proxy可能已停止）')
            elif age_hours > 2:
                alerts.append(f'proxy_stats.json {int(age_hours)}h未更新（proxy活跃度下降）')
        except ValueError:
            pass

    # 请求吞吐量检查（total_requests 为 0 → proxy 可能从未成功处理请求）
    total = s.get('total_requests', 0)
    errors = s.get('total_errors', 0)
    if total > 0 and errors > 0:
        error_rate = errors / total * 100
        if error_rate > 20:
            alerts.append(f'Proxy 错误率 {error_rate:.1f}%（{errors}/{total}）')

    print('\\n'.join(alerts))
except Exception as e:
    print(f'proxy_stats.json 读取失败: {e}')
" 2>/dev/null)

    while IFS= read -r line; do
        [ -n "$line" ] && ALERTS+=("$line")
    done <<< "$PROXY_CHECK"

    # SLO 合规检查（V33: 接入 slo_checker.py）
    SLO_SCRIPT="$HOME/slo_checker.py"
    if [ -f "$SLO_SCRIPT" ]; then
        SLO_ALERT=$(python3 "$SLO_SCRIPT" --alert 2>/dev/null) && SLO_RC=0 || SLO_RC=$?
        if [ "$SLO_RC" -eq 2 ] && [ -n "$SLO_ALERT" ]; then
            while IFS= read -r line; do
                [ -n "$line" ] && ALERTS+=("$line")
            done <<< "$SLO_ALERT"
        elif [ "$SLO_RC" -eq 0 ]; then
            STATS_PASS=$((STATS_PASS + 1))
        fi
    fi
fi

# ════════════════════════════════════════════════════════════════════
# 7/8 磁盘空间检查
# ════════════════════════════════════════════════════════════════════
DISK_PCT=$(df -h / 2>/dev/null | awk 'NR==2 {gsub(/%/,""); print $5}')
if [ -n "$DISK_PCT" ] && [ "$DISK_PCT" -ge 95 ] 2>/dev/null; then
    ALERTS+=("磁盘空间严重不足: 已用 ${DISK_PCT}%（>95%，job 可能写入失败）")
elif [ -n "$DISK_PCT" ] && [ "$DISK_PCT" -ge 85 ] 2>/dev/null; then
    STATS_WARN=$((STATS_WARN + 1))
else
    STATS_PASS=$((STATS_PASS + 1))
fi

# ════════════════════════════════════════════════════════════════════
# 8/8 KB 数据新鲜度（全部 ingestion 停止的终极指标）
# ════════════════════════════════════════════════════════════════════
KB_SOURCES="$HOME/.kb/sources"
if [ -d "$KB_SOURCES" ]; then
    # 检查最近 48h 内是否有新的 source 文件生成
    if [ "$(uname)" = "Darwin" ]; then
        RECENT_KB=$(find "$KB_SOURCES" -name "*.md" -mtime -2 2>/dev/null | wc -l | tr -d ' ')
    else
        RECENT_KB=$(find "$KB_SOURCES" -name "*.md" -mmin -2880 2>/dev/null | wc -l | tr -d ' ')
    fi

    if [ "$RECENT_KB" -eq 0 ]; then
        ALERTS+=("KB 数据停滞: 48h 内无新 source 文件（所有 ingestion job 可能已失效）")
    else
        STATS_PASS=$((STATS_PASS + 1))
    fi
fi

# KB 向量索引新鲜度
KB_INDEX="$HOME/.kb/text_index/meta.json"
if [ -f "$KB_INDEX" ]; then
    if [ "$(uname)" = "Darwin" ]; then
        IDX_MOD=$(stat -f %m "$KB_INDEX" 2>/dev/null || echo "0")
    else
        IDX_MOD=$(stat -c %Y "$KB_INDEX" 2>/dev/null || echo "0")
    fi
    IDX_AGE=$(( NOW_EPOCH - IDX_MOD ))
    # kb_embed 每4小时运行，12小时未更新 = 异常
    if [ "$IDX_AGE" -gt 43200 ]; then
        IDX_HOURS=$((IDX_AGE / 3600))
        ALERTS+=("KB 向量索引 ${IDX_HOURS}h 未更新（kb_embed 可能未运行）")
    fi
fi

# ════════════════════════════════════════════════════════════════════
# 汇总告警
# ════════════════════════════════════════════════════════════════════
TOTAL_CHECKS=$((STATS_PASS + STATS_WARN + ${#ALERTS[@]}))

if [ ${#ALERTS[@]} -eq 0 ]; then
    echo "[$TS] watchdog: 全部 $TOTAL_CHECKS 项检查通过（${#JOBS[@]} job + 3 服务 + cron + proxy + 磁盘 + KB）"
    exit 0
fi

# 组装告警消息（带严重程度分级）
CRITICAL_COUNT=0
WARNING_COUNT=0
for a in "${ALERTS[@]}"; do
    case "$a" in
        *"服务无响应"*|*"双失败"*|*"cron daemon"*|*"磁盘空间严重"*|*"被意外清空"*)
            CRITICAL_COUNT=$((CRITICAL_COUNT + 1))
            ;;
        *)
            WARNING_COUNT=$((WARNING_COUNT + 1))
            ;;
    esac
done

if [ "$CRITICAL_COUNT" -gt 0 ]; then
    SEVERITY="🔴 CRITICAL"
else
    SEVERITY="🟡 WARNING"
fi

ALERT_MSG="🚨 系统监控告警 $SEVERITY ($TS)
检查: $TOTAL_CHECKS 项 | 通过: $STATS_PASS | 告警: ${#ALERTS[@]} (core:${#CORE_ALERTS[@]} aux:${#AUX_ALERTS[@]} exp:${#EXP_ALERTS[@]})"

# 按 tier 分组展示：core 先行
if [ ${#CORE_ALERTS[@]} -gt 0 ]; then
    ALERT_MSG+="

🔴 CORE（立即处理）:"
    for a in "${CORE_ALERTS[@]}"; do
        ALERT_MSG+="
• $a"
    done
fi

if [ ${#AUX_ALERTS[@]} -gt 0 ]; then
    ALERT_MSG+="

🟡 AUXILIARY（关注）:"
    for a in "${AUX_ALERTS[@]}"; do
        ALERT_MSG+="
• $a"
    done
fi

if [ ${#EXP_ALERTS[@]} -gt 0 ]; then
    ALERT_MSG+="

⚪ EXPERIMENT（仅记录）:"
    for a in "${EXP_ALERTS[@]}"; do
        ALERT_MSG+="
• $a"
    done
fi

# 非 job 告警（服务/磁盘/cron 等）单独展示
NON_JOB_ALERTS=()
for a in "${ALERTS[@]}"; do
    # 跳过已在 tier 分组中展示的 job 告警
    is_job=false
    for c in "${CORE_ALERTS[@]}" "${AUX_ALERTS[@]}" "${EXP_ALERTS[@]}"; do
        # tier 告警带前缀，去掉前缀后比较
        stripped="${c#🔴 }"
        stripped="${stripped#🟡 }"
        stripped="${stripped#⚪ }"
        if [ "$a" = "$stripped" ]; then
            is_job=true
            break
        fi
    done
    if ! $is_job; then
        NON_JOB_ALERTS+=("$a")
    fi
done

if [ ${#NON_JOB_ALERTS[@]} -gt 0 ]; then
    ALERT_MSG+="

🔧 系统告警:"
    for a in "${NON_JOB_ALERTS[@]}"; do
        ALERT_MSG+="
• $a"
    done
fi

ALERT_MSG+="

排查建议：
1. python3 ~/incident_snapshot.py --list  # 查看故障快照
2. bash cron_doctor.sh     # 全面诊断
3. curl localhost:5002/health  # Proxy健康
4. curl localhost:5001/health  # Adapter健康
5. rmdir /tmp/*.lockdir    # 清除残留锁
6. crontab -l | wc -l     # 确认调度条目"

echo "$ALERT_MSG"

# ── 故障快照：告警时自动收集系统状态 ──
# 仅在有 CORE 告警或 CRITICAL 告警时触发（避免低级别告警频繁快照）
if [ "$CRITICAL_COUNT" -gt 0 ] || [ "${#CORE_ALERTS[@]}" -gt 0 ]; then
    SNAPSHOT_DESC="watchdog: ${#ALERTS[@]} alerts (${CRITICAL_COUNT} critical, ${#CORE_ALERTS[@]} core)"
    python3 "$HOME/incident_snapshot.py" --auto "$SNAPSHOT_DESC" 2>/dev/null && {
        echo "[watchdog] 故障快照已创建"
    } || {
        echo "[watchdog] 故障快照创建失败（非致命）"
    }
fi

# 推送 WhatsApp（失败时写本地告警文件，打破 WhatsApp↔Gateway 循环依赖）
ALERT_LOG="$HOME/.openclaw_alerts.log"
"$OPENCLAW" message send --channel whatsapp --target "$TO" --message "$ALERT_MSG" --json >/dev/null 2>&1 || {
    echo "[$TS] watchdog: ⚠️ WhatsApp 推送失败，写入本地告警文件"
    echo "=== UNDELIVERED ALERT [$TS] ===" >> "$ALERT_LOG"
    echo "$ALERT_MSG" >> "$ALERT_LOG"
    echo "================================" >> "$ALERT_LOG"
}
"$OPENCLAW" message send --channel discord --target "${DISCORD_CH_ALERTS:-}" --message "$ALERT_MSG" --json >/dev/null 2>&1 || true

# 本地告警文件始终写入（供 cron_doctor / SSH 检查时查看）
echo "[$TS] ALERT: ${#ALERTS[@]} issues (${CRITICAL_COUNT} critical, ${WARNING_COUNT} warning)" >> "$ALERT_LOG"
# 保留最近 500 行
if [ -f "$ALERT_LOG" ] && [ "$(wc -l < "$ALERT_LOG" | tr -d ' ')" -gt 500 ]; then
    tail -300 "$ALERT_LOG" > "$ALERT_LOG.tmp" && mv "$ALERT_LOG.tmp" "$ALERT_LOG"
fi
