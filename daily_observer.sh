#!/usr/bin/env bash
# daily_observer.sh -- V37.9.84 Daily Self-Critique thin wrapper
#
# Cron 06:30 calls daily_observer.py, pushes to Discord #daily only.
# Observer is for operator quality audit, not end-user push.
#
# V37.5.1 env-var heredoc pattern (no pipe+heredoc stdin conflict).
# V37.9.63 cron_monitor_fatal_handler helper (ERR trap alert).

set -eEuo pipefail

# -- path discovery --
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
OBSERVER_PY=""
for candidate in "$HOME/daily_observer.py" "$SCRIPT_DIR/daily_observer.py"; do
    if [ -f "$candidate" ]; then
        OBSERVER_PY="$candidate"
        break
    fi
done
if [ -z "$OBSERVER_PY" ]; then
    echo "[observer] FATAL: daily_observer.py not found" >&2
    exit 1
fi

# -- logging --
log() { echo "[observer] $(date '+%H:%M:%S') $*" >&2; }

# -- notification --
NOTIFY_SH=""
for candidate in "$HOME/notify.sh" "$SCRIPT_DIR/notify.sh"; do
    if [ -f "$candidate" ]; then
        NOTIFY_SH="$candidate"
        break
    fi
done
NOTIFY_LOADED=false
if [ -n "$NOTIFY_SH" ]; then
    # shellcheck disable=SC1090
    source "$NOTIFY_SH" && NOTIFY_LOADED=true
fi

send_alert() {
    local msg="[SYSTEM_ALERT] daily_observer $1"
    log "ALERT: $msg"
    if $NOTIFY_LOADED; then
        notify "$msg" --topic alerts 2>/dev/null || true
    fi
}

# -- ERR trap (V37.9.63 helper pattern) --
CRON_FATAL_LABEL="daily_observer"
CRON_FATAL_LOG="${HOME}/daily_observer.log"
CRON_FATAL_BASH_X="bash -x ~/daily_observer.sh"
CRON_FATAL_REASON="Daily Self-Critique observer crash"
HELPER_SH=""
for candidate in "$HOME/cron_monitor_fatal_handler.sh" "$SCRIPT_DIR/cron_monitor_fatal_handler.sh"; do
    if [ -f "$candidate" ]; then
        HELPER_SH="$candidate"
        break
    fi
done
if [ -n "$HELPER_SH" ]; then
    # shellcheck disable=SC1090
    source "$HELPER_SH"
    trap '_cron_monitor_fatal_handler $LINENO' ERR
else
    trap 'send_alert "FATAL exit=$? line=$LINENO"' ERR
fi

# -- date arg (default yesterday) --
TARGET_DATE="${1:-$(date -d 'yesterday' '+%Y%m%d' 2>/dev/null || date -v-1d '+%Y%m%d' 2>/dev/null || '')}"
if [ -z "$TARGET_DATE" ]; then
    log "WARN: cannot compute yesterday date, using default"
    TARGET_DATE=""
fi

DATE_ARG=""
if [ -n "$TARGET_DATE" ]; then
    DATE_ARG="--date $TARGET_DATE"
fi

# -- run observer (stdout=JSON, stderr=log) --
log "starting daily_observer.py ${DATE_ARG:-'(default yesterday)'}"

OBSERVER_OUTPUT=""
OBSERVER_OUTPUT=$(python3 "$OBSERVER_PY" --json $DATE_ARG) || {
    _rc=$?
    log "observer failed (exit=$_rc)"
    send_alert "observer.py failed (exit=$_rc)"
    exit 1
}

# -- parse all fields in one Python call (avoid 3 forks) --
PARSED=""
PARSED=$(echo "$OBSERVER_OUTPUT" | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    status = d.get('status', 'unknown')
    score = d.get('overall_score')
    score_str = str(score) if score is not None else 'N/A'
    discord = d.get('discord_summary', '')
    print(status)
    print(score_str)
    print(discord)
except Exception:
    print('parse_error')
    print('N/A')
    print('')
" 2>/dev/null) || PARSED=$'parse_error\nN/A\n'

STATUS=$(echo "$PARSED" | head -n1)
OVERALL_SCORE=$(echo "$PARSED" | sed -n '2p')
DISCORD_SUMMARY=$(echo "$PARSED" | tail -n +3)

log "status=$STATUS overall_score=$OVERALL_SCORE"

# -- save report (read-only: writes only to its own directory) --
CRITIQUE_DIR="${KB_DIR:-$HOME/.kb}/self_critique"
mkdir -p "$CRITIQUE_DIR"

REPORT_DATE="${TARGET_DATE:-$(date -d 'yesterday' '+%Y%m%d' 2>/dev/null || date -v-1d '+%Y%m%d' 2>/dev/null || date '+%Y%m%d')}"
REPORT_FILE="$CRITIQUE_DIR/daily_critique_${REPORT_DATE}.md"

python3 "$OBSERVER_PY" $DATE_ARG > "$REPORT_FILE" 2>/dev/null || {
    log "WARN: failed to write report file, continuing"
}

# -- push full report to WhatsApp + Discord dual-channel --
if [ -f "$REPORT_FILE" ] && $NOTIFY_LOADED; then
    REPORT_CONTENT=$(cat "$REPORT_FILE" 2>/dev/null || echo "")
    if [ -n "$REPORT_CONTENT" ]; then
        log "pushing full report to dual-channel (--topic daily)"
        notify "$REPORT_CONTENT" --topic daily 2>/dev/null || {
            log "WARN: push failed (non-fatal)"
        }
    fi
fi

# -- status file --
STATUS_FILE="${KB_DIR:-$HOME/.kb}/last_run_self_critique.json"
python3 -c "
import json, sys
d = {
    'time': '$(date -u '+%Y-%m-%dT%H:%M:%SZ')',
    'status': '$STATUS',
    'overall_score': '$OVERALL_SCORE',
    'report_file': '$REPORT_FILE',
    'version': 'V37.9.84'
}
json.dump(d, sys.stdout, ensure_ascii=False)
" > "$STATUS_FILE" 2>/dev/null || true

# -- result handling --
case "$STATUS" in
    ok)
        log "critique complete (score=$OVERALL_SCORE)"
        ;;
    no_outputs)
        log "no outputs found for target date (normal on quiet days)"
        ;;
    llm_failed)
        send_alert "LLM critique failed, rule-based detection completed"
        ;;
    *)
        log "status=$STATUS"
        ;;
esac

log "done"
