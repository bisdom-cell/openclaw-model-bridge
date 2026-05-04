#!/usr/bin/env bash
# movespeed_rsync_helper.sh — V37.9.27 错峰 + retry + fail-loud + capture 一站式包装
#
# Purpose: V37.9.26 watchdog 主动告警立即暴露真相 — V37.9.4 APFS 重建只解决文件
# 系统层 EPERM, 但 OS 调度层 SSD I/O 竞争仍每天 ~19 次 transient 失败影响 11
# 个 KB cron jobs. 本 helper 三层包装 rsync 调用消除大部分 transient incident:
#   Phase 1 错峰 sleep 30-180s (避多 cron 同秒触发抢 SSD I/O 总线)
#   Phase 2 retry 3 次 10s/20s 指数退避 (transient EPERM 通常 30s 内自愈)
#   Phase 3 全部失败才调 movespeed_incident_capture.sh 取证 + 推 WARN: SSD
#
# Usage: bash $HOME/movespeed_rsync_helper.sh <caller_path> -- <rsync args...>
# Example:
#   bash "$HOME/movespeed_rsync_helper.sh" "$0" -- -a "$KB_BASE/dreams/" \
#        "/Volumes/MOVESPEED/KB/dreams/"
#
# Replaces existing pattern at 20 sites (V37.9.4 INV-BACKUP-001 + V37.9.14
# INV-BACKUP-001 check 4):
#   旧:  rsync ... 2>&1 || { _rc=$?; echo "WARN..."; capture; }  # 1 long line
#   新:  bash "$HOME/movespeed_rsync_helper.sh" "$0" -- ...      # 1 clean line
#
# Env override (testing only):
#   MOVESPEED_RSYNC_NO_SLEEP=1     skip Phase 1 jitter (deterministic test)
#   MOVESPEED_RSYNC_MAX_ATTEMPTS=N override Phase 2 retry count (default 3)
#   MOVESPEED_RSYNC_NO_RETRY=1     equivalent to MAX_ATTEMPTS=1
#   MOVESPEED_RSYNC_BACKOFF_BASE=N override base backoff seconds (default 10)
#
# Exit codes:
#   0    rsync succeeded (possibly after retry)
#   2    usage error (missing args / no -- separator)
#   N    rsync's exit code if all retries failed
#
# Output:
#   stdout: rsync's normal output (transparent passthrough)
#   stderr: phase markers + retry diagnostics (don't pollute stdout for callers
#           that pipe rsync output to scripts — safe by V37.8.6 MR-11 stderr rule)

set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd 2>/dev/null)" || SCRIPT_DIR="$HOME"
CAPTURE_HELPER="$SCRIPT_DIR/movespeed_incident_capture.sh"

# ── Argument parsing ──────────────────────────────────────────────────────
if [ $# -lt 2 ]; then
    echo "usage: $(basename "$0") <caller_path> -- <rsync args...>" >&2
    echo "  caller_path: typically \"\$0\" from invoking script" >&2
    echo "  --: separator before rsync args" >&2
    exit 2
fi
CALLER="$1"
shift
if [ "$1" != "--" ]; then
    echo "ERROR: missing -- separator before rsync args (got: $1)" >&2
    exit 2
fi
shift  # consume --
# Remaining $@ is rsync args

# ── Phase 1: 错峰抖动 (30-180s) — 避免多 cron 同秒触发抢 SSD I/O ─────────
if [ "${MOVESPEED_RSYNC_NO_SLEEP:-0}" != "1" ]; then
    # 30-180s uniform jitter (130s mean) — much shorter than the 5-15min
    # design first considered, to keep cron drift acceptable while still
    # decorrelating concurrent cron triggers.
    JITTER_S=$((30 + RANDOM % 151))  # 30 + 0..150 = 30..180
    echo "[$(basename "$CALLER")] movespeed_rsync_helper: 错峰 sleep ${JITTER_S}s 避 SSD I/O 同秒竞争" >&2
    sleep "$JITTER_S"
fi

# ── Phase 2: rsync with retry ─────────────────────────────────────────────
MAX_ATTEMPTS="${MOVESPEED_RSYNC_MAX_ATTEMPTS:-3}"
BACKOFF_BASE="${MOVESPEED_RSYNC_BACKOFF_BASE:-10}"
if [ "${MOVESPEED_RSYNC_NO_RETRY:-0}" = "1" ]; then
    MAX_ATTEMPTS=1
fi

# Defensive: clamp MAX_ATTEMPTS to [1, 10] (avoid env typo causing infinite loop)
case "$MAX_ATTEMPTS" in
    *[!0-9]*|"") MAX_ATTEMPTS=3 ;;
esac
if [ "$MAX_ATTEMPTS" -lt 1 ]; then MAX_ATTEMPTS=1; fi
if [ "$MAX_ATTEMPTS" -gt 10 ]; then MAX_ATTEMPTS=10; fi

EXIT_CODE=0
ATTEMPT=0
while [ "$ATTEMPT" -lt "$MAX_ATTEMPTS" ]; do
    ATTEMPT=$((ATTEMPT + 1))
    # Run rsync — preserve original 2>&1 behavior (caller's existing pattern)
    rsync "$@" 2>&1
    EXIT_CODE=$?
    if [ "$EXIT_CODE" -eq 0 ]; then
        if [ "$ATTEMPT" -gt 1 ]; then
            echo "[$(basename "$CALLER")] rsync recovered on attempt ${ATTEMPT}/${MAX_ATTEMPTS}" >&2
        fi
        exit 0
    fi
    if [ "$ATTEMPT" -lt "$MAX_ATTEMPTS" ]; then
        # Exponential backoff: BASE * attempt (10s, 20s, 30s by default)
        BACKOFF=$((BACKOFF_BASE * ATTEMPT))
        echo "[$(basename "$CALLER")] rsync attempt ${ATTEMPT}/${MAX_ATTEMPTS} exit=${EXIT_CODE}, retry in ${BACKOFF}s" >&2
        sleep "$BACKOFF"
    fi
done

# ── Phase 3: All retries failed → fail-loud + incident capture ────────────
# Preserve V37.9.4 INV-BACKUP-001 "WARN: SSD" string contract (governance
# guards grep for this literal in cron logs).
echo "[$(basename "$CALLER")] WARN: SSD rsync failed after ${MAX_ATTEMPTS} retries (exit=${EXIT_CODE})" >&2

# V37.9.14 INV-BACKUP-001 check 4 contract: invoke incident capture helper.
# Defensive: if helper missing (dev / partial deploy), don't break anything.
if [ -x "$CAPTURE_HELPER" ]; then
    "$CAPTURE_HELPER" "$EXIT_CODE" "$CALLER" || true
elif [ -f "$CAPTURE_HELPER" ]; then
    bash "$CAPTURE_HELPER" "$EXIT_CODE" "$CALLER" || true
fi

exit "$EXIT_CODE"
