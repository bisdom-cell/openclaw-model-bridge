#!/bin/bash
# cron_doctor.sh — 定时任务全面诊断工具（V30新增）
# 用途：当所有定时任务停止推送时，一键定位根因
# 用法：bash cron_doctor.sh          （Mac Mini 上运行）
# 设计：覆盖7大失效场景，每项给出具体修复命令
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:$PATH"
set -uo pipefail

TS="$(TZ=Asia/Hong_Kong date '+%Y-%m-%d %H:%M:%S')"
NOW_EPOCH=$(date +%s)
PASS=0
FAIL=0
WARN=0

pass() { echo "  ✅ $1"; PASS=$((PASS + 1)); }
fail() { echo "  ❌ $1"; FAIL=$((FAIL + 1)); }
warn() { echo "  ⚠️  $1"; WARN=$((WARN + 1)); }
info() { echo "  ℹ️  $1"; }

echo "╔══════════════════════════════════════════════════════╗"
echo "║        Cron Doctor — 定时任务全面诊断                  ║"
echo "║        $TS                            ║"
echo "╚══════════════════════════════════════════════════════╝"
echo ""

# ═══════════════════════════════════════════════════════════════════
# 1. crontab 完整性检查 — 最常见的"全部失效"原因
# ═══════════════════════════════════════════════════════════════════
echo "🔍 1/7 Crontab 完整性"

CRON_LINES=$(crontab -l 2>/dev/null | grep -v "^#" | grep -v "^$" | wc -l | tr -d ' ')
if [ "$CRON_LINES" -eq 0 ]; then
    fail "crontab 为空！所有定时任务已丢失"
    info "修复：从仓库 jobs_registry.yaml 重新注册所有 cron 条目"
    info "  crontab -l  # 确认当前状态"
else
    pass "crontab 有 $CRON_LINES 条活跃条目"

    # 检查关键 job 是否在 crontab 中
    EXPECTED_JOBS=(
        "run_arxiv.sh|ArXiv监控"
        "run_hn_fixed.sh|HN抓取"
        "run_freight.sh|货代Watcher"
        "job_watchdog.sh|元监控"
        "auto_deploy.sh|自动部署"
        "wa_keepalive.sh|WA保活"
        "kb_evening.sh|KB晚间"
        "kb_inject.sh|KB摘要"
        "cron_canary.sh|Cron心跳"
    )

    CRON_CONTENT=$(crontab -l 2>/dev/null)
    MISSING_JOBS=0
    for entry in "${EXPECTED_JOBS[@]}"; do
        IFS='|' read -r script name <<< "$entry"
        if echo "$CRON_CONTENT" | grep -q "$script"; then
            pass "$name ($script) 已注册"
        else
            # cron_canary 可能尚未注册，标记为 warn 而非 fail
            if [ "$script" = "cron_canary.sh" ]; then
                warn "$name ($script) 未注册（建议添加）"
            else
                fail "$name ($script) 不在 crontab 中！"
                MISSING_JOBS=$((MISSING_JOBS + 1))
            fi
        fi
    done

    if [ "$MISSING_JOBS" -gt 0 ]; then
        info "修复：对比 jobs_registry.yaml 补齐缺失条目"
    fi
fi

# ═══════════════════════════════════════════════════════════════════
# 2. 陈旧锁文件检测 — 第二常见的"全部静默跳过"原因
# ═══════════════════════════════════════════════════════════════════
echo ""
echo "🔍 2/7 锁文件检测"

LOCK_DIRS=(
    "/tmp/arxiv_monitor.lockdir|ArXiv监控"
    "/tmp/hn_watcher.lockdir|HN抓取"
    "/tmp/freight_watcher.lockdir|货代Watcher"
    "/tmp/job_watchdog.lockdir|元监控"
    "/tmp/auto_deploy.lockdir|自动部署"
    "/tmp/openclaw_run.lockdir|OpenClaw版本"
    "/tmp/run_discussions.lockdir|Issues监控"
    "/tmp/kb_review.lockdir|KB回顾"
    "/tmp/kb_evening.lockdir|KB晚间"
)

