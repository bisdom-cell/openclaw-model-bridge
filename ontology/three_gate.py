#!/usr/bin/env python3
"""
three_gate.py — Phase 4 P3: three-gate policy enforcement scaffolding (V37.9.15)

Defines the proxy request pipeline as three sequential gates that query
policy_ontology.yaml via engine.evaluate_policy() with per-request context:

    pre_check      : before dispatching to LLM (alert isolation, quiet hours)
    runtime_gate   : between request shaping and backend call
                     (tool count, tool-call count, body size)
    post_verify    : after backend response returned, before fix_tool_args
                     (observability-only: reserved file writes, alert echo)

Status (2026-04-24): SHADOW mode only — gates observe and log findings but
NEVER mutate requests or reject responses. Existing enforcement in
proxy_filters.py (filter_tools / truncate_messages / filter_system_alerts)
remains authoritative. Promotion to "on" is a future V37.9.16+ decision.

Why this exists:
    V37.9.12 (P1) + V37.9.13 (P2) resolved static policy limits at proxy
    startup (one-shot). That's right for static policies but contextual and
    temporal policies (alert-context-isolation / quiet-hours-00-07 /
    multimodal-routing / ...) need PER-REQUEST context. evaluate_policy()
    has six context evaluators registered but nothing in production calls
    it. three_gate is that call site.

Design principles:
    - Pure functions; no I/O, no state, no HTTP.
    - Caller supplies context dict; gates return list[GateFinding].
    - FAIL-OPEN: any evaluate_policy exception → empty list + log, never
      propagates to the caller. Gates must never break a request.
    - Mode gate: ONTOLOGY_GATES_MODE env var (off / shadow / on).
      off → immediate return []. shadow → evaluate + annotate findings
      with enforced=False. on → same findings with enforced=True, but
      since no action code path exists yet, callers should ignore the
      enforced flag. Wiring callers to act on enforced=True is Phase 4 P4.
    - Decoupled from ONTOLOGY_MODE (used by proxy_filters for hardcoded
      replacement): a user can run ONTOLOGY_MODE=on and gates=off while
      observing wiring stability independently.

Gate policy matrix:

    pre_check: contextual + temporal policies that affect INPUT
        - alert-context-isolation    (has [SYSTEM_ALERT] in messages)
        - quiet-hours-00-07          (hour ∈ [0,7))

    runtime_gate: static policies with concrete request-derived signals
        - max-tools-per-agent        (len(request.tools))
        - max-tool-calls-per-task    (count of assistant tool_calls)
        - max-request-body-size      (len(request_body_bytes))

    post_verify: observability-only checks on response
        - alert-context-isolation (detects alert echo in assistant output)
        - [future] reserved-file writes in tool_calls

Test contract: see ontology/tests/test_three_gate.py.
"""

import os
from collections import namedtuple


# ---------------------------------------------------------------------------
# Mode handling
# ---------------------------------------------------------------------------
# Decoupled from ONTOLOGY_MODE so P3 observability can start independently
# of Phase 3 tool-data replacement.

_VALID_GATE_MODES = ("off", "shadow", "on")
_DEFAULT_GATE_MODE = "shadow"  # V37.9.15: start in shadow for observation


def gates_mode():
    """Return current gate mode: 'off' / 'shadow' / 'on'.

    Read fresh each call — allows tests to monkey-patch env var.
    Unknown values fall back to shadow (fail-open to observability).
    """
    m = os.environ.get("ONTOLOGY_GATES_MODE", _DEFAULT_GATE_MODE).lower().strip()
    if m not in _VALID_GATE_MODES:
        return _DEFAULT_GATE_MODE
    return m


# ---------------------------------------------------------------------------
# GateFinding dataclass (namedtuple for easy pickling/comparison in tests)
# ---------------------------------------------------------------------------

GateFinding = namedtuple(
    "GateFinding",
    [
        "gate",         # "pre_check" | "runtime_gate" | "post_verify"
        "policy_id",    # e.g. "max-tools-per-agent"
        "verdict",      # "pass" | "flag" | "block" (flag = would act if on)
        "action",       # human-readable intent: "truncate_tools" / "filter_alerts" / ...
        "reason",       # why this verdict was reached
        "enforced",     # bool: gate mode == "on" (informational; no enforcement yet)
    ],
)


# ---------------------------------------------------------------------------
# Gate → policy list mapping
# ---------------------------------------------------------------------------
# Each tuple: (policy_id, action_if_applicable).
# "action" is an advisory label — NOT a function pointer. Upstream code
# decides how to react (today: shadow log only).

_PRE_CHECK_POLICIES = (
    ("alert-context-isolation", "filter_alerts_before_truncate"),
    ("quiet-hours-00-07", "suppress_non_critical_whatsapp"),
)

_RUNTIME_GATE_POLICIES = (
    ("max-tools-per-agent", "truncate_tools"),
    ("max-tool-calls-per-task", "cap_tool_call_count"),
    ("max-request-body-size", "truncate_messages"),
)

