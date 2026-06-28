# When Errors Become Narratives: A Longitudinal Taxonomy of Silent Failures in a Production LLM Agent Runtime

> **ISSRE Industry Track submission manuscript (assembled 2026-06-26, V37.9.190).**
> This is the **conference / industry-track** document — distinct from the IEEE Software magazine
> experience report (`ieee_software_experience_report.md`) and from the arXiv preprint
> ([arXiv:2606.14589](https://arxiv.org/abs/2606.14589), the underlying full paper). Per the
> submission guide, ISSRE uses the **full paper** with the strengthened Runeson–Höst case-study
> methodology (§3) and four-category threats schema (§8) replacing the magazine-level treatment,
> author/system names kept (industry tracks are not double-blind), and a generative-AI disclosure
> per IEEE policy.
>
> **✅ CFP confirmed (2026-06-28 — ISSRE 2026 Industry Track).** Cross-checked via the EAPLS CFP
> mirror and the EasyChair listing (`easychair.org/cfp/ISSRE2026`); the official
> `cyprusconferences.org/issre2026/industry-track/` page returned HTTP 403 to our fetcher, so
> **re-confirm on the official page before submitting**:
> - **Page limit: 6 pages (full paper), 4 pages (short) — _including references_** (IEEE Computer
>   Society 2-column format). The first page counts toward the budget; over-limit papers are
>   desk-rejected without review. This manuscript is full-fidelity (~arXiv length, ≈14 IEEE pages)
>   and **must be trimmed to 6 pages incl. references** — the camera-ready-target version is
>   `issre_6page_submission.md` (derived from this file via the "Trim priorities" appendix). Note
>   this is _tighter_ than the earlier "6 + references" assumption.
> - **Not double-blind:** keep title + author name/affiliation; abstract **≤150 words**; **≤4 keywords**.
> - **Deadlines:** abstract **2026-06-28 (extended to 2026-07-03)**; full paper **2026-07-05
>   (extended to 2026-07-12)**; author notification **2026-08-12**. Single PDF via **EasyChair**
>   (`easychair.org/conferences?conf=issre2026`).
> - **GenAI disclosure:** IEEE permits AI-assisted writing **with disclosure** and prohibits AI as a
>   listed author — already satisfied below (Generative-AI Use Disclosure + Acknowledgments; Wei Wu
>   sole author). Re-confirm IEEE's current-year wording on the publisher policy page.
>
> **Numbers are frozen at the study cutoff (2026-06-11)** to match the published arXiv version
> (`VERSION 0.37.9.70`, governance v3.56). Do **not** update them to current repository counts —
> the paper is a snapshot; the data inventory (`data_inventory.md`) traces every figure.
>
> **Format target:** IEEEtran conference template (2-column). Paste each section into the template;
> the ASCII Fig. 3 should be redrawn as a proper figure (TikZ or vector) for camera-ready.

**Authors:** Wei Wu, Independent Researcher (wuweinanonuaa@gmail.com). *No institutional
affiliation; this work was conducted independently.* AI-assistance disclosed in the Generative-AI
Use Disclosure and Acknowledgments; no AI system is an author.

**Index Terms** — software reliability, LLM agent systems, silent failures, gray failure,
hallucination, incident analysis, postmortem, fault taxonomy, case study, AI engineering.

---

## Abstract

Large language model (LLM) agent systems are increasingly deployed as long-running, autonomous
runtimes — orchestrating scheduled jobs, calling tools, maintaining memory, and pushing results to
humans over messaging channels. We present a longitudinal empirical study of *silent failures* in
one such system: a personal-assistant agent runtime in continuous production since March 2026,
comprising roughly 40 scheduled jobs, 8 LLM providers, a tool-governance proxy, and a knowledge-base
memory plane, defended by 4,286 unit tests and 827 declarative governance checks. Over an eight-week
window we documented 22 incidents with full root-cause postmortems, within which a single
meta-pattern — *a failure whose error signal never reaches a human in actionable form* — manifested
at least 28 times.

From these postmortems we derive a five-class, mechanism-oriented taxonomy of silent failure: (A)
environment and platform quirks, (B) design-assumption mismatches, (C) error swallowing and
dilution, (D) chained hallucination and fabrication, and (E) operational omission and forensic blind
spots. Class D is, to our knowledge, specific to LLM-based systems and the most dangerous: the
system does not merely fail to report an error — the LLM actively *transforms* the error into
fluent, plausible narrative content delivered to the user. We term this behavior **fail-plausible**,
and position it as the LLM-era escalation of *gray failure*'s differential observability: the
observer is not just blind, it is being convincingly lied to by the failure itself. Three
cross-cutting findings challenge common assumptions about agent reliability engineering: ~70% of
silent failures were caught by *human user-view observation*, not by tests, health checks, or
governance audits (all green through most incidents); a retrospective audit of 15 incidents found a
**0% ex-ante prevention but 87% ex-post regression-blocking rate** — audits are regression engines,
not prediction engines; and incident latency (13 hours to 60 days) correlates with failure
mechanism, not code complexity — the longest-lived failures lived in the *seams* between components,
where no test runs. We describe the defense framework that emerged and distill design principles for
agent systems whose failures are loud, attributable, and boring. All 22 postmortems, the governance
engine, and the defense framework are public; the governance engine is released as a project-agnostic
PyPI package.

---

## 1. Introduction

The reliability literature has long known that the most damaging failures in production systems are
not crashes. *Gray failures* [1] degrade cloud systems while their failure detectors report health;
*fail-slow* hardware [2] throttles performance for hours before anyone suspects the disk. The
defining property of both is **differential observability**: the application suffers, but the
observer designed to notice does not.

LLM agent systems inherit this entire problem class — and then add a new one. An agent runtime is a
generator of fluent language. When an upstream error leaks into its context window, the system's
failure mode is not silence; it is *plausible speech*. In one incident we document (§4.4), an HTTP
400 error page was captured into a cache by a logging bug, and the downstream LLM — seeing error
strings where signals should be — confidently fabricated an industry analysis titled around a
"Hugging Face platform crisis" and pushed it to the user as a routine insight digest. No detector
fired. Every test was green. The error had not disappeared; it had been *narrated*.

We call this behavior **fail-plausible**: a failure mode in which the system transforms an internal
error into coherent, contextually appropriate, and false output. Fail-plausible is to LLM agent
systems what gray failure was to cloud infrastructure — the dominant, hardest-to-see failure class —
but it is strictly worse for the observer: gray failure starves the failure detector of signal,
while fail-plausible feeds the human a counterfeit one.

This paper reports what eight weeks of documented production incidents in a real, continuously
operating LLM agent runtime taught us about silent failures, in five contributions:

1. **A mechanism-oriented taxonomy** (§4) of five silent-failure classes derived from 22 fully
   documented production incidents, each with a complete causal-chain postmortem, classified by
   *failure mechanism* rather than by location, because the same mechanism recurs across unrelated
   components and a defense against a mechanism immunizes a class rather than a file.

2. **The fail-plausible failure class** (§4.4), with four documented incidents in which LLMs
   converted polluted context (error logs, stale alerts, injected summaries) into confident
   fabrications delivered to the user — including fabricated software releases, fabricated platform
   crises, and fabricated remediation instructions for the host operating system.

3. **Quantified cross-cutting findings** (§5): incident-latency distribution (13 hours to 60 days),
   discovery-channel distribution (~70% human user-view; unit tests ≈0% for this failure class), a
   three-layer root-cause structure (trigger / amplifier / concealer) present in nearly every
   incident, and the observation that one meta-pattern manifested ≥28 times across all five classes
   — silent failure is a *bug class*, not a bug.

4. **An audited defense framework** (§6) and its honest scorecard: a retrospective Q1/Q2/Q3 audit of
   15 incidents showing 0% ex-ante prevention but 87% ex-post regression blocking — *audit is a
   regression engine, not a prediction engine*; a three-step defense maturation path (point fix →
   meta-rule → mechanized scanner) with evidence that lessons stopping at step one recur within
   days; and sabotage validation (deliberately breaking the system to prove each guard fires).

5. **A complexity argument** (§7): the longest-latency failures did not live in complex components
   but in *seams* — between repository and deployment, between dev and target OS, between declared
   and runtime state, between observer and observed. We argue that agent reliability engineering
   should optimize for *seam reduction* over defense accretion, and report our adoption of an
   explicit "Sunset Law" — a standing rule that retiring complexity outranks adding protection.

Our study is a **longitudinal single-case study** (§3) — the methodological complement of recent
horizontal studies that sample failures across many frameworks from benchmark execution traces [3]
or across many customers from a provider's incident database [4]. Horizontal studies establish
breadth; what they cannot see is the longitudinal texture of production silent failure — multi-week
latency, discovery channels, defense evolution, recurrence of "fixed" lessons — because those only
exist in a system that runs continuously, pushes output to a real user, and keeps complete
postmortems. That texture is the subject of this paper.

---

## 2. Background and Related Work

**Silent failures in distributed systems.** Huang et al. introduced *gray failure* as the failure
mode behind most cloud incidents — components degrade while failure detectors report health,
formalized as **differential observability** [1]. Gunawi et al.'s *fail-slow at scale* study
collected 101 (later 114) incident reports of hardware performance faults across 12–14 institutions,
documenting cascading root causes, fault conversion, and multi-hundred-hour diagnosis costs [2]. Our
study is methodologically downstream of this tradition — taxonomy from production incident reports —
but at a different layer (an LLM agent runtime) and with a different lens (every incident in our
corpus is a *silent* failure by selection; crashes that announced themselves did not generate
postmortems). Two of our five classes (C: swallowing/dilution, E: operational omission) will look
familiar to readers of that literature. Class D will not.

**LLM agent failure studies.** Cemri et al.'s MAST is the first empirically grounded taxonomy of
multi-agent LLM failures: 14 failure modes in 3 categories, built from 1,600+ annotated execution
traces across 7 frameworks (Cohen's κ=0.88) [3]. MAST's unit of analysis is the *benchmark task
trace*; failures are visible as task non-completion. Our unit is the *production incident*: failures
that by definition did **not** visibly fail any task, kept all detectors green, and were discovered
hours-to-months later. A recent provider-side study analyzed 156 high-severity LLM inference
incidents and derived a four-way operational taxonomy [4]; it shares our production grounding but
covers the *inference service* layer, where failures are loud (availability, latency). Ezell et al.
argue for structured incident analysis of AI agents and propose what agent incident reports should
contain [9]; our corpus can be read as an existence proof of their program, and our experience adds a
requirement: the report must record *how long the incident was silent and who finally noticed*,
because for this class those two fields carry most of the engineering signal (§5.1–5.2). Related
systems work addresses exception handling in agentic workflows (SHIELDA [5]), incident response for
agent safety (AIR [6]), and fault taxonomies for the Model Context Protocol ecosystem [7], [10].
None focuses on the silent/fail-plausible class, the latency-and-discovery question, or
defense-framework evolution under real recurrence pressure.

**Hallucination research.** Hallucination is usually studied as a *model* property — ungrounded
generation against references, with intrinsic/extrinsic taxonomies and model-side mitigations [11].
Our Class D reframes it as a *systems* property: in 4/4 documented fabrication incidents the model
behaved exactly as trained (fluent completion over its context); the failure was that **the system
delivered polluted context**. The defense is therefore not model-side but system-side: context
hygiene, provenance labeling, and layered anti-fabrication guards. This *garbage context in,
confident narrative out* view is distinct from model-centric hallucination work.

**Operational practice.** Site Reliability Engineering codified error budgets, postmortem culture,
and the principle that operational knowledge must be engineered rather than remembered [8]; our
convergence engine (§6) applies that to an agent runtime's declared state. Chaos engineering
established deliberate fault injection [12]; we apply the same epistemology one level up — *sabotage
validation* injects violations to gain confidence in the **guards**, because in a silent-failure
regime an unvalidated detector is indistinguishable from a vacuous one (a lesson from 67 checks that
silently executed empty strings for months). Large-scale incident studies such as Ghosh et al.'s
analysis of 152 severe incidents [13] established the empirical template — incident corpus,
root-cause and mitigation coding, automation-gap analysis — that we instantiate for an agent
runtime; the variable our setting adds is that the system under study *speaks*, which changes both
what failure looks like (§4.4) and what detection requires (§5.2).

---

## 3. Research Method and System Context

We frame this work explicitly as a **longitudinal single-case study** in the sense of Runeson and
Höst [15], so that "n=1" is a deliberate, justified design with an explicit generalization argument
rather than a limitation to be forgiven.

### 3.1 Method selection and rationale

Case study is the appropriate method here for three reasons that follow from the phenomenon. First,
*silent failures are not reproducible on demand*: by definition they manifest only in a continuously
operating system whose automated indicators stay green, which precludes a controlled experiment and
rules out benchmark-trace sampling (the failures are absent from any trace that records task
completion). Second, the variables of interest — **silence latency, discovery channel, and defense
evolution** — only exist over real operational time; they cannot be elicited in a laboratory
snapshot. Third, the unit that produces these variables is an *operating system in context*, and
case study is the established method for studying a contemporary phenomenon in its real-life context
when the boundary between phenomenon and context is not clear-cut. The case is *revelatory* (it
provides public access to production fail-plausible fabrication with complete causal-chain
postmortems) and *longitudinal* (the same case observed across eight weeks in which both failures and
defenses evolve).

### 3.2 The system under study (the case)

The subject system (public repository `openclaw-model-bridge`) is a two-layer middleware connecting
self-hosted and commercial LLMs to OpenClaw, an open-source personal-agent framework bridging
WhatsApp and Discord, in continuous production on a single macOS host since early March 2026. It
follows a three-plane design: a **control plane** (tool-governance proxy with a hard tool cap, schema
repair, alert-context stripping; a declarative governance engine of 90 invariants, 827 checks, 23
meta-rules, 14 mechanized discovery scanners, daily audit cron; SLO monitoring; circuit breakers;
and a convergence engine that machine-synchronizes declared state to runtime state); a **capability
plane** (an adapter routing chat/completions across 8 providers — self-hosted Qwen3-235B primary,
Qwen2.5-VL multimodal route, commercial fallbacks — with capability-scored automatic fallback
chains); and a **memory plane** (a ~1,100-note local-embedding RAG index, multimodal media index,
conversation-harvest pipeline, and daily LLM-driven synthesis jobs that push insights to the user).
Scale snapshot at the study cutoff (2026-06-11): ~40 registered scheduled jobs across two cron
schedulers; 8 LLM providers; 3 supervised long-running services; 4,286 unit tests in 121 suites; 22
published incident postmortems. One human operator and one AI engineering collaborator (Claude, via
a coding-agent interface) develop and operate the system, whose runtime is powered by separate
(Qwen3-class) models — making this also a data point on AI-assisted operation of an AI system (§7).

### 3.3 Unit of analysis and corpus

The **unit of analysis** is the *silent-failure incident* — an event that (a) reached production,
(b) had a silent phase during which the system was failing while all automated indicators stayed
green, and (c) was closed with a complete postmortem. The embedded sub-unit is the *mechanism* (how
the error evaded observation), which is the basis for the taxonomy. The single-case-with-embedded-
units design (case = the runtime; embedded units = 22 incidents, decomposed into ≥28 mechanism
manifestations) is what licenses within-case cross-incident comparison while keeping context fixed.
The corpus is the **complete population** of qualifying incidents in the window (2026-04-09 to
2026-06-02), not a sample, which removes sampling bias within the window; the residual selection
effect (failures still silent at cutoff) is treated as a survivorship threat (§8).

### 3.4 Research questions

The study is exploratory but proposition-guided; we carried four questions into the corpus.
**RQ1 (taxonomy):** what mechanism classes account for how silent failures evade observation?
**RQ2 (latency):** how long do silent failures persist before detection, and what does latency
correlate with? **RQ3 (discovery):** through which channel are silent failures actually detected?
**RQ4 (defense):** which defenses prevent recurrence, and how does a lesson travel from a single fix
to a structural guarantee?

### 3.5 Data collection: the postmortem protocol

Data collection followed a **fixed, pre-registered-in-repository postmortem protocol** (the
"exception-analysis constitution"), adopted after the second incident and applied retroactively to
the first two. For every qualifying incident, before any fix, the protocol mandates: (1) a full
causal-chain diagram (timeline × layer × logic × architecture, with code-branch truth values
annotated); (2) a three-layer root cause (*trigger* / *amplifier* / *concealer*); (3) a per-minute
timeline reconstruction where logs allow; (4) a condition-combination ("why now, not before")
analysis; and (5) an entry feeding the governance ontology (new invariants, meta-rule candidacy,
catalog entry). This yields **methodological triangulation across data sources** — runtime logs, the
OS audit log, version-control history, test/governance results, and the operator's contemporaneous
notes — each incident corroborated by ≥2 independent sources before its root cause is recorded. Each
postmortem is a standalone public document (`ontology/docs/cases/`); the consolidated catalog
(`ontology/docs/failure_modes_catalog.md`) is the canonical index from which the taxonomy is built.

### 3.6 Analysis: mechanism-oriented constant comparison

Classification proceeded by **constant comparison**. Each new postmortem's "true root cause" was
matched against existing classes; two consecutive non-matches forced a class split or a new class.
We classify by mechanism rather than location because a location taxonomy proved to have no defensive
value (the same mechanism recurred across unrelated components) whereas a mechanism-level defense
immunizes a whole class. The scheme stabilized in mid-May and was stable over the final 9 incidents —
an emergent **saturation** signal appropriate to an exploratory design. A distinguishing strength is
that the classes are *load-bearing, not merely descriptive*: each class drives a mechanized scanner
whose findings are objective and independently checkable, so a misclassification has a falsifiable
consequence rather than only a narrative one.

### 3.7 The meta-pattern counter

Early in the study we adopted a meta-rule, **MR-4 "silent-failure-is-a-bug,"** and began counting its
*manifestations* — distinct incidents or sub-events in which an error signal existed somewhere in the
system but never reached a human in actionable form. The counter reached ≥28 across the 22 incidents
(several incidents contain multiple manifestations; numbering has deliberate gaps where
manifestations were recorded inside hotfix notes). We report it not for precision but for shape:
manifestations continued to appear at a roughly constant rate even as defenses accumulated — in *new
forms* each time (§5.4). This is the empirical basis for treating silent failure as a bug class.

### 3.8 Generalization basis (analytical, not statistical)

We claim **analytical generalization** in the sense of Runeson & Höst (generalization to *theory*),
not statistical generalization (to a population). **What generalizes (theory-level):** the five
mechanism classes; the *trigger/amplifier/concealer* causal structure; the latency ∝
observational-distance relationship; the *point fix → meta-rule → scanner* maturation; and
*audit-as-regression-engine*. These are claims about **mechanisms**, which are not artifacts of one
stack — Classes A, C, and E have direct ancestry in distributed-systems and hardware failure
literature, and Class D follows from a property (fluent completion over context) common to all LLM
agent runtimes with synthesis-and-push pipelines. **What does not generalize (population-level):**
the *frequencies, shares, and latencies* are descriptive statistics of this case and are reported as
point estimates with no claim of populational validity; the ~70% user-view discovery share in
particular is presented as an **existence proof**, not a constant. Two early incidents were
reconstructed retroactively under the protocol and are flagged as such; this is the standard,
defensible posture for a revelatory case study, and the framing we ask reviewers to evaluate the work
against.

---

## 4. A Taxonomy of Silent Failures

Table 1 summarizes the five classes. Full per-incident detail is in the public catalog; here we
define each class, give its mechanism signature, and narrate one or two representative incidents.

**Table 1 — Five classes over 22 incidents (+ sub-events).**

| Class | Mechanism | Incidents | Silence span | Defining property |
|---|---|---|---|---|
| A — Environment/platform quirk | Logic correct; runtime environment's implicit behavior defeats it | 1 (+6 sub-events) | hours–weeks | dev always green; only target OS/client reveals |
| B — Design-assumption mismatch | Code assumes a deployment topology / contract / input shape that reality violates | 4 | days | unit tests cover the assumption, not reality |
| C — Error swallowing & dilution | Error occurs, is eaten by a layer or stripped of cause across layers | 5 | hours–days | the alert that arrives carries no usable information |
| D — Chained hallucination & fabrication | LLM converts polluted context into confident false output | 4 | hours–days | **fail-plausible**: user receives counterfeit health |
| E — Operational omission & forensic blind spot | Deployment/registration step skipped; or the forensic tool itself is blocked and reads as "normal" | 8 | days–**60 days** | declared state ≠ runtime state; diagnosis instruments lie |

### 4.1 Class A — Environment and platform quirks

*The logic is right; the environment's implicit behavior is not what anyone assumed.* The development
environment (Linux container, root, GNU userland, bash 5) is systematically more permissive than the
production target (macOS, bash 3.2 as `/bin/bash`, BSD userland, zsh interactive shell, sandboxed
cron). Documented sub-events include: bash 3.2 not propagating ERR traps into functions without
`set -E`, silently disarming a watchdog's self-alarm; BSD awk aborting on invalid UTF-8 multibyte
sequences, which — combined with `set -e` and `pipefail` — killed the *monitoring* script itself for
7 days; the absence of GNU `timeout` on stock macOS turning a defensive wrapper into a universal
"tool unavailable" failure; CJK full-width punctuation adjacent to unbraced shell variables parsing
as part of the variable name; and a messaging client folding long messages at an undocumented
~4,000-character threshold, silently changing the user-visible shape of every long push. The class
signature is **green dev, silent prod**, and its defense is necessarily mechanized: a cross-OS quirk
scanner now encodes six known quirk patterns as repository-wide checks, and a meta-rule requires
framework-level fixes to be validated *on the target environment*, after a fix validated only in dev
shipped broken twice in one day.

### 4.2 Class B — Design-assumption mismatches

*The code is internally consistent with an assumption; production violates the assumption.*
Representative incident: a metadata-resolution function shipped with three path candidates for
locating a registry file, all of which missed the file's actual production location — because the
deployment pipeline placed it under a path none of the candidates covered. The component fell back,
silently, to an unfiltered mode for **five days**; its own log line announcing the fallback scrolled
past unread, and a companion feature (writing health scores into shared state) silently never
executed at all. Unit tests were green throughout: they tested the resolution *logic*, with fixtures
laid out according to the same wrong assumption. A second representative: an LLM-output parser
indexed lines positionally (`lines[i+1]`, stride 3); the model occasionally omits one line —
instruction-following is a distribution, not a contract — after which every subsequent field shifted
one slot, and users received messages whose "title" field contained a literal separator string. The
mechanism generalizes: **any positional parse of LLM output is a latent Class B failure**, which we
froze into a meta-rule (parsers must be key-based, never positional) and a scanner. The class
signature is *tests mirror the assumption rather than the caller*. Defense: explicit cross-script
contracts, deployment-layout testing on target, scanners that walk every path-resolution function and
assert the production-canonical candidate is present.

### 4.3 Class C — Error swallowing and dilution

*The error happens and is reported — into a void.* Three mechanism variants. **Swallowing:** a
summary function counted only `status=="fail"` results; invariants whose check raised an *exception*
(`status=="error"`) vanished, and the governance audit printed "all invariants hold" over a pile of
dead checks. In a later echo, a governance executor read check code from one YAML field while 67
checks (across 21 invariants) had their code in a differently named field: all 67 executed `exec("")`
— vacuous passes — for months, discovered only when a *new* invariant's sabotage validation refused
to fail. **Dilution:** an evening digest failed with "HTTP 502: Bad Gateway" for two days; the true
cause (the fallback provider's free-tier quota exhausted by daytime jobs after the primary's circuit
breaker opened) was in the upstream response body, which the adapter wrapped into a 502, whose body
the proxy never read (`str(e)` only), whose remnant the client reduced to `f"HTTP {code}: {reason}"`
— three hops, each individually reasonable, each stripping cause; the alert that reached the human
contained zero actionable bits. The emergent meta-rule — *error chains must preserve upstream cause
across layers* — has a twist: in agent systems the "client" of an error is often another LLM prompt,
so a diluted error is one step from becoming Class D fabrication input. **Amplified swallowing:** an
automated batch tool injected an environment-variable read into 8 job scripts without the
corresponding `import` — a `NameError` in all 8, caught by a fail-open guard that dutifully skipped
the new validation in all 8 while every test and syntax check passed. Automation is a bug amplifier:
a single wrong decision propagates to N sites with machine efficiency, and `bash -n` cannot see
inside a Python heredoc.

### 4.4 Class D — Chained hallucination and fabrication (fail-plausible)

*The most dangerous class, and the one without precedent in the gray-failure literature.* The error
is not suppressed; it is **transformed**. Four incidents.

**D1 — The fabricated platform crisis.** A nightly knowledge-synthesis job (`dream`) collects signals
from ~290 notes via map-reduce LLM calls. A Unicode surrogate in scraped content made `json.dump`
raise mid-write, producing a truncated request body and a 400 from the adapter — and here the chain
turns: the map step's logging function wrote diagnostics to **stdout**, the caller captured stdout by
command substitution as the *signal payload*, and the cache filled with `HTTP Error 400: Bad JSON`
strings. The reduce-step LLM, prompted to find cross-domain signals, did what language models do with
anomalous-but-thematic context: it composed a confident analysis of a "Hugging Face platform crisis"
— platform trouble being the most probable narrative shell for error-code vocabulary — and the system
pushed it to the user as a routine insight digest. Every component succeeded; the pipeline laundered
an encoding bug into industry analysis (Fig. 1). The structural fix was four lines deep in defense
(stderr discipline, surrogate sanitization, encoding-error policy, anti-pollution prompt guards), but
the load-bearing one was a single `>&2`: **one redirection operator severed the entire hallucination
chain.**

```
Fig. 1 — The D1 pollution chain: from one malformed byte to a fabricated
         industry analysis. Every component behaves as designed.

  scraped content with isolated UTF-16 surrogate (U+D800–DFFF)
        │
        ▼
  json.dump(body, ensure_ascii=False)──UnicodeEncodeError──► request body TRUNCATED
        │
        ▼
  adapter: json.loads fails ──► HTTP 400 "Bad JSON" (456-byte HTML error page)
        │
        ▼                              ┌──────────────────────────────────┐
  llm_call(): extraction empty;       │ AMPLIFIER                        │
  log() prints error dump to STDOUT ──┤ signals=$(llm_call …)            │
  (4 deterministic retries, same)     │ command substitution captures    │
        │                             │ stdout → error log becomes the   │
        ▼                             │ "signal" payload                 │
  cache := "…Error code: 400 Bad      └──────────────────────────────────┘
   JSON… Waiting 3s before retry…"
        │
        ▼                              ┌──────────────────────────────────┐
  reduce-step LLM reads cache as      │ CONCEALER                        │
  cross-domain "signals" ─────────────┤ non-empty check passes; status   │
        │                             │ file reports ok; no detector     │
        ▼                             │ inspects content semantics       │
  fluent synthesis: "Hugging Face     └──────────────────────────────────┘
   platform crisis" analysis
        │
        ▼
  pushed to user via WhatsApp/Discord as routine insight digest
        │
        ▼
  ✗ no alarm — discovered by the user noticing a thematic incoherence
    (the "signal" and the "action item" did not match)

  Fix that severed the chain: log() { echo … >&2; }   (one redirection)
```

**D2 — The fabricated remediation.** A system alert pushed by a watchdog was persisted into the chat
session history as an ordinary assistant message. Thirty-six minutes later the user asked an
unrelated architecture question; the model, attending across the contaminated context, replied that
it had "received the system alert follow-up task" and instructed the user to grant Full Disk Access
to a cron binary in macOS System Preferences — instructions that were off-topic and *technically
fabricated* for the scenario. Alert traffic and conversation are different speech acts; letting them
share a context window invites the model to weave them into one narrative. (A later forensic twist: an
unrelated 60-day investigation established that the *general remediation direction* the model
fabricated — FDA for cron-derived processes — was coincidentally relevant to a real problem elsewhere.
Fabrication is not refuted by occasional accuracy; it is defined by absence of grounding.)

**D3 — The fabricated success.** A weekly review job whose LLM call failed fell back to a mechanical
line-filter that emitted leftover container headings as if they were review content, and wrote
`"llm": true` into its status file unconditionally. Six co-located issues each made the others harder
to see; the user-visible artifact looked plausible enough to pass casual inspection for weeks.
Fabrication does not require a model: **a fallback path that manufactures plausible-shaped output is a
hallucination implemented in shell.**

**D4 — The fabricated release.** An evening digest, given a list of the day's high-alignment papers as
context enrichment, inferred that the user's project "must have shipped" and announced a community
release of an internal version number that exists only in the project changelog — inventing a source
tag for it. The injection of *true but unlabeled* context produced false attribution:
provenance-free enrichment is fabrication fuel.

The common structure is a **pollution chain**: a Class A/B/C failure deposits non-signal content
where a downstream LLM expects signal; the LLM performs its function — fluent, coherent completion —
and the output inherits the *form* of health with the *content* of failure. Defense is therefore
system-side context hygiene, applied at every link: stderr discipline so diagnostics can never enter
data channels; alert stripping before context assembly; provenance/credibility labeling of injected
content; and a six-level cumulative ladder of anti-fabrication prompt guards (up to literal
prohibited phrases extracted from actual incidents), shared as a single imported module across all
nine LLM-calling jobs rather than copy-pasted. We make no claim that prompt guards are sufficient —
they are the *last* layer, behind hygiene and provenance, and §5.5 shows why layered placement
matters more than any single guard.

### 4.5 Class E — Operational omission and forensic blind spots

*The code is right; an operational step never happened — or the diagnostic instrument itself is
compromised.* This is the largest class (8 incidents) and contains the longest silences.

**Omission.** A new daily-analysis job was fully implemented, tested, registered, and deployed — and
never ran, because the final step (writing the crontab line) was a human memory item. Three
independent small bugs conspired to hide it: the preflight checker grepped for only one of two
registry-drift warning strings; the crontab helper did not check its own install exit code and
compared counts with `<` instead of equality (reporting ✅ on a rejected install); and the job's
absence produced no log to scan. The incident generalized into the system's largest architectural
correction: **declared state must converge to runtime state via machine, not memory**, implemented as
a convergence engine that diffs registry declarations against observed crontab/launchd/provider state
on every audit, with staged escalation (alert-only → dry-run → machine-sync) and automatic
synchronization after a one-week zero-drift observation window per spec.

**The reserved-file incident.** The agent, completing an alert-handling task, wrote "task complete"
notes into a file named `HEARTBEAT.md` in its workspace — to the agent, a scratch filename; to the
runtime, a *reserved control file* whose non-empty content activates a heartbeat protocol that
instructs the model to reply with a bare acknowledgment token if nothing needs attention. The gateway
then strips that token from outbound messages. Result: for 13 hours, every user message received a
reply of `HEARTBEAT_OK`, stripped to nothing in transit — **total silence, with every component
functioning exactly as designed.** The model had been handed a pen that doubled as the system's mute
button. The lesson became a meta-rule: any file path carrying special runtime semantics must be
unwritable by the LLM's generic file tools (write attempts are intercepted at the proxy and rewritten
to a comments-only placeholder).

**Forensic blind spots.** The longest silence in the corpus — 60 days — was an external-SSD backup
path failing with EPERM. Six successive hypotheses were each falsified by data over multiple weeks;
the breakthrough came only from the OS's own audit log (`log show`), which revealed macOS TCC sandbox
denials: cron-derived processes lack Full Disk Access by default. The deeper finding is
methodological: during those weeks, the *forensic collectors themselves* (lsof, ACL listing, snapshot
enumeration) were being silently denied by the same sandbox and returning empty output — which the
diagnosis pipeline recorded as "normal/empty." **An instrument that cannot distinguish "nothing
there" from "I was not allowed to look" will manufacture false reassurance**; all collectors now
capture stderr separately and tag `[sandbox_denied]` as a first-class observation. The companion
lesson — every falsified hypothesis was answered by *adding a forensic dimension*, never by
speculative code change — is the discipline we found most transferable. Also in this class: a 9-hour
total gateway outage whose three independent alarms each failed (a quiet-hours filter suppressing both
channels including the emergency one; a keepalive that logged WARN without alerting; a restart script
that reported success without verifying health) — yielding the meta-rule that **an alert path must not
depend on the failing subject**; and the watchdog that died of a Class A quirk and stayed dead for 7
days because nothing watched the watcher.

---

## 5. Cross-Cutting Findings

### 5.1 Latency: how long silence lasts

| Silence span | Incidents (examples) | Mechanism layer |
|---|---|---|
| 60 days | SSD backup TCC sandbox | architecture-level assumption + compromised forensics |
| 7 days | watchdog self-death | monitoring layer's own silence |
| 5–6 days | observer path fallback; backup anti-pattern | deployment topology; copy-pasted suppression |
| 9–13 hours | gateway death; reserved-file mute | alert-chain dependency; runtime semantics |
| hours–2 days | digest 502; cron omission | error dilution; operational omission |

Latency tracks the **mechanism layer, not code complexity**. Code-level bugs die young: unit tests
and the next run catch them. What survives for weeks lives where no test runs — in deployment
topology, OS policy, monitoring-of-monitoring, and the gap between declared and runtime state.
Latency is therefore a *measure of observational distance*: the further a mechanism sits from any
existing observer, the longer it lives. We suggest silence-latency percentiles as a reportable
reliability metric for agent systems, complementary to MTTR — for silent failures, time-to-*detect*
dominates time-to-repair by one to two orders of magnitude.

### 5.2 Discovery: who finally notices

| Channel | Share (qualitative) | Notes |
|---|---|---|
| **Human user-view** (reading actual pushed output; weekly observation ritual) | **~70%** | "this digest looks shallow"; "why two windows?"; "didn't receive yesterday's analysis" |
| Target-environment execution (dev green, prod reveals) | high | all Class A; several Class B |
| Self-observation (governance auditing governance; observer critiquing output) | rising | added mid-study; caught its own executor bug |
| Unit tests / preflight | ≈0 for this corpus | by selection: anything they caught never became a silent incident |

The last row is partly tautological — our corpus selects for what tests missed — but the magnitude of
the first row is not. The system runs 4,286 tests, 827 governance checks, and a 19-point preflight,
all green through most of these incidents; the single most productive detector of silent failure was
**a human looking at the product as a user**. We institutionalized this as a weekly 30-minute
observation ritual (no coding allowed; four dimensions: alert noise, push latency, information
density, response quality) — and it continued to out-detect the automated stack. For practitioners
the implication is blunt: *user-view observation is a first-class observability signal and deserves
calendar time*; for researchers, the open problem is mechanizing part of what the human eye does here.
The LLM-as-judge literature gives grounds for optimism — strong judges reach >80% agreement with
human preference on open-ended quality [14] — and our own daily LLM "observer" found real regressions
(including a fabrication, and two bugs in *itself*). The qualifier our experience adds: an LLM judging
a *system's output for silent failure* is itself an LLM component of that system, inheriting every
class in this taxonomy — ours shipped with a Class B path bug and a sampling artifact that made it
hallucinate a truncation — so the judge needs the same governance, provenance hygiene, and sabotage
validation as the components it judges.

### 5.3 The trigger–amplifier–concealer structure

Nearly every postmortem decomposed into three causally distinct layers: a **trigger** (the external
spark — a surrogate byte; an omitted LLM output line; a transient EPERM), an **amplifier** (the
architectural flaw that spreads it — stdout logging into command substitution; positional parsing; 18
copy-pasted suppression idioms), and a **concealer** (the absence that hides it — a status file lying
"ok"; a fail-open guard; a quiet-hours filter; a forensic tool silently denied). The practical force
of the decomposition is prescriptive: **a fix that addresses only the trigger is cosmetic.** Triggers
are unbounded (the environment will always produce another malformed byte); amplifiers and concealers
are finite and owned by the architecture. Every recurrence in our corpus (§5.4) traces to a fix that
stopped at the trigger; conversely, the highest-leverage single fixes were amplifier-level (one
`>&2`; one shared helper replacing 20 idiom copies) and concealer-level (fail-loud with cause;
forensic stderr tagging).

### 5.4 Silent failure is a bug class, not a bug

The MR-4 manifestation counter kept advancing at a roughly constant rate across the study — but never
twice in the same form. Early manifestations were classic swallowing; middle-period ones were
dilution and fabrication; late ones included *a fix introducing a deeper silence* (a retry helper
whose exit-code propagation silently killed twenty `set -e` callers mid-script), *a correct fix to the
wrong bug* (an ownership repair real but irrelevant to its target EPERM, exposed only because the
metric refused to move), and *vacuous verification* (the 67 checks executing empty strings). The
lesson is structural: silent failure cannot be enumerated and fixed; it can only be **governed** — by
meta-rules that constrain whole mechanism families, scanners that enforce them mechanically, and
validation that the guards themselves are alive (§6).

### 5.5 Defense maturation: point fix → meta-rule → scanner

Defense effectiveness correlates with how far a lesson traveled along a three-step path. (1)
**Point fix** — repair this bug. *Empirically insufficient:* an import-omission bug fixed at one site
recurred at eight sites via an automated injector **two days later**, because the lesson existed only
as a diff. (2) **Meta-rule** — write the lesson as a named, cross-case rule (23 by study end; e.g.,
diagnostics-to-stderr; key-based parsing; alert-path independence; declared-state convergence;
reserved-file unwritability). Necessary but memory-bound. (3) **Mechanized scanner** — encode the
meta-rule as a repository-wide check that runs in CI and the daily audit (14 by study end: cross-OS
quirks, path consistency, heredoc import closure, monitor self-alarm compliance, cross-environment
path resolution, …). Only at this step does recurrence become structurally impossible rather than
culturally discouraged. Every meta-rule that reached step 3 has zero recorded recurrences as of the
study cutoff; every recurrence in the corpus involves a lesson that stopped at step 1.

### 5.6 Audit is a regression engine, not a prediction engine

Mid-study we audited our own defense framework against the first 15 incidents with three questions
per incident: Q1 — could the audit, as it existed *before* the incident, have caught it? Q2 — why
not? Q3 — do the guards added *after* it block the same class going forward?

| Metric | Value |
|---|---|
| Ex-ante prevention (Q1 fully) | **0 / 15 = 0%** |
| Partial early warning | 2 / 15 = 13% |
| Ex-post regression blocking (Q3 ≥ half) | **13 / 15 = 87%** |
| Root cause of misses: a dimension the audit had never conceived | 12 / 15 = 80% |

The 0% is not an indictment of the audit — it is its *job description*. 80% of misses were "blank
categories": dimensions no invariant had ever contemplated, which no diligence within the existing
dimension set would have covered. Audits, like regression test suites, encode the past. The honest
posture is to (a) maximize the regression rate (ours: 87%, with sabotage validation hardening that
number), (b) accept that prevention of *novel* mechanism classes comes from elsewhere — user-view
observation, adversarial review, target-environment exposure — and (c) measure the *conversion
latency* from novel incident to mechanized guard, which the three-step path (§5.5) minimizes. A
complementary adversarial audit (16 destruction scenarios injected against the live repository: 10
replaying known incidents, 6 probing suspected blind spots) scored 16/16 after the blind-spot batch
drove its own round of guard additions — useful precisely because its first run did not score 16/16.

---

## 6. The Defense Framework That Emerged

We summarize the framework not as a recommendation of its specifics but as an existence proof of one
coherent answer, with its audited scorecard (§5.6) attached. Five pillars.

**1. Declarative governance with mandatory depth.** 90 invariants / 827 checks in a YAML ontology,
executed by an engine on a daily audit cron and in CI. Each invariant declares its meta-rule lineage,
severity, and *verification layers*; critical invariants are mechanically required to verify at ≥2
layers (declaration-level greps are not allowed to stand alone — a rule the system enforces on itself
via a meta-invariant, after single-layer greps demonstrably failed to notice behavioral regressions).

**2. Sabotage validation ("test the test").** Every new guard must be proven *alive* by deliberately
introducing the violation it targets and observing it fire — then reverting. This caught guards that
matched their own assertion strings, the 67 vacuous checks (found when a sabotage refused to fail),
and tests whose fixtures mirrored the wrong assumption. In a system whose primary failure class is
*silent*, an unvalidated guard is indistinguishable from a vacuous one.

**3. Declared-state convergence.** A convergence engine diffs every declared registry (jobs,
providers, services, KB sources, runtime config) against observed runtime state on each audit, with
per-spec staged escalation. This retired the largest Class E mechanism from "human memory item" to
"machine-closed loop." Its own history doubles as a cautionary tale (§7): an audit that *applies*
synchronization is an observer mutating the observed, and one drift bug caused a thrice-recurring
crontab duplicate until the audit path was forced to dry-run — leading to a standing rule that **audit
observes and never mutates.**

**4. Context hygiene and anti-fabrication layers (Class D defense).** Mechanism-level: shell
diagnostic output must go to stderr (scanner-enforced repo-wide); alerts are tagged at every producer
and stripped from chat context at the proxy *before* truncation; reserved runtime files are unwritable
by LLM tools. Content-level: a single shared module provides a six-level cumulative ladder of
anti-fabrication guards injected into all LLM-calling jobs (upper levels contain literal prohibited
phrases harvested from actual fabrication incidents and tag requirements like `[strong-evidence]` /
`[weak-association]` on cross-domain claims); a source-credibility module labels every ingested source
on a five-tier provenance scale at prompt-injection time. Post-deployment measurement showed the
targeted fabrication patterns (multi-hop causal chains, "therefore"-style necessity claims) dropping
by 53–92% in the affected synthesis job while tagged-claim usage went from zero to ~9 per day.

**5. Monitoring that monitors itself, and alarms that outlive their subject.** Watchdog ERR-trap
self-alarms plus heartbeat canary files age-checked independently; alert routing that never depends on
the failing subject (gateway-down notifications travel a second transport); freshness guarantees on
every health field; and an LLM "observer" job that critiques the previous day's user-facing output —
the beginning of mechanizing §5.2's human advantage. What the framework deliberately does **not**
claim: prevention of novel mechanism classes (§5.6), and net complexity reduction — which motivates
§7.

---

## 7. Discussion: Seams, Not Components

Two months in, facing a week with five incidents, we asked the obvious question: *is the system
failing because it has become too complex?* The postmortem-backed answer was more specific: **no
individual failing part was complex.** A symlink. An `abspath` call. A one-line registry entry. A
boolean default. Every part was simple and locally correct; every incident lived in a *combination* —
symlink × non-resolving path API × a particular invocation route; sync-enabled audit × bare-name
entry × format mismatch × non-dry-run path. Components are covered by 4,286 tests; **combinations grow
superlinearly and tests cover only the combinations someone imagined.** Complexity is about parts;
incidents are about seams.

This reframing inverts the instinctive response to incidents — *add a guard* — which itself adds a
part, and therefore seams. (Our convergence engine, built to close the declared-vs-runtime seam,
opened an observer-mutates-observed seam that produced three incidents before being caged.) The stable
posture we landed on, codified as a standing **"Sunset Law"** with two operational meta-rules, is:
before adding any mechanism, attempt to *retire* an equivalent one; one logical entity must have one
physical representation (multiple representations *will* drift, and the bug lives between them); and
defenses themselves are incident surfaces — prefer **seam reduction** (unify representations, shrink
the dev–production gap, make observers read-only) over defense accretion. In the five weeks since
adoption, the system retired more representation duplicates than it added invariants — and we note,
with appropriate caution about confounds, that incident frequency declined while feature velocity did
not.

**On the AI-assisted operation of an AI system.** This system is developed and operated by one human
domain expert working with an AI engineering collaborator, governing a runtime powered by *other*
LLMs. Several findings are entangled with that arrangement in both directions. The postmortem
protocol's rigidity (mandatory causal-chain diagram before any fix; the three-question rule before
touching code) exists substantially *because* an AI collaborator will otherwise pattern-match symptoms
to plausible fixes with great speed — industrializing the trigger-level cosmetic fix (§5.3); one
documented incident chain consisted of five cascading "fixes" to a problem whose correct resolution
was a single file copy. Conversely, the framework's volume (90 invariants, 14 scanners, 4,286 tests,
22 publication-grade postmortems in eight weeks) is achievable for a single human *only* with such a
collaborator. We offer the observation, not a verdict: AI collaboration shifts the binding constraint
of reliability engineering from implementation bandwidth to **judgment discipline** — exactly the
property the meta-rules exist to encode.

