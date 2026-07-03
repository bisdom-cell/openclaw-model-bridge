#!/usr/bin/env python3
"""
daily_observer.py — V37.9.84 Daily Self-Critique Observer

每日 LLM-as-judge 扫前日推送输出，评分 + 发现问题 + 输出 read-only 改进提案。
永不直接修改任何生产文件/代码/配置。

设计契约 (V37.9.83 三大第一性原理 方向 1 兑现):
  - READ-ONLY for production code/config: 不修改任何 job 脚本/部署/配置
  - FAIL-OPEN: observer 失败不影响任何生产 cron
  - LLM-as-judge: 用第三方视角评估推送质量，不是自评
  - 观察结果发布到 (V37.9.92 闭环):
    * ~/.kb/self_critique/daily_critique_YYYYMMDD.md  (V37.9.84)
    * ~/.kb/self_critique/score_history.jsonl          (V37.9.84)
    * ~/.kb/status.json `quality.observer`             (V37.9.92 — 三方共享锚点)

数据源:
  - jobs/*/cache/last_run.json — 各 job 执行状态
  - ~/.kb/sources/*.md — H2 日期章节 (昨日内容)
  - ~/.kb/daily/evening_YYYYMMDD.md — 晚间整理
  - ~/.kb/dreams/YYYY-MM-DD.md — Dream 梦境
  - ~/.kb/deep_dives/YYYY-MM-DD.md — 每日深度分析

CLI:
  python3 daily_observer.py                    # 默认扫昨日
  python3 daily_observer.py --date 20260525    # 指定日期
  python3 daily_observer.py --json             # JSON 输出
  python3 daily_observer.py --dry-run          # 只扫不调 LLM

Exit codes:
  0 — 正常完成 (status: ok / no_outputs / llm_failed)
  1 — 致命错误
"""
import glob
import json
import os
import re
import sys
from datetime import datetime, timedelta


def log(msg):
    print(f"[observer] {msg}", file=sys.stderr)


# ══════════════════════════════════════════════════════════════════════
# 1. Job Status Scanner — 读 last_run.json
# ══════════════════════════════════════════════════════════════════════

# Observer scans these data-source jobs for status. Infrastructure jobs
# (kb_embed, mm_index, governance_audit, etc.) are NOT scanned here —
# they're tracked elsewhere (job_watchdog, governance, kb_status_refresh).
# This is the *maximum possible set*. V37.9.88 filters down to enabled-only
# at scan time using jobs_registry.yaml as single source of truth (MR-8).
JOBS_SUBDIRS = [
    "hf_papers", "arxiv_monitor", "semantic_scholar", "dblp",
    "acl_anthology", "pwc", "github_trending", "rss_blogs",
    "ai_leaders_x", "karpathy_x", "ontology_sources",
    "freight_watcher", "finance_news", "openclaw_official",
    "chaspark",
]

# V37.9.88: stale last_run.json detection — if a job's last_run timestamp
# is older than this many days before target_date, observer flags it as
# MED "stale_job" anomaly and SUPPRESSES the HIGH job_failure anomaly
# (the status data itself is untrustworthy when stale). Without this,
# disabled/dormant jobs with old last_run.json poison anomaly counts
# (e.g. pwc disabled V31, last_run 2026-03-31, observer flagged it as
# today's HIGH fetch_failed for 2 months — discovered 2026-05-29).
STALE_LAST_RUN_MAX_DAYS = 7

# V37.9.56-hotfix same pattern: Mac Mini deploys to ~/.openclaw/jobs/
# HN special case: subdirectory is hn_watcher not run_hn_fixed
_MAC_MINI_JOBS_DIR = os.path.expanduser("~/.openclaw/jobs")

# V37.9.88: env var override for tests to inject mock registry path
_REGISTRY_ENV_VAR = "OBSERVER_REGISTRY_PATH"


def _resolve_last_run_path(jobs_dir, job_id):
    """Find last_run.json across dev + Mac Mini candidate paths.

    Returns: path if found, else None.
    """
    candidates = [
        os.path.join(jobs_dir, job_id, "cache", "last_run.json"),
        os.path.join(_MAC_MINI_JOBS_DIR, job_id, "cache", "last_run.json"),
    ]
    for c in candidates:
        if os.path.isfile(c):
            return c
    return None


# ══════════════════════════════════════════════════════════════════════
# 1b. V37.9.88 Registry-driven enabled job filter (MR-8 SoT)
# ══════════════════════════════════════════════════════════════════════
#
# Background (discovered 2026-05-29 during V37.9.84 trend review):
#   Observer's hardcoded JOBS_SUBDIRS drifted from jobs_registry.yaml in
#   3 ways:
#     - 2 disabled jobs (pwc V31-disabled / karpathy_x disabled) still
#       scanned → stale last_run.json reported as today's HIGH anomaly
#     - 25 enabled jobs NOT in hardcoded list — by design (infra jobs)
#     - 1 ID inconsistency (openclaw_official)
#   The pwc bug was active for 2 months before V37.9.84 observer surfaced
#   it as a recurring fetch_failed HIGH anomaly. False positives erode
#   operator trust in the observer.
#
# Fix (V37.9.88):
#   _filter_enabled_jobs() reads jobs_registry.yaml, returns subset of
#   JOBS_SUBDIRS where enabled=true. FAIL-OPEN: if registry can't be
#   loaded (parser error / missing file), returns subdirs unchanged so
#   observer still runs but logs WARN.
#
# Single source of truth (MR-8): jobs_registry.yaml is the only place
# that decides "is this job enabled". JOBS_SUBDIRS hardcoded list is
# now just the "data-source job category whitelist" (so infra jobs like
# kb_embed are still excluded).


def _resolve_registry_path():
    """Determine registry path. Test override via OBSERVER_REGISTRY_PATH
    env var, then $HOME/jobs_registry.yaml, then Mac Mini canonical
    ($HOME/openclaw-model-bridge/jobs_registry.yaml — V37.9.92 added),
    then script-adjacent (dev fallback).

    V37.9.92: 4th candidate (~/openclaw-model-bridge/jobs_registry.yaml)
    added because auto_deploy FILE_MAP on Mac Mini doesn't copy
    jobs_registry.yaml to $HOME — files stay in the git checkout dir.
    Without this, V37.9.88 LAYER 1 (registry filter) silently fell back
    to JOBS_SUBDIRS hardcoded list for 5 days (5/29-6/1 observed in
    `~/daily_observer.log` repeated WARN). Same blood lesson as
    V37.9.56-hotfix / V37.9.76-hotfix / V37.9.78-hotfix (MR-15
    deployment-layout-must-be-tested-on-target, 4th occurrence).
    """
    env_override = os.environ.get(_REGISTRY_ENV_VAR)
    if env_override:
        return env_override
    candidates = [
        os.path.expanduser("~/jobs_registry.yaml"),
        os.path.expanduser("~/openclaw-model-bridge/jobs_registry.yaml"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "jobs_registry.yaml"),
    ]
    for c in candidates:
        if os.path.isfile(c):
            return c
    return None


