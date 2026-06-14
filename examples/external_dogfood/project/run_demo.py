#!/usr/bin/env python3
"""run_demo.py — exercise the WHEEL-INSTALLED openclaw-ontology-engine against LibraryBot.

Unlike examples/minimal_consumer (which imports `ontology` via PYTHONPATH/editable
install), this demo imports `ontology_engine` — the *distribution* package name —
proving the de-genericized import name (V37.9.128 chunk-2-lite) works for a real
third party who ran `pip install openclaw-ontology-engine` and has NO access to the
monorepo source.

Assumes the environment is already injected (run via run_dogfood.sh):
  ONTOLOGY_CONFIG_DIR   -> this project's ontology/ directory
  ONTOLOGY_PROJECT_ROOT -> this project directory
  ontology_engine installed into the active (isolated) venv from a wheel

Because engine.py / governance_checker.py / convergence.py resolve their config
dir at *module load time*, the env MUST be set before these imports — exactly
what a real consumer's process environment does.

Five capabilities, all honoring config-injection:
  1. ToolOntology    — allowed_tools reads consumer's tool_ontology.yaml
  2. find_by_domain  — reads consumer's domain_ontology.yaml
  3. evaluate_policy — reads consumer's policy_ontology.yaml
  4. governance run_all — audits consumer's invariants against consumer's files
  5. convergence     — reads consumer's convergence_ontology.yaml + source files

Returns process exit code: 0 if the governance audit passes, 1 otherwise.
"""
import os
import sys

# Distribution package name (NOT the in-repo `ontology`). This line only works
# for a third party because the wheel ships `ontology_engine` (package-dir maps
# it to the engine's ontology/ source). If this import resolved to the monorepo,
# the dogfood would be meaningless — the test asserts __file__ lives in the venv.
import ontology_engine.convergence as cv
import ontology_engine.engine as engine
import ontology_engine.governance_checker as gov


def _h(title):
    print(f"\n{'─' * 64}\n  {title}\n{'─' * 64}")


def main() -> int:
    cfg = os.environ.get("ONTOLOGY_CONFIG_DIR", "(unset)")
    root = os.environ.get("ONTOLOGY_PROJECT_ROOT", "(unset)")
    print("📚  LibraryBot — an EXTERNAL (wheel-installed) consumer of openclaw-ontology-engine")
    print(f"    engine package      = {engine.__file__}")
    print(f"    ONTOLOGY_CONFIG_DIR   = {cfg}")
    print(f"    ONTOLOGY_PROJECT_ROOT = {root}")

    # ── 1. ToolOntology query ──────────────────────────────────────────
    _h("1. ToolOntology — allowed tools (from consumer's tool_ontology.yaml)")
    onto = engine.get_ontology()
    tools = sorted(onto.allowed_tools | onto.custom_tool_names)
    print(f"    allowed_tools = {tools}")
    assert "checkout_book" in tools, "engine should load LibraryBot tools"
    assert "data_clean" not in tools, "must NOT be reading the bridge's tools"
    assert "get_forecast" not in tools, "must NOT be reading WeatherBot's tools"

    # ── 2. find_by_domain ──────────────────────────────────────────────
    _h("2. find_by_domain('Actor') — from consumer's domain_ontology.yaml")
    actors = engine.find_by_domain("Actor")
    for a in actors:
        print(f"    actor: {a}")
    actor_ids = {a["id"] for a in actors}
    assert "librarian_bot" in actor_ids, "should see LibraryBot's actors"

    # ── 3. evaluate_policy ─────────────────────────────────────────────
    _h("3. evaluate_policy('max-books-per-checkout') — consumer's policy_ontology.yaml")
    pol = engine.evaluate_policy("max-books-per-checkout")
    print(f"    found={pol.get('found')} type={pol.get('type')} "
          f"limit={pol.get('limit')} hard_limit={pol.get('hard_limit')}")
    print(f"    governance_invariant={pol.get('governance_invariant')}")
    assert pol.get("found") is True, "policy must be resolvable from injected config"
    assert pol.get("limit") == 5, "LibraryBot caps checkout at 5"

    # ── 4. governance run_all (invariant phase) ────────────────────────
    _h("4. governance audit — consumer's invariants vs consumer's files")
    data = gov._load()  # reads governance_ontology.yaml from ONTOLOGY_CONFIG_DIR
    meta = data.get("audit_metadata", {})
    print(f"    auditing {meta.get('total_invariants')} invariant(s), "
          f"version {meta.get('version')}")
    results = gov.run_all(data)
    failed = 0
    for r in results:
        icon = "✅" if r["status"] == "pass" else "❌"
        print(f"    {icon} {r['id']:24s} {r['passed_checks']}/{r['total_checks']} checks  "
              f"[{r['severity']}]")
        if r["status"] != "pass":
            failed += 1
            for c in r["checks"]:
                if c["status"] != "pass":
                    print(f"         ↳ {c['status']}: {c['name']} — {c['message']}")

    # ── 5. convergence — consumer's specs vs runtime ───────────────────
    _h("5. convergence — consumer's convergence_ontology.yaml")
    spec_ids = cv.list_spec_ids()
    print(f"    consumer convergence specs = {spec_ids}")
    assert "librarybot-allowed-genres-active" in spec_ids, \
        "convergence must read THIS consumer's spec, not the engine-bundled 5"
    assert "jobs_to_crontab" not in spec_ids, \
        "must NOT be reading the bridge's convergence specs"
    cres = cv.verify_convergence("librarybot-allowed-genres-active")
    print(f"    {cv.format_result_for_log(cres)}")
    assert cres.error is None, f"convergence spec should run cleanly, got {cres.error}"
    assert not cres.drift_detected, "LibraryBot genres are in sync — expected zero drift"
    print("    ✓ convergence evaluated against THIS project's spec + source files")

    _h("Result")
    if failed == 0:
        print("    🎉 LibraryBot audited clean by the WHEEL-INSTALLED engine — "
              "third-party config-injection works end-to-end.")
        return 0
    print(f"    ⚠️  {failed} invariant(s) failed.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
