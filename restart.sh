#!/bin/bash
set -euo pipefail
# restart.sh - 一键重启所有服务 / One-command restart all services
#
# V37.9.13 架构清理（2026-04-23）:
#   Adapter + Proxy 改用 `launchctl kickstart -k` 统一走 launchd 管理。
#   消除 V37.9.12.1 发现的 manual nohup 进程 + launchd KeepAlive 双管理
#   冲突（两路同时抢占 :5001 / :5002 端口 → launchd 侧持续 crash-loop）。
#   若 plist 不存在（dev 环境 / 未装 plist），fallback 到 nohup（向后兼容）。

# Ensure Homebrew binaries are in PATH (needed when called from cron)
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:$PATH"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
OPENCLAW="${OPENCLAW:-$(command -v openclaw 2>/dev/null || echo /opt/homebrew/bin/openclaw)}"
LAUNCH_AGENTS="$HOME/Library/LaunchAgents"
# V37.9.54: marker dir 追踪每个 label 的"上次成功加载 plist 时间"
# 用于检测 plist mtime > marker → 走 bootout/bootstrap 强制重读 plist 路径
PLIST_LOAD_MARKER_DIR="$HOME/.openclaw/restart_markers"
mkdir -p "$PLIST_LOAD_MARKER_DIR" 2>/dev/null || true

# Helper: restart a service under launchd management.
#   Signature: restart_via_launchd <label> <port> <plist> <display>
#   Returns: 0 success, 1 launchd failure, 2 launchd unavailable (caller fallback)
#
# V37.9.13 引入 `launchctl kickstart -k` (modern idempotent API).
# V37.9.54: kickstart -k 不重读 plist (V37.9.13 + V37.9.53 两次踩坑),
# 改用 plist mtime vs marker file mtime 判断是否需要 bootout/bootstrap 重读:
#   - plist 比 marker 新 (含无 marker, 视为首次启动) → 强制 bootout + bootstrap
#     (慢 ~1s 但保证 env 同步)
#   - plist 不比 marker 新 → kickstart -k 快路径 (V37.9.13 性能)
# 加载成功后 touch marker 记录时间. Marker 假设 restart.sh 是 plist 加载唯一入口.
# Applies V37.8.13 post-start health verification loop (5×2s curl probe).
restart_via_launchd() {
    local label="$1" port="$2" plist="$3" display="$4"
    if ! command -v launchctl >/dev/null 2>&1 || [ ! -f "$plist" ]; then
        return 2
    fi

    # V37.9.54: plist mtime > marker mtime → 需要 bootout/bootstrap 重读 plist
    local marker="$PLIST_LOAD_MARKER_DIR/${label}.loaded"
    local need_full_reload=1  # 默认 safe path (无 marker = 首次启动视为需重读)
    if [ -f "$marker" ]; then
        local plist_mtime marker_mtime
        plist_mtime=$(stat -f %m "$plist" 2>/dev/null || echo 0)
        marker_mtime=$(stat -f %m "$marker" 2>/dev/null || echo 0)
        if [ "$marker_mtime" -ge "$plist_mtime" ]; then
            need_full_reload=0  # marker 比 plist 新 → daemon 已加载该 plist 版本
        fi
    fi

    if [ "$need_full_reload" -eq 1 ]; then
        echo "[restart] $display: plist 更新于上次加载之后, bootout+bootstrap 重读 plist"
        launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
        sleep 1
        if ! launchctl bootstrap "gui/$(id -u)" "$plist" 2>/dev/null; then
            echo "[restart] ⚠️ $display bootstrap failed (label=$label)"
            return 1
        fi
    else
        echo "[restart] $display: launchctl kickstart -k gui/$(id -u)/$label (plist 无变化)"
        if ! launchctl kickstart -k "gui/$(id -u)/$label" 2>/dev/null; then
            # kickstart 失败 — 服务可能未注册, fallback 到 bootstrap
            launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
            if ! launchctl bootstrap "gui/$(id -u)" "$plist" 2>/dev/null; then
                echo "[restart] ⚠️ $display bootstrap failed (label=$label)"
                return 1
            fi
        fi
    fi

    # Health verification (V37.8.13 pattern extended from Gateway to adapter/proxy)
    local _attempt _code
    for _attempt in 1 2 3 4 5; do
        sleep 2
        _code=$(curl -s -o /dev/null -w "%{http_code}" --connect-timeout 2 --max-time 3 \
            "http://localhost:$port/health" 2>/dev/null || echo "000")
        if [ "$_code" = "200" ]; then
            echo "[restart] $display healthy (HTTP $_code after ${_attempt}×2s)"
            # V37.9.54: 加载成功后更新 marker 记录"该 plist 已加载到 daemon"
            touch "$marker" 2>/dev/null || true
            return 0
        fi
    done
    echo "[restart] ⚠️ $display failed to become healthy within 10s"
    return 1
}

