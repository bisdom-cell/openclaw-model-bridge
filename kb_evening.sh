#!/bin/bash
# kb_evening.sh — V37.6 KB 晚间整理（fail-fast + registry-driven + LLM 深度分析）
#
# 用法：bash kb_evening.sh [天数，默认1]
# 职责：thin wrapper — 调用 kb_evening_collect.py 采集+LLM，写 evening 文件+拼接
#      dedup 报告+推送。dedup 报告作为 "KB 健康附注" 拼接到 evening 消息尾部。
#
# V37.6 改动（fixes evening dumb-summary bug class，复用 V37.5 架构）：
#   1. 全 Python 化：kb_evening_collect.py 复用 kb_review helpers，消除 shell 头脑
#   2. 源从 jobs_registry.yaml 读取（含 14 个 cron 源，不再只看 notes）
#   3. H2 drill-down 精确提取今日章节（不是"文件名前 80 字"）
#   4. LLM 深度分析：今日要闻 / 行动 / 明日关注 / 健康度
#   5. LLM 失败 → [SYSTEM_ALERT] 推送 alerts topic + exit 1（不再静默"今日无"）
#   6. dedup 报告保留，作为健康附注拼接到消息尾部
#
# cron 环境 PATH 极简，必须显式声明（原则 #13）
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:$PATH"
# 加载环境变量（cron 环境中 OPENCLAW_PHONE/DISCORD_CH_* 等必须从 profile 获取）
source "$HOME/.bash_profile" 2>/dev/null || source "$HOME/.env_shared" 2>/dev/null || true
set -euo pipefail

DATE=$(date +%Y%m%d)
DAYS="${1:-1}"
KB_DIR="${KB_BASE:-$HOME/.kb}"
EVENING_FILE="$KB_DIR/daily/evening_${DATE}.md"
PHONE="${OPENCLAW_PHONE:-+85200000000}"
TS="$(TZ=Asia/Hong_Kong date '+%Y-%m-%d %H:%M:%S')"
STATUS_FILE="$KB_DIR/last_run_evening.json"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REGISTRY="${KB_EVENING_REGISTRY:-$SCRIPT_DIR/jobs_registry.yaml}"

# Fallback: Mac Mini 部署目录也找一下
if [ ! -f "$REGISTRY" ]; then
    REGISTRY="$HOME/openclaw-model-bridge/jobs_registry.yaml"
fi
COLLECTOR="$SCRIPT_DIR/kb_evening_collect.py"
if [ ! -f "$COLLECTOR" ]; then
    COLLECTOR="$HOME/openclaw-model-bridge/kb_evening_collect.py"
fi
if [ ! -f "$COLLECTOR" ]; then
    COLLECTOR="$HOME/kb_evening_collect.py"
fi

mkdir -p "$KB_DIR/daily"

log() { echo "[$TS] kb_evening: $1" >&2; }

# Try to source notify.sh so we can use notify() with --topic daily/alerts
NOTIFY_SH=""
for candidate in "$SCRIPT_DIR/notify.sh" "$HOME/openclaw-model-bridge/notify.sh" "$HOME/notify.sh"; do
    if [ -f "$candidate" ]; then
        NOTIFY_SH="$candidate"
        break
    fi
done
if [ -n "$NOTIFY_SH" ]; then
    # shellcheck disable=SC1090
    source "$NOTIFY_SH" || true
fi

send_alert() {
    local reason="$1"
    local msg="[SYSTEM_ALERT] kb_evening 失败
时间: $TS
原因: $reason
降级处理: 未推送 evening 整理内容
建议: 检查 Adapter/Proxy 状态 + LLM 可用性 + 查看 $STATUS_FILE"
    if command -v notify >/dev/null 2>&1; then
        notify "$msg" --topic alerts >/dev/null 2>&1 || true
    else
        openclaw message send --channel whatsapp --target "$PHONE" --message "$msg" --json >/dev/null 2>&1 || true
    fi
}

write_status() {
    local status="$1"
    local llm_status="$2"
    local reason="${3:-}"
    python3 - "$STATUS_FILE" "$TS" "$status" "$llm_status" "$reason" << 'PYEOF'
import json, sys
path, ts, status, llm_status, reason = sys.argv[1:6]
payload = {
    "time": ts,
    "status": status,
    "llm_status": llm_status,
}
if reason:
    payload["reason"] = reason
with open(path, "w", encoding="utf-8") as f:
    json.dump(payload, f, ensure_ascii=False)
PYEOF
}

