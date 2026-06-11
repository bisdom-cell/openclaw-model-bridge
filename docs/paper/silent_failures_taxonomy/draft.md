# When Errors Become Narratives: A Longitudinal Taxonomy of Silent Failures in a Production LLM Agent Runtime

> **DRAFT v0.2 — 2026-06-11 (V37.9.139 session, second pass)**
> Working paper for arXiv (target category: cs.SE, cross-list cs.AI / cs.DC).
> Status: complete draft in Markdown; second pass done (related work expanded to 13 verified
> references incl. §2.4 SRE/chaos/AIOps; Fig. 3 D1 pollution-chain diagram added in ASCII).
> LaTeX conversion deferred until venue decision.
> All numbers traceable to repository state at commit window 2026-06-11 (`VERSION 0.37.9.70`,
> governance v3.56, data inventory in `data_inventory.md`).
> User-facing decisions pending: see `DECISIONS_NEEDED.md`.

**Alternative titles (user to choose):**

1. *When Errors Become Narratives: A Longitudinal Taxonomy of Silent Failures in a Production LLM Agent Runtime* (current)
2. *Silent Failures in LLM Agent Systems: An Eight-Week Longitudinal Study of 22 Production Incident Postmortems*
3. *Fail-Plausible: How LLM Agent Systems Turn Errors into Believable Output, and What 22 Production Incidents Taught Us About Defending Against It*

**Authors:** [USER NAME — domain expert, system owner], with Claude (Anthropic) as AI engineering collaborator — *attribution form pending user decision; see DECISIONS_NEEDED.md*

---

## Abstract

Large language model (LLM) agent systems are increasingly deployed as long-running, autonomous
runtimes — orchestrating scheduled jobs, calling tools, maintaining memory, and pushing results
to humans over messaging channels. We present a longitudinal empirical study of *silent
failures* in one such system: a personal-assistant agent runtime in continuous production since
March 2026, comprising roughly 40 scheduled jobs, 8 LLM providers, a tool-governance proxy, and
a knowledge-base memory plane, defended by 4,286 unit tests and 827 declarative governance
checks. Over an eight-week window we documented 22 incidents with full root-cause postmortems,
within which a single meta-pattern — *a failure whose error signal never reaches a human in
actionable form* — manifested at least 28 times.

From these postmortems we derive a five-class, mechanism-oriented taxonomy of silent failure:
(A) environment and platform quirks, (B) design-assumption mismatches, (C) error swallowing and
dilution, (D) chained hallucination and fabrication, and (E) operational omission and forensic
blind spots. Class D is, to our knowledge, specific to LLM-based systems and the most dangerous:
the system does not merely fail to report an error — the LLM actively *transforms* the error
into fluent, plausible narrative content delivered to the user. We term this behavior
**fail-plausible**, and position it as the LLM-era escalation of *gray failure*'s differential
observability: the observer is not just blind, it is being convincingly lied to by the failure
itself.

Three cross-cutting findings challenge common assumptions about agent reliability engineering.
First, discovery channel: roughly 70% of silent failures were ultimately caught by *human
user-view observation* of system output, not by unit tests, health checks, or governance audits
— all of which stayed green through most incidents. Second, a retrospective audit of 15
incidents found our declarative governance layer had a **0% ex-ante prevention rate but an 87%
ex-post regression-blocking rate** — audits are regression engines, not prediction engines.
Third, incident latency (13 hours to 60 days of silence) correlates with failure mechanism, not
code complexity: the longest-lived failures lived in the *seams* between components — deployment
topology, cross-script contracts, observer–observed coupling — where, by construction, no test
runs. We describe the defense framework that emerged (meta-rules, mechanized scanners,
sabotage-validated invariants, a declared-state convergence engine, and layered anti-fabrication
guards), and distill design principles for engineering LLM agent systems whose failures are loud,
attributable, and boring. All 22 postmortems, the governance engine, and the defense framework
are publicly available.

---

## 1. Introduction

The reliability literature has long known that the most damaging failures in production systems
are not crashes. *Gray failures* [Huang et al. 2017] degrade cloud systems while their failure
detectors report health; *fail-slow* hardware [Gunawi et al. 2018] throttles performance for
hours before anyone suspects the disk. The defining property of both is **differential
observability**: the application suffers, but the observer designed to notice does not.

LLM agent systems inherit this entire problem class — and then add a new one. An agent runtime
is a generator of fluent language. When an upstream error leaks into its context window, the
system's failure mode is not silence; it is *plausible speech*. In one incident we document
(§4.4), an HTTP 400 error page was captured into a cache by a logging bug, and the downstream
LLM — seeing error strings where signals should be — confidently fabricated an industry analysis
titled around a "Hugging Face platform crisis" and pushed it to the user as a routine insight
digest. No detector fired. Every test was green. The error had not disappeared; it had been
*narrated*.

We call this behavior **fail-plausible**: a failure mode in which the system transforms an
internal error into coherent, contextually appropriate, and false output. Fail-plausible is to
LLM agent systems what gray failure was to cloud infrastructure — the dominant, hardest-to-see
failure class — but it is strictly worse for the observer: gray failure starves the failure
detector of signal, while fail-plausible feeds the human a counterfeit one.

This paper reports what eight weeks of documented production incidents in a real, continuously
operating LLM agent runtime taught us about silent failures, in five contributions:

1. **A mechanism-oriented taxonomy** (§4) of five silent-failure classes derived from 22 fully
   documented production incidents, each with a complete causal-chain postmortem. We classify by
   *failure mechanism* rather than by failure location, because the same mechanism recurs across
   unrelated components, and a defense against a mechanism immunizes a class rather than a file.

2. **The fail-plausible failure class** (§4.4), with four documented incidents in which LLMs
   converted polluted context (error logs, stale alerts, injected summaries) into confident
   fabrications delivered to the user — including fabricated software releases, fabricated
   platform crises, and fabricated remediation instructions for the host operating system.

3. **Quantified cross-cutting findings** (§5): incident latency distribution (13 hours to 60
   days), discovery-channel distribution (~70% human user-view; unit tests close to 0% for this
   failure class), a three-layer root-cause structure (trigger / amplifier / concealer) present
   in nearly every incident, and the empirical observation that one meta-pattern manifested ≥28
   times across all five classes — silent failure is a *bug class*, not a bug.

