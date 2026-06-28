# When Errors Become Narratives: A Longitudinal Taxonomy of Silent Failures in a Production LLM Agent Runtime

> **ISSRE 2026 Industry Track — camera-ready-target version (6 pages incl. references).**
> Derived from `issre_industry_track_paper.md` (the full-fidelity manuscript) by applying its "Trim
> priorities" appendix to the **confirmed** ISSRE limit of **6 pages including references**, IEEE
> Computer Society 2-column format (CFP cross-checked 2026-06-28 via EAPLS / EasyChair; re-confirm on
> the official page). **All numbers are frozen at the study cutoff 2026-06-11** to match
> arXiv:2606.14589 — do NOT update to current repository counts. **Before submission:** paste into the
> IEEEtran conference template, redraw Fig. 1 as a vector figure, delete this note, and verify the
> typeset PDF is ≤6 pages incl. references (over-limit = desk reject). This compression targets 6
> pages; its true page count is only knowable after typesetting. **If the typeset PDF runs over, apply
> the next cuts in order:** compress §2 further (each theme to two sentences); reduce D2–D4 to one
> sentence of mechanism + one of consequence each; render the §6 pillars as a bare list. **Never cut**
> §3 (method), the §5.5 0%/87% scorecard, §8 (threats), or the GenAI disclosure. Author: Wei Wu,
> Independent Researcher.

**Wei Wu**, Independent Researcher — wuweinanonuaa@gmail.com
*No institutional affiliation; this work was conducted independently. AI-assistance is disclosed in
the Generative-AI Use Disclosure; no AI system is an author.*

**Index Terms** — software reliability; LLM agent systems; silent failures; fault taxonomy.

## Abstract

LLM agent systems increasingly run as long-running autonomous runtimes: scheduling jobs, calling
tools, maintaining memory, messaging humans. We present an eight-week longitudinal study of silent
failures in one production personal-assistant runtime (~40 scheduled jobs, 8 LLM providers, a
tool-governance proxy, a knowledge-base memory plane; 4,286 unit tests, 827 governance checks). We
documented 22 incidents with full postmortems; one meta-pattern — an error signal that never reaches
a human in actionable form — recurred at least 28 times. We derive a five-class mechanism taxonomy:
(A) environment quirks, (B) design-assumption mismatches, (C) error swallowing, (D) chained
hallucination, (E) operational omission. Class D is LLM-specific and most dangerous: the model
transforms errors into fluent, false narrative — we name this *fail-plausible*. Three findings
challenge assumptions: ~70% of failures surfaced through human user-view observation, not tests;
audits showed 0% prevention but 87% regression-blocking; latency (13 hours to 60 days) tracks
mechanism, not code complexity.

## 1. Introduction

The reliability literature has long known that the most damaging failures are not crashes. *Gray
failures* [1] degrade cloud systems while their failure detectors report health; *fail-slow* hardware
[2] throttles for hours before anyone suspects the disk. The defining property of both is
**differential observability**: the application suffers, but the observer designed to notice does not.

LLM agent systems inherit this entire problem class — and add a new one. An agent runtime is a
generator of fluent language. When an upstream error leaks into its context window, its failure mode
is not silence; it is *plausible speech*. In one incident (§4.4), an HTTP 400 error page was captured
into a cache by a logging bug, and the downstream LLM — seeing error strings where signals should be —
confidently fabricated an industry analysis about a "Hugging Face platform crisis" and pushed it to
the user as a routine digest. No detector fired; every test was green. The error had not disappeared;
it had been *narrated*. We call this **fail-plausible**: a failure mode in which the system transforms
an internal error into coherent, contextually appropriate, and false output. Fail-plausible is to LLM
agents what gray failure was to cloud infrastructure — the dominant, hardest-to-see class — but
strictly worse for the observer: gray failure starves the detector of signal; fail-plausible feeds the
human a counterfeit one.

This paper reports what eight weeks of documented production incidents in a continuously operating LLM
agent runtime taught us, in five contributions: (1) a **mechanism-oriented taxonomy** (§4) of five
silent-failure classes from 22 fully documented incidents, classified by *failure mechanism* rather
than location, because the same mechanism recurs across unrelated components and a mechanism-level
defense immunizes a class rather than a file; (2) the **fail-plausible class** (§4.4), with four
incidents in which LLMs converted polluted context into confident fabrications — including fabricated
software releases, platform crises, and host-OS remediation instructions; (3) **quantified findings**
(§5): latency 13 hours–60 days, ~70% human user-view discovery (unit tests ≈0% for this class), a
three-layer trigger/amplifier/concealer structure, and one meta-pattern manifesting ≥28 times —
silent failure is a *bug class*, not a bug; (4) an **audited defense framework** (§6) with an honest
scorecard — 0% ex-ante prevention, 87% ex-post regression blocking — *audit is a regression engine,
not a prediction engine*; and (5) a **complexity argument** (§7): the longest failures lived not in
complex components but in *seams*, motivating an explicit "Sunset Law" that ranks retiring complexity
over adding protection. The study is a **longitudinal single-case study** (§3) — the methodological
complement to horizontal studies that sample failures across many frameworks from benchmark traces [3]
or across customers from a provider's incident database [4]; what those cannot see is the longitudinal
texture of production silent failure — multi-week latency, discovery channels, defense evolution,
recurrence of "fixed" lessons — which only exists in a system that runs continuously and keeps
complete postmortems.

