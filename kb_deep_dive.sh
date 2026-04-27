#!/bin/bash
# kb_deep_dive.sh — V37.9.16 每日深度分析 thin wrapper
#
# 每日 22:30 HKT 挑选今日最有价值的论文/文章，抓取原文 + LLM 深度分析
# + 推送 WhatsApp + Discord #daily + 归档到 ~/.kb/deep_dives/。
#
# 设计契约（镜像 kb_review.sh V37.5.1 模式）：
#   - 复用 kb_deep_dive.py 纯 Python collector，本文件仅 thin wrapper
#   - env-var heredoc 模式（禁止 `echo | python3 -` pipe+heredoc stdin 冲突）
#   - fail-fast：LLM 失败 → [SYSTEM_ALERT] + exit 1
#   - 优雅降级：no_candidates 推 [SYSTEM_ALERT] 提示（不 exit 1，允许明日再试）
#   - 双通道推送 WhatsApp + Discord #daily（通过 notify.sh topic=deep_dive）
#
# cron 环境 PATH 极简，必须显式声明（原则 #13）
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:$PATH"
set -euo pipefail

DATE=$(date +%Y-%m-%d)
KB_DIR="${KB_BASE:-$HOME/.kb}"
DEEP_DIR="$KB_DIR/deep_dives"
DEEP_FILE="$DEEP_DIR/${DATE}.md"
PHONE="${OPENCLAW_PHONE:-+85200000000}"
TS="$(TZ=Asia/Hong_Kong date '+%Y-%m-%d %H:%M:%S')"
STATUS_FILE="$KB_DIR/last_run_deep_dive.json"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REGISTRY="${KB_DEEP_DIVE_REGISTRY:-$SCRIPT_DIR/jobs_registry.yaml}"

# Mac Mini 部署目录 fallback
if [ ! -f "$REGISTRY" ]; then
    REGISTRY="$HOME/openclaw-model-bridge/jobs_registry.yaml"
fi
COLLECTOR="$SCRIPT_DIR/kb_deep_dive.py"
if [ ! -f "$COLLECTOR" ]; then
    COLLECTOR="$HOME/openclaw-model-bridge/kb_deep_dive.py"
fi
if [ ! -f "$COLLECTOR" ]; then
    COLLECTOR="$HOME/kb_deep_dive.py"
fi

mkdir -p "$DEEP_DIR"

log() { echo "[$TS] kb_deep_dive: $1" >&2; }

# Source notify.sh（topic=deep_dive 路由到 Discord #daily）
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
    local msg="[SYSTEM_ALERT] kb_deep_dive 失败
时间: $TS
原因: $reason
降级处理: 今日未产出深度分析
建议: 检查 Adapter/Proxy 状态 + 候选源数据 + 查看 $STATUS_FILE"
    if command -v notify >/dev/null 2>&1; then
        notify "$msg" --topic alerts >/dev/null 2>&1 || true
    else
        openclaw message send --channel whatsapp --target "$PHONE" --message "$msg" --json >/dev/null 2>&1 || true
    fi
}

write_status() {
    local status="$1"
    local reason="${2:-}"
    local mode="${3:-}"
    python3 - "$STATUS_FILE" "$TS" "$status" "$reason" "$mode" << 'PYEOF'
import json, sys
path, ts, status, reason, mode = sys.argv[1:6]
payload = {"time": ts, "status": status}
if reason:
    payload["reason"] = reason
if mode:
    payload["mode"] = mode
with open(path, "w", encoding="utf-8") as f:
    json.dump(payload, f, ensure_ascii=False)
PYEOF
}

# ── 1. 预检 ──
if [ ! -f "$COLLECTOR" ]; then
    log "FATAL: kb_deep_dive.py 不存在"
    send_alert "kb_deep_dive.py 不存在（部署文件缺失）"
    write_status "collector_missing" "kb_deep_dive.py not found"
    exit 1
fi

if [ ! -f "$REGISTRY" ]; then
    log "FATAL: jobs_registry.yaml 不存在 at $REGISTRY"
    send_alert "jobs_registry.yaml 不存在 (path=$REGISTRY)"
    write_status "registry_missing" "jobs_registry.yaml not found"
    exit 1