4. **An audited defense framework** (§6) and its honest scorecard: a retrospective Q1/Q2/Q3
   audit of 15 incidents showing 0% ex-ante prevention but 87% ex-post regression blocking,
   leading to the position that *audit is a regression engine, not a prediction engine*; a
   three-step defense maturation path (point fix → meta-rule → mechanized scanner) with evidence
   that lessons stopping at step one recur within days; and sabotage validation (deliberately
   breaking the system to prove each guard actually fires).

5. **A complexity argument** (§7): the longest-latency failures did not live in complex
   components but in *seams* — between repository and deployment, between dev and target OS,
   between declared state and runtime state, between observer and observed. We argue that agent
   reliability engineering should optimize for *seam reduction* (representation unification,
   declared-state convergence, read-only observers) over defense accretion, and report our
   adoption of an explicit "Sunset Law" — a standing rule that retiring complexity outranks
   adding protection.

Our study is a single-system longitudinal case study — the methodological complement of recent
horizontal studies that sample failures across many frameworks from benchmark execution traces
[Cemri et al. 2025] or across many customers from a provider's incident database
[Ranganathan et al. 2025]. Horizontal studies establish breadth; what they cannot see is the
longitudinal texture of production silent failure — multi-week latency, discovery channels,
defense evolution, recurrence of "fixed" lessons — because those only exist in a system that
runs continuously, pushes output to a real user, and keeps complete postmortems. That texture is
the subject of this paper.

---

## 2. Background and Related Work

### 2.1 Silent failures in distributed systems

Huang et al. introduced *gray failure* as the failure mode behind most cloud incidents:
components degrade while failure detectors report health, formalized as **differential
observability** between the application's view and the observer's view [Huang et al. 2017].
Gunawi et al.'s *fail-slow at scale* study collected 101 (later 114) incident reports of
hardware performance faults across 12–14 institutions, documenting cascading root causes,
fault conversion (one form turning into another), and multi-hundred-hour diagnosis costs
[Gunawi et al. 2018]. Our study is methodologically downstream of this tradition — taxonomy
from production incident reports — but at a different layer (an LLM agent runtime rather than
cloud infrastructure or hardware) and with a different lens (every incident in our corpus is a
*silent* failure by selection; crashes that announced themselves were handled as routine
operations and did not generate postmortems).

Two of our five classes (C: error swallowing/dilution, E: operational omission) will look
familiar to readers of that literature. Class D will not.

### 2.2 LLM agent failure studies