**Generalizability.** We claim the *taxonomy classes and cross-cutting structures* generalize to LLM
agent systems with long-running scheduled autonomy and human-facing output; we explicitly do not claim
the *frequencies* generalize (single system, single operator pair, one OS), nor that Class D
frequencies transfer to systems without synthesis-and-push pipelines. What we most hope transfers is
the method: complete causal-chain postmortems, a mechanism-oriented catalog treated as the unit of
institutional memory, and sabotage-validated conversion of every lesson into a machine check.

---

## 8. Threats to Validity

We organize threats under the four standard case-study validity categories [15] and state the
mitigation for each.

**Construct validity.** The central construct, *silent failure*, requires a silence span with
concurrently green indicators; it was applied at postmortem time, which risks hindsight framing.
Borderline cases — loud-but-cause-free alerts (the "HTTP 502" with no actionable cause) — were
included only when the *actionable* signal was absent, a judgment a reasonable observer could draw
differently for 2–3 incidents. The *fail-plausible* construct is defined by **absence of grounding**,
not by inaccuracy of output (one fabricated remediation direction later proved tangentially correct,
which does not refute it). *Mitigation:* every construct is operationalized against repository
artifacts (a silence span is a timestamp gap against green CI/audit records; a fabrication is a claim
with no source in the input context), so classification is checkable rather than purely interpretive.