## 2. Background and Related Work

**Silent failures in distributed systems.** Huang et al. introduced *gray failure* as the mode behind
most cloud incidents — components degrade while detectors report health, formalized as differential
observability [1]. Gunawi et al.'s *fail-slow at scale* collected 101 (later 114) hardware-fault
reports across 12–14 institutions, documenting cascading root causes and multi-hundred-hour diagnosis
[2]. Our study is downstream of this tradition (taxonomy from production incident reports) but at a
different layer (an LLM agent runtime) and lens (every incident is *silent* by selection). Two of our
classes (C, E) will look familiar to that readership; Class D will not.

**LLM agent failure studies.** Cemri et al.'s MAST is the first empirically grounded multi-agent LLM
failure taxonomy: 14 modes in 3 categories from 1,600+ annotated traces across 7 frameworks (κ=0.88)
[3]; its unit is the *benchmark task trace*, where failures show as task non-completion. Our unit is
the *production incident*: failures that by definition did **not** visibly fail any task, kept all
detectors green, and surfaced hours-to-months later. A provider-side study of 156 high-severity
inference incidents derives an operational taxonomy [4] but covers the *inference service* layer,
where failures are loud. Ezell et al. argue for structured AI-agent incident analysis [9]; our corpus
is an existence proof, and adds a requirement: record *how long the incident was silent and who
noticed*. Related systems work on agentic exception handling [5], incident response [6], and MCP fault
taxonomies [7], [10] does not target the silent/fail-plausible class.

**Hallucination research.** Usually studied as a *model* property [11]. Our Class D reframes it as a
*systems* property: in 4/4 fabrication incidents the model behaved as trained (fluent completion over
context); the failure was that the *system delivered polluted context*. The defense is therefore
system-side — context hygiene, provenance labeling, layered guards — not model-side.

**Operational practice.** SRE codified postmortem culture and engineering (not remembering)
operational knowledge [8]; our convergence engine (§6) applies that to declared state. Chaos
engineering established deliberate fault injection [12]; we apply the same epistemology one level up —
*sabotage validation* injects violations to gain confidence in the **guards**, because in a
silent-failure regime an unvalidated detector is indistinguishable from a vacuous one. Large-scale
incident studies [13] established the corpus/root-cause/automation-gap template we instantiate; the
variable our setting adds is that the system *speaks*, changing both what failure looks like (§4.4)
and what detection requires (§5.2).

## 3. Research Method and System Context

We frame this as a **longitudinal single-case study** in the sense of Runeson and Höst [15], so "n=1"
is a deliberate design with an explicit generalization argument.

**Method rationale.** Case study fits for three reasons grounded in the phenomenon: silent failures
are *not reproducible on demand* (they manifest only in a continuously operating system whose
indicators stay green, precluding controlled experiments and benchmark-trace sampling); the variables
of interest — silence latency, discovery channel, defense evolution — only exist over real operational
time; and the unit is an *operating system in context*. The case is *revelatory* (public access to
production fail-plausible fabrication with complete causal-chain postmortems) and *longitudinal*
(observed across eight weeks in which failures and defenses co-evolve).

**The case.** The subject (public repository `openclaw-model-bridge`) is two-layer middleware
connecting self-hosted and commercial LLMs to OpenClaw, an open-source personal-agent framework, in
continuous production on one macOS host since early March 2026. It follows a three-plane design: a
**control plane** (tool-governance proxy with a hard tool cap, schema repair, alert stripping; a
declarative engine of 90 invariants / 827 checks / 23 meta-rules / 14 mechanized scanners with a daily
audit; SLO monitoring; circuit breakers; a convergence engine synchronizing declared to runtime
state); a **capability plane** (an adapter routing across 8 providers — Qwen3-235B primary, a
multimodal route, commercial fallbacks — with capability-scored fallback chains); and a **memory
plane** (a ~1,100-note local-embedding RAG index, a media index, conversation harvesting, and daily
LLM synthesis jobs that push insights to the user). Scale at the cutoff (2026-06-11): ~40 scheduled
jobs across two cron schedulers; 8 providers; 3 supervised services; 4,286 unit tests in 121 suites;
22 published postmortems. One human operator and one AI engineering collaborator (Claude, via a
coding-agent interface) develop and operate the system, whose runtime is powered by *separate*
Qwen3-class models — making this also a data point on AI-assisted operation of an AI system (§7).

**Unit of analysis and corpus.** The unit is the *silent-failure incident* — an event that (a)
reached production, (b) had a silent phase with all automated indicators green, and (c) closed with a
complete postmortem; the embedded sub-unit is the *mechanism*. The single-case-with-embedded-units
design (case = the runtime; units = 22 incidents → ≥28 mechanism manifestations) licenses within-case
cross-incident comparison with context fixed. The corpus is the **complete population** of qualifying
incidents in the window (2026-04-09 to 2026-06-02), not a sample.