Cemri et al.'s MAST is the first empirically grounded taxonomy of multi-agent LLM system
failures: 14 failure modes in 3 categories (specification issues, inter-agent misalignment,
task verification), built from 1,600+ annotated execution traces across 7 frameworks with
strong inter-annotator agreement (Cohen's κ = 0.88) [Cemri et al. 2025]. MAST's unit of
analysis is the *benchmark task trace*; failures are mostly visible in the trace as task
non-completion or wrong answers. Our unit of analysis is the *production incident*: failures
that by definition did **not** visibly fail any task, kept all detectors green, and were
discovered hours-to-months later. The two taxonomies are complementary: MAST describes why
agent collectives fail at tasks; we describe why an agent *system in operation* fails its
operator without anyone noticing.

A recent provider-side study analyzed 156 high-severity LLM inference incidents and derived a
four-way operational taxonomy (infrastructure, model configuration, inference engine,
operational failures) [Ranganathan et al. 2025]. That work shares our production-incident grounding
but covers the *inference service* layer below agents, where failures manifest as availability
and latency violations — loud by nature. Ezell et al. argue for structured incident analysis
of AI agents and propose what information agent incident reports should contain [Ezell et al.
2025]; our corpus can be read as an existence proof of their program — 22 reports collected
under a fixed postmortem protocol — and our experience adds a requirement their framework
should absorb: the report must record *how long the incident was silent and who finally
noticed*, because for this failure class those two fields carry most of the engineering
signal (§5.1–5.2). Related systems work addresses exception handling in agentic workflows
(SHIELDA [Zhou et al. 2025]), incident response for agent safety (AIR [Xiao et al. 2026]),
and fault taxonomies for the Model Context Protocol ecosystem [Taraghi et al. 2026; Owotogbe
et al. 2026]. None of these focuses on the silent/fail-plausible class, the longitudinal
latency-and-discovery question, or defense-framework evolution under real recurrence pressure.

### 2.3 Hallucination research

Hallucination is usually studied as a *model* property — ungrounded generation measured
against references, with taxonomies of intrinsic/extrinsic and factuality/faithfulness
variants, detection benchmarks, and model-side mitigations [Huang et al. 2023]. Our Class D
reframes it as a *systems* property: in 4/4 documented fabrication
incidents the model behaved exactly as trained (fluent completion over its context); the failure
was that **the system delivered polluted context** (error logs captured by command substitution
into a cache; stale alert messages persisted into the chat history; injected cross-day summaries
without provenance marking). The defense consequently is not model-side but system-side: context
hygiene (stderr discipline, alert stripping before context assembly), provenance labeling
(source-credibility tiers), and layered anti-fabrication guards with explicit, literal
prohibitions. This systems view of hallucination — *garbage context in, confident narrative out*
— is a distinct contribution relative to model-centric hallucination work.

### 2.4 Operational practice: SRE, chaos engineering, and incident studies

Site Reliability Engineering codified error budgets, postmortem culture, and the principle
that operational knowledge must be engineered rather than remembered [Beyer et al. 2016];
our convergence engine (§6) is that principle applied to an agent runtime's declared state.
Chaos engineering established deliberate fault injection as the way to gain confidence in a
system's resilience [Basiri et al. 2016]; our framework applies the same epistemology one
level up — *sabotage validation* (§6) injects violations to gain confidence in the **guards**,
because in a silent-failure regime an unvalidated detector is indistinguishable from a vacuous
one, a lesson we learned from 67 checks that had silently executed empty strings for months.
Large-scale incident studies such as Ghosh et al.'s analysis of 152 severe incidents in a
production cloud service [Ghosh et al. 2022] established the empirical template — incident
corpus, root-cause and mitigation coding, automation gap analysis — that we instantiate for
an agent runtime; the variable our setting adds is that the system under study *speaks*, which
changes both what failure looks like (§4.4) and what detection requires (§5.2).

---

## 3. System Context and Methodology

### 3.1 The system under study

The subject system (public repository `openclaw-model-bridge`) is a two-layer middleware that
connects self-hosted and commercial LLMs to OpenClaw, an open-source personal-agent framework
bridging WhatsApp and Discord. It has been in continuous production on a single macOS host
since early March 2026. Architecturally it follows a three-plane design:

- **Control plane** — a tool-governance proxy (tool filtering to a hard cap, schema repair,
  alert-context stripping, custom tool interception), a declarative governance engine
  (90 invariants, 827 checks, 23 meta-rules, 15 mechanized discovery scanners, daily audit
  cron), SLO monitoring, circuit breakers, and a convergence engine that machine-synchronizes
  declared state (job registry, provider registry, service registry) to runtime state
  (crontab, fallback chains, launchd).
- **Capability plane** — an adapter routing chat/completions across 8 providers (self-hosted
  Qwen3-235B primary, Qwen2.5-VL multimodal route, plus commercial fallbacks) with
  capability-scored automatic fallback chains.
- **Memory plane** — a knowledge base (~1,100+ notes, local-embedding RAG index), multimodal
  media index, conversation-harvest pipeline, and daily LLM-driven synthesis jobs ("dream",
  "evening digest", "deep dive") that push insights to the user.

Scale snapshot at the study cutoff (2026-06-11): ~40 registered scheduled jobs across two cron
schedulers; 8 LLM providers; 3 supervised long-running services; 4,286 unit tests in 121
suites; 22 published incident postmortems. One human operator (the system owner) and one AI
engineering collaborator (Claude, used through a coding-agent interface) develop and operate
the system; the agent runtime itself is powered by separate models (Qwen3-class), making this —
to our knowledge — also a data point on AI-assisted operation of an AI system.

### 3.2 Incident corpus and postmortem protocol

The corpus is every incident between 2026-04-09 and 2026-06-02 that (a) reached production,
(b) had a silent phase — a period in which the system was failing or had failed while all
automated indicators stayed green — and (c) was closed with a full postmortem. 22 incidents
qualify. Postmortems follow a mandatory in-repo protocol (the "exception-analysis
constitution") requiring, before any fix:

1. **Full causal-chain diagram** — timeline × layer × logic × architecture, from upstream
   trigger to user-perceived symptom, with code-branch truth values annotated;
2. **Three-layer root cause** — *trigger* (what external event ignited it), *amplifier* (what
   architectural flaw spread it), *concealer* (what absence hid it until a human noticed);
3. **Timeline reconstruction** with per-minute precision where logs allow;
4. **Condition-combination analysis** — why now and not before: which set of individually
   benign, often long-latent conditions first co-occurred;
5. **Feeding the governance ontology** — new invariants, meta-rule candidacy, catalog entry.

The protocol was itself a product of early incidents (it was adopted after the second incident
in the corpus) and was applied retroactively to the first two. Each postmortem is a standalone
document in the public repository (`ontology/docs/cases/`), and the consolidated catalog
(`ontology/docs/failure_modes_catalog.md`) is the canonical index from which this paper's
taxonomy is built.

### 3.3 Taxonomy construction

We classify by **mechanism** (how the error evaded observation) rather than by **location**
(which job or file failed). The location-oriented draft of the catalog was produced first and
discarded: the same mechanism (e.g., positional parsing of LLM output; copy-pasted
error-suppression idioms) recurred across unrelated jobs, so location classes had no predictive
or defensive value, while one mechanism-level defense (e.g., a repo-wide scanner for the
suppression idiom) immunized every location at once. Classes were derived iteratively: each new
postmortem was matched against existing classes' "true root cause" column; two consecutive
non-matches forced a class split or a new class. The five-class scheme has been stable since
mid-May 2026 over the last 9 incidents.

Two limitations are flagged here and expanded in §8: classification was performed by the two
system operators (human + AI collaborator) without independent annotators, so we report no
inter-annotator agreement; and selection requires a *completed* postmortem, so failures still
silent today are by construction absent (we discuss this surviving-silence bias in §8).

### 3.4 The meta-pattern counter

Early in the study we adopted a meta-rule, **MR-4 "silent-failure-is-a-bug"**, and began
counting its *manifestations* — distinct incidents or sub-events in which an error signal
existed somewhere in the system but never reached a human in actionable form. The counter
reached ≥28 across the 22 incidents (several incidents contain multiple distinct
manifestations; numbering has deliberate gaps where manifestations were recorded inside hotfix
notes). We report the counter not for its precision but for its shape: manifestations continued
to appear at a roughly constant rate even as defenses accumulated — in *new forms* each time
(§5.4). This is the empirical basis for treating silent failure as a bug class rather than a
finite list of bugs.

---

## 4. A Taxonomy of Silent Failures

Table 1 summarizes the five classes. Full per-incident detail is in the public catalog; here we
define each class, give its mechanism signature, and narrate one or two representative
incidents.

**Table 1 — Five classes over 22 incidents (+ sub-events).**

| Class | Mechanism | Incidents | Silence span | Defining property |
|---|---|---|---|---|
| A — Environment/platform quirk | Logic correct; runtime environment's implicit behavior defeats it | 1 (+6 sub-events) | hours–weeks | dev always green; only target OS/client reveals |
| B — Design-assumption mismatch | Code assumes a deployment topology / contract / input shape that reality violates | 4 | days | unit tests cover the assumption, not reality |
| C — Error swallowing & dilution | Error occurs, is eaten by a layer or stripped of cause across layers | 5 | hours–days | the alert that arrives carries no usable information |
| D — Chained hallucination & fabrication | LLM converts polluted context into confident false output | 4 | hours–days | **fail-plausible**: user receives counterfeit health |
| E — Operational omission & forensic blind spot | Deployment/registration step skipped; or the forensic tool itself is blocked and reads as "normal" | 8 | days–**60 days** | declared state ≠ runtime state; diagnosis instruments lie |

### 4.1 Class A — Environment and platform quirks

*The logic is right; the environment's implicit behavior is not what anyone assumed.* The
development environment (Linux container, root, GNU userland, bash 5) is systematically more
permissive than the production target (macOS, bash 3.2 as `/bin/bash`, BSD userland, zsh
interactive shell, sandboxed cron). Documented sub-events include: bash 3.2 not propagating ERR
traps into functions without `set -E`, silently disarming a watchdog's self-alarm; BSD awk
aborting on invalid UTF-8 multibyte sequences, which — combined with `set -e` and `pipefail` —
killed the *monitoring* script itself for 7 days (§4.5 overlaps); the absence of GNU `timeout`
on stock macOS turning a defensive wrapper into a universal "tool unavailable" failure; CJK
full-width punctuation adjacent to unbraced shell variables parsing as part of the variable
name; and a messaging client folding long messages at an undocumented ~4,000-character
threshold, silently changing the user-visible shape of every long push.

The class signature is **green dev, silent prod**, and its defense is necessarily mechanized:
a cross-OS quirk scanner now encodes six known quirk patterns as repository-wide checks, and a
meta-rule requires framework-level fixes to be validated *on the target environment*, after a
fix validated only in dev shipped broken twice in one day.

### 4.2 Class B — Design-assumption mismatches

*The code is internally consistent with an assumption; production violates the assumption.*
Representative incident: a metadata-resolution function shipped with three path candidates for
locating a registry file, all of which missed the file's actual production location — because
the deployment pipeline placed it under a path none of the candidates covered. The component
fell back, silently, to an unfiltered mode for **five days**; its own log line announcing the
fallback scrolled past unread, and a companion feature (writing health scores into shared
state) silently never executed at all. Unit tests were green throughout: they tested the
resolution *logic*, with fixtures laid out according to the same wrong assumption.

A second representative: an LLM-output parser indexed lines positionally (`lines[i+1]`,
`lines[i+2]`, stride 3). The model occasionally omits one line — instruction-following is a
distribution, not a contract — after which every subsequent field shifted one slot, and users
received messages whose "title" field contained a literal separator string. The mechanism
generalizes: **any positional parse of LLM output is a latent Class B failure**, which we later
froze into a meta-rule (parsers must be key-based, never positional) and a scanner.

The class signature is *tests mirror the assumption rather than the caller*: in a later
incident the test suite constructed inputs with a heading format the production caller never
produces, passing 12 tests while production emitted 3 message windows instead of 4. Defense:
explicit cross-script contracts, deployment-layout testing on target, scanners that walk every
path-resolution function and assert the production-canonical candidate is present.

### 4.3 Class C — Error swallowing and dilution

*The error happens and is reported — into a void.* Three mechanism variants:

**Swallowing.** A summary function counted only `status=="fail"` results; invariants whose
check raised an *exception* (`status=="error"`) vanished, and the governance audit printed
"all invariants hold" over a pile of dead checks. The observer had no observer — we return to
this as the *observer's own blind spot* in §6. In a later echo of the same class, a governance
executor read check code from one YAML field while 67 checks (across 21 invariants) had their
code in a differently named field: all 67 executed `exec("")` — vacuous passes — for months,
and were discovered only when a *new* invariant's sabotage validation refused to fail.

**Dilution.** An evening digest failed with "HTTP 502: Bad Gateway" for two consecutive days.
The true cause — the fallback provider's free-tier quota exhausted by daytime jobs, after the
primary's circuit breaker opened — was present in the upstream response body, which the
adapter wrapped into a 502, whose body the proxy never read (`str(e)` only), whose remnant the
client reduced to `f"HTTP {code}: {reason}"`. Three hops, each individually reasonable, each
stripping cause; the alert that reached the human contained zero actionable bits. The meta-rule
that emerged — *error chains must preserve upstream cause across layers* — is the LLM-pipeline
restatement of a classic distributed-tracing lesson, but with a twist: in agent systems the
"client" of the error is often another LLM prompt, so a diluted error is one step from becoming
Class D fabrication input.

**Amplified swallowing.** An automated batch tool injected an environment-variable read into 8
job scripts without injecting the corresponding `import` — a `NameError` in all 8, caught by a
fail-open guard, which dutifully skipped the new validation logic in all 8 while every test and
syntax check passed. Automation is a bug amplifier: a single wrong design decision propagates
to N sites with machine efficiency, and `bash -n` cannot see inside a Python heredoc.

### 4.4 Class D — Chained hallucination and fabrication (fail-plausible)

*The most dangerous class, and the one without precedent in the gray-failure literature.* The
error is not suppressed; it is **transformed**. Four incidents:

**D1 — The fabricated platform crisis.** A nightly knowledge-synthesis job (`dream`) collects
signals from ~290 notes via map-reduce LLM calls. A Unicode surrogate in scraped content made
`json.dump` raise mid-write, producing a truncated request body, a 400 from the adapter — and
here the chain turns: the map step's logging function wrote diagnostics to **stdout**, the
caller captured stdout by command substitution as the *signal payload*, and the cache filled
with `HTTP Error 400: Bad JSON` strings. The reduce-step LLM, prompted to find cross-domain
signals, did exactly what language models do with anomalous-but-thematic context: it composed a
confident analysis of a "Hugging Face platform crisis" — platform trouble being the most
probable narrative shell for error-code vocabulary — and the system pushed it to the user as a
routine insight digest. Every component succeeded; the pipeline laundered an encoding bug into
industry analysis (Fig. 3). The structural fix was four lines deep in defense (stderr
discipline, surrogate sanitization, encoding error policy, anti-pollution prompt guards), but
the load-bearing one was a single `>&2`: **one redirection operator severed the entire
hallucination chain.**

```
Fig. 3 — The D1 pollution chain: from one malformed byte to a fabricated
         industry analysis. Every component behaves as designed.

  scraped content with isolated UTF-16 surrogate (U+D800–DFFF)
        │
        ▼
  json.dump(body, ensure_ascii=False)──UnicodeEncodeError──► request body
        │                                                    TRUNCATED
        ▼
  adapter: json.loads fails ──► HTTP 400 "Bad JSON" (456-byte HTML error page)
        │
        ▼                              ┌──────────────────────────────────┐
  llm_call(): extraction empty;       │ AMPLIFIER                        │
  log() prints error dump to STDOUT ──┤ signals=$(llm_call …)            │
  (4 deterministic retries, same)     │ command substitution captures    │
        │                             │ stdout → error log becomes the   │
        ▼                             │ "signal" payload                 │
  cache file := "…Error code: 400    └──────────────────────────────────┘
   Bad JSON… Waiting 3s before retry…"
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
  Defense-in-depth added: surrogate sanitization · encoding error policy ·
  anti-pollution prompt guards ("never treat error codes / tool names /
  HTTP status text as external signals")
```

**D2 — The fabricated remediation.** A system alert pushed by a watchdog was persisted into
the chat session history as an ordinary assistant message. Thirty-six minutes later the user
asked an unrelated architecture question; the model, attending across the contaminated context,
replied that it had "received the system alert follow-up task" and instructed the user to grant
Full Disk Access to a cron binary in macOS System Preferences — instructions that were not only
off-topic but *technically fabricated* for the scenario. Alert traffic and conversation are
different speech acts; letting them share a context window invites the model to weave them into
one narrative. (A later forensic twist worth reporting: weeks afterward, an unrelated 60-day
investigation (§4.5) established that the *general remediation direction* the model fabricated
— FDA for cron-derived processes — was coincidentally relevant to a real problem elsewhere in
the system. Fabrication is not refuted by occasional accuracy; it is defined by absence of
grounding.)

**D3 — The fabricated success.** A weekly review job whose LLM call failed fell back to a
mechanical line-filter that emitted leftover container headings as if they were review content,
and wrote `"llm": true` into its status file unconditionally. Six co-located issues each made
the others harder to see; the user-visible artifact looked plausible enough to pass casual
inspection for weeks. Fabrication does not require a model: **a fallback path that
manufactures plausible-shaped output is a hallucination implemented in shell.**

**D4 — The fabricated release.** An evening digest, given a list of the day's high-alignment
papers as context enrichment, inferred that the user's project "must have shipped" and
announced a community release of an internal version number that exists only in the project
changelog — inventing a source tag for it. The injection of *true but unlabeled* context
produced false attribution: provenance-free enrichment is fabrication fuel.

The common structure is a **pollution chain**: a Class A/B/C failure deposits non-signal
content where a downstream LLM expects signal; the LLM performs its function — fluent,
coherent completion — and the output inherits the *form* of health with the *content* of
failure. Defense is therefore system-side context hygiene, applied at every link: stderr
discipline so diagnostics can never enter data channels (MR-11); alert stripping before context
assembly; provenance/credibility labeling of injected content; and a six-level cumulative
ladder of anti-fabrication prompt guards (from "never invent facts" through literal prohibited
phrases extracted from actual incidents, e.g. the exact fabricated release string), shared as a
single imported module across all nine LLM-calling jobs rather than copy-pasted. We make no
claim that prompt guards are sufficient — they are the *last* layer, behind hygiene and
provenance, and §5.5 shows why layered placement matters more than any single guard.

### 4.5 Class E — Operational omission and forensic blind spots

*The code is right; an operational step never happened — or the diagnostic instrument itself is
compromised.* This is the largest class (8 incidents) and contains the longest silences.

**Omission.** A new daily-analysis job was fully implemented, tested, registered in the job
registry, and deployed — and never ran, because the final step (writing the crontab line) was a
human memory item. Three independent small bugs conspired to hide it: the preflight checker
grepped for only one of two registry-drift warning strings; the crontab helper did not check
its own install exit code and compared counts with `<` instead of equality (reporting ✅ on a
rejected install); and the job's absence produced no log to scan. The incident generalized into
the system's largest architectural correction: **declared state must converge to runtime state
via machine, not memory** (MR-17), implemented as a convergence engine that diffs registry
declarations against observed crontab/launchd/provider state on every audit, with a staged
escalation path (alert-only → dry-run → machine-sync) and, after a one-week zero-drift
observation window per spec, automatic synchronization.

**The reserved-file incident** deserves narration for its shape. The agent, completing an
alert-handling task, wrote "task complete" notes into a file named `HEARTBEAT.md` in its
workspace — to the agent, a scratch filename; to the runtime, a *reserved control file* whose
non-empty content activates a heartbeat protocol that instructs the model to reply with a bare
acknowledgment token if nothing needs attention. The gateway then strips that token from
outbound messages. Result: for 13 hours, every user message received a reply of `HEARTBEAT_OK`,
stripped to nothing in transit — **total silence, with every component functioning exactly as
designed.** The model had, in effect, been handed a pen that doubled as the system's mute
button. The general lesson became a meta-rule: any file path carrying special runtime semantics
must be unwritable by the LLM's generic file tools (write attempts are intercepted at the proxy
and rewritten to a comments-only placeholder).

**Forensic blind spots.** The longest silence in the corpus — 60 days — was an external-SSD
backup path failing with EPERM. Six successive hypotheses (filesystem format, ownership UIDs,
ACLs, daemon contention, snapshot locks, physical disconnection) were each falsified by data
over multiple weeks; the breakthrough came only from the OS's own audit log (`log show`),
which revealed macOS TCC sandbox denials: cron-derived processes lack Full Disk Access by
default. The deeper finding is methodological: during those weeks, the *forensic collectors
themselves* (lsof, ACL listing, snapshot enumeration) were being silently denied by the same
sandbox and returning empty output — which the diagnosis pipeline recorded as "normal/empty".
**An instrument that cannot distinguish "nothing there" from "I was not allowed to look" will
manufacture false reassurance**; all collectors now capture stderr separately and tag
`[sandbox_denied]` as a first-class observation. The companion lesson — every falsified
hypothesis was answered by *adding a forensic dimension*, never by speculative code change —
is the discipline we found most transferable.

Also in this class: a 9-hour total gateway outage whose three independent alarms each failed
(a quiet-hours filter suppressing both channels including the one designed for emergencies; a
keepalive that logged WARN without alerting; a restart script that reported success without
verifying health) — yielding the meta-rule that **an alert path must not depend on the failing
subject** (gateway-down alerts must travel a channel the gateway cannot take down); and the
watchdog that died of a Class A quirk and stayed dead for 7 days because nothing watched the
watcher (now: ERR-trap self-alarms plus a heartbeat canary file that an independent job can
age-check).

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

Latency tracks the **mechanism layer, not code complexity**. Code-level bugs die young: unit
tests and the next run catch them. What survives for weeks lives where no test runs — in
deployment topology, OS policy, monitoring-of-monitoring, and the gap between declared and
runtime state. Latency is therefore a *measure of observational distance*: the further a
mechanism sits from any existing observer, the longer it lives. We suggest silence-latency
percentiles as a reportable reliability metric for agent systems, complementary to MTTR — our
experience is that for silent failures, time-to-*detect* dominates time-to-repair by one to
two orders of magnitude.

### 5.2 Discovery: who finally notices

| Channel | Share (qualitative) | Notes |
|---|---|---|
| **Human user-view** (reading actual pushed output; weekly observation ritual) | **~70%** | "this digest looks shallow", "why two windows?", "didn't receive yesterday's analysis" |
| Target-environment execution (dev green, prod reveals) | high | all Class A; several Class B |
| Self-observation (governance auditing governance; observer critiquing output) | rising | added mid-study; caught its own executor bug |
| Unit tests / preflight | ≈0 for this corpus | by selection: anything they caught never became a silent incident |

The last row is partly tautological — our corpus selects for what tests missed — but the
magnitude of the first row is not. The system runs 4,286 tests, 827 governance checks, and a
19-point preflight, all green through most of these incidents; the single most productive
detector of silent failure was **a human looking at the product as a user**. We
institutionalized this as a weekly 30-minute observation ritual (no coding allowed; four
dimensions: alert noise, push latency, information density, response quality) — and it
continued to out-detect the automated stack. For practitioners, the implication is blunt:
*user-view observation is a first-class observability signal and deserves calendar time*; for
researchers, the open problem is mechanizing even part of what the human eye does here. The
LLM-as-judge literature gives grounds for optimism — strong LLM judges reach >80% agreement
with human preference on open-ended response quality [Zheng et al. 2023] — and our own step
in that direction, a daily LLM "observer" that critiques yesterday's outputs against quality
heuristics, found real regressions (including a fabrication, and including two bugs in
*itself*). The qualifier our experience adds to that literature: an LLM judging a *system's
output for silent failure* is itself an LLM component of that system, inheriting every class
in this taxonomy — ours shipped with a Class B path bug and a sampling artifact that made it
hallucinate a truncation — so the judge needs the same governance, provenance hygiene, and
sabotage validation as the components it judges.

### 5.3 The trigger–amplifier–concealer structure

Nearly every postmortem decomposed into three causally distinct layers:

```
trigger     the external spark        (a surrogate byte; an omitted LLM output line; a transient EPERM)
amplifier   the architectural flaw    (stdout logging into command substitution; positional parsing;
            that spreads it            18 copy-pasted suppression idioms)
concealer   the absence that hides it (status file lying "ok"; fail-open guard; quiet-hours filter;
                                       forensic tool silently denied)
```

The practical force of the decomposition is prescriptive: **a fix that addresses only the
trigger is cosmetic**. Triggers are unbounded (the environment will always produce another
malformed byte, another quirk); amplifiers and concealers are finite and owned by the
architecture. Every recurrence in our corpus (§5.4) traces to a fix that stopped at the
trigger. Conversely, the highest-leverage single fixes were amplifier-level (one `>&2`;
one shared helper replacing 20 idiom copies) and concealer-level (fail-loud with cause;
forensic stderr tagging).

### 5.4 Silent failure is a bug class, not a bug

The MR-4 manifestation counter (§3.4) kept advancing at a roughly constant rate across the
study — but never twice in the same form. Early manifestations were classic swallowing;
middle-period ones were dilution and fabrication; late ones included *a fix introducing a
deeper silence* (a retry helper whose exit-code propagation silently killed twenty `set -e`
callers mid-script — the fix for a silent failure became a worse one), *a correct fix to the
wrong bug* (an ownership repair that was real but irrelevant to the EPERM it targeted, exposed
only because the metric refused to move), and *vacuous verification* (the 67 checks executing
empty strings). The lesson we draw is structural: silent failure cannot be enumerated and
fixed; it can only be **governed** — by meta-rules that constrain whole mechanism families,
scanners that enforce the meta-rules mechanically, and validation that the guards themselves
are alive (§6).

### 5.5 Defense maturation: point fix → meta-rule → scanner

Defense effectiveness in our record correlates with how far a lesson traveled along a
three-step path:

1. **Point fix** — repair this bug. *Empirically insufficient:* an import-omission bug fixed
   at one site recurred at eight sites via an automated injector **two days later**, because
   the lesson existed only as a diff.
2. **Meta-rule** — write the lesson as a named, cross-case rule (23 such rules by study end;
   e.g., diagnostics-to-stderr; key-based parsing; alert-path independence; declared-state
   convergence; reserved-file unwritability). Necessary but memory-bound: rules constrain
   authors who remember them.
3. **Mechanized scanner** — encode the meta-rule as a repository-wide check that runs in CI
   and the daily audit (15 such scanners by study end: cross-OS quirks, path consistency,
   heredoc import closure, monitor self-alarm compliance, cross-environment path resolution,
   …). Only at this step does recurrence become structurally impossible rather than
   culturally discouraged.

Every meta-rule that reached step 3 has zero recorded recurrences as of the study cutoff;
every recurrence in the corpus involves a lesson that had stopped at step 1.

### 5.6 Audit is a regression engine, not a prediction engine

Mid-study we audited our own defense framework against the first 15 incidents with three
questions per incident: Q1 — could the audit, as it existed *before* the incident, have caught
it? Q2 — why not? Q3 — do the guards added *after* it block the same class going forward?
Results:

| Metric | Value |
|---|---|
| Ex-ante prevention (Q1 fully) | **0 / 15 = 0%** |
| Partial early warning | 2 / 15 = 13% |
| Ex-post regression blocking (Q3 ≥ half) | **13 / 15 = 87%** |
| Root cause of misses: a dimension the audit had never conceived | 12 / 15 = 80% |

The 0% is not an indictment of the audit — it is its *job description*. 80% of misses were
"blank categories": dimensions no invariant had ever contemplated, which no amount of
diligence within the existing dimension set would have covered. Audits, like regression test
suites, encode the past. The honest engineering posture is to (a) maximize the regression rate
(ours: 87%, with sabotage validation pushing reliability of that number), (b) accept that
prevention of *novel* mechanism classes comes from elsewhere — user-view observation,
adversarial review ("what could break that we would not notice?"), and target-environment
exposure — and (c) measure the *conversion latency* from novel incident to mechanized guard,
which our three-step path (§5.5) is designed to minimize. A complementary adversarial audit
(16 destruction scenarios injected against the live repository: 10 replaying known incidents,
6 probing suspected blind spots) scored 16/16 after the blind-spot batch drove its own round
of guard additions — a useful exercise precisely because its first run did not score 16/16.

---

## 6. The Defense Framework That Emerged

We summarize the framework not as a recommendation of its specifics but as an existence proof
of one coherent answer, with its audited scorecard (§5.6) attached. Five pillars:

**1. Declarative governance with mandatory depth.** 90 invariants / 827 checks in a YAML
ontology, executed by an engine on a daily audit cron and in CI. Each invariant declares its
meta-rule lineage, severity, and *verification layers*; critical invariants are mechanically
required to verify at ≥2 layers (declaration-level greps are not allowed to stand alone —
a rule the system enforces on itself via a meta-invariant, after single-layer greps
demonstrably failed to notice behavioral regressions).

**2. Sabotage validation ("test the test").** Every new guard must be proven *alive* by
deliberately introducing the violation it targets and observing it fire — then reverting. This
discipline caught, among others: guards that matched their own assertion strings
(self-referential greps), the 67 vacuous checks (§4.3, found when a sabotage refused to fail),
and tests whose fixtures mirrored the wrong assumption (§4.2). In a system whose primary
failure class is *silent*, an unvalidated guard is indistinguishable from a vacuous one.

**3. Declared-state convergence.** A convergence engine diffs every declared registry (jobs,
providers, services, KB sources, runtime config) against observed runtime state on each audit,
with per-spec staged escalation: alert-only → machine-sync with dry-run default → live
synchronization after a one-week zero-drift observation window. This retired the largest Class
E mechanism (operational omission) from "human memory item" to "machine-closed loop". Its own
history doubles as a cautionary tale (§7): an audit that *applies* synchronization is an
observer mutating the observed, and one drift bug caused a thrice-recurring crontab duplicate
until the audit path was forced to dry-run — leading to a standing rule that **audit observes
and never mutates**.

**4. Context hygiene and anti-fabrication layers (Class D defense).** Mechanism-level: shell
diagnostic output must go to stderr (scanner-enforced repo-wide); alerts are tagged at every
producer and stripped from chat context at the proxy *before* truncation; reserved runtime
files are unwritable by LLM tools (proxy-intercepted). Content-level: a single shared module
provides a six-level cumulative ladder of anti-fabrication guards injected into all
LLM-calling jobs (level selection by task risk; upper levels contain literal prohibited
phrases harvested from actual fabrication incidents and tag requirements like
`[strong-evidence]` / `[weak-association]` on every cross-domain claim); a source-credibility
module labels every ingested source on a five-tier provenance scale at prompt-injection time.
Post-deployment measurement showed the targeted fabrication patterns (multi-hop causal chains,
"therefore"-style necessity claims) dropping by 53–92% in the affected synthesis job while
tagged-claim usage went from zero to ~9 per day.

**5. Monitoring that monitors itself, and alarms that outlive their subject.** Watchdog
ERR-trap self-alarms plus heartbeat canary files age-checked independently; alert routing that
never depends on the failing subject (gateway-down notifications travel a second transport);
freshness guarantees on every health field; and an LLM "observer" job that critiques the
previous day's user-facing output — the beginning of mechanizing §5.2's human advantage.

What the framework deliberately does **not** claim: prevention of novel mechanism classes
(§5.6), and net complexity reduction — which motivates §7.

---

## 7. Discussion: Seams, Not Components

Two months in, facing a week with five incidents, we asked the obvious question: *is the
system failing because it has become too complex?* The postmortem-backed answer was more
specific and, we believe, more useful: **no individual failing part was complex.** A symlink.
An `abspath` call. A one-line registry entry. A boolean default. Every part was simple and
locally correct; every incident lived in a *combination* — symlink × non-resolving path API ×
a particular invocation route; sync-enabled audit × bare-name entry × format mismatch ×
non-dry-run path. Components are covered by 4,286 tests; **combinations grow superlinearly and
tests cover only the combinations someone imagined.** Complexity is about parts; incidents are
about seams.

This reframing has bite because it inverts the instinctive response to incidents — *add a
guard* — which itself adds a part, and therefore seams. (Our convergence engine, built to
close the declared-vs-runtime seam, opened an observer-mutates-observed seam that produced
three incidents before being caged.) The stable posture we landed on, codified as a standing
"Sunset Law" with two operational meta-rules, is: before adding any mechanism, attempt to
*retire* an equivalent one; one logical entity must have one physical representation (multiple
representations *will* drift, and the bug lives between them); and defenses themselves are
incident surfaces — prefer **seam reduction** (unify representations, shrink the
dev–production gap, make observers read-only) over defense accretion. In the five weeks since
adoption, the system retired more representation duplicates than it added invariants — and we
note, with appropriate caution about confounds, that incident frequency declined while feature
velocity did not.

**On the AI-assisted operation of an AI system.** This system is developed and operated by one
human domain expert working with an AI engineering collaborator, governing a runtime powered by
*other* LLMs. Several findings above are entangled with that arrangement in both directions.
The postmortem protocol's rigidity (mandatory causal-chain diagram before any fix; the
three-question rule before touching code) exists substantially *because* an AI collaborator
will otherwise pattern-match symptoms to plausible fixes with great speed — industrializing
the trigger-level cosmetic fix (§5.3); one documented incident chain consisted of five
cascading "fixes" to a problem whose correct resolution was a single file copy. Conversely,
the framework's volume (90 invariants, 15 scanners, 4,286 tests, 22 publication-grade
postmortems in eight weeks) is achievable for a single human *only* with such a collaborator.
We offer the observation, not a verdict: AI collaboration shifts the binding constraint of
reliability engineering from implementation bandwidth to **judgment discipline** — exactly the
property the meta-rules exist to encode. The governance framework is, in this reading, not
just the system's immune system but the collaboration's contract.

