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
# V37.9.63 MR-19 ERR trap: 调公共 helper cron_monitor_fatal_handler.sh (MR-8 抽公共)
# ════════════════════════════════════════════════════════════════════
# 之前 V37.9.60 inline _governance_audit_fatal_handler, V37.9.63 抽到 helper.
# helper 三层 FAIL-OPEN (stderr / 本地告警文件 / notify→openclaw 直发 canonical CLI).
OPENCLAW_BIN="${OPENCLAW:-/opt/homebrew/bin/openclaw}"

HELPER_DIR="$(cd "$(dirname "$0")" && pwd)"
if [ -f "$HELPER_DIR/cron_monitor_fatal_handler.sh" ]; then
    # shellcheck disable=SC1091
    source "$HELPER_DIR/cron_monitor_fatal_handler.sh"
    CRON_FATAL_LABEL="governance_audit"
    CRON_FATAL_LOG="$HOME/governance_audit.log"
    CRON_FATAL_BASH_X="bash -x ~/governance_audit_cron.sh"
    CRON_FATAL_REASON="治理审计自身死亡, governance 不变式当日未跑! V37.9.60 MR-19 横向推广防回归."
fi
trap '_cron_monitor_fatal_handler $LINENO' ERR

# ── 1. Governance Checker（不变式 + 元发现）────────────────────────────
log "开始 governance_checker.py --full"
GOV_OUTPUT=""
GOV_RC=0
# V37.9.105-hotfix → V37.9.214 日落法根治: set -E (errtrace) 让 $(...) 子 shell 继承
# ERR trap → governance/engine 退出 1 (真发现) 时子 shell 内失败误触发假 FATAL
# "治理审计自身死亡", 尽管外层 || RC=$? 已正确捕获. V37.9.105 用 `set +E` 包每个 $()
# 再 `set -E` 复原 — 但 bash 3.2 下每个 `set -E` re-enable 都是 landmine, 三次复发
# (V37.9.105 line 64 / line 100 / 2026-07-02 line 101) = whack-a-mole (dev bash 5.x
# 不复现, 纯 bash 3.2 errtrace quirk). V37.9.214 根治: `set +E` 一次 (errtrace 从此
# 关到脚本尾), 绝不再 re-enable — 消除整类 landmine. errexit (set -e) 仍在 → 真
# main-shell silent abort 仍触发 ERR trap (MR-19 核心保留); 顶部 set -eEuo 声明保留
# (governance check + cron_monitor_scanner 查的是声明); 两个审计 $() 都 errtrace-off
# (子 shell 不继承 trap, V37.9.105 保护保留); reporting 全 if/|| 安全.
#
# V37.9.116: CONVERGENCE_DRY_RUN=1 — 07:00 生产审计只检测+告警 drift, 不静默改 crontab.
# 血案: INV-CRON-004 auto_deploy 双行 3 次复发 (V37.9.106/111/V37.9.116). 真根因 = V37.9.58
# 切关 convergence dry-run 默认后, 此审计的 jobs_to_crontab machine_sync 每次 real-apply 重加
# line 54 (~/auto_deploy.sh HOME 裸名, _format_cron_line 输出) 与 canonical line 5
# (~/openclaw-model-bridge/auto_deploy.sh repo 路径) 格式错配 → 用户删了下次审计又加回.
# Mac Mini 原子实验铁证: 删除后=0 → 跑 no-dry-run governance --full → =1 (governance 重加).
# 修复原则 (MR-9 + V37.9.113 扩到生产审计): 治理"审计"是观察者, 必须检测+告警, 不静默 mutate
# 被审计的系统状态. machine_sync 自愈应是"显式刻意动作"(operator 手动跑无此 env 即 real-apply),
# 不是审计的副作用. drift 仍被检测 (governance 报 ⚠️/❌), 只是不自动 apply.
set +E   # V37.9.214: errtrace OFF for the whole audit-run + report phase — NO re-enable
GOV_OUTPUT=$(cd "$REPO_DIR" && CONVERGENCE_DRY_RUN=1 python3 ontology/governance_checker.py --full 2>&1) || GOV_RC=$?

# 提取摘要行
# V37.9.60-hotfix: grep | head subshell pipe 必须 || true 容错
# 否则 grep no-match exit 1 → pipefail → set -eE 让 ERR trap 触发 false-positive FATAL
# (governance_audit 实际成功跑完, 但 grep "❌" no match 时 ERR trap 误触发推 [SYSTEM_ALERT])
# V37.9.58-hotfix4 同款 bash quirk 横向修复
GOV_SUMMARY=$(echo "$GOV_OUTPUT" | grep -E "通过:|不变式:" | head -2 | tr '\n' ' ' || true)
# V37.9.214: grep BOTH ❌ (fail) AND 💥 (error) — a check that ERRORS (e.g. a
# --full runtime check whose subprocess hits the 4.27 openclaw cold-call and
# times out → TimeoutExpired → 💥, NOT ❌) was invisible in the alert (grep ❌
# only) → "Governance Audit 失败" fired with an EMPTY 不变式违反 section, telling
# the user THAT it failed but not WHICH check. Mirrors V37.9.213 F1 (surface
# the reason). Now cold-flake failures self-report the 💥 check name.
GOV_VIOLATIONS=$(echo "$GOV_OUTPUT" | grep -E "❌|💥" | head -5 || true)
GOV_WARNINGS=$(echo "$GOV_OUTPUT" | grep "⚠️" | head -5 || true)

log "governance_checker 完成: rc=$GOV_RC $GOV_SUMMARY"

# ── 2. Engine Check（工具本体一致性）──────────────────────────────────
log "开始 engine.py --check"
ENGINE_OUTPUT=""
ENGINE_RC=0
# V37.9.214: errtrace 已从 governance 块起单区域关闭 (无 re-enable landmine);
# engine $() 子 shell 同样不继承 ERR trap. 无 set +E/set -E 切换.
ENGINE_OUTPUT=$(cd "$REPO_DIR" && python3 ontology/engine.py --check 2>&1) || ENGINE_RC=$?

ENGINE_SUMMARY=$(echo "$ENGINE_OUTPUT" | tail -1)
log "engine_check 完成: rc=$ENGINE_RC $ENGINE_SUMMARY"

# ── 3. 结果判定 + 告警 ───────────────────────────────────────────────
# V37.9.72 (i): OVERALL="ok" 替代 "pass" — watchdog line 280 期望 "ok|unknown" 作正常状态,
# 实际写 "pass" 让 watchdog default 分支报"治理审计: 异常状态 (pass)" 误告警.
# 跨脚本契约对齐: 与 7+ 其他 ALIGNED jobs (V37.5/V37.8.10/V37.9.16/V37.9.39/40/41/43/44/45 等) 一致.
# governance_checker.py 内部 "pass" 是 check 状态真理源, 与本字面量解耦不动.
# 失败状态 "fail" (line 91/101) 不动 — 维持告警目的 (watchdog default 分支正确触发).
OVERALL="ok"
ALERT_MSG=""

if [ "$GOV_RC" -ne 0 ]; then
    OVERALL="fail"
    ALERT_MSG="⚠️ Governance Audit 失败 ($TS)

不变式违反 / 检查出错 (❌ fail / 💥 error):
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
