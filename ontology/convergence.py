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

# V37.9.23 — machine_sync subprocess timeout (per crontab_safe.sh add invocation).
# Should be short — crontab_safe.sh add reads + writes crontab + count verify,
# all local and fast. 15s ceiling avoids audit cron hang on weird subprocess state.
_MACHINE_SYNC_TIMEOUT_SEC = 15

# V37.9.24 — kb_embed.py incremental subprocess timeout. kb_embed reads KB
# notes/sources, computes mtime diff, only re-embeds changed files. LLM
# embedding calls dominate latency for changed files. 5min ceiling covers
# typical incremental run; full re-index is rare and not on this code path.
_KB_EMBED_TIMEOUT_SEC = 300

# V37.9.23 — Default dry-run for machine_sync. Read from env var so audit
# V37.9.58 切关 escalation 兑现 (2026-05-12): 一周观察期到期 (5/3-5/11 零漂移),
# V37.9.23/24 yaml meta 收工承诺 "V37.9.24+ (一周后): 切关 CONVERGENCE_DRY_RUN
# 默认 → 真激活 machine_sync (default=不要 dry-run, 必须 CONVERGENCE_DRY_RUN=1
# 才 dry-run)" 兑现. typo-safe direction 反转: 旧 V37.9.23 typo→dry-run (保守);
# 新 V37.9.58 typo→real apply (兑现 escalation 承诺).
# Only literal "1" enables dry-run — all other values (unset, "0", "true",
# anything) trigger real machine_sync.
_DRY_RUN_ENV_VAR = "CONVERGENCE_DRY_RUN"


def _is_dry_run():
    """Read CONVERGENCE_DRY_RUN env var. V37.9.58 切关后默认 False (real apply).
    Only the literal "1" enables dry-run — all other values (unset, "0",
    "true", anything) trigger real machine_sync. Operator must explicitly set
    CONVERGENCE_DRY_RUN=1 to flip back to dry-run for observation/debugging.
    V37.9.23/24 yaml meta 一周观察期到期 → 切关 dry-run, MR-17 (declared-state-
    must-converge-to-runtime-via-machine-not-memory) 真正兑现 — 从"机器可检测"
    升级到"机器可同步"且"默认同步".
    """
    return os.environ.get(_DRY_RUN_ENV_VAR, "0") == "1"

# ── Result type ───────────────────────────────────────────────────────────