_POST_VERIFY_POLICIES = (
    ("alert-context-isolation", "detect_alert_echo_in_response"),
)


# ---------------------------------------------------------------------------
# Engine import (lazy + safe)
# ---------------------------------------------------------------------------
# V37.9.15.1 HOTFIX: tool_proxy.py 通过 spec_from_file_location("_three_gate",
# ontology/three_gate.py) 加载本模块时，__package__ 为空 + sys.path 不含
# ontology/，导致原有两条 import 路径 (package-relative / absolute) 都失败，
# 所有 findings 降级为 engine_unavailable。Production log 2026-04-24 确认:
#   [gate:runtime_gate] 3 findings: max-tools-per-agent=pass(engine_unavailable) ...
# 三 gate 全部 FAIL-OPEN 等于 shadow 模式完全没评估 policy。
# 修复: 加第三条 import 路径用 __file__ 定位同目录 engine.py，不依赖调用方
# 环境。模块级缓存 _ENGINE_MOD 避免重复 exec_module 开销。

_ENGINE_MOD = None  # V37.9.15.1: 模块级缓存，首次成功 import 后复用


def _load_engine_module():
    """Attempt to load the ontology engine module via three strategies.

    Strategy order (fail-through):
      1. package-relative `from . import engine` — works when three_gate is
         imported as `ontology.three_gate`
      2. absolute `import engine` — works when ontology/ is on sys.path
      3. V37.9.15.1 HOTFIX: spec_from_file_location pointing at the engine.py
         sibling of this module's __file__ — always works as long as the
         ontology/ directory is intact (the delivery guarantee from CLAUDE.md
         ontology subproject charter).

    Returns the engine module on success, None on all-paths failure.
    Caches result at module level so subsequent calls skip import cost.
    """
    global _ENGINE_MOD
    if _ENGINE_MOD is not None:
        return _ENGINE_MOD

    # Path 1: package-relative
    try:
        from . import engine as _e1
        _ENGINE_MOD = _e1
        return _e1
    except (ImportError, ValueError):
        pass

    # Path 2: absolute (sys.path has ontology/)
    try:
        import engine as _e2  # type: ignore
        _ENGINE_MOD = _e2
        return _e2
    except ImportError:
        pass

    # Path 3: __file__-adjacent spec_from_file_location (V37.9.15.1 hotfix)
    try:
        import importlib.util as _imp_util
        import os as _os
        _engine_path = _os.path.join(
            _os.path.dirname(_os.path.abspath(__file__)), "engine.py")
        if not _os.path.exists(_engine_path):
            return None
        _spec = _imp_util.spec_from_file_location(
            "_three_gate_engine_lazy", _engine_path)
        _mod = _imp_util.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)
        _ENGINE_MOD = _mod
        return _mod
    except Exception:
        return None


def _safe_evaluate_policy(policy_id, context):
    """Call engine.evaluate_policy with full exception isolation.

    Returns dict result on success, None if engine unavailable or raised.
    Log emission is caller's responsibility (three_gate.py never prints).
    """
    engine = _load_engine_module()
    if engine is None:
        return None
    try:
        return engine.evaluate_policy(policy_id, context=context)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Gate implementations (pure functions)
# ---------------------------------------------------------------------------

def _collect_findings(gate_name, policy_pairs, context, mode):
    """Shared policy-loop helper. Returns list[GateFinding]."""
    findings = []
    enforced = (mode == "on")
    for policy_id, action in policy_pairs:
        result = _safe_evaluate_policy(policy_id, context)
        if result is None:
            findings.append(GateFinding(
                gate=gate_name,
                policy_id=policy_id,
                verdict="pass",
                action=action,
                reason="engine_unavailable",
                enforced=False,
            ))
            continue
        if not result.get("found"):
            # unknown policy id — log once, not a blocker
            findings.append(GateFinding(
                gate=gate_name,
                policy_id=policy_id,
                verdict="pass",
                action=action,
                reason=f"policy_not_found: {result.get('reason', 'unknown')}",
                enforced=False,
            ))
            continue
        applicable = result.get("applicable")
        reason = result.get("reason") or ""
        if applicable is True:
            # Hard limit (static with numeric threshold) → additional context check
            limit = result.get("limit")
            signal = _extract_signal_for_limit(policy_id, context)
            if limit is not None and signal is not None and signal <= limit:
                # applicable=True because static, but signal under limit → no concern
                findings.append(GateFinding(
                    gate=gate_name,
                    policy_id=policy_id,
                    verdict="pass",
                    action=action,
                    reason=f"signal={signal} <= limit={limit}",
                    enforced=False,
                ))
            elif limit is not None and signal is not None and signal > limit:
                findings.append(GateFinding(
                    gate=gate_name,
                    policy_id=policy_id,
                    verdict="flag",
                    action=action,
                    reason=f"signal={signal} > limit={limit}",
                    enforced=enforced,
                ))
            else:
                # Contextual/temporal true, or static without signal data
                findings.append(GateFinding(
                    gate=gate_name,
                    policy_id=policy_id,
                    verdict="flag",
                    action=action,
                    reason=reason or "applicable=True",
                    enforced=enforced,
                ))
        elif applicable is False:
            findings.append(GateFinding(
                gate=gate_name,
                policy_id=policy_id,
                verdict="pass",
                action=action,
                reason=reason or "applicable=False",
                enforced=False,
            ))
        else:
            # applicable=None → undecidable with current context (missing fields)
            findings.append(GateFinding(
                gate=gate_name,
                policy_id=policy_id,
                verdict="pass",
                action=action,
                reason=reason or "context_incomplete",
                enforced=False,
            ))
    return findings


