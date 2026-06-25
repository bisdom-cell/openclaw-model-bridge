# When Errors Become Narratives: A Longitudinal Taxonomy of Silent Failures in a Production LLM Agent Runtime

**Submission type:** IEEE Software — Feature article (experience report / "lessons learned")
**Author:** Wei Wu, Independent Researcher (wuweinanonuaa@gmail.com)
**Target length:** ≤ 4,200 words including 250 words per table/figure (1 table + 1 figure budgeted ≈ 500). Body ≈ 3,700 words.
**Companion preprint:** arXiv:2606.14589 (extended version, full 22 postmortems).

---

## Abstract (150 words)

Large language model (LLM) agent runtimes increasingly run unattended for weeks, calling tools
and pushing results to humans. I report an eight-week longitudinal study of *silent failures* in
one such production system: a personal-assistant agent runtime defended by 4,286 unit tests and
827 governance checks, in which I documented 22 incidents with full root-cause postmortems. One
failure class proved unique to systems that *speak*: the LLM transforms an upstream error into
fluent, plausible, false output and delivers it to the user — a behavior I call **fail-plausible**.
Three findings unsettle common assumptions: ~70% of silent failures were caught by a human
reading the product, not by the automated stack; the governance audit prevented 0% of novel
incidents but blocked 87% from recurring; and the longest failures lived in the *seams* between
simple parts, not inside complex ones. I distill the defenses that worked into practices
adoptable without my stack.

**Index Terms** — LLM agent systems, software reliability, silent failures, fail-plausible,
observability, fault taxonomy, postmortem analysis, hallucination, AI-assisted software
engineering, experience report.

---

## Three practitioner takeaways

1. **Schedule user-view observation as a first-class observability signal.** In this study a
   human reading the actual pushed output caught ~70% of silent failures that thousands of green
   tests and hundreds of green checks missed. Put 30 minutes a week on the calendar to *use* the
   system, not test it.
2. **Keep diagnostics out of data channels, and preserve error cause across every hop.**
   An LLM downstream will narrate whatever lands in its context. A single `>&2` redirection
   severed one fabricated "platform crisis." Treat stderr discipline and cause-preserving error
   chains as security controls for agent pipelines.
3. **Convert each incident into a mechanized check, and prove every guard by sabotage.** A
   lesson that stops at a point fix recurred within two days; an unvalidated guard is
   indistinguishable from a vacuous one (I found 67 checks silently running empty strings for
   months).

---

## A crisis that never happened

One morning my agent runtime pushed me a confident industry analysis about a "Hugging Face
platform crisis." It was well written, on schedule, thematically plausible — and completely
fabricated. No alarm fired. Every unit test was green. Every health check passed.

Here is what actually happened. A nightly knowledge-synthesis job scraped content containing an
isolated Unicode surrogate, which made `json.dump` raise mid-write. The truncated request body
drew an HTTP 400 from the model adapter. So far, an ordinary bug. The damage came next: the
job's logging function wrote its diagnostics to *stdout*, and the caller captured stdout by
shell command substitution as the *signal payload*. The cache filled with strings like
`HTTP Error 400: Bad JSON … waiting 3s before retry`. The downstream reduce-step LLM, prompted
to find cross-domain signals, did exactly what language models do with anomalous-but-thematic
text: it composed a fluent narrative — platform trouble being the most probable story for
error-code vocabulary — and the system delivered it to me as a routine insight digest. Every
component succeeded. The pipeline laundered an encoding bug into industry analysis.

The fix that severed the chain was a single character: `>&2`, redirecting diagnostics to stderr
so they could never enter the data channel. I added defense in depth around it — surrogate
sanitization, an encoding-error policy, anti-fabrication prompt guards — but the load-bearing
repair was one redirection operator.

This incident is not an outlier. It is the most vivid member of a failure class I believe is
specific to LLM agent systems, and it changed how I think about reliability for software that
generates language.

## Fail-plausible: the silent failure of systems that speak

