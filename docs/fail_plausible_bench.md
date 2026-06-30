# 🔬 Fail-Plausible Detection Bench

> A community-runnable benchmark for **silent-failure / fail-plausible** detection in production LLM agent runtimes.
> Companion to the paper [*When Errors Become Narratives* (arXiv:2606.14589)](https://arxiv.org/abs/2606.14589) and a sibling of the fault-injection [`reliability_bench`](reliability_bench_report.md).

**bench_id**: `fail-plausible-detection-bench` · **bench_version**: `0.1` (dataset + metric contract, independent of project VERSION)

---

## Why this bench exists

The paper's headline finding (also its biggest open problem): **~70% of silent failures in this runtime were found by a human looking at the product — tests, governance audits, and health checks caught ≈0%.** Thousands of green tests and hundreds of governance checks could not see them, because the failures were **semantic**: an LLM narrated an internal error into a fluent, plausible-looking output and pushed it to the user.

We call this class **fail-plausible** — the observer is not just blind, it is *persuaded by the failure itself*. This bench measures whether an automated **user-viewpoint observer** can mechanize the human eye and catch fail-plausible artifacts *before* a person reads the product.

The bench is the **breadth** deliverable: it turns "we caught these in our system" into something **others can run, reproduce, and contribute new cases to**.

---

## What it measures

The runner ([`llm_observer_selfcheck.py`](../llm_observer_selfcheck.py)) evaluates the deterministic **Layer 1** detector against a labeled corpus and emits a scorecard. All headline metrics are **offline** (zero LLM, zero network).

| Metric | Current | Sample | Target | Meaning |
|--------|---------|--------|--------|---------|
| **defense rate** | 100% | 6/6 | →100% | Category A (regression) cases flagged by deterministic Layer 1 |
| **false-positive rate** | 0% | 0/4 | →0% | Clean outputs falsely flagged (noise is itself a problem) |
| **false-negative rate (Category B)** | 100% | 4/4 | honest report, not a gate | Held-out/novel cases missed (the open problem — see below) |
| detection latency | N/A | — | paper #2 | Hours the observer beats the human eye (production-only) |
| confidence calibration | N/A | — | Stage 5/6 | Layer 2 LLM-judge calibration (live-LLM only) |

The **Category B false-negative rate is high by design and is reported honestly, not gated**: Layer 1 is a *regression engine*, not a *prediction engine*. It catches the fail-plausible patterns it was built for and is systematically blind to novel ones — exactly the [audit-as-regression](articles/audit_is_regression_not_prevention.md) logic applied to the observer itself. Novel detection has to come from somewhere else (Layer 2 semantic grounding, the human eye, or new detection rules). The bench does not pretend otherwise.

---

## How to run

No dependencies beyond Python 3 standard library for the offline Layer 1 scorecard.

```
git clone https://github.com/bisdom-cell/openclaw-model-bridge.git
cd openclaw-model-bridge
python3 llm_observer_selfcheck.py              # human-readable scorecard
python3 llm_observer_selfcheck.py --json       # raw per-case results + scorecard + sabotage
python3 llm_observer_selfcheck.py --sabotage   # only the load-bearing sabotage suite
python3 llm_observer_selfcheck.py --manifest   # stable machine-readable bench datasheet
python3 llm_observer_selfcheck.py --save       # write docs/llm_observer_scorecard.md
```

The exit code is a regression gate: `0` iff defense rate is 100%, false-positive rate is 0%, and every detector is load-bearing. Category B false-negatives never fail the gate.

### Reproducibility — the manifest

`--manifest` emits a **stable, timestamp-free** JSON datasheet: the bench id/version, corpus category counts, the detector inventory, each metric's definition/target/current value, and the sabotage summary. The same corpus + detectors produce a byte-identical manifest on any machine, so a contributor can diff their before/after manifest to see whether a change moved the bench.

---

## Corpus structure

The corpus is the **seed** for the community bench. Category A cases are bound to the labeled ground-truth at [`docs/llm_observer_ground_truth.yaml`](llm_observer_ground_truth.yaml) (single source of truth — 22 paper-canonical incident postmortems + 5 golden seeds).

| Category | Count | Contract |
|----------|-------|----------|
| **A** — regression | 6 | A real historical fail-plausible artifact. Observer **must** catch it (deterministically). Each is bound to a `golden_seed` in the ground-truth and tagged with the single detector it relies on (`only_signal`) for sabotage. |
| **clean** — false-positive control | 4 | A legitimate synthesis output. Observer must **not** flag it. |
| **B** — held-out / novel | 4 | A genuinely fail-plausible output for which **no detection rule was designed**. Measures the honest held-out recall (the open problem). |

### Detectors (Layer 1, deterministic)

The Category A cases exercise the deterministic S-rules: `pollution_signal` (error-code / `Bad JSON` / alert-artifact fingerprints), `coherence_structural` (boilerplate repetition, all-heading-no-body), `fabrication_phrase` (blood-lesson exact phrases), and `provenance_gap` (forced-equivalence idioms without an evidence tag). Each rule is **sabotage-validated**: disabling it must make its golden case slip from flagged to clean — proving the detector is load-bearing and not a no-op.

---

## Contributing a case (the breadth angle)

New fail-plausible cases are the most valuable contribution — they widen the corpus beyond one system's incidents. To add one:

1. **Add an entry to `_CORPUS` in [`llm_observer_selfcheck.py`](../llm_observer_selfcheck.py)** with the schema:
   - `id` (unique), `category` (`A` | `clean` | `B`), `source` (job id or `None`), `text` (the artifact), `expect_flag` (bool).
   - **Category A also needs** `gt_id` (binding back to a `golden_seed` in `llm_observer_ground_truth.yaml`) and `only_signal` (the single deterministic detector it relies on, for sabotage).
   - **Category B** should add a `blind_spot` note explaining *why* Layer 1 cannot catch it (this documents the held-out frontier).
2. **Run the guards**: the corpus schema, the ground-truth binding, and the sabotage suite are all CI-enforced (`python3 test_llm_observer_selfcheck.py`). A Category A case that is not actually caught, or whose detector is not load-bearing, fails CI.
3. **A new fail-plausible pattern that Layer 1 cannot catch belongs in Category B**, not A — that is the honest place for novelty, and it feeds the backlog for new detection rules.

This is how the bench grows: known patterns harden Layer 1 (Category A regression), and novel patterns are honestly logged (Category B) until a rule or Layer-2 judge retires them.

---

## Honest limitations

- **Held-out recall is the open problem.** Whether the detector has *non-trivial* recall on Category B (patterns it was never designed for) is the real test of the "description → prediction" claim, and the bench reports it without spin.
- **detection_latency / confidence_calibration are N/A offline.** They need production observation (how many hours the observer beats the human eye) and live-LLM Layer 2 calibration. Those numbers come from production data, not this offline harness.
- **The observer is itself an LLM component** (paper §5.2): it inherits the whole taxonomy, so its own verdicts must cite deterministic evidence and survive sabotage. This bench is the observer's own sabotage-validation, applied to itself.

---

## Sibling bench

| Bench | Concern | Runner |
|-------|---------|--------|
| `reliability_bench` | Fault-injection resilience (provider down, tool timeout, malformed args, …) — 17 scenarios | [`reliability_bench.py`](../reliability_bench.py) |
| **`fail-plausible-detection-bench`** | Semantic silent-failure detection (this doc) | [`llm_observer_selfcheck.py`](../llm_observer_selfcheck.py) |

Together they cover both halves of "does the runtime fail safely *and* does it tell the truth when it fails."

---

*Generated as part of research thrust #1 (mechanizing the human eye / LLM-Observer), Stage 6. Corpus and scorecard are reproducible offline; see [`docs/llm_observer_design.md`](llm_observer_design.md) for the full design.*