# V37.9.308 (对抗审计 C4): 重启失败汇总 — 让 restart.sh 诚实退出非零。
# 血案: restart_via_launchd 的 rc=1 (bootstrap 失败 / 10s 内未健康) 此前只被
# `-eq 2` 分支看一眼就丢掉 → 脚本恒 exit 0 → auto_deploy 的
# `bash restart.sh || { ❌ restart.sh 失败 }` 是死代码 → 一次把 adapter/proxy
# 留在崩溃循环里的部署会被记成 "✅ 服务重启完成"。
# 更窄的窗口: 漂移修复路径 (auto_deploy DRIFT_REASON, HAS_NEW_COMMITS=false)
# 也会置 NEED_RESTART=true, 但其后的 preflight 兜底只在 HAS_NEW_COMMITS 时运行
# → 该路径下服务死掉零告警 (auto_deploy.log 在 job_watchdog 里只有 LOG_FRESHNESS,
#   不在 HOME_LOGS 错误扫描名单)。
# 刻意不加重试: launchd KeepAlive 已经在重试进程本身 (原则 #8 谁已经在管这件事),
# auto_deploy 侧再加一层重试 = 与 launchd 抢管理权的新机器 (日落法 #34)。
# 本修复只做一件事: 让既有的失败信号读到真值并抵达既有告警通道。
# bash 3.2 安全 (Mac Mini): 用字符串累加器而非数组 —— 空数组 ${arr[@]} 在
# `set -u` + bash 3.2 下报 unbound variable。
RESTART_FAILED=""

echo "[restart] Stopping Gateway..."
# Stop Gateway via launchctl (preserves plist registration knowledge for restart)
launchctl bootout "gui/$(id -u)/ai.openclaw.gateway" 2>/dev/null || true
# Kill any stray process on gateway port (adapter/proxy are handled below via launchd)
lsof -ti :18789 2>/dev/null | xargs kill 2>/dev/null || true
sleep 2

# ── Adapter (:5001) ──────────────────────────────────────────────────
# V37.9.13: single manager (launchd) instead of nohup + launchd double management.
echo "[restart] Restarting Adapter on :5001..."
ADAPTER_PLIST="$LAUNCH_AGENTS/com.openclaw.adapter.plist"
_ad_rc=0
restart_via_launchd "com.openclaw.adapter" 5001 "$ADAPTER_PLIST" "Adapter" || _ad_rc=$?
if [ "$_ad_rc" -eq 2 ]; then
    echo "[restart] Adapter plist not found, fallback to nohup (no auto-restart)"
    lsof -ti :5001 2>/dev/null | xargs kill 2>/dev/null || true
    sleep 1
    nohup python3 "$SCRIPT_DIR/adapter.py" > ~/adapter.log 2>&1 &
    disown
    sleep 1
elif [ "$_ad_rc" -ne 0 ]; then
    # V37.9.308: rc=1 = launchd 失败或 10s 内未健康 —— 此前静默丢弃
    RESTART_FAILED="${RESTART_FAILED}Adapter(:5001) "
fi

# ── Tool Proxy (:5002) ───────────────────────────────────────────────
echo "[restart] Restarting Tool Proxy on :5002..."
PROXY_PLIST="$LAUNCH_AGENTS/com.openclaw.proxy.plist"
_px_rc=0
restart_via_launchd "com.openclaw.proxy" 5002 "$PROXY_PLIST" "Tool Proxy" || _px_rc=$?
if [ "$_px_rc" -eq 2 ]; then
    echo "[restart] Tool Proxy plist not found, fallback to nohup (no auto-restart)"
    lsof -ti :5002 2>/dev/null | xargs kill 2>/dev/null || true
    sleep 1
    nohup python3 "$SCRIPT_DIR/tool_proxy.py" > ~/tool_proxy.log 2>&1 &
    disown
    sleep 1
elif [ "$_px_rc" -ne 0 ]; then
    # V37.9.308: 同 Adapter —— rc=1 不再静默
    RESTART_FAILED="${RESTART_FAILED}ToolProxy(:5002) "
fi

