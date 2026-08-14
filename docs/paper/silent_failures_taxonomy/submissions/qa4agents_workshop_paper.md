# Silent Failures in a Production LLM Agent Runtime: A Labelled Incident Corpus and a Detection Benchmark

> **Target venue:** QA4AGENTS 2026 — International Workshop on Quality Assurance of Conversational
> Agentic Systems, co-located with ISSRE 2026 (Limassol, Cyprus). Submission deadline 2026-08-19.
> Accepted workshop papers appear in the ISSRE Supplemental Proceedings and are submitted to IEEE Xplore.
> **Format:** IEEE Computer Society two-column. **Confirm the page limit on the workshop site before
> typesetting** (the co-located ReSAISE workshop used 8 pages full / 4 pages short in 2025).
> **Provenance:** this is a substantially rewritten and re-scoped version of arXiv:2606.14589. It is not
> the ISSRE Industry Track submission, which was rejected; §"Statement of Differences" below records
> what changed and why. **Numbers are frozen at the study cutoff 2026-06-11** except the corpus
> labelling and benchmark figures, which come from the released artifacts and are dated where used.
> Delete this note before submission.

**Wei Wu**, Independent Researcher — wuweinanonuaa@gmail.com

*No institutional affiliation. This work was conducted independently. Generative-AI assistance is
disclosed at the end of the paper; no AI system is an author.*

**Index Terms** — LLM agent systems; silent failures; fault taxonomy; benchmark; runtime monitoring.

---

## Abstract

Conversational agent runtimes fail in a way that classical monitoring does not catch. When an internal
error reaches a language model's context, the model does not stop. It writes a fluent, plausible, and
wrong message to the user. We call this a *fail-plausible* failure.

We report an eight-week study of one production personal-assistant runtime. The runtime schedules about
40 jobs, routes across 8 LLM providers, and messages a human user daily. We recorded 22 silent-failure
incidents with complete postmortems. We label each incident with a failure mechanism, a silence span,
a discovery channel, and the artifact a human could have seen.

Three measurements over this corpus: a human reading the product discovered 13 of 22 incidents (59%);
automated checks discovered 3 (14%); silence spans ranged from 36 minutes to 60 days. The corpus yields
a five-class mechanism taxonomy. One class, chained hallucination, has no counterpart in the gray-failure
literature.

We release the labelled corpus and a runnable benchmark for fail-plausible detectors. A deterministic
reference detector flags 6 of 6 regression cases with 0 false positives on 4 clean cases, and misses
4 of 4 held-out cases. That last number is the point: pattern-based detection is a regression engine,
not a prediction engine. The benchmark exists so other teams can measure that gap on their own systems.

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

This paper contributes the examples and a way to measure detectors against them.

**Research questions.**

- **RQ1.** What mechanisms produce silent failures in a production LLM agent runtime?
- **RQ2.** Which channel discovers these failures, and how long do they stay silent?
- **RQ3.** Can the corpus support a reproducible benchmark for fail-plausible detectors, and what does
  a deterministic reference detector achieve on it?

**Contributions.**

1. **A labelled incident corpus** (§4). Twenty-two production silent-failure incidents, each with a
   full public postmortem and machine-readable labels: mechanism class, silence span, discovery
   channel, observable artifact, and whether the incident involved model fabrication.
2. **A five-class mechanism taxonomy** (§5), with an explicit discriminator between the two classes
   that are easiest to confuse.
3. **Discovery and latency measurements** (§6) with explicit denominators, reported as descriptive
   counts for this system rather than as population estimates.
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

**Hallucination.** Hallucination is usually studied as a property of models [11]. Our Class D treats it
as a property of systems. In all four fabrication incidents, the model behaved as trained: it completed
fluently over the context it received. The defect was that the system delivered polluted context. The
defense is therefore on the system side.

**Operational practice.** Site reliability engineering codified postmortems and the principle that
operational knowledge belongs in mechanisms rather than in memory [8]. Chaos engineering established
deliberate fault injection to build confidence in a system [12]. We apply the same idea one level up:
we inject known violations to check that a *guard* still fires, because an unvalidated guard and a
vacuous guard look identical from outside. Large-scale incident studies established the corpus and
root-cause template we follow [13]. The variable our setting adds is that the system under study
speaks, which changes both what a failure looks like and what detection requires.

We note a limitation of this section. Work on LLM agent reliability is recent, and several of the
studies closest to ours are preprints that have not completed peer review ([3], [4], [5], [6], [7],
[9], [10]). We mark them as such and anchor our framing on the peer-reviewed literature where possible.

---

## 3. Study Design

### 3.1 Case and context

