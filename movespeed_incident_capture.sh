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
# V37.9.29 Fix: only grep MOVESPEED — older `-e Volumes` matched /System/Volumes/Data
# etc. and pushed the MOVESPEED line past the 400 char limit, causing analyzer to
# systematically misreport mount=other_or_unmounted (8 days silent misdiagnosis).
mount 2>/dev/null | grep -i MOVESPEED > "$_TMP/mount" 2>/dev/null

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

# Filesystem ownership state — V37.9.29 (b)
# Record real UID:GID at incident time. macOS noowners flag would otherwise
# show all files as the calling user, masking the actual ownership UID.
# 60 days of silent failure (V37.9.4 → V37.9.29) was caused exactly by this
# masking: stat seemed normal but kernel ACL checks saw root:wheel + UID 99.
# Capturing real UID:GID lets future incidents reveal ownership misalignment
# in 1 day instead of 60.
stat -f "%u:%g" /Volumes/MOVESPEED > "$_TMP/ownership_top" 2>/dev/null
stat -f "%u:%g" /Volumes/MOVESPEED/KB > "$_TMP/ownership_kb" 2>/dev/null

# V37.9.30: ACL + xattr + open-handle + TM-snapshot forensics
# Why: V37.9.29 path D' chown verified working (19/21 records show 501:20
# bisdom:staff) but EPERM 100% persists (24h still 21 incidents). The
# ownership-misalignment hypothesis is partially falsified — UID was a real
# bug but not the EPERM root cause. New hypotheses to differentiate:
#   (a) ACL deny rules surviving chown (chown changes owner but ACLs persist)
#   (b) macOS daemons holding I/O handles at incident moment
#   (c) Time Machine local snapshots locking metadata (TM exclude doesn't
#       prevent local snapshots from being created)
# Each of these would leave a fingerprint that ownership-only forensics miss.
# Best-effort: missing tool / hang / non-macOS = empty field, never blocks.
#
# V37.9.81 B: capture stderr to separate files instead of swallowing with
# 2>/dev/null. V37.9.30 had a critical observability blind spot — when ls -le@
# / lsof / tmutil themselves are denied by macOS TCC Sandbox (the exact root
# cause V37.9.80 ended after 60 days), their stderr would say "Operation not
# permitted" but the redirect 2>/dev/null swallowed it. The empty stdout file
# then read by Python returned "" → analyzer classified it as "empty/normal".
# Six weeks of "采集器自身被沙箱拒绝" were misread as "no anomaly". V37.9.81 B
# captures each tool's stderr to "${field}_err" file so Python can detect the
# difference between "tool succeeded but produced empty output" (truly normal)
# vs "tool was sandbox-denied" (采集失败 — different bucket).
ls -le@ /Volumes/MOVESPEED/ > "$_TMP/acl_top" 2> "$_TMP/acl_top_err"
ls -le@ /Volumes/MOVESPEED/KB/ > "$_TMP/acl_kb" 2> "$_TMP/acl_kb_err"
# lsof can hang on macOS under contention; head -50 caps both runtime and bytes.
# V37.9.81 B: capture lsof's stderr (e.g. "lsof: WARNING" or sandbox-deny) to file
( lsof /Volumes/MOVESPEED 2> "$_TMP/lsof_err" | head -50 ) > "$_TMP/lsof" 2>/dev/null
# Local snapshots are per-volume on macOS (root volume listing covers all APFS).
tmutil listlocalsnapshots / 2> "$_TMP/snapshots_err" | head -20 > "$_TMP/snapshots" 2>/dev/null

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


def read_file_with_stderr(name, stderr_name, limit=2000):
    """V37.9.81 B: read stdout + detect stderr to distinguish 采集失败 vs 真空.

    Returns stdout content with a prefix marker when stderr indicates failure:
      - sandbox denied (V37.9.80 TCC main case)    → "[sandbox_denied] " prefix
      - tool unavailable (Linux / missing binary)  → "[tool_unavailable] " prefix
      - empty/unrecognized stderr                  → no prefix (legacy behavior)

    Backward compat: if stderr_name doesn't exist (very old paths or first run
    after upgrade), behaves identically to read_file. Analyzer's classify_*
    functions detect the prefix marker; pre-V37.9.81 records without marker
    continue to fall through to the legacy empty/normal classification bucket.
    """
    stdout_content = read_file(name, limit)
    stderr_path = os.path.join(td, stderr_name)
    try:
        if not os.path.exists(stderr_path):
            return stdout_content
        with open(stderr_path, "r", encoding="utf-8", errors="replace") as fp:
            err_lower = fp.read(300).lower()
    except (IOError, OSError):
        return stdout_content
    if not err_lower.strip():
        return stdout_content
    # Sandbox-deny patterns take priority (V37.9.80 TCC root cause).
    if ("operation not permitted" in err_lower
            or "permission denied" in err_lower
            or "sandbox" in err_lower):
        return "[sandbox_denied] " + stdout_content
    # Tool unavailability (Linux / dev environment / missing binary).
    if ("command not found" in err_lower
            or "no such file or directory" in err_lower
            or "not found" in err_lower):
        return "[tool_unavailable] " + stdout_content
    return stdout_content


rec = {
    "timestamp_iso": ts,
    "caller": caller,
    "exit_code": ec,
    "mount": read_file("mount", 800),  # V37.9.29: 400→800 defense-in-depth (grep already narrowed to MOVESPEED-only)
    "disk_info": read_file("diskutil", 2000),
    "ls_top": read_file("ls_top", 800),
    "ls_kb": read_file("ls_kb", 1200),
    "df": read_file("df", 300),
    "probe_top": read_file("probe_top_rc") + "|" + read_file("probe_top_err", 300),
    "probe_kb": read_file("probe_kb_rc") + "|" + read_file("probe_kb_err", 300),
    "procs": read_file("procs", 1500),
    "os": read_file("sw_vers", 200),
    "ownership_top": read_file("ownership_top", 50),  # V37.9.29 (b): real UID:GID at top level
    "ownership_kb": read_file("ownership_kb", 50),    # V37.9.29 (b): real UID:GID at /KB
    # V37.9.81 B: stderr-aware reads to distinguish sandbox-denied (V37.9.80
    # TCC blind spot) from "tool ran fine and output was empty/normal".
    "acl_top": read_file_with_stderr("acl_top", "acl_top_err", 1500),  # V37.9.30 + V37.9.81 B
    "acl_kb": read_file_with_stderr("acl_kb", "acl_kb_err", 2500),    # V37.9.30 + V37.9.81 B
    "lsof": read_file_with_stderr("lsof", "lsof_err", 2000),          # V37.9.30 + V37.9.81 B
    "snapshots": read_file_with_stderr("snapshots", "snapshots_err", 800),  # V37.9.30 + V37.9.81 B
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