# ── Gateway (:18789) ─────────────────────────────────────────────────
# V37.9.140 日落法退役: 原 #48703 listeners-Map 自动补丁段已移除 — 上游 2026.3.23
# 修复, Gateway 已升级 v2026.4.27 (2026-06-11, V37.9.138)。该段曾是 restart.sh 唯一
# 的 sudo 依赖。上游回归监控由 preflight_check.sh 12/19 检查承担 (保留, 验证修复仍在)。
echo "[restart] Starting Gateway via launchd..."
GATEWAY_PLIST="$HOME/Library/LaunchAgents/ai.openclaw.gateway.plist"
if [ -f "$GATEWAY_PLIST" ]; then
    # bootout first (ignore error if not loaded)
    launchctl bootout "gui/$(id -u)/ai.openclaw.gateway" 2>/dev/null || true
    sleep 1

    # V37.8.13: Post-bootstrap health verification (2026-04-16 血案：Gateway bootstrap
    # 成功但 21 秒内崩溃，restart.sh 报 "Done!" 却 Gateway 已死。现在主动等待并验证)
    GATEWAY_HEALTHY=false
    # V37.9.308 (对抗审计 C4): bootstrap 加守卫 —— 此前是裸调用, 在 `set -e` 下失败
    # 即刻杀掉脚本, 而上一行刚 bootout 成功 → Gateway 被留在 booted-out 态 (launchd
    # 不再认识该 label, KeepAlive 拉不起来), 且健康验证/⚠️ 日志/失败汇总全部不执行。
    # 守卫后脚本走完流程、留下可读日志、并经 RESTART_FAILED 退出非零。
    if ! launchctl bootstrap "gui/$(id -u)" "$GATEWAY_PLIST"; then
        echo "[restart] ⚠️ Gateway bootstrap 失败 (plist=$GATEWAY_PLIST) — 服务当前处于 booted-out 态"
        # V37.9.308: 只有 **bootstrap 失败** 才计入失败汇总, "15s 内没健康" 不计 ——
        # 二者不是同一种故障, V37.8.13 的决定只针对后者:
        #   · bootstrap 失败 = launchd 根本没接管这个 job → KeepAlive 无从重试 →
        #     永久 down 到人工介入 → 必须让调用方看见 (本行)。
        #   · bootstrap 成功但 15s 未健康 = launchd 已接管且正在重试, 多半是慢启动;
        #     此时 Adapter/Proxy 已正常, 让整个部署失败得不偿失 → 仅 ⚠️ 告警
        #     (V37.8.13 的原判断, 本次保留)。持续不健康由 wa_keepalive (每 30min
        #     真实发送验证) + job_watchdog 兜底, 不靠 restart.sh 的退出码。
        RESTART_FAILED="${RESTART_FAILED}Gateway(:18789,bootstrap失败) "
    else
        echo "[restart] Gateway loaded via launchd (KeepAlive enabled)"
        for _gw_attempt in 1 2 3 4 5; do
            sleep 3
            _gw_http=$(curl -s -o /dev/null -w "%{http_code}" --connect-timeout 2 --max-time 3 http://localhost:18789 2>/dev/null || echo "000")
            if [ "$_gw_http" -ge 200 ] 2>/dev/null && [ "$_gw_http" -lt 400 ] 2>/dev/null; then
                echo "[restart] Gateway health verified (HTTP $_gw_http after ${_gw_attempt}×3s)"
                GATEWAY_HEALTHY=true
                break
            fi
            echo "[restart] Gateway not yet healthy (attempt $_gw_attempt/5, HTTP $_gw_http)..."
        done
    fi

    if ! $GATEWAY_HEALTHY; then
        echo "[restart] ⚠️ Gateway failed to become healthy within 15s — needs manual investigation"
        echo "[restart] ⚠️ Check: launchctl list | grep gateway ; tail ~/.openclaw/logs/gateway.err.log"
        # V37.8.13 决定保留: 此处刻意 **不** 计入 RESTART_FAILED —— Adapter/Proxy 已正常,
        # 且 launchd 仍在 KeepAlive 重试, 不因慢启动让整个部署判失败。
    fi
else
    echo "[restart] WARNING: launchd plist not found, falling back to nohup (no auto-restart)"
    nohup "$OPENCLAW" gateway --verbose >> ~/gateway.log 2>&1 &
    disown
fi
sleep 3

# V37.9.308 (对抗审计 C4): 诚实退出码 —— 有服务没恢复健康就不报 Done!。
# 消费方 auto_deploy.sh 的失败分支据此被激活 (此前是死代码)。
if [ -n "$RESTART_FAILED" ]; then
    echo "[restart] ❌ 未恢复健康: ${RESTART_FAILED}— 详见上方日志"
    exit 1
fi
echo "[restart] Done!"
