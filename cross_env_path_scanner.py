#!/usr/bin/env python3
"""
cross_env_path_scanner.py — V37.9.94 MR-15 deployment-layout 防御 scanner

Detects resolver-pattern Python functions that lack Mac Mini canonical
path candidate (`~/openclaw-model-bridge/<file>`), preventing the 5th
recurrence of MR-15 (deployment-layout-must-be-tested-on-target).

MR-15 演出史 (4 次):
  - V37.9.56-hotfix: top_alignment_picker (`~/.openclaw/jobs/` vs `~/jobs/`)
  - V37.9.76-hotfix: router_decide `_load_yaml_job_profile` (PATH 重合)
  - V37.9.78-hotfix: health_check.sh (macOS BSD timeout missing — shell)
  - V37.9.92:        daily_observer `_resolve_registry_path` (3 candidates miss)

V37.9.94 INV-CROSS-ENV-PATH-001 立 governance 守 framework 级预防.

Detection logic:
  - Functions with: `os.path.expanduser("~/<config_file>")` AND
                    `os.path.dirname(os.path.abspath(__file__))` (script-adj)
    = "resolver pattern" — function tries multiple candidates to find a file
  - For each resolver, verify it also includes:
                    `os.path.expanduser("~/openclaw-model-bridge/<config_file>")`
  - Config file = endswith .yaml/.yml/.json/.md (typical shared config)

FAIL-CLOSE: exit 1 if any violation found.
"""

import ast
import os
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent

# V37.9.94: Marker for source-level + governance grep
V37_9_94_MARKER = "V37.9.94"

# Files to exclude from scanning
EXCLUDED_FILES = {
    "cross_env_path_scanner.py",   # self
    "cross_os_quirk_scanner.py",   # V37.9.67 sister scanner
}

# Extensions considered "config-like" — these are paths that should be
# cross-environment compatible (registry/policy/docs files).
CONFIG_FILE_SUFFIXES = (".yaml", ".yml", ".json", ".md")

# Regex: os.path.expanduser("~/<file>") — captures the path after ~/
_EXPAND_HOME_RE = re.compile(r'os\.path\.expanduser\(["\']~/([^"\']+)["\']\)')

# Regex: os.path.expanduser("~/openclaw-model-bridge/<file>")
_CANONICAL_RE = re.compile(
    r'os\.path\.expanduser\(["\']~/openclaw-model-bridge/([^"\']+)["\']\)'
)


def gather_python_files(root=None):
    """Find Python files to scan. Excludes test_*.py, scanner itself,
    and known sister scanners."""
    if root is None:
        root = REPO_ROOT
    root = Path(root)
    files = []

    def is_excluded(p):
        if p.name in EXCLUDED_FILES:
            return True
        if p.name.startswith("test_"):
            return True
        return False

    # Top-level .py files
    for p in root.glob("*.py"):
        if not is_excluded(p):
            files.append(p)

    # jobs/ + ontology/ subtrees
    for sub in ("jobs", "ontology"):
        subdir = root / sub
        if subdir.is_dir():
            for p in subdir.rglob("*.py"):
                if not is_excluded(p):
                    files.append(p)

    return sorted(files)


def is_config_path(path_after_tilde):
    """Decide if a `~/X` path is config-like (worth checking for canonical).

    Skips paths that are:
      - kb data paths (~/.kb/...) — runtime data not git-managed
      - hidden dirs (~/.openclaw/...) — Mac Mini-specific deployments
      - log files (.log) — process output
      - script paths (.py / .sh) — handled via FILE_MAP not canonical resolver
      - non-config extensions
    """
    if path_after_tilde.startswith("."):
        return False  # hidden dirs (.kb, .openclaw)
    if path_after_tilde.startswith("openclaw-model-bridge/"):
        return False  # this IS the canonical, not a regular candidate
    return any(path_after_tilde.endswith(suf)
               for suf in CONFIG_FILE_SUFFIXES)


