#!/usr/bin/env bash
# movespeed_incident_capture.sh — V37.9.14
#
# Forensic snapshot helper. Called from the 20 rsync fail-loud branches
# targeting /Volumes/MOVESPEED (see INV-BACKUP-001).
#
# Purpose: when rsync fails, capture a JSONL line recording the filesystem +
# permission + process state at the moment of failure, so the next transient
# MOVESPEED EPERM reproduction has evidence instead of guesswork.
# (WARN: SSD rsync failure is logged separately by the caller's echo line;
# this helper adds structured forensic data on top, no replacement.)
#
# Contract: MUST NEVER FAIL. Every diagnostic command is wrapped with `|| true`
# or directed to a tmp file with `2>/dev/null`. Missing diagnostics become empty
# fields — they do not propagate a non-zero exit code.
#
# Usage: movespeed_incident_capture.sh <exit_code> <caller_path>
#
# Compat: bash 3.2 (macOS default). No ${var@Q}, no ${var^^}, no associative
# arrays, no mapfile.
#
# Environment overrides (for tests):
#   MOVESPEED_INCIDENT_FILE — target JSONL path
#   MOVESPEED_INCIDENT_MAX_SIZE — rotation threshold in bytes

# Intentionally no `set -e` or `set -o pipefail`: best-effort snapshot.

EXIT_CODE="${1:-unknown}"
CALLER_RAW="${2:-unknown}"
CALLER="$(basename "$CALLER_RAW" 2>/dev/null)"
[ -z "$CALLER" ] && CALLER="$CALLER_RAW"

INCIDENT_FILE="${MOVESPEED_INCIDENT_FILE:-${HOME}/.kb/movespeed_incidents.jsonl}"
MAX_FILE_SIZE="${MOVESPEED_INCIDENT_MAX_SIZE:-10485760}"  # 10 MB default

mkdir -p "$(dirname "$INCIDENT_FILE")" 2>/dev/null

# Rotate if oversized (keep .1 as prior incidents)
if [ -f "$INCIDENT_FILE" ]; then
    _size="$(stat -f%z "$INCIDENT_FILE" 2>/dev/null || stat -c%s "$INCIDENT_FILE" 2>/dev/null || echo 0)"
    if [ "${_size:-0}" -gt "$MAX_FILE_SIZE" ] 2>/dev/null; then
        mv "$INCIDENT_FILE" "${INCIDENT_FILE}.1" 2>/dev/null
    fi
fi

_TS="$(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null)"
[ -z "$_TS" ] && _TS="unknown"

_TMP="$(mktemp -d 2>/dev/null)"
if [ -z "$_TMP" ] || [ ! -d "$_TMP" ]; then
    # Cannot create tmp; still write a minimal line and bail.
    python3 - "$_TS" "$CALLER" "$EXIT_CODE" <<'PYEOF' >> "$INCIDENT_FILE" 2>/dev/null
import json, sys
ts, caller, ec = sys.argv[1:4]
print(json.dumps({"timestamp_iso": ts, "caller": caller, "exit_code": ec, "error": "tmpdir_failed"}, ensure_ascii=False))
PYEOF
    exit 0
fi

# --- Diagnostics (each best-effort; missing tool = empty field) ---

# Filesystem mount state
mount 2>/dev/null | grep -i -e MOVESPEED -e Volumes > "$_TMP/mount" 2>/dev/null

# macOS disk info (diskutil). On Linux this silently produces empty file.
diskutil info /Volumes/MOVESPEED > "$_TMP/diskutil" 2>/dev/null

# Directory listings: top + KB subdir
ls -la /Volumes/MOVESPEED/ > "$_TMP/ls_top" 2>/dev/null
ls -la /Volumes/MOVESPEED/KB/ > "$_TMP/ls_kb" 2>/dev/null

# Free-space snapshot
df -h /Volumes/MOVESPEED > "$_TMP/df" 2>/dev/null

# Permission probes: can we touch at the top, and under KB?
# (Transient EPERM is the thing we want to catch.)
_probe_top_file="/Volumes/MOVESPEED/.incident_probe_top_$$"
( touch "$_probe_top_file" ) 2> "$_TMP/probe_top_err"
echo "exit=$?" > "$_TMP/probe_top_rc"
rm -f "$_probe_top_file" 2>/dev/null

_probe_kb_file="/Volumes/MOVESPEED/KB/.incident_probe_kb_$$"
( touch "$_probe_kb_file" ) 2> "$_TMP/probe_kb_err"
echo "exit=$?" > "$_TMP/probe_kb_rc"
rm -f "$_probe_kb_file" 2>/dev/null

# Concurrent processes that commonly contend for the drive
ps -ax -o pid,etime,command 2>/dev/null | \
    grep -E 'rsync|backupd|mds|fseventsd|tmutil|Spotlight' | \
    grep -v grep > "$_TMP/procs" 2>/dev/null

# OS identity
sw_vers > "$_TMP/sw_vers" 2>/dev/null || uname -a > "$_TMP/sw_vers" 2>/dev/null

# --- Build JSON via python3 (argv parameters are escape-safe) ---
python3 - "$_TS" "$CALLER" "$EXIT_CODE" "$_TMP" <<'PYEOF' >> "$INCIDENT_FILE" 2>/dev/null
import json
import os
import sys

ts, caller, ec, td = sys.argv[1:5]


def read_file(name, limit=2000):
    path = os.path.join(td, name)
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fp:
            data = fp.read(limit + 1)
            if len(data) > limit:
                data = data[:limit] + "...[truncated]"
            return data.strip()
    except (IOError, OSError):
        return ""


rec = {
    "timestamp_iso": ts,
    "caller": caller,
    "exit_code": ec,
    "mount": read_file("mount", 400),
    "disk_info": read_file("diskutil", 2000),
    "ls_top": read_file("ls_top", 800),
    "ls_kb": read_file("ls_kb", 1200),
    "df": read_file("df", 300),
    "probe_top": read_file("probe_top_rc") + "|" + read_file("probe_top_err", 300),
    "probe_kb": read_file("probe_kb_rc") + "|" + read_file("probe_kb_err", 300),
    "procs": read_file("procs", 1500),
    "os": read_file("sw_vers", 200),
    "env": {
        "user": os.environ.get("USER", ""),
        "home": os.environ.get("HOME", ""),
        "shell": os.environ.get("SHELL", ""),
        "path_head": os.environ.get("PATH", "")[:200],
    },
}
print(json.dumps(rec, ensure_ascii=False))
PYEOF

# Clean up tmp dir; ignore errors
rm -rf "$_TMP" 2>/dev/null

# Contract: always succeed so caller's exit status is unchanged.
exit 0