The reliability literature has long known that the most damaging production failures are not
crashes. *Gray failure* degrades cloud systems while their failure detectors report health [1];
*fail-slow* hardware throttles for hours before anyone suspects the disk [2]. Both share one
defining property — **differential observability**: the application suffers, but the observer
built to notice does not.

LLM agent runtimes inherit this entire problem class and add a worse one. An agent runtime is a
generator of fluent language. When an upstream error leaks into its context window, its failure
mode is not silence; it is *plausible speech*. I call this **fail-plausible**: a failure in
which the system transforms an internal error into coherent, contextually appropriate, and false
output. Fail-plausible is to agent systems what gray failure was to cloud infrastructure — the
dominant, hardest-to-see class — but it is strictly worse for the observer. Gray failure starves
the detector of a signal; fail-plausible hands the human a counterfeit one. The observer is not
merely blind; it is being convincingly lied to by the failure itself.

Crucially, the model is not malfunctioning. In every fabrication I documented, the LLM behaved
exactly as trained: fluent completion over its context. The failure is a *systems* failure — the
pipeline delivered polluted context (captured error logs, stale alerts, unlabeled enrichment) to
a component whose job is to narrate whatever it is given. That reframing matters because it moves
the defense off the model and onto the system, where engineers actually have leverage.

## The system, and how I studied it

The subject is a two-layer middleware (public repository `openclaw-model-bridge`) connecting
self-hosted and commercial LLMs to an open-source personal-agent framework on WhatsApp and
Discord. It has run continuously on a single macOS host since March 2026, with roughly 40
scheduled jobs across two cron schedulers, 8 LLM providers, a tool-governance proxy, and a
knowledge-base memory plane — defended at the study cutoff by 4,286 unit tests in 121 suites and
827 declarative governance checks. One human operator (me) and one AI engineering collaborator
(Claude, used through a coding-agent interface) develop and operate it; the runtime itself is
powered by separate Qwen3-class models.

The corpus is every incident between 2026-04-09 and 2026-06-02 that reached production, had a
*silent phase* (failing while all automated indicators stayed green), and was closed with a full
postmortem. Twenty-two qualify. Each follows a mandatory in-repo protocol that requires, *before
any fix*: a full causal-chain diagram (timeline × layer × logic); a three-layer root cause
(trigger / amplifier / concealer); a timeline reconstruction; a "why now, not before"
condition-combination analysis; and an entry feeding the governance ontology. Every postmortem
is a standalone public document, and the numbers in this article are mechanically derivable from
the repository — which I offer as the primary check on narrative bias, including the bias of an
AI-assisted author writing about AI-assisted operation.

I classify by **mechanism** (how the error evaded observation), not **location** (which job
failed), because the same mechanism recurs across unrelated components: a location taxonomy had
no defensive value, while one mechanism-level scanner immunized every location at once.

## Five ways an agent fails in silence

**Table 1 — A mechanism-oriented taxonomy over 22 incidents.**

| Class | Mechanism | Silence span | Signature |
|---|---|---|---|
| A — Environment/platform quirk | Logic is correct; the runtime environment's implicit behavior defeats it | hours–weeks | green in dev, silent in prod |
| B — Design-assumption mismatch | Code assumes a deployment topology, contract, or input shape that reality violates | days | tests mirror the assumption, not the caller |
| C — Error swallowing & dilution | The error is eaten by a layer, or stripped of cause across layers | hours–days | the alert that arrives carries no usable information |
| D — Chained hallucination (**fail-plausible**) | The LLM converts polluted context into confident false output | hours–days | the user receives counterfeit health |
| E — Operational omission & forensic blind spot | A deploy/registration step never happened, or the diagnostic instrument itself is blocked and reads as "normal" | days–**60 days** | declared state ≠ runtime state; instruments lie |