def has_script_adjacent_pattern(func_body):
    """Detect script-adjacent fallback marker (signals resolver pattern).

    Resolver functions typically have:
      os.path.join(os.path.dirname(os.path.abspath(__file__)), "<file>")
    """
    return ("os.path.dirname" in func_body and "__file__" in func_body)


def scan_function_body(func_body, func_name, file_label):
    """Check one function body for MR-15 violations.

    Args:
        func_body: source code of the function
        func_name: function name (for reporting)
        file_label: file path (for reporting)

    Returns:
        list[str] of violation messages, empty if compliant
    """
    violations = []

    # Find all ~/<file> candidates that look like config
    home_files = set()
    for m in _EXPAND_HOME_RE.finditer(func_body):
        path = m.group(1)
        if is_config_path(path):
            home_files.add(path)

    if not home_files:
        return violations  # not a resolver

    if not has_script_adjacent_pattern(func_body):
        return violations  # has ~/X but no script-adj = not the resolver pattern

    # For each config home_file, verify canonical is in same function
    canonical_files = {m.group(1) for m in _CANONICAL_RE.finditer(func_body)}

    for home_file in sorted(home_files):
        if home_file in canonical_files:
            continue  # ✓ has canonical
        violations.append(
            f"{file_label}:{func_name} — resolver has '~/{home_file}' but "
            f"missing canonical '~/openclaw-model-bridge/{home_file}' "
            "(MR-15 4 prior演出 — V37.9.56h/76h/78h/92 / V37.9.94 立 INV 防 5th)"
        )

    return violations


def scan_file(file_path, root=None):
    """Scan one .py file. Returns list of violation messages."""
    if root is None:
        root = REPO_ROOT
    file_label = str(file_path.relative_to(root))
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            source = f.read()
    except OSError:
        return []

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []  # skip unparseable files silently

    violations = []
    source_lines = source.split("\n")
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            start = node.lineno - 1
            end = (node.end_lineno
                   if hasattr(node, "end_lineno") and node.end_lineno
                   else len(source_lines))
            func_body = "\n".join(source_lines[start:end])
            violations.extend(scan_function_body(
                func_body, node.name, file_label))
    return violations


def scan_repo(root=None):
    """Scan entire repo. Returns (file_count, list_of_violations)."""
    if root is None:
        root = REPO_ROOT
    files = gather_python_files(root)
    all_violations = []
    for f in files:
        all_violations.extend(scan_file(f, root=root))
    return len(files), all_violations


def main():
    """CLI entry. FAIL-CLOSE: exit 1 if violations found.

    Usage:
      python3 cross_env_path_scanner.py          # scan REPO_ROOT
      python3 cross_env_path_scanner.py --file X # scan single file
    """
    if len(sys.argv) >= 3 and sys.argv[1] == "--file":
        target = Path(sys.argv[2])
        if not target.exists():
            print(f"ERROR: file not found: {target}", file=sys.stderr)
            sys.exit(2)
        violations = scan_file(target, root=target.parent)
        file_count = 1
    else:
        file_count, violations = scan_repo()

    print(f"=== cross_env_path_scanner ({V37_9_94_MARKER}): "
          f"{file_count} Python file(s) scanned ===")

    if violations:
        print(f"\n❌ {len(violations)} MR-15 violation(s):", file=sys.stderr)
        for v in violations:
            print(f"  - {v}", file=sys.stderr)
        print("\n⚠️  Fix: each resolver function with `~/<file>` candidate "
              "and script-adjacent fallback MUST also include\n"
              "    `os.path.expanduser('~/openclaw-model-bridge/<file>')` "
              "as Mac Mini canonical candidate.\n"
              "    See V37.9.92 daily_observer._resolve_registry_path for "
              "reference implementation.", file=sys.stderr)
        sys.exit(1)

    print("✅ all resolver functions include Mac Mini canonical path "
          "(MR-15 5th occurrence prevented)")
    sys.exit(0)


if __name__ == "__main__":
    main()