**Generalizability.** We claim the *taxonomy classes and cross-cutting structures* (latency ∝
observational distance; trigger/amplifier/concealer; fix→rule→scanner maturation;
audit-as-regression) generalize to LLM agent systems with long-running scheduled autonomy and
human-facing output — the mechanisms are not artifacts of our stack, and Classes A/C/E have
direct ancestry in systems known to fail this way at every scale. We explicitly do not claim
the *frequencies* generalize (single system, single operator pair, one OS), nor that Class D
frequencies transfer to systems without synthesis-and-push pipelines. What we most hope
transfers is the method: complete causal-chain postmortems, a mechanism-oriented catalog
treated as the unit of institutional memory, and sabotage-validated conversion of every lesson
into a machine check.

---

## 8. Threats to Validity

**Construct.** "Silent failure" requires a silence span with green indicators; we applied the
definition at postmortem time, and borderline cases (loud-but-cause-free alerts, §4.3) were
included when the *actionable* signal was absent. Reasonable observers could draw the line
differently for 2–3 incidents.

**Internal.** Classification was performed by the system's two operators (human + AI) without
independent annotation; we report no κ and acknowledge confirmation-bias risk in mechanism
assignment, partially mitigated by the classes being load-bearing (each class drives a real
scanner whose findings are objective) rather than purely descriptive. Postmortem quality varies
with log retention; two early incidents were reconstructed retroactively under the protocol.