# ── 1. 预检 ──
if [ ! -f "$COLLECTOR" ]; then
    log "FATAL: kb_evening_collect.py 不存在"
    send_alert "kb_evening_collect.py 不存在（部署文件缺失）"
    write_status "collector_missing" "unknown" "kb_evening_collect.py not found"
    exit 1
fi

if [ ! -f "$REGISTRY" ]; then
    log "FATAL: jobs_registry.yaml 不存在 at $REGISTRY"
    send_alert "jobs_registry.yaml 不存在 (path=$REGISTRY)"
    write_status "registry_missing" "unknown" "jobs_registry.yaml not found"
    exit 1
fi

# ── 2. 调用 Python collector ──
log "开始 LLM 晚间整理（${DAYS} 天窗口，from registry）..."
COLLECTOR_OUTPUT=$(KB_DIR="$KB_DIR" DAYS="$DAYS" REGISTRY="$REGISTRY" python3 "$COLLECTOR" 2>&1) || {
    EXIT_CODE=$?
    log "ERROR: collector exited $EXIT_CODE"
    log "Output: $(echo "$COLLECTOR_OUTPUT" | head -5)"
    send_alert "collector exited $EXIT_CODE: $(echo "$COLLECTOR_OUTPUT" | head -3 | tr '\n' ' ')"
    write_status "collector_failed" "unknown" "exit $EXIT_CODE"
    exit 1
}

# ── 3. 解析 JSON 结果 ──
STATUS=$(echo "$COLLECTOR_OUTPUT" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("status","unknown"))' 2>/dev/null || echo "parse_error")

if [ "$STATUS" = "parse_error" ]; then
    log "ERROR: collector 输出非法 JSON"
    send_alert "collector 输出非法 JSON: $(echo "$COLLECTOR_OUTPUT" | head -3 | tr '\n' ' ')"
    write_status "parse_error" "unknown" "invalid JSON from collector"
    exit 1
fi

# ── 4. LLM 失败 → fail-fast，推送 [SYSTEM_ALERT] 并退出 ──
if [ "$STATUS" = "llm_failed" ]; then
    REASON=$(echo "$COLLECTOR_OUTPUT" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("reason","unknown"))')
    log "ERROR: LLM 晚间整理失败: $REASON"
    send_alert "LLM 晚间整理失败: $REASON"
    write_status "llm_failed" "failed" "$REASON"
    exit 1
fi

if [ "$STATUS" = "collector_failed" ]; then
    REASON=$(echo "$COLLECTOR_OUTPUT" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("reason","unknown"))')
    log "ERROR: collector 内部失败: $REASON"
    send_alert "collector 内部失败: $REASON"
    write_status "collector_failed" "unknown" "$REASON"
    exit 1
fi

if [ "$STATUS" != "ok" ]; then
    log "ERROR: 未知 status: $STATUS"
    send_alert "未知 status: $STATUS"
    write_status "unknown_status" "unknown" "$STATUS"
    exit 1
fi

# ── 5. LLM 成功 — 写 evening 文件 ──
# V37.5.1 反模式教训：禁止 pipe+heredoc stdin 冲突，用环境变量传 JSON
COLLECTOR_OUTPUT="$COLLECTOR_OUTPUT" EVENING_FILE="$EVENING_FILE" python3 << 'PYEOF'
import json, os
data = json.loads(os.environ["COLLECTOR_OUTPUT"])
with open(os.environ["EVENING_FILE"], "w", encoding="utf-8") as f:
    f.write(data["evening_markdown"])
PYEOF
log "晚间整理文件已生成: $EVENING_FILE"

# ── 6. 提取 wa_message + 统计信息 ──
WA_MSG=$(echo "$COLLECTOR_OUTPUT" | python3 -c 'import json,sys; print(json.load(sys.stdin)["wa_message"])')
NOTE_COUNT=$(echo "$COLLECTOR_OUTPUT" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("note_count",0))')
TODAY_NOTE_COUNT=$(echo "$COLLECTOR_OUTPUT" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("today_note_count",0))')
SOURCES_USED=$(echo "$COLLECTOR_OUTPUT" | python3 -c 'import json,sys; print(len(json.load(sys.stdin).get("sources_used",[])))')

