"""
expert_escalation.py — V37.9.90-r1 Expert Escalation Capability

V37.9.83 Direction 2 (AI Partnership Framework) — redesigned 2026-05-29 to
route via Doubao Seed 2.0 Pro (already running in production, V37.9.55) instead
of Anthropic SDK (Claude pending future-flip when API key + integration ready).

Why Doubao first:
- Already integrated: V37.9.52 接入 + V37.9.55 flip verified_text/streaming/
  tool_calling/reasoning (cap_score=16, framework视角 > Qwen3 cap_score=14)
- Already paid: ARK_API_KEY + ARK_ENDPOINT_ID already in plist (no new key mgmt)
- 10-30x cheaper than Claude Opus 4.7 (~$0.01-0.02/call vs ~$0.47 首调)
- Volcengine Context Cache: automatic prompt caching for prefix ≥ 1024 tokens
  (no manual cache_control needed; we keep stable prefix structure to benefit)
- Reasoning model: doubao-seed-2-0-pro returns `reasoning_content` field
  (similar surface to Claude adaptive thinking)
- Zero new dependency: uses stdlib urllib (no openai / anthropic / requests)

Architecture (unchanged from v1):
- PA (Qwen3-235B Mac Mini) detects "复杂判断" via SOUL.md 规则 12 trigger words
- PA invokes `expert_escalate(question)` as custom tool
- One-shot Volcengine Ark Chat Completions call (NOT a multi-turn agent loop)
- Context block (status.json + 14d CLAUDE.md changelog + relevant case docs)
  benefits from Volcengine Context Cache automatically
- Returns structured JSON proposal (read-only — no embedded shell commands)
- Audit logged per call; daily quota guards cost

Backend selector:
- backend="doubao"  → primary (this PR, V37.9.90-r1)
- backend="claude_pending" → returns status=claude_pending error (future flip)

V37.9.83 alignment:
- 方向 1 Daily Self-Critique Observer → V37.9.84 已上线 (3 天数据)
- 方向 2 Expert Escalation → V37.9.90-r1 (this PR; Claude path stubbed)
- 方向 3 Red Team Sandbox → deferred

CLI usage:
    python3 expert_escalation.py --question "..." [--dry-run] [--json] [--backend doubao]
"""

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone, timedelta


# ══════════════════════════════════════════════════════════════════════
# Constants
# ══════════════════════════════════════════════════════════════════════

# Backend selection — V37.9.90-r1 routes to Doubao (already running).
# Claude path stubbed as future-flip when ANTHROPIC_API_KEY + integration ready.
BACKEND_DOUBAO = "doubao"
BACKEND_CLAUDE_PENDING = "claude_pending"
DEFAULT_BACKEND = BACKEND_DOUBAO

# Volcengine Ark — V37.9.52 plugin registered, V37.9.55 verified.
# OpenAI Chat Completions compatible — no custom client needed.
DOUBAO_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"
DOUBAO_ENDPOINT_URL = DOUBAO_BASE_URL + "/chat/completions"
DOUBAO_API_KEY_ENV = "ARK_API_KEY"
DOUBAO_ENDPOINT_ID_ENV = "ARK_ENDPOINT_ID"
# Public model identifier — fallback when ARK_ENDPOINT_ID env missing (dev safe).
# In production ARK_ENDPOINT_ID = "ep-20260511174451-dlhm8" (user-specific).
DOUBAO_DEFAULT_MODEL_ID = "doubao-seed-2-0-pro"
DOUBAO_REQUEST_TIMEOUT_SEC = 60

DEFAULT_MAX_TOKENS = 4000          # response cap; per-call cost ceiling
DEFAULT_CHANGELOG_DAYS = 14         # CLAUDE.md window
# Doubao cost ~$0.01-0.02/call → daily quota can be more generous than Claude's 10.
DEFAULT_DAILY_QUOTA = 30
DEFAULT_KB_DIR = os.path.expanduser("~/.kb")
DEFAULT_AUDIT_LOG = os.path.expanduser("~/.kb/audit/expert_escalations.jsonl")
DEFAULT_MAX_CONTEXT_CHARS = 80000   # Volcengine context window allows much more