**External.** One system, one host OS, one operator pair, eight weeks, ~40 jobs: frequencies,
shares, and latencies are descriptive statistics of a case study, not population estimates. The
70% user-view discovery share in particular may reflect this system's unusually attentive
operator; we present it as an existence proof that mature automated stacks can be out-detected
by a user's eye, not as a constant.

**Survivorship/selection.** The corpus contains only failures that *stopped* being silent.
Failures silent at study end are absent by construction; the latency distribution is therefore
right-censored, and the true tail is unknown — a point that argues for, not against, the
paper's thesis.

**AI involvement.** The AI collaborator that co-wrote the postmortems also co-wrote this paper;
the audited numbers (§5.6, test/check counts, dates) are mechanically derivable from the public
repository, which we offer as the primary check on narrative bias.

---

## 9. Conclusion

Eight weeks of complete postmortems from a production LLM agent runtime yield a five-class,
mechanism-oriented taxonomy of silent failures, of which one class — fail-plausible chained
fabrication — is specific to systems that speak. The quantified record is uncomfortable in
useful ways: a defense stack of thousands of tests and hundreds of declarative checks prevented
*none* of the novel incidents ex ante (while blocking 87% from recurring); the best detector
was a human reading the product; and the longest failures lived not in complex code but in the
seams between simple, correct parts. The constructive program that follows — postmortems as
causal chains, lessons as meta-rules, meta-rules as scanners, guards proven by sabotage,
declared state converged by machine, observers kept read-only, and complexity actively retired
— is less a framework than a discipline, and it is the discipline, not the specific artifacts,
that we believe transfers.

