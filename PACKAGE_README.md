# openclaw-ontology-engine

Declarative governance + tool-ontology engine for Agent Runtime control planes.
**Bring your own YAML (Layer 2), get tool governance + semantic query +
governance audit + declared-state convergence (Layer 1).**

Extracted from a production Agent Runtime ([openclaw-model-bridge](https://github.com/bisdom-cell/openclaw-model-bridge))
that runs 40+ scheduled jobs against a multi-provider LLM gateway — every rule
in this engine exists because a real production incident demanded it
(90 invariants, 23 meta-rules, 14 silent-failure scanners and counting).

## Two-layer architecture

| Layer | What | Who writes it |
|---|---|---|
| **Layer 1 — engine** (this package) | Tool Ontology engine, 5 check executors, meta-rule discovery (MRD) scanners, convergence framework, three-stage gates | project-agnostic, `pip install` |
| **Layer 2 — config** (your repo) | `tool_ontology.yaml`, `governance_ontology.yaml`, `domain_ontology.yaml`, `policy_ontology.yaml`, `convergence_ontology.yaml` | you |

The engine never hardcodes your project: all Layer 2 config is injected via
two environment variables.

## Quick start

```bash
pip install openclaw-ontology-engine

# Point the engine at YOUR config + YOUR project root
export ONTOLOGY_CONFIG_DIR=/path/to/your/ontology
export ONTOLOGY_PROJECT_ROOT=/path/to/your/repo

# Audit your invariants (exit 1 on violation — CI-friendly)
openclaw-ontology-audit

# Query your tool ontology
openclaw-ontology-query
```

```python
from ontology_engine.engine import ToolOntology, evaluate_policy, find_by_domain
from ontology_engine import governance_checker

onto = ToolOntology()                      # reads ONTOLOGY_CONFIG_DIR
print(onto.allowed_tools())                # your declared tool whitelist
print(find_by_domain("Actor"))             # your domain instances
print(evaluate_policy("max-tools-per-agent"))  # your declared policy limits
```

## What you get

1. **Tool governance** — declarative whitelist / schema / alias resolution /
   semantic classification, replacing hardcoded tool lists.
2. **Governance audit** — invariants with declaration + runtime verification
   layers, executed by 5 check types (`file_contains`, `file_not_contains`,
   `python_assert`, …).
3. **Meta-rule discovery (MRD)** — proactive scanners for whole bug *classes*
   (silent `except: pass`, log-to-stdout pollution, push-route bypasses, …)
   with project patterns supplied via `mrd_scan_patterns` in your YAML.
4. **Convergence framework** — verifies your *declared* state (job registry,
   service list) actually matches *runtime* state (crontab, launchd), with
   per-spec dry-run/machine-sync escalation.
5. **Three-stage gates** — pre-check / runtime-gate / post-verify policy
   observation at the request boundary (shadow mode by default).

A complete worked example (a fictional WeatherBot runtime, nothing shared
with the host project) lives in
[`examples/minimal_consumer/`](https://github.com/bisdom-cell/openclaw-model-bridge/tree/main/examples/minimal_consumer).

## Documentation

- [Extension guide](https://github.com/bisdom-cell/openclaw-model-bridge/blob/main/docs/ontology_engine_extension_guide.md) — 60-second onboarding
- [Packaging design](https://github.com/bisdom-cell/openclaw-model-bridge/blob/main/docs/ontology_engine_packaging.md) — architecture & roadmap

## License

MIT
