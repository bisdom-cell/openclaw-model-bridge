#!/usr/bin/env python3
"""run_demo.py — exercise openclaw-ontology-engine against the WeatherBot consumer.

Assumes the environment is already injected (run via run_demo.sh):
  ONTOLOGY_CONFIG_DIR   -> examples/minimal_consumer/ontology
  ONTOLOGY_PROJECT_ROOT -> examples/minimal_consumer
  PYTHONPATH includes the engine (real consumers: `pip install openclaw-ontology-engine`)

Because engine.py / governance_checker.py resolve their config dir at *module
load time*, the env MUST be set before these imports — which is exactly what a
real consumer's process environment does. This is the end-to-end proof that
config-injection works: the same engine code audits a *different* project.

The five capabilities demonstrated all honor config-injection:
  1. ToolOntology  — allowed_tools query reads consumer's tool_ontology.yaml
  2. find_by_domain — reads consumer's domain_ontology.yaml
  3. evaluate_policy — reads consumer's policy_ontology.yaml
  4. governance run_all — audits consumer's invariants against consumer's files
  5. convergence — reads consumer's convergence_ontology.yaml + source files
     (V37.9.107 chunk-3: previously the only phase NOT config-injected; the
     coupling this very demo exposed in chunk-4, now closed)

Returns process exit code: 0 if the governance audit passes, 1 otherwise.
"""
import os
import sys

import ontology.convergence as cv
import ontology.engine as engine
import ontology.governance_checker as gov


def _h(title):
    print(f"\n{'─' * 64}\n  {title}\n{'─' * 64}")


def main() -> int:
    cfg = os.environ.get("ONTOLOGY_CONFIG_DIR", "(unset)")
    root = os.environ.get("ONTOLOGY_PROJECT_ROOT", "(unset)")
    print("🌦  WeatherBot — a consumer of openclaw-ontology-engine")
    print(f"    ONTOLOGY_CONFIG_DIR   = {cfg}")
    print(f"    ONTOLOGY_PROJECT_ROOT = {root}")

    # ── 1. ToolOntology query ──────────────────────────────────────────
    _h("1. ToolOntology — allowed tools (from consumer's tool_ontology.yaml)")
    onto = engine.get_ontology()
    tools = sorted(onto.allowed_tools)
    print(f"    allowed_tools = {tools}")
    assert "get_forecast" in tools, "engine should load WeatherBot tools, not the bridge's"
    assert "data_clean" not in tools, "must NOT be reading openclaw-model-bridge's tools"

    # ── 2. find_by_domain ──────────────────────────────────────────────
    _h("2. find_by_domain('Actor') — from consumer's domain_ontology.yaml")
    actors = engine.find_by_domain("Actor")
    for a in actors:
        print(f"    actor: {a}")
    actor_ids = {a["id"] for a in actors}
    assert "weather_bot" in actor_ids, "should see WeatherBot's actors"

    # ── 3. evaluate_policy ─────────────────────────────────────────────
    _h("3. evaluate_policy('max-cities-per-query') — consumer's policy_ontology.yaml")
    pol = engine.evaluate_policy("max-cities-per-query")
    print(f"    found={pol.get('found')} type={pol.get('type')} "
          f"limit={pol.get('limit')} hard_limit={pol.get('hard_limit')}")
    print(f"    governance_invariant={pol.get('governance_invariant')}")
    assert pol.get("found") is True, "policy must be resolvable from injected config"
    assert pol.get("limit") == 3, "WeatherBot caps cities at 3"

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

    # ── 5. convergence — consumer's specs vs runtime (chunk-3) ─────────
    _h("5. convergence — consumer's convergence_ontology.yaml (chunk-3)")
    spec_ids = cv.list_spec_ids()
    print(f"    consumer convergence specs = {spec_ids}")
    assert "weatherbot-allowed-units-active" in spec_ids, \
        "convergence must read THIS consumer's spec, not the engine-bundled 5"
    assert "jobs_to_crontab" not in spec_ids, \
        "must NOT be reading openclaw-model-bridge's convergence specs"
    cres = cv.verify_convergence("weatherbot-allowed-units-active")
    print(f"    {cv.format_result_for_log(cres)}")
    assert cres.error is None, f"convergence spec should run cleanly, got {cres.error}"
    assert not cres.drift_detected, "WeatherBot units are in sync — expected zero drift"
    print("    ✓ convergence evaluated against THIS project's spec + source files")

    _h("Result")
    if failed == 0:
        print("    🎉 WeatherBot audited clean by the *injected* engine — "
              "config-injection works end-to-end (incl. convergence, chunk-3).")
        return 0
    print(f"    ⚠️  {failed} invariant(s) failed.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
