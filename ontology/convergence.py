#!/usr/bin/env python3
"""
convergence.py — V37.9.19: Declared-state → Runtime-state convergence framework
                  (Phase 4 Layer 5 — Control Plane core abstraction)

Status (2026-04-26): SHADOW / ALERT_ONLY mode — framework detects drift but
NEVER auto-applies fixes. Promotion to machine_sync is a per-spec V37.9.20+
decision after one-week observation.

Why this exists (MR-17 declared-state-must-converge-to-runtime-via-machine-not-memory):
    Every "declared resource" (jobs in jobs_registry.yaml, providers in
    providers.d/, agents in openclaw.json, sources writing to KB) has a
    corresponding "runtime fact" (crontab -l, /health response,
    OpenClaw runtime state, text_index/ contents). Drift between the two
    is the most dangerous category of bugs: 1478 unit tests + 309
    governance checks all live INSIDE the repo, but the runtime fact
    lives OUTSIDE (cron / process / http / filesystem). Without machine
    sync, "Claude Code remembering to run one command after merge" is
    the only sync mechanism — and memory is the weakest reliability
    primitive.

    Blood lesson: V37.9.18 kb_deep_dive cron unregistered for 2 days
    despite all 1478 tests + 309 checks green at deploy time. See
    ontology/docs/cases/kb_deep_dive_cron_unregistered_case.md.

Design principles (mirrors three_gate.py V37.9.15):
    - Pure functions; no I/O at import.
    - Caller provides spec_id; verify_convergence() does its own bounded I/O
      via subprocess (shell_command method).
    - FAIL-OPEN: any extractor / observer / parser exception → result.error
      is set, drift_detected=False, never raises to caller.
    - Named-extractor / named-observer / named-parser dispatch tables make
      adding new spec types declarative (yaml only) without code changes.
    - drift_action is informational, NOT enforcement. V37.9.19 ships only
      alert_only; machine_sync requires per-spec opt-in via V37.9.20+ with
      one-week observation evidence.
    - Decoupled from ONTOLOGY_MODE / ONTOLOGY_GATES_MODE: convergence is
      governance-layer observability, not request-path enforcement.

Public API:
    load_specs(path=None) -> dict
    list_spec_ids(specs=None, path=None) -> list[str]
    get_spec(spec_id, specs=None, path=None) -> dict | None
    verify_convergence(spec_id, specs=None, path=None) -> ConvergenceResult
    format_result_for_log(result) -> str

Extending (V37.9.20+):
    - New extractor: add to _DECLARED_EXTRACTORS dict
    - New observer: add to _RUNTIME_OBSERVERS dict
    - New parser: add to _IDENTIFIER_PARSERS dict
    - New spec: append to convergence_ontology.yaml::convergence_specs[]
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from collections import namedtuple
from pathlib import Path

# ── Constants ─────────────────────────────────────────────────────────────

_DEFAULT_SPEC_PATH = Path(__file__).resolve().parent / "convergence_ontology.yaml"

# Valid drift_action values. Order = severity (alert_only weakest).
_VALID_DRIFT_ACTIONS = ("alert_only", "machine_sync", "block_until_human")
_DEFAULT_DRIFT_ACTION = "alert_only"

# Subprocess timeout for shell_command method (seconds).
_SHELL_COMMAND_TIMEOUT_SEC = 10

# HTTP observer timeout (seconds). Adapter /health is local, fast — short bound
# avoids spec verification hanging governance audit cron when adapter is wedged.
_HTTP_OBSERVER_TIMEOUT_SEC = 5

# ── Result type ───────────────────────────────────────────────────────────

ConvergenceResult = namedtuple(
    "ConvergenceResult",
    [
        "spec_id",            # str — spec identifier
        "declared",           # frozenset[str] — identifiers declared in source
        "observed",           # frozenset[str] — identifiers found in runtime
        "missing_in_runtime", # frozenset[str] — declared but not observed (drift)
        "drift_detected",     # bool — True iff missing_in_runtime is non-empty
        "drift_action",       # str — from spec (alert_only by default)
        "error",              # str | None — FAIL-OPEN: non-None means partial result
    ],
)


def _empty_result(spec_id, error=None, drift_action=_DEFAULT_DRIFT_ACTION):
    return ConvergenceResult(
        spec_id=spec_id,
        declared=frozenset(),
        observed=frozenset(),
        missing_in_runtime=frozenset(),
        drift_detected=False,
        drift_action=drift_action,
        error=error,
    )


# ── YAML loading (pyyaml optional, fall back to engine helpers if needed) ──

def _load_yaml(path):
    """Minimal YAML loader. Tries pyyaml first; falls back to ontology.engine
    helpers if available (project pattern). Raises on unrecoverable failure."""
    path = str(path)
    try:
        import yaml  # type: ignore
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except ImportError:
        # Project ships a fallback in check_registry.load_yaml; reuse it.
        try:
            import sys
            repo_root = str(Path(__file__).resolve().parent.parent)
            if repo_root not in sys.path:
                sys.path.insert(0, repo_root)
            from check_registry import load_yaml as _legacy_loader  # type: ignore
            return _legacy_loader(path) or {}
        except Exception as e:
            raise RuntimeError(f"yaml load fallback failed: {e}")


def load_specs(path=None):
    """Load convergence_ontology.yaml. Returns dict (may be empty)."""
    path = path or _DEFAULT_SPEC_PATH
    return _load_yaml(path)


def list_spec_ids(specs=None, path=None):
    """Return list of spec ids (in declared order)."""
    if specs is None:
        try:
            specs = load_specs(path)
        except Exception:
            return []
    return [s.get("id", "") for s in (specs.get("convergence_specs") or [])]


def get_spec(spec_id, specs=None, path=None):
    """Look up spec by id. Returns dict or None."""
    if specs is None:
        try:
            specs = load_specs(path)
        except Exception:
            return None
    for s in (specs.get("convergence_specs") or []):
        if s.get("id") == spec_id:
            return s
    return None


# ── Declared-state extractors (named dispatch) ─────────────────────────────
#
# Each extractor: (spec) -> set[str] of identifiers
# Receives the full spec dict so it can read declaration sub-config.

def _extract_registry_enabled_system_jobs(spec):
    """jobs_registry.yaml → set of script basenames where enabled=true and
    scheduler=system. Identifier = entry field (e.g. "kb_deep_dive.sh")."""
    decl = spec.get("declaration", {})
    src = decl.get("source", "jobs_registry.yaml")
    # Resolve relative to repo root (ontology/ is one level deep).
    src_path = Path(__file__).resolve().parent.parent / src
    data = _load_yaml(src_path)
    out = set()
    for job in (data.get("jobs") or []):
        if not job.get("enabled"):
            continue
        if job.get("scheduler") != "system":
            continue
        entry = job.get("entry") or ""
        if entry:
            out.add(entry)
    return out


def _extract_providers_from_registry(spec):
    """providers.py ProviderRegistry.list_names() → set of provider name strings.

    V37.9.20: Captures both built-in providers (qwen/openai/gemini/...) and
    auto-discovered YAML plugins from providers.d/. The registry is the
    single source of truth for "what providers exist according to declaration"
    — built-in registrations run at module load, then PluginLoader.discover()
    scans providers.d/ for additional yaml/python plugins.

    FAIL-OPEN: ImportError or registry construction error → bubble up to
    verify_convergence's extractor_failed branch (caller sees structured error,
    doesn't crash governance audit).
    """
    try:
        # Late import — avoid hard dependency at convergence module import time
        # so dev environments without providers.py can still load the framework.
        import sys
        repo_root = str(Path(__file__).resolve().parent.parent)
        if repo_root not in sys.path:
            sys.path.insert(0, repo_root)
        from providers import get_registry  # type: ignore
    except Exception as e:
        raise RuntimeError(f"providers module not importable: {e}")
    try:
        names = get_registry().list_names()
    except Exception as e:
        raise RuntimeError(f"registry.list_names() failed: {e}")
    return {str(n) for n in (names or []) if n}


_DECLARED_EXTRACTORS = {
    "registry_enabled_system_jobs": _extract_registry_enabled_system_jobs,
    "providers_from_registry": _extract_providers_from_registry,
}


def _extract_declared(spec):
    """Dispatch to the named extractor declared in spec.declaration.extractor."""
    decl = spec.get("declaration", {})
    name = decl.get("extractor", "")
    fn = _DECLARED_EXTRACTORS.get(name)
    if fn is None:
        raise ValueError(f"unknown declared extractor: {name!r}")
    return fn(spec)


# ── Runtime observers (named dispatch) ─────────────────────────────────────
#
# Each observer: (spec) -> str (raw text observed from runtime)
# Subprocess errors (non-zero exit, timeout, missing binary) are caught and
# converted to RuntimeError with diagnostic context for FAIL-OPEN handling.

def _observe_shell_command(spec):
    """Execute spec.runtime_observable.command and return its stdout."""
    obs = spec.get("runtime_observable", {})
    cmd = obs.get("command", "")
    if not cmd:
        raise ValueError("shell_command observer requires runtime_observable.command")
    try:
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=_SHELL_COMMAND_TIMEOUT_SEC,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"shell_command timed out after {_SHELL_COMMAND_TIMEOUT_SEC}s: {cmd}")
    except FileNotFoundError as e:
        raise RuntimeError(f"shell_command binary missing: {e}")
    if result.returncode != 0:
        # crontab -l returns non-zero when no crontab installed; allow empty
        # output to mean "no entries" rather than hard-error. This is
        # intentional FAIL-OPEN: missing crontab → empty observed → drift
        # reported correctly.
        if result.stdout == "" and result.stderr:
            return ""
        raise RuntimeError(
            f"shell_command exit={result.returncode}: stderr={result.stderr[:200]}"
        )
    return result.stdout


def _observe_http_endpoint(spec):
    """Execute spec.runtime_observable.url HTTP GET and return response body.

    V37.9.20: Mirrors _observe_shell_command's contract — return raw stdout/body
    string, raise RuntimeError on any failure for FAIL-OPEN handling. urllib
    (stdlib) avoids external dependencies. Bounded by _HTTP_OBSERVER_TIMEOUT_SEC.

    Spec fields:
        url (required): full URL to GET
        timeout_sec (optional): override default _HTTP_OBSERVER_TIMEOUT_SEC

    Returns: str (UTF-8 decoded response body)
    Raises: RuntimeError on connection error, timeout, non-2xx status, or
            decode failure. ValueError on missing url.
    """
    obs = spec.get("runtime_observable", {})
    url = obs.get("url", "")
    if not url:
        raise ValueError("http_endpoint observer requires runtime_observable.url")
    timeout = obs.get("timeout_sec", _HTTP_OBSERVER_TIMEOUT_SEC)
    try:
        timeout = float(timeout)
    except (TypeError, ValueError):
        timeout = _HTTP_OBSERVER_TIMEOUT_SEC

    # Late stdlib imports — keep top-of-module light.
    from urllib.error import HTTPError, URLError
    from urllib.request import Request, urlopen

    req = Request(url, headers={"User-Agent": "convergence-observer/1.0"})
    try:
        with urlopen(req, timeout=timeout) as resp:
            status = getattr(resp, "status", 200)
            if status < 200 or status >= 300:
                raise RuntimeError(f"http_endpoint status={status}: {url}")
            body = resp.read()
    except HTTPError as e:
        raise RuntimeError(f"http_endpoint http_error={e.code}: {url}")
    except URLError as e:
        raise RuntimeError(f"http_endpoint url_error: {e.reason}")
    except Exception as e:
        # urllib socket.timeout / connection refused etc surface as various types
        raise RuntimeError(f"http_endpoint failed: {type(e).__name__}: {e}")
    try:
        return body.decode("utf-8")
    except UnicodeDecodeError:
        return body.decode("utf-8", errors="replace")


_RUNTIME_OBSERVERS = {
    "shell_command": _observe_shell_command,
    "http_endpoint": _observe_http_endpoint,
}


def _observe_runtime(spec):
    """Dispatch to the named observer declared in spec.runtime_observable.method."""
    obs = spec.get("runtime_observable", {})
    method = obs.get("method", "")
    fn = _RUNTIME_OBSERVERS.get(method)
    if fn is None:
        raise ValueError(f"unknown runtime observer: {method!r}")
    return fn(spec)


# ── Identifier parsers (named dispatch) ────────────────────────────────────
#
# Each parser: (spec, raw_observed, declared_set) -> set[str] of observed
# identifiers (always a subset of declared — extras are out of scope V37.9.19).

def _parse_line_contains_identifier(spec, raw, declared):
    """For each declared identifier, mark it as observed if any non-comment
    line in raw contains it as a substring."""
    if not raw:
        return set()
    active_lines = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        active_lines.append(stripped)
    found = set()
    for ident in declared:
        if not ident:
            continue
        for line in active_lines:
            if ident in line:
                found.add(ident)
                break
    return found


def _parse_line_contains_word_boundary(spec, raw, declared):
    """Stricter variant: identifier must appear as a word-boundary match.
    Avoids false positives when one identifier is a substring of another
    (e.g. 'kb_dream.sh' vs 'kb_dream_helper.sh')."""
    if not raw:
        return set()
    found = set()
    for ident in declared:
        if not ident:
            continue
        # word boundary on each side; escape regex metachars in ident
        pattern = r"(?:^|[\s\"'/])" + re.escape(ident) + r"(?:$|[\s\"'])"
        if re.search(pattern, raw, re.MULTILINE):
            found.add(ident)
    return found


def _parse_json_set_union(spec, raw, declared):
    """Walk spec.runtime_observable.json_paths over JSON body, union into set,
    intersect with declared (V37.9.19 framework convention).

    Path syntax (V37.9.20 minimal — extend in V37.9.21+ if more shapes needed):
        "field"      → top-level scalar (str/int/etc), included if present
        "field[]"    → top-level list, each element included as string

    Example: json_paths=["provider", "fallback", "fallback_chain[]"] over
    /health body {"provider":"qwen", "fallback":"gemini",
    "fallback_chain":["gemini","claude"]} yields union {"qwen","gemini","claude"}.

    FAIL-OPEN philosophy: empty/None values silently skipped; only structural
    errors (unparseable JSON, non-list paths declared as []) raise.
    """
    if not raw:
        return set()
    obs = spec.get("runtime_observable", {})
    paths = obs.get("json_paths") or []
    if not isinstance(paths, list) or not paths:
        raise ValueError("json_set_union parser requires runtime_observable.json_paths (non-empty list)")
    try:
        data = json.loads(raw)
    except (TypeError, ValueError) as e:
        raise ValueError(f"json_set_union parser: invalid JSON body: {e}")
    if not isinstance(data, dict):
        raise ValueError(f"json_set_union parser: top-level JSON must be object, got {type(data).__name__}")

    union = set()
    for path in paths:
        if not isinstance(path, str) or not path:
            continue
        if path.endswith("[]"):
            key = path[:-2]
            val = data.get(key)
            if val is None:
                continue
            if not isinstance(val, list):
                raise ValueError(f"json_set_union parser: path {path!r} expected list, got {type(val).__name__}")
            for elem in val:
                if elem is None:
                    continue
                union.add(str(elem))
        else:
            val = data.get(path)
            if val is None:
                continue
            # Skip non-scalar values silently — list/dict on a scalar path is
            # likely a misconfigured spec, but we don't raise (FAIL-OPEN).
            if isinstance(val, (list, dict)):
                continue
            union.add(str(val))

    # Framework convention: observed ⊆ declared (extras dropped silently)
    return union & set(declared)


_IDENTIFIER_PARSERS = {
    "line_contains_identifier": _parse_line_contains_identifier,
    "line_contains_word_boundary": _parse_line_contains_word_boundary,
    "json_set_union": _parse_json_set_union,
}


def _parse_observed(spec, raw, declared):
    """Dispatch to the named parser declared in spec.runtime_observable.parser."""
    obs = spec.get("runtime_observable", {})
    name = obs.get("parser", "line_contains_identifier")
    fn = _IDENTIFIER_PARSERS.get(name)
    if fn is None:
        raise ValueError(f"unknown identifier parser: {name!r}")
    return fn(spec, raw, declared)


# ── Top-level API ─────────────────────────────────────────────────────────

def verify_convergence(spec_id, specs=None, path=None):
    """Verify a single spec. FAIL-OPEN: any error → result with error field
    set, drift_detected=False (caller distinguishes via result.error).

    Returns:
        ConvergenceResult (always a valid namedtuple, never raises)
    """
    # 1. Load spec
    try:
        spec = get_spec(spec_id, specs=specs, path=path)
    except Exception as e:
        return _empty_result(spec_id, error=f"load_failed: {e}")
    if spec is None:
        return _empty_result(spec_id, error="spec_not_found")

    # Skip disabled specs (treat as no-op pass).
    if spec.get("enabled") is False:
        return _empty_result(spec_id, error="spec_disabled")

    drift_action = spec.get("drift_action", _DEFAULT_DRIFT_ACTION)
    if drift_action not in _VALID_DRIFT_ACTIONS:
        # Unknown drift_action → treat as alert_only (safest), report as error
        return _empty_result(
            spec_id,
            error=f"invalid_drift_action: {drift_action}",
            drift_action=_DEFAULT_DRIFT_ACTION,
        )

    # 2. Extract declared identifiers
    try:
        declared = _extract_declared(spec)
    except Exception as e:
        return _empty_result(
            spec_id, error=f"extractor_failed: {e}", drift_action=drift_action
        )
    declared_fs = frozenset(declared)

    # 3. Observe runtime
    try:
        raw = _observe_runtime(spec)
    except Exception as e:
        return ConvergenceResult(
            spec_id=spec_id,
            declared=declared_fs,
            observed=frozenset(),
            missing_in_runtime=declared_fs,  # nothing observed = all missing
            drift_detected=bool(declared_fs),
            drift_action=drift_action,
            error=f"observer_failed: {e}",
        )

    # 4. Parse observed
    try:
        observed = _parse_observed(spec, raw, declared)
    except Exception as e:
        return ConvergenceResult(
            spec_id=spec_id,
            declared=declared_fs,
            observed=frozenset(),
            missing_in_runtime=declared_fs,
            drift_detected=bool(declared_fs),
            drift_action=drift_action,
            error=f"parser_failed: {e}",
        )
    observed_fs = frozenset(observed)

    # 5. Compute drift
    missing = declared_fs - observed_fs
    return ConvergenceResult(
        spec_id=spec_id,
        declared=declared_fs,
        observed=observed_fs,
        missing_in_runtime=missing,
        drift_detected=bool(missing),
        drift_action=drift_action,
        error=None,
    )


def format_result_for_log(result):
    """One-line human-readable summary suitable for `grep [convergence:]` ops."""
    if result.error:
        return (
            f"[convergence:{result.spec_id}] error={result.error} "
            f"declared={len(result.declared)} observed={len(result.observed)}"
        )
    if not result.drift_detected:
        return (
            f"[convergence:{result.spec_id}] ok "
            f"declared={len(result.declared)} observed={len(result.observed)}"
        )
    missing_preview = ",".join(sorted(result.missing_in_runtime)[:5])
    extra_count = max(0, len(result.missing_in_runtime) - 5)
    suffix = f" (+{extra_count} more)" if extra_count else ""
    return (
        f"[convergence:{result.spec_id}] DRIFT action={result.drift_action} "
        f"missing={len(result.missing_in_runtime)} "
        f"[{missing_preview}{suffix}]"
    )


# ── CLI for ad-hoc inspection ─────────────────────────────────────────────

def _cli():
    import argparse
    import json
    import sys

    ap = argparse.ArgumentParser(description="Convergence framework CLI")
    ap.add_argument("--spec", help="Verify single spec by id")
    ap.add_argument("--all", action="store_true", help="Verify all enabled specs")
    ap.add_argument("--json", action="store_true", help="JSON output")
    ap.add_argument("--path", help="Override default spec yaml path")
    args = ap.parse_args()

    try:
        specs = load_specs(args.path)
    except Exception as e:
        print(f"❌ failed to load specs: {e}", file=sys.stderr)
        sys.exit(2)

    targets = []
    if args.spec:
        targets = [args.spec]
    elif args.all:
        targets = list_spec_ids(specs)
    else:
        ap.print_help()
        sys.exit(1)

    results = [verify_convergence(sid, specs=specs) for sid in targets]
    any_drift = any(r.drift_detected for r in results)
    any_error = any(r.error for r in results)

    if args.json:
        out = [
            {
                "spec_id": r.spec_id,
                "declared": sorted(r.declared),
                "observed": sorted(r.observed),
                "missing_in_runtime": sorted(r.missing_in_runtime),
                "drift_detected": r.drift_detected,
                "drift_action": r.drift_action,
                "error": r.error,
            }
            for r in results
        ]
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        for r in results:
            print(format_result_for_log(r))

    # exit code: 1 if any drift in alert_only mode, 2 if any error, 0 otherwise.
    # alert_only specs do NOT exit non-zero by default — caller decides.
    # This CLI uses exit 1 only when something is genuinely actionable.
    if any_error:
        sys.exit(2)
    if any_drift:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    _cli()
