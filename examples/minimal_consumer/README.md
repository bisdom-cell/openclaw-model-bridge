# WeatherBot — a minimal consumer of `openclaw-ontology-engine`

This directory is **not** part of `openclaw-model-bridge`. It is a deliberately
tiny, fictional agent runtime ("WeatherBot") whose only job is to prove that the
ontology-engine (Layer 1) can govern a **different** project (Layer 2 = your own
YAML) purely via two environment variables — **config-injection**.

It is the engine's "Doubao moment": just as the Provider Plugin interface was only
proven extensible when a real 8th provider (Doubao) plugged in, the ontology-engine
is proven extensible when a real *second project* (WeatherBot) consumes it.

## Run it

```bash
bash run_demo.sh
```

Expected tail:

```
🎉 WeatherBot audited clean by the *injected* engine — config-injection works end-to-end (incl. convergence, chunk-3).
```

## What's here

```
examples/minimal_consumer/
├── weatherbot.py                  # the consumer's code (audited by its own invariants)
├── weatherbot_state.json          # consumer state file (read by the convergence spec)
├── run_demo.sh                    # sets ONTOLOGY_CONFIG_DIR/ONTOLOGY_PROJECT_ROOT, runs the demo
├── run_demo.py                    # exercises 5 config-injected capabilities
└── ontology/                      # the consumer's Layer 2 config (its own YAML)
    ├── tool_ontology.yaml         # WeatherBot's tools (get_forecast, save_favorite_city, ...)
    ├── domain_ontology.yaml       # WeatherBot's actors / resources / providers
    ├── policy_ontology.yaml       # WeatherBot's policies (max-cities-per-query, units-whitelist)
    ├── governance_ontology.yaml   # WeatherBot's invariants (audit weatherbot.py)
    └── convergence_ontology.yaml  # WeatherBot's declared→runtime convergence spec (chunk-3)
```

## Why this proves config-injection

`run_demo.sh` exports:

```bash
export ONTOLOGY_CONFIG_DIR="$DEMO_DIR/ontology"      # engine reads WeatherBot's YAML
export ONTOLOGY_PROJECT_ROOT="$DEMO_DIR"             # checks resolve against WeatherBot's files
```

Then the **same engine code** that governs `openclaw-model-bridge`:

1. queries WeatherBot's tools (`get_forecast`, not the bridge's `data_clean`),
2. finds WeatherBot's actors (`weather_bot`),
3. resolves WeatherBot's policy (`max-cities-per-query`, limit 3),
4. audits WeatherBot's invariants against `weatherbot.py` — and passes,
5. runs WeatherBot's **convergence** spec (`weatherbot-allowed-units-active`),
   reading `convergence_ontology.yaml` + `weatherbot_state.json` from the consumer
   — zero drift (V37.9.107 chunk-3: the convergence phase is now config-injected too).

No engine code was edited. That's the whole point.

## Full接入指南

See [`docs/ontology_engine_extension_guide.md`](../../docs/ontology_engine_extension_guide.md)
for the 60-second guide, check-type reference, and how to write a convergence spec.