The failure your agent system should frighten you with is not the crash. It is the confident
paragraph, on schedule, in perfect grammar, about a crisis that does not exist — pushed to a
user who has every reason to believe it, by a pipeline in which every component worked.

---

## Artifact Availability

All 22 incident postmortems (`ontology/docs/cases/`), the canonical failure-mode catalog, the
governance ontology (90 invariants / 827 checks / 23 meta-rules), the 15 mechanized scanners,
and the full test suite are public in the system repository. The governance engine is
additionally published as a standalone, project-agnostic package on PyPI
(`openclaw-ontology-engine`, v0.1.0): any agent-runtime project can adopt the
invariant/meta-rule/scanner framework with its own YAML configuration. A data inventory mapping
every number in this paper to its repository source accompanies the draft
(`data_inventory.md`).

---

## References

*(titles/IDs and author lists verified against web sources on 2026-06-11; author spellings for
entries 4–7 and 9–10 sourced from search snippets — final pass against arXiv abs pages required
before submission; see data_inventory.md)*

1. P. Huang, C. Guo, L. Zhou, J. R. Lorch, Y. Dang, M. Chintalapati, R. Yao. **Gray Failure:
   The Achilles' Heel of Cloud-Scale Systems.** HotOS 2017. https://dl.acm.org/doi/10.1145/3102980.3103005
