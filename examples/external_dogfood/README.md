# external_dogfood — out-of-repo dogfood for `openclaw-ontology-engine`

This example proves the **published engine is consumable by a third party who has
never seen this monorepo** — the remaining gap external-reviewer-2 flagged
("ontology dogfood WeatherBot demo exists + PyPI published; the gap is an
*independent, out-of-repo* dogfood").

It is a regression-guarded escalation of the one-time PyPI smoke test done in
V37.9.137: instead of "imported once from PyPI", it builds the wheel, installs it
into an **isolated venv**, copies a brand-new toy project **out of the repo**, and
runs the engine against it — every CI run.

## Why this differs from `examples/minimal_consumer`

| | `minimal_consumer` (WeatherBot) | `external_dogfood` (LibraryBot) |
|---|---|---|
| engine on path via | `PYTHONPATH=repo` (editable-like) | **wheel installed into an isolated venv** |
| import name in demo | `import ontology` (in-repo) | **`import ontology_engine`** (distribution name) |
| toy project location | inside the repo | **copied OUT of the repo to `/tmp`** |
| what it proves | config-injection works | a true third party can `pip install` + run with **no monorepo access** |

The de-genericized import name (`ontology_engine`, V37.9.128 chunk-2-lite) only
matters for a real consumer who installed the wheel — which is exactly what this
dogfood exercises. `minimal_consumer` can't catch a break there because it imports
the in-repo `ontology` package.

## What it verifies

1. The wheel **builds offline** and installs into an isolated venv with no monorepo on the path.
2. `import ontology_engine` (+ `.engine` / `.governance_checker` / `.convergence` / `.three_gate`)
   resolves to the **venv wheel**, not `/.../openclaw-model-bridge/ontology` (no leakage).
3. Console scripts `openclaw-ontology-audit` / `openclaw-ontology-query` are registered.
4. **Config-injection** (`ONTOLOGY_CONFIG_DIR` + `ONTOLOGY_PROJECT_ROOT`) makes the engine
   audit **LibraryBot** (a fresh domain ≠ bridge ≠ WeatherBot): query → LibraryBot tools,
   audit → LibraryBot invariants pass (exit 0), convergence → LibraryBot spec, zero drift.
5. **Reverse-validation**: the *same* wheel WITHOUT injection falls back to its **own bundled
   defaults** (the bridge's 19 tools incl. `data_clean`, no `checkout_book`) — proving
   config-injection (not `PYTHONPATH`, not the monorepo) is what loads the toy.

## Run it

```
bash examples/external_dogfood/run_dogfood.sh
```

Everything happens in a `/tmp` working dir (wheel build artifacts, venv, and the
out-of-repo copy of `project/`); the repo is left untouched. Expected tail:

```
🎉 PASS — openclaw-ontology-engine is consumable out-of-repo from a wheel.
```

## Layout

```
external_dogfood/
  run_dogfood.sh                  # build wheel → isolated venv → install → run out-of-repo → reverse-validate
  project/                        # the LibraryBot third-party consumer (copied to /tmp to run)
    librarybot.py                 # audited source (MAX_BOOKS_PER_CHECKOUT / ALLOWED_GENRES)
    librarybot_state.json         # convergence source file (allowed_genres)
    run_demo.py                   # exercises 5 capabilities via `import ontology_engine`
    ontology/
      tool_ontology.yaml          # LibraryBot tools
      domain_ontology.yaml        # LibraryBot actors / resources / providers
      policy_ontology.yaml        # max-books-per-checkout / genre-whitelist
      governance_ontology.yaml    # INV-LIBRARY-CHECKOUT / INV-LIBRARY-GENRES (audit librarybot.py)
      convergence_ontology.yaml   # librarybot-allowed-genres-active
```

## Offline note

The sandbox blocks PyPI, so the wheel is **built offline** (the engine layer needs
only `setuptools` to build) and installed `--no-deps`. PyYAML — the engine's one
runtime dependency — is supplied by a `--system-site-packages` venv. The isolation
that matters (no monorepo *source*) still holds: the system has neither `ontology`
nor `ontology_engine` installed, so `import ontology_engine` resolves unambiguously
to the venv wheel. A real third party with network would simply run
`pip install openclaw-ontology-engine` (which pulls PyYAML).

The regression guard `test_external_dogfood.py` runs the source-level checks always
and the full build+venv+install end-to-end where the toolchain (setuptools/venv) is
available, skipping gracefully otherwise.
