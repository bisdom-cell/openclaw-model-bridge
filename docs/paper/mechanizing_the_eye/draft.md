# Mechanizing the User's Eye: Pre-Registered Deployment of a Sabotage-Validated Fail-Plausible Observer in a Production LLM Agent Runtime

> **v1.0 — 2026-08-28 (submission version; v0.1 first pass 2026-08-12)**
> Follow-up to *When Errors Become Narratives*
> ([arXiv:2606.14589](https://arxiv.org/abs/2606.14589)). Venue (user decisions,
> 2026-08-28): **arXiv direct submission, cs.SE primary + cross-list cs.AI**;
> quiet-window version submitted now per the §9.2.5 negative-results commitment;
> journal targeting is a separate later step. Title = option 1 (decided).
> All production numbers frozen at the pre-registered study cutoff **2026-08-09 23:59 HKT**
> per the analysis protocol registered 2026-07-31 (`docs/llm_observer_design.md` §9.2),
> *before* the window data was read. Repository traceability in `data_inventory.md`.
> Post-cutoff developments appear only in the clearly-labeled Postscript.

**Authors:** Wei Wu (Independent researcher), with AI engineering collaborator
disclosure mirroring paper #1 (title-page footnote + Acknowledgments; confirmed
2026-08-28, paper #1 precedent applied unchanged).

---

## Abstract

In a previous longitudinal study of silent failures in a production LLM agent runtime
(*When Errors Become Narratives*, arXiv:2606.14589) we reported an uncomfortable finding:
roughly 70% of silent failures were ultimately discovered by a human looking at the
product as a user, while thousands of unit tests and hundreds of governance checks stayed
green — and we posed *mechanizing even part of what the human eye does* as an open
problem. This paper reports our attempt. We built an automated user-viewpoint observer
targeting the taxonomy's most dangerous class, **fail-plausible** failure, in which the
system transforms an internal error into fluent, plausible output delivered to the user.

The observer is a two-layer pipeline: five deterministic signals distilled from incident
postmortems escalate to an LLM judge whose verdicts must cite verbatim evidence from the
artifact or be discarded. Ground truth comes from 24 labeled production postmortems, with
explicit honesty boundaries — 16 of 24 incidents are structurally invisible to any
content-reading observer, and we say so rather than claim them. Offline, the
deterministic layer achieves 6/6 regression detection with 0/4 false positives, every
detector proven load-bearing by sabotage; held-out recall on novel patterns is 0/4. The
observer is, so far, a regression engine, and the scorecard reports that without spin.

Deployment followed a protocol borrowed from experimental science: **pre-registration**.
Shadow mode caught and retired one systematic false positive on its first production
run, after which the registered 26-day shadow window ran clean; flip criteria were
registered before the shadow data was read; the analysis
protocol — regime rules, exclusion rules, a negative-results commitment — was frozen
before the enforcing-mode window opened. That window (12 observed days) fired zero
verdicts: per the pre-registered path we report live precision as **undefined** (zero
denominator) rather than narrating quiet as success. Meanwhile the observer produced
**eight silent failures of its own** during its development — including polluting its own
evidence file and an enforcing-mode integration that was silently inert — empirically
confirming the prior paper's warning that the judge inherits the taxonomy it judges. We
release the labeled corpus, detector, and scorecard as a community-runnable bench, and
argue that what mechanization buys today is retiring the human's *regression scanning*
so the human eye can specialize in novelty — prediction remains open, but it is now
measurable, under rules already frozen.

---

## 1. Introduction

The predecessor to this paper documented 22 production incidents in a continuously
operating LLM agent runtime and derived a five-class taxonomy of *silent failures* —
failures whose error signal never reaches a human in actionable form [Wu 2026]. Its
headline empirical finding doubled as its largest open problem. Across the corpus,
roughly 70% of silent failures were finally discovered by **a human reading the
product as a user**: noticing that a digest "looked shallow," that a signal section and
an action section did not match, that yesterday's analysis never arrived. The automated
stack — at the time 4,286 unit tests, 827 governance checks, a 19-point preflight —
stayed green through most incidents. We institutionalized a weekly 30-minute user-view
observation ritual, watched it out-detect the automation for two more months, and wrote:

> *"for practitioners, user-view observation is a first-class observability signal and
> deserves calendar time; for researchers, the open problem is mechanizing even part of
> what the human eye does here."*

This paper reports what happened when we tried. We scoped the attempt to the taxonomy's
Class D — **fail-plausible** failure, in which an LLM system does not merely fail to
report an error but *transforms* it into coherent, contextually appropriate, false
output. Fail-plausible incidents are the ones the human eye caught that nothing else
could have: a fabricated "platform crisis" analysis synthesized from captured error
pages; fabricated OS remediation instructions woven from stale alert context; a
fallback path manufacturing plausible-shaped review content in shell. They are semantic
failures. Detecting them requires reading *content*, not status codes — which is
precisely why the test/check stack scored ≈0% on them.

Three design commitments distinguish our attempt from a generic LLM-as-judge deployment:

**First, determinism before semantics.** The observer is a two-layer pipeline. Layer 1
is five deterministic signals (error-code pollution, credibility mismatch, blood-lesson
fabrication phrases, provenance gaps, structural incoherence), each distilled from a
specific documented incident, each cheap, each explainable, each *sabotage-validated* —
disabling a rule must make the historical incident it guards slip through, or the rule
is dead weight. Layer 2, an LLM judge, runs only on escalation, and its verdicts must
cite evidence that appears **verbatim** in the artifact under judgment; ungrounded
evidence is dropped, and a verdict left without grounded evidence is downgraded to
clean. The grounding rule exists because our own observer hallucinated a defect within
its first week of operation (§7).

**Second, ground truth with honesty boundaries.** The prior paper's 22-incident
postmortem corpus (since grown to 24 labeled incidents) is, to our knowledge, the only
longitudinal, causally annotated silent-failure corpus for a production LLM agent
system. We turned it into a labeled validation set — and found that only 3 of 24
incidents are cleanly within a content-reading observer's reach, with 5 more partially
so. The remaining 16 have no user-facing semantic artifact at all: deployment omissions,
OS sandbox denials, a gateway dying silently. An observer that reads pushed content is
structurally blind to them. We label this per-incident (`observer_in_scope`) rather
than let the observer implicitly claim the whole corpus.

**Third, pre-registration.** Promoting a detector from shadow observation to enforcing
mode — where its verdicts affect quality scores and page a human — is a decision with
real costs in both directions, taken by the same people who built the detector and want
it to succeed. That is a textbook motivated-reasoning surface. We imported the remedy
experimental science uses [Nosek et al. 2018]: flip criteria registered before reading
shadow data; an analysis protocol (regime rules, exclusion rules, what counts as a
true/false positive, and a commitment to publish negative results) frozen before the
enforcing-mode window opened; and mechanical execution of those rules when the data
arrived — including the rule applications that hurt (§6.6).

The results are deliberately mixed, and we believe the mixture is the contribution:

1. **An observer architecture** (§3) for fail-plausible detection — deterministic
   pre-filter, evidence-grounded LLM judge, read-only by construction, integrated into
   an existing daily observation job rather than added as a new one.
2. **A labeled ground-truth corpus** (§4) derived from production postmortems, with
   per-incident scope honesty, released with the detector.
3. **A sabotage-validated offline scorecard and community bench** (§5): 6/6 regression
   detection, 0/4 false positives, 0/4 held-out recall — the last number reported as an
   honest measurement of the description→prediction gap, not gated away.
4. **A pre-registered deployment study** (§6): a shadow phase whose first production
   run caught a systematic false positive (fixed inside shadow; the registered 26-day
   window then ran clean), registered flip criteria, a frozen analysis protocol, and a
   12-day enforcing-mode window that fired zero verdicts — reported as an
   *undefined-precision quiet window* per the registered path, not as success.
5. **The observer's own incident log** (§7): eight silent failures the observer itself
   produced during development — including contaminating its own evidence file and an
   enforcing-mode integration that was silently inert — which we offer as the strongest
   empirical support yet for the prior paper's claim that an LLM judge inherits every
   failure class of the system it judges.

We close (§8) with what we think mechanization has actually bought: not prediction —
held-out recall is 0/4 offline and untested live — but the retirement of the human's
*regression scanning*. The observer now re-checks every known fail-plausible pattern
daily, within 24 hours of artifact production, against incidents whose historical
detection latencies ran 13 hours to 60 days. The human eye is repositioned where the
audit-as-regression argument says it is irreplaceable: novelty. Whether the instrument
can cross from regression to prediction is now a measurable question with frozen rules,
an open bench, and a corpus that grows with every incident.

---

## 2. Background and Related Work

### 2.1 The prior study, compressed

*When Errors Become Narratives* [Wu 2026] derived five mechanism classes from 22
production incidents: (A) environment/platform quirks, (B) design-assumption
mismatches, (C) error swallowing and dilution, (D) chained hallucination and
fabrication — fail-plausible — and (E) operational omission and forensic blind spots.
Three cross-cutting findings matter here. *Discovery*: ~70% of incidents were caught by
human user-view observation; tests and checks ≈0% for this failure class. *Audit is a
regression engine*: a retrospective audit found 0% ex-ante prevention but 87% ex-post
regression blocking — audits encode the past, and prevention of novel classes must come
from elsewhere. *Seams, not components*: the longest-lived failures inhabited the seams
between correct parts (deployment topology, cross-script contracts, observer–observed
coupling), where by construction no test runs. All three findings constrain the present
work: the observer targets the discovery gap, is honestly framed as a regression engine
with predictive ambitions, and is itself a new seam — which §7 shows is not a
hypothetical concern.

### 2.2 LLM-as-judge, and why we distrust our own

LLM judges reach striking agreement with human preference on open-ended quality
[Zheng et al. 2023], which grounds the hope that an LLM can read a digest the way a
user would. But the judge literature also documents self-preference: LLM evaluators
recognize and favor their own generations [Panickssery et al. 2024]. Our setting is a
close cousin of that hazard — the observer judges outputs produced by sibling LLM
pipelines in the same runtime, is configured by the same operators, and (the prior
paper's §5.2 warning) *is itself an LLM component inheriting every taxonomy class*.
Our response is architectural rather than aspirational: determinism first (Layer 1
verdicts are lexical and auditable), verbatim evidence grounding for the LLM layer
(§3.3), read-only operation enforced as a standing rule, and the same sabotage
validation applied to the observer as to everything else. Section 7 reports how many
times those constraints earned their keep.

### 2.3 Pre-registration

Pre-registration — committing to hypotheses and analysis plans before observing data —
exists because analytic flexibility plus motivated reasoning reliably manufactures
positive results [Nosek et al. 2018]. Software engineering has already imported the
instrument on the *publication* side: registered reports, where a study protocol is
peer-reviewed before results exist, have run as a track at MSR since 2020 and are
established at several SE venues [Ernst & Baldassarre 2023]. Our use is adjacent but
distinct — the pre-registration here is an *operational* one, binding an engineering
decision (promote a detector from shadow to enforcing) and its post-hoc measurement,
with the team itself as the only reviewer and a production system rather than a study
as the object.

Observability engineering has the same disease surface as empirical research, with
different symptoms: the team that builds a detector decides when it is "ready,"
interprets its early signals, and later writes the narrative. Existing runtime-guard
frameworks for LLM systems specify *what* a guard enforces — programmable rails and
policy DSLs [Rebedea et al. 2023] — but not how a team should decide, in advance and
against its own incentives, that a guard has earned enforcement authority. We did not
find a report of a production observability component deployed under an explicitly
pre-registered protocol of the kind we describe (shadow criteria, flip criteria, frozen
analysis rules, negative-results commitment), and we make the weaker claim that this is
uncommon rather than unprecedented. We describe ours in §6 in enough detail to be
reused, and we report the three occasions where the frozen rules overrode what
narrative convenience would have preferred (§6.6).

### 2.4 Gray failure lineage

Gray failure named the cloud-era gap between an application's experience and its
observer's view — *differential observability* [Huang et al. 2017]. The prior paper
positioned fail-plausible as the LLM-era escalation: the observer is not starved of
signal but fed a counterfeit one. Under that framing, the present work is an attempt to
give the human's side of the differential a mechanical ally on the semantic axis — a
detector that reads the counterfeit the way the deceived human eventually does, but on
a daily clock. MAST [Cemri et al. 2025] and related agent-failure taxonomies
characterize what goes wrong inside agent traces; our observer instead watches the
*products* of an agent system in operation, because that is where fail-plausible
manifests and where the human eye historically caught it.

---

## 3. The Observer

### 3.1 Design constraints

Five constraints, each traceable to a documented incident or standing rule:

- **Read-only.** The observer never mutates what it observes. The system's convergence
  engine — an *audit that applied fixes* — caused three incidents before being caged
  behind a dry-run default, yielding the standing rule "audit observes, never mutates."
  The observer is born under that rule.
- **Extend, don't parallel.** The runtime already ran a daily LLM critique job
  (06:30, quality heuristics, prose output for humans). The fail-plausible detector is
  wired *into* that job — same scan, same report, same status plumbing — rather than
  shipped as a second observer. A parallel observer would add a new job, a new status
  file, a new watchdog contract, and an observer–observer seam; the prior paper's
  complexity argument (add a part, add seams) forbids it.
- **Evidence or silence.** Layer 2 verdicts must cite evidence that appears verbatim in
  the artifact. Ungrounded evidence is dropped; a verdict with no grounded evidence
  left is downgraded to clean. An accusation the operator cannot verify by searching
  the artifact is worse than no accusation.
- **Sampling-aware.** Large artifacts are sampled (head + tail with an explicit
  elision marker) before being shown to the judge, and the judge is told so. This
  constraint was learned, not designed: the observer's first false accusation was a
  hallucinated "truncation" at exactly the sampling boundary (§7, O2).
- **Cheap by default.** Layer 2 runs only when Layer 1 fires (or under an explicit
  force flag, off by default). A clean day costs zero LLM calls. Detection economics
  decide whether an observer survives contact with a budget; §6's window recorded
  zero Layer-2 cost across 12 clean production days.

### 3.2 Layer 1: five deterministic signals with incident lineage

Each signal is a deterministic rule over artifact text, each with a named incident it
would have caught — the corpus is not just evaluation data; it is the design source.

| Signal | Fires on | Incident lineage (prior-paper class) |
|---|---|---|
| S1 `pollution_signal` | Error codes, HTTP status text, `Bad JSON`, retry diagnostics, alert markers appearing inside user-facing content | D1: a 400 error page laundered into a "platform crisis" analysis; D2: alert artifacts surfacing in chat answers |
| S2 `credibility_mismatch` | Low-tier sources (blog/social) wrapped in high-certainty phrasing ("research shows", "proven") | Provenance-tier module lineage; D4 family (unlabeled context as fabrication fuel) |
| S3 `fabrication_phrase` | Exact blood-lesson phrases from documented fabrications (e.g., a release announcement string for a version that exists only in an internal changelog) | D4: fabricated community release |
| S4 `provenance_gap` | Forced-equivalence idioms (cross-domain "X is essentially Y" claims) without a required evidence tag (`[strong-evidence]` / `[weak-association]`) | Echo-chamber incident: flattering forced analogies presented as insight |
| S5 `coherence_structural` | Boilerplate repetition (same filler ≥3×), all-heading-no-body sections, field values equal to separators | D3: quota-exhaustion stubs shaped like content; the review job emitting container headings as findings |

Two properties matter more than the rules themselves. *Reuse*: S2, S3, and S4 do not
re-implement their knowledge — they import the runtime's existing provenance-tier
module and anti-fabrication guard ladder, so the observer and the generation-side
defenses share one source of truth and cannot drift apart. *Sabotage validation*:
disabling any rule must cause the specific historical artifact it guards to slip from
flagged to clean (§5); a rule that survives its own disabling is indistinguishable
from dead code and is treated as such.

### 3.3 Layer 2: the grounded judge

When Layer 1 fires, the artifact (with sampling disclosure) goes to an LLM judge with
a dedicated system prompt scoring four dimensions, each a fingerprint of a documented
incident family:

- **grounding** — can each factual assertion be supported from the source material?
- **intent-alignment** — is the output answering something nobody asked? (the
  fabricated-remediation fingerprint)
- **pollution-evidence** — are system artifacts (error text, tool names, HTTP status)
  being treated as external signals? (the semantic version of S1)
- **fabricated-success** — is this a fallback-manufactured shell with the shape of
  content but no content? (the shell-fabrication fingerprint)

The prompt injects the runtime's own anti-fabrication guard text and source-credibility
tiers — the judge is constrained by the same rules the generators are. The verdict
parser then enforces the grounding rule mechanically: every evidence snippet the judge
cites is searched for, verbatim, in the artifact; snippets not found are discarded; a
fail-plausible verdict with no surviving grounded evidence is downgraded to clean. The
rule costs recall and we accept that: an observer for fabrication that itself
fabricates evidence is self-refuting, and ours demonstrably tried (§7, O2).

### 3.4 Verdict contract and integration

Verdicts are structured objects — severity, category, artifact, evidence list (layer,
signal, locus, snippet), confidence, human-readable message — merged into the daily
job's existing anomaly stream, so reports, score history, shared status, and alerting
all inherit fail-plausible awareness without schema breaks. A mode flag governs blast
radius: `shadow` (verdicts reported in a labeled section, no effect on scores or
alerts) and `on` (verdicts join the anomaly stream that drives scores and paging).
The flag's default is shadow; §6 is the story of earning `on`.

### 3.5 Non-goals

The observer does not attempt novelty prediction (its Layer 1 is, by construction, a
regression engine; whether Layer 2 generalizes is an open measurement, §5.3); it does
not read components (4,000+ unit tests own those); it does not act on its verdicts
(read-only; a human decides). It also does not replace the weekly human observation
ritual — §8 discusses what it retired and what it deliberately did not.

---

## 4. Ground Truth from Postmortems

### 4.1 From narrative corpus to labeled validation set

The corpus at labeling time: 28 postmortem documents, of which 24 are incident
postmortems carrying machine-readable labels (22 fall inside the prior paper's study
window; 2 were closed after its cutoff), 3 are non-failure analyses (architecture
reviews, decision records) excluded by definition, and 1 later incident postdates the
labeling pass. Labels live in a single YAML table (one file, not 24 front-matters —
one logical entity, one physical representation), with per-incident fields:

- `taxonomy_class` (A–E), `fail_plausible` (yes / partial / no),
  `llm_fabrication` (yes/no — one fabrication was implemented entirely in shell),
- `observable_artifact` (what a content-reading observer could actually see, and
  whether it is user-facing),
- `discovery_channel` (user-view / check / log-forensics / self-observation),
- `observer_in_scope` (yes / partial / no) with a written reason,
- `expected_signal` (which S-rules and judge dimensions should fire — the field that
  turns a label into a testable assertion),
- category (A regression / B held-out / out-of-scope), golden-seed flag, and the
  prior paper's silence-span bucket.

### 4.2 The honesty split

The most consequential labeling decision was `observer_in_scope`:

| Scope | Count | Meaning |
|---|---|---|
| yes | 3 | The failure's artifact is exactly what the observer reads (pushed synthesis content) |
| partial | 5 | The mechanism is in scope but the artifact lives partly outside the scanned set (e.g., live chat turns) |
| no | 16 | No user-facing semantic artifact exists — deployment omissions, OS sandbox denials, silent process death |

Two-thirds of the corpus is structurally invisible to any content-reading observer.
We consider stating this plainly a feature of the method: an observer evaluated only on
the incidents it could possibly see would look far better than it deserves, and the
per-incident scope label is what prevents that inflation. It also quantifies a design
truth the prior paper argued qualitatively: most silent failure lives in seams and
operations, not in content — a content observer is one instrument, not a panacea.

### 4.3 Categories and seeds

From the in-scope incidents we derive the bench corpus (§5): **Category A** (6
regression cases — real historical fail-plausible artifacts the observer must catch
deterministically, each bound to a ground-truth entry and tagged with the single
detector it relies on, for sabotage); **clean** (4 legitimate synthesis outputs the
observer must not flag — false-positive control); **Category B** (4 held-out cases:
genuinely fail-plausible outputs for which no detection rule was designed — fabricated
conference acceptances, paraphrased pollution with no literal error strings, fabricated
co-authorship, unsupported strong claims from high-tier sources).

### 4.4 Labeling limitations

Labels were produced by the system's two operators (human + AI collaborator); no
independent annotators, no κ — inherited honestly from the prior paper, with the same
mitigation: the labels are load-bearing, not descriptive. Each `expected_signal` drives
a regression test whose outcome ("does the observer flag this artifact?") is a binary
fact, and each Category A label drives a sabotage test that fails CI if the label and
detector disagree. Mislabeled ground truth does not silently bias a statistic; it
breaks a build.

---

## 5. Offline Evaluation and the Bench

### 5.1 Scorecard

The offline harness (zero LLM, zero network — Layer 1 only) evaluates the corpus and
emits a scorecard; the numbers below are the current published values, reproducible
from the repository at the study cutoff:

| Metric | Value | Sample | Reading |
|---|---|---|---|
| Defense rate (Category A) | **100%** | 6/6 | Every known in-scope fail-plausible artifact is caught deterministically |
| False-positive rate (clean) | **0%** | 0/4 | No noise on legitimate synthesis output |
| Held-out recall (Category B) | **0%** (FN 4/4) | 0/4 | No novel pattern is caught by Layer 1 — reported, not gated |
| Detection latency | N/A offline | — | Production-only (§6) |
| Confidence calibration | N/A offline | — | Requires live Layer 2 (§6) |

### 5.2 Sabotage: every detector is load-bearing

For each Category A case, the harness disables the single detector the case relies on
and re-runs: the case must slip from flagged to clean. All six do. This is chaos
engineering applied one level up — injecting faults into the *guards* rather than the
system — and it exists because this runtime once discovered 67 governance checks that
had been executing empty strings for months. In a silent-failure regime, an unvalidated
detector is indistinguishable from a vacuous one; sabotage is the distinguisher. The
harness exit code is a CI gate: defense must stay 100%, false positives 0%, every
detector load-bearing. Held-out misses never gate — Category B is a measurement, not a
target, and gating it would only pressure someone to quietly reclassify hard cases.

### 5.3 What FN 4/4 means

The held-out result is the audit-as-regression finding pointed at our own instrument.
Layer 1 catches exactly the patterns it was built from and is systematically blind to
patterns it was not — a fabricated conference acceptance contains no error strings, no
blood-lesson phrases, no forced-equivalence idioms; nothing lexical distinguishes it
from a true report. Novel detection must come from Layer 2 semantics (grounding against
source material), from the human eye, or from new rules earned by future incidents.
The bench design encodes this honesty: a contributed case that Layer 1 cannot catch
belongs in Category B *by rule*, documenting the frontier instead of debasing the
regression set.

### 5.4 The community bench

The corpus, detectors, harness, scorecard, and a byte-stable machine-readable manifest
are released as a runnable bench (stdlib-only for the offline layer): clone, run one
script, diff the manifest before/after a change to see whether the bench moved. The
contribution protocol is CI-enforced — a new Category A case must actually be caught
and its detector proven load-bearing; a new pattern the detector cannot catch is
honestly logged as Category B until a rule or judge retires it. The bench is the
breadth deliverable: it converts "we caught these in our system" into something others
can run, reproduce, and extend with their own incidents.

---

## 6. Production Deployment as a Pre-Registered Study

### 6.1 Why pre-register an observability component

Flipping the observer from shadow to enforcing mode changes real behavior: verdicts
join the anomaly stream, depress quality scores, and page a human. The people deciding
are the people who built it. Every incentive points one way — ship it — and the
counter-incentive (fear of alert noise) points the other with equal irrationality.
Both are motivated-reasoning surfaces, and both were live in our history: this runtime
had previously watched a "verified" mechanism turn out vacuous, and had also watched
useful defenses stall in permanent shadow out of caution. The remedy we imported from
experimental science [Nosek et al. 2018] is to decide *the rules* before seeing *the
data*, in three registered layers:

1. **Flip criteria** (registered before reading shadow data): C1 — no unfixed
   *systematic* false-positive pattern (daily/multi-source recurrence); C2 — signal
   usefulness: ≥1 confirmed true positive **or** a clean window (flipping a clean
   detector adds no noise; its value simply remains unproven); C3 — sustainable cost
   (clean days must cost ≈0 LLM calls). All three must hold. Rollback is a
   single-variable revert (mode flag back to shadow), which is what makes a
   lean-toward-flip posture defensible.
2. **Analysis protocol** (frozen 2026-07-31, before the enforcing window's data was
   read): field-level regime rules for every history row (which field, which value,
   assigns a row to shadow / on / boundary-excluded); a named exclusion rule for a
   known contamination source; window boundaries; the true/false-positive adjudication
   protocol (single-annotator, same-day, with an `inconclusive` bucket that never
   enters the precision denominator); a human-first race rule (any silent failure a
   human finds first is an observer false negative, logged and fed back); and a
   **negative-results commitment**: a quiet window must be reported as absence of
   evidence, never narrated as detection success — *the paper about fail-plausible
   must not itself fail plausibly*.
3. **Mechanical execution**: when data arrives, the rules run as written; discovered
   protocol defects are disclosed as appended amendments, never silently rewritten.

### 6.2 Shadow phase: one catch on day one, then 26 clean days

Shadow mode's **first production run** validated the phase's existence: the
deterministic boilerplate-repetition signal (S5) mis-fired on a legitimately
repetitive star-rating field shared by roughly ten structured content sources — a
*systematic* false positive that would have recurred daily in enforcing mode, burying
real signal and depressing scores. It was diagnosed and fixed the same day, inside
shadow: the rule gained a descriptive-character threshold separating rating-field
repetition (legitimate structure) from descriptive-sentence repetition (the
fabrication-shell fingerprint it was built for), a regression guard was added, and the
offline scorecard was re-run (defense still 6/6, false positives still 0/4). The
registered shadow window (2026-06-30 → 07-25, 26 days) then ran clean: zero fired
verdicts. One detail deserves precision: the mis-fire occurred on the wiring-day run
that preceded the registered window's opening, so the window's 26/26 clean days are
all post-fix — which is exactly the state C1 asks a shadow phase to demonstrate.

### 6.3 The flip decision

On 2026-07-24 the shadow data was pulled and the registered criteria were checked
mechanically: C1 ✓ (the one systematic FP had been fixed in shadow; 14 consecutive
final days clean); C2 ✓ (clean-window branch); C3 ✓ (clean days cost zero Layer-2
calls). Decision: flip. The mode flag went live on 07-25; the first enforcing-mode
production day was 07-26. Two things about this decision are worth reporting precisely.
First, it exercised the *clean-window branch* of C2 — we flipped a detector whose live
value was unproven, because the registered criteria said a clean detector is safe to
promote and waiting indefinitely for a positive is its own bias. Second, the act of
grounding the flip mechanism before flipping caught two latent defects (§7, O4–O5):
the environment plumbing that was supposed to carry the mode flag to the process did
not exist, and the observer's own evidence file was being contaminated by its own test
suite. A flip executed on vibes would have silently flipped nothing and then analyzed
contaminated data.

### 6.4 The frozen analysis protocol, executed

The enforcing window closed per protocol (study cutoff 2026-08-09 23:59 HKT). Regime
accounting, mechanically per the frozen field rules:

| Regime | Window | Rows | Assignment rule | Fired |
|---|---|---|---|---|
| shadow | 06-30 → 07-25 | 26/26 days | fp fields present, mode field absent, date ≤ 07-25 | 0 |
| on | 07-27 → 08-07 | 12 rows | mode field == "on" | 0 |
| boundary-excluded | 07-26 | 1 row | date > 07-25 with mode field absent (field deployed mid-day) → registered plumbing-boundary rule | (0) |
| out-of-window | 06-29 | 3 rows | wiring-day runs predating the shadow window (one carries the §6.2 false positive) | — |

The named contamination-exclusion rule matched **zero** rows — the contamination source
had been eliminated at the flip-grounding step (§7, O4), and the rule's emptiness is
itself a verification that the cleanup held. The enforcing window's designed span was
14 days (07-26 → 08-08); observed coverage is 12/14: one day lost to the registered
boundary exclusion, and the final day lost because the host machine was shut down (the
next morning's run would have written it) — a benign outage, root-caused with the
operator, reported as a gap rather than backfilled. A single-host observation boundary
is noted: when the host sleeps, the observer and its watchdog sleep together.

### 6.5 Results under the frozen rules

**H1 — live precision.** Zero fired verdicts in 12 enforcing-mode days (double-sourced:
per-day history fields and per-day report sections agree). The registered protocol
speaks plainly: *precision = TP / (TP + FP) is undefined at zero denominator, and no
precision number may be reported.* The precision evidence for this paper is therefore
corpus-side only (6/6 · 0/4 · 4/4 with sabotage), plus one operational claim the window
does support: twelve days of enforcing-mode integration produced no false-positive
storm, no score disturbance, and zero marginal LLM cost — the flip was safe, whether or
not it is yet useful.

**H2 — detection latency.** Constructive upper bound only: the observer runs daily at
06:30, so any caught artifact is caught ≤24h after production. Zero live true positives
means zero measured latencies. The honest comparison remains: historical human
detection of the corpus incidents ran 13 hours to 60 days (median in the multi-day
bucket); the observer's *design* latency undercuts all but the fastest human catch,
but this remains a property of the schedule, not evidence of detection.

**H3 — the human-first race.** In-window, zero in-scope silent failures manifested and
were caught by humans first — so zero observer false negatives were charged. But the
window was not incident-free, and the registered protocol requires reporting the
discovery-channel evolution separately rather than blending it into the race: two
rounds of adversarial *code* audit during the window (four-lens and three-lens sweeps,
14 findings and 15 candidates respectively, all triaged with grounded verification)
intercepted latent silent-failure mechanisms **before they manifested** in any
user-facing artifact — among them a memory-index family that could have silently
disabled a retrieval layer, and ghost vectors that would have had the assistant citing
deleted notes. These are exactly the class of failure that, in the prior paper's era,
manifested first and was human-caught later; in this window, the prevention channel
moved upstream of manifestation. The observer's race (catch manifested semantic
failures before the user) never started, partly because the pipeline feeding it
incidents has itself improved — a confound we register rather than resolve (§9).

### 6.6 Three times the frozen rules overrode convenience

Pre-registration earns its cost only when it binds. It bound three times:

1. **The boundary row.** The first enforcing-mode day (07-26) is the single most
   tempting row to include — it is the flip-day, the story's climax. The field rule
   excludes it (its regime field predates the field's deployment), so it is excluded,
   and disclosed as such.
2. **The label/field mismatch.** Two report files carried the enforcing-mode label one
   day before the history field existed. Narrative preference would call them "on";
   the registered rule says fields decide, labels do not; they were assigned by rule
   (shadow / boundary) and the discrepancy is disclosed with zero numeric effect.
3. **The zero denominator.** The strong temptation in a quiet window is to report
   "100% precision (no false positives!)". The frozen protocol forbids manufacturing a
   number from an empty denominator, and the paper you are reading complies.

We offer this subsection as the paper's methodological core: none of these three rule
applications required judgment at analysis time, because the judgment had been spent at
registration time — which is the entire point.

---

## 7. The Observer's Own Incident Log

The prior paper warned that an LLM judge "inherits every class in this taxonomy" and
therefore "needs the same governance, provenance hygiene, and sabotage validation as
the components it judges." During this study the warning stopped being theoretical:
the observer produced **eight documented silent failures of its own** between its first
deployment and the end of the study window. We report them as first-class data — to our
knowledge the first incident log *of* an observability component for LLM systems,
kept to the same postmortem standard as the system it watches.

| # | Incident | Taxonomy class | Silence | How it was caught |
|---|---|---|---|---|
| O1 | Registry-path fallback: all path candidates missed the production location; the observer silently scanned 3 disabled jobs and never wrote its scores to shared status — its "three-party consciousness anchor" design promise was unimplemented for 5 days while daily runs logged ✅ | B (assumption mismatch) | 5 days | Partially by itself: its own LLM critique mentioned a stale warning, prompting the human to trace the data path |
| O2 | Sampling hallucination: the observer sampled the first 2,000 chars of a healthy artifact, saw its own sample end mid-word, and confidently reported the artifact "truncated" | D (fail-plausible — by the observer) | 1 report cycle | Human forensics: the artifact was intact; the cut was the observer's own sampling boundary |
| O3 | Dry-run self-pollution: preview runs (`--dry-run`) persisted scores and status — operator previews contaminating the production record | C/E flavor | until audited | Adversarial audit of the observer's pipeline |
| O4 | Evidence contamination by its own test suite: CI tests ran the observer's CLI without home-directory isolation on the production host, appending fabricated failure rows (a fixture date, ~3×/day) into the *production* score history — the observer's shadow-phase evidence was polluted by its own tests | E (operational seam) | ~2 months | Discovered during the flip-decision data pull; excluded by the registered contamination rule; write paths sealed on both sides |
| O5 | The dead flip switch: the observer's launcher was the one job in its family that never loaded the environment file carrying the mode flag — `on` would have silently remained shadow forever (shadow "worked" only because it was the code default) | E (omission) / A flavor | latent since wiring | Caught by grounding the flip mechanism before flipping |
| O6 | Inert enforcing mode: on flip day, fail-plausible verdicts were appended to the anomaly list *after* the scoring prompt had been built — enforcing-mode reports carried the label "integrated · affects scoring" while scores remained byte-identical to shadow | D (fail-plausible — the deployment itself) | 1 day | Adversarial audit on the first enforcing-mode day |
| O7 | Calibration corruption: when the judge rejected an escalation (verdict clean), its confidence-in-clean was attached to the surviving Layer-1 verdict — a 90% confidence *of rejection* displayed as 90% confidence *of accusation*; calibration data would have been corrupted from flip day | C (semantic dilution) | caught pre-data | Same audit |
| O8 | Swallowed report failure: if writing the daily report failed, the wrapper discarded the error stream, skipped the push silently, and recorded `status: ok` — the observer would report success on the day its output was lost | D3 shape (fabricated success, in shell) | latent | Same audit family |

Three readings. First, the distribution: the observer's own failures span four of the
five taxonomy classes, including two genuine fail-plausibles (O2: it fabricated a
defect; O6: its deployment fabricated its own integration). The inheritance claim is
not rhetorical. Second, the discovery channels: not one of the eight was caught by the
observer's own operation in the ordinary sense — they were caught by human forensics,
adversarial audits, and the grounding discipline imposed by pre-registration (O4 and
O5 specifically fell out of refusing to flip a switch without verifying the switch was
connected). The observer watches the system; the *method* watches the observer. Third,
the repairs held: the contamination-exclusion rule matching zero rows at analysis time
(§6.4) is the audit trail that O4's two-sided fix actually worked.

We commend this practice — an incident log for the observability component, kept to
the same standard as the system's — as a concrete, checkable form of the "same
governance for the judge" principle.

---

## 8. Discussion

### 8.1 What mechanization actually bought

The Sunset Law standing rule in this runtime demands that new machinery name what it
retires. The observer retires **the human's regression scanning**: the portion of the
weekly user-view ritual spent re-checking known fail-plausible patterns — error text
posing as signal, boilerplate posing as content, forced analogies posing as insight,
release claims posing as news — is now executed daily, deterministically, with
sabotage-proven coverage of every documented in-scope pattern, at ≤24h design latency
against a historical human-detection record of 13 hours to 60 days. What it does not
retire — by the audit-as-regression argument, cannot retire — is the human's role as
the *novelty* detector. The weekly ritual continues, re-aimed: less re-scanning of
known patterns, more attention to the unknown. We consider "retire the scanning, keep
the eye" the honest, defensible value proposition for observer components today, in
contrast to claims that LLM judges can stand in for human oversight wholesale.

### 8.2 Description → prediction: current honest status

Offline held-out recall is 0/4; live true positives are zero in a quiet window. On the
evidence so far, the observer is a validated regression engine with an unproven
predictive layer. What has changed is that the question is now *instrumented*: a
grounded semantic judge exists behind the deterministic layer; a bench measures
held-out recall and invites adversarial contributions; a frozen live protocol defines
exactly what a predictive success or failure will look like (H1–H3) before it happens;
and the corpus grows with every incident, each new postmortem becoming a labeled case
within a day of closure. Prediction is no longer a claim to argue about; it is a
number that will arrive.

### 8.3 Pre-registration as an observability discipline

The transferable method contribution is independent of our detector's specifics: any
team promoting any detector (LLM or not) from shadow to enforcing faces the same
motivated-reasoning surface we did, and the same three-layer remedy applies — register
flip criteria before reading shadow data; freeze the analysis protocol before the
window opens; execute mechanically and disclose amendments. The cost was two short
documents written at the right time. The benefit, in our window alone: one systematic
false positive retired *before* it could page anyone; two latent deployment defects
caught because grounding-before-flipping is a registered step; three analysis-time
temptations neutralized because the rules had already spent the judgment; and a
negative result this paper can publish without embarrassment because publishing it was
promised in advance. We would not deploy another observability component any other way.

### 8.4 Economics

Two-layer economics held in production: 12/12 enforcing-mode days were clean and cost
zero Layer-2 calls — the marginal cost of having the observer armed is the
deterministic scan alone. This matters because observer components die of budget
review: a design whose quiet days are free is deployable indefinitely, and quiet days
are, we hope, the common case.

### 8.5 Limits of self-observation

The judge evaluates outputs of sibling LLM pipelines under the same roof, configured by
the same operators who wrote this paper; model-level self-preference is documented
[Panickssery et al. 2024], and operator-level motivated reasoning is documented in our
own §6.6. The mitigations — determinism-first, verbatim grounding, sabotage, read-only,
pre-registration — are the paper's method; §7 is the evidence they were necessary; we
do not claim they are sufficient, and an external replication of the bench on another
runtime's corpus is the experiment we most want to see.

---

## 9. Threats to Validity

**The quiet window.** Twelve observed enforcing-mode days contained zero manifested
in-scope incidents: live precision, live latency, and live recall are all unmeasured.
We cannot distinguish "nothing to catch" from "cannot catch" on live evidence — only
the corpus-side results separate those hypotheses, and they do so only for known
patterns. The window will lengthen; the registered protocol persists; the ledger is
append-only.

**Era mismatch (a confound we created).** The corpus incidents (2026-04→06) drove
defenses — context hygiene, provenance labeling, guard ladders — that plausibly
lowered the base rate of fail-plausible artifacts in the deployment era (07→08). The
observer may be guarding a door its own corpus helped close. We register the confound;
the human-first race rule (H3) exists precisely so that any future incident, caught by
either party, resolves it with data.

**Single system, single operator pair, self-observation.** All prior-paper external
threats carry over; additionally, the authors judged their own component's true/false
positives. Mitigations as in §8.5; the strongest is that every load-bearing claim
(defense, FP, sabotage, regime accounting) is mechanically reproducible from the
public repository, and the judgment-bearing claims (TP/FP adjudication) had zero
occasions to be exercised in-window.

**Single annotator, no κ** for ground-truth labels — inherited and mitigated as in
§4.4 (labels are load-bearing; disagreement breaks CI, not a statistic).

**Data gap.** 2/14 designed window days are missing (one registered boundary
exclusion, one host shutdown — root-caused as benign, disclosed, not backfilled). A
single-host system's observer shares the host's outages; observation of the observer
from a second failure domain is future work, not a claim.

**Bench external validity.** The corpus is one runtime's incidents; Category B holds
four cases. The bench's value grows only if others contribute cases from other
systems — which is why the contribution protocol and CI guards ship with it.

---

## 10. Conclusion

The prior paper ended with the image of the failure that should frighten an agent-system
operator: the confident paragraph, on schedule, in perfect grammar, about a crisis that
does not exist. This paper built the thing that is supposed to read that paragraph
first. Against every such paragraph from our recorded past, it works — deterministically,
provably, with each detector demonstrated load-bearing by sabotage. Against the
paragraphs not yet written, it is honestly unproven: held-out recall 0/4 offline, a
production window too quiet to score, and a predictive layer whose test will arrive on
its own schedule under rules we froze in advance.

What we can already recommend without qualification is the deployment method. Shadow
first; register the flip criteria before reading the shadow data; freeze the analysis
protocol before the window opens; ground the flip mechanism before flipping (two of our
eight observer-side incidents were caught by exactly that step); publish the quiet
window because you promised to. And keep an incident log for the observer itself — ours
produced eight entries spanning four taxonomy classes, including one hallucinated
defect and one silently inert deployment, and we built it *knowing* the judge inherits
the taxonomy. The eye can be partially mechanized. The mechanized eye then needs
watching — by method, not by hope.

---

## Postscript: The Loop, After the Freeze (added 2026-08-28)

This section postdates the registered study cutoff (2026-08-09) and enters none of
the frozen tables above. We add it because the paper's central loop — a human catches
a semantic defect the automation missed, and the catch is mechanized into a
deterministic detector within a day — executed twice more between the freeze and
submission. Both instances are repository-verifiable in the same way as everything
else in this paper; neither changes any frozen number.

**A sixth deterministic signal (2026-08-18).** Reviewing a routine nightly synthesis,
the human operator noticed two credibility-marker defects the automation had not:
the synthesis had *invented* a provenance tier — an emoji marker outside the
five-tier contract, wrapped in responsible-sounding hedging — and had *upgraded* a
social-media source to an industrial-practice tier. Both are textbook fail-plausible:
plausible-looking annotations, wrong by contract. Neither S1–S5 nor the existing
credibility-mismatch rule could see them: S2 checks wording against a known
single-source identity, while these were defects in the explicit markers of a
multi-source synthesis document. The same day, the catch became S6 — a deterministic
marker-contract checker that derives the legal tier set from the runtime's provenance
module (one source of truth, so a contract change updates the detector) and flags
out-of-contract or contract-inconsistent markers. Against the same real artifact, S6
flags exactly the two human-caught defects and nothing else on the in-contract
markers; sabotage confirms every branch load-bearing. Layer 1 is now six signals;
the frozen sections above describe the five that existed inside the study window.

**A distribution detector born from an acceptance test (2026-08-28).** A
material-coverage regression family — the nightly cross-domain synthesis silently
sourcing nearly all of its citations from a single calendar month, because a fixed
positional sampling window was pinned to one end of an append-only archive — was
caught twice by the human eye in quick succession (the first fix relocated the bias
to the archive's other end rather than removing it; only the second redesign made the
sampling content-aware). The first fix shipped with twenty-two green guard tests —
mechanism checks, all of them; not one asked what the user would see. When the
redesign's acceptance
data arrived — a healthy five-month citation mix, judged by the human eye — the
acceptance criterion itself was mechanized the same day: the daily observer now
extracts date tokens from the synthesis deterministically and raises an anomaly when
the cited months collapse (fewer than three distinct months, or one month above 80%
of tokens). Against the accepted real artifact it stays silent; against the
historical single-month shape it fires.

Neither event is evidence about the quiet window; both are evidence about the method.
The human-to-machine conversion path this paper describes is not a one-off — it ran
twice more while the paper was being prepared, each time within a day of the human
catch, and each new detector entered production under the same read-only, zero-cost,
sabotage-validated discipline as the pipeline it joined. The enforcing-mode ledger
remains append-only under the registered protocol.

---

## Artifact Availability

The labeled ground-truth corpus, two-layer detector, self-validation harness,
scorecard, byte-stable bench manifest, and the pre-registered protocol documents
(flip criteria, frozen analysis rules, decision records, and the append-only
adjudication ledger) are public in the system repository, alongside the 28 incident
postmortems and the governance framework described in the prior paper. The offline
bench requires only a Python standard library. The daily observer job, its shadow/on
mode flag, and the score-history data schema are in the same repository.

---

## References

*(entries 1–7 verified for the prior paper via the arXiv API with 100% author-list
agreement and reused; entries 8–11 verified against publisher pages on 2026-08-12;
2026-08-28 note: the submission-day visual pass over the six arXiv abs links is a
one-minute step in the submission runbook — the drafting environment's egress proxy
blocks arxiv.org, so it is delegated to the submitting author's browser)*

1. W. Wu. **When Errors Become Narratives: A Longitudinal Taxonomy of Silent Failures
   in a Production LLM Agent Runtime.** arXiv:2606.14589, 2026.
2. P. Huang, C. Guo, L. Zhou, J. R. Lorch, Y. Dang, M. Chintalapati, R. Yao. **Gray
   Failure: The Achilles' Heel of Cloud-Scale Systems.** HotOS 2017.
3. M. Cemri, M. Z. Pan, S. Yang, et al. **Why Do Multi-Agent LLM Systems Fail?**
   arXiv:2503.13657, 2025.
4. L. Zheng, W.-L. Chiang, Y. Sheng, et al. **Judging LLM-as-a-Judge with MT-Bench and
   Chatbot Arena.** NeurIPS 2023; arXiv:2306.05685.
5. L. Huang, W. Yu, W. Ma, et al. **A Survey on Hallucination in Large Language
   Models: Principles, Taxonomy, Challenges, and Open Questions.** arXiv:2311.05232,
   2023 (rev. 2024).
6. A. Basiri, N. Behnam, R. de Rooij, L. Hochstein, L. Kosewski, J. Reynolds,
   C. Rosenthal. **Chaos Engineering.** IEEE Software 33(3):35–41, 2016.
7. B. Beyer, C. Jones, J. Petoff, N. R. Murphy (eds.). **Site Reliability Engineering:
   How Google Runs Production Systems.** O'Reilly, 2016.
8. B. A. Nosek, C. R. Ebersole, A. C. DeHaven, D. T. Mellor. **The preregistration
   revolution.** PNAS 115(11):2600–2606, 2018. doi:10.1073/pnas.1708274114
9. A. Panickssery, S. R. Bowman, S. Feng. **LLM Evaluators Recognize and Favor Their
   Own Generations.** NeurIPS 2024; arXiv:2404.13076.
10. N. A. Ernst, M. T. Baldassarre. **Registered Reports in Software Engineering.**
    Empirical Software Engineering 28(2), 2023; doi:10.1007/s10664-022-10277-5;
    arXiv:2302.03649.
11. T. Rebedea, R. Dinu, M. Sreedhar, C. Parisien, J. Cohen. **NeMo Guardrails: A
    Toolkit for Controllable and Safe LLM Applications with Programmable Rails.**
    EMNLP 2023 (System Demonstrations); arXiv:2310.10501.