**Data collection.** A **fixed postmortem protocol** (the "exception-analysis constitution"), adopted
after the second incident and applied retroactively to the first two, mandates before any fix: a full
causal-chain diagram; a three-layer root cause (*trigger / amplifier / concealer*); a per-minute
timeline where logs allow; a condition-combination ("why now") analysis; and an entry feeding the
governance ontology. This triangulates across runtime logs, the OS audit log, version control,
test/governance results, and the operator's notes — each incident corroborated by ≥2 independent
sources before root cause is recorded. Each postmortem is a standalone public document.

**Analysis.** Classification used **constant comparison**: each "true root cause" matched against
existing classes; two consecutive non-matches forced a split or new class. We classify by mechanism
because a location taxonomy had no defensive value (the same mechanism recurred across unrelated
components) whereas a mechanism-level defense immunizes a class. The scheme stabilized in mid-May and
was stable over the final 9 incidents — a saturation signal. The classes are *load-bearing*: each
drives a scanner whose findings are objective, so a misclassification has a falsifiable consequence.

**Generalization basis.** We claim **analytical generalization** (to theory), not statistical (to a
population). *What generalizes:* the five mechanism classes; the trigger/amplifier/concealer structure;
the latency ∝ observational-distance relationship; the *point fix → meta-rule → scanner* maturation;
*audit-as-regression-engine*. These are claims about mechanisms — Classes A, C, E have ancestry in
distributed-systems/hardware literature, and Class D follows from a property (fluent completion over
context) common to LLM runtimes with synthesis-and-push pipelines. *What does not generalize:* the
frequencies, shares, and latencies are descriptive statistics of this case; the ~70% user-view
discovery share is an **existence proof**, not a constant.

## 4. A Taxonomy of Silent Failures

Table 1 summarizes the five classes; we define each, give its mechanism signature, and narrate the
load-bearing incidents in full.

**Table 1 — Five classes over 22 incidents (+ sub-events).**

| Class | Mechanism | Inc. | Silence | Defining property |
|---|---|---|---|---|
| A — Environment/platform quirk | Logic correct; runtime environment's implicit behavior defeats it | 1 (+6) | hrs–wks | dev green; only target OS/client reveals |
| B — Design-assumption mismatch | Code assumes a topology/contract/input shape reality violates | 4 | days | tests cover the assumption, not reality |
| C — Error swallowing & dilution | Error occurs, is eaten or stripped of cause across layers | 5 | hrs–days | the alert carries no usable information |
| D — Chained hallucination | LLM converts polluted context into confident false output | 4 | hrs–days | **fail-plausible**: user receives counterfeit health |
| E — Operational omission & forensic blind spot | A deploy/registration step is skipped, or the forensic tool itself is blocked and reads "normal" | 8 | days–**60d** | declared ≠ runtime state; instruments lie |

**4.1 Class A — Environment and platform quirks.** *The logic is right; the environment's implicit
behavior is not what anyone assumed.* The dev environment (Linux, root, GNU userland, bash 5) is
systematically more permissive than the production target (macOS, bash 3.2, BSD userland, sandboxed
cron). Sub-events: bash 3.2 not propagating ERR traps into functions without `set -E`, disarming a
watchdog self-alarm; BSD awk aborting on invalid UTF-8 which — with `set -e`/`pipefail` — killed the
*monitoring* script for 7 days; missing GNU `timeout` turning a defensive wrapper into a universal
"tool unavailable"; CJK full-width punctuation adjacent to unbraced shell variables parsed into the
variable name; and a messaging client folding long messages at an undocumented ~4,000-char threshold.
The signature is **green dev, silent prod**; the defense is mechanized — a cross-OS quirk scanner
encodes six known patterns as repo-wide checks, and a meta-rule requires framework fixes to be
validated *on the target environment*, after a dev-only fix shipped broken twice in one day.

**4.2 Class B — Design-assumption mismatches.** *Code internally consistent with an assumption;
production violates it.* A metadata-resolution function shipped three path candidates for a registry
file, all missing its actual production location; the component fell back silently to an unfiltered
mode for **five days**, its fallback log line scrolling past unread, while a companion feature
(writing health scores to shared state) silently never ran — unit tests green throughout because they
tested the resolution logic with fixtures laid out per the same wrong assumption. A second instance: an
LLM-output parser indexed lines positionally; the model occasionally omits one line (instruction-
following is a distribution, not a contract), shifting every field one slot so "title" became a
separator string. **Any positional parse of LLM output is a latent Class B failure** — frozen into a
meta-rule (parsers must be key-based) and a scanner. Signature: *tests mirror the assumption, not the
caller.*