# Read-only output contract enforcement.
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

SYSTEM_PROMPT = """You are an expert consultant called by PA (the project author's WhatsApp PA agent, Qwen3-235B running on Mac Mini) for complex judgments that exceed PA's confidence.

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

Context provided to you (benefits from Volcengine automatic context cache,
~10-25% input cost when prefix is stable across calls):
- status.json — current project state, priorities, unfinished items, recent changes
- CLAUDE.md recent changelog — last 14 days of major versions
- Relevant case docs — selected by keyword match against the question

The PA's specific question follows in the next user turn."""


# ══════════════════════════════════════════════════════════════════════
# Exceptions
# ══════════════════════════════════════════════════════════════════════


class EscalationError(Exception):
    """Base exception for escalation failures."""


class QuotaExceededError(EscalationError):
    """Daily call quota exceeded."""


# ══════════════════════════════════════════════════════════════════════
# Context loaders
# ══════════════════════════════════════════════════════════════════════


def load_status(kb_dir):
    """Load status.json from kb_dir, $HOME, Mac Mini canonical, or repo root.

    V37.9.94: 4th candidate (~/openclaw-model-bridge/status.json) added —
    auto_deploy FILE_MAP deploys this script to $HOME, so `dirname(__file__)`
    resolves to `$HOME/status.json` (collapses with candidate 2). Without
    the canonical path, fresh Mac Mini installs (no ~/.kb/status.json yet)
    or `kb_dir` misconfigured cases silently fall back to non-existent paths.
    Same blood lesson as V37.9.56-hotfix / V37.9.76-hotfix / V37.9.78-hotfix
    / V37.9.92 (MR-15 5th near-miss, caught by cross_env_path_scanner first
    scan).
    """
    if kb_dir is None:
        kb_dir = DEFAULT_KB_DIR
    candidates = [
        os.path.join(kb_dir, "status.json"),
        os.path.expanduser("~/status.json"),
        # V37.9.94: Mac Mini canonical (auto_deploy doesn't copy yaml/md/json
        # to $HOME, repo lives in $HOME/openclaw-model-bridge/)
        os.path.expanduser("~/openclaw-model-bridge/status.json"),
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

    Parses `| Vx.y.z | YYYY-MM-DD | body |` rows.
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
            if len(body) > 4000:
                body = body[:4000] + "... (truncated)"
            rows.append("| " + version + " | " + date_str + " | " + body + " |")

    if not rows:
        return ""
    return (
        "## CLAUDE.md changelog (last "
        + str(days) + " days, " + str(len(rows)) + " entries)\n\n"
        + "| 版本 | 日期 | 关键变更 |\n"
        + "|------|------|----------|\n"
        + "\n".join(rows) + "\n"
    )


def select_relevant_case_docs(question, cases_dir, max_docs=3):
    """Keyword-match question against case doc filename + first 1KB content."""
    if not cases_dir or not os.path.isdir(cases_dir):
        return []

    q_lower = question.lower()
    q_words = set(re.findall(r"[a-z_-][\w-]{3,}", q_lower))
    q_zh = re.findall(r"[一-鿿]{2,}", question)

    scored = []
    for fname in sorted(os.listdir(cases_dir)):
        if not fname.endswith(".md"):
            continue
        path = os.path.join(cases_dir, fname)
        fname_lower = fname.lower()
        score = sum(2 for w in q_words if w in fname_lower)
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
    """Assemble the stable context block.

    Volcengine Context Cache benefits from stable prefix bytes across calls.
    Layout: status.json → changelog → case docs (most stable first).
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
    """Scan all string fields recursively for shell command patterns."""
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
    """Count escalations today. Raise QuotaExceededError if over."""
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
    """Append JSONL record. FAIL-OPEN on IO error."""
    if not audit_log_path:
        return
    try:
        parent = os.path.dirname(audit_log_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(audit_log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as e:
        print("[expert_escalation] WARN: audit write failed: " + str(e), file=sys.stderr)


# ══════════════════════════════════════════════════════════════════════
# Doubao (Volcengine Ark) HTTP transport
# ══════════════════════════════════════════════════════════════════════


class DoubaoTransport:
    """Volcengine Ark HTTP transport using stdlib urllib (zero new deps).

    OpenAI Chat Completions compatible API. Mockable: subclass override _post()
    in tests to inject responses, or pass to escalate() directly.
    """

    def __init__(self, api_key=None, endpoint_id=None, base_url=DOUBAO_BASE_URL,
                 timeout=DOUBAO_REQUEST_TIMEOUT_SEC):
        self.api_key = api_key or os.environ.get(DOUBAO_API_KEY_ENV, "")
        # endpoint_id is user-specific (e.g. ep-20260511174451-dlhm8) in production;
        # falls back to public model identifier in dev (safe — won't auth without key).
        self.endpoint_id = (endpoint_id
                            or os.environ.get(DOUBAO_ENDPOINT_ID_ENV, "")
                            or DOUBAO_DEFAULT_MODEL_ID)
        self.base_url = base_url
        self.endpoint_url = base_url + "/chat/completions"
        self.timeout = timeout

    def is_configured(self):
        """True if API key present. Endpoint ID falls back to public default."""
        return bool(self.api_key)

    def _post(self, payload):
        """POST JSON to endpoint_url. Returns (success, response_dict, error_str).

        Override in test subclasses to inject mock responses.
        """
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            self.endpoint_url,
            data=data,
            method="POST",
            headers={
                "Authorization": "Bearer " + self.api_key,
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = resp.read().decode("utf-8", errors="replace")
                return True, json.loads(body), ""
        except urllib.error.HTTPError as e:
            try:
                err_body = e.read().decode("utf-8", errors="replace")
            except Exception:
                err_body = ""
            return False, {}, (
                "HTTPError " + str(e.code) + ": " + str(e.reason)
                + (" | " + err_body[:300] if err_body else "")
            )
        except urllib.error.URLError as e:
            return False, {}, "URLError: " + str(e.reason)
        except Exception as e:
            return False, {}, type(e).__name__ + ": " + str(e)

    def call(self, system_prompt, context_md, user_message, max_tokens):
        """Call Doubao with structured messages.

        Volcengine Context Cache automatically caches stable prefix when ≥ 1024
        tokens — no manual cache_control needed. We keep stable prefix structure
        (system_prompt + context_md concatenated) to maximize cache hits.

        Returns (success: bool, text: str, usage: dict, error: str).
        """
        if not self.is_configured():
            return False, "", {}, (
                "ARK_API_KEY not set (configure in plist EnvironmentVariables on Mac Mini)"
            )

        # Concatenate system_prompt + context_md as a single system message.
        # Stable bytes across calls → Volcengine Context Cache auto applies.
        full_system = system_prompt + "\n\n" + context_md

        payload = {
            "model": self.endpoint_id,
            "messages": [
                {"role": "system", "content": full_system},
                {"role": "user", "content": user_message},
            ],
            "max_tokens": max_tokens,
            # Doubao supports temperature (unlike Claude Opus 4.7/4.8).
            # 0.3 = low variance, more deterministic for expert advisory.
            "temperature": 0.3,
        }

        ok, response, err = self._post(payload)
        if not ok:
            return False, "", {}, err

        # Extract content from OpenAI-compatible response
        choices = response.get("choices", [])
        if not choices:
            return False, "", {}, "no choices in response: " + json.dumps(response)[:200]
        message = choices[0].get("message", {})
        text = message.get("content", "")
        # Doubao seed reasoning model also returns reasoning_content (captured
        # for audit visibility; future analysis may want it)
        reasoning_chars = len(message.get("reasoning_content", "") or "")

        usage_raw = response.get("usage", {}) or {}
        # Volcengine returns cached_tokens in prompt_tokens_details when cache hits
        cache_read = 0
        pt_details = usage_raw.get("prompt_tokens_details")
        if isinstance(pt_details, dict):
            cache_read = pt_details.get("cached_tokens", 0)
        usage = {
            "input_tokens": usage_raw.get("prompt_tokens", 0),
            "output_tokens": usage_raw.get("completion_tokens", 0),
            "cache_read_input_tokens": cache_read,
            "reasoning_chars": reasoning_chars,
            "finish_reason": choices[0].get("finish_reason", "unknown"),
        }
        return True, text, usage, ""


def parse_response_json(text):
    """Extract JSON object from response text.

    Tries direct json.loads, then first {...} block via regex.
    """
    if not text:
        raise ValueError("empty response")
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
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
    transport=None,
    backend=DEFAULT_BACKEND,
    max_tokens=DEFAULT_MAX_TOKENS,
    max_daily=DEFAULT_DAILY_QUOTA,
    dry_run=False,
    today_for_test=None,
):
    """One-shot expert escalation (V37.9.90-r1 Doubao backend).

    Returns dict:
        status: "ok" | "quota_exceeded" | "no_context" | "api_unavailable"
              | "parse_failed" | "read_only_violation" | "dry_run"
              | "claude_pending" | "unknown_backend"
        backend, proposal, rationale, confidence, refs, usage,
        violations (only on read_only_violation), error (on failure).
    """
    if not question or not isinstance(question, str) or not question.strip():
        return {
            "status": "no_context",
            "error": "question must be non-empty string",
        }

    # Backend gate (Claude pending; future-flip when API key + integration ready)
    if backend == BACKEND_CLAUDE_PENDING:
        return {
            "status": "claude_pending",
            "backend": BACKEND_CLAUDE_PENDING,
            "error": (
                "Claude backend deferred to V37.9.91+ (awaiting ANTHROPIC_API_KEY "
                "+ Mac Mini integration). Currently routing via Doubao."
            ),
        }
    if backend != BACKEND_DOUBAO:
        return {
            "status": "unknown_backend",
            "error": "unknown backend: " + repr(backend)
                     + " (supported: doubao, claude_pending)",
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

    # Quota check (FAIL-CLOSE before loading context)
    try:
        check_daily_quota(audit_log_path, max_daily, today=today_for_test)
    except QuotaExceededError as e:
        return {"status": "quota_exceeded", "backend": backend, "error": str(e)}

    # Load context
    status_data = load_status(kb_dir)
    changelog = load_changelog_window(claude_md_path)
    case_docs = select_relevant_case_docs(question, cases_dir)

    if status_data is None and not changelog and not case_docs:
        return {
            "status": "no_context",
            "backend": backend,
            "error": (
                "could not load any context (no status.json, no CLAUDE.md changelog, "
                "no matching case docs)"
            ),
        }

    context_md = build_context_block(status_data, changelog, case_docs)
    user_message = (
        "Question from PA:\n\n"
        + question.strip()
        + "\n\nRespond as a JSON object with the fields described in your system prompt."
    )

    # Dry-run path
    if dry_run:
        synthetic = {
            "proposal": "[DRY RUN] No API call made. Question recorded for review.",
            "rationale": (
                "Dry-run mode: synthetic response. In production this would route via "
                "Doubao Seed 2.0 Pro on Volcengine Ark with automatic context cache."
            ),
            "confidence": "low",
            "refs": [],
        }
        record = {
            "timestamp_iso": today_for_test or datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            ),
            "backend": backend,
            "question_preview": question[:500],
            "status": "dry_run",
            "usage": {},
            "context_chars": len(context_md),
        }
        write_audit_record(audit_log_path, record)
        return {
            "status": "dry_run",
            "backend": backend,
            "usage": {},
            **synthetic,
        }

    # Acquire transport (Doubao). Mockable via subclass.
    if transport is None:
        transport = DoubaoTransport()

    if not transport.is_configured():
        return {
            "status": "api_unavailable",
            "backend": backend,
            "error": (
                DOUBAO_API_KEY_ENV + " not set. Configure in Mac Mini plist "
                "EnvironmentVariables (V37.9.55 same path as adapter). "
                "Use --dry-run for dev validation."
            ),
        }

    # Make the call
    ok, text, usage, err = transport.call(
        SYSTEM_PROMPT, context_md, user_message, max_tokens
    )
    if not ok:
        record = {
            "timestamp_iso": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "backend": backend,
            "question_preview": question[:500],
            "status": "api_unavailable",
            "error": err[:300],
            "context_chars": len(context_md),
        }
        write_audit_record(audit_log_path, record)
        return {
            "status": "api_unavailable",
            "backend": backend,
            "error": err,
        }

    # Parse JSON
    try:
        proposal_dict = parse_response_json(text)
    except ValueError as e:
        record = {
            "timestamp_iso": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "backend": backend,
            "question_preview": question[:500],
            "status": "parse_failed",
            "error": str(e)[:300],
            "usage": usage,
            "context_chars": len(context_md),
        }
        write_audit_record(audit_log_path, record)
        return {
            "status": "parse_failed",
            "backend": backend,
            "error": str(e),
            "usage": usage,
        }

    # Validate read-only contract
    violations = validate_read_only(proposal_dict)
    if violations:
        record = {
            "timestamp_iso": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "backend": backend,
            "question_preview": question[:500],
            "status": "read_only_violation",
            "violations_count": len(violations),
            "usage": usage,
            "context_chars": len(context_md),
        }
        write_audit_record(audit_log_path, record)
        return {
            "status": "read_only_violation",
            "backend": backend,
            "violations": violations,
            "usage": usage,
            "error": (
                str(len(violations))
                + " read-only contract violations detected; response rejected"
            ),
        }

    # Success
    record = {
        "timestamp_iso": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "backend": backend,
        "question_preview": question[:500],
        "status": "ok",
        "usage": usage,
        "context_chars": len(context_md),
        "confidence": proposal_dict.get("confidence", "unknown"),
    }
    write_audit_record(audit_log_path, record)
    return {
        "status": "ok",
        "backend": backend,
        "proposal": proposal_dict.get("proposal", ""),
        "rationale": proposal_dict.get("rationale", ""),
        "confidence": proposal_dict.get("confidence", "low"),
        "refs": proposal_dict.get("refs", []),
        "usage": usage,
    }


# ══════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════


def main():
    parser = argparse.ArgumentParser(
        description="Expert Escalation Capability (V37.9.90-r1 — Doubao backend, "
                    "Claude pending) — one-shot Volcengine Ark Chat Completions call "
                    "with structured context + read-only output enforcement."
    )
    parser.add_argument("--question", required=True, help="Question for the expert")
    parser.add_argument("--kb-dir", help="KB directory (default ~/.kb)")
    parser.add_argument("--backend", default=DEFAULT_BACKEND,
                        choices=[BACKEND_DOUBAO, BACKEND_CLAUDE_PENDING],
                        help="Backend (default: doubao; claude_pending returns stub)")
    parser.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS)
    parser.add_argument("--max-daily", type=int, default=DEFAULT_DAILY_QUOTA)
    parser.add_argument("--dry-run", action="store_true",
                        help="Skip API call (dev mode without ARK_API_KEY)")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    result = escalate(
        question=args.question,
        kb_dir=args.kb_dir,
        backend=args.backend,
        max_tokens=args.max_tokens,
        max_daily=args.max_daily,
        dry_run=args.dry_run,
    )

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("Status: " + result["status"])
        if result.get("backend"):
            print("Backend: " + result["backend"])
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
                  + " cache_read=" + str(u.get("cache_read_input_tokens", 0))
                  + " reasoning_chars=" + str(u.get("reasoning_chars", 0)))
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