2. H. S. Gunawi et al. **Fail-Slow at Scale: Evidence of Hardware Performance Faults in Large
   Production Systems.** FAST 2018; extended in ACM Transactions on Storage 14(3), 2018.
   https://www.usenix.org/conference/fast18/presentation/gunawi
3. M. Cemri, M. Z. Pan, S. Yang, et al. **Why Do Multi-Agent LLM Systems Fail?** (MAST: 14
   failure modes, 3 categories, 1,600+ annotated traces, κ=0.88). arXiv:2503.13657, 2025.
   https://arxiv.org/abs/2503.13657
4. B. Ranganathan, M. Zhang, K. Wu. **Enhancing Reliability in AI Inference Services: An
   Empirical Study on Real Production Incidents.** (156 high-severity incidents, four-way
   operational taxonomy.) arXiv:2511.07424, 2025.
5. J. Zhou, J. Chen, Q. Lu, D. Zhao, L. Zhu. **SHIELDA: Structured Handling of Exceptions in
   LLM-Driven Agentic Workflows.** arXiv:2508.07935, 2025.
6. Z. Xiao, J. Sun, J. Chen. **AIR: Improving Agent Safety through Incident Response.**
   arXiv:2602.11749, 2026.
7. M. Taraghi, M. M. Morovati, F. Khomh. **Real Faults in Model Context Protocol (MCP)
   Software: A Comprehensive Taxonomy.** arXiv:2603.05637, 2026.