**4.3 Class C — Error swallowing and dilution.** *The error happens and is reported — into a void.*
**Swallowing:** a summary counted only `status=="fail"`; invariants raising an *exception*
(`status=="error"`) vanished and the audit printed "all invariants hold" over dead checks. Later, a
governance executor read check code from one YAML field while 67 checks (across 21 invariants) stored
theirs in a differently named field: all 67 ran `exec("")` — vacuous passes — for months, found only
when a *new* sabotage refused to fail. **Dilution:** an evening digest failed with "HTTP 502" for two
days; the true cause (a fallback provider's free quota exhausted after the primary's breaker opened)
sat in the upstream body, which the adapter wrapped into a 502, whose body the proxy never read, whose
remnant the client reduced to `f"HTTP {code}: {reason}"` — three hops, each reasonable, each stripping
cause. The meta-rule (*error chains must preserve upstream cause*) has a twist: the "client" of an
error is often another LLM prompt, so a diluted error is one step from Class D input. **Amplified
swallowing:** a batch tool injected an env-var read into 8 job scripts without the `import` — a
`NameError` in all 8, caught by a fail-open guard that skipped the new validation in all 8 while every
test passed; automation is a bug amplifier, and `bash -n` cannot see inside a Python heredoc.

**4.4 Class D — Chained hallucination and fabrication (fail-plausible).** *The most dangerous class,
and the one without precedent in the gray-failure literature.* The error is not suppressed; it is
**transformed**.

*D1 — The fabricated platform crisis.* A nightly synthesis job collects signals from ~290 notes via
map-reduce LLM calls. A Unicode surrogate in scraped content made `json.dump` raise mid-write,
truncating the request body into a 400 from the adapter — and the chain turns: the map step's logging
wrote diagnostics to **stdout**, the caller captured stdout by command substitution as the *signal
payload*, and the cache filled with `HTTP Error 400: Bad JSON` strings. The reduce-step LLM, prompted
for cross-domain signals, did what models do with anomalous-but-thematic context: it composed a
confident analysis of a "Hugging Face platform crisis" — platform trouble being the most probable
narrative shell for error-code vocabulary — pushed to the user as a routine digest. Every component
succeeded; the pipeline laundered an encoding bug into industry analysis (Fig. 1). The structural fix
was four layers deep (stderr discipline, surrogate sanitization, encoding policy, anti-pollution
prompt guards), but the load-bearing one was a single `>&2`: **one redirection operator severed the
entire hallucination chain.**

```
Fig. 1 — D1 pollution chain: one malformed byte to a fabricated industry
         analysis. Every component behaves as designed.

  scraped content with isolated UTF-16 surrogate (U+D800–DFFF)
        │ json.dump(ensure_ascii=False) → UnicodeEncodeError → body TRUNCATED
        ▼
  adapter: json.loads fails → HTTP 400 "Bad JSON" (456-byte HTML page)
        │                         ┌─ AMPLIFIER: signals=$(llm_call …) command
  llm_call(): extraction empty;   │  substitution captures stdout → the error
  log() prints dump to STDOUT ────┤  dump becomes the "signal" payload
        ▼ (4 deterministic retries)└─
  cache := "…Error code: 400 Bad JSON… Waiting 3s before retry…"
        │                         ┌─ CONCEALER: non-empty check passes; status
  reduce-step LLM reads cache ────┤  file reports ok; no detector inspects
  as cross-domain "signals"       │  content semantics
        ▼                         └─
  fluent synthesis: "Hugging Face platform crisis" → pushed to user
        ▼
  ✗ no alarm — found by the user noticing the "signal" and "action item" mismatch
  Fix that severed the chain: log() { echo … >&2; }   (one redirection)
```

*D2 — The fabricated remediation.* A watchdog alert was persisted into chat session history as an
ordinary assistant message; 36 minutes later the user asked an unrelated architecture question, and
the model, attending across the contaminated context, claimed it had "received the system alert
follow-up task" and instructed the user to grant Full Disk Access to a cron binary — off-topic and
*technically fabricated*. Alert traffic and conversation are different speech acts; sharing a context
window invites the model to weave them into one narrative. (Fabrication is defined by absence of
grounding, not by inaccuracy — a later 60-day investigation found the *general* direction coincidentally
relevant elsewhere, which does not refute it.)

*D3 — The fabricated success.* A weekly-review job whose LLM call failed fell back to a mechanical
line-filter that emitted leftover container headings as review content and wrote `"llm": true`
unconditionally; the artifact looked plausible for weeks. **A fallback that manufactures
plausible-shaped output is a hallucination implemented in shell.**

*D4 — The fabricated release.* An evening digest, given the day's high-alignment papers as enrichment,
inferred the project "must have shipped" and announced a community release of an internal version
number that exists only in the changelog. *True but unlabeled* context produced false attribution:
provenance-free enrichment is fabrication fuel.

The common structure is a **pollution chain**: a Class A/B/C failure deposits non-signal content where
a downstream LLM expects signal; the LLM completes fluently, and the output inherits the *form* of
health with the *content* of failure. Defense is system-side context hygiene at every link: stderr
discipline; alert stripping before context assembly; provenance/credibility labeling; and a six-level
cumulative ladder of anti-fabrication prompt guards (up to literal prohibited phrases from real
incidents), shared as one imported module across all nine LLM-calling jobs. Prompt guards are the
*last* layer, behind hygiene and provenance (§5.5).

