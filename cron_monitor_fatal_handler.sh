#!/usr/bin/env bash
# ════════════════════════════════════════════════════════════════════════════
# cron_monitor_fatal_handler.sh — V37.9.63 公共 FATAL handler helper (MR-8/MR-19)
# ════════════════════════════════════════════════════════════════════════════
#
# 目的: 抽取 V37.9.60 + V37.9.61 的 7 个 inline _<script>_fatal_handler 公共逻辑,
#       消除 ~210 行 copy-paste, 兑现 MR-8 (copy-paste-is-a-bug-class) 正向兑现.
#
# V37.9.60: 4 个 cron 聚合监控类 (job_watchdog/governance_audit/daily_ops/auto_deploy)
# V37.9.61: 3 个 LLM-task 类 (kb_deep_dive/kb_evening/kb_review)
# V37.9.63: 抽公共 helper (本文件) + 顺势修复 V37.9.60 CLI 参数 bug
#
# 顺势修复: V37.9.60 我写的 6 个 fatal_handler 第二层 FAIL-OPEN 用了
#   `--channel-id` + `--content` 但 canonical CLI (notify.sh + auto_deploy + watchdog
#   主告警路径同款) 是 `--target` + `--message` + `--json`. 6 个 fatal_handler
#   第二层 FAIL-OPEN 在 notify 不可用时实际是死代码 (CLI 参数错). 本 helper 统一
#   到 canonical CLI 风格.
#
# 用法 (在 governed script 中):
#   # 1. source 本 helper
#   source "$(dirname "$0")/cron_monitor_fatal_handler.sh"
#
#   # 2. 配置 4 个变量
#   CRON_FATAL_LABEL="watchdog"
#   CRON_FATAL_LOG="$HOME/job_watchdog.log"
#   CRON_FATAL_BASH_X="bash -x ~/job_watchdog.sh"
#   CRON_FATAL_REASON="监控自身死亡! 5/5-5/12 silent 7 天血案防回归."
#
#   # 3. 注册 trap ERR (必须显式传 $LINENO, bash trap 内 $LINENO 是 trap 定义行)
#   trap '_cron_monitor_fatal_handler $LINENO' ERR
#
# 契约:
#   - 三层 FAIL-OPEN: stderr + 本地 .openclaw_alerts.log + notify→openclaw 直发
#   - 任一层失败不让 handler 自身崩 (`|| true` 兜底)
#   - canonical CLI: `--channel discord --target X --message Y --json`
#   - 不清理 LOCK / 不写 canary / 不发 EXIT (那是各脚本各自 EXIT trap 的职责)
#
# 必须显式声明这些变量在 caller 中可用 (任一缺失 → fallback 到 unknown 不崩):
#   CRON_FATAL_LABEL  - 脚本标识 (用于 fatal_msg 和 stderr 前缀)
#   CRON_FATAL_LOG    - 日志路径提示 (排查指引)
#   CRON_FATAL_BASH_X - bash -x 调试命令提示
#   CRON_FATAL_REASON - 一句话血案锚点 (V37.9.60 MR-19 etc.)
#
# 调用方 OPENCLAW_BIN 可选 — helper 用 ${OPENCLAW_BIN:-${OPENCLAW:-/opt/homebrew/bin/openclaw}}
# 三档 fallback 链, 兼容 watchdog (用 OPENCLAW) 和 6 governed scripts (用 OPENCLAW_BIN).

_cron_monitor_fatal_handler() {
    local exit_code=$?
    local line_no="${1:-unknown}"
    local label="${CRON_FATAL_LABEL:-unknown}"
    local log_path="${CRON_FATAL_LOG:-unknown}"
    local bash_x="${CRON_FATAL_BASH_X:-bash -x \$0}"
    local reason="${CRON_FATAL_REASON:-FATAL abort}"

    # 拼 [SYSTEM_ALERT] 消息 (V37.4.3 marker, 经 SOUL.md 规则 10 + INV-PA-001 隔离)
    local fatal_msg="[SYSTEM_ALERT] ${label} FATAL abort exit=${exit_code} line=${line_no} — ${reason} 排查 ${log_path} + ${bash_x}"

    # Layer 1: stderr (cron log 必看)
    echo "[${label}] 🚨 FATAL exit=${exit_code} at line=${line_no} (set -e abort)" >&2

    # Layer 2: 本地告警文件 (即使推送全失败也有证据)
    echo "[$(TZ=${SYSTEM_TZ:-Asia/Hong_Kong} date '+%Y-%m-%d %H:%M:%S')] ${label} FATAL abort exit=${exit_code} line=${line_no}" >> "$HOME/.openclaw_alerts.log" 2>/dev/null || true

    # Layer 3: 三层 FAIL-OPEN 推送 (notify → openclaw 直发 → 本地 log 已写)
    if command -v notify >/dev/null 2>&1; then
        notify "$fatal_msg" --topic alerts 2>/dev/null || true
    else
        # canonical CLI 风格 (notify.sh / auto_deploy / watchdog 主告警同款)
        # V37.9.63 修 V37.9.60 6 个 fatal_handler 第二层 CLI 参数 bug
        # (旧: --channel-id X --content Y → 新: --target X --message Y --json)
        local openclaw_bin="${OPENCLAW_BIN:-${OPENCLAW:-/opt/homebrew/bin/openclaw}}"
        if [ -x "$openclaw_bin" ]; then
            "$openclaw_bin" message send --channel discord --target "${DISCORD_CH_ALERTS:-}" --message "$fatal_msg" --json >/dev/null 2>&1 || true
        fi
    fi
}

# helper sentinel: caller 可用 [ -n "$CRON_MONITOR_FATAL_HANDLER_LOADED" ] 验证 source 成功
CRON_MONITOR_FATAL_HANDLER_LOADED="V37.9.63"
