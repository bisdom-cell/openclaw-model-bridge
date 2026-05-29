#!/usr/bin/env python3
"""
daily_observer.py — V37.9.84 Daily Self-Critique Observer

每日 LLM-as-judge 扫前日推送输出，评分 + 发现问题 + 输出 read-only 改进提案。
永不直接修改任何生产文件/代码/配置。

设计契约 (V37.9.83 三大第一性原理 方向 1 兑现):
  - READ-ONLY: 只读取 ~/.kb/ 和 jobs/*/cache/ 数据，零写入生产文件
  - FAIL-OPEN: observer 失败不影响任何生产 cron
  - LLM-as-judge: 用第三方视角评估推送质量，不是自评
  - 输出: ~/.kb/self_critique/daily_critique_YYYYMMDD.md

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

JOBS_SUBDIRS = [
    "hf_papers", "arxiv_monitor", "semantic_scholar", "dblp",
    "acl_anthology", "pwc", "github_trending", "rss_blogs",
    "ai_leaders_x", "karpathy_x", "ontology_sources",
    "freight_watcher", "finance_news", "openclaw_official",
    "chaspark",
]

# V37.9.56-hotfix same pattern: Mac Mini deploys to ~/.openclaw/jobs/
# HN special case: subdirectory is hn_watcher not run_hn_fixed
_MAC_MINI_JOBS_DIR = os.path.expanduser("~/.openclaw/jobs")


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


def scan_job_statuses(jobs_dir, target_date):
    """Scan last_run.json from all known job subdirectories.

    Returns:
        list of dict: [{job_id, status, time, new, reason, found}, ...]
    """
    results = []
    for job_id in JOBS_SUBDIRS:
        lr_path = _resolve_last_run_path(jobs_dir, job_id)
        entry = {"job_id": job_id, "found": False, "status": "unknown",
                 "time": "", "new": 0, "reason": ""}
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
        except (OSError, json.JSONDecodeError):
            entry["found"] = True
            entry["status"] = "parse_error"
        results.append(entry)
    return results


# ══════════════════════════════════════════════════════════════════════
# 2. Push Output Scanner — 读 evening/dream/deep_dive/sources
# ══════════════════════════════════════════════════════════════════════

MAX_SAMPLE_CHARS = 2000


def _read_file_sample(path, max_chars=MAX_SAMPLE_CHARS):
    """Read first max_chars of a file. Returns (content, full_length)."""
    if not os.path.isfile(path):
        return "", 0
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        return content[:max_chars], len(content)
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
            text = "".join(section_lines)[:max_chars_per]
            results.append({
                "source": source_name,
                "section_text": text,
                "char_count": len("".join(section_lines)),
            })

    results.sort(key=lambda x: x["char_count"], reverse=True)
    return results[:max_sources]


# ══════════════════════════════════════════════════════════════════════
# 3. Rule-Based Anomaly Detection (no LLM needed)
# ══════════════════════════════════════════════════════════════════════

def detect_anomalies(job_statuses, push_outputs, source_sections):
    """Detect obvious anomalies using rules (no LLM).

    Returns:
        list of dict: [{severity, category, message}, ...]
        severity: HIGH / MED / LOW
    """
    anomalies = []

    failed_jobs = [j for j in job_statuses
                   if j["status"] in ("llm_failed", "fetch_failed", "send_failed")]
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
- 提案必须具体可执行，不要泛泛建议"""

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
                          overall_score, trend_section=""):
    """Build the final markdown report."""
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
                         job_statuses, push_outputs, status):
    """Append one record to score_history.jsonl. FAIL-OPEN."""
    path = _score_history_path(kb_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)

    ok_jobs = sum(1 for j in job_statuses
                  if j["status"] in ("ok", "partial_degraded"))
    high = sum(1 for a in anomalies if a["severity"] == "HIGH")
    med = sum(1 for a in anomalies if a["severity"] == "MED")
    outputs_found = sum(1 for v in push_outputs.values() if v.get("found"))

    record = {
        "date": target_date.strftime("%Y-%m-%d"),
        "overall_score": overall_score,
        "jobs_ok": ok_jobs,
        "jobs_total": len(job_statuses),
        "outputs_found": outputs_found,
        "anomalies_high": high,
        "anomalies_med": med,
        "status": status,
    }
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        pass


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
    anomalies = detect_anomalies(job_statuses, push_outputs, source_sections)

    log(f"jobs: {len(job_statuses)} scanned, "
        f"outputs: {sum(1 for v in push_outputs.values() if v['found'])}/3, "
        f"sources: {len(source_sections)}, "
        f"anomalies: {len(anomalies)}")

    has_content = any(v.get("found") for v in push_outputs.values()) or source_sections

    if not has_content:
        report = build_report_markdown(
            target_date, job_statuses, push_outputs,
            source_sections, anomalies, "", None)
        return {
            "status": "no_outputs",
            "date": target_date.strftime("%Y-%m-%d"),
            "report_markdown": report,
            "discord_summary": f"🔍 Daily Self-Critique "
                               f"{target_date.strftime('%Y-%m-%d')}: "
                               f"no outputs found for target date",
            "anomalies": anomalies,
            "overall_score": None,
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

    # Score history + trend analysis
    append_score_history(kb_dir, target_date, overall_score, anomalies,
                         job_statuses, push_outputs, status)
    history = load_score_history(kb_dir)
    trend_section = build_trend_section(history)
    trend_suffix = build_trend_discord_suffix(history)

    report = build_report_markdown(
        target_date, job_statuses, push_outputs,
        source_sections, anomalies, llm_content, overall_score,
        trend_section=trend_section)

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
