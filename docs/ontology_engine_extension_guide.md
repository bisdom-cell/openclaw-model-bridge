# Ontology Engine Extension Guide — govern *your* project in 60 seconds

> **V37.9.104 · Phase 5 chunk 4** — the first worked example of a *consumer project*
> using `openclaw-ontology-engine` via config-injection. Companion to
> `docs/ontology_engine_packaging.md` (the two-layer design) and mirror of
> `docs/provider_plugin_guide.md` (the provider-plugin model that was only
> proven once Doubao — a real 8th provider — actually plugged in).

---

## Overview

`openclaw-ontology-engine` is a **project-agnostic governance engine** (Layer 1).
You bring **your own YAML** (Layer 2) and the engine gives you:

- **Tool governance** — declared allowlist / schemas / aliases (`ToolOntology`)
- **Semantic query** — `find_by_domain(...)`, `evaluate_policy(...)`
- **Governance audit** — your invariants checked against your files (`openclaw-ontology-audit`)

You **never edit the engine**. You point two environment variables at your config.

A complete, runnable consumer lives in
[`examples/minimal_consumer/`](../examples/minimal_consumer/) — the *WeatherBot*
demo. Run it:

```bash
bash examples/minimal_consumer/run_demo.sh
```

---

## Quick Start: govern a new project in 60 seconds

### 1. Install the engine (Layer 1)

```bash
pip install openclaw-ontology-engine        # provides `import ontology` + 2 console scripts
```

> In this repo (dev), `pip install -e .` or `PYTHONPATH=<repo-root>` is equivalent —
> the demo's `run_demo.sh` uses `PYTHONPATH` so it runs with zero install.

### 2. Write your config (Layer 2)

Create an `ontology/` directory in your project. The smallest useful set:

```
myproject/
├── weatherbot.py                  # your code (audited)
└── ontology/
    ├── tool_ontology.yaml         # what tools your agent may call
    ├── domain_ontology.yaml       # your six-domain model (optional)
    ├── policy_ontology.yaml       # your declarative policies (optional)
    └── governance_ontology.yaml   # your invariants (required for audit)
```

The smallest valid `governance_ontology.yaml`:

```yaml
audit_metadata:
  version: "0.1"
  total_invariants: 1
  total_checks: 1
  meta_rules: 0
meta_rules: []
invariants:
  - id: INV-MYPROJECT-LIMIT
    name: caps-something
    meta_rule: ""
    verification_layer: [declaration]
    severity: high
    declaration: "What this invariant guarantees."
    checks:
      - name: "source declares the limit"
        check_type: file_contains      # field is check_type (not type)
        file: weatherbot.py            # relative to ONTOLOGY_PROJECT_ROOT
        pattern: "MAX_CITIES_PER_QUERY"
```

### 3. Point the engine at your config (the keystone)

```bash
export ONTOLOGY_CONFIG_DIR=$PWD/ontology     # where your YAML lives
export ONTOLOGY_PROJECT_ROOT=$PWD            # base for file_contains / python_assert paths
```

### 4. Run

```bash
openclaw-ontology-query --json     # query YOUR tools/domains/policies
openclaw-ontology-audit            # audit YOUR invariants against YOUR files
```

Exit code `0` = all invariants pass; `1` = a fail/error.

That's it. The same engine that governs `openclaw-model-bridge` now governs your
project — because the engine reads *your* YAML, not its own.

---

## The two environment variables

| Variable | Controls | Default (backward-compatible) |
|---|---|---|
| `ONTOLOGY_CONFIG_DIR` | Directory holding `tool_ontology.yaml` / `governance_ontology.yaml` / `domain_ontology.yaml` / `policy_ontology.yaml` | the engine's own `ontology/` dir |
| `ONTOLOGY_PROJECT_ROOT` | Base dir for governance `file_contains` / `python_assert` relative paths + MRD scan root | the engine's repo root |

Fine-grained overrides still work: `ToolOntology(path=...)`,
`evaluate_policy(..., path=...)`, `find_by_domain(..., path=...)`. The env vars
are just the "no explicit path" fallback.

---

## Check types you can use

| `check_type` | Semantics |
|---|---|
| `file_contains` | file (relative to `ONTOLOGY_PROJECT_ROOT`) matches `pattern` (regex) |
| `file_not_contains` | file does NOT match `pattern` (missing file = pass) |
| `python_assert` | run `code` with cwd=`ONTOLOGY_PROJECT_ROOT`; no exception = pass |
| `env_var_exists` | environment variable is set |
| `command_succeeds` | shell `command` exits 0 (cwd=`ONTOLOGY_PROJECT_ROOT`, 30s timeout) |