fi

# ── 2. 调用 Python collector ──
log "开始每日深度分析（从 registry 收集今日候选）..."
COLLECTOR_OUTPUT=$(KB_DIR="$KB_DIR" REGISTRY="$REGISTRY" python3 "$COLLECTOR" 2>&1) || {
    EXIT_CODE=$?
    log "ERROR: collector exited $EXIT_CODE"
    log "Output: $(echo "$COLLECTOR_OUTPUT" | head -5)"
    send_alert "collector exited $EXIT_CODE: $(echo "$COLLECTOR_OUTPUT" | head -3 | tr '\n' ' ')"
    write_status "collector_failed" "exit $EXIT_CODE"
    exit 1
}

# ── 3. 解析 JSON 状态 ──
STATUS=$(echo "$COLLECTOR_OUTPUT" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("status","unknown"))' 2>/dev/null || echo "parse_error")

if [ "$STATUS" = "parse_error" ]; then
    log "ERROR: collector 输出非法 JSON"
    send_alert "collector 输出非法 JSON: $(echo "$COLLECTOR_OUTPUT" | head -3 | tr '\n' ' ')"
    write_status "parse_error" "invalid JSON from collector"
    exit 1
fi

# ── 4. no_candidates → 推 [SYSTEM_ALERT] 提示（不 exit 1，允许明日再试）──
if [ "$STATUS" = "no_candidates" ]; then
    REASON=$(echo "$COLLECTOR_OUTPUT" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("reason","unknown"))')
    log "NOTICE: 无候选: $REASON"
    if command -v notify >/dev/null 2>&1; then
        notify "[SYSTEM_ALERT] kb_deep_dive 今日无⭐≥4 候选，跳过深度分析（明日再试）" --topic alerts >/dev/null 2>&1 || true
    fi
    write_status "no_candidates" "$REASON"
    exit 0
fi

# ── 5. LLM / collector 失败 → fail-fast ──
if [ "$STATUS" = "llm_failed" ]; then
    REASON=$(echo "$COLLECTOR_OUTPUT" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("reason","unknown"))')
    log "ERROR: LLM 分析失败: $REASON"
    send_alert "LLM 分析失败: $REASON"
    write_status "llm_failed" "$REASON"
    exit 1
fi

if [ "$STATUS" = "collector_failed" ]; then
    REASON=$(echo "$COLLECTOR_OUTPUT" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("reason","unknown"))')
    log "ERROR: collector 内部失败: $REASON"
    send_alert "collector 内部失败: $REASON"
    write_status "collector_failed" "$REASON"
    exit 1
fi

if [ "$STATUS" != "ok" ]; then
    log "ERROR: 未知 status: $STATUS"
    send_alert "未知 status: $STATUS"
    write_status "unknown_status" "$STATUS"
    exit 1
fi

# ── 6. LLM 成功 — 写归档 + 推送（env-var heredoc，V37.5.1 反模式防御）──
COLLECTOR_OUTPUT="$COLLECTOR_OUTPUT" DEEP_FILE="$DEEP_FILE" python3 << 'PYEOF'
import json, os
data = json.loads(os.environ["COLLECTOR_OUTPUT"])
with open(os.environ["DEEP_FILE"], "w", encoding="utf-8") as f:
    f.write(data["markdown"])
PYEOF
log "深度分析文件已生成: $DEEP_FILE"

DISCORD_MSG=$(echo "$COLLECTOR_OUTPUT" | python3 -c 'import json,sys; print(json.load(sys.stdin)["discord_message"])')
MODE=$(echo "$COLLECTOR_OUTPUT" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("mode","?"))')
PICK_TITLE=$(echo "$COLLECTOR_OUTPUT" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("pick",{}).get("title",""))')

# V37.9.21: WhatsApp 多窗口分片 — 镜像 kb_dream.sh:1263-1319 模式
# wa_parts 是 list[str], 单段时 length=1, 长内容自动分多段 [1/N] [2/N] ...
# 段间 sleep 1 避免 WhatsApp 消息乱序
WA_CHUNK_DIR=$(mktemp -d -t kb_deep_dive_wa_XXXXXX)
trap 'rm -rf "$WA_CHUNK_DIR"' EXIT

