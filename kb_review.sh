#!/bin/bash
# kb_review.sh — KB 跨笔记回顾（V37.5: fail-fast + registry-driven + digest drill-down）
#
# 用法：bash kb_review.sh [天数，默认7]
# 职责：thin wrapper — 调用 kb_review_collect.py 采集+LLM，然后写 review 文件+推送。
#
# V37.5 改动（fixes 6-issue silent degradation bug class）：
#   1. 删除 shell 变量未导出导致 LLM 看空 prompt 的 bug（全部 Python 化）
#   2. 删除机械 fallback — LLM 失败时推送 [SYSTEM_ALERT] 并 exit 1（fail-fast）
#   3. 源枚举从 jobs_registry.yaml 读取（kb_source_file 字段），消除硬编码漂移
#   4. H2 章节解析替代行级日期匹配，drill-down 到论文粒度
#   5. status.json 字段诚实（llm_status: ok/failed，不再永远写 true）
#   6. 移除无实现的 follow-up 悬空承诺字符串

# cron 环境 PATH 极简，必须显式声明（原则 #13）
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:$PATH"
set -euo pipefail

DATE=$(date +%Y%m%d)
DAYS="${1:-7}"
KB_DIR="${KB_BASE:-$HOME/.kb}"
REVIEW_FILE="$KB_DIR/daily/review_${DATE}.md"
PHONE="${OPENCLAW_PHONE:-+85200000000}"
TS="$(TZ=Asia/Hong_Kong date '+%Y-%m-%d %H:%M:%S')"
STATUS_FILE="$KB_DIR/last_run_review.json"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REGISTRY="${KB_REVIEW_REGISTRY:-$SCRIPT_DIR/jobs_registry.yaml}"

# Fallback: Mac Mini 部署目录也找一下
if [ ! -f "$REGISTRY" ]; then
    REGISTRY="$HOME/openclaw-model-bridge/jobs_registry.yaml"
fi
COLLECTOR="$SCRIPT_DIR/kb_review_collect.py"
if [ ! -f "$COLLECTOR" ]; then
    COLLECTOR="$HOME/openclaw-model-bridge/kb_review_collect.py"
fi
if [ ! -f "$COLLECTOR" ]; then
    COLLECTOR="$HOME/kb_review_collect.py"
fi

mkdir -p "$KB_DIR/daily"

log() { echo "[$TS] kb_review: $1" >&2; }

# Try to source notify.sh so we can use notify() with --topic alerts
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
    # Always include [SYSTEM_ALERT] prefix — notify.sh --topic alerts injects it
    # too, but prepending here makes direct `openclaw message send` callers safe.
    local msg="[SYSTEM_ALERT] kb_review 失败
时间: $TS
原因: $reason
降级处理: 未推送 review 内容
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
    log "FATAL: kb_review_collect.py 不存在"
    send_alert "kb_review_collect.py 不存在（部署文件缺失）"
    write_status "collector_missing" "unknown" "kb_review_collect.py not found"
    exit 1
fi

if [ ! -f "$REGISTRY" ]; then
    log "FATAL: jobs_registry.yaml 不存在 at $REGISTRY"
    send_alert "jobs_registry.yaml 不存在 (path=$REGISTRY)"
    write_status "registry_missing" "unknown" "jobs_registry.yaml not found"
    exit 1
fi

# ── 2. 调用 Python collector ──
log "开始 LLM 深度分析（${DAYS} 天回顾，from registry）..."
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
    log "ERROR: LLM 分析失败: $REASON"
    send_alert "LLM 分析失败: $REASON"
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

# ── 5. LLM 成功 — 写 review 文件 + 推送 ──
# V37.5.1: 禁止 `echo ... | python3 - <<EOF` 反模式（pipe+heredoc 冲突，
# stdin 被 heredoc 覆盖，json.load(sys.stdin) 读到空串→JSONDecodeError）。
# 改用环境变量传 collector output，heredoc 只传代码，无 stdin 竞争。
COLLECTOR_OUTPUT="$COLLECTOR_OUTPUT" REVIEW_FILE="$REVIEW_FILE" python3 << 'PYEOF'
import json, os
data = json.loads(os.environ["COLLECTOR_OUTPUT"])
with open(os.environ["REVIEW_FILE"], "w", encoding="utf-8") as f:
    f.write(data["review_markdown"])
PYEOF
log "回顾文件已生成: $REVIEW_FILE"

# ── 6. 推送 WhatsApp + Discord ──
WA_MSG=$(echo "$COLLECTOR_OUTPUT" | python3 -c 'import json,sys; print(json.load(sys.stdin)["wa_message"])')
NOTE_COUNT=$(echo "$COLLECTOR_OUTPUT" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("note_count",0))')
SOURCES_USED=$(echo "$COLLECTOR_OUTPUT" | python3 -c 'import json,sys; print(len(json.load(sys.stdin).get("sources_used",[])))')

if command -v notify >/dev/null 2>&1; then
    if notify "$WA_MSG" --topic daily >/dev/null 2>&1; then
        log "回顾已推送（WhatsApp + Discord #daily）"
        write_status "ok" "ok"
    else
        log "WARN: notify 推送失败，尝试直接 openclaw"
        if openclaw message send --channel whatsapp --target "$PHONE" --message "$WA_MSG" --json >/dev/null 2>&1; then
            openclaw message send --channel discord --target "${DISCORD_CH_DAILY:-}" --message "$WA_MSG" --json >/dev/null 2>&1 || true
            write_status "ok" "ok"
        else
            log "ERROR: 所有推送通道失败"
            send_alert "LLM 分析成功但推送通道全部失败"
            write_status "send_failed" "ok" "all push channels failed"
            exit 1
        fi
    fi
else
    # notify.sh not available — direct openclaw
    SEND_ERR=$(mktemp)
    if openclaw message send --channel whatsapp --target "$PHONE" --message "$WA_MSG" --json >/dev/null 2>"$SEND_ERR"; then
        log "回顾已推送 WhatsApp"
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

# ── 7. rsync 备份 ──
rsync -a --quiet "$KB_DIR/" "/Volumes/MOVESPEED/KB/" 2>/dev/null || true

log "知识回顾 ${DATE} | 覆盖 ${SOURCES_USED} 源 | 本期笔记 ${NOTE_COUNT} 篇 | LLM: ✓"
log "回顾文件: ${REVIEW_FILE}"
