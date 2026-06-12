# examples/minimal_runtime — 10-Minute Minimal Core Runtime Demo

> V37.9.144, in response to external review #2: *"a 10-minute minimal example plus the full
> production example, as two entry points."* This is the minimal one. The full production
> example is the rest of this repo (the author's live PA — see README "Product Layers").
> The sibling demo [`examples/minimal_consumer/`](../minimal_consumer/) covers the
> **Governance-Ontology layer** as an external consumer (WeatherBot); this demo covers the
> **Core Runtime layer**.

## What it shows (no network, no API keys)

| Step | Control-plane primitive | What you see |
|------|------------------------|--------------|
| 1 | **Provider registry + capability routing** | 8 providers (7 built-in + auto-discovered plugin), capability-sorted fallback chain for `qwen`, `find_best_provider(text, prefer reasoning)` → `doubao` |
| 2 | **Tool governance** | 24 tools in → **12 out**: 12 whitelist-rejected, schema-cleaned, 3 custom tools injected, hard cap ≤12 truncates the overflow — with the three drop/add categories reported separately |
| 3 | **Declarative policy** (optional Layer 2) | `evaluate_policy("max-tools-per-agent")` → `limit=12` from `policy_ontology.yaml`. Skips gracefully with an install hint if PyYAML is absent — a live demo of the dependency boundary |
| 4 | **SLO mini-stats** | per-step timings + ok count (the habit, not the dashboard) |

Then it self-verifies: deterministic decisions (filter results, fallback chain, policy limit)
are compared against the committed [`golden_trace.json`](golden_trace.json). **MATCH means the
control plane behaves on your machine exactly as in the author's production environment.**

## Run

From the repo root (or anywhere):

```
python3 examples/minimal_runtime/minimal_runtime.py
```

Expected tail line:

```
golden trace: MATCH — 确定性决策与提交的 golden_trace.json 一致
```

Exit codes: `0` = demo ok + golden match · `1` = deterministic decisions diverged.

Options: `--json` prints the full trace; `--write-golden` regenerates the reference
(maintainers only, after an intentional behavior change).

## Dependencies

- Steps 1, 2, 4: **Python stdlib only** (Core Runtime layer — zero third-party deps).
- Step 3: needs **PyYAML ≥ 5.4** (`pip install pyyaml` or `pip install openclaw-ontology-engine`).
  Without it the step prints a hint and is skipped; the demo still exits 0 and the golden
  check simply omits the policy comparison.

## Reading guide

- Provider abstraction: [`providers.py`](../../providers.py) (`ProviderRegistry`,
  `build_fallback_chain`, `find_best_provider`)
- Tool governance: [`proxy_filters.py`](../../proxy_filters.py) (`filter_tools`, whitelist +
  `CLEAN_SCHEMAS` + custom-tool injection + hard cap)
- Policy layer: [`ontology/policy_ontology.yaml`](../../ontology/policy_ontology.yaml) +
  [`ontology/engine.py`](../../ontology/engine.py) (`evaluate_policy`)
- Production-scale evidence for all of the above: README "Product Layers" table, layer 3.
