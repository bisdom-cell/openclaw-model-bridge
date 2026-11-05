# Silent Failures in a Production LLM Agent Runtime: A Labelled Incident Corpus and a Detection Benchmark

> **Journal base draft v3 — 2026-11-05.** Derived from `qa4agents_workshop_paper.md` (V37.9.307, the
> rewrite after the ISSRE reviews) with every point from the IEEE Software decision of 2026-11-04
> applied (see `IEEE_SOFTWARE_DECISION_20261104.md` §1 for the point-by-point map). Target: one
> archival journal that needs neither paid registration nor attendance (EMSE first, JSS "In
> Practice" fallback — author decision pending), and arXiv:2606.14589 **v2**.
>
> **Numbers.** Corpus, audit and benchmark figures are frozen at the study cutoff (2026-06-11) exactly
> as in the published preprint. The denominator figures in §6.5 are **new and provisional**: they come
> from a single-coder pass over the project changelog (`denominator_coding_20261105.md/.csv`) and
> must be re-coded by the author before this draft is submitted anywhere. They are marked
> *(provisional)* in the text.
>
> **Format.** Venue-independent Markdown. Typeset after the venue is chosen. Delete this note before
> submission.

**Wei Wu**, Independent Researcher — wuweinanonuaa@gmail.com

*No institutional affiliation. This work was conducted independently. Generative-AI assistance is
disclosed at the end of the paper; no AI system is an author.*

**Index Terms** — LLM agent systems; silent failures; fault taxonomy; benchmark; runtime monitoring;
case study.

---

## Abstract

Conversational agent runtimes fail in a way that classical monitoring does not catch. When an internal
error reaches a language model's context, the model does not stop. It writes a fluent, plausible, and
wrong message to the user. We call this a *fail-plausible* failure.

We report an eight-week single-case study of one production personal-assistant runtime (about 40
scheduled jobs, 8 LLM providers, daily messages to one human user). We recorded 22 silent-failure
incidents with complete postmortems and labelled each with a mechanism class, a silence span, a
discovery channel, the artifact a human could have seen, and whether a model fabricated content. The
corpus yields a five-class mechanism taxonomy with explicit coding rules; one class, chained
hallucination, has no counterpart in the gray-failure literature.

Over the corpus, a human reading the product discovered 13 of 22 incidents (59%) and automated checks
3 (14%). Because the corpus admits only failures that escaped automation, we also coded every
production-reached failure in the same window from the project's change record: 59 in total, of which
runtime automation was the first signal for 8 (14%) and humans for 51 (86%), with the operator's
post-deployment verification accounting for 12 (20%) *(provisional)*. Silence spans ranged from 36
minutes to 60 days.

We release the labelled corpus and a runnable benchmark for fail-plausible detectors. A deterministic
reference detector flags 6 of 6 regression cases with 0 false positives on 4 clean cases, and misses
4 of 4 held-out cases. That last number is the point: pattern-based detection is a regression engine,
not a prediction engine. All claims are bounded to this system; the benchmark exists so other teams
can measure the same quantities on theirs.

---

## 1. Introduction

The reliability literature has studied failures that hide from their own detectors. Huang et al. named
*gray failure*: a component degrades while the health check reports success [1]. Gunawi et al. documented
*fail-slow* hardware that throttles for hours before anyone suspects it [2]. Both share one property.
The system suffers, and the observer built to notice does not.

LLM agent runtimes inherit this problem. They also add a new one. An agent runtime produces language.
When an upstream error leaks into its context window, the runtime does not go quiet. It speaks.

One incident in our corpus shows the shape. A logging bug wrote HTTP error text into a cache that a
nightly synthesis job treated as input signal. The model read error strings where topic signals were
expected. It produced a confident analysis of a "Hugging Face platform crisis" and pushed that analysis
to the user as a routine digest. No detector fired. Every test passed. The error was not lost. It was
narrated.

We call this failure mode **fail-plausible**: the system converts an internal error into output that is
coherent, contextually appropriate, and false. For an observer, fail-plausible is worse than gray
failure. Gray failure denies the observer a signal. Fail-plausible hands the observer a counterfeit one.

Quality assurance for conversational agentic systems has to detect this class. Detecting it is hard for
a reason that our data makes concrete: the failures are semantic, so tests that check status codes,
exit codes, and schemas do not see them. Building detectors requires labelled examples of real
production failures, and such examples are scarce.

This paper contributes the examples and a way to measure detectors against them. It is a single-case
study [15]: one system, one operator, eight weeks. We say what that permits and forbids in §3.1, §6.5
and §8, and we report every count with its denominator.

**Research questions.**

- **RQ1.** What mechanisms produce silent failures in a production LLM agent runtime, and can they be
  assigned by explicit rules?
- **RQ2.** Which channel discovers these failures — in the silent-failure corpus, and in the full
  population of production-reached failures in the same window — and how long do they stay silent?
- **RQ3.** Can the corpus support a reproducible benchmark for fail-plausible detectors, and what does
  a deterministic reference detector achieve on it?

**Contributions.**

1. **A labelled incident corpus** (§4). Twenty-two production silent-failure incidents, each with a
   full public postmortem and machine-readable labels: primary and secondary mechanism class, silence
   span, discovery channel, observable artifact, and whether the incident involved model fabrication.
2. **A five-class mechanism taxonomy with coding rules** (§3.4, §5), including an operational
   discriminator between the two classes that are easiest to confuse and a precedence rule for chains
   that cross classes.
3. **Discovery and latency measurements with denominators** (§6), including a population-level
   comparison that the corpus alone cannot support (§6.5).
4. **A runnable benchmark** (§7) built from the corpus, with a reference detector, a published
   scorecard, and a contribution protocol for adding cases from other systems.

All artifacts are public at `https://github.com/bisdom-cell/openclaw-model-bridge`.

---

## 2. Related Work

**Silent failure in distributed systems.** Gray failure formalizes differential observability: the
application's experience and the detector's view diverge [1]. Fail-slow at scale collected 114 hardware
fault reports across institutions and documented diagnosis times in the hundreds of hours [2]. Our study
sits in this tradition but at a different layer. The subject is an LLM agent runtime, and every incident
in the corpus is silent by construction. Two of our classes will be familiar to that readership. One
will not.

**Failure studies of LLM agents.** MAST derives 14 failure modes from over 1,600 annotated traces across
7 multi-agent frameworks, with reported inter-annotator agreement of 0.88 [3]. Its unit of analysis is
the benchmark task trace, where a failure appears as task non-completion. Our unit is the production
incident. Our incidents did not fail any task visibly, kept indicators green, and surfaced hours to
months later. A provider-side study of 156 inference incidents derives an operational taxonomy at the
inference-service layer, where failures are loud [4]. Ezell et al. argue for structured incident analysis
for AI agents [9]; our corpus is one instantiation, and it adds two required fields: how long the
incident stayed silent, and who noticed.

**Hallucination.** The natural-language-generation literature has a mature taxonomy of hallucination.
Ji et al. distinguish *intrinsic* hallucination (output contradicts the source) from *extrinsic*
hallucination (output cannot be verified from the source) and survey its causes and metrics across
generation tasks [16]; the LLM-specific survey by Huang et al. extends this to pre-training, alignment
and inference-time causes [11]. Kalai et al. argue that hallucination is partly structural: pre-training
produces errors as any classifier does, and post-training evaluation rewards a confident guess over an
admission of uncertainty, so models are optimized to answer rather than to abstain [17].

Our Class D is, in Ji et al.'s terms, *extrinsic* hallucination whose source material is itself
polluted by the surrounding system. The contribution is not a new text-level category. It is the
system-level chain: *how* error text, stale alerts and unlabelled enrichment reach a model's context,
and why every component on the way looks correct. Kalai et al. explain the model's half of the chain.
A model that is trained to guess rather than abstain will, handed error-code vocabulary, produce the
most probable narrative for it. We therefore do not claim that the model is blameless. In all four
corpus fabrications the model did fabricate. We claim that the *leverage* is on the system side: an
operator controls what enters the context window and whether abstention is an allowed output, and does
not control the training objective. Our defenses — keep diagnostics out of data channels, label
provenance, require an explicit "no signal" output — are the system-side complement of the structural
account in [17].

**Operational practice.** Site reliability engineering codified postmortems and the principle that
operational knowledge belongs in mechanisms rather than in memory [8]. Chaos engineering established
deliberate fault injection to build confidence in a system [12]. We apply the same idea one level up:
we inject known violations to check that a *guard* still fires, because an unvalidated guard and a
vacuous guard look identical from outside. Large-scale incident studies established the corpus and
root-cause template we follow [13]. The variable our setting adds is that the system under study
speaks, which changes both what a failure looks like and what detection requires.

We note a limitation of this section. Work on LLM agent reliability is recent, and several of the
studies closest to ours are preprints that have not completed peer review ([3], [4], [5], [6], [7],
[9], [10], [17]). We mark them as such and anchor our framing on the peer-reviewed literature where
possible.

---

## 3. Study Design

### 3.1 Case, context, and how the system was run

The subject is `openclaw-model-bridge`, a public two-layer middleware connecting self-hosted and
commercial LLMs to an open-source personal-agent framework. It has run continuously on one macOS host
since March 2026. It has three parts. A **control plane** governs tool use, repairs schemas, strips
alerts from conversational context, and runs a declarative audit. A **capability plane** routes requests
across providers with capability-scored fallback chains. A **memory plane** holds a local-embedding
index over roughly 1,100 notes and runs daily synthesis jobs that push results to the user.

At the study cutoff (2026-06-11) the system had about 40 scheduled jobs, 8 providers, 4,286 unit tests
in 121 suites, and a governance ontology of 90 invariants and 827 checks. One human operator and one AI
coding assistant develop and operate it.

**Runtime configuration during the window.** Because the incidents depend on it, we state how the
agent was run. The agent framework (OpenClaw Gateway) was at version 2026.3.13-1 for the whole window
(the upgrade to 2026.4.27 happened on the cutoff day, after the last incident). The conversational
agent and all synthesis jobs were served by a self-hosted, OpenAI-compatible endpoint running
`Qwen3-235B-A22B-Instruct-2507` (W8A8 quantization, 262K context) for text and
`Qwen2.5-VL-72B-Instruct` for images; seven further providers were configured as fallbacks and one
(Doubao Seed 2.0) was added as a plugin on 2026-05-11. Requests pass through a Python tool-governance
proxy and a provider adapter, both supervised by `launchd`. Jobs are scheduled by the system `cron`
(deterministic scripts, `bash -lc`) and by the framework's own scheduler (jobs that need the agent);
the host is a Mac mini with macOS, bash 3.2 and BSD userland. Each LLM job calls the adapter over
HTTP with its own prompt and temperature; the models used have no separate reasoning channel. The
exact job list, schedules, provider declarations and the per-job prompts are in the repository
(`jobs_registry.yaml`, `providers.py`, `jobs/*/run_*.sh`), pinned at the cutoff commit.

**What the automated stack asserted.** The finding in §6 that humans discovered most failures is only
interpretable against what the automation was checking. At the cutoff it consisted of: unit tests
(component behaviour, run before every merge); governance checks (declarative invariants over the
repository's own files, plus a subset that runs against the live host; most are static
"file-contains" or Python assertions about code and configuration); a 19-point preflight run after
deployment (file consistency between repository and runtime, environment variables, service
reachability, secret scanning); an hourly watchdog (log error patterns, log freshness, stale lock
files, status files, service health endpoints); and HTTP health endpoints. **None of these asserted
any semantic property of the content pushed to the user.** The first component that did — an LLM
"observer" that critiques the previous day's pushed output — went live on 2026-05-26, one week before
the window closed. The human therefore did not out-perform a semantic check; there was none. What the
corpus shows is which failures a stack of structural checks lets through, and what a semantic check
would have had to assert.

We state the scale plainly because it bounds the claims. This is one system with one operator, not an
enterprise deployment, and all frequencies in this paper are descriptive counts for it.

### 3.2 Unit of analysis, corpus construction, and negative cases

The unit is the **silent-failure incident**. An event qualifies if it (a) reached production,
(b) had a phase during which it was active while all automated indicators reported healthy, and
(c) was closed with a complete postmortem document.

Twenty-two incidents qualify in the window 2026-04-09 to 2026-06-02. They are every incident in the
window that satisfies all three criteria. **Criterion (c) is a documentation decision made by the
operator**, so the corpus is not the complete population of silent failures; it is the complete
population of *postmortem-documented* ones. §6.5 counts what (c) excludes: 37 further
production-reached failures in the same window received a fix but no standalone postmortem, and
several of them also had silent phases. We report the corpus and the full population separately.

Negative cases, for the record: three documents in the postmortem directory are not incidents (an
architecture essay, a decision record, a reflection) and are excluded; two incidents after the cutoff
are labelled in the released file but excluded from every count in this paper; and the 37
undocumented failures above are excluded from the corpus and included in §6.5.

### 3.3 Data collection

A fixed postmortem protocol was adopted after the second incident and applied retroactively to the
first two. Before any code change, the protocol requires a causal chain, a three-layer root cause, a
timeline where logs permit, an analysis of why the incident happened at that moment and not earlier,
and an entry in the governance ontology.

Each incident is corroborated against at least two independent sources before a root cause is recorded.
Sources include runtime logs, the operating system audit log, version control history, test and audit
results, and operator notes. Each postmortem is a standalone public document.

### 3.4 Labelling and coding rules

After the study window we labelled every incident in a single machine-readable file. Each record
carries: primary mechanism class; secondary classes present in the chain; silence span; discovery
channel; the observable artifact a human could have seen; whether a model fabricated content; and a
pointer to the postmortem document.

Classification used constant comparison. Each candidate root cause was matched against the existing
classes. Two consecutive non-matches forced a new class or a split. The scheme stabilized in mid-May
and did not change over the final nine incidents.

Because incidents are chains, one chain can contain mechanisms from several classes. The rules we
applied are:

1. **The unit classified is the incident**, not its sub-events. An incident receives one primary
   class and zero or more secondary classes.
2. **The primary class names the mechanism that produced the silence** — the step at which the error
   stopped being observable — not the trigger. The trigger is recorded in the postmortem's
   trigger/amplifier/concealer decomposition (§6.3) and, when it belongs to another class, as a
   secondary class.
3. **Class D takes precedence.** If any step in the chain transformed an error into user-facing
   content, the incident is Class D, whatever triggered it, because that step alone changes the
   apparent health of the artifact. D1 (§5.4) is triggered by an environment quirk (A) and amplified
   by error capture (C); it is classified D with secondary A and C.
4. **Classes A and B are separated by a reproduction test** (§5.2).
5. **Fabrication is defined by absence of grounding**, not by inaccuracy: a claim with no source in
   the input context is fabricated even if it is coincidentally true.

Table I-c lists the secondary classes for every incident, so the overlap the rules absorb is visible.

The labels are load-bearing rather than decorative. Each class drives a mechanized scanner in the
repository, so a misclassification produces a wrong scanner result, which is falsifiable.

We report **no inter-annotator agreement statistic**. The two coders were the system's operators, one
human and one AI assistant, and both also contributed to this paper. This is the study's most
significant internal-validity limitation (§8). The released label file is the interface for an
independent re-coding; we invite it.

---

## 4. The Incident Corpus

Table I lists all 22 incidents. This table is the answer to a practical question: what does a silent
failure in an agent runtime actually look like, case by case?

**TABLE I. The 22-incident corpus.** Class per §5 (primary). Silence is the span between the incident
becoming active and a human recognizing it. Discovery is the channel that produced that recognition.
FP marks incidents in which a model produced fabricated content.

| # | Incident | Class | Silence | Discovery | FP |
|---|---|---|---|---|---|
| 1 | dream map budget overflow | A | hours | log-forensics | no |
| 2 | backup owner/UID mismatch | A | weeks | log-forensics | no |
| 3 | backup TCC sandbox denial | A | **60 days** | log-forensics | no |
| 4 | messaging client display folding | A | hours–weeks | user-view | no |
| 5 | syndication zombie accounts | B | days | user-view | partial |
| 6 | zombie-detection closure | B | same-day | user-view | no |
| 7 | positional parser cascade | B | days | user-view | partial |
| 8 | governance summary swallows error | C | months | self-observation | no |
| 9 | KB content/source dedup | C | days | check | partial |
| 10 | evening digest fallback quota chain | C | 2 days | user-view | no |
| 11 | exFAT silent backup failure | C | 5–6 days | log-forensics | no |
| 12 | dream quota blast radius | D | hours | user-view | **yes** |
| 13 | dream self-referential fabrication | D | hours | user-view | **yes** |
| 14 | weekly review silent degradation | D | weeks | user-view | **yes** |
| 15 | alert contaminates chat context | D | 36 min | user-view | **yes** |
| 16 | assistant echo chamber | D | single reply | user-view | partial |
| 17 | reserved file self-silencing | E | 13 h | user-view | no |
| 18 | deep-dive cron unregistered | E | 1+ days | user-view | no |
| 19 | cascading preflight fixes | E | n/a (dev) | check | no |
| 20 | rsync helper `set -e` regression | E | 8 days | check | no |
| 21 | observer path blind spot | E | 5 days | self-observation | no |
| 22 | gateway silent death | E | 9 h | user-view | no |

**TABLE I-b. Incidents per primary class, n = 22.**

| Class | A | B | C | D | E |
|---|---|---|---|---|---|
| Incidents | 4 | 3 | 4 | 5 | **6** |
| Longest silence | 60 days | days | months | weeks | 9 h–8 days |

Class E is the largest; Class D is the only class whose artifact carries the *form* of health.

**TABLE I-c. Secondary classes recorded in the chain (coding rule 3, §3.4).**

| # | Primary | Secondary | Where the secondary mechanism sits |
|---|---|---|---|
| 1 | A | B | cache key assumed stable inputs |
| 2 | A | E | operational mount option never set |
| 5 | B | E | zombie accounts were an unmaintained inventory |
| 9 | C | D-adjacent | a model consumed a `repr()` string as input |
| 11 | C | E | twenty copies of a suppression idiom |
| 12 | D | C | quota exhaustion reported as a bare 502 |
| 13 | D | A, C | surrogate byte (A); diagnostics captured as data (C) |
| 14 | D | C, B | six mutually masking defects |
| 15 | D | B | alert persisted into chat history by design |
| 20 | E | A | `set -e` propagation quirk |
| 21 | E | B | path assumption in the observer |
| others | — | none | single-mechanism chains |

The full records, including causal chains and fixes, are in the repository. The labels are in
`docs/llm_observer_ground_truth.yaml`, which is the file the benchmark in §7 consumes.

---

## 5. Taxonomy (RQ1)

Five mechanisms account for all 22 incidents. We classify by mechanism, not by location, because the
same mechanism recurred in unrelated components, and because a mechanism-level fix protects a class
while a location-level fix protects a file.

### 5.1 Class A — Environment and platform quirk

The logic is correct. The runtime environment behaves differently than the code assumes.

The development environment (Linux, GNU userland, bash 5) is systematically more permissive than the
production target (macOS, bash 3.2, BSD userland, sandboxed cron). Instances include: bash 3.2 not
propagating ERR traps into functions without `set -E`, which disarmed a watchdog's self-alarm; BSD awk
aborting on invalid UTF-8, which under `set -e` killed the monitoring script for seven days; and a
messaging client folding long messages at an undocumented threshold.

Signature: green in development, silent in production.

### 5.2 Class B — Design-assumption mismatch

The code encodes an assumption about the system's own structure or about the shape of its input.
Production violates that assumption.

One instance: a metadata-resolution function shipped three candidate paths for a registry file, none of
which was its actual production location. The component fell back to an unfiltered mode for five days.
Unit tests passed throughout, because the fixtures were laid out according to the same wrong assumption.

A second instance: a parser indexed the model's output by line position. The model occasionally omits a
line, because instruction-following is a distribution and not a contract. Every field shifted by one
slot. Any positional parse of LLM output is a latent Class B failure.

Signature: the tests mirror the assumption rather than the caller.

**Discriminating A from B.** These two classes are easy to conflate, so we use an operational test.
Reproduce the incident on the development platform, supplying the production inputs and topology. If it
reproduces, the defect is in the code's model of its own system: Class B. If it does not reproduce, the
defect requires a specific platform behavior: Class A. Every incident in Table I was assigned by
applying this test, and it resolves all seven A and B cases without ambiguity.

### 5.3 Class C — Error swallowing and dilution

The error occurs and is reported into a void.

*Swallowing.* An audit summary counted only checks with `status == "fail"`. Invariants that raised an
exception produced `status == "error"`, which the summary ignored. The audit printed "all invariants
hold" over dead checks. Separately, an executor read check code from one YAML field while 67 checks
stored theirs in a differently named field. All 67 executed an empty string and passed vacuously for
months. A new sabotage test found them, by refusing to fail.

*Dilution.* An evening digest failed for two days with "HTTP 502". The real cause was a fallback
provider's exhausted quota after the primary provider's circuit breaker opened. That cause sat in the
upstream response body. The adapter wrapped it into a 502. The proxy never read the body. The client
reduced what remained to a status code and a reason phrase. Three hops, each locally reasonable, each
removing cause.

There is a consequence specific to agent runtimes. The consumer of an error message is often another
model prompt. A diluted error is therefore one step away from becoming Class D input.

### 5.4 Class D — Chained hallucination (fail-plausible)

The error is not suppressed. It is transformed. This class has no counterpart in the gray-failure
literature, and it is the class that motivates the benchmark in §7.

*D1: the fabricated platform crisis.* A nightly job collects signals from about 290 notes using
map-reduce LLM calls. The chain ran as follows.

1. Scraped content contained an isolated UTF-16 surrogate. `json.dump` raised mid-write, so the request
   body was truncated.
2. The adapter could not parse the truncated body and returned HTTP 400 with a short HTML error page.
3. The map step's logging function wrote its diagnostics to standard output. The caller captured
   standard output by command substitution and treated it as the signal payload.
4. The cache therefore filled with strings such as `Error code: 400 Bad JSON`.
5. The reduce step's model read that cache as cross-domain signals. It produced a fluent analysis of a
   "Hugging Face platform crisis", which is a probable narrative shell for error-code vocabulary.
6. The analysis was pushed to the user as a routine digest.

Every component behaved as designed. The user found it, by noticing that the stated signal and the
stated action item did not match. The fix that severed the chain was one redirection: the logging
function now writes to standard error.

*D2: the fabricated remediation.* A watchdog alert was persisted into chat session history as an
ordinary assistant message. Thirty-six minutes later the user asked an unrelated architecture question.
The model attended across the contaminated context, claimed it had received a system alert follow-up
task, and instructed the user to grant Full Disk Access to a cron binary. The instruction was
ungrounded. Alert traffic and conversation are different speech acts, and sharing one context window
invites the model to merge them.

*D3: the fabricated success.* A weekly review job whose LLM call failed fell back to a mechanical line
filter. That filter emitted leftover container headings as review content, and the job recorded that an
LLM had produced the output. The artifact looked plausible for weeks.

*D4: the fabricated release.* An evening digest received the day's high-alignment papers as enrichment
context, without provenance labels. It inferred that the project must have shipped a release, and
announced a version number that exists only in an internal changelog. True but unlabelled context
produced a false attribution.

The common structure is a **pollution chain**. A Class A, B, or C failure deposits non-signal content
where a downstream model expects signal. The model completes fluently. The output has the form of health
and the content of failure.

**Scope of the class.** We observed fail-plausible only in the LLM-mediated pipelines, and we have
four fabrication incidents in the corpus plus three outside it (§6.5), all from one runtime. We
therefore do not claim it is unique to LLM systems in general. The precise claim is narrower: the
mechanism requires a component that produces well-formed output regardless of whether its input is
valid. D3 shows that a shell fallback can produce the *shape* of such output. A language model is the
component for which this behaviour is the default rather than a bug, and it is the only one in our
corpus that also supplied the *content*. Whether other generative components in other systems show
the same mechanism is exactly the question the benchmark in §7 is meant to let other teams answer.

### 5.5 Class E — Operational omission and forensic blind spot

The code is correct. An operational step never happened, or the diagnostic instrument is itself
compromised. This is the largest class and holds the longest silences.

*Omission.* A new daily job was implemented, tested, registered, and deployed. It never ran, because
the final step — writing the crontab line — was a human memory item. Three small defects hid it: the
preflight check grepped for only one of two warning strings; the crontab helper ignored its install
exit code and compared counts with `<` rather than equality, reporting success on a rejected install;
and a job that never runs produces no log.

*Self-silencing.* The AI assistant, finishing an alert task, wrote completion notes into a file named
`HEARTBEAT.md`. To the assistant this was a scratch filename. To the runtime it was a reserved control
file whose non-empty content activates a heartbeat protocol. Under that protocol the model replies with
a bare acknowledgment token, which the gateway strips from outbound messages. For 13 hours every user
message received an acknowledgment token that was stripped to nothing. The user received silence. Every
component worked as designed.

*Forensic blind spot.* The longest silence in the corpus, 60 days, was an external-SSD backup failing
with EPERM. Six successive hypotheses were each falsified by data over several weeks. The breakthrough
came from the operating system audit log, which revealed macOS TCC sandbox denials: cron-derived
processes lacked Full Disk Access.

The methodological finding matters more than the cause. During those weeks the forensic collectors
themselves were being denied and were returning empty output, which the pipeline recorded as "normal".
An instrument that cannot distinguish "nothing is there" from "I was not allowed to look" manufactures
false reassurance. The collectors now capture standard error separately and tag denied results.

---

## 6. Findings (RQ2)

### 6.1 Discovery channel in the corpus

Table II gives the discovery channel for all 22 incidents.

**TABLE II. Discovery channel in the corpus, n = 22.**

| Channel | Count | Share |
|---|---|---|
| Human user-view observation | 13 | 59% |
| Log forensics (after suspicion) | 4 | 18% |
| Automated check (preflight or audit) | 3 | 14% |
| Self-observation (governance auditing itself) | 2 | 9% |

The largest single channel is a human reading the product as a user would. That reading was
institutionalized during the study as a weekly 30-minute review with no coding, covering alert noise,
push latency, information density, and response quality. Typical findings sound like "this digest looks
shallow", "why did I get two windows", and "yesterday's analysis never arrived".

**This table cannot compare humans with automation.** The corpus admits only incidents that survived a
silent phase with green indicators, so anything automation caught at once is excluded by construction,
and the three "automated check" rows are checks that fired only after the silent phase. The table says
what a stack of structural checks (§3.1) let through and who then noticed. The comparison the design
does support is in §6.5.

### 6.2 Silence latency

Silence spans in the corpus range from 36 minutes to 60 days. We did not measure code complexity and
make no claim about it. What we can say is where the long-lived incidents sat: every incident that
stayed silent for a week or more lived where no automated check ran — deployment topology, operating
system policy, the monitoring of monitoring, and gaps between declared and actual state — while
defects inside code that tests exercise were found by the next scheduled run.

We suggest reading latency as a measure of observational distance: the further a mechanism sits from
any existing observer, the longer it lives. For silent failures in this corpus, time to detect
exceeded time to repair by one to two orders of magnitude, which makes detection latency a more useful
reliability metric than repair time for this class.

### 6.3 Structure of the incidents

Nearly every postmortem decomposed into three layers: a **trigger** (the external spark, such as a
surrogate byte or a transient permission error), an **amplifier** (the flaw that spreads it, such as
diagnostics on standard output being captured by command substitution), and a **concealer** (the absence
that hides it, such as a status file reporting success or a fail-open guard).

The decomposition has a practical consequence. A fix that addresses only the trigger is cosmetic.
Triggers are unbounded. Amplifiers and concealers are finite and belong to the architecture. In this
corpus the highest-leverage single fixes were at the amplifier level (one output redirection; one shared
helper replacing twenty copies of an idiom) and the concealer level (failing loudly with the cause;
tagging denied forensic output).

### 6.4 What the audit framework prevented, and what it was later judged to block

Mid-study we audited the governance framework against the first 15 incidents, asking for each whether
the framework as it existed *before* the incident could have caught it, and whether the guards added
*after* would block the mechanism from recurring.

**TABLE III. Self-audit of the governance framework, n = 15.**

| Question | Result | Nature of the evidence |
|---|---|---|
| Prevented the incident in advance | 0 / 15 | factual: the framework's state before each incident is in version control |
| Gave partial early warning | 2 / 15 | factual, same source |
| Judged to block the mechanism from recurring | 13 / 15 | **ex-post design judgment** made at audit time about guards built in response |
| Miss rooted in a dimension never considered | 12 / 15 | coder judgment |

The first two rows are checkable against the repository. The third row is not evidence that recurrence
was prevented; it is the auditor's judgment, at the time the guard was written, that the guard would
block at least half of the same failure class. Prospective evidence would be a recurrence count over a
follow-up window for each guard; through the cutoff we recorded no recurrence for the mechanisms that
reached a scanner (§6.6), but the follow-up windows are short and unequal.

Within those limits, the pattern is informative. The framework prevented none of the novel incidents.
Twelve of the fifteen misses were in categories no invariant had contemplated, which no amount of
diligence within the existing set would have covered. That is the behaviour of a regression suite,
and it is the expected behaviour of any check set that encodes the past. If this generalizes, the
implication for QA is that prevention of novel failure classes must come from somewhere other than
the regression apparatus.

### 6.5 Discovery channel over all production-reached failures *(provisional)*

Reviewer feedback on an earlier version made a point we accept: a corpus selected for having escaped
automation cannot show that humans out-detect automation. To obtain the comparison, we coded every
entry in the project's append-only change record for the same window. The record has 135 version rows
between 2026-04-09 and 2026-06-02; each names what triggered it, because the postmortem protocol
requires it. Each row was coded by its primary trigger into: one of the 22 corpus incidents; a
production-reached failure without a standalone postmortem; a follow-up on an incident already
counted; a latent defect caught by a scanner before it manifested; or non-failure work (features,
documentation, development tooling). The coding file and the rules are released with the corpus.
**This pass was done by one coder and is provisional**; the figures below must be read with that
caveat.

**TABLE V. First discovery signal over all production-reached failures in the window
(provisional, single coder).**

| Channel | Corpus (n=22) | Non-corpus failures (n=37) | Combined (n=59) |
|---|---|---|---|
| Human: user-view observation | 13 | 15 | 28 (47%) |
| Human: operator's post-deployment verification | 0 | 12 | 12 (20%) |
| Human: log forensics | 4 | 4 | 8 (14%) |
| Human: self-observation / code audit | 2 | 1 | 3 (5%) |
| Automated: watchdog, preflight, governance, monitors | 3 | 4 | 7 (12%) |
| Automated: LLM observer (live from 2026-05-26) | 0 | 1 | 1 (2%) |
| **Human, any channel** | 19 (86%) | 32 (86%) | **51 (86%)** |
| **Automated, any channel** | 3 (14%) | 5 (14%) | **8 (14%)** |

Three things change when the denominator is widened, and one does not.

First, a second human channel appears that the corpus could not contain: the operator's mandatory
verification run on the production host after each deployment. It accounts for one fifth of all
failures and for most of the development–production mismatches, which it caught before any user saw
them. It is a process control, not product use, and a team without it would see those failures
surface as user-view incidents instead.

Second, the corpus is a documentation-selected subset. Thirty-seven production-reached failures in
the window received a fix but no standalone postmortem, and several had silent phases comparable to
corpus incidents (a watchdog that silently aborted for seven days; a daily job that silently aborted
for five; placeholder digests pushed with a success status). The user-view share over the full
population is 47%, lower than the corpus's 59%, because the operator's verification channel absorbs
part of it.

Third, the corpus understates fabrication. Three of the 37 non-corpus failures are Class D events: a
cross-domain "necessity chain" fabrication that the LLM observer caught on its third day live, and two
fabricated project announcements. The four `fail_plausible: yes` incidents in Table I are a lower
bound.

What does not change is the ratio. Including everything automation caught first, runtime automation
was the first signal for 8 of 59 failures and humans for 51. We draw a bounded conclusion from this:
in this system, over this window, failures that reached deployment were overwhelmingly first noticed
by a person, through product use, post-deploy verification, or reading logs, and the structural
automation described in §3.1 was the first signal for about one in seven.

Two limits of the comparison. The change record cannot see defects that the unit-test suite blocked
before merge, so the preventive contribution of the 4,286 tests is invisible here; the comparison is
between *runtime* automation and humans, for failures that reached deployment. And the rows were coded
by one coder; the released file exposes every coding with a one-line justification, and we estimate
that a second coder disagreeing on five rows would move the automated share by about eight points.

### 6.6 Recurrence

An earlier version of this work said that a lesson which reached a mechanized scanner made recurrence
"structurally impossible". That is not something eight weeks can show. What the record shows is
narrower: for the meta-rules that reached a scanner before the cutoff, no recurrence of the scanned
pattern was recorded through the cutoff, while every recurrence we did record involved a lesson that
had stopped at a point fix (one import omission fixed at one site recurred at eight sites two days
later, via an automated injector). A scanner changes the recurrence from a cultural question to a
build failure; whether that holds over longer windows and under code churn is an empirical question
we leave open and intend to report on.

---

## 7. From Corpus to Benchmark (RQ3)

Section 6 identifies the open problem: the most effective detector in this study was a human, and
mechanizing part of that role requires a way to measure detectors. We release the corpus as a benchmark
for that purpose.

### 7.1 Benchmark construction

The benchmark consumes the label file described in §3.4. Each case supplies the artifact text a
detector would see, the expected verdict, and the signal that should fire. Cases are partitioned into
three sets.

- **Category A — regression (6 cases).** Known fail-plausible patterns drawn from incidents in Table I.
  A detector is expected to flag these.
- **Clean controls (4 cases).** Healthy outputs from the same jobs. A detector is expected not to flag
  these, because noise is itself a failure of a monitoring tool.
- **Category B — held out (4 cases).** Real incidents for which no detection rule was written. These
  measure whether a detector generalizes beyond the patterns it was built for.

Held-out cases are never a pass/fail gate. They are reported.

### 7.2 Reference detector and results

The reference detector is a deterministic pre-filter of five rules over output text.

1. **Pollution signature.** Error codes or status phrases appear in content where topic signal belongs.
2. **Credibility mismatch.** A low-tier source is wrapped in high-confidence language.
3. **Fabrication phrase.** A literal phrase recorded from a real fabrication incident appears.
4. **Provenance gap.** An equivalence claim is asserted without a provenance label.
5. **Structural incoherence.** Headings appear without bodies, or fields collapse into separators.

**TABLE IV. Reference detector on the benchmark.**

| Metric | Result | Interpretation |
|---|---|---|
| Detection rate, Category A | 6 / 6 | Known patterns are caught reliably |
| False positives, clean controls | 0 / 4 | No noise on healthy output |
| Misses, Category B (held out) | 4 / 4 | No generalization to unseen patterns |

Every rule is validated by sabotage: the suite disables one rule at a time and requires the corresponding
case to fail. A rule that cannot be made to fail is vacuous, and we found vacuous checks in this system
before (§5.3), so we treat this as mandatory.

The third row is the useful result. A deterministic detector built from known incidents catches known
incidents and generalizes to none of the held-out ones. This is what a regression engine does, and it
mirrors the framework-level measurement in §6.4. Detecting novel fail-plausible output requires
something other than pattern matching, such as semantic grounding checks or human review.

We state a limitation about the detector's own nature. A model that judges another model's output is
itself a component of the system, and it inherits every class in §5. Our own monitoring component shipped
with a Class B path defect and a sampling artifact that made it report a truncation that had not occurred.
An LLM judge needs the same governance and sabotage validation as the system it judges.

### 7.3 Using and extending the benchmark

The benchmark runs offline with no model access and no external dependencies, which makes it usable in
continuous integration. The exit code is a gate: it is zero only if Category A detection is complete,
false positives are zero, and every rule is load-bearing.

We include a contribution protocol. A team adding a case from their own system supplies the artifact
text, the mechanism class, the expected signal, and a pointer to their postmortem. New fail-plausible
patterns that no rule targets are added as Category B, where they are reported rather than silently
absorbed into the regression set.

The reason to release this is directly connected to our main limitation. A corpus from one system cannot
establish how often these mechanisms occur elsewhere. A benchmark that other teams can run and extend can.

---

## 8. Threats to Validity

We organize threats using the four case-study categories [15], and name the four limits a reader
should hold in mind throughout: **one system, one human operator, one host, one LLM family, eight
weeks; no independent coding of the labels; a corpus selected by documentation (§3.2); and
survivorship — only failures that were eventually discovered and written up can appear in any count
in this paper.**

**Construct validity.** *Silent failure* requires a silence span with concurrently green indicators,
and we applied that definition at postmortem time, which risks hindsight framing. Borderline cases
that produced a loud but causeless alert were included only when the actionable signal was absent; two
or three incidents could reasonably be classified differently. *Fail-plausible* is defined by absence
of grounding rather than by inaccuracy. Each construct is operationalized against repository artifacts:
a silence span is a timestamp gap against green audit records, and a fabricated claim is one with no
source in the input context. Classification is therefore checkable by a third party.

**Internal validity.** The principal threat is confirmation bias in mechanism assignment. Both coders
were the system's operators, and both contributed to this paper, so inter-annotator agreement cannot be
measured and no κ is reported. This is the study's most significant limitation. Three factors partially
mitigate it. Classes are load-bearing, so an incorrect class produces a wrong scanner result rather than
only a wrong label. Causal claims were triangulated across at least two sources before recording.
Sabotage validation independently confirms many amplifiers and concealers by reintroducing the mechanism.
Two early incidents were reconstructed retroactively and are flagged as such in the corpus. The
denominator coding in §6.5 is subject to the same threat in a stronger form (one coder, after the fact)
and is labelled provisional for that reason.

**Selection and survivorship.** The corpus contains only failures that (a) escaped automation, (b) were
eventually discovered, and (c) were documented. (a) is the design and is why Table II cannot compare
humans with automation; §6.5 is the comparison the data support. (b) means failures still silent at the
cutoff are absent from every count; nothing in this paper bounds their number. (c) is an operator
decision; §6.5 shows it excluded 37 production-reached failures in the window.

We note one discrepancy for the record. An earlier report of this study stated that approximately 70% of
incidents were discovered by user-view observation. The labelled corpus released with this paper gives
13 of 22, or 59%, and the full-population count gives 47%. The corpus labelling is the later and more
careful pass, and it is the number we use.

**External validity.** One system, one host operating system, one operator pair, one model family,
eight weeks, about 40 jobs. We claim analytical generalization of the mechanism classes and of the
trigger–amplifier–concealer structure, and we disclaim generalization of the frequencies. The settings
that most bound our results are systems without a synthesis-and-push pipeline, where Class D frequencies
will not transfer; teams without a mandatory post-deployment verification step, where the §6.5 operator
channel will not exist; and teams without an unusually attentive single operator, where the user-view
share will be lower.

**Reliability.** Every postmortem is a standalone public document. All counts, dates, and audit results
are mechanically derivable from the public repository at the cited commit window, and the runtime
configuration is stated in §3.1. The AI assistant that helped write the postmortems also helped write
this paper, which raises a narrative-bias risk. We treat that risk as first class: the human is sole
author and final arbiter, all numbers are repository-derivable, and the findings are unflattering to the
narrator. The framework prevented nothing in advance, the best detector was a person rather than the
automation, and the component built to close one gap opened another.

---

## 9. Conclusion

Eight weeks of complete postmortems from one production LLM agent runtime yield a five-class taxonomy of
silent failures with explicit coding rules. One class, chained hallucination, is specific to pipelines
that contain a component which produces well-formed output regardless of input validity; in this
system that component was a language model, and the corpus's four fabrications are a lower bound. In
the corpus, a human reading the product found 13 of 22 incidents; over every failure that reached
deployment in the window, people were the first signal for 51 of 59 and runtime automation for 8
(provisional). The governance framework prevented none of the novel incidents in advance.

We release the labelled corpus, the denominator coding, and a runnable benchmark so that these numbers
can be tested rather than believed. The reference detector's result on held-out cases is the honest
summary of where fail-plausible detection stands: reliable on patterns it was built for, and blind to
the rest.

---

## Statement of Differences from Prior Versions

This paper is derived from arXiv:2606.14589 (v1), from a submission to the ISSRE 2026 Industry Track
(not accepted, 2026-08-14), and from a submission to IEEE Software (not accepted, 2026-11-04). The
differences are substantive rather than editorial.

1. **Scope.** The prior versions presented a taxonomy and a defense framework. This version presents a
   taxonomy with coding rules, a labelled corpus, a population-level discovery comparison, and a
   benchmark. Sections 3.4, 4, 6.5 and 7 are new relative to the preprint.
2. **Evidence.** The 22 incidents are enumerated individually with class, silence span, discovery
   channel, and fabrication flag (Table I); per-class counts (Table I-b) and secondary classes
   (Table I-c) are given; the machine-readable labels are released.
3. **Denominator.** Discovery is reported over the full population of production-reached failures in
   the window (§6.5), not only over the silent-failure corpus. The comparative claim that "a human
   out-detected the automated stack" is replaced by a bounded count with its selection effect stated.
4. **Statistics.** Every share carries its count. The user-view share is 13/22 (59%) in the corpus and
   28/59 (47%) in the full population, correcting an earlier approximate figure of ~70%. The self-audit
   result is labelled as an ex-post design judgment, not prospective prevention evidence.
5. **Claims withdrawn or narrowed.** "Latency tracked mechanism rather than code complexity" (code
   complexity was never measured); "structurally impossible" recurrence (now: no recurrence recorded
   through the cutoff); "specific to LLM systems" (now: specific to generative components, observed in
   one runtime).
6. **Reproducibility.** The runtime configuration (framework version, models, host, schedulers) and the
   automation's actual assertions are stated (§3.1).
7. **Related work.** The hallucination literature is engaged directly (Ji et al. 2023; Kalai et al.
   2025), and the "systems, not model" framing is replaced by a shared-causation account with the
   leverage on the system side.
8. **Presentation.** The text was rewritten for a plainer register: shorter sentences, no aphoristic
   formulations, and no forward references.

---

## Generative-AI Use Disclosure

This research used Anthropic's Claude, accessed through a coding-agent interface, as an engineering and
writing collaborator. It assisted in triaging and reconstructing incident postmortems, extracting and
tabulating data from the public repository, performing the first pass of the denominator coding in
§6.5, drafting and editing prose, and proposing structure. It was not used to generate empirical
results, invent data, or make research claims. Every quantitative figure is mechanically derivable from
the public repository and was verified by the author. The author defined the research questions,
performed the classification, drew the conclusions, and takes full responsibility for the content,
including any errors. No generative-AI system is an author.

The runtime under study is itself an LLM agent system, powered by separate Qwen3-class models. The AI
collaborator described here is the author's development tool and not the system studied.

---

## Artifact Availability

The 22 incident postmortems, the labelled corpus (`docs/llm_observer_ground_truth.yaml`), the
denominator coding (`docs/paper/silent_failures_taxonomy/submissions/denominator_coding_20261105.csv`
and its rules), the benchmark and its reference detector, the governance ontology, and the full test
suite are public at `https://github.com/bisdom-cell/openclaw-model-bridge`. The governance engine is
separately available on PyPI as `openclaw-ontology-engine`. A data inventory maps every number in this
paper to its source in the repository. A preprint of the earlier version is available as
arXiv:2606.14589, disclosed per publisher preprint policy.

---

## References

1. P. Huang, C. Guo, L. Zhou, J. R. Lorch, Y. Dang, M. Chintalapati, R. Yao. "Gray Failure: The Achilles' Heel of Cloud-Scale Systems." *HotOS*, 2017.
2. H. S. Gunawi et al. "Fail-Slow at Scale: Evidence of Hardware Performance Faults in Large Production Systems." *USENIX FAST*, 2018; extended in *ACM Trans. Storage* 14(3), 2018.
3. M. Cemri, M. Z. Pan, S. Yang, et al. "Why Do Multi-Agent LLM Systems Fail?" arXiv:2503.13657, 2025. (preprint)
4. B. Ranganathan, M. Zhang, K. Wu. "Enhancing Reliability in AI Inference Services: An Empirical Study on Real Production Incidents." arXiv:2511.07424, 2025. (preprint)
5. J. Zhou, J. Chen, Q. Lu, D. Zhao, L. Zhu. "SHIELDA: Structured Handling of Exceptions in LLM-Driven Agentic Workflows." arXiv:2508.07935, 2025. (preprint)
6. Z. Xiao, J. Sun, J. Chen. "AIR: Improving Agent Safety through Incident Response." arXiv:2602.11749, 2026. (preprint)
7. M. Taraghi, M. M. Morovati, F. Khomh. "Real Faults in Model Context Protocol (MCP) Software: A Comprehensive Taxonomy." arXiv:2603.05637, 2026. (preprint)
8. B. Beyer, C. Jones, J. Petoff, N. R. Murphy (eds.). *Site Reliability Engineering.* O'Reilly, 2016.
9. C. Ezell, X. Roberts-Gaal, A. Chan. "Incident Analysis for AI Agents." *AIES*, 2025; arXiv:2508.14231.
10. J. Owotogbe, I. Kumara, W.-J. van den Heuvel, D. A. Tamburri, A. K. Iannillo, R. Natella. "A Taxonomy of Runtime Faults in Model Context Protocol Servers." arXiv:2606.05339, 2026. (preprint)
11. L. Huang, W. Yu, W. Ma, et al. "A Survey on Hallucination in Large Language Models." arXiv:2311.05232, 2023 (rev. 2024).
12. A. Basiri, N. Behnam, R. de Rooij, et al. "Chaos Engineering." *IEEE Software* 33(3):35–41, 2016.
13. S. Ghosh, M. Shetty, C. Bansal, S. Nath. "How to Fight Production Incidents? An Empirical Study on a Large-Scale Cloud Service." *ACM SoCC*, 2022.
14. L. Zheng, W.-L. Chiang, Y. Sheng, et al. "Judging LLM-as-a-Judge with MT-Bench and Chatbot Arena." *NeurIPS*, 2023.
15. P. Runeson, M. Höst. "Guidelines for Conducting and Reporting Case Study Research in Software Engineering." *Empirical Software Engineering* 14(2):131–164, 2009.
16. Z. Ji, N. Lee, R. Frieske, T. Yu, D. Su, Y. Xu, E. Ishii, Y. J. Bang, A. Madotto, P. Fung. "Survey of Hallucination in Natural Language Generation." *ACM Computing Surveys* 55(12), Article 248, 2023. DOI 10.1145/3571730.
17. A. T. Kalai, O. Nachum, S. S. Vempala, E. Zhang. "Why Language Models Hallucinate." arXiv:2509.04664, 2025. (preprint)
