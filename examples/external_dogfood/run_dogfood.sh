#!/usr/bin/env bash
# run_dogfood.sh — out-of-repo dogfood for openclaw-ontology-engine (V37.9.148).
#
# What this proves (the gap external-reviewer-2 flagged): the PUBLISHED engine is
# consumable by a third party who has never seen this monorepo. Unlike
# examples/minimal_consumer (editable / PYTHONPATH=repo), this:
#   1. builds the wheel offline,
#   2. installs it (--no-deps) into an ISOLATED venv (no PYTHONPATH back to the repo),
#   3. copies the toy LibraryBot project OUT of the repo (to /tmp), and
#   4. runs the engine against it from /tmp — using the distribution import name
#      `ontology_engine` and the console scripts openclaw-ontology-audit / -query.
#
# Reverse-validation: with the SAME wheel, removing config-injection makes the
# engine fall back to its OWN bundled defaults (the bridge's) — proving
# config-injection (not PYTHONPATH, not the monorepo) is what loads LibraryBot.
#
# Offline: a --system-site-packages venv supplies PyYAML; the wheel is installed
# --no-deps. The isolation that matters (no monorepo source) holds because the
# system has neither `ontology` nor `ontology_engine` installed.
set -euo pipefail

DOGFOOD_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$DOGFOOD_DIR/../.." && pwd)"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/dogfood.XXXXXX")"
trap 'rm -rf "$WORK"; rm -rf "$REPO_ROOT/build" "$REPO_ROOT/openclaw_ontology_engine.egg-info"' EXIT

say() { printf '%s\n' "$*"; }
hr() { say "════════════════════════════════════════════════════════════════"; }

# ── 1. build the wheel offline (robust method chain) ──────────────────
build_wheel() {
  local dest="$1"
  mkdir -p "$dest"
  # Method 1: python -m build (Mac Mini / proper dev container, if `build` present)
  if ( cd "$REPO_ROOT" && python3 -m build --wheel --no-isolation -o "$dest" ) >/dev/null 2>&1; then return 0; fi
  # Method 2: setuptools build_meta backend directly (no `build` package needed)
  if ( cd "$REPO_ROOT" && python3 -c "import setuptools.build_meta as b; b.build_wheel('$dest')" ) >/dev/null 2>&1; then return 0; fi
  # Method 3: Debian sandbox fallback — stdlib distutils avoids the install_layout quirk
  if ( cd "$REPO_ROOT" && SETUPTOOLS_USE_DISTUTILS=stdlib python3 -c "import setuptools.build_meta as b; b.build_wheel('$dest')" ) >/dev/null 2>&1; then return 0; fi
  return 1
}

hr
say "  openclaw-ontology-engine · out-of-repo dogfood (V37.9.148)"
hr
say "» 1/5  Building wheel offline …"
if ! build_wheel "$WORK/dist"; then
  say "✗  Could not build a wheel in this environment (need setuptools or python -m build)."
  exit 2
fi
WHEEL="$(ls "$WORK"/dist/*.whl | head -1)"
say "    wheel: $(basename "$WHEEL")"
rm -rf "$REPO_ROOT/build" "$REPO_ROOT/openclaw_ontology_engine.egg-info"

# ── 2. isolated venv + install the wheel (offline) ────────────────────
say "» 2/5  Creating isolated venv + installing the wheel (--no-deps, offline) …"
python3 -m venv --system-site-packages "$WORK/venv"
VPY="$WORK/venv/bin/python"
"$VPY" -m pip install --no-deps --quiet "$WHEEL"
AUDIT="$WORK/venv/bin/openclaw-ontology-audit"
QUERY="$WORK/venv/bin/openclaw-ontology-query"
say "    console scripts: $(basename "$AUDIT"), $(basename "$QUERY")"

# Confirm the engine resolves to the venv wheel, NOT the monorepo.
ENGINE_FILE="$(cd /tmp && "$VPY" -c 'import ontology_engine; print(ontology_engine.__file__)')"
case "$ENGINE_FILE" in
  "$WORK"/venv/*) say "    ✓ ontology_engine resolves to the venv wheel (no monorepo leakage)";;
  *) say "    ✗ engine leaked to: $ENGINE_FILE"; exit 1;;
esac

# ── 3. copy the toy project OUT of the repo to /tmp ───────────────────
say "» 3/5  Copying the LibraryBot toy project out of the repo to /tmp …"
TOY="$WORK/librarybot_project"
cp -R "$DOGFOOD_DIR/project" "$TOY"
say "    toy project: $TOY"

# ── 4. run the engine against the out-of-repo toy (config-injected) ───
say "» 4/5  Running the wheel-installed engine against LibraryBot (config-injected) …"
say ""
# NOTE: PYTHONPATH is explicitly unset for these runs — the whole point is that
# the engine works WITHOUT the monorepo on the path. This is the opposite of
# examples/minimal_consumer/run_demo.sh, which sets PYTHONPATH=repo.
( cd "$TOY" && env -u PYTHONPATH \
    ONTOLOGY_CONFIG_DIR="$TOY/ontology" ONTOLOGY_PROJECT_ROOT="$TOY" \
    "$VPY" run_demo.py )
say ""
say "    console: openclaw-ontology-audit (exit 0 = LibraryBot invariants pass)"
( cd "$TOY" && env -u PYTHONPATH \
    ONTOLOGY_CONFIG_DIR="$TOY/ontology" ONTOLOGY_PROJECT_ROOT="$TOY" \
    "$AUDIT" >/dev/null 2>&1 ) && say "    ✓ audit exit 0" || { say "    ✗ audit failed"; exit 1; }
TOY_TOOLS="$( cd "$TOY" && env -u PYTHONPATH \
    ONTOLOGY_CONFIG_DIR="$TOY/ontology" ONTOLOGY_PROJECT_ROOT="$TOY" \
    "$QUERY" --tools --json | python3 -c 'import json,sys; print(",".join(sorted(t["name"] for t in json.load(sys.stdin))))' )"
say "    ✓ openclaw-ontology-query --tools → $TOY_TOOLS"

# ── 5. reverse-validation: no injection → wheel's OWN bundled defaults ─
say "» 5/5  Reverse-validation: SAME wheel WITHOUT injection → bundled defaults …"
BUNDLED="$( cd /tmp && env -u PYTHONPATH -u ONTOLOGY_CONFIG_DIR -u ONTOLOGY_PROJECT_ROOT \
    "$QUERY" --tools --json | python3 -c 'import json,sys
ts=[t["name"] for t in json.load(sys.stdin)]
print(str(len(ts))+" tools;data_clean="+str("data_clean" in ts)+";checkout_book="+str("checkout_book" in ts))' )"
say "    bundled (no injection) → $BUNDLED"
case "$TOY_TOOLS,$BUNDLED" in
  *checkout_book*data_clean=True*checkout_book=False*)
    say "    ✓ injected→LibraryBot, bundled→bridge defaults: config-injection is the mechanism";;
  *) say "    ✗ reverse-validation mismatch"; exit 1;;
esac

say ""
hr
say "  🎉 PASS — openclaw-ontology-engine is consumable out-of-repo from a wheel."
hr