**4.5 Class E — Operational omission and forensic blind spots.** *The code is right; an operational
step never happened — or the diagnostic instrument itself is compromised.* The largest class (8) with
the longest silences.

*Omission.* A new daily job was implemented, tested, registered, deployed — and never ran, because the
final step (writing the crontab line) was a human memory item. Three small bugs hid it: the preflight
grepped only one of two drift-warning strings; the crontab helper ignored its install exit code and
compared counts with `<` instead of equality (reporting ✅ on a rejected install); and the job's
absence produced no log. This drove the system's largest correction: **declared state must converge to
runtime state via machine, not memory** — a convergence engine diffing registry declarations against
observed crontab/launchd/provider state on every audit, with staged escalation and auto-sync after a
one-week zero-drift window per spec.

*The reserved-file incident.* The agent, completing an alert task, wrote "task complete" notes into a
file named `HEARTBEAT.md` — to the agent a scratch filename, to the runtime a *reserved control file*
whose non-empty content activates a heartbeat protocol instructing the model to reply with a bare
acknowledgment token, which the gateway then strips from outbound messages. For 13 hours, every user
message received `HEARTBEAT_OK`, stripped to nothing in transit — **total silence, every component
working exactly as designed.** The model had been handed a pen that doubled as the system's mute
button. Meta-rule: any path with special runtime semantics must be unwritable by the LLM's generic
file tools.

*Forensic blind spots.* The longest silence — **60 days** — was an external-SSD backup failing with
EPERM; six successive hypotheses were each falsified by data over weeks, and the breakthrough came
only from the OS audit log, revealing macOS TCC sandbox denials (cron-derived processes lack Full Disk
Access). The deeper finding is methodological: during those weeks the *forensic collectors themselves*
(lsof, ACL listing, snapshot enumeration) were being silently denied and returning empty output, which
the pipeline recorded as "normal/empty." **An instrument that cannot distinguish "nothing there" from
"I was not allowed to look" manufactures false reassurance**; collectors now capture stderr separately
and tag `[sandbox_denied]`. Also here: a 9-hour gateway outage whose three independent alarms each
failed (a quiet-hours filter suppressing both channels including the emergency one; a keepalive that
logged WARN without alerting; a restart script reporting success without verifying health) — yielding
the meta-rule that **an alert path must not depend on the failing subject**; and the watchdog that died
of a Class A quirk and stayed dead 7 days because nothing watched the watcher.

## 5. Cross-Cutting Findings

**5.1 Latency.** Silence spans: 60 days (SSD backup TCC + compromised forensics); 7 days (watchdog
self-death); 5–6 days (observer-path fallback; backup anti-pattern); 9–13 hours (gateway death;
reserved-file mute); hours–2 days (digest 502; cron omission). Latency tracks the **mechanism layer,
not code complexity.** Code-level bugs die young (tests and the next run catch them); what survives for
weeks lives where no test runs — deployment topology, OS policy, monitoring-of-monitoring, declared-vs-
runtime gaps. Latency is a *measure of observational distance*. We suggest silence-latency percentiles
as a reportable agent-reliability metric, complementary to MTTR — for silent failures, time-to-*detect*
dominates time-to-repair by one to two orders of magnitude.