**Internal validity.** Each postmortem asserts a causal chain. The principal threat is **confirmation
bias in mechanism assignment**, compounded by the coders being the system's two operators (one human,
one AI collaborator) with no independent annotators — we therefore report **no inter-annotator
agreement (no κ)**, the study's most significant internal-validity limitation. Three factors partially
mitigate it: (1) the classes are load-bearing — each drives a scanner whose findings are objective, so
an incorrect class produces a falsifiable scanner result, not just a label; (2) causal claims are
required to be triangulated across ≥2 independent data sources before being recorded; and (3) the
*sabotage-validation* discipline independently confirms the proposed amplifier/concealer of many
incidents by deliberately re-introducing the mechanism. Postmortem quality varies with log retention;
two early incidents were reconstructed retroactively and are flagged as such.

**External validity.** One system, one host OS, one operator pair, eight weeks, ~40 jobs. As stated in
§3.8, we claim analytical generalization of the **mechanism classes and cross-cutting structures** and
explicitly disclaim generalization of **frequencies and shares**. The two settings that most bound
external validity are (1) systems *without* a synthesis-and-push pipeline, to which Class D
frequencies do not transfer (though the mechanism remains latent wherever an LLM consumes
machine-generated context), and (2) teams *without* an unusually attentive single operator, which
likely lowers the user-view discovery share. *Mitigation:* the taxonomy's mechanism orientation, the
public postmortem corpus, and the project-agnostic release of the governance engine
(`openclaw-ontology-engine` on PyPI) let other teams test whether the classes recur in *their*
systems — the empirical replication this case study invites.

