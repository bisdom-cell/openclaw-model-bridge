"""
claude_escalation.py — V37.9.90 Claude Escalation Capability PoC

Direction 2 from V37.9.83 strategic sediment ("AI Partnership framework").

Architecture:
- PA (Qwen3-235B Mac Mini) detects "complex judgment needed" via SOUL.md rule 12
  trigger words (CLAUDE.md 原则 #24: SOUL.md 触发词是唯一可靠的工具调用机制)
- PA invokes `escalate(question)` as a custom tool
- This module performs a one-shot Anthropic SDK call (NOT a new Claude Code session)
- Context block (status.json + 14d CLAUDE.md changelog + relevant case docs) is cached
  via prompt caching (V37.9.83 第二原理 "系统自我成长" 在 framework 层的延伸 — Claude
  judgment is now machine-invokable, not session-dependent)
- Returns structured JSON proposal (read-only — no embedded shell commands)
- Audit logged per call

Design contracts:
1. FAIL-CLOSE on missing API key — no silent fallback to Qwen3 (boundaries > trust)
2. Read-only output enforcement — Claude proposes, human/PA decides
3. Daily quota — prevents runaway cost
4. Mockable transport — dev environment without ANTHROPIC_API_KEY can run --dry-run
5. Prompt caching on context block — ~90% cost reduction across repeated calls

V37.9.83 alignment:
- 方向 1 (Daily Self-Critique Observer V37.9.84) → 已落地
- 方向 2 (this) → V37.9.90 PoC, Mac Mini 集成留 V37.9.91+
- 方向 3 (Red Team Sandbox) → deferred

Skill: claude-api (Python SDK), prompt caching, structured outputs.

CLI:
    python3 claude_escalation.py --question "..." [--kb-dir ~/.kb] [--dry-run] [--json]
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone, timedelta


# ══════════════════════════════════════════════════════════════════════
# Constants
# ══════════════════════════════════════════════════════════════════════

# User explicitly named opus-4.7 first, sonnet-4.6 fallback (V37.9.90 design).
# Note: skill default is claude-opus-4-8; we respect explicit user choice.
DEFAULT_MODEL_PRIORITY = ("claude-opus-4-7", "claude-sonnet-4-6")

DEFAULT_MAX_TOKENS = 4000           # response cap; per-call cost ceiling
DEFAULT_CHANGELOG_DAYS = 14          # CLAUDE.md window
DEFAULT_DAILY_QUOTA = 10             # max escalations per day (cost guard)
DEFAULT_KB_DIR = os.path.expanduser("~/.kb")
DEFAULT_AUDIT_LOG = os.path.expanduser("~/.kb/audit/claude_escalations.jsonl")
DEFAULT_MAX_CONTEXT_CHARS = 80000    # context block ceiling (cache budget)

# Read-only output contract enforcement (V37.9.90 invariant: Claude proposes,
# human decides — never embed actual command lines for execution).
_SHELL_FENCE_RE = re.compile(r"```(?:bash|sh|shell|zsh|fish)\b", re.IGNORECASE)
_SHELL_SUBST_RE = re.compile(r"\$\(|`[^`]{2,}`")
_DANGEROUS_TOKENS = (
    "rm -rf",
    "sudo ",
    "chmod -R",
    "mkfs",
    "dd if=",
    "> /dev/sd",
    ":(){:|:&};:",
    "curl | bash",
    "wget | sh",
    "eval $(",
)

SYSTEM_PROMPT = """You are Claude, an expert consultant called by PA (the project author's WhatsApp PA agent, Qwen3-235B running on Mac Mini) for complex judgments that exceed PA's confidence.

Your role: read-only advisor. You propose; the human or PA decides what to act on.

Output contract (strict — your response MUST be a valid JSON object):

{
  "proposal": "1-3 paragraphs of plain prose. What to consider doing. No embedded shell commands. No code blocks. You MAY mention command names in prose (e.g. \\"review with git status\\") but never embed executable lines.",
  "rationale": "1-2 paragraphs. WHY this proposal — cite specific facts from the provided status.json / changelog / case docs.",
  "confidence": "high" | "medium" | "low",
  "refs": ["file path", "version number", "case doc ID", "commit SHA — only those that appear in provided context"]
}

Strict rules:
1. Read-only: never propose actions that modify production files, push commits, send messages, or execute commands. Propose them as suggestions for the human to evaluate.
2. Grounding: every concrete claim must be traceable to the provided context. If context is insufficient, say so in rationale and lower confidence to "low".
3. No fabrication: never invent file paths, version numbers, case IDs, or commit SHAs that do not appear in the provided context.
4. Brevity: if 3 sentences suffice, give 3 sentences. Don't pad.
5. No shell metachars: no `$()`, no backtick command substitution, no fenced shell blocks. If discussing a command, write it in plain text only.

Context provided to you (cached via prompt caching, reused across calls):
- status.json — current project state, priorities, unfinished items, recent changes
- CLAUDE.md recent changelog — last 14 days of major versions
- Relevant case docs — selected by keyword match against the question

The PA's specific question follows in the next user turn."""


# ══════════════════════════════════════════════════════════════════════
# Exceptions (structured failure)
# ══════════════════════════════════════════════════════════════════════


class EscalationError(Exception):
    """Base exception for escalation failures."""


class QuotaExceededError(EscalationError):
    """Daily call quota exceeded."""


# ══════════════════════════════════════════════════════════════════════
# Context loaders (status / changelog / case docs)
# ══════════════════════════════════════════════════════════════════════


def load_status(kb_dir):
    """Load status.json from kb_dir, $HOME, or repo root (in order).

    Returns dict on success, None if no candidate found / unparseable.
    """
    if kb_dir is None:
        kb_dir = DEFAULT_KB_DIR
    candidates = [
        os.path.join(kb_dir, "status.json"),
        os.path.expanduser("~/status.json"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "status.json"),
    ]
    for path in candidates:
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (OSError, json.JSONDecodeError):
                continue
    return None


def load_changelog_window(claude_md_path, days=DEFAULT_CHANGELOG_DAYS, today=None):
    """Extract recent N days of changelog from CLAUDE.md.

    Parses the version table format `| Vx.y.z | YYYY-MM-DD | body |`.
    Returns markdown string (possibly empty if no rows in window).

    Args:
        claude_md_path: path to CLAUDE.md
        days: window size (default 14)
        today: date object for testing (defaults to UTC today)
    """
    if not claude_md_path or not os.path.isfile(claude_md_path):
        return ""
    if today is None:
        today = datetime.now(timezone.utc).date()
    cutoff = today - timedelta(days=days)
    try:
        with open(claude_md_path, "r", encoding="utf-8") as f:
            content = f.read()
    except OSError:
        return ""

    # Pattern: | V37.9.X | YYYY-MM-DD | body |
    line_re = re.compile(
        r"^\|\s*(V[\d.]+(?:[-+][a-z0-9]+)?)\s*\|\s*(\d{4}-\d{2}-\d{2})\s*\|\s*(.+)\s*\|\s*$"
    )
    in_changelog = False
    rows = []
    for line in content.split("\n"):
        if "## 版本变更历史" in line or "## Version" in line:
            in_changelog = True
            continue
        if not in_changelog:
            continue
        # End of changelog section
        if line.startswith("## ") and "版本" not in line and "Version" not in line:
            break
        m = line_re.match(line)
        if not m:
            continue
        version, date_str, body = m.group(1), m.group(2), m.group(3)
        try:
            row_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            continue
        if row_date >= cutoff:
            # Truncate huge bodies (V37.9.83 has 600+ word entries)
            if len(body) > 4000:
                body = body[:4000] + "... (truncated)"
            rows.append(f"| {version} | {date_str} | {body} |")

    if not rows:
        return ""
    return (
        "## CLAUDE.md changelog (last {} days, {} entries)\n\n"
        "| 版本 | 日期 | 关键变更 |\n"
        "|------|------|----------|\n"
        "{}\n"
    ).format(days, len(rows), "\n".join(rows))


def select_relevant_case_docs(question, cases_dir, max_docs=3):
    """Keyword-match question against case doc filename + first 1KB of content.

    Returns list of (path, content) tuples, sorted by score (descending).
    Empty if no matches or cases_dir missing.
    """
    if not cases_dir or not os.path.isdir(cases_dir):
        return []

    q_lower = question.lower()
    # Extract keywords (length >= 4, alphanumeric)
    q_words = set(re.findall(r"[a-z_-][\w-]{3,}", q_lower))
    # Also try Chinese substring matching (2+ char sequences)
    q_zh = re.findall(r"[一-鿿]{2,}", question)

    scored = []
    for fname in sorted(os.listdir(cases_dir)):
        if not fname.endswith(".md"):
            continue
        path = os.path.join(cases_dir, fname)
        fname_lower = fname.lower()
        # Filename score
        score = sum(2 for w in q_words if w in fname_lower)
        # Content score (head only — full scan is expensive)
        try:
            with open(path, "r", encoding="utf-8") as f:
                head = f.read(1024)
        except OSError:
            continue
        head_lower = head.lower()
        score += sum(1 for w in q_words if w in head_lower)
        score += sum(2 for w in q_zh if w in head)
        if score > 0:
            scored.append((score, path))

    scored.sort(key=lambda x: -x[0])
    selected = []
    for _, path in scored[:max_docs]:
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
            selected.append((path, content))
        except OSError:
            continue
    return selected


def build_context_block(status, changelog, case_docs, max_chars=DEFAULT_MAX_CONTEXT_CHARS):
    """Assemble the stable context block for prompt caching.

    Layout: status.json → changelog → case docs.
    Truncates each section + overall to stay under max_chars.
    """
    parts = []
    if status is not None:
        try:
            status_str = json.dumps(status, indent=2, ensure_ascii=False)
        except (TypeError, ValueError):
            status_str = "(status.json contains non-serializable data)"
        if len(status_str) > 30000:
            status_str = status_str[:30000] + "\n... (truncated)"
        parts.append("## status.json (current project state)\n\n```json\n" + status_str + "\n```\n")

    if changelog:
        parts.append(changelog)

    if case_docs:
        parts.append("## Relevant case docs (selected by keyword match)\n")
        for path, content in case_docs:
            rel = os.path.basename(path)
            if len(content) > 8000:
                content = content[:8000] + "\n... (truncated)"
            parts.append("### " + rel + "\n\n" + content + "\n")

    combined = "\n".join(parts)
    if len(combined) > max_chars:
        combined = combined[:max_chars] + "\n\n... (context truncated at {} chars)".format(max_chars)
    return combined


# ══════════════════════════════════════════════════════════════════════
# Read-only output validator
# ══════════════════════════════════════════════════════════════════════


def validate_read_only(proposal_dict):
    """Scan all string fields recursively for shell command patterns.

    Returns list of violations. Each violation:
        {"field": "field.path[idx]", "pattern": "...", "snippet": "..."}

    Empty list = clean. V37.9.90 read-only contract enforcement.
    """
    violations = []

    def _scan_value(field, value):
        if isinstance(value, str):
            for m in _SHELL_FENCE_RE.finditer(value):
                start = max(0, m.start() - 20)
                end = min(len(value), m.end() + 40)
                violations.append({
                    "field": field,
                    "pattern": "shell_code_fence",
                    "snippet": value[start:end],
                })
            for m in _SHELL_SUBST_RE.finditer(value):
                start = max(0, m.start() - 20)
                end = min(len(value), m.end() + 20)
                violations.append({
                    "field": field,
                    "pattern": "command_substitution",
                    "snippet": value[start:end],
                })
            v_lower = value.lower()
            for token in _DANGEROUS_TOKENS:
                idx = v_lower.find(token.lower())
                if idx >= 0:
                    start = max(0, idx - 20)
                    end = min(len(value), idx + len(token) + 20)
                    violations.append({
                        "field": field,
                        "pattern": "dangerous_token:" + token,
                        "snippet": value[start:end],
                    })
        elif isinstance(value, list):
            for i, item in enumerate(value):
                _scan_value(field + "[" + str(i) + "]", item)
        elif isinstance(value, dict):
            for k, v in value.items():
                _scan_value(field + "." + str(k), v)

    if isinstance(proposal_dict, dict):
        for field, value in proposal_dict.items():
            _scan_value(field, value)
    return violations


# ══════════════════════════════════════════════════════════════════════
# Quota tracking + audit logging
# ══════════════════════════════════════════════════════════════════════


def check_daily_quota(audit_log_path, max_daily=DEFAULT_DAILY_QUOTA, today=None):
    """Count escalations today from audit log. Raise QuotaExceededError if over.

    Returns current count if under quota.
    """
    if today is None:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if not audit_log_path or not os.path.isfile(audit_log_path):
        return 0
    count = 0
    try:
        with open(audit_log_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts = rec.get("timestamp_iso", "")
                if ts.startswith(today):
                    count += 1
    except OSError:
        return 0
    if count >= max_daily:
        raise QuotaExceededError(
            "Daily quota exceeded: " + str(count) + "/" + str(max_daily)
            + " escalations today"
        )
    return count


def write_audit_record(audit_log_path, record):
    """Append JSONL record. FAIL-OPEN on IO error (log to stderr, never crash caller)."""
    if not audit_log_path:
        return
    try:
        parent = os.path.dirname(audit_log_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(audit_log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as e:
        print("[claude_escalation] WARN: audit write failed: " + str(e), file=sys.stderr)


# ══════════════════════════════════════════════════════════════════════
# Anthropic API call (with prompt caching + adaptive thinking)
# ══════════════════════════════════════════════════════════════════════


def call_claude(client, model, system_blocks, user_message, max_tokens):
    """Call Anthropic SDK messages.create with prompt caching.

    Returns (success: bool, text: str, usage: dict, error: str).

    Uses:
    - System blocks with cache_control on last (stable context) block
    - Adaptive thinking (off by default on Opus 4.7; we set explicitly)
    - effort=high (recommended minimum on Opus 4.7 / Sonnet 4.6)
    """
    try:
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system_blocks,
            messages=[{"role": "user", "content": user_message}],
            thinking={"type": "adaptive"},
            output_config={"effort": "high"},
        )
    except Exception as e:
        return False, "", {}, type(e).__name__ + ": " + str(e)

    # Extract text content blocks
    text_parts = []
    for block in getattr(response, "content", []) or []:
        if getattr(block, "type", None) == "text":
            text_parts.append(getattr(block, "text", ""))
    text = "\n".join(text_parts).strip()

    # Usage metrics (for audit + cost tracking)
    usage = {}
    if hasattr(response, "usage"):
        u = response.usage
        usage = {
            "input_tokens": getattr(u, "input_tokens", 0),
            "output_tokens": getattr(u, "output_tokens", 0),
            "cache_read_input_tokens": getattr(u, "cache_read_input_tokens", 0),
            "cache_creation_input_tokens": getattr(u, "cache_creation_input_tokens", 0),
        }

    return True, text, usage, ""


def parse_response_json(text):
    """Extract JSON object from Claude's response text.

    Tries direct json.loads first, then first {...} block via regex.
    Raises ValueError on failure.
    """
    if not text:
        raise ValueError("empty response")
    text = text.strip()
    # Direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Try first {...} block (greedy match for nested objects)
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    raise ValueError("response is not valid JSON; first 200 chars: " + text[:200])


# ══════════════════════════════════════════════════════════════════════
# Main entry: escalate()
# ══════════════════════════════════════════════════════════════════════


def escalate(
    question,
    kb_dir=None,
    claude_md_path=None,
    cases_dir=None,
    audit_log_path=None,
    client=None,
    model_priority=None,
    max_tokens=DEFAULT_MAX_TOKENS,
    max_daily=DEFAULT_DAILY_QUOTA,
    dry_run=False,
    today_for_test=None,
):
    """One-shot Claude escalation.

    Args:
        question: str, non-empty. The PA's question.
        kb_dir: ~/.kb path.
        claude_md_path: CLAUDE.md path (default repo CLAUDE.md).
        cases_dir: ontology/docs/cases/ path.
        audit_log_path: JSONL audit log path.
        client: optional anthropic.Anthropic instance (for testing).
        model_priority: tuple of model IDs (default opus-4.7, sonnet-4.6).
        max_tokens: response token cap.
        max_daily: daily escalation quota.
        dry_run: if True, returns synthetic response without API call.
        today_for_test: YYYY-MM-DD override (quota tests).

    Returns dict with keys:
        status: "ok" | "quota_exceeded" | "no_context" | "api_unavailable"
              | "parse_failed" | "read_only_violation" | "dry_run"
        model_used, proposal, rationale, confidence, refs, usage,
        violations (only on read_only_violation),
        error (on failure).
    """
    # Input validation
    if not question or not isinstance(question, str) or not question.strip():
        return {
            "status": "no_context",
            "error": "question must be non-empty string",
        }

    # Resolve paths
    repo_root = os.path.dirname(os.path.abspath(__file__))
    if kb_dir is None:
        kb_dir = DEFAULT_KB_DIR
    if claude_md_path is None:
        claude_md_path = os.path.join(repo_root, "CLAUDE.md")
    if cases_dir is None:
        cases_dir = os.path.join(repo_root, "ontology", "docs", "cases")
    if audit_log_path is None:
        audit_log_path = DEFAULT_AUDIT_LOG
    if model_priority is None:
        model_priority = DEFAULT_MODEL_PRIORITY

    # Quota check (BEFORE loading context — fail fast)
    try:
        check_daily_quota(audit_log_path, max_daily, today=today_for_test)
    except QuotaExceededError as e:
        return {"status": "quota_exceeded", "error": str(e)}

    # Load context (status + changelog + case docs)
    status_data = load_status(kb_dir)
    changelog = load_changelog_window(claude_md_path)
    case_docs = select_relevant_case_docs(question, cases_dir)

    if status_data is None and not changelog and not case_docs:
        return {
            "status": "no_context",
            "error": (
                "could not load any context (no status.json, no CLAUDE.md changelog, "
                "no matching case docs)"
            ),
        }

    context_md = build_context_block(status_data, changelog, case_docs)

    # Build system blocks with prompt caching on the context block (stable)
    system_blocks = [
        {"type": "text", "text": SYSTEM_PROMPT},
        {
            "type": "text",
            "text": context_md,
            "cache_control": {"type": "ephemeral"},
        },
    ]

    user_message = (
        "Question from PA:\n\n"
        + question.strip()
        + "\n\nRespond as a JSON object with the fields described in your system prompt."
    )

    # Dry-run path — dev environment without ANTHROPIC_API_KEY
    if dry_run:
        synthetic = {
            "proposal": "[DRY RUN] No API call made. Question recorded for review.",
            "rationale": (
                "Dry-run mode: returning synthetic response. In production this would "
                "consult Claude " + model_priority[0] + " with prompt caching on context."
            ),
            "confidence": "low",
            "refs": [],
        }
        record = {
            "timestamp_iso": today_for_test or datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            ),
            "question_preview": question[:500],
            "model_used": None,
            "status": "dry_run",
            "usage": {},
            "context_chars": len(context_md),
        }
        write_audit_record(audit_log_path, record)
        return {
            "status": "dry_run",
            "model_used": None,
            "usage": {},
            **synthetic,
        }

    # Acquire client (lazy import so dev env without anthropic package can still run --dry-run)
    if client is None:
        try:
            import anthropic
            client = anthropic.Anthropic()
        except ImportError as e:
            return {
                "status": "api_unavailable",
                "error": "anthropic SDK not installed: " + str(e),
            }
        except Exception as e:
            return {
                "status": "api_unavailable",
                "error": "client init failed (likely missing ANTHROPIC_API_KEY): " + str(e),
            }

    # Try models in priority order (opus-4.7, then sonnet-4.6 for cost fallback)
    last_error = ""
    for model in model_priority:
        ok, text, usage, err = call_claude(
            client, model, system_blocks, user_message, max_tokens
        )
        if not ok:
            last_error = err
            continue

        # Parse JSON
        try:
            proposal_dict = parse_response_json(text)
        except ValueError as e:
            last_error = "parse_failed on " + model + ": " + str(e)
            continue

        # Validate read-only contract
        violations = validate_read_only(proposal_dict)
        if violations:
            record = {
                "timestamp_iso": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "question_preview": question[:500],
                "model_used": model,
                "status": "read_only_violation",
                "violations_count": len(violations),
                "usage": usage,
                "context_chars": len(context_md),
            }
            write_audit_record(audit_log_path, record)
            return {
                "status": "read_only_violation",
                "model_used": model,
                "violations": violations,
                "usage": usage,
                "error": (
                    str(len(violations)) + " read-only contract violations detected; "
                    "response rejected"
                ),
            }

        # Success
        record = {
            "timestamp_iso": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "question_preview": question[:500],
            "model_used": model,
            "status": "ok",
            "usage": usage,
            "context_chars": len(context_md),
            "confidence": proposal_dict.get("confidence", "unknown"),
        }
        write_audit_record(audit_log_path, record)
        return {
            "status": "ok",
            "model_used": model,
            "proposal": proposal_dict.get("proposal", ""),
            "rationale": proposal_dict.get("rationale", ""),
            "confidence": proposal_dict.get("confidence", "low"),
            "refs": proposal_dict.get("refs", []),
            "usage": usage,
        }

    # All models failed
    record = {
        "timestamp_iso": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "question_preview": question[:500],
        "model_used": None,
        "status": "api_unavailable",
        "error": last_error[:300],
        "context_chars": len(context_md),
    }
    write_audit_record(audit_log_path, record)
    return {
        "status": "api_unavailable",
        "model_used": None,
        "error": last_error,
    }


# ══════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════


def main():
    parser = argparse.ArgumentParser(
        description="Claude Escalation Capability (V37.9.90 PoC) — "
                    "one-shot Anthropic SDK call with context + read-only output."
    )
    parser.add_argument("--question", required=True, help="Question for Claude")
    parser.add_argument("--kb-dir", help="KB directory (default ~/.kb)")
    parser.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS)
    parser.add_argument("--max-daily", type=int, default=DEFAULT_DAILY_QUOTA)
    parser.add_argument("--dry-run", action="store_true",
                        help="Skip API call (dev mode without ANTHROPIC_API_KEY)")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    result = escalate(
        question=args.question,
        kb_dir=args.kb_dir,
        max_tokens=args.max_tokens,
        max_daily=args.max_daily,
        dry_run=args.dry_run,
    )

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("Status: " + result["status"])
        if result.get("model_used"):
            print("Model: " + result["model_used"])
        if "proposal" in result:
            print("\n--- Proposal ---\n" + result["proposal"])
            print("\n--- Rationale ---\n" + result.get("rationale", ""))
            print("\nConfidence: " + str(result.get("confidence", "?")))
            refs = result.get("refs", [])
            if refs:
                print("Refs: " + ", ".join(str(r) for r in refs))
        if result.get("usage"):
            u = result["usage"]
            print("\nUsage: input=" + str(u.get("input_tokens", 0))
                  + " output=" + str(u.get("output_tokens", 0))
                  + " cache_read=" + str(u.get("cache_read_input_tokens", 0)))
        if "error" in result:
            print("\nError: " + result["error"])
        if result.get("violations"):
            print("\nRead-only violations: " + str(len(result["violations"])))
            for v in result["violations"][:5]:
                print("  - " + v["field"] + ": " + v["pattern"]
                      + " | " + repr(v["snippet"]))

    sys.exit(0 if result["status"] in ("ok", "dry_run") else 1)


if __name__ == "__main__":
    main()