Three classes will be familiar to systems engineers. **Class A** is the dev–prod environment
gap with teeth: bash 3.2 not propagating an `ERR` trap into a function silently disarmed a
watchdog; BSD `awk` aborting on invalid UTF-8 killed the *monitoring* script for seven days.
**Class C** is error dilution: an evening digest failed with "HTTP 502" for two days while the
true cause — a fallback provider's quota exhausted after the primary's circuit breaker opened —
sat in an upstream response body that three successive layers each declined to read. **Class E**
is the largest and longest: a new job fully implemented, tested, and registered simply never ran
because the final step (writing the crontab line) was a human memory item, and three small bugs
conspired to report success.

Two classes are sharper in an agent runtime. **Class B** includes any *positional* parse of LLM
output: a parser that indexed `lines[i+1]`, `lines[i+2]` shifted every field by one the day the
model omitted a line — because instruction-following is a distribution, not a contract. And
**Class D** is fail-plausible, narrated above and elaborated below.

The fabrications were not all dramatic. A weekly review job whose LLM call failed fell back to a
mechanical line filter that emitted leftover container headings as "review content" and wrote
`"llm": true` to its status file unconditionally — *a hallucination implemented in shell.* An
evening digest, handed the day's papers as unlabeled context, inferred my project "must have
shipped" and announced a release of an internal version number that exists only in a changelog.
The common structure is a **pollution chain**: a Class A/B/C failure deposits non-signal content
where a downstream LLM expects signal; the LLM completes fluently; and the output inherits the
*form* of health with the *content* of failure.

## Three uncomfortable findings

**A human reading the product out-detected the entire automated stack.** Sorting incidents by
discovery channel, roughly 70% were ultimately caught by *user-view observation* — me noticing
"this digest looks shallow," "why two windows?", "I didn't get yesterday's analysis." The 4,286
tests, 827 checks, and a 19-point preflight stayed green through most incidents; unit tests
caught essentially none of this corpus (by selection — anything they caught never became
silent). I institutionalized a weekly 30-minute observation ritual: no coding, just using the
system as a user along four axes (alert noise, push latency, information density, response
quality). It kept out-detecting the automated stack. The research-grade version of this — an LLM
"observer" that critiques yesterday's output [9] — is promising and found real regressions, but
it is itself an LLM component subject to every class in this taxonomy (mine shipped with a Class
B bug and a hallucinated truncation), so it needs the same governance as what it judges.

**The audit prevented nothing and blocked almost everything.** Mid-study I audited my own
defense framework against the first 15 incidents with three questions each: could the audit, as
it existed *before* the incident, have caught it (Q1)? Why not (Q2)? Do the guards added *after*
block the class going forward (Q3)?

| Metric | Value |
|---|---|
| Ex-ante prevention (Q1) | **0 / 15 = 0%** |
| Ex-post regression blocking (Q3) | **13 / 15 = 87%** |
| Misses rooted in a dimension never conceived | 12 / 15 = 80% |

The 0% is not an indictment — it is the job description. Audits, like regression suites, encode
the past; 80% of misses were *blank categories* no invariant had ever contemplated. The honest
posture: maximize the regression rate, accept that prevention of *novel* classes comes from
elsewhere (user-view observation, adversarial review, target-environment exposure), and minimize
the *conversion latency* from a new incident to a mechanized guard.

**The longest failures lived in seams, not in complex code.** Latency tracked the failure
mechanism, not code complexity. Code-level bugs die young — tests and the next run catch them.
What survived for weeks lived where no test runs: deployment topology, OS policy, the gap
between declared and runtime state, and monitoring-of-monitoring. The corpus's longest silence
(60 days) was a backup failing with EPERM, where every forensic tool I reached for was *itself*
being silently denied by the macOS TCC sandbox and returning empty output the pipeline recorded
as "normal." An instrument that cannot tell "nothing there" from "I was not allowed to look"
manufactures false reassurance. Latency, in other words, measures *observational distance*: the
further a mechanism sits from any existing observer, the longer it lives. I now treat
silence-latency percentiles as a reliability metric in their own right — for silent failures,
time-to-*detect* dominated time-to-repair by one to two orders of magnitude.

## What actually defends against silent failure

The defenses that held up are less a framework than a discipline. Five practices, mapped to the
takeaways above:

**Make diagnostics structurally unable to become data, and never let an error lose its cause.**
Shell diagnostics go to stderr, enforced repository-wide by a scanner; alerts are tagged at
every producer and stripped from chat context *before* truncation; error chains must preserve
upstream cause across every hop. In an agent system the "client" of an error is often another
LLM prompt, so a diluted or misrouted error is one step from becoming fabrication input. This is
the Class D defense, and it lives mostly in plumbing, not prompts.

**Treat the LLM's context as a supply chain.** Beyond hygiene, label provenance: a
source-credibility module tags every ingested source on a five-tier scale at prompt-injection
time, and a shared anti-fabrication guard module (imported, never copy-pasted) injects a
cumulative ladder of prohibitions into all LLM-calling jobs — the upper levels containing
*literal* phrases harvested from real fabrication incidents, including the exact false release
string from the digest above. After deployment, the targeted fabrication patterns (multi-hop
causal chains, "therefore"-style necessity claims) dropped 53–92% in the affected job. Prompt
guards are the *last* layer, behind hygiene and provenance — never the first.

**Climb the three-step ladder: point fix → meta-rule → scanner.** A point fix repairs this bug;
empirically insufficient — an import-omission fix at one site recurred at eight sites via an
automated injector two days later, because the lesson existed only as a diff. A meta-rule names
the lesson as a cross-case rule (I reached 23: diagnostics-to-stderr, key-based parsing,
alert-path independence, declared-state convergence, reserved-file unwritability). A mechanized
scanner encodes the meta-rule as a check that runs in CI and the daily audit (14 by study end).
Only at step three does recurrence become structurally impossible rather than culturally
discouraged. Every meta-rule that reached step three has zero recorded recurrences; every
recurrence I logged involved a lesson that stopped at step one.

**Prove every guard by sabotage.** New guards must be shown *alive* — introduce the violation
they target, watch them fire, revert. This caught guards that matched their own assertion
strings, tests whose fixtures mirrored the wrong assumption, and 67 governance checks that had
been executing empty strings for months (discovered only when a *new* invariant's sabotage
refused to fail). In a regime whose primary failure is silence, an unvalidated detector is
indistinguishable from a vacuous one.

**Make alarms outlive their subject, and make monitoring watch itself.** A nine-hour total
outage went unannounced because three independent alarms each failed — including a quiet-hours
filter that suppressed the very channel meant for emergencies. The rule: an alert path must not
depend on the failing subject (gateway-down notices travel a transport the gateway cannot take
down), and the watcher needs a watcher (an independent job age-checks a heartbeat canary file).

## Seams, not components

Facing a week with five incidents, I asked the obvious question: *is this system failing because
it has become too complex?* The postmortem-backed answer was more useful: **no individual
failing part was complex.** A symlink. An `abspath` call. A one-line registry entry. A boolean
default. Every part was simple and locally correct; every incident lived in a *combination*.
Components are covered by thousands of tests; **combinations grow superlinearly, and tests cover
only the combinations someone imagined.** Complexity is about parts; incidents are about seams.

This inverts the reflex response to an incident — *add a guard* — because a guard is itself a
part, and therefore new seams. (A convergence engine I built to close the declared-vs-runtime
seam opened an observer-mutates-observed seam that caused three incidents before I caged it with
a rule: *audit observes and never mutates*.) The posture I landed on, codified as a standing
"Sunset Law": before adding any mechanism, try to *retire* an equivalent one; one logical entity
should have one physical representation (multiple representations *will* drift, and the bug lives
between them); and defenses are themselves incident surfaces — prefer **seam reduction**
(unify representations, shrink the dev–prod gap, keep observers read-only) over defense
accretion. In the weeks since, the system retired more representation duplicates than it added
invariants, and — with due caution about confounds — incident frequency fell while feature
velocity did not.