The subject is `openclaw-model-bridge`, a public two-layer middleware connecting self-hosted and
commercial LLMs to an open-source personal-agent framework. It has run continuously on one macOS host
since March 2026. It has three parts. A **control plane** governs tool use, repairs schemas, strips
alerts from conversational context, and runs a declarative audit. A **capability plane** routes requests
across providers with capability-scored fallback chains. A **memory plane** holds a local-embedding
index over roughly 1,100 notes and runs daily synthesis jobs that push results to the user.

At the study cutoff (2026-06-11) the system had about 40 scheduled jobs, 8 providers, 4,286 unit tests
in 121 suites, and a governance ontology of 90 invariants and 827 checks. One human operator and one AI
coding assistant develop and operate it. The runtime itself is powered by separate Qwen3-class models.

We state the scale plainly because it bounds the claims. This is one system with one operator, not an
enterprise deployment. We return to what that permits and forbids in §8.

### 3.2 Unit of analysis and corpus construction

The unit is the **silent-failure incident**. An event qualifies if it (a) reached production,
(b) had a phase during which it was active while all automated indicators reported healthy, and
(c) closed with a complete postmortem.

The corpus is the complete population of qualifying incidents in the window 2026-04-09 to 2026-06-02.
It is not a sample. Twenty-two incidents qualify.

### 3.3 Data collection

A fixed postmortem protocol was adopted after the second incident and applied retroactively to the
first two. Before any code change, the protocol requires a causal chain, a three-layer root cause, a
timeline where logs permit, an analysis of why the incident happened at that moment and not earlier,
and an entry in the governance ontology.

Each incident is corroborated against at least two independent sources before a root cause is recorded.
Sources include runtime logs, the operating system audit log, version control history, test and audit
results, and operator notes. Each postmortem is a standalone public document.

### 3.4 Labelling

After the study window we labelled every incident in a single machine-readable file. Each record
carries: mechanism class; silence span; discovery channel; the observable artifact a human could have
seen; whether a model fabricated content; and a pointer to the postmortem document.

Classification used constant comparison. Each candidate root cause was matched against the existing
classes. Two consecutive non-matches forced a new class or a split. The scheme stabilized in mid-May
and did not change over the final nine incidents.

The labels are load-bearing rather than decorative. Each class drives a mechanized scanner in the
repository, so a misclassification produces a wrong scanner result, which is falsifiable.

We report **no inter-annotator agreement statistic**. The two coders were the system's operators, one
human and one AI assistant, and both also contributed to this paper. This is the study's most
significant internal-validity limitation, and we discuss it in §8 rather than minimizing it here.

---

## 4. The Incident Corpus

Table I lists all 22 incidents. This table is the answer to a practical question: what does a silent
failure in an agent runtime actually look like, case by case?

**TABLE I. The 22-incident corpus.** Class per §5. Silence is the span between the incident becoming
active and a human recognizing it. Discovery is the channel that produced that recognition. FP marks
incidents in which a model produced fabricated content.

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

We define fabrication by absence of grounding, not by inaccuracy. A fabricated claim can be
coincidentally true and is still fabricated.

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

### 6.1 Discovery channel

Table II gives the discovery channel for all 22 incidents.

**TABLE II. Discovery channel, n = 22.**

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

Automated checks discovered 3 of 22. We report that number rather than an approximation, and we note
that it is partly a selection effect: the corpus admits only incidents that survived a silent phase with
green indicators, so it excludes everything the checks caught immediately.

The magnitude of the first row is not a selection effect. The system ran 4,286 tests, 827 governance
checks, and a 19-point preflight, all green through most of these incidents.

For QA practice, we draw one recommendation. User-view observation is an observability signal and should
receive scheduled time. For QA research, the open problem is mechanizing part of what that human eye
does. Section 7 is our first attempt at measuring progress on that problem.

### 6.2 Silence latency

Silence spans in the corpus range from 36 minutes to 60 days. The distribution is not driven by code
complexity. Code-level defects were caught quickly, by tests or by the next scheduled run. The incidents
that survived for weeks lived where no test ran: deployment topology, operating system policy, the
monitoring of monitoring, and gaps between declared and actual state.

We suggest reading latency as a measure of observational distance. For silent failures, time to detect
dominates time to repair by one to two orders of magnitude, which makes detection latency a more useful
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

### 6.4 What the audit framework prevented

Mid-study we audited the governance framework against the first 15 incidents, asking for each whether
the framework as it existed *before* the incident could have caught it, and whether the guards added
*after* block the mechanism from recurring.

**TABLE III. Self-audit of the governance framework, n = 15.**

| Question | Result |
|---|---|
| Prevented the incident in advance | 0 / 15 |
| Gave partial early warning | 2 / 15 |
| Blocks the mechanism from recurring | 13 / 15 |
| Miss rooted in a dimension never considered | 12 / 15 |

These are counts over 15 incidents in one system. They are not estimates of how audit frameworks perform
in general.