**Reliability.** The repeatability threat in a single-operator study is that the procedure and chain of
evidence are not independently inspectable. *Mitigation:* every incident postmortem is a standalone
public document; the taxonomy's source-of-record is a single public catalog; the governance results,
test counts, and dates are **mechanically derivable from the public repository** at the cited commit
window; and a number-by-number *data inventory* maps every quantitative claim to its repository
source. A second researcher cannot re-run the eight weeks but can re-derive every number, re-read every
causal chain, and re-execute every scanner and sabotage test — the form of reliability appropriate for
a longitudinal field study.

**Researcher–instrument and AI-involvement.** The AI collaborator that co-wrote the postmortems also
co-wrote the paper, raising the possibility of **narrative bias toward a tidy story**. We treat this as
a first-class threat. *Mitigation:* the human author is the sole author and final arbiter of every
claim; all reported counts and dates are mechanically derivable from the public repository (the primary
external check on narrative bias); and the study's own findings are deliberately *unflattering to the
narrator* (0% ex-ante prevention; the best detector being a human, not the automation the author built;
the convergence engine the author is proud of having *caused* three of its own incidents) — an
internal-consistency signal that the account was not optimized for the system's, or the collaborator's,
image.

---

## 9. Conclusion

Eight weeks of complete postmortems from a production LLM agent runtime yield a five-class,
mechanism-oriented taxonomy of silent failures, of which one class — fail-plausible chained
fabrication — is specific to systems that speak. The quantified record is uncomfortable in useful
ways: a defense stack of thousands of tests and hundreds of declarative checks prevented *none* of the
novel incidents ex ante (while blocking 87% from recurring); the best detector was a human reading the
product; and the longest failures lived not in complex code but in the seams between simple, correct
parts. The constructive program that follows — postmortems as causal chains, lessons as meta-rules,
meta-rules as scanners, guards proven by sabotage, declared state converged by machine, observers kept
read-only, and complexity actively retired — is less a framework than a discipline, and it is the
discipline, not the specific artifacts, that we believe transfers.