One honest note on method. A single human producing 22 publication-grade postmortems, 90
invariants, and 14 scanners in eight weeks is achievable *only* with an AI collaborator — and
that same collaborator will, unguided, pattern-match symptoms to plausible fixes at high speed,
industrializing the cosmetic trigger-level fix (one documented chain was five cascading "fixes"
to a problem whose correct resolution was a single file copy). The rigid postmortem protocol
exists largely to counter that. AI collaboration shifts the binding constraint of reliability
engineering from implementation bandwidth to *judgment discipline* — which is exactly what the
meta-rules encode. This is a single-system case study, and I claim the *classes and structures*
generalize to long-running agent runtimes with human-facing output, not the frequencies; the
public repository and a number-by-number data inventory are offered so others can check the
chain and, ideally, replicate the method on their own systems.

## The failure that should frighten you

Eight weeks of complete postmortems yield a five-class taxonomy of silent failures, of which one
— fail-plausible chained fabrication — is specific to systems that speak. The record is
uncomfortable in useful ways: thousands of tests and hundreds of checks prevented none of the
novel incidents while blocking 87% from recurring; the best detector was a human reading the
product; and the longest failures lived in the seams between simple, correct parts. The failure
your agent system should frighten you with is not the crash. It is the confident paragraph, on
schedule, in perfect grammar, about a crisis that does not exist — pushed to a user who has every
reason to believe it, by a pipeline in which every component worked.

---

## Acknowledgment of AI assistance

This article and the underlying postmortems were prepared with the assistance of Anthropic's
Claude, used as an engineering and writing collaborator through a coding-agent interface. Claude
contributed to incident triage, postmortem drafting, repository data extraction, and prose
editing. The author, Wei Wu, is the sole author, defined all research questions and conclusions,
verified every reported figure against the public repository, and is solely responsible for the
content. No generative-AI system is a listed author. (See the project's disclosure statement for
the full division of contributions.)

---

## References

[1] P. Huang et al., "Gray Failure: The Achilles' Heel of Cloud-Scale Systems," *HotOS*, 2017.

[2] H. S. Gunawi et al., "Fail-Slow at Scale: Evidence of Hardware Performance Faults in Large
Production Systems," *USENIX FAST*, 2018.

[3] M. Cemri et al., "Why Do Multi-Agent LLM Systems Fail?" (MAST), arXiv:2503.13657, 2025.

[4] B. Ranganathan, M. Zhang, and K. Wu, "Enhancing Reliability in AI Inference Services: An
Empirical Study on Real Production Incidents," arXiv:2511.07424, 2025.

[5] C. Ezell, X. Roberts-Gaal, and A. Chan, "Incident Analysis for AI Agents," *AAAI/ACM AIES*,
2025; arXiv:2508.14231.

[6] B. Beyer, C. Jones, J. Petoff, and N. R. Murphy (eds.), *Site Reliability Engineering: How
Google Runs Production Systems*, O'Reilly, 2016.

[7] A. Basiri et al., "Chaos Engineering," *IEEE Software*, vol. 33, no. 3, pp. 35–41, 2016.

[8] S. Ghosh, M. Shetty, C. Bansal, and S. Nath, "How to Fight Production Incidents? An Empirical
Study on a Large-Scale Cloud Service," *ACM SoCC*, 2022.

[9] L. Zheng et al., "Judging LLM-as-a-Judge with MT-Bench and Chatbot Arena," *NeurIPS*, 2023;
arXiv:2306.05685.

[10] L. Huang et al., "A Survey on Hallucination in Large Language Models: Principles, Taxonomy,
Challenges, and Open Questions," arXiv:2311.05232, 2023.

[11] P. Runeson and M. Höst, "Guidelines for Conducting and Reporting Case Study Research in
Software Engineering," *Empirical Software Engineering*, vol. 14, no. 2, pp. 131–164, 2009.

[12] M. Taraghi, M. M. Morovati, and F. Khomh, "Real Faults in Model Context Protocol (MCP)
Software: A Comprehensive Taxonomy," arXiv:2603.05637, 2026.

---

*Extended version with all 22 postmortems, the full taxonomy, and the defense framework:
arXiv:2606.14589. Governance engine available as `openclaw-ontology-engine` on PyPI.*