Within those limits, the pattern is informative. The framework prevented none of the novel incidents and
blocks most of them from recurring. That is the behavior of a regression suite. Twelve of the fifteen
misses were in categories no invariant had contemplated, which no amount of diligence within the existing
set would have covered. If this generalizes at all, the implication for QA is that prevention of novel
failure classes must come from somewhere other than the regression apparatus.

---

## 7. From Corpus to Benchmark (RQ3)

Section 6.1 identifies the open problem: the most effective detector in this study was a human, and
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

We organize threats using the four case-study categories [15].

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
Two early incidents were reconstructed retroactively and are flagged as such in the corpus.

We note one discrepancy for the record. An earlier report of this study stated that approximately 70% of
incidents were discovered by user-view observation. The labelled corpus released with this paper gives
13 of 22, or 59%. The corpus labelling is the later and more careful pass, and it is the number we use.

**External validity.** One system, one host operating system, one operator pair, eight weeks, about 40
jobs. We claim analytical generalization of the mechanism classes and of the trigger–amplifier–concealer
structure, and we disclaim generalization of the frequencies. The settings that most bound our results
are systems without a synthesis-and-push pipeline, where Class D frequencies will not transfer, and
teams without an unusually attentive single operator, where the user-view share will be lower.

**Reliability.** Every postmortem is a standalone public document. All counts, dates, and audit results
are mechanically derivable from the public repository at the cited commit window. The AI assistant that
helped write the postmortems also helped write this paper, which raises a narrative-bias risk. We treat
that risk as first class: the human is sole author and final arbiter, all numbers are repository-derivable,
and the findings are unflattering to the narrator. The framework prevented nothing in advance, the best
detector was a person rather than the automation, and the component built to close one gap opened
another.

---

## 9. Conclusion

Eight weeks of complete postmortems from a production LLM agent runtime yield a five-class taxonomy of
silent failures. One class, chained hallucination, is specific to systems that produce language. In this
corpus a human reading the product found 13 of 22 incidents and automated checks found 3, while the
framework prevented none of them in advance and now blocks 13 of 15 from recurring.

We release the labelled corpus and a runnable benchmark so that these numbers can be tested rather than
believed. The reference detector's result on held-out cases is the honest summary of where fail-plausible
detection stands: reliable on patterns it was built for, and blind to the rest.

---

## Statement of Differences from Prior Versions

This paper is derived from arXiv:2606.14589 and from a submission to the ISSRE 2026 Industry Track that
was not accepted. The differences are substantive rather than editorial.

1. **Scope.** The prior versions presented a taxonomy and a defense framework. This version presents a
   taxonomy, a labelled corpus, and a benchmark. Sections 4 and 7 are new.
2. **Evidence.** The 22 incidents are now enumerated individually with class, silence span, discovery
   channel, and fabrication flag (Table I), and the machine-readable labels are released.
3. **Research questions.** Three research questions are stated explicitly and answered in dedicated
   sections. Prior versions asserted research questions without stating them.
4. **Statistics.** Discovery-channel figures are recomputed from the labelled corpus with explicit
   denominators. The user-view share is 13/22 (59%), correcting an earlier approximate figure of ~70%.
   Self-audit results are presented as counts over 15 incidents rather than as percentages.
5. **Taxonomy.** Classes A and B now have an operational discriminator (§5.2), addressing an ambiguity
   in the prior classification.
6. **Defense framework.** The five-pillar framework section is removed. Only the parts that bear on
   detection and on the benchmark are retained.
7. **Presentation.** The text was rewritten for a plainer register: shorter sentences, no aphoristic
   formulations, and no forward references.

---

## Generative-AI Use Disclosure

This research used Anthropic's Claude, accessed through a coding-agent interface, as an engineering and
writing collaborator. It assisted in triaging and reconstructing incident postmortems, extracting and
tabulating data from the public repository, drafting and editing prose, and proposing structure. It was
not used to generate empirical results, invent data, or make research claims. Every quantitative figure
is mechanically derivable from the public repository and was verified by the author. The author defined
the research questions, performed the classification, drew the conclusions, and takes full responsibility
for the content, including any errors. No generative-AI system is an author.

The runtime under study is itself an LLM agent system, powered by separate Qwen3-class models. The AI
collaborator described here is the author's development tool and not the system studied.

---

## Artifact Availability

The 22 incident postmortems, the labelled corpus (`docs/llm_observer_ground_truth.yaml`), the benchmark
and its reference detector, the governance ontology, and the full test suite are public at
`https://github.com/bisdom-cell/openclaw-model-bridge`. The governance engine is separately available on
PyPI as `openclaw-ontology-engine`. A data inventory maps every number in this paper to its source in the
repository. A preprint of the earlier version is available as arXiv:2606.14589, disclosed per IEEE
preprint policy.

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