**5.2 Discovery: who finally notices.** Channels: **human user-view** (reading actual pushed output; a
weekly observation ritual) **~70%** ("this digest looks shallow"; "why two windows?"; "didn't receive
yesterday's analysis"); target-environment execution (high; all Class A, several B); self-observation
(governance auditing governance; rising; caught its own executor bug); **unit tests/preflight ≈0** for
this corpus. The last row is partly tautological (the corpus selects for what tests missed) but the
magnitude of the first is not: the system runs 4,286 tests, 827 checks, and a 19-point preflight, all
green through most incidents — yet the single most productive detector was **a human looking at the
product as a user**, institutionalized as a weekly 30-minute ritual (no coding; four dimensions: alert
noise, push latency, information density, response quality). For practitioners: *user-view observation
is a first-class observability signal and deserves calendar time*; for researchers, the open problem is
mechanizing part of what the human eye does. The LLM-as-judge literature gives grounds for optimism
(strong judges reach >80% agreement with human preference [14]), and our own daily LLM "observer" found
real regressions (including a fabrication and two bugs in itself) — but an LLM judging a system's output
*is* an LLM component of that system, inheriting every class here (ours shipped with a Class B path bug
and a sampling artifact that made it hallucinate a truncation), so the judge needs the same governance,
hygiene, and sabotage validation as what it judges.

**5.3 Trigger–amplifier–concealer (and why silent failure is a bug class).** Nearly every postmortem
decomposed into a **trigger** (external spark — a surrogate byte; an omitted output line; a transient
EPERM), an **amplifier** (the flaw that spreads it — stdout logging into command substitution;
positional parsing; 18 copy-pasted suppression idioms), and a **concealer** (the absence that hides it
— a status file lying "ok"; a fail-open guard; a quiet-hours filter; a forensic tool silently denied).
The decomposition is prescriptive: **a fix addressing only the trigger is cosmetic.** Triggers are
unbounded; amplifiers and concealers are finite and owned by the architecture — the highest-leverage
single fixes were amplifier-level (one `>&2`; one shared helper replacing 20 idiom copies) and
concealer-level (fail-loud with cause; forensic stderr tagging). The MR-4 manifestation counter
advanced at a roughly constant rate across the study but **never twice in the same form** — early ones
classic swallowing; middle dilution and fabrication; late ones a *fix introducing a deeper silence* (a
retry helper whose exit-code propagation killed twenty `set -e` callers), a *correct fix to the wrong
bug*, and *vacuous verification* (the 67 empty-string checks). Silent failure cannot be enumerated and
fixed; it can only be **governed** — meta-rules over mechanism families, scanners enforcing them, and
validation that the guards themselves are alive (§6).

**5.4 Defense maturation: point fix → meta-rule → scanner.** Effectiveness correlates with how far a
lesson traveled. (1) *Point fix* is empirically insufficient — an import-omission fixed at one site
recurred at eight sites via an automated injector **two days later**, because the lesson existed only
as a diff. (2) *Meta-rule* — the lesson as a named cross-case rule (23 by study end) — is necessary but
memory-bound. (3) *Mechanized scanner* — the rule as a repo-wide check in CI and the daily audit (14 by
study end). Only at step 3 does recurrence become structurally impossible rather than culturally
discouraged: every meta-rule that reached step 3 has zero recorded recurrences as of the cutoff; every
recurrence in the corpus involves a lesson that stopped at step 1.

**5.5 Audit is a regression engine, not a prediction engine.** Mid-study we audited our own framework
against the first 15 incidents with three questions each: Q1 — could the audit, as it existed *before*
the incident, have caught it? Q2 — why not? Q3 — do the guards added *after* block the class going
forward?

| Metric | Value |
|---|---|
| Ex-ante prevention (Q1 fully) | **0 / 15 = 0%** |
| Partial early warning | 2 / 15 = 13% |
| Ex-post regression blocking (Q3 ≥ half) | **13 / 15 = 87%** |
| Misses rooted in a never-conceived dimension | 12 / 15 = 80% |

The 0% is not an indictment — it is the *job description*. 80% of misses were "blank categories":
dimensions no invariant had contemplated, which no diligence within the existing set would have
covered. Audits, like regression suites, encode the past. The honest posture is to (a) maximize the
regression rate (87%, hardened by sabotage validation), (b) accept that prevention of *novel* classes
comes from elsewhere — user-view observation, adversarial review, target-environment exposure — and (c)
measure the *conversion latency* from novel incident to mechanized guard, which §5.4 minimizes. A
complementary adversarial audit (16 destruction scenarios against the live repository: 10 replaying
known incidents, 6 probing suspected blind spots) scored 16/16 *after* the blind-spot batch drove its
own guard additions — useful precisely because its first run did not score 16/16.

## 6. The Defense Framework That Emerged

We present the framework as an existence proof of one coherent answer, with its scorecard (§5.5)
attached. Five pillars. **(1) Declarative governance with mandatory depth:** 90 invariants / 827 checks
in a YAML ontology, run daily and in CI; critical invariants are mechanically required to verify at ≥2
layers (single-layer greps demonstrably missed behavioral regressions — a rule the system enforces on
itself). **(2) Sabotage validation ("test the test"):** every new guard is proven *alive* by injecting
the violation it targets and observing it fire; this caught guards matching their own assertion strings
and the 67 vacuous checks. **(3) Declared-state convergence:** the engine diffs every declared registry
(jobs, providers, services, KB sources, config) against observed runtime state per audit — retiring the
largest Class E mechanism from "human memory" to "machine-closed loop"; its own history is a cautionary
tale (§7) — an audit that *applies* sync is an observer mutating the observed, and one drift bug caused
a thrice-recurring crontab duplicate until the audit path was forced to dry-run, yielding the rule that
**audit observes and never mutates.** **(4) Context hygiene and anti-fabrication (Class D defense):**
mechanism-level — shell diagnostics to stderr (scanner-enforced), alerts tagged and stripped from chat
context before truncation, reserved files unwritable by LLM tools; content-level — one shared module of
six-level anti-fabrication guards and a five-tier source-credibility labeler injected at prompt time;
post-deployment, the targeted fabrication patterns dropped **53–92%** in the affected job while
tagged-claim usage rose from zero to ~9/day. **(5) Monitoring that monitors itself:** watchdog ERR-trap
self-alarms plus age-checked heartbeat canaries; alert routing that never depends on the failing
subject; freshness guarantees on health fields; and an LLM "observer" critiquing the prior day's output
— the start of mechanizing §5.2's human advantage. The framework deliberately does **not** claim
prevention of novel classes, nor net complexity reduction — which motivates §7.

## 7. Discussion: Seams, Not Components

Facing a week with five incidents, we asked: *is the system failing because it is too complex?* The
postmortem answer was specific: **no individual failing part was complex.** A symlink. An `abspath`
call. A one-line registry entry. A boolean default. Every part was simple and locally correct; every
incident lived in a *combination* — symlink × non-resolving path API × a particular invocation route;
sync-enabled audit × bare-name entry × format mismatch × non-dry-run path. Components are covered by
4,286 tests; **combinations grow superlinearly and tests cover only the combinations someone imagined.**
This inverts the instinctive response to incidents — *add a guard* — which itself adds a part, and
therefore seams (our convergence engine, built to close one seam, opened an observer-mutates-observed
seam that produced three incidents before being caged). The posture we codified as a standing **"Sunset
Law"**: before adding any mechanism, attempt to *retire* an equivalent one; one logical entity must have
one physical representation (multiple representations *will* drift, and the bug lives between them); and
defenses themselves are incident surfaces — prefer **seam reduction** over defense accretion. In the
five weeks since adoption the system retired more representation duplicates than it added invariants,
and — with caution about confounds — incident frequency declined while feature velocity did not.

**On AI-assisted operation of an AI system.** This system is developed and operated by one human domain
expert with an AI engineering collaborator, governing a runtime powered by *other* LLMs. The postmortem
protocol's rigidity (mandatory causal-chain diagram and three-question rule before touching code) exists
substantially *because* an AI collaborator will otherwise pattern-match symptoms to plausible fixes at
speed, industrializing the trigger-level cosmetic fix — one documented chain was five cascading "fixes"
to a problem whose correct resolution was a single file copy. Conversely, the framework's volume (90
invariants, 14 scanners, 4,286 tests, 22 publication-grade postmortems in eight weeks) is achievable for
a single human *only* with such a collaborator. The observation, not a verdict: AI collaboration shifts
the binding constraint of reliability engineering from implementation bandwidth to **judgment
discipline** — exactly what the meta-rules encode.