The failure your agent system should frighten you with is not the crash. It is the confident
paragraph, on schedule, in perfect grammar, about a crisis that does not exist — pushed to a user who
has every reason to believe it, by a pipeline in which every component worked.

---

## Generative-AI Use Disclosure

This research used Anthropic's Claude (a large language model), accessed through a coding-agent
interface, as an engineering and writing collaborator. Claude was used to assist in triaging and
reconstructing incident postmortems; extract and tabulate quantitative data from the public source
repository; draft and edit prose; and propose document structure. Claude was *not* used to generate
empirical results, invent data, or make research claims: every quantitative figure reported in this
paper (incident counts, test and governance counts, dates, percentages) is mechanically derivable from
the public repository at the cited commit window, and was verified by the author against that
repository. The author defined all research questions, performed all incident classification, drew all
conclusions, and takes full responsibility for the entire content of this paper, including any errors.
No generative-AI system is an author of this work. Separately, for clarity: the *runtime system under
study* is itself an LLM agent system (powered by separate Qwen3-class models); the AI collaborator
described here (Claude) is the author's development tool, not the system being studied. The
relationship between the two is examined as a finding in Section 7.

---

## Acknowledgments

I thank the maintainers of the open-source agent framework on which the studied system is built, and
the open scientific-literature infrastructure (arXiv, Semantic Scholar, DBLP) that the runtime depends
on. I disclose, as described in the Generative-AI Use Disclosure, the use of Anthropic's Claude as an
engineering and writing collaborator throughout the construction of the studied system and the
preparation of this paper; the collaboration is itself part of the paper's subject matter (Section 7).
All conclusions, and all responsibility for them, are mine.

