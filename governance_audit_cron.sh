#!/bin/bash
# governance_audit_cron.sh — 每日定时治理审计（ontology-native 主动监控）
#
# 将 governance_checker.py 从"手动回归测试"升级为"生产定时监控"。
# 失败时通过 notify.sh 推送告警，不再依赖开发者手动发现。
#
# 2026-04-09 教训：governance_checker 有 17 个不变式但只在手动跑时执行，
# ontology Discord 频道从未收到推送的问题存在数周无人发现。
#
# 执行内容：
#   1. governance_checker.py --full（17 不变式 + 6 元发现规则）
#   2. engine.py --check（工具本体一致性 81 规则）
#   3. 失败 → notify.sh --topic alerts 告警
#   4. 结果写入状态文件供 watchdog 检查
#
# crontab: 0 7 * * *  bash -lc '~/governance_audit_cron.sh' >> ~/governance_audit.log 2>&1

export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:$PATH"
source "$HOME/.bash_profile" 2>/dev/null || source "$HOME/.env_shared" 2>/dev/null || true

# V37.9.60 MR-19 err_trap_handler 契约横向推广 (V37.9.58-hotfix3 watchdog 同款模式)
# 注: -E (errtrace) 让 ERR trap 在 function 内 fail 也触发, 防 bash 默认作用域陷阱
set -eEuo pipefail

TS="$(TZ=Asia/Hong_Kong date '+%Y-%m-%d %H:%M:%S')"
REPO_DIR="$HOME/openclaw-model-bridge"
STATUS_FILE="$HOME/.kb/last_run_governance_audit.json"
LOG_PREFIX="[$TS] governance_audit"

log() { echo "$LOG_PREFIX: $1"; }

# ── 加载 notify.sh (提前 source 让 ERR trap 可用) ────────────────────
NOTIFY_LOADED=false
for _np in "$REPO_DIR/notify.sh" "$HOME/notify.sh"; do
    if [ -f "$_np" ]; then
        source "$_np"
        NOTIFY_LOADED=true
        break
    fi
done

# ════════════════════════════════════════════════════════════════════
# V37.9.60 MR-19 ERR trap: silent abort 变 loud
# ════════════════════════════════════════════════════════════════════
# 血案模式: governance_audit 每日 07:00 cron 跑 set -euo + 无 trap ERR.
# 若 python3 ontology/governance_checker.py 不可执行 / module 缺失 / mkdir fail 等,
# set -e abort 整脚本死, STATUS_FILE 不写, [SYSTEM_ALERT] 不推 → 累积 silent.
# V37.9.60 复用 V37.9.58-hotfix3 watchdog 同款三层 FAIL-OPEN 推送.
OPENCLAW_BIN="${OPENCLAW:-/opt/homebrew/bin/openclaw}"

_governance_audit_fatal_handler() {
    local exit_code=$?
    local line_no="${1:-unknown}"
    local fatal_msg="[SYSTEM_ALERT] governance_audit FATAL abort exit=${exit_code} line=${line_no} — 治理审计自身死亡, governance 不变式当日未跑! V37.9.60 MR-19 横向推广防回归. 排查 ~/governance_audit.log + bash -x ~/governance_audit_cron.sh"
    # stderr 写错误日志 (cron log)
    echo "[governance_audit] 🚨 FATAL exit=${exit_code} at line=${line_no} (set -e abort)" >&2
    # 本地告警文件 (即使推送失败也有证据)
    echo "[$(TZ=Asia/Hong_Kong date '+%Y-%m-%d %H:%M:%S')] governance_audit FATAL abort exit=${exit_code} line=${line_no}" >> "$HOME/.openclaw_alerts.log" 2>/dev/null || true
    # 三层 FAIL-OPEN 推送 (notify → openclaw 直发 → 本地 log 已写)
    if command -v notify >/dev/null 2>&1; then
        notify "$fatal_msg" --topic alerts 2>/dev/null || true
    elif [ -x "$OPENCLAW_BIN" ]; then
        "$OPENCLAW_BIN" message send --channel discord --channel-id "${DISCORD_CH_ALERTS:-}" --content "$fatal_msg" 2>/dev/null || true
    fi
}
trap '_governance_audit_fatal_handler $LINENO' ERR

# ── 1. Governance Checker（不变式 + 元发现）────────────────────────────
log "开始 governance_checker.py --full"
GOV_OUTPUT=""
GOV_RC=0
GOV_OUTPUT=$(cd "$REPO_DIR" && python3 ontology/governance_checker.py --full 2>&1) || GOV_RC=$?

# 提取摘要行
GOV_SUMMARY=$(echo "$GOV_OUTPUT" | grep -E "通过:|不变式:" | head -2 | tr '\n' ' ')
GOV_VIOLATIONS=$(echo "$GOV_OUTPUT" | grep "❌" | head -5)
GOV_WARNINGS=$(echo "$GOV_OUTPUT" | grep "⚠️" | head -5)

log "governance_checker 完成: rc=$GOV_RC $GOV_SUMMARY"

# ── 2. Engine Check（工具本体一致性）──────────────────────────────────
log "开始 engine.py --check"
ENGINE_OUTPUT=""
ENGINE_RC=0
ENGINE_OUTPUT=$(cd "$REPO_DIR" && python3 ontology/engine.py --check 2>&1) || ENGINE_RC=$?

ENGINE_SUMMARY=$(echo "$ENGINE_OUTPUT" | tail -1)
log "engine_check 完成: rc=$ENGINE_RC $ENGINE_SUMMARY"

# ── 3. 结果判定 + 告警 ───────────────────────────────────────────────
OVERALL="pass"
ALERT_MSG=""

if [ "$GOV_RC" -ne 0 ]; then
    OVERALL="fail"
    ALERT_MSG="⚠️ Governance Audit 失败 ($TS)

不变式违反:
$GOV_VIOLATIONS

$GOV_SUMMARY"
fi

if [ "$ENGINE_RC" -ne 0 ]; then
    OVERALL="fail"
    ALERT_MSG="${ALERT_MSG:+$ALERT_MSG

}⚠️ Tool Ontology 一致性检查失败:
$ENGINE_SUMMARY"
fi

# 元发现警告（不阻断，但附加到报告）
if [ -n "$GOV_WARNINGS" ]; then
    WARN_COUNT=$(echo "$GOV_WARNINGS" | wc -l | tr -d ' ')
    log "元发现警告: $WARN_COUNT 项"
fi

# ── 4. 告警推送 ──────────────────────────────────────────────────────
if [ "$OVERALL" = "fail" ] && $NOTIFY_LOADED; then
    ALERT_MSG="$(echo "$ALERT_MSG" | head -c 3000)"
    notify "$ALERT_MSG" --topic alerts 2>/dev/null || log "WARN: 告警推送失败"
    log "已推送告警到 alerts 频道"
fi

# ── 5. 写入状态文件 ──────────────────────────────────────────────────
mkdir -p "$(dirname "$STATUS_FILE")"
cat > "$STATUS_FILE" <<EOF
{"time":"$TS","status":"$OVERALL","governance_rc":$GOV_RC,"engine_rc":$ENGINE_RC,"summary":"$GOV_SUMMARY"}
EOF

log "完成: overall=$OVERALL"