## 8. Threats to Validity

We organize threats under the four case-study validity categories [15]. **Construct validity:** *silent
failure* requires a silence span with concurrently green indicators, applied at postmortem time (risking
hindsight framing); borderline loud-but-cause-free alerts were included only when the *actionable* signal
was absent (a judgment 2–3 incidents could be drawn differently); *fail-plausible* is defined by absence
of grounding, not inaccuracy. *Mitigation:* every construct is operationalized against repository
artifacts (a silence span is a timestamp gap against green CI/audit records; a fabrication is a claim
with no source in the input context), so classification is checkable. **Internal validity:** the
principal threat is **confirmation bias in mechanism assignment**, compounded by the coders being the
system's two operators (one human, one AI) with no independent annotators — we therefore report **no
inter-annotator agreement (no κ)**, the study's most significant internal-validity limitation. Three
factors partially mitigate: classes are load-bearing (an incorrect class yields a falsifiable scanner
result, not just a label); causal claims are triangulated across ≥2 sources before recording; and
sabotage validation independently confirms many amplifiers/concealers by re-introducing the mechanism.
Two early incidents were reconstructed retroactively and flagged. **External validity:** one system, one
host OS, one operator pair, eight weeks, ~40 jobs; we claim analytical generalization of the *mechanism
classes and structures* and disclaim generalization of *frequencies*. The settings that most bound it
are systems *without* a synthesis-and-push pipeline (Class D frequencies do not transfer, though the
mechanism stays latent wherever an LLM consumes machine-generated context) and teams *without* an
unusually attentive single operator (lowering the user-view share). *Mitigation:* the mechanism
orientation, the public postmortem corpus, and the project-agnostic governance engine
(`openclaw-ontology-engine` on PyPI) let other teams test whether the classes recur in *their* systems.
**Reliability and AI-involvement:** every postmortem is a standalone public document; all counts, dates,
and governance results are **mechanically derivable from the public repository** at the cited commit
window, with a number-by-number data inventory. The AI collaborator that co-wrote the postmortems also
co-wrote the paper, raising **narrative-bias** risk; we treat it as first-class — the human is sole
author and final arbiter, all numbers are repo-derivable (the primary external check), and the findings
are deliberately *unflattering to the narrator* (0% prevention; the best detector a human, not the
automation; the convergence engine causing three of its own incidents).

## 9. Conclusion

Eight weeks of complete postmortems from a production LLM agent runtime yield a five-class,
mechanism-oriented taxonomy of silent failures, of which one — fail-plausible chained fabrication — is
specific to systems that speak. The record is uncomfortable in useful ways: thousands of tests and
hundreds of declarative checks prevented *none* of the novel incidents ex ante (while blocking 87% from
recurring); the best detector was a human reading the product; and the longest failures lived not in
complex code but in the seams between simple, correct parts. The constructive program — postmortems as
causal chains, lessons as meta-rules, meta-rules as scanners, guards proven by sabotage, declared state
converged by machine, observers kept read-only, complexity actively retired — is less a framework than a
discipline, and it is the discipline that we believe transfers. The failure your agent system should
frighten you with is not the crash. It is the confident paragraph, on schedule, in perfect grammar,
about a crisis that does not exist — pushed to a user who has every reason to believe it, by a pipeline
in which every component worked.