**Author Contributions (CRediT).** **Wei Wu** — Conceptualization, Methodology, Investigation
(operation of the production system; incident postmortems), Formal analysis (taxonomy construction,
validity analysis), Data curation, Writing – original draft, Writing – review & editing, Software (the
studied system and its governance engine). Sole author and guarantor. **AI-assistance statement
(non-author):** Anthropic's Claude provided tool-assisted support for postmortem drafting, repository
data extraction, prose drafting/editing, and document structuring, under the author's direction and
verification; as a generative-AI system it does not meet authorship criteria and is not credited as an
author.

---

## Artifact Availability

All 22 incident postmortems (`ontology/docs/cases/`), the canonical failure-mode catalog, the
governance ontology (90 invariants / 827 checks / 23 meta-rules), the 14 mechanized scanners, and the
full test suite are public in the system repository. The governance engine is additionally published as
a standalone, project-agnostic package on PyPI (`openclaw-ontology-engine`, v0.1.0): any agent-runtime
project can adopt the invariant/meta-rule/scanner framework with its own YAML configuration. This paper
is also available as preprint arXiv:2606.14589 (disclosed per IEEE preprint policy); a data inventory
mapping every number in this paper to its repository source accompanies the public draft.

---

## References

1. P. Huang, C. Guo, L. Zhou, J. R. Lorch, Y. Dang, M. Chintalapati, R. Yao. "Gray Failure: The
   Achilles' Heel of Cloud-Scale Systems." *HotOS* 2017.