8. B. Beyer, C. Jones, J. Petoff, N. R. Murphy (eds.). **Site Reliability Engineering: How
   Google Runs Production Systems.** O'Reilly, 2016.
9. C. Ezell, X. Roberts-Gaal, A. Chan. **Incident Analysis for AI Agents.** AAAI/ACM
   Conference on AI, Ethics, and Society (AIES), 2025; arXiv:2508.14231.
10. J. Owotogbe, I. Kumara, W.-J. van den Heuvel, D. A. Tamburri, A. K. Iannillo, R. Natella.
    **A Taxonomy of Runtime Faults in Model Context Protocol Servers.** arXiv:2606.05339, 2026.
11. L. Huang, W. Yu, W. Ma, W. Zhong, Z. Feng, H. Wang, Q. Chen, W. Peng, X. Feng, B. Qin,
    T. Liu. **A Survey on Hallucination in Large Language Models: Principles, Taxonomy,
    Challenges, and Open Questions.** arXiv:2311.05232, 2023 (rev. 2024).
12. A. Basiri, N. Behnam, R. de Rooij, L. Hochstein, L. Kosewski, J. Reynolds, C. Rosenthal.
    **Chaos Engineering.** IEEE Software 33(3):35–41, 2016. https://dl.acm.org/doi/10.1109/MS.2016.60
13. S. Ghosh, M. Shetty, C. Bansal, S. Nath. **How to Fight Production Incidents? An
    Empirical Study on a Large-Scale Cloud Service.** ACM SoCC 2022 (Best Paper; 152 severe
    incidents in Microsoft Teams).
14. L. Zheng, W.-L. Chiang, Y. Sheng, S. Zhuang, Z. Wu, Y. Zhuang, Z. Lin, Z. Li, D. Li,
    E. P. Xing, H. Zhang, J. E. Gonzalez, I. Stoica. **Judging LLM-as-a-Judge with MT-Bench
    and Chatbot Arena.** NeurIPS 2023; arXiv:2306.05685.

*(All related-work passes registered for this draft are complete. Pre-submission: re-verify
snippet-sourced author lists against arXiv abs pages from a network-unrestricted environment.)*