# ── 7. KB 去重（原 kb_dedup 合并到晚间整理，作为健康附注）────────────
DEDUP_REPORT=""
DEDUP_OUTPUT=$(python3 "$HOME/kb_dedup.py" --no-push 2>&1) || true
DEDUP_REPORT=$(echo "$DEDUP_OUTPUT" | grep -v '^\[kb_dedup\]')
if [ -n "${DEDUP_REPORT// }" ]; then
    WA_MSG="$WA_MSG

━━━━━━━━━━━━━━━━━━━━

$DEDUP_REPORT"
fi

# 推送截断（WhatsApp 硬上限）
WA_MSG="$(echo "$WA_MSG" | head -c 4000)"

# ── 8. 推送 WhatsApp + Discord ──
if command -v notify >/dev/null 2>&1; then
    if notify "$WA_MSG" --topic daily >/dev/null 2>&1; then
        log "晚间整理已推送（WhatsApp + Discord #daily）"
        write_status "ok" "ok"
    else
        log "WARN: notify 推送失败，尝试直接 openclaw"
        if openclaw message send --channel whatsapp --target "$PHONE" --message "$WA_MSG" --json >/dev/null 2>&1; then
            openclaw message send --channel discord --target "${DISCORD_CH_DAILY:-}" --message "$WA_MSG" --json >/dev/null 2>&1 || true
            write_status "ok" "ok"
        else
            log "ERROR: 所有推送通道失败"
            send_alert "LLM 晚间整理成功但推送通道全部失败"
            write_status "send_failed" "ok" "all push channels failed"
            exit 1
        fi
    fi
else
    # notify.sh not available — direct openclaw
    SEND_ERR=$(mktemp)
    if openclaw message send --channel whatsapp --target "$PHONE" --message "$WA_MSG" --json >/dev/null 2>"$SEND_ERR"; then
        log "晚间整理已推送 WhatsApp"
        openclaw message send --channel discord --target "${DISCORD_CH_DAILY:-}" --message "$WA_MSG" --json >/dev/null 2>&1 || true
        write_status "ok" "ok"
    else
        log "ERROR: WhatsApp 推送失败: $(head -3 "$SEND_ERR" 2>/dev/null)"
        send_alert "WhatsApp 推送失败: $(head -3 "$SEND_ERR" 2>/dev/null | tr '\n' ' ')"
        write_status "send_failed" "ok" "openclaw send failed"
        rm -f "$SEND_ERR"
        exit 1
    fi
    rm -f "$SEND_ERR"
fi

# ── 9. rsync 备份 ──
rsync -a --quiet "$KB_DIR/" "/Volumes/MOVESPEED/KB/" 2>/dev/null || true

log "晚间整理 ${DATE} | 覆盖 ${SOURCES_USED} 源 | 笔记总数 ${NOTE_COUNT} | 今日新增 ${TODAY_NOTE_COUNT} 篇 | LLM: ✓"
log "晚间整理文件: ${EVENING_FILE}"

# ── 10. 日志轮转（保留 V37 前版本的功能）────────────────────────────
LOG_ROTATE_COUNT=0
LOG_ROTATE_LIMIT=$((100 * 1024))  # 100KB in bytes

_rotate_if_large() {
    local f="$1"
    f="${f/#\~/$HOME}"
    [ -f "$f" ] || return 0
    local size
    size=$(wc -c < "$f" 2>/dev/null || echo 0)
    if [ "$size" -gt "$LOG_ROTATE_LIMIT" ]; then
        local tmp
        tmp=$(mktemp)
        tail -200 "$f" > "$tmp" && mv "$tmp" "$f"
        LOG_ROTATE_COUNT=$((LOG_ROTATE_COUNT + 1))
    fi
}

for _log_file in \
    ~/conv_quality.log \
    ~/token_report.log \
    ~/kb_dedup.log \
    ~/kb_evening.log \
    ~/kb_embed.log \
    ~/kb_dream.log \
    ~/job_watchdog.log
do
    _rotate_if_large "$_log_file"
done

for _log_file in "$HOME/.openclaw/logs/jobs/"*.log; do
    _rotate_if_large "$_log_file"
done

log "日志轮转: $LOG_ROTATE_COUNT 个文件已清理"