2. H. S. Gunawi et al. "Fail-Slow at Scale: Evidence of Hardware Performance Faults in Large
   Production Systems." *USENIX FAST* 2018; extended in *ACM Transactions on Storage* 14(3), 2018.
3. M. Cemri, M. Z. Pan, S. Yang, et al. "Why Do Multi-Agent LLM Systems Fail?" (MAST). arXiv:2503.13657, 2025.
4. B. Ranganathan, M. Zhang, K. Wu. "Enhancing Reliability in AI Inference Services: An Empirical Study
   on Real Production Incidents." arXiv:2511.07424, 2025.
5. J. Zhou, J. Chen, Q. Lu, D. Zhao, L. Zhu. "SHIELDA: Structured Handling of Exceptions in LLM-Driven
   Agentic Workflows." arXiv:2508.07935, 2025.
6. Z. Xiao, J. Sun, J. Chen. "AIR: Improving Agent Safety through Incident Response." arXiv:2602.11749, 2026.
7. M. Taraghi, M. M. Morovati, F. Khomh. "Real Faults in Model Context Protocol (MCP) Software: A
   Comprehensive Taxonomy." arXiv:2603.05637, 2026.
8. B. Beyer, C. Jones, J. Petoff, N. R. Murphy (eds.). *Site Reliability Engineering: How Google Runs
   Production Systems.* O'Reilly, 2016.