> **Field name gotcha:** the dispatch key is `check_type`, not `type`. The python
> code field is `code:` (also accepts `assertion:` for backward-compat, but prefer
> `code:`). See the WeatherBot demo's `governance_ontology.yaml` for working examples.

> **Regex escaping in YAML:** in *double-quoted* YAML use `\\s` (YAML eats one
> backslash → regex sees `\s`); in *single-quoted* YAML use `\s` (YAML passes the
> backslash through). Mixing these up is the #1 demo bug.

---

## Worked example: WeatherBot

[`examples/minimal_consumer/`](../examples/minimal_consumer/) is a deliberately
tiny agent runtime, intentionally different from `openclaw-model-bridge` (it has
`get_forecast` / `save_favorite_city`, not `data_clean` / `search_kb`). It proves
the engine governs a *second* project, not a copy of the first.

`run_demo.sh` exercises five config-injected capabilities and prints a clean
walkthrough. Expected tail:

```
🎉 WeatherBot audited clean by the *injected* engine — config-injection works end-to-end (incl. convergence, chunk-3).
```

---

## Config-injected phases

The `openclaw-ontology-audit` console runs three phases: **invariants**, **MRD
meta-discovery**, and **convergence**. As of **V37.9.107 (chunk-3a)** all three
honor config-injection:

- **invariants** → reads `ONTOLOGY_CONFIG_DIR` / scans `ONTOLOGY_PROJECT_ROOT`.
- **MRD** → scans `ONTOLOGY_PROJECT_ROOT`; gracefully no-ops when a consumer lacks
  `jobs_registry.yaml` / `notify.sh` / `jobs/*/run_*.sh`.
- **convergence** → reads your `convergence_ontology.yaml` (via `ONTOLOGY_CONFIG_DIR`)
  and resolves declared source files (`jobs_registry.yaml`, `*.json`, …) relative to
  `ONTOLOGY_PROJECT_ROOT`. The WeatherBot demo's `convergence_ontology.yaml` +
  `weatherbot_state.json` exercise this end-to-end (`run_demo.py` section 5). With
  no env set, the engine preserves its V37.9.19 default byte-for-byte (zero
  regression for the bridge itself).

> **Writing a convergence spec:** declare a `source` (a file in your project, e.g.
> `weatherbot_state.json`), an `extractor` (e.g. `json_file_paths` with `json_paths`),
> a `runtime_observable` (`method: shell_command` + `command` + `parser:
> line_contains_identifier`), and a `drift_action` (`alert_only` is safest). See
> `examples/minimal_consumer/ontology/convergence_ontology.yaml`.

### Remaining follow-up (chunk-3b)

The MRD scanners' project-specific *filename patterns* (`jobs_registry.yaml` /
`notify.sh` / `jobs/*/run_*.sh`) are still hardcoded in the framework. Because the
scan **root** is already injected (`_PROJECT_ROOT`) and the scanners gracefully
no-op for consumers, this is a configurability nicety, not a correctness bug —
parameterizing the pattern table is tracked as chunk-3b (a focused session, to
avoid regressing the MR-4 blood-lesson scanners).

See `docs/ontology_engine_packaging.md` §5 (known coupling) and §6 (migration
roadmap) for the full picture.

---

## API reference (Layer 1)

| Symbol | Purpose |
|---|---|
| `ontology.engine.get_ontology(path=None) -> ToolOntology` | load tool ontology (honors `ONTOLOGY_CONFIG_DIR`) |
| `ToolOntology.allowed_tools` / `.allowed_prefixes` / `.custom_tool_names` | tool allowlist queries |
| `ontology.engine.find_by_domain(name, path=None) -> list` | six-domain instance query |
| `ontology.engine.evaluate_policy(policy_id, context=None, path=None) -> dict` | resolve a declarative policy |
| `ontology.governance_checker.run_all(data) -> list` | run invariant phase |
| `ontology.governance_checker.main() -> int` | full audit (= `openclaw-ontology-audit`) |

---

## See also

- `docs/ontology_engine_packaging.md` — two-layer architecture, migration chunks 1–5
- `docs/provider_plugin_guide.md` — the provider-plugin model this guide mirrors
- `ontology/CONSTITUTION.md` — Ontology constitution (top clause: deleting the engine leaves the host system working)