WA_PARTS_TOTAL=$(COLLECTOR_OUTPUT="$COLLECTOR_OUTPUT" CHUNK_DIR="$WA_CHUNK_DIR" python3 << 'PYEOF'
import json, os
data = json.loads(os.environ["COLLECTOR_OUTPUT"])
parts = data.get("wa_parts") or [data.get("wa_message", "")]
chunk_dir = os.environ["CHUNK_DIR"]
for idx, part in enumerate(parts):
    with open(os.path.join(chunk_dir, f"{idx:03d}.txt"), "w", encoding="utf-8") as f:
        f.write(part)
print(len(parts))
PYEOF
)

# ── 7. 推送 ──
# topic=deep_dive 路由到 Discord #daily（保留主 daily 频道作为深度分析归属）
# WhatsApp 收多段简版（V37.9.21 分片），Discord 收单条完整版（内容不同）— 用 --channel 分别发
WA_SEND_OK=0
WA_PART_IDX=0

send_wa_parts_via_notify() {
    for chunk_file in "$WA_CHUNK_DIR"/*.txt; do
        [ -f "$chunk_file" ] || continue
        WA_PART_IDX=$((WA_PART_IDX + 1))
        WA_SEGMENT=$(cat "$chunk_file")
        if notify "$WA_SEGMENT" --channel whatsapp --topic deep_dive >/dev/null 2>&1; then
            WA_SEND_OK=$((WA_SEND_OK + 1))
        else
            log "WARN: WhatsApp 第 $WA_PART_IDX/$WA_PARTS_TOTAL 段推送失败"
        fi
        # 段间间隔 1 秒，避免消息乱序（Dream 同款）
        [ "$WA_PART_IDX" -lt "$WA_PARTS_TOTAL" ] && sleep 1
    done
}

send_wa_parts_via_openclaw() {
    for chunk_file in "$WA_CHUNK_DIR"/*.txt; do
        [ -f "$chunk_file" ] || continue
        WA_PART_IDX=$((WA_PART_IDX + 1))
        WA_SEGMENT=$(cat "$chunk_file")
        if openclaw message send --channel whatsapp --target "$PHONE" --message "$WA_SEGMENT" --json >/dev/null 2>&1; then
            WA_SEND_OK=$((WA_SEND_OK + 1))
        else
            log "WARN: WhatsApp 第 $WA_PART_IDX/$WA_PARTS_TOTAL 段推送失败"
        fi
        [ "$WA_PART_IDX" -lt "$WA_PARTS_TOTAL" ] && sleep 1
    done
}

if command -v notify >/dev/null 2>&1; then
    # WhatsApp 多段（V37.9.21 分片）
    send_wa_parts_via_notify
    # Discord 完整版（单条）
    notify "$DISCORD_MSG" --channel discord --topic deep_dive >/dev/null 2>&1 || \
        log "WARN: Discord 推送失败"
    log "深度分析已推送（WhatsApp $WA_SEND_OK/$WA_PARTS_TOTAL 段 + Discord #daily 完整版）"
    write_status "ok" "" "$MODE"
else
    # notify.sh 不可用 — fallback 直接 openclaw
    send_wa_parts_via_openclaw
    openclaw message send --channel discord --target "${DISCORD_CH_DAILY:-}" --message "$DISCORD_MSG" --json >/dev/null 2>&1 || \
        log "WARN: Discord 推送失败"
    log "深度分析已推送（WhatsApp $WA_SEND_OK/$WA_PARTS_TOTAL 段 + Discord 完整版）"
    write_status "ok" "" "$MODE"
fi

# ── 8. rsync 备份（V37.9.14 事故取证模式，INV-BACKUP-001 check 4）──
rsync -a "$KB_DIR/" "/Volumes/MOVESPEED/KB/" 2>&1 || { _rc=$?; echo "[$(basename "$0")] WARN: SSD rsync failed (exit=$_rc)" >&2; "$HOME/movespeed_incident_capture.sh" "$_rc" "$0"; }

log "每日深度分析 ${DATE} | 模式: ${MODE} | 标题: ${PICK_TITLE}"
log "归档文件: ${DEEP_FILE}"