9. C. Ezell, X. Roberts-Gaal, A. Chan. "Incident Analysis for AI Agents." *AAAI/ACM Conf. on AI, Ethics,
   and Society (AIES)* 2025; arXiv:2508.14231.
10. J. Owotogbe, I. Kumara, W.-J. van den Heuvel, D. A. Tamburri, A. K. Iannillo, R. Natella. "A
    Taxonomy of Runtime Faults in Model Context Protocol Servers." arXiv:2606.05339, 2026.
11. L. Huang, W. Yu, W. Ma, et al. "A Survey on Hallucination in Large Language Models: Principles,
    Taxonomy, Challenges, and Open Questions." arXiv:2311.05232, 2023 (rev. 2024).
12. A. Basiri, N. Behnam, R. de Rooij, et al. "Chaos Engineering." *IEEE Software* 33(3):35–41, 2016.
13. S. Ghosh, M. Shetty, C. Bansal, S. Nath. "How to Fight Production Incidents? An Empirical Study on a
    Large-Scale Cloud Service." *ACM SoCC* 2022 (Best Paper).
14. L. Zheng, W.-L. Chiang, Y. Sheng, et al. "Judging LLM-as-a-Judge with MT-Bench and Chatbot Arena."
    *NeurIPS* 2023; arXiv:2306.05685.
15. P. Runeson, M. Höst. "Guidelines for Conducting and Reporting Case Study Research in Software
    Engineering." *Empirical Software Engineering* 14(2):131–164, 2009.

---

## Trim priorities (delete this section before submission)

If the confirmed ISSRE Industry Track page limit is tight (commonly 6 pages + references in IEEE
2-column), trim in this order — each step preserves all load-bearing claims and the audited
scorecard:

1. **§2 Related Work** → compress each paragraph to 2–3 sentences (the four themes — distributed
   silent failure, LLM agent studies, hallucination-as-systems, operational practice — must each keep
   one anchor citation; the comparative framing vs. MAST [3] and the inference-incident study [4] is
   the part reviewers check, so keep those two contrasts).
2. **§4 narratives** → keep Table 1; keep D1 (with Fig. 1) and the reserved-file (E) incident in full;
   reduce B, C, and the other D incidents to one sentence of mechanism + one of consequence each.
3. **§6** → render the five pillars as a tight list (one sentence each) rather than paragraphs; keep
   the 53–92% fabrication-reduction number and the "audit observes and never mutates" rule.
4. **§5.4** → fold into §5.3 (the bug-class point can be a single paragraph).
5. **Last to cut:** §3 (method — the Runeson–Höst framing is what defends n=1 and must stay), §5.6
   (the 0%/87% scorecard — the paper's headline empirical result), §8 (threats), and the AI
   disclosure (IEEE-required). Never cut these.

**Do NOT trim by dropping the threats-to-validity section or the methodology framing** — they are the
single-case study's defense against the most likely reviewer objection ("one system, single author").

**Pitch line for the cover note:** *"A reliability-engineering field study: eight weeks, 22
silent-failure postmortems from one production LLM agent runtime, a mechanism taxonomy including the
LLM-specific fail-plausible class, and an audited defense framework (0% prevention / 87%
regression-blocking)."*