STALE_LOCKS=0
for entry in "${LOCK_DIRS[@]}"; do
    IFS='|' read -r lock_path name <<< "$entry"
    if [ -d "$lock_path" ]; then
        # 检查锁文件年龄
        if [ "$(uname)" = "Darwin" ]; then
            # macOS: stat -f %m 返回 epoch
            LOCK_EPOCH=$(stat -f %m "$lock_path" 2>/dev/null || echo "0")
        else
            # Linux: stat -c %Y
            LOCK_EPOCH=$(stat -c %Y "$lock_path" 2>/dev/null || echo "0")
        fi
        LOCK_AGE=$(( NOW_EPOCH - LOCK_EPOCH ))
        LOCK_HOURS=$(( LOCK_AGE / 3600 ))
        LOCK_MINS=$(( (LOCK_AGE % 3600) / 60 ))

        if [ "$LOCK_AGE" -gt 3600 ]; then
            fail "$name: 陈旧锁 $lock_path （已存在 ${LOCK_HOURS}h${LOCK_MINS}m）"
            STALE_LOCKS=$((STALE_LOCKS + 1))
        elif [ "$LOCK_AGE" -gt 600 ]; then
            warn "$name: 锁 $lock_path 已存在 ${LOCK_MINS}m（可能正在执行）"
        else
            info "$name: 锁 $lock_path 存在 ${LOCK_MINS}m（正常执行中）"
        fi
    fi
done

if [ "$STALE_LOCKS" -gt 0 ]; then
    fail "发现 $STALE_LOCKS 个陈旧锁文件！这会导致对应任务永远跳过执行"
    info "修复：rmdir /tmp/*.lockdir  （清除所有锁文件）"
    info "根因：可能是 Mac Mini 异常重启或进程被 kill -9"
elif [ "$STALE_LOCKS" -eq 0 ]; then
    pass "无陈旧锁文件"
fi

# ═══════════════════════════════════════════════════════════════════
# 3. Cron 心跳检测 — 验证 cron daemon 实际在运行
# ═══════════════════════════════════════════════════════════════════
echo ""
echo "🔍 3/7 Cron 心跳检测"

CANARY_FILE="$HOME/.cron_canary"
if [ -f "$CANARY_FILE" ]; then
    CANARY_CONTENT=$(cat "$CANARY_FILE" 2>/dev/null)
    CANARY_EPOCH=$(echo "$CANARY_CONTENT" | head -1 | tr -d '[:space:]')

    # 验证是有效数字
    if [[ "$CANARY_EPOCH" =~ ^[0-9]+$ ]]; then
        CANARY_AGE=$(( NOW_EPOCH - CANARY_EPOCH ))
        CANARY_MINS=$(( CANARY_AGE / 60 ))

        if [ "$CANARY_AGE" -gt 1800 ]; then
            fail "Cron 心跳已 ${CANARY_MINS}m 未更新（阈值 30m）"
            info "可能原因：cron daemon 停止、crontab 被清空、或 cron_canary.sh 未注册"
            info "检查：sudo launchctl list | grep cron"
        elif [ "$CANARY_AGE" -gt 900 ]; then
            warn "Cron 心跳 ${CANARY_MINS}m 前更新（正常范围但接近阈值）"
        else
            pass "Cron 心跳正常（${CANARY_MINS}m 前更新）"
        fi
    else
        warn "心跳文件格式异常（内容: ${CANARY_CONTENT:0:50}）"
    fi
else
    warn "心跳文件 $CANARY_FILE 不存在（cron_canary.sh 可能未注册到 crontab）"
    info "注册：将 '*/10 * * * * bash $HOME/openclaw-model-bridge/cron_canary.sh' 加入 crontab"
fi

# ═══════════════════════════════════════════════════════════════════
# 4. 服务状态检查 — 三层服务级联依赖
# ═══════════════════════════════════════════════════════════════════
echo ""
echo "🔍 4/7 服务状态"