ConvergenceResult = namedtuple(
    "ConvergenceResult",
    [
        "spec_id",            # str — spec identifier
        "declared",           # frozenset[str] — identifiers declared in source
        "observed",           # frozenset[str] — identifiers found in runtime
        "missing_in_runtime", # frozenset[str] — declared but not observed (drift)
        "drift_detected",     # bool — True iff missing_in_runtime OR extra_in_runtime non-empty (V37.9.66)
        "drift_action",       # str — from spec (alert_only by default)
        "error",              # str | None — FAIL-OPEN: non-None means partial result
        # V37.9.23 — machine_sync apply tracking (defaults preserve V37.9.22 contract):
        "applied_actions",    # tuple[str] — what was applied (or "would apply" if dry-run)
        "apply_dry_run",      # bool — True iff machine_sync ran in dry-run mode
        "apply_errors",       # tuple[str] — per-missing-entry apply failures (machine_sync only)
        # V37.9.66 — extra_in_runtime for bidirectional sync (defaults preserve V37.9.23 contract):
        "extra_in_runtime",   # frozenset[str] — observed but not declared (V37.9.66 双向 sync, 默认 frozenset())
    ],
    # defaults align to last N fields — V37.9.23 added 3, V37.9.66 added 1 (4 total)
    defaults=((), True, (), frozenset()),
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
        applied_actions=(),
        apply_dry_run=True,
        apply_errors=(),
        extra_in_runtime=frozenset(),  # V37.9.66
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


def _walk_json_paths_to_set(data, paths):
    """Walk V37.9.20 path syntax over JSON dict, return union as set[str].

    Path syntax (kept identical to _parse_json_set_union for cross-side parity):
        "field"   → top-level scalar value coerced to str (skip dict/list silently)
        "field[]" → top-level list, each element coerced to str (skip None)

    Shared by _extract_json_file_paths (V37.9.22 declared side, no downstream
    intersection) and _parse_json_set_union (observed side, intersected upstream).
    MR-8 兑现: single source of truth for path traversal logic — V37.9.22+
    syntax extensions land in one place. Pure function, no I/O, no side effects.

    Raises ValueError on structural misconfig (path declared as [] but value
    is not a list) — caller decides how to surface.
    """
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
                raise ValueError(f"path {path!r} expected list, got {type(val).__name__}")
            for elem in val:
                if elem is None:
                    continue
                union.add(str(elem))
        else:
            val = data.get(path)
            if val is None:
                continue
            # Skip non-scalar values silently — list/dict on scalar path is
            # likely misconfigured spec but we don't raise (FAIL-OPEN).
            if isinstance(val, (list, dict)):
                continue
            union.add(str(val))
    return union


def _extract_json_file_paths(spec):
    """Read JSON file at declaration.source, walk declaration.json_paths to set[str].

    V37.9.22: General-purpose declared-state extractor for any JSON config file.
    First use case: openclaw_config_to_runtime spec — reads ~/.openclaw/openclaw.json
    declared agents/channels/providers and compares against runtime endpoint.

    Path syntax: same as _parse_json_set_union (shared helper _walk_json_paths_to_set).

    File resolution:
        - Absolute path: used as-is
        - Relative path: resolved against repo root (parent of ontology/)
        - Supports ~, $HOME, $VAR via os.path.expanduser + expandvars

    FAIL-OPEN philosophy:
        - File missing → return set() (dev environments without OpenClaw runtime;
          declared=set() yields drift_detected=False, governance audit doesn't
          spuriously alert on environments where the file legitimately doesn't
          exist; Mac Mini admin is expected to notice if openclaw.json deleted
          since that breaks Gateway entirely — a higher-priority alert).
        - File unreadable / invalid JSON → RuntimeError (caller turns into
          extractor_failed for ops visibility — distinct from "file not present").
        - Top-level JSON not object → ValueError (structural misconfig).
        - Path-traversal structural error → ValueError via helper.

    Spec fields:
        declaration.source (required): file path (abs / rel / with env vars)
        declaration.json_paths (required, list): paths to walk (V37.9.20 syntax)
    """
    decl = spec.get("declaration", {})
    src = decl.get("source", "")
    paths = decl.get("json_paths") or []
    if not src:
        raise ValueError("json_file_paths extractor requires declaration.source")
    if not isinstance(paths, list) or not paths:
        raise ValueError("json_file_paths extractor requires declaration.json_paths (non-empty list)")

    # Resolve file path: ~ / $VAR expansion + relative-to-repo-root fallback
    expanded = os.path.expanduser(os.path.expandvars(src))
    p = Path(expanded)
    if not p.is_absolute():
        p = Path(__file__).resolve().parent.parent / expanded

    # FAIL-OPEN on missing file (dev environments)
    if not p.exists():
        return set()

    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
    except OSError as e:
        raise RuntimeError(f"json_file_paths: failed to read {p}: {e}")
    except ValueError as e:
        raise RuntimeError(f"json_file_paths: invalid JSON in {p}: {e}")

    if not isinstance(data, dict):
        raise ValueError(
            f"json_file_paths: top-level JSON must be object, got {type(data).__name__} in {p}"
        )

    return _walk_json_paths_to_set(data, paths)


def _extract_registry_kb_source_files(spec):
    """jobs_registry.yaml → set of kb_source_file basenames where enabled=true.

    V37.9.22 fourth-spec sibling extractor — registry-specific, mirrors
    V37.9.19 _extract_registry_enabled_system_jobs pattern (different field).
    Each enabled job declaring kb_source_file means that file basename should
    appear at least once in ~/.kb/text_index/meta.json's chunks[].source_file
    list (i.e. kb_embed.py successfully indexed the source).

    Note: does NOT filter scheduler=system — KB sources can come from either
    system crontab jobs (most current cases) or openclaw cron jobs in future,
    both should be indexed regardless of scheduling lane.

    Sibling to V37.9.5 INV-KB-COVERAGE-001 (which guards kb_embed.py source-
    code logic to *attempt* indexing); this extractor backs V37.9.22
    INV-CONVERGENCE-KB-001 which validates *successful* indexing at runtime.
    """
    decl = spec.get("declaration", {})
    src = decl.get("source", "jobs_registry.yaml")
    src_path = Path(__file__).resolve().parent.parent / src
    data = _load_yaml(src_path)
    out = set()
    for job in (data.get("jobs") or []):
        if not job.get("enabled"):
            continue
        kb_file = job.get("kb_source_file") or ""
        if kb_file:
            out.add(kb_file)
    return out


def _extract_services_from_registry(spec):
    """services_registry.yaml → set of launchd label strings.

    V37.9.25 fifth-spec extractor — declares services that MUST be active in
    launchd (loaded by launchctl + supervised). Each entry's `label` field
    becomes the identifier compared against `launchctl list` runtime output.

    Schema (services_registry.yaml):
        services:
          - id: adapter           # short id (informational, framework uses label)
            label: com.openclaw.adapter   # required, identifier for set-diff
            port: 5001            # optional informational
            plist: com.openclaw.adapter.plist  # optional informational
            description: ...      # required for ops visibility

    Spec.declaration.source defaults to "services_registry.yaml" if missing.
    Resolved relative to repo root (ontology/ is one level deep).

    Returns: set[str] of label strings (e.g. {"com.openclaw.adapter",
    "com.openclaw.proxy", "ai.openclaw.gateway"}).

    Filtering: no filter — all entries' labels go into the set. If you want
    to disable a declared service from convergence checking, remove the entry
    (or in future schema add `enabled: false` field).
    """
    decl = spec.get("declaration", {})
    src = decl.get("source", "services_registry.yaml")
    src_path = Path(__file__).resolve().parent.parent / src
    data = _load_yaml(src_path)
    out = set()
    for svc in (data.get("services") or []):
        label = svc.get("label") or ""
        if label:
            out.add(label)
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


# V37.9.66: 新 extractor — 输出每个 enabled+system job 的完整 cron line (用 _format_cron_line).
# 用途: 配合 cron_lines_set_diff parser 实现完整 cron 行精确匹配 (V37.9.65 line_contains_identifier 升级版),
# 让 framework 检测 interval/log 字段漂移 (当前 line_contains_identifier 只检 entry 包含, 漏掉 interval 改动).
# V37.9.66 实施: extractor 已注册可用, 但 jobs_to_crontab spec yaml 暂不切换 (避免 34 job 路径一致性 audit 风暴,
# V37.9.67+ 候选). _format_cron_line V37.9.66 已修 jobs/ entry .openclaw/ 前缀确保拼出 path 与 runtime 一致.
def _extract_jobs_to_full_cron_lines(spec):
    """V37.9.66 extractor: yield full cron lines for each enabled+system job.

    Returns: set[str] of full cron lines (each formatted by _format_cron_line).
    Skips jobs that fail _format_cron_line validation (malformed registry entry).
    Skips jobs with scheduler != "system" or enabled != True.

    FAIL-OPEN: registry load failure → raise RuntimeError, framework 转 extractor_failed.
    Individual malformed jobs are silently skipped (defensive — single bad job not
    halt extraction of others).
    """
    decl = spec.get("declaration", {})
    src = decl.get("source", "jobs_registry.yaml")
    src_path = Path(__file__).resolve().parent.parent / src
    try:
        data = _load_yaml(src_path)
    except Exception as e:
        raise RuntimeError(f"registry load failed for cron-line extractor: {e}")

    lines = set()
    for job in data.get("jobs", []) or []:
        if not job.get("enabled"):
            continue
        if job.get("scheduler") != "system":
            continue
        try:
            line = _format_cron_line(job)
            lines.add(line)
        except ValueError:
            # malformed job (missing fields / bad metachar) — skip silently,
            # other jobs continue. INV-CRON-003 守卫单独校验 job format.
            continue
    return lines


_DECLARED_EXTRACTORS = {
    "registry_enabled_system_jobs": _extract_registry_enabled_system_jobs,
    "registry_kb_source_files": _extract_registry_kb_source_files,
    "providers_from_registry": _extract_providers_from_registry,
    "jobs_to_full_cron_lines": _extract_jobs_to_full_cron_lines,  # V37.9.66
    "json_file_paths": _extract_json_file_paths,
    "services_from_registry": _extract_services_from_registry,  # V37.9.25 — fifth spec
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

    # V37.9.22: shared helper with _extract_json_file_paths (MR-8 单一真理源)
    try:
        union = _walk_json_paths_to_set(data, paths)
    except ValueError as e:
        raise ValueError(f"json_set_union parser: {e}")

    # Framework convention: observed ⊆ declared (extras dropped silently)
    return union & set(declared)


# V37.9.66: 新 parser — 输出 raw 中所有非注释/非空行作为 set, 不与 declared 求交.
# 用途: 配合 jobs_to_full_cron_lines extractor 实现完整 cron 行精确匹配 (declared 是完整 cron lines,
# observed 是 raw cron 行 set, missing = declared - observed, extra = observed - declared).
# 关键差异 vs line_contains_identifier: 不丢 observed extras (V37.9.65 line_contains_identifier
# 是 framework "observed ⊆ declared" 单向 sync, 漏检 runtime 多余行).
# 与 ConvergenceResult.extra_in_runtime 字段 (V37.9.66 新增) 配套使用.
def _parse_cron_lines_set_diff(spec, raw, declared):
    """V37.9.66 parser: return all non-comment/non-empty raw lines as set.

    Does NOT intersect with declared (V37.9.66 deliberate — 让 verify_convergence
    顶层计算 missing = declared - observed AND extra = observed - declared 实现双向 sync).
    """
    if not raw:
        return set()
    observed = set()
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        observed.add(stripped)
    return observed


_IDENTIFIER_PARSERS = {
    "line_contains_identifier": _parse_line_contains_identifier,
    "line_contains_word_boundary": _parse_line_contains_word_boundary,
    "json_set_union": _parse_json_set_union,
    "cron_lines_set_diff": _parse_cron_lines_set_diff,  # V37.9.66
}


def _parse_observed(spec, raw, declared):
    """Dispatch to the named parser declared in spec.runtime_observable.parser."""
    obs = spec.get("runtime_observable", {})
    name = obs.get("parser", "line_contains_identifier")
    fn = _IDENTIFIER_PARSERS.get(name)
    if fn is None:
        raise ValueError(f"unknown identifier parser: {name!r}")
    return fn(spec, raw, declared)


# ── V37.9.23 — machine_sync apply path (jobs_to_crontab first user) ───────
#
# Plan B (gradual escalation): drift_action=machine_sync triggers apply, but
# default mode is dry-run via CONVERGENCE_DRY_RUN env. This separates the
# "wiring exists + cron line construction is correct" verification (V37.9.23)
# from the "real crontab modifications happen automatically" activation
# (V37.9.24+ after one-week dry-run observation window).
#
# Why a separate _format_cron_line function:
#   - testable in isolation (no subprocess required)
#   - rejects malformed inputs early (5-field interval, non-empty entry/log)
#   - centralizes cron-line conventions (`bash -lc 'bash ~/X >> Y 2>&1'`)
#     so future changes (e.g. add `set -e` wrapper) need only one edit
#
# Why crontab_safe.sh add (not direct crontab manipulation):
#   - crontab_safe.sh has V37.9.18 hard-fail + V30 backup-and-restore safety
#   - already idempotent: `grep -qF` skip if line exists (line 64 of script)
#   - exit 1 on cron rejection (V37.9.18 — kb_deep_dive blood lesson fix)
#   - 30-day rolling backups protect against framework bugs corrupting crontab

# Cron line template (matches V37.9.18 INV-CRON-003 _cron_cmd_invokes pattern):
#   <interval> bash -lc 'bash ~/<entry> >> <log> 2>&1'
# Where:
#   <interval> is 5-field cron expression (e.g. "30 22 * * *")
#   <entry>    is jobs_registry entry field, relative to $HOME (e.g. "kb_deep_dive.sh"
#              or "jobs/arxiv_monitor/run_arxiv.sh")
#   <log>      is jobs_registry log field, already starts with "~/" or absolute
#              path (e.g. "~/health_check.log" or "~/.openclaw/logs/jobs/X.log")


def _format_cron_line(job):
    """Format a single jobs_registry job dict → cron line string.

    V37.9.23 — paired with _apply_machine_sync for jobs_to_crontab spec.

    Args:
        job: dict with keys interval (5-field cron), entry (script path
             relative to $HOME), log (log path with ~ or absolute).
             Optional: id (for error context only — not used in line).

    Returns:
        Cron line string matching V37.9.18 INV-CRON-003 _cron_cmd_invokes
        pattern (`bash -lc 'bash ~/X >> Y 2>&1'`).

    Raises:
        ValueError: missing/empty required fields, malformed interval (not
                    5 fields), suspicious shell metacharacters in entry/log
                    that could break the inner single-quoted command.
    """
    if not isinstance(job, dict):
        raise ValueError(f"_format_cron_line: job must be dict, got {type(job).__name__}")

    interval = job.get("interval", "")
    entry = job.get("entry", "")
    log = job.get("log", "")

    if not interval or not isinstance(interval, str):
        raise ValueError(f"_format_cron_line: missing/non-string 'interval' in job {job.get('id', '?')!r}")
    if not entry or not isinstance(entry, str):
        raise ValueError(f"_format_cron_line: missing/non-string 'entry' in job {job.get('id', '?')!r}")
    if not log or not isinstance(log, str):
        raise ValueError(f"_format_cron_line: missing/non-string 'log' in job {job.get('id', '?')!r}")

    # Validate interval is 5-field cron expression. cron also accepts @reboot
    # / @daily / etc shortcuts but jobs_registry currently uses 5-field only;
    # if @-shortcuts ever appear we'll extend here.
    fields = interval.strip().split()
    if len(fields) != 5:
        raise ValueError(
            f"_format_cron_line: interval must be 5-field cron expression "
            f"(got {len(fields)} fields: {interval!r}) in job {job.get('id', '?')!r}"
        )

    # Reject shell metacharacters that could escape the inner single-quoted
    # command. crontab_safe.sh wraps the line with bash -lc '...', and
    # entry/log are placed inside that single-quote context. A literal `'`
    # in entry/log would close the quote prematurely. Other metachars are
    # allowed (paths can contain spaces, but `;`, `\``, `$(` would also be
    # unsafe — defense-in-depth even though they shouldn't occur in real
    # registry data).
    for fname, fval in (("entry", entry), ("log", log)):
        if "'" in fval:
            raise ValueError(
                f"_format_cron_line: {fname!r} contains single quote, would break "
                f"inner shell quoting: {fval!r} in job {job.get('id', '?')!r}"
            )
        # Reject backtick / $() / ; / & / | which could allow command injection.
        # These are not expected in any legitimate registry value.
        for bad in ("`", "$(", ";", "&", "|"):
            if bad in fval:
                raise ValueError(
                    f"_format_cron_line: {fname!r} contains shell metachar {bad!r}, "
                    f"refusing for safety: {fval!r} in job {job.get('id', '?')!r}"
                )

    # entry is relative to $HOME (registry convention); log already starts
    # with ~/ or absolute path. Don't double-prefix log.
    if log.startswith("~") or log.startswith("/"):
        log_resolved = log
    else:
        # Defensive: registry convention requires ~/ prefix for log, but
        # accept bare path by adding ~/ for compatibility.
        log_resolved = "~/" + log

    # V37.9.66: jobs/ 开头的 entry 部署到 ~/.openclaw/{entry} (auto_deploy FILE_MAP 约定),
    # 其他 entry (老 V27 系统脚本如 health_check.sh) 部署到 ~/{entry} 直接保留.
    # 之前 _format_cron_line 一律拼 ~/{entry}, framework 未来真激活 add 时会拼错路径
    # (~/jobs/... 不存在文件, 真实路径是 ~/.openclaw/jobs/...). 潜伏 bug, 当前因
    # missing=0 没触发. V37.9.66 修复确保未来真激活时拼路径与 Mac Mini runtime 一致.
    if entry.startswith("jobs/"):
        entry_runtime = ".openclaw/" + entry
    else:
        entry_runtime = entry

    # Match V37.9.18 INV-CRON-003 pattern: bash -lc 'bash ~/{entry} >> {log} 2>&1'
    return f"{interval} bash -lc 'bash ~/{entry_runtime} >> {log_resolved} 2>&1'"


def _load_jobs_registry_index(spec):
    """Load jobs_registry.yaml and return id→job dict for O(1) lookup.

    Used by _apply_machine_sync to find interval/log/entry for each missing
    identifier (declared by registry_enabled_system_jobs extractor as 'entry'
    field — we need to find the corresponding job dict to access interval/log).
    """
    decl = spec.get("declaration", {})
    src = decl.get("source", "jobs_registry.yaml")
    src_path = Path(__file__).resolve().parent.parent / src
    data = _load_yaml(src_path)
    by_entry = {}
    for job in (data.get("jobs") or []):
        if not job.get("enabled"):
            continue
        if job.get("scheduler") != "system":
            continue
        entry = job.get("entry") or ""
        if entry:
            by_entry[entry] = job
    return by_entry


def _apply_jobs_to_crontab_per_entry(spec, missing_entries, dry_run, extra_entries=frozenset()):
    """V37.9.23 jobs_to_crontab apply path — per-entry crontab_safe.sh add.
    V37.9.66: 加 extra_entries 参数支持双向 sync (调 crontab_safe.sh remove).

    For each missing entry, look up the corresponding job in jobs_registry,
    format a cron line via _format_cron_line, and (in real mode) invoke
    crontab_safe.sh add. dry-run mode emits "DRY-RUN would apply: <line>"
    instead of executing subprocess.

    V37.9.66: For each extra_entry (full cron line in runtime but not declared),
    invoke crontab_safe.sh remove with the cron line as pattern (grep -F 固定字符串).
    dry-run mode emits "DRY-RUN would remove: <line>".

    FAIL-OPEN: per-entry errors become individual entries in apply_errors.
    Other entries continue. extra_entries 默认 frozenset() — 向后兼容 V37.9.23 调用.
    """
    try:
        by_entry = _load_jobs_registry_index(spec)
    except Exception as e:
        return (), (f"registry_load_failed: {e}",), dry_run

    applied = []
    errors = []

    # V37.9.23 — Add missing entries (declared 中有 runtime 中缺)
    for entry in sorted(missing_entries):
        # V37.9.66: 若 missing_entries 是完整 cron lines (cron_lines_set_diff parser 输出),
        # 直接用 entry 作 cron line; 否则 (line_contains_identifier 旧 parser) 查 registry.
        if entry.startswith(("@", "0", "1", "2", "3", "4", "5", "*")) and " " in entry:
            # 看起来是完整 cron line (以时间字段开头 + 含空格), V37.9.66 cron_lines_set_diff 路径
            cron_line = entry
        else:
            # entry 是 identifier (registry entry field), V37.9.65 line_contains_identifier 路径
            job = by_entry.get(entry)
            if job is None:
                errors.append(f"{entry}: not in current registry (stale identifier?)")
                continue
            try:
                cron_line = _format_cron_line(job)
            except ValueError as e:
                errors.append(f"{entry}: format_cron_line failed: {e}")
                continue

        if dry_run:
            applied.append(f"DRY-RUN would apply: {cron_line}")
            continue

        helper = os.path.expanduser("~/crontab_safe.sh")
        if not os.path.exists(helper):
            errors.append(f"{entry}: crontab_safe.sh not found at {helper}")
            continue

        try:
            proc = subprocess.run(
                ["bash", helper, "add", cron_line],
                capture_output=True, text=True,
                timeout=_MACHINE_SYNC_TIMEOUT_SEC,
            )
        except subprocess.TimeoutExpired:
            errors.append(f"{entry}: crontab_safe.sh add timed out after {_MACHINE_SYNC_TIMEOUT_SEC}s")
            continue
        except FileNotFoundError as e:
            errors.append(f"{entry}: bash binary missing: {e}")
            continue
        except Exception as e:
            errors.append(f"{entry}: subprocess unexpected error: {type(e).__name__}: {e}")
            continue

        if proc.returncode != 0:
            errors.append(
                f"{entry}: crontab_safe.sh add exit={proc.returncode}: "
                f"stderr={proc.stderr[:200].strip()}"
            )
            continue
        applied.append(f"applied: {cron_line}")

    # V37.9.66 — Remove extra entries (runtime 中有 declared 中没的) via crontab_safe.sh remove
    # 注意: extra_entries 仅在 spec 用 cron_lines_set_diff parser 时非空; V37.9.65
    # line_contains_identifier 路径下 framework 保证 observed ⊆ declared 始终 extra 空.
    for extra_line in sorted(extra_entries):
        if dry_run:
            applied.append(f"DRY-RUN would remove: {extra_line}")
            continue

        helper = os.path.expanduser("~/crontab_safe.sh")
        if not os.path.exists(helper):
            errors.append(f"extra_remove: crontab_safe.sh not found at {helper}")
            continue

        # V37.9.65 cmd_remove 用 grep -F 固定字符串匹配整个 cron line
        try:
            proc = subprocess.run(
                ["bash", helper, "remove", extra_line],
                capture_output=True, text=True,
                timeout=_MACHINE_SYNC_TIMEOUT_SEC,
            )
        except subprocess.TimeoutExpired:
            errors.append(f"extra_remove: crontab_safe.sh remove timed out after {_MACHINE_SYNC_TIMEOUT_SEC}s")
            continue
        except FileNotFoundError as e:
            errors.append(f"extra_remove: bash binary missing: {e}")
            continue
        except Exception as e:
            errors.append(f"extra_remove: subprocess unexpected error: {type(e).__name__}: {e}")
            continue

        if proc.returncode != 0:
            errors.append(
                f"extra_remove: crontab_safe.sh remove exit={proc.returncode}: "
                f"stderr={proc.stderr[:200].strip()}"
            )
            continue
        applied.append(f"removed: {extra_line}")

    return tuple(applied), tuple(errors), dry_run


def _apply_kb_embed_incremental(spec, missing_entries, dry_run, extra_entries=frozenset()):
    """V37.9.24 kb_sources_to_index apply path — single kb_embed.py incremental call.
    V37.9.66: accept extra_entries parameter (ignored — kb_embed.py 没有 remove indexed sources 语义).

    Unlike jobs_to_crontab (per-entry helper call), kb_embed.py is invoked
    ONCE per machine_sync trigger regardless of how many sources are missing.
    kb_embed.py default mode is incremental (mtime diff) — it scans all KB
    notes/sources and re-embeds only changed/new files. So one invocation
    covers all missing entries.

    Why one-shot rather than per-entry:
      - kb_embed.py loads the embedding model once (~3s startup overhead per
        call); per-entry calls would amortize poorly
      - kb_embed.py's mtime-diff logic processes all files; selective re-index
        would require new CLI args we'd have to add to kb_embed.py
      - Idempotent: kb_embed.py guarded by internal lock (V37.8 introduced),
        safe to call repeatedly
      - Returns to alert path immediately on success — convergence framework
        does not own the embedding; kb_embed.py owns it

    dry-run mode emits "DRY-RUN would run: bash kb_embed.py (incremental,
    N missing sources: [list])" without executing subprocess.

    FAIL-OPEN: subprocess timeout / non-zero exit / missing helper become
    apply_errors. Empty missing_entries → empty result.
    """
    # Defense-in-depth: top-level _apply_machine_sync already does this check,
    # but if this function is called directly (testing / future direct use)
    # we must not fabricate a "would run kb_embed" line for zero missing.
    if not missing_entries:
        return (), (), dry_run

    sorted_missing = sorted(missing_entries)
    preview = ", ".join(sorted_missing[:3])
    suffix = f"... +{len(sorted_missing) - 3} more" if len(sorted_missing) > 3 else ""
    summary = f"{len(sorted_missing)} missing sources: {preview}{suffix}"

    if dry_run:
        action = (
            f"DRY-RUN would run: python3 ~/openclaw-model-bridge/kb_embed.py "
            f"(incremental, {summary})"
        )
        return (action,), (), dry_run

    # Real apply path — call kb_embed.py incremental via subprocess.
    helper = os.path.expanduser("~/openclaw-model-bridge/kb_embed.py")
    if not os.path.exists(helper):
        # Defensive: dev / deployment dir absent.
        return (), (f"kb_embed.py not found at {helper}",), dry_run

    try:
        proc = subprocess.run(
            ["python3", helper],
            capture_output=True,
            text=True,
            timeout=_KB_EMBED_TIMEOUT_SEC,
        )
    except subprocess.TimeoutExpired:
        return (), (f"kb_embed.py timed out after {_KB_EMBED_TIMEOUT_SEC}s",), dry_run
    except FileNotFoundError as e:
        return (), (f"python3 binary missing: {e}",), dry_run
    except Exception as e:
        return (), (f"subprocess unexpected error: {type(e).__name__}: {e}",), dry_run

    if proc.returncode != 0:
        return (), (
            f"kb_embed.py exit={proc.returncode}: "
            f"stderr={proc.stderr[-200:].strip()}",
        ), dry_run

    return (f"applied: kb_embed.py incremental ({summary})",), (), dry_run


# V37.9.24 — Apply-function named dispatch (mirrors V37.9.20 _DECLARED_EXTRACTORS
# / V37.9.13 _CONTEXT_EVALUATORS pattern). Each spec yaml's
# convergence_method.apply_function field selects which apply path runs.
# Adding a new machine_sync spec type only requires: (1) new apply function
# here, (2) new entry in this dict, (3) spec yaml apply_function field.
# Zero changes to verify_convergence orchestrator or _apply_machine_sync
# top-level dispatcher.
_APPLY_FUNCTIONS = {
    "jobs_to_crontab_per_entry": _apply_jobs_to_crontab_per_entry,
    "kb_embed_incremental": _apply_kb_embed_incremental,
}


def _apply_machine_sync(spec, missing_entries, extra_entries=frozenset(), dry_run=None):
    """Top-level machine_sync dispatcher — route to spec-specific apply path.

    V37.9.23 introduced this helper as a single-spec implementation. V37.9.24
    refactored it into a named-dispatch top-level router via apply_function field.
    V37.9.66: 加 extra_entries 参数支持双向 sync. apply functions 接受 4 个参数:
    (spec, missing_entries, dry_run, extra_entries=frozenset()).

    Args:
        spec: convergence spec dict
        missing_entries: iterable — declared 中有 runtime 中缺的 identifiers
        extra_entries: V37.9.66 — runtime 中有 declared 中缺的 identifiers (双向 sync)
            默认 frozenset() — 向后兼容 V37.9.23 调用 (line_contains_identifier 单向 parser
            自动保证 observed ⊆ declared, 所以 extra 永远空, 无需此参数)
        dry_run: explicit override; if None, reads CONVERGENCE_DRY_RUN env

    Returns:
        (applied_actions: tuple[str], apply_errors: tuple[str], dry_run: bool)

    FAIL-OPEN: unknown apply_function → apply_errors entry, dry_run preserved.
    """
    if dry_run is None:
        dry_run = _is_dry_run()

    # V37.9.66: 任一非空触发 apply (missing OR extra)
    if not missing_entries and not extra_entries:
        return (), (), dry_run

    method = spec.get("convergence_method") or {}
    apply_fn_name = method.get("apply_function") or ""

    if not apply_fn_name:
        legacy_id_fallback = {
            "jobs_to_crontab": "jobs_to_crontab_per_entry",
        }
        apply_fn_name = legacy_id_fallback.get(spec.get("id", ""), "")

    fn = _APPLY_FUNCTIONS.get(apply_fn_name)
    if fn is None:
        return (), (
            f"no apply_function registered: {apply_fn_name!r} "
            f"(spec.id={spec.get('id', '?')!r})",
        ), dry_run

    try:
        # V37.9.66: 传 extra_entries 给 apply function (kwarg 形式向后兼容 V37.9.23 函数签名)
        return fn(spec, missing_entries, dry_run, extra_entries=extra_entries)
    except Exception as e:
        return (), (
            f"apply_function {apply_fn_name!r} raised: {type(e).__name__}: {e}",
        ), dry_run


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

    # 5. Compute drift (V37.9.66: 双向 sync — missing AND extra)
    missing = declared_fs - observed_fs
    # V37.9.66: extra_in_runtime — observed 中有但 declared 中没有的 (runtime 多余项)
    # 仅当 parser 输出真"set diff" 形式 (e.g. cron_lines_set_diff) 时有意义。
    # line_contains_identifier / json_set_union 等 V37.9.65 旧 parser 自己做 observed ⊆ declared
    # 求交, observed_fs ⊆ declared_fs 始终成立, extra 永远 frozenset() — 向后兼容.
    extra = observed_fs - declared_fs

    # 6. V37.9.23 — machine_sync apply (only if drift_action says so AND drift detected)
    # V37.9.66: drift_detected 现在含 extra (双向 sync), apply 路径也接受 extra 触发
    applied_actions = ()
    apply_dry_run = True
    apply_errors = ()
    if (missing or extra) and drift_action == "machine_sync":
        try:
            # V37.9.66: 新签名 _apply_machine_sync(spec, missing, extra, dry_run=None)
            applied_actions, apply_errors, apply_dry_run = _apply_machine_sync(
                spec, missing, extra_entries=extra
            )
        except Exception as e:
            # _apply_machine_sync is supposed to be FAIL-OPEN, but defense-in-depth.
            apply_errors = (f"apply_machine_sync raised: {type(e).__name__}: {e}",)
            apply_dry_run = _is_dry_run()

    return ConvergenceResult(
        spec_id=spec_id,
        declared=declared_fs,
        observed=observed_fs,
        missing_in_runtime=missing,
        drift_detected=bool(missing or extra),  # V37.9.66 双向
        drift_action=drift_action,
        error=None,
        applied_actions=applied_actions,
        apply_dry_run=apply_dry_run,
        apply_errors=apply_errors,
        extra_in_runtime=extra,  # V37.9.66
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
    base = (
        f"[convergence:{result.spec_id}] DRIFT action={result.drift_action} "
        f"missing={len(result.missing_in_runtime)} "
        f"[{missing_preview}{suffix}]"
    )
    # V37.9.23 — append apply status when machine_sync mode produced anything
    if result.drift_action == "machine_sync" and (result.applied_actions or result.apply_errors):
        mode = "dry-run" if result.apply_dry_run else "real"
        base += (
            f" apply[{mode}]={len(result.applied_actions)} "
            f"apply_errors={len(result.apply_errors)}"
        )
    return base


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
                # V37.9.23 — machine_sync apply tracking (zero-noise for non-machine_sync specs)
                "applied_actions": list(r.applied_actions),
                "apply_dry_run": r.apply_dry_run,
                "apply_errors": list(r.apply_errors),
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
