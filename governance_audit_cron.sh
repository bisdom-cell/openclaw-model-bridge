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

TS="$(TZ=${SYSTEM_TZ:-Asia/Hong_Kong} date '+%Y-%m-%d %H:%M:%S')"
REPO_DIR="$HOME/openclaw-model-bridge"
STATUS_FILE="$HOME/.kb/last_run_governance_audit.json"
LOG_PREFIX="[$TS] governance_audit"

log() { echo "$LOG_PREFIX: $1"; }

# ── V37.9.301 告警正文分层渲染 helper（血案注释见下方 GOV_VIOLATIONS 提取处）──
# 注: 两者都只在 `set +E` 之后被调用（errtrace 已关, 不会假 FATAL）; 每个 pipe
# 都带 `|| true`（本脚本 set -eEuo pipefail, head 提前关管道会让 printf 收 SIGPIPE
# → pipefail 非零 → set -e abort, V37.9.60-hotfix 同款 bash quirk）。
_line_count() {
    if [ -z "$1" ]; then echo 0; else echo "$1" | wc -l | tr -d ' '; fi
}

# 有界渲染: $1=行块 $2=最大行数 $3=截断提示里的量词。超出部分不静默丢弃, 显式说明
# 还有多少 + 去哪看全量（V37.9.300 教训: 截断不留痕 = 正文读起来像完整的）。
_bounded_block() {
    local _blk="$1" _max="$2" _what="$3" _total _rest
    _total=$(_line_count "$_blk")
    if [ "$_total" -eq 0 ]; then return 0; fi
    echo "$_blk" | head -"$_max" || true
    if [ "$_total" -gt "$_max" ]; then
        _rest=$(( _total - _max ))
        echo "（还有 ${_rest} ${_what}未显示，完整见 ~/governance_audit.log）"
    fi
}

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
#
# V37.9.301 血案（2026-08-13, V37.9.300 告警疲劳同族第二例）: 旧版 `| head -5`
# 对治理输出是"抓明细丢标题"。governance_checker 渲染器的层级是
#     `  ❌ 🔴 [INV-XXX-001] 名称`                  2 空格 = 不变式级判定
#     `       ❌ check 名`                          7 空格 = check 级明细
#     `  ❌ N 个不变式被违反, 💥 M 个检查执行出错`   2 空格 = 顶层计数, 输出**最后**一行
# 一个 check 多的不变式就吃掉 5 个配额中的 4 个 → 排在后面的整条失败不变式静默消失,
# 而"一共几个"的顶层计数行因为排在最末永远第一个被丢 → 告警正文读起来像完整的。
# 连 V37.9.214 特意加进来的 💥（让冷启动 flake 自报家门）也在多失败时被截掉 = 那次
# 修复被截断打败。修法与 V37.9.300 auto_deploy 同款判据: 按缩进分层 + 顶层优先 +
# 显式截断提示（一物一形: 两个告警正文用同一套分层规则）。
GOV_VIOLATIONS=$(echo "$GOV_OUTPUT" | grep -E "^ {0,3}(❌|💥)" || true)
GOV_VIOLATION_DETAIL=$(echo "$GOV_OUTPUT" | grep -E "^ {4,}(❌|💥)" || true)
# V37.9.301: ⚠️ 只取顶层行（MRD 元发现 + convergence 漂移）。收敛框架的
# `  💥 [spec] ⚠️  error: ...` 以 💥 开头已进 GOV_VIOLATIONS, 不在此重复。
GOV_WARNINGS=$(echo "$GOV_OUTPUT" | grep -E "^ {0,3}⚠️" || true)

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
    # V37.9.301: 顶层判定（哪些不变式挂了 + 一共几个）优先占正文, 明细单列成段。
    # 顺序刻意是"先索引后明细": 用户从 07:00 告警最需要的是可行动的索引, 明细在日志里。
    _GOV_TOP_BLOCK=$(_bounded_block "$GOV_VIOLATIONS" 12 "个不变式") || true
    _GOV_DETAIL_BLOCK=$(_bounded_block "$GOV_VIOLATION_DETAIL" 6 "行明细") || true
    ALERT_MSG="⚠️ Governance Audit 失败 ($TS)

不变式违反 / 检查出错 (❌ fail / 💥 error):
$_GOV_TOP_BLOCK"
    if [ -n "$_GOV_DETAIL_BLOCK" ]; then
        ALERT_MSG="$ALERT_MSG

失败明细:
$_GOV_DETAIL_BLOCK"
    fi
    ALERT_MSG="$ALERT_MSG

$GOV_SUMMARY"
fi

if [ "$ENGINE_RC" -ne 0 ]; then
    OVERALL="fail"
    ALERT_MSG="${ALERT_MSG:+$ALERT_MSG

}⚠️ Tool Ontology 一致性检查失败:
$ENGINE_SUMMARY"
fi

# 元发现 / 收敛漂移警告（不阻断退出码, 但告警已经在发时附进正文 = 完整图景）
# V37.9.301: 旧版注释写"附加到报告", 代码却只 log 了个计数 —— 报告里从来没有过。
# 这正是 V37.9.299 血案的形状（决定性 ⚠️ 信号被算出来却从不送达: 那次是"向量索引已
# 106h 未更新", 它能到用户手里纯粹是搭了一个无关 ❌ 的顺风车）。
# 刻意**不**为 warn-only 的干净轮次新开推送（那会造噪声 = 正在治的病本身, 且 crontab
# 漂移/索引老化已分别被 preflight fail 与 job_watchdog 覆盖, 原则 #8 谁已经在管）:
# 只在告警**已经**触发时附上并存警告, 新增推送量 = 0（V37.9.300 同款理由）。
if [ -n "$GOV_WARNINGS" ]; then
    WARN_COUNT=$(_line_count "$GOV_WARNINGS")
    log "治理警告: $WARN_COUNT 项"
    if [ "$OVERALL" = "fail" ]; then
        _GOV_WARN_BLOCK=$(_bounded_block "$GOV_WARNINGS" 6 "项警告") || true
        ALERT_MSG="$ALERT_MSG

⚠️ 并存警告 ($WARN_COUNT 项, 不影响退出码):
$_GOV_WARN_BLOCK"
    fi
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