# Gateway :18789
GW_CODE=$(curl -s --max-time 5 -o /dev/null -w '%{http_code}' http://localhost:18789 2>/dev/null) || GW_CODE="000"
if [ "$GW_CODE" -ge 200 ] 2>/dev/null && [ "$GW_CODE" -lt 400 ] 2>/dev/null; then
    pass "Gateway :18789 (HTTP $GW_CODE)"
else
    fail "Gateway :18789 不可达 (HTTP $GW_CODE)"
    info "修复：sudo launchctl kickstart -k system/com.openclaw.gateway"
fi

# Proxy :5002
PROXY_RESP=$(curl -s --max-time 5 http://localhost:5002/health 2>/dev/null) || PROXY_RESP=""
if echo "$PROXY_RESP" | python3 -c "import json,sys; d=json.load(sys.stdin); assert d.get('ok')" 2>/dev/null; then
    pass "Tool Proxy :5002 健康"
    # 检查 Proxy→Adapter 级联
    ADAPTER_OK=$(echo "$PROXY_RESP" | python3 -c "import json,sys; print(json.load(sys.stdin).get('adapter', False))" 2>/dev/null)
    if [ "$ADAPTER_OK" = "True" ]; then
        pass "Proxy→Adapter 级联正常"
    else
        warn "Proxy 运行但 Adapter 连接异常"
    fi
else
    fail "Tool Proxy :5002 不可达"
    info "修复：nohup python3 ~/tool_proxy.py > ~/tool_proxy.log 2>&1 &"
fi

# Adapter :5001
ADAPTER_RESP=$(curl -s --max-time 5 http://localhost:5001/health 2>/dev/null) || ADAPTER_RESP=""
if echo "$ADAPTER_RESP" | python3 -c "import json,sys; d=json.load(sys.stdin); assert d.get('ok')" 2>/dev/null; then
    pass "Adapter :5001 健康"
else
    fail "Adapter :5001 不可达"
    info "修复：nohup python3 ~/adapter.py > ~/adapter.log 2>&1 &"
fi

# ═══════════════════════════════════════════════════════════════════
# 5. 环境变量检查 — 模拟 cron 环境
# ═══════════════════════════════════════════════════════════════════
echo ""
echo "🔍 5/7 环境变量（cron 环境模拟）"

REQUIRED_VARS=(
    "REMOTE_API_KEY|GPU API认证"
    "OPENCLAW_PHONE|WhatsApp推送号码"
    "OPENCLAW|OpenClaw CLI路径"
    "KB_BASE|知识库目录"
    "GEMINI_API_KEY|多模态索引API"
)

for entry in "${REQUIRED_VARS[@]}"; do
    IFS='|' read -r var_name var_desc <<< "$entry"

    # 用 bash -lc 模拟 cron 环境
    VAL=$(bash -lc "echo \${$var_name:-}" 2>/dev/null)
    if [ -n "$VAL" ]; then
        if [ ${#VAL} -gt 8 ]; then
            MASKED="${VAL:0:4}...${VAL: -4}"
        else
            MASKED="***"
        fi
        pass "$var_name ($var_desc) = $MASKED"
    else
        # 区分必需和可选
        case "$var_name" in
            REMOTE_API_KEY|OPENCLAW_PHONE)
                fail "$var_name ($var_desc) 未设置！"
                info "修复：echo 'export $var_name=\"...\"' >> ~/.bash_profile && source ~/.bash_profile"
                ;;
            *)
                warn "$var_name ($var_desc) 未设置（部分 job 可能受影响）"
                ;;
        esac
    fi
done

# PATH 检查
HAS_BREW=$(bash -lc 'command -v brew >/dev/null 2>&1 && echo yes || echo no' 2>/dev/null)
HAS_OPENCLAW=$(bash -lc 'command -v openclaw >/dev/null 2>&1 && echo yes || echo no' 2>/dev/null)
HAS_PYTHON3=$(bash -lc 'command -v python3 >/dev/null 2>&1 && echo yes || echo no' 2>/dev/null)
HAS_CURL=$(bash -lc 'command -v curl >/dev/null 2>&1 && echo yes || echo no' 2>/dev/null)
HAS_JQ=$(bash -lc 'command -v jq >/dev/null 2>&1 && echo yes || echo no' 2>/dev/null)

[ "$HAS_BREW" = "yes" ] && pass "PATH: brew 可用" || fail "PATH: brew 不可用"
[ "$HAS_OPENCLAW" = "yes" ] && pass "PATH: openclaw 可用" || fail "PATH: openclaw 不可用"
[ "$HAS_PYTHON3" = "yes" ] && pass "PATH: python3 可用" || fail "PATH: python3 不可用"
[ "$HAS_CURL" = "yes" ] && pass "PATH: curl 可用" || fail "PATH: curl 不可用"
[ "$HAS_JQ" = "yes" ] && pass "PATH: jq 可用" || warn "PATH: jq 不可用（部分脚本需要）"

# ═══════════════════════════════════════════════════════════════════
# 6. Job 执行时效检查 — 每个 job 最后一次成功执行时间
# ═══════════════════════════════════════════════════════════════════
echo ""
echo "🔍 6/7 Job 执行时效"

STATUS_FILES=(
    "$HOME/.openclaw/jobs/arxiv_monitor/cache/last_run.json|ArXiv监控|25200"
    "$HOME/.openclaw/jobs/hn_watcher/cache/last_run.json|HN抓取|25200"
    "$HOME/.openclaw/jobs/freight_watcher/cache/last_run.json|货代Watcher|50400"
    "$HOME/.openclaw/jobs/openclaw_official/cache/last_run.json|OpenClaw版本|180000"
    "$HOME/.openclaw/jobs/openclaw_official/cache/last_run_discussions.json|Issues监控|10800"
    "$HOME/.kb/last_run_evening.json|KB晚间|180000"
)

OVERDUE_COUNT=0
for entry in "${STATUS_FILES[@]}"; do
    IFS='|' read -r status_file name max_silence <<< "$entry"

    if [ ! -f "$status_file" ]; then
        warn "$name: 状态文件不存在（从未运行？）"
        continue
    fi

    LAST_TIME=$(python3 -c "
import json
from datetime import datetime, timedelta, timezone
try:
    with open('$status_file') as f:
        d = json.load(f)
    t = d.get('time', '')
    dt = datetime.strptime(t, '%Y-%m-%d %H:%M:%S')
    dt_utc = dt - timedelta(hours=8)
    print(int(dt_utc.replace(tzinfo=timezone.utc).timestamp()))
except Exception:
    print(0)
" 2>/dev/null)

    if [ "${LAST_TIME:-0}" -eq 0 ]; then
        warn "$name: 状态文件格式异常"
        continue
    fi

    ELAPSED=$(( NOW_EPOCH - LAST_TIME ))
    HOURS=$(( ELAPSED / 3600 ))

    if [ "$ELAPSED" -gt "$max_silence" ]; then
        MAX_H=$(( max_silence / 3600 ))
        fail "$name: 已 ${HOURS}h 未执行（阈值 ${MAX_H}h）"
        OVERDUE_COUNT=$((OVERDUE_COUNT + 1))
    else
        pass "$name: ${HOURS}h 前执行（正常）"
    fi

    # 检查最后执行状态
    LAST_STATUS=$(python3 -c "
import json
try:
    with open('$status_file') as f:
        print(json.load(f).get('status', 'unknown'))
except Exception:
    print('unknown')
" 2>/dev/null)

    case "$LAST_STATUS" in
        ok|success) ;;
        fetch_failed|parse_failed|send_failed)
            warn "$name: 最近状态 = $LAST_STATUS"
            ;;
    esac
done

if [ "$OVERDUE_COUNT" -gt 3 ]; then
    info "多个 job 同时超时 → 很可能是系统级问题（crontab/锁文件/重启），而非单个 job 故障"
fi

# ═══════════════════════════════════════════════════════════════════
# 7. 系统状态检查 — 磁盘/重启/进程
# ═══════════════════════════════════════════════════════════════════
echo ""
echo "🔍 7/7 系统状态"

# 最近重启时间
BOOT_TIME=$(sysctl -n kern.boottime 2>/dev/null | sed 's/.*sec = \([0-9]*\).*/\1/' || echo "0")
if [ "$BOOT_TIME" -gt 0 ] 2>/dev/null; then
    UPTIME_SECS=$(( NOW_EPOCH - BOOT_TIME ))
    UPTIME_HOURS=$(( UPTIME_SECS / 3600 ))
    UPTIME_DAYS=$(( UPTIME_HOURS / 24 ))
    if [ "$UPTIME_HOURS" -lt 2 ]; then
        warn "系统最近重启（uptime: ${UPTIME_HOURS}h）— 检查锁文件是否残留"
    else
        pass "系统运行 ${UPTIME_DAYS}d ${UPTIME_HOURS}h"
    fi
else
    # Linux fallback
    if command -v uptime >/dev/null 2>&1; then
        UPTIME_STR=$(uptime -p 2>/dev/null || uptime)
        info "系统运行时间: $UPTIME_STR"
    fi
fi

# 磁盘空间
DISK_PCT=$(df -h / 2>/dev/null | tail -1 | awk '{print $5}' | tr -d '%')
if [ "${DISK_PCT:-0}" -gt 95 ] 2>/dev/null; then
    fail "磁盘使用 ${DISK_PCT}%（>95%）— /tmp 可能无法创建锁文件"
    info "修复：清理大文件（~/tool_proxy.log, ~/adapter.log 等）"
elif [ "${DISK_PCT:-0}" -gt 85 ] 2>/dev/null; then
    warn "磁盘使用 ${DISK_PCT}%（>85%）"
else
    pass "磁盘使用 ${DISK_PCT:-?}%"
fi

# /tmp 是否可写
if TMPTEST=$(mktemp -d /tmp/cron_doctor_test.XXXXX 2>/dev/null); then
    rmdir "$TMPTEST"
    pass "/tmp 可写"
else
    fail "/tmp 不可写！所有锁文件创建将失败，job 行为不可预测"
fi

# 日志文件大小检查
for logfile in ~/tool_proxy.log ~/adapter.log; do
    if [ -f "$logfile" ]; then
        LOG_SIZE=$(du -sm "$logfile" 2>/dev/null | cut -f1)
        if [ "${LOG_SIZE:-0}" -gt 500 ]; then
            warn "$(basename $logfile): ${LOG_SIZE}MB（>500MB，影响性能）"
            info "修复：tail -10000 $logfile > ${logfile}.tmp && mv ${logfile}.tmp $logfile"
        fi
    fi
done

# ═══════════════════════════════════════════════════════════════════
# 汇总 + 修复建议
# ═══════════════════════════════════════════════════════════════════
echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║  通过: $PASS | 失败: $FAIL | 警告: $WARN"
echo "╚══════════════════════════════════════════════════════╝"

if [ $FAIL -gt 0 ]; then
    echo ""
    echo "🚨 发现 $FAIL 个问题需要修复"
    echo ""
    echo "📋 快速修复清单（按优先级排列）："
    echo ""

    if [ "$STALE_LOCKS" -gt 0 ]; then
        echo "  1️⃣  清除陈旧锁文件："
        echo "     rmdir /tmp/*.lockdir 2>/dev/null; echo 'locks cleared'"
        echo ""
    fi

    if [ "$CRON_LINES" -eq 0 ]; then
        echo "  2️⃣  恢复 crontab："
        echo "     对照 jobs_registry.yaml 重新注册所有条目"
        echo ""
    fi

    echo "  3️⃣  验证修复："
    echo "     # 等待 10 分钟后重新运行"
    echo "     bash cron_doctor.sh"
    exit 1
elif [ $WARN -gt 0 ]; then
    echo ""
    echo "⚠️  全部通过但有 $WARN 条警告，建议处理"
    exit 0
else
    echo ""
    echo "✅ 全部正常 — 定时任务系统健康"
    exit 0
fi