# Map static policies → context key holding the measured signal.
# When a signal field is present in context, we compare against policy limit
# to produce a richer verdict (pass under limit, flag over limit). Without
# a signal we still honor the policy's applicable verdict.
_STATIC_SIGNAL_KEYS = {
    "max-tools-per-agent": "tool_count",
    "max-tool-calls-per-task": "tool_call_count",
    "max-request-body-size": "body_bytes",
}


def _extract_signal_for_limit(policy_id, context):
    """Pull the numeric signal from context for a static policy, if present."""
    if context is None:
        return None
    key = _STATIC_SIGNAL_KEYS.get(policy_id)
    if key is None:
        return None
    val = context.get(key)
    if isinstance(val, bool):  # bool is int — exclude by intent
        return None
    if isinstance(val, (int, float)):
        return val
    return None


def pre_check(context):
    """Pre-LLM gate: alert isolation + temporal windowing.

    Context keys consumed (all optional):
        messages (list)         — LLM conversation; has_alert evaluator scans for [SYSTEM_ALERT]
        hour (int 0-23)         — current hour in local TZ
        now (datetime)          — alternative to hour (evaluator reads .hour)
        has_image (bool)        — short-circuit for multimodal

    Returns:
        list[GateFinding]. Empty list when mode=off or engine unavailable.
    """
    mode = gates_mode()
    if mode == "off":
        return []
    if context is None:
        context = {}
    return _collect_findings("pre_check", _PRE_CHECK_POLICIES, context, mode)


def runtime_gate(context):
    """Mid-pipeline gate: static resource limits.

    Context keys consumed (all optional):
        tool_count (int)        — len(request.tools) after filter_tools
        tool_call_count (int)   — count of assistant tool_calls seen
        body_bytes (int)        — request body size after truncate_messages

    Returns:
        list[GateFinding]. Findings with verdict="flag" indicate the signal
        exceeded policy limit — caller MAY act (today: log only).
    """
    mode = gates_mode()
    if mode == "off":
        return []
    if context is None:
        context = {}
    return _collect_findings("runtime_gate", _RUNTIME_GATE_POLICIES, context, mode)


def post_verify(context, response=None):
    """Post-response gate: observability-only.

    Context keys consumed (all optional):
        messages (list)         — for alert isolation context chain

    Args:
        context: request context dict (mirrors pre_check/runtime_gate)
        response: backend response dict (optional; reserved for future
                  reserved-file-write detection by scanning tool_calls).

    Returns:
        list[GateFinding]. All findings are advisory (enforced=False).
    """
    mode = gates_mode()
    if mode == "off":
        return []
    if context is None:
        context = {}
    # Build a shadow-context that includes response-derived signals
    ctx_with_response = dict(context)
    if response is not None:
        ctx_with_response["response"] = response
        # Surface alert-echo detection: scan assistant output for marker
        assistant_text = _extract_assistant_text(response)
        if assistant_text and "[SYSTEM_ALERT]" in assistant_text:
            ctx_with_response["messages"] = list(ctx_with_response.get("messages", [])) + [
                {"role": "assistant", "content": assistant_text}
            ]
    return _collect_findings("post_verify", _POST_VERIFY_POLICIES,
                             ctx_with_response, mode)


def _extract_assistant_text(response):
    """Pull assistant message text from a chat-completions response dict.

    Returns "" if the shape is unexpected. Never raises.
    """
    if not isinstance(response, dict):
        return ""
    try:
        choices = response.get("choices") or []
        if not choices:
            return ""
        first = choices[0]
        if not isinstance(first, dict):
            return ""
        msg = first.get("message") or {}
        content = msg.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, dict):
                    t = block.get("text")
                    if isinstance(t, str):
                        parts.append(t)
            return " ".join(parts)
    except Exception:
        return ""
    return ""


# ---------------------------------------------------------------------------
# Logging helper for tool_proxy.py wiring (caller-provided log fn)
# ---------------------------------------------------------------------------

def format_findings_for_log(findings):
    """Compact one-line summary for proxy log stream.

    Returns "" if findings is empty (caller should skip logging). Otherwise
    a string like "[gate:pre_check] 2 findings: quiet-hours-00-07=flag(hour=3)
    alert-context-isolation=pass(applicable=False)".
    """
    if not findings:
        return ""
    gate = findings[0].gate
    parts = []
    for f in findings:
        parts.append(f"{f.policy_id}={f.verdict}({f.reason})")
    return f"[gate:{gate}] {len(findings)} findings: " + " ".join(parts)