def _load_enabled_job_ids_from_registry(registry_path=None):
    """Parse jobs_registry.yaml minimally (no PyYAML dependency).

    Returns:
      set[str] of job IDs where enabled=true, OR None on parse failure
      (caller treats None as "use fallback / no filter").

    Mirrors kb_review_collect.load_sources_from_registry parser style.
    """
    if registry_path is None:
        registry_path = _resolve_registry_path()
    if registry_path is None or not os.path.isfile(registry_path):
        return None
    try:
        with open(registry_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return None

    enabled_ids = set()
    current_id = None
    current_enabled = None
    for raw_line in lines:
        line = raw_line.rstrip("\n")
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("- id:"):
            # Finalize previous record
            if current_id is not None and current_enabled is True:
                enabled_ids.add(current_id)
            current_id = stripped.split(":", 1)[1].strip()
            current_enabled = None
            continue
        if current_id is None:
            continue
        if ":" in stripped:
            key, val = stripped.split(":", 1)
            key = key.strip()
            val = val.strip()
            if "#" in val:
                val = val[: val.index("#")].strip()
            val = val.strip('"').strip("'")
            if key == "enabled":
                current_enabled = (val.lower() == "true")
    # Finalize last record
    if current_id is not None and current_enabled is True:
        enabled_ids.add(current_id)
    return enabled_ids


def _filter_enabled_jobs(subdirs, registry_path=None):
    """Filter JOBS_SUBDIRS to only those enabled in registry.

    FAIL-OPEN: registry unreadable → return subdirs unchanged + log WARN.
    """
    enabled = _load_enabled_job_ids_from_registry(registry_path)
    if enabled is None:
        log("WARN: V37.9.88 registry filter fallback — jobs_registry.yaml "
            "not loadable, using full JOBS_SUBDIRS (may include disabled)")
        return list(subdirs)
    filtered = [j for j in subdirs if j in enabled]
    excluded = [j for j in subdirs if j not in enabled]
    if excluded:
        log(f"V37.9.88 registry filter: excluded {len(excluded)} disabled "
            f"job(s) from scan: {','.join(excluded)}")
    return filtered


# ══════════════════════════════════════════════════════════════════════
# 1c. V37.9.88 Stale last_run.json detection
# ══════════════════════════════════════════════════════════════════════


def _parse_lr_time(time_str):
    """Parse last_run.json 'time' field. Handles multiple formats:
       - '2026-05-28 11:00:00' (s2/pwc style)
       - '2026-05-28T11:00:00Z' (ISO UTC)
       - '2026-05-28T11:00:00' (ISO local)

    Returns: naive datetime, or None on parse failure.
    """
    if not time_str or not isinstance(time_str, str):
        return None
    s = time_str.strip()
    # Strip trailing Z (treat as UTC = naive for comparison purposes)
    if s.endswith("Z"):
        s = s[:-1]
    # Try ISO formats
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%d", "%Y/%m/%d %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _is_stale_last_run(time_str, target_date,
                      max_age_days=STALE_LAST_RUN_MAX_DAYS):
    """True if last_run time is older than max_age_days before target_date.

    Uses calendar-date subtraction (ignores time-of-day) so "March 31 →
    May 28" reads as 58 days the way a human counts calendar days.

    Returns: (bool stale, int days_old or None on parse fail).
    target_date is the date observer is auditing (typically yesterday).
    """
    lr_time = _parse_lr_time(time_str)
    if lr_time is None:
        return False, None
    target_d = (target_date.date() if isinstance(target_date, datetime)
                else target_date)
    lr_d = lr_time.date()
    days_old = (target_d - lr_d).days
    return (days_old > max_age_days), days_old


def scan_job_statuses(jobs_dir, target_date, registry_path=None):
    """Scan last_run.json from all known job subdirectories.

    V37.9.88: filters JOBS_SUBDIRS by registry-declared enabled=true
    (MR-8 SoT) and computes stale flag (last_run > 7d before target_date).
    Stale entries get their status data flagged as untrustworthy in
    detect_anomalies (suppresses HIGH job_failure, emits MED stale_job).

    Returns:
        list of dict: [{job_id, status, time, new, reason, found,
                        stale, stale_days}, ...]
    """
    results = []
    subdirs = _filter_enabled_jobs(JOBS_SUBDIRS, registry_path=registry_path)
    for job_id in subdirs:
        lr_path = _resolve_last_run_path(jobs_dir, job_id)
        entry = {"job_id": job_id, "found": False, "status": "unknown",
                 "time": "", "new": 0, "reason": "",
                 "stale": False, "stale_days": None}
        if lr_path is None:
            results.append(entry)
            continue
        try:
            with open(lr_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            entry["found"] = True
            entry["status"] = data.get("status", "unknown")
            entry["time"] = data.get("time", "")
            entry["new"] = data.get("new", data.get("sent_count", 0))
            entry["reason"] = data.get("reason", "")
            if isinstance(entry["new"], bool):
                entry["new"] = 1 if entry["new"] else 0
            # V37.9.88 stale detection
            stale, days_old = _is_stale_last_run(entry["time"], target_date)
            entry["stale"] = stale
            entry["stale_days"] = days_old
        except (OSError, json.JSONDecodeError):
            entry["found"] = True
            entry["status"] = "parse_error"
        results.append(entry)
    return results


# ══════════════════════════════════════════════════════════════════════
# 2. Push Output Scanner — 读 evening/dream/deep_dive/sources
# ══════════════════════════════════════════════════════════════════════

MAX_SAMPLE_CHARS = 2000

# V37.9.93: Smart sampling — head + middle marker + tail.
# Background: pre-V37.9.93 only took first MAX_SAMPLE_CHARS, so LLM never
# saw the file footer. For long Dream files (e.g. 5/31 = 6782 chars), the
# 2000-char sample cut mid-sentence at "AgentScope 1" → LLM read this as
# file truncation and falsely reported [MED] dream output truncated. Real
# file had complete `*Generated by kb_dream.sh v2 ...*` footer 4800 chars
# down. Discovered 2026-06-01 during V37.9.92 Observer trend review.
#
# Fix: budget split as head + marker + tail within max_chars total.
# Head shows opening structure, tail shows footer/closing — LLM can verify
# completeness by checking the footer rather than seeing truncated middle.
SMART_SAMPLE_HEAD_CHARS = 1400
SMART_SAMPLE_TAIL_CHARS = 500
# V37.9.213: snap the head cut back to the last line boundary within this
# many chars of the budget, so the head does NOT end mid-sentence. A
# mid-sentence head cut fooled the Observer LLM into a false-positive
# "truncation" report (2026-07-01 recurrence of the V37.9.93 false-positive
# class — V37.9.93 fixed the TAIL/footer visibility but the HEAD cut still
# looked like truncation despite the marker). Markdown files have frequent
# newlines so this almost always lands cleanly; a very long line with no
# nearby newline keeps the raw head (budget preserved).
SMART_SAMPLE_HEAD_SNAP_MAX = 300
SMART_SAMPLE_MARKER_TEMPLATE = (
    "\n\n[...{omitted} chars omitted from middle — this is sampling for "
    "LLM evaluation, NOT file truncation. File is complete; head + tail "
    "shown below.]\n\n"
)


def _read_file_sample(path, max_chars=MAX_SAMPLE_CHARS):
    """Read file with smart head+tail sampling.

    V37.9.93: For files ≤ max_chars, return full content (no sampling).
    For larger files, return head (SMART_SAMPLE_HEAD_CHARS) + explicit
    marker + tail (SMART_SAMPLE_TAIL_CHARS), staying within max_chars
    total budget. This lets the Observer LLM see both the opening and
    the closing of the file — including footer markers like Dream's
    `*Generated by kb_dream.sh v2 ...*` — so it can verify file
    completeness without seeing the middle.

    Returns: (sample_content, full_length).
    """
    if not os.path.isfile(path):
        return "", 0
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        full_length = len(content)
        if full_length <= max_chars:
            return content, full_length
        # V37.9.93: smart head + marker + tail
        head_chars = SMART_SAMPLE_HEAD_CHARS
        tail_chars = SMART_SAMPLE_TAIL_CHARS
        # marker omitted count uses the NOMINAL head budget (stable, so the
        # count doesn't wobble with snapping/budget-fit below).
        omitted = full_length - head_chars - tail_chars
        marker = SMART_SAMPLE_MARKER_TEMPLATE.format(omitted=omitted)
        # V37.9.213: reserve budget for marker + tail FIRST, then snap the
        # head to the last line boundary within SMART_SAMPLE_HEAD_SNAP_MAX so
        # it never ends mid-sentence. Reserving up front means the snap is
        # never undone by a later defensive trim (the 2026-07-01 head-cut
        # false-positive was a V37.9.93 recurrence at the HEAD boundary). No
        # nearby newline (very long line) → keep the raw head.
        head_budget = max(0, max_chars - len(marker) - tail_chars)
        raw_head = content[:head_budget]
        nl = raw_head.rfind("\n")
        head = (raw_head[:nl]
                if nl >= head_budget - SMART_SAMPLE_HEAD_SNAP_MAX
                else raw_head)
        sample = head + marker + content[-tail_chars:]
        # Defensive: extreme small-max_chars edge only (head_budget already
        # fits marker+tail in the normal path); never re-introduces a
        # mid-sentence cut in practice since head_budget reserved the space.
        if len(sample) > max_chars:
            overflow = len(sample) - max_chars
            head = head[:max(0, len(head) - overflow)]
            sample = head + marker + content[-tail_chars:]
        return sample, full_length
    except OSError:
        return "", 0


def scan_push_outputs(kb_dir, target_date):
    """Scan yesterday's push outputs (evening, dream, deep_dive).

    Args:
        kb_dir: path to ~/.kb
        target_date: datetime object for the target day

    Returns:
        dict: {evening: {content, length, found}, dream: {...}, deep_dive: {...}}
    """
    date_ymd = target_date.strftime("%Y%m%d")
    date_dash = target_date.strftime("%Y-%m-%d")

    outputs = {}

    evening_path = os.path.join(kb_dir, "daily", f"evening_{date_ymd}.md")
    content, length = _read_file_sample(evening_path)
    outputs["evening"] = {"content": content, "length": length,
                          "found": length > 0, "path": evening_path}

    dream_path = os.path.join(kb_dir, "dreams", f"{date_dash}.md")
    content, length = _read_file_sample(dream_path)
    outputs["dream"] = {"content": content, "length": length,
                        "found": length > 0, "path": dream_path}

    dd_path = os.path.join(kb_dir, "deep_dives", f"{date_dash}.md")
    content, length = _read_file_sample(dd_path)
    outputs["deep_dive"] = {"content": content, "length": length,
                            "found": length > 0, "path": dd_path}

    return outputs


def scan_source_sections(kb_dir, target_date, max_sources=5, max_chars_per=500):
    """Scan yesterday's H2 date sections from source files.

    Returns:
        list of dict: [{source, section_text, char_count}, ...]
        Sorted by content length descending, top max_sources.
    """
    sources_dir = os.path.join(kb_dir, "sources")
    if not os.path.isdir(sources_dir):
        return []

    date_str = target_date.strftime("%Y-%m-%d")
    h2_pattern = re.compile(r"^##\s+" + re.escape(date_str))

    results = []
    for md_path in glob.glob(os.path.join(sources_dir, "*.md")):
        source_name = os.path.splitext(os.path.basename(md_path))[0]
        try:
            with open(md_path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except OSError:
            continue

        in_section = False
        section_lines = []
        for line in lines:
            if h2_pattern.match(line):
                in_section = True
                section_lines = [line]
                continue
            if in_section:
                if line.startswith("## ") and not h2_pattern.match(line):
                    break
                section_lines.append(line)

        if section_lines:
            # V37.9.235 (observer 2026-07-03 finding #3): 原硬切片 [:max_chars_per]
            # 把行切在半中间 — 实测把 alignment 校验标记切成 "⚠️ LLM 评 ⭐4 偏高 (ru"
            # → LLM assessor 把采样痕迹误报为内容截断 [LOW]。镜像 V37.9.213 head-snap:
            # 就近换行收尾 + 显式采样标注 (V37.9.93 sampling-aware 家族第 3 个位点:
            # _read_file_sample head → tail → source sections)。
            raw = "".join(section_lines)
            text = raw[:max_chars_per]
            if len(raw) > max_chars_per:
                nl = text.rfind("\n", max(0, max_chars_per - SMART_SAMPLE_HEAD_SNAP_MAX))
                if nl > 0:
                    text = text[:nl]
                text += f"\n…(样本截断于行边界, 完整 section 共 {len(raw)} 字)"
            results.append({
                "source": source_name,
                "section_text": text,
                "char_count": len(raw),
            })

    results.sort(key=lambda x: x["char_count"], reverse=True)
    return results[:max_sources]


# ══════════════════════════════════════════════════════════════════════
# 2b. V37.9.168 — Deep-dive degrade ratio observability ([19](c) closure)
# ══════════════════════════════════════════════════════════════════════
#
# Background (V37.9.132, discovered 2026-06-11 by user):
#   kb_deep_dive falls back to abstract_only mode when full-text PDF/HTML
#   fetch fails. 76% (34/45) of deep_dives were summary-level — a
#   structural gap that stayed latent for 2 MONTHS because the degrade
#   frequency had no aggregated review (the user had to read 45 files to
#   notice). V37.9.132 fixed the link-construction root cause; this
#   section makes the degrade ratio OBSERVABLE so any future regression
#   surfaces in the daily report instead of needing a user to read files.
#   status.json unfinished [19](c), sanctioned as a legitimate
#   observability addition in the V37.9.166 changelog (MR-4 闭真盲区).
#
# Detection: kb_deep_dive.build_deep_dive_markdown writes a deterministic
#   line `**模式**: 完整原文` (full_text) or `**模式**: 摘要级`
#   (abstract_only) exactly once per file. We classify on that structured
#   marker (code-written, reliable) rather than the LLM-generated
#   "基于摘要的分析" string (which depends on LLM compliance).

DEEP_DIVE_MODE_WINDOW_DAYS = 30
DEEP_DIVE_DEGRADE_RATIO_THRESHOLD = 0.5
DEEP_DIVE_DEGRADE_MIN_SAMPLE = 5
_DD_MODE_RE = re.compile(r"\*\*模式\*\*\s*[:：]\s*(完整原文|摘要级)")


def _classify_deep_dive_mode(content):
    """Classify a deep_dive file's mode from its 模式 marker.

    Returns: "full_text" | "abstract_only" | "unknown".
    "unknown" when the structured marker is absent (older/format-drifted
    file) — we never guess, so unknowns are counted separately and
    excluded from the degrade ratio denominator.
    """
    m = _DD_MODE_RE.search(content or "")
    if not m:
        return "unknown"
    return "full_text" if m.group(1) == "完整原文" else "abstract_only"


# V37.9.213: degrade-REASON aggregation. V37.9.168 surfaced the degrade
# RATE (77%) but not WHY, so root-cause diagnosis still required a human to
# grep ~/.kb/deep_dives/*.md on Mac Mini (exactly the MR-4 闭真盲区 the
# observer is meant to close). kb_deep_dive.build_deep_dive_markdown already
# writes a machine-parseable line `> ⚠️ 抓取降级原因：<reason>` into every
# abstract_only file, where <reason> is one of:
#   "PDF fetch failed: <inner>"  (tier1 PDF path failed — resolution bug/404)
#   "HTML fetch failed: <inner>" (tier2 HTML path failed)
#   "tier{N} source (no fetch attempted)" (tier3/no-url — structurally
#      unfetchable: X tweet weekend fallback, paywall with no URL)
# We bucket these so the report answers "of the 77%, how much is structurally
# unfetchable (accept) vs fetch-path failure (fixable)" self-serve.
_DD_DEGRADE_RE = re.compile(r"抓取降级原因\s*[:：]\s*(.+)")
# 显式 4 桶（顺序即渲染优先级的稳定基准；实际渲染按计数降序）
_DD_DEGRADE_CATEGORIES = (
    "PDF 抓取失败", "HTML 抓取失败", "非全文来源", "未标注原因")


def _extract_degrade_category(content):
    """V37.9.213: bucket an abstract_only deep_dive's degrade reason.

    Returns one of _DD_DEGRADE_CATEGORIES. "未标注原因" when no reason line
    is present (older files before V37.9.132 or format drift) — we never
    guess the cause.
    """
    m = _DD_DEGRADE_RE.search(content or "")
    if not m:
        return "未标注原因"
    reason = m.group(1).strip()
    if reason.startswith("PDF fetch failed"):
        return "PDF 抓取失败"
    if reason.startswith("HTML fetch failed"):
        return "HTML 抓取失败"
    if reason.startswith("tier"):
        return "非全文来源"
    return "未标注原因"


def scan_deep_dive_modes(kb_dir, target_date,
                         window_days=DEEP_DIVE_MODE_WINDOW_DAYS):
    """Scan ~/.kb/deep_dives/*.md within a rolling window and classify
    full_text vs abstract_only to compute the degrade ratio.

    Window is [target_date - window_days, target_date] anchored on the
    audited date (so old pre-fix files age out and don't permanently skew
    the ratio). degrade_ratio is over CLASSIFIED files (full + abstract);
    unknowns are reported separately, never guessed.

    Returns dict:
        {found, total, full_text, abstract_only, unknown, degrade_ratio,
         window_days, recent: [{date, mode}, ...]}
        found=False when dir missing or no files within window.
    """
    stats = {"found": False, "total": 0, "full_text": 0,
             "abstract_only": 0, "unknown": 0, "degrade_ratio": None,
             "window_days": window_days, "recent": [], "degrade_reasons": {}}
    dd_dir = os.path.join(kb_dir, "deep_dives")
    if not os.path.isdir(dd_dir):
        return stats

    anchor = (target_date.date() if isinstance(target_date, datetime)
              else target_date)
    oldest = anchor - timedelta(days=window_days)

    entries = []
    for md_path in glob.glob(os.path.join(dd_dir, "*.md")):
        base = os.path.splitext(os.path.basename(md_path))[0]
        try:
            file_date = datetime.strptime(base, "%Y-%m-%d").date()
        except ValueError:
            continue  # non-date filename, skip
        if file_date < oldest or file_date > anchor:
            continue
        try:
            with open(md_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except OSError:
            continue
        mode = _classify_deep_dive_mode(content)
        entry = {"date": base, "mode": mode}
        if mode == "abstract_only":
            # V37.9.213: capture WHY it degraded, not just THAT it degraded.
            entry["degrade_category"] = _extract_degrade_category(content)
        entries.append(entry)

    if not entries:
        return stats

    entries.sort(key=lambda e: e["date"], reverse=True)
    full = sum(1 for e in entries if e["mode"] == "full_text")
    abst = sum(1 for e in entries if e["mode"] == "abstract_only")
    unk = sum(1 for e in entries if e["mode"] == "unknown")
    classified = full + abst
    # V37.9.213: tally degrade reasons over abstract_only files so the report
    # can distinguish structural-unfetchable (非全文来源) from fetch-path
    # failure (PDF/HTML 抓取失败). Keys are _DD_DEGRADE_CATEGORIES; absent
    # buckets are simply omitted (no zero rows).
    degrade_reasons = {}
    for e in entries:
        if e["mode"] == "abstract_only":
            cat = e.get("degrade_category", "未标注原因")
            degrade_reasons[cat] = degrade_reasons.get(cat, 0) + 1
    stats.update({
        "found": True,
        "total": len(entries),
        "full_text": full,
        "abstract_only": abst,
        "unknown": unk,
        "degrade_ratio": (abst / classified) if classified else None,
        "degrade_reasons": degrade_reasons,
        "recent": entries[:7],
    })
    return stats


def build_deep_dive_mode_section(deep_dive_modes):
    """V37.9.168: markdown section showing deep_dive full_text vs
    abstract_only ratio over the window. Returns "" when no data.
    """
    if not deep_dive_modes or not deep_dive_modes.get("found"):
        return ""
    total = deep_dive_modes["total"]
    full = deep_dive_modes["full_text"]
    abst = deep_dive_modes["abstract_only"]
    unk = deep_dive_modes["unknown"]
    ratio = deep_dive_modes.get("degrade_ratio")
    win = deep_dive_modes["window_days"]
    pct = f"{round(ratio * 100)}%" if ratio is not None else "N/A"

    lines = [f"## 🔬 Deep-Dive Mode (last {win}d)\n"]
    breakdown = f"摘要级 {abst} / 全文 {full}"
    if unk:
        breakdown += f" / 未分类 {unk}"
    breakdown += f" / 共 {total}"
    lines.append(f"- 摘要级降级率: {pct} ({breakdown})")
    # V37.9.213: WHY breakdown — turns "77% but why?" self-serve (no Mac Mini
    # grep needed). Sorted by count desc; 非全文来源 = structural (accept),
    # PDF/HTML 抓取失败 = fetch-path failure (the fixable target).
    dr = deep_dive_modes.get("degrade_reasons")
    if dr:
        parts = [f"{k} {v}" for k, v in
                 sorted(dr.items(), key=lambda x: (-x[1], x[0]))]
        lines.append(f"- 降级原因: {' / '.join(parts)}")
    if deep_dive_modes.get("recent"):
        icons = {"full_text": "📄", "abstract_only": "📃", "unknown": "❔"}
        recent_str = " ".join(
            f"{icons.get(e['mode'], '❔')}{e['date'][5:]}"
            for e in deep_dive_modes["recent"])
        lines.append(f"- 最近: {recent_str}")
    lines.append("")
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════
# 3. Rule-Based Anomaly Detection (no LLM needed)
# ══════════════════════════════════════════════════════════════════════

def detect_anomalies(job_statuses, push_outputs, source_sections,
                     deep_dive_modes=None):
    """Detect obvious anomalies using rules (no LLM).

    Args:
        deep_dive_modes: V37.9.168 optional dict from scan_deep_dive_modes.
            Backward compatible — None means no deep_dive degrade check
            (existing 3-arg callers/tests unaffected).

    Returns:
        list of dict: [{severity, category, message}, ...]
        severity: HIGH / MED / LOW
    """
    anomalies = []

    # V37.9.88: emit MED stale_job for any job whose last_run is >7d old.
    # SUPPRESS the HIGH job_failure for stale entries (the status data
    # itself is untrustworthy when stale — e.g. pwc disabled V31-V37.8.13
    # had last_run.json from 2026-03-31 reported as today's fetch_failed
    # for 2 months until V37.9.88 fix).
    stale_jobs = [j for j in job_statuses if j.get("stale")]
    stale_job_ids = {j["job_id"] for j in stale_jobs}
    for j in stale_jobs:
        days = j.get("stale_days")
        days_str = f"{days}d" if days is not None else "unknown age"
        anomalies.append({
            "severity": "MED",
            "category": "stale_job",
            "message": (f"{j['job_id']}: last_run is {days_str} old "
                        f"(stale, status={j['status']} untrustworthy)"),
        })

    failed_jobs = [j for j in job_statuses
                   if j["status"] in ("llm_failed", "fetch_failed", "send_failed")
                   and j["job_id"] not in stale_job_ids]
    for j in failed_jobs:
        reason = f" ({j['reason']})" if j["reason"] else ""
        anomalies.append({
            "severity": "HIGH",
            "category": "job_failure",
            "message": f"{j['job_id']}: status={j['status']}{reason}",
        })

    degraded_jobs = [j for j in job_statuses
                     if j["status"] == "partial_degraded"]
    for j in degraded_jobs:
        anomalies.append({
            "severity": "MED",
            "category": "job_degraded",
            "message": f"{j['job_id']}: partial_degraded",
        })

    missing_lr = [j for j in job_statuses if not j["found"]]
    for j in missing_lr:
        anomalies.append({
            "severity": "LOW",
            "category": "missing_status",
            "message": f"{j['job_id']}: no last_run.json found",
        })

    for name, info in push_outputs.items():
        if not info["found"]:
            anomalies.append({
                "severity": "MED",
                "category": "missing_output",
                "message": f"{name}: output file not found",
            })

    if not source_sections:
        anomalies.append({
            "severity": "HIGH",
            "category": "no_sources",
            "message": "No source sections found for target date",
        })

    for info in push_outputs.values():
        if info["found"] and info["length"] < 200:
            anomalies.append({
                "severity": "MED",
                "category": "thin_output",
                "message": f"Output suspiciously short ({info['length']} chars)",
            })

    # V37.9.168: deep_dive degrade ratio anomaly ([19](c)). Most deep_dives
    # SHOULD be full_text (V37.9.16 design assumption); a high abstract_only
    # ratio over the window means the full-text fetch path regressed (the
    # 76% gap latent 2 months root cause). Require a minimum sample so a
    # couple of genuinely-paywalled papers don't trip the alert.
    if deep_dive_modes and deep_dive_modes.get("found"):
        ratio = deep_dive_modes.get("degrade_ratio")
        classified = (deep_dive_modes.get("full_text", 0)
                      + deep_dive_modes.get("abstract_only", 0))
        if (ratio is not None
                and classified >= DEEP_DIVE_DEGRADE_MIN_SAMPLE
                and ratio >= DEEP_DIVE_DEGRADE_RATIO_THRESHOLD):
            pct = round(ratio * 100)
            anomalies.append({
                "severity": "MED",
                "category": "deep_dive_degraded",
                "message": (
                    f"deep_dive 摘要级降级率 {pct}% "
                    f"({deep_dive_modes['abstract_only']}/{classified} 近"
                    f"{deep_dive_modes['window_days']}天) — 全文抓取路径可能退化"),
            })

    return anomalies


# ══════════════════════════════════════════════════════════════════════
# 4. LLM Critique Prompt Builder
# ══════════════════════════════════════════════════════════════════════

CRITIQUE_SYSTEM = """你是一位独立的 AI 系统质量审计员。你的任务是评估一个自动化知识推送系统昨日的输出质量。

你必须以第三方视角客观评估，不美化不贬低。评估维度：
1. **信息密度** (1-5⭐)：内容是否有实质信息价值，还是表面空洞
2. **准确性风险** (1-5⭐)：是否有幻觉/编造/不可验证的断言迹象
3. **主题多样性** (1-5⭐)：是否覆盖多个领域，还是重复单一主题
4. **可行动性** (1-5⭐)：是否给出具体可执行的下一步，而非泛泛而谈
5. **格式规范** (1-5⭐)：结构是否清晰，字段是否完整

输出格式（严格遵守）：

## 评分
- 信息密度: ⭐×N
- 准确性风险: ⭐×N
- 主题多样性: ⭐×N
- 可行动性: ⭐×N
- 格式规范: ⭐×N
- 综合: ⭐×N / 5

## 发现的问题
1. [HIGH/MED/LOW] 问题描述
2. ...

## 改进提案 (read-only, 仅供人类参考)
1. 提案描述
2. ...

⚠️ 严格约束：
- 你的评估基于下方提供的实际输出内容
- 不要编造你没看到的问题
- 如果内容质量确实好，给高分，不要为了"显得有用"而硬找问题
- 提案必须具体可执行，不要泛泛建议

⚠️ 采样说明 (V37.9.93)：
- 为控制 token 成本，超长输出 (如 dream 可能 6000+ 字符) 会以"head + 中间省略 marker + tail"采样形式呈现。
- 当你看到 `[...N chars omitted from middle — this is sampling for LLM evaluation, NOT file truncation. File is complete; head + tail shown below.]` 这种 marker，说明你正在看的是采样不是完整文件。
- **重要**：不要把"sampling 中间省略"误判为"文件被截断"。文件完整性请用以下方法判定：
  (1) 看 head 末尾 + tail 是否有完整 footer/结尾标识 (如 `*Generated by kb_dream.sh v2 ...*` 是完整 Dream footer)
  (2) 看文件实际长度 (`X chars total` 标签) 是否符合预期最低字符数 (DEEP ≥1500 / WIDE+RADAR ≥2500 / Overview ≥300)
- 只在 (a) 没有 footer/末尾出现中断, 或 (b) 实际长度低于预期 两种情况下, 才报告"输出截断"问题."""

MIN_CONTENT_FOR_CRITIQUE = 200


def build_critique_prompt(push_outputs, source_sections, anomalies, target_date):
    """Build the LLM critique prompt with sampled content.

    Returns:
        str: the full user prompt, or "" if no content to critique.
    """
    date_str = target_date.strftime("%Y-%m-%d")

    content_parts = []
    has_content = False

    for name in ("evening", "dream", "deep_dive"):
        info = push_outputs.get(name, {})
        if info.get("found") and info.get("content"):
            content_parts.append(
                f"═══ {name} ({info['length']} chars total) ═══\n"
                f"{info['content']}"
            )
            has_content = True

    if source_sections:
        src_block = "═══ Source Samples (top by length) ═══\n"
        for s in source_sections[:3]:
            src_block += f"--- {s['source']} ({s['char_count']} chars) ---\n"
            src_block += s["section_text"] + "\n"
        content_parts.append(src_block)
        has_content = True

    if not has_content:
        return ""

    anomaly_block = ""
    if anomalies:
        anomaly_block = "\n═══ 规则检测发现的异常 ═══\n"
        for a in anomalies:
            anomaly_block += f"- [{a['severity']}] {a['category']}: {a['message']}\n"

    prompt = f"""请评估以下系统 {date_str} 的推送输出质量。

{chr(10).join(content_parts)}
{anomaly_block}
请按上述评分维度给出评估。"""

    return prompt


# ══════════════════════════════════════════════════════════════════════
# 5. LLM 调用 (复用 kb_review_collect 同款模式)
# ══════════════════════════════════════════════════════════════════════

PROXY_URL = "http://127.0.0.1:5002/v1/chat/completions"
LLM_TIMEOUT = 90
MAX_LLM_TOKENS = 1500


def call_llm_critique(system_prompt, user_prompt, timeout=LLM_TIMEOUT,
                      url=PROXY_URL):
    """Call LLM for critique. Returns (ok, content, reason)."""
    import urllib.error
    import urllib.request

    payload = json.dumps({
        "model": "any",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": MAX_LLM_TOKENS,
        "temperature": 0.3,
    }).encode("utf-8")

    req = urllib.request.Request(
        url, data=payload, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = (e.read() or b"").decode("utf-8", errors="replace")[:300]
        except Exception:
            pass
        return False, "", f"HTTP {e.code}: {e.reason} | {body}"
    except urllib.error.URLError as e:
        return False, "", f"URLError: {e.reason}"
    except (TimeoutError, json.JSONDecodeError) as e:
        return False, "", f"{type(e).__name__}: {e}"
    except Exception as e:
        return False, "", f"{type(e).__name__}: {e}"

    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as e:
        return False, "", f"invalid response: {e}"

    if not content or len(content.strip()) < 50:
        return False, "", f"content too short ({len((content or '').strip())} chars)"

    return True, content.strip(), ""


# ══════════════════════════════════════════════════════════════════════
# 6. Report Builder
# ══════════════════════════════════════════════════════════════════════

def parse_overall_score(llm_content):
    """Extract overall score from LLM critique output.

    Returns: float (0-5) or None if not parseable.
    """
    m = re.search(r"综合[：:]\s*⭐×?(\d(?:\.\d)?)\s*/\s*5", llm_content)
    if m:
        return float(m.group(1))
    stars = re.findall(r"综合[：:]\s*(⭐+)", llm_content)
    if stars:
        return min(len(stars[0]), 5)
    return None


def build_report_markdown(target_date, job_statuses, push_outputs,
                          source_sections, anomalies, llm_critique,
                          overall_score, trend_section="",
                          deep_dive_section="", fp_section=""):
    """Build the final markdown report.

    V37.9.168: deep_dive_section (optional) surfaces the deep_dive
    full_text vs abstract_only degrade ratio. Backward compatible —
    empty string means the section is omitted.
    V37.9.198 (Stage 5): fp_section (optional) surfaces fail-plausible
    verdicts (L1+L2 evidence). Backward compatible — empty = omitted.
    """
    date_str = target_date.strftime("%Y-%m-%d")

    lines = [f"# 🔍 Daily Self-Critique — {date_str}\n"]

    if overall_score is not None:
        lines.append(f"> 综合评分: {'⭐' * int(overall_score)}"
                     f"{'½' if overall_score % 1 >= 0.5 else ''}"
                     f" ({overall_score}/5)\n")

    # Coverage section
    ok_count = sum(1 for j in job_statuses
                   if j["status"] in ("ok", "partial_degraded"))
    total = len(job_statuses)
    lines.append(f"## 📊 Job Coverage ({ok_count}/{total})\n")
    for j in job_statuses:
        if j["status"] == "ok":
            icon = "✅"
        elif j["status"] == "partial_degraded":
            icon = "⚠️"
        elif j["status"] in ("llm_failed", "fetch_failed", "send_failed"):
            icon = "❌"
        elif not j["found"]:
            icon = "❓"
        else:
            icon = "🔘"
        extra = f" ({j['new']} items)" if j["new"] else ""
        lines.append(f"- {icon} {j['job_id']}: {j['status']}{extra}")
    lines.append("")

    # Push outputs section
    lines.append("## 📤 Push Outputs\n")
    for name in ("evening", "dream", "deep_dive"):
        info = push_outputs.get(name, {})
        if info.get("found"):
            lines.append(f"- ✅ {name}: {info['length']} chars")
        else:
            lines.append(f"- ❌ {name}: not found")
    lines.append("")

    # V37.9.168: deep_dive mode degrade ratio (full_text vs abstract_only)
    if deep_dive_section:
        lines.append(deep_dive_section.rstrip("\n"))
        lines.append("")

    # V37.9.198 (Stage 5): fail-plausible detection (L1 确定性 + L2 LLM-judge)
    if fp_section:
        lines.append(fp_section.rstrip("\n"))
        lines.append("")

    # Source sections
    if source_sections:
        lines.append("## 📚 Source Sections (top by content)\n")
        for s in source_sections[:5]:
            lines.append(f"- {s['source']}: {s['char_count']} chars")
        lines.append("")

    # Rule-based anomalies
    if anomalies:
        lines.append("## ⚠️ Rule-Based Anomalies\n")
        for a in anomalies:
            lines.append(f"- [{a['severity']}] {a['category']}: {a['message']}")
        lines.append("")

    # Trend analysis
    if trend_section:
        lines.append(trend_section)

    # LLM critique
    if llm_critique:
        lines.append("## 🤖 LLM Quality Assessment\n")
        lines.append(llm_critique)
        lines.append("")

    lines.append("---")
    lines.append(f"*Generated by daily_observer.py V37.9.84 at "
                 f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*")
    lines.append("*READ-ONLY: This report contains proposals only. "
                 "No production files were modified.*")

    return "\n".join(lines)


def build_discord_summary(target_date, overall_score, anomalies, job_statuses,
                          trend_suffix=""):
    """Build a short Discord push summary."""
    date_str = target_date.strftime("%Y-%m-%d")
    ok_count = sum(1 for j in job_statuses
                   if j["status"] in ("ok", "partial_degraded"))
    total = len(job_statuses)

    score_str = f"{'⭐' * int(overall_score)}" if overall_score else "N/A"
    high_issues = sum(1 for a in anomalies if a["severity"] == "HIGH")
    med_issues = sum(1 for a in anomalies if a["severity"] == "MED")

    lines = [f"🔍 Daily Self-Critique {date_str}"]
    lines.append(f"综合: {score_str} | Jobs: {ok_count}/{total}{trend_suffix}")
    if high_issues or med_issues:
        lines.append(f"Issues: {high_issues} HIGH / {med_issues} MED")
    if not high_issues and not med_issues:
        lines.append("No significant issues found.")

    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════
# 6b. Score History + Trend Analysis
# ══════════════════════════════════════════════════════════════════════

SCORE_HISTORY_FILENAME = "score_history.jsonl"
TREND_WINDOW_DAYS = 7


def _score_history_path(kb_dir):
    return os.path.join(kb_dir, "self_critique", SCORE_HISTORY_FILENAME)


def append_score_history(kb_dir, target_date, overall_score, anomalies,
                         job_statuses, push_outputs, status, fp_verdicts=None):
    """Append one record to score_history.jsonl. FAIL-OPEN.

    V37.9.198 (Stage 5): fp_verdicts (optional) → fp_high/fp_med 计数 (fail-plausible
    趋势观察, JSONL append 旧 reader 不破)。fp_verdicts=None → fp 计数 0 (向后兼容)。
    """
    path = _score_history_path(kb_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)

    ok_jobs = sum(1 for j in job_statuses
                  if j["status"] in ("ok", "partial_degraded"))
    high = sum(1 for a in anomalies if a["severity"] == "HIGH")
    med = sum(1 for a in anomalies if a["severity"] == "MED")
    outputs_found = sum(1 for v in push_outputs.values() if v.get("found"))
    fp = fp_verdicts or []
    fp_high = sum(1 for v in fp if v.get("severity") == "HIGH")
    fp_med = sum(1 for v in fp if v.get("severity") == "MED")

    record = {
        "date": target_date.strftime("%Y-%m-%d"),
        "overall_score": overall_score,
        "jobs_ok": ok_jobs,
        "jobs_total": len(job_statuses),
        "outputs_found": outputs_found,
        "anomalies_high": high,
        "anomalies_med": med,
        "fp_high": fp_high,
        "fp_med": fp_med,
        "status": status,
    }
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        pass


# ══════════════════════════════════════════════════════════════════════
# 6b. V37.9.92 — status.json quality.observer publication (V37.9.84 closure)
# ══════════════════════════════════════════════════════════════════════
#
# Background (V37.9.92 observed 2026-06-01):
#   V37.9.84 design contract promised "三方共享意识锚点" — observer score
#   should be visible to PA / kb_status_refresh / health_check via
#   ~/.kb/status.json `quality.observer` field. But for 5+ days post-
#   launch the field stayed `{}` because daily_observer.py never wrote
#   to status.json. The score lived only in score_history.jsonl and
#   the daily critique markdown — invisible to other 三方 consumers.
#
# Fix (V37.9.92 Tier 2):
#   _write_observer_to_status() publishes daily summary to status.json
#   quality.observer after each successful run. Uses status_update
#   module (atomic tmpfile + os.replace via save_status — MR-9 helper
#   compliant). FAIL-OPEN: import or write failure logs WARN and
#   continues; observer never aborts on publication failure.
#
# Read-only contract clarification:
#   V37.9.84 promised "READ-ONLY for production files". status.json is
#   the 三方共享意识锚点 — explicitly a write target for cron / PA /
#   Claude Code (kb_status_refresh writes health.*, status_update CLI
#   writes priorities/recent_changes/etc). Publishing observer summary
#   here is the same mechanism kb_status_refresh uses, not a violation
#   of the read-only contract.


def _write_observer_to_status(kb_dir, target_date, overall_score, anomalies,
                              status, job_statuses, fp_verdicts=None):
    """V37.9.92: Surface daily observer summary to status.json
    quality.observer (V37.9.84 design closure).

    V37.9.198 (Stage 5): fp_verdicts (optional) → fail_plausible_high/med 字段
    (PA/health_check 可见 fail-plausible 计数)。None → 0 (向后兼容)。

    FAIL-OPEN: any failure (import error, file IO, schema corruption)
    logs WARN and returns; observer run continues. status.json write
    goes through status_update.save_status() (atomic tmpfile +
    os.replace, MR-9 compliant).
    """
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from status_update import load_status, save_status
    except ImportError as e:
        log(f"WARN: V37.9.92 status_update not importable ({e}), "
            f"skipping quality.observer write")
        return False

    try:
        data = load_status()
        anomalies_high = sum(1 for a in anomalies
                             if a.get("severity") == "HIGH")
        anomalies_med = sum(1 for a in anomalies
                            if a.get("severity") == "MED")
        jobs_ok = sum(1 for j in job_statuses
                      if j.get("status") in ("ok", "partial_degraded"))

        fp = fp_verdicts or []
        fp_high = sum(1 for v in fp if v.get("severity") == "HIGH")
        fp_med = sum(1 for v in fp if v.get("severity") == "MED")

        quality = data.setdefault("quality", {})
        quality["observer"] = {
            "score": overall_score,
            "status": status,
            "anomalies_high": anomalies_high,
            "anomalies_med": anomalies_med,
            "fail_plausible_high": fp_high,
            "fail_plausible_med": fp_med,
            "jobs_ok": jobs_ok,
            "jobs_total": len(job_statuses),
            "last_run_date": target_date.strftime("%Y-%m-%d"),
            "last_updated_at": datetime.now().isoformat(timespec="seconds"),
            "v37_9_92": True,
        }

        save_status(data, updated_by="daily_observer",
                    audit_action="observer_score_update",
                    audit_target="status.json:quality.observer",
                    audit_summary=(
                        f"score={overall_score} "
                        f"high={anomalies_high} med={anomalies_med} "
                        f"jobs={jobs_ok}/{len(job_statuses)} "
                        f"date={target_date.strftime('%Y-%m-%d')}"))
        return True
    except Exception as e:
        log(f"WARN: V37.9.92 failed to write quality.observer: {e}")
        return False


def load_score_history(kb_dir, max_days=TREND_WINDOW_DAYS):
    """Load recent score history records.

    Returns: list of dicts, most recent first, up to max_days entries.
    """
    path = _score_history_path(kb_dir)
    if not os.path.isfile(path):
        return []
    records = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    seen = set()
    deduped = []
    for r in reversed(records):
        d = r.get("date", "")
        if d not in seen:
            seen.add(d)
            deduped.append(r)
        if len(deduped) >= max_days:
            break
    return deduped


def build_trend_section(history):
    """Build trend analysis section from score history.

    Returns: markdown string, or "" if insufficient data.
    """
    scored = [h for h in history if h.get("overall_score") is not None]
    if len(scored) < 2:
        return ""

    latest = scored[0]
    prev = scored[1]

    lines = ["## 📈 Trend (last 7 days)\n"]

    scores = [h["overall_score"] for h in scored]
    avg = sum(scores) / len(scores)
    delta = latest["overall_score"] - prev["overall_score"]

    if delta > 0:
        trend_icon = "📈"
        trend_word = "improved"
    elif delta < 0:
        trend_icon = "📉"
        trend_word = "declined"
    else:
        trend_icon = "➡️"
        trend_word = "stable"

    lines.append(f"- Today: ⭐{latest['overall_score']} | "
                 f"Previous: ⭐{prev['overall_score']} | "
                 f"{trend_icon} {trend_word} ({delta:+.1f})")
    lines.append(f"- 7-day avg: ⭐{avg:.1f} ({len(scored)} data points)")

    high_counts = [h.get("anomalies_high", 0) for h in scored]
    if sum(high_counts) > 0:
        lines.append(f"- HIGH anomalies: {' → '.join(str(c) for c in high_counts)}")

    job_rates = [f"{h.get('jobs_ok', 0)}/{h.get('jobs_total', 0)}"
                 for h in scored[:3]]
    lines.append(f"- Job success: {' → '.join(job_rates)}")

    lines.append("")
    return "\n".join(lines)


def build_trend_discord_suffix(history):
    """Build a short trend suffix for discord summary.

    Returns: string like " | 📈 +1 vs yesterday" or "".
    """
    scored = [h for h in history if h.get("overall_score") is not None]
    if len(scored) < 2:
        return ""
    delta = scored[0]["overall_score"] - scored[1]["overall_score"]
    if delta > 0:
        return f" | 📈 +{delta:.0f} vs prev"
    elif delta < 0:
        return f" | 📉 {delta:.0f} vs prev"
    return ""


# ══════════════════════════════════════════════════════════════════════
# 6c. V37.9.198 — fail-plausible 检测接入 (研究攻关 #1 Stage 5)
# ══════════════════════════════════════════════════════════════════════
#
# 机械化人眼 (论文 arXiv:2606.14589 §5.2): 把 llm_observer 两层管道
# (Layer 1 确定性 S1-S5 + Layer 2 LLM-judge) 接进每日 observer, 在用户之前
# 捕获 fail-plausible 类语义级静默故障 (D1-D4)。设计 docs/llm_observer_design.md §6.1。
#
# shadow-first 纪律 (镜像 ONTOLOGY_MODE / three_gate off→shadow→on):
#   OBSERVER_FP_MODE = off / shadow (默认) / on
#   - off    : 完全跳过 (逃生舱)
#   - shadow : 检测 + 专属报告段 + score_history/status fp 计数 (观察性),
#              【不】进主 anomalies → 不影响 overall_score、不触发主告警。
#              观察一周真实输出 (噪声/价值) 后再决定 on。
#   - on     : verdicts 进 anomalies (完整集成: 影响评分 + 告警 rollup)。
#
# 成本约束: cheap-path (Layer 2 仅在 Layer 1 命中触发) → 干净日 ≈0 LLM 调用,
#   事故日 +1-2 (与事故成正比)。dry_run → 仅 Layer 1 (零 LLM)。
# FAIL-OPEN: llm_observer 不可用 / LLM 错误 / 单 artifact 异常 → 不阻塞 observer run。

_FP_MODE_ENV = "OBSERVER_FP_MODE"
_FP_VALID_MODES = ("off", "shadow", "on")


def _fp_mode():
    """解析 OBSERVER_FP_MODE (off/shadow/on, 默认 shadow, 未知值 → shadow 观察优先)。"""
    v = os.environ.get(_FP_MODE_ENV, "shadow").strip().lower()
    return v if v in _FP_VALID_MODES else "shadow"


def build_fail_plausible_section(fp_verdicts, mode):
    """渲染 fail-plausible 报告段 (带 L1+L2 证据)。shadow 标注观察性。

    fp_verdicts 为空 → 简短 ✅ 行。off 模式调用方不会传 (返回空)。
    """
    if mode == "off":
        return ""
    tag = "观察性·不影响评分/告警" if mode == "shadow" else "已集成·影响评分/告警"
    lines = [f"## 🔬 Fail-Plausible 检测 [{mode}] ({tag})\n",
             "> 机械化人眼 Layer 1 确定性 + Layer 2 LLM-judge (研究攻关 #1)。\n"]
    if not fp_verdicts:
        lines.append("✅ 无 fail-plausible 信号")
        lines.append("")
        return "\n".join(lines)
    for v in fp_verdicts:
        lines.append(f"- [{v['severity']}] {v['category']} @ {v.get('artifact', '?')} "
                     f"(confidence {v.get('confidence', 0):.2f})")
        for e in v.get("evidence", []):
            if e.get("layer") == 1:
                lines.append(f"  - L1 {e.get('signal')} @L{e.get('locus')}: {e.get('snippet')}")
            else:
                lines.append(f"  - L2 {e.get('judge')}: {e.get('snippet')}"
                             + (f" — {e.get('rationale')}" if e.get("rationale") else ""))
    lines.append("")
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════
# 7. Orchestrator
# ══════════════════════════════════════════════════════════════════════

DEFAULT_KB_DIR = os.path.expanduser("~/.kb")
DEFAULT_JOBS_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "jobs"
)


def run(kb_dir=None, jobs_dir=None, target_date=None, dry_run=False,
        llm_caller=None):
    """Main orchestrator. Returns result dict.

    Args:
        kb_dir: path to KB directory (default ~/.kb)
        jobs_dir: path to jobs/ directory
        target_date: datetime for target day (default yesterday)
        dry_run: if True, skip LLM call
        llm_caller: injectable LLM function for testing

    Returns:
        dict with keys: status, date, report_markdown, discord_summary,
                        anomalies, overall_score, llm_ok, llm_reason
    """
    if kb_dir is None:
        kb_dir = os.environ.get("KB_DIR", DEFAULT_KB_DIR)
    if jobs_dir is None:
        jobs_dir = os.environ.get("JOBS_DIR", DEFAULT_JOBS_DIR)
    if target_date is None:
        target_date = datetime.now() - timedelta(days=1)

    log(f"scanning {target_date.strftime('%Y-%m-%d')} "
        f"(kb={kb_dir}, jobs={jobs_dir})")

    job_statuses = scan_job_statuses(jobs_dir, target_date)
    push_outputs = scan_push_outputs(kb_dir, target_date)
    source_sections = scan_source_sections(kb_dir, target_date)
    # V37.9.168: deep_dive degrade ratio over a rolling window ([19](c))
    deep_dive_modes = scan_deep_dive_modes(kb_dir, target_date)
    anomalies = detect_anomalies(job_statuses, push_outputs, source_sections,
                                 deep_dive_modes=deep_dive_modes)
    deep_dive_section = build_deep_dive_mode_section(deep_dive_modes)

    log(f"jobs: {len(job_statuses)} scanned, "
        f"outputs: {sum(1 for v in push_outputs.values() if v['found'])}/3, "
        f"sources: {len(source_sections)}, "
        f"deep_dive: {deep_dive_modes['total']} files "
        f"(degrade={deep_dive_modes['degrade_ratio']}), "
        f"anomalies: {len(anomalies)}")

    has_content = any(v.get("found") for v in push_outputs.values()) or source_sections

    if not has_content:
        report = build_report_markdown(
            target_date, job_statuses, push_outputs,
            source_sections, anomalies, "", None,
            deep_dive_section=deep_dive_section)
        return {
            "status": "no_outputs",
            "date": target_date.strftime("%Y-%m-%d"),
            "report_markdown": report,
            "discord_summary": f"🔍 Daily Self-Critique "
                               f"{target_date.strftime('%Y-%m-%d')}: "
                               f"no outputs found for target date",
            "anomalies": anomalies,
            "overall_score": None,
            "deep_dive_modes": deep_dive_modes,
            "llm_ok": False,
            "llm_reason": "no content to critique",
        }

    critique_prompt = build_critique_prompt(
        push_outputs, source_sections, anomalies, target_date)

    llm_content = ""
    llm_ok = False
    llm_reason = ""
    overall_score = None

    if dry_run:
        llm_reason = "dry_run"
        log("dry-run mode, skipping LLM call")
    elif not critique_prompt:
        llm_reason = "empty prompt"
    else:
        caller = llm_caller or call_llm_critique
        llm_ok, llm_content, llm_reason = caller(
            CRITIQUE_SYSTEM, critique_prompt)
        if llm_ok:
            overall_score = parse_overall_score(llm_content)
            log(f"LLM critique ok, overall_score={overall_score}")
        else:
            log(f"LLM critique failed: {llm_reason}")

    status = "ok" if llm_ok else ("no_outputs" if not has_content else "llm_failed")

    # V37.9.198 (Stage 5): fail-plausible 检测 (机械化人眼两层管道, §6.1)。
    # shadow-first: 默认 shadow → 观察性, 不进 anomalies (不影响评分/告警)。
    # cheap-path (Layer 2 仅 Layer 1 命中触发) + dry_run 仅 Layer 1 + FAIL-OPEN。
    fp_mode = _fp_mode()
    fp_verdicts = []
    if fp_mode != "off" and has_content:
        try:
            import llm_observer
            fp_caller = (llm_caller or call_llm_critique) if not dry_run else None
            fp_verdicts = llm_observer.scan_fail_plausible(
                push_outputs, source_sections,
                llm_caller=fp_caller, enable_layer2=(not dry_run))
            log(f"fail_plausible[{fp_mode}]: {len(fp_verdicts)} verdict(s)")
            if fp_mode == "on":
                anomalies.extend(fp_verdicts)   # 完整集成: 影响评分 + 告警
        except Exception as e:  # FAIL-OPEN: observer 绝不因 fp 检测崩溃
            log(f"WARN: fail_plausible scan failed (FAIL-OPEN): {e}")
            fp_verdicts = []
    fp_section = build_fail_plausible_section(fp_verdicts, fp_mode)

    # Score history + trend analysis
    append_score_history(kb_dir, target_date, overall_score, anomalies,
                         job_statuses, push_outputs, status,
                         fp_verdicts=fp_verdicts)
    # V37.9.92: surface observer summary to status.json quality.observer
    # for 三方共享意识 (PA / kb_status_refresh / health_check). FAIL-OPEN.
    _write_observer_to_status(kb_dir, target_date, overall_score, anomalies,
                              status, job_statuses, fp_verdicts=fp_verdicts)
    history = load_score_history(kb_dir)
    trend_section = build_trend_section(history)
    trend_suffix = build_trend_discord_suffix(history)

    report = build_report_markdown(
        target_date, job_statuses, push_outputs,
        source_sections, anomalies, llm_content, overall_score,
        trend_section=trend_section,
        deep_dive_section=deep_dive_section, fp_section=fp_section)

    discord = build_discord_summary(
        target_date, overall_score, anomalies, job_statuses,
        trend_suffix=trend_suffix)

    return {
        "status": status,
        "date": target_date.strftime("%Y-%m-%d"),
        "report_markdown": report,
        "discord_summary": discord,
        "anomalies": anomalies,
        "overall_score": overall_score,
        "deep_dive_modes": deep_dive_modes,
        "fail_plausible": fp_verdicts,
        "fp_mode": fp_mode,
        "llm_ok": llm_ok,
        "llm_reason": llm_reason,
    }


# ══════════════════════════════════════════════════════════════════════
# 8. CLI
# ══════════════════════════════════════════════════════════════════════

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Daily Self-Critique Observer")
    parser.add_argument("--date", help="Target date YYYYMMDD (default: yesterday)")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--dry-run", action="store_true",
                        help="Scan only, skip LLM call")
    parser.add_argument("--kb-dir", help="KB directory path")
    parser.add_argument("--jobs-dir", help="Jobs directory path")
    args = parser.parse_args()

    target_date = None
    if args.date:
        try:
            target_date = datetime.strptime(args.date, "%Y%m%d")
        except ValueError:
            print(f"Invalid date format: {args.date} (expected YYYYMMDD)",
                  file=sys.stderr)
            sys.exit(1)

    result = run(
        kb_dir=args.kb_dir,
        jobs_dir=args.jobs_dir,
        target_date=target_date,
        dry_run=args.dry_run,
    )

    if args.json:
        # V37.9.87: include report_markdown in JSON output so wrapper can
        # extract it without a second observer invocation. Eliminates the
        # double-write bug to score_history.jsonl (BUG #1) and the
        # last_run.json vs score_history score mismatch (BUG #2).
        # Pre-V37.9.87 wrapper called run() twice (once --json, once for
        # markdown); each call appended to score_history.jsonl with
        # potentially different LLM scores.
        output = dict(result)
        output["report_length"] = len(result.get("report_markdown", ""))
        print(json.dumps(output, ensure_ascii=False, indent=2))
    else:
        print(result["report_markdown"])


if __name__ == "__main__":
    main()