## Generative-AI Use Disclosure

This research used Anthropic's Claude, accessed through a coding-agent interface, as an engineering and
writing collaborator: to assist in triaging and reconstructing incident postmortems, extract and
tabulate quantitative data from the public repository, draft and edit prose, and propose structure.
Claude was *not* used to generate empirical results, invent data, or make research claims: every
quantitative figure (incident counts, test/governance counts, dates, percentages) is mechanically
derivable from the public repository at the cited commit window and was verified by the author. The
author defined all research questions, performed all classification, drew all conclusions, and takes
full responsibility for the content, including any errors. No generative-AI system is an author. For
clarity, the *runtime under study* is itself an LLM agent system (powered by separate Qwen3-class
models); the AI collaborator described here (Claude) is the author's development tool, not the system
studied — the relationship is examined as a finding in §7.

## Acknowledgments

I thank the maintainers of the open-source agent framework on which the studied system is built, and the
open scientific-literature infrastructure (arXiv, Semantic Scholar, DBLP) the runtime depends on. I
disclose the use of Anthropic's Claude as an engineering and writing collaborator throughout the
construction of the studied system and the preparation of this paper; the collaboration is itself part
of the subject matter (§7). All conclusions, and all responsibility for them, are mine.

**Author Contributions (CRediT).** **Wei Wu** — Conceptualization, Methodology, Investigation,
Formal analysis, Data curation, Writing (original draft; review & editing), Software. Sole author and
guarantor. **AI-assistance (non-author):** Anthropic's Claude provided tool-assisted support for
postmortem drafting, repository data extraction, prose drafting/editing, and document structuring under
the author's direction and verification; as a generative-AI system it does not meet authorship criteria.

## Artifact Availability

All 22 incident postmortems, the canonical failure-mode catalog, the governance ontology (90 invariants
/ 827 checks / 23 meta-rules), the 14 mechanized scanners, and the full test suite are public in the
system repository. The governance engine is additionally published as a standalone, project-agnostic
package on PyPI (`openclaw-ontology-engine`, v0.1.0). This paper is also available as preprint
arXiv:2606.14589 (disclosed per IEEE preprint policy); a data inventory maps every number to its
repository source.

## References

1. P. Huang, C. Guo, L. Zhou, J. R. Lorch, Y. Dang, M. Chintalapati, R. Yao. "Gray Failure: The Achilles' Heel of Cloud-Scale Systems." *HotOS* 2017.
2. H. S. Gunawi et al. "Fail-Slow at Scale: Evidence of Hardware Performance Faults in Large Production Systems." *USENIX FAST* 2018; ext. *ACM Trans. Storage* 14(3), 2018.
3. M. Cemri, M. Z. Pan, S. Yang, et al. "Why Do Multi-Agent LLM Systems Fail?" (MAST). arXiv:2503.13657, 2025.
4. B. Ranganathan, M. Zhang, K. Wu. "Enhancing Reliability in AI Inference Services: An Empirical Study on Real Production Incidents." arXiv:2511.07424, 2025.
5. J. Zhou, J. Chen, Q. Lu, D. Zhao, L. Zhu. "SHIELDA: Structured Handling of Exceptions in LLM-Driven Agentic Workflows." arXiv:2508.07935, 2025.
6. Z. Xiao, J. Sun, J. Chen. "AIR: Improving Agent Safety through Incident Response." arXiv:2602.11749, 2026.
7. M. Taraghi, M. M. Morovati, F. Khomh. "Real Faults in Model Context Protocol (MCP) Software: A Comprehensive Taxonomy." arXiv:2603.05637, 2026.
8. B. Beyer, C. Jones, J. Petoff, N. R. Murphy (eds.). *Site Reliability Engineering.* O'Reilly, 2016.
9. C. Ezell, X. Roberts-Gaal, A. Chan. "Incident Analysis for AI Agents." *AIES* 2025; arXiv:2508.14231.
10. J. Owotogbe, I. Kumara, W.-J. van den Heuvel, D. A. Tamburri, A. K. Iannillo, R. Natella. "A Taxonomy of Runtime Faults in Model Context Protocol Servers." arXiv:2606.05339, 2026.
11. L. Huang, W. Yu, W. Ma, et al. "A Survey on Hallucination in Large Language Models." arXiv:2311.05232, 2023 (rev. 2024).
12. A. Basiri, N. Behnam, R. de Rooij, et al. "Chaos Engineering." *IEEE Software* 33(3):35–41, 2016.
13. S. Ghosh, M. Shetty, C. Bansal, S. Nath. "How to Fight Production Incidents? An Empirical Study on a Large-Scale Cloud Service." *ACM SoCC* 2022.
14. L. Zheng, W.-L. Chiang, Y. Sheng, et al. "Judging LLM-as-a-Judge with MT-Bench and Chatbot Arena." *NeurIPS* 2023; arXiv:2306.05685.
15. P. Runeson, M. Höst. "Guidelines for Conducting and Reporting Case Study Research in Software Engineering." *Empirical Software Engineering* 14(2):131–164, 2009.
