# Case-Study Methodology and Threats to Validity (drop-in section)

> **Purpose.** A strengthened, Runeson–Höst-framed methodology + threats-to-validity section for
> the *journal / conference* versions of the paper (ISSRE Industry Track, EMSE, JSS "In Practice",
> ICSE SEIP). It reframes the single-system, single-author study as a **longitudinal single-case
> study** — a recognized empirical method in software engineering — so that "n=1" is a deliberate
> design choice with an explicit generalization argument, not a weakness reviewers must forgive.
>
> **How to use.** Replace the paper's §3 (System Context and Methodology) preamble and §8
> (Threats to Validity) with the two parts below, or insert Part A before §4 and Part B as §8.
> The IEEE Software magazine version does *not* need this depth (magazines reward a focused
> threats paragraph, not a methods chapter); keep it light there and full here.
>
> **Key citation to add:** Runeson & Höst, "Guidelines for Conducting and Reporting Case Study
> Research in Software Engineering," *Empirical Software Engineering* 14(2):131–164, 2009. All
> four validity categories and the term *analytical generalization* below are anchored to it.

---

## Part A — Research method: a longitudinal single-case study

### A.1 Method selection and rationale

This work is a **longitudinal, exploratory, single-case study** in the sense of Runeson and Höst
[Runeson & Höst 2009]. Case study is the appropriate method here for three reasons that follow
directly from the phenomenon under investigation. First, *silent failures are not reproducible on
demand*: by definition they manifest only in a continuously operating system whose automated
indicators remain green, which precludes a controlled experiment and rules out
benchmark-trace sampling (the failures are absent from any trace that records task completion).
Second, the variables of interest — **silence latency, discovery channel, and defense
evolution** — only exist over real operational time; they cannot be elicited in a laboratory
snapshot. Third, the unit that produces these variables is an *operating system in context*, and
case study is the established method for studying "a contemporary phenomenon in its real-life
context" when the boundary between phenomenon and context is not clear-cut.

We therefore make the single-case design explicit rather than incidental. The case is *revelatory*
and *longitudinal*: revelatory because it provides access to a phenomenon — production
fail-plausible fabrication with complete causal-chain postmortems — that is rarely documented
publicly, and longitudinal because the same case is observed across an eight-week window in which
both failures and defenses evolve.

### A.2 Case and unit of analysis

- **Case (context):** one personal-assistant LLM agent runtime (`openclaw-model-bridge`), in
  continuous production on a single host since March 2026; ~40 scheduled jobs, 8 LLM providers, a
  tool-governance control plane, and a knowledge-base memory plane; defended by 4,286 unit tests
  and 827 declarative governance checks at the study cutoff.
- **Unit of analysis:** the *silent-failure incident* — an event that (a) reached production, (b)
  had a silent phase during which the system was failing while all automated indicators stayed
  green, and (c) was closed with a complete postmortem. The embedded sub-unit is the *mechanism*
  (how the error evaded observation), which is the basis for the taxonomy.

The single-case-with-embedded-units design (case = the runtime; embedded units = 22 incidents,
further decomposed into ≥28 mechanism manifestations) is what licenses within-case
cross-incident comparison while keeping the context fixed.

### A.3 Propositions / research questions

The study is exploratory but proposition-guided. We carried four questions into the corpus:

- **RQ1 (taxonomy):** What mechanism classes account for how silent failures evade observation in
  an LLM agent runtime?
- **RQ2 (latency):** How long do silent failures persist before detection, and what does latency
  correlate with?
- **RQ3 (discovery):** Through which channel are silent failures actually detected?
- **RQ4 (defense):** Which defenses prevent recurrence, and how does a lesson travel from a single
  fix to a structural guarantee?

### A.4 Data collection procedure

Data collection followed a **fixed, pre-registered-in-repository postmortem protocol** (the
"exception-analysis constitution"), adopted after the second incident and applied retroactively to
the first two. For every qualifying incident, before any fix, the protocol mandates: (1) a full
causal-chain diagram (timeline × layer × logic × architecture); (2) a three-layer root cause
(*trigger* / *amplifier* / *concealer*); (3) a per-minute timeline reconstruction where logs
allow; (4) a condition-combination ("why now, not before") analysis; and (5) an entry feeding the
governance ontology. This yields **methodological triangulation across data sources** — runtime
logs, the OS audit log, the version-control history, the test/governance results, and the
operator's contemporaneous notes — each incident being corroborated by at least two independent
sources before its root cause is recorded.

The corpus is the *complete population* of qualifying incidents in the window (2026-04-09 to
2026-06-02), not a sample: every incident meeting the three criteria is included, which removes
sampling bias within the window (the residual selection effect — failures still silent at cutoff
— is treated as a survivorship threat in B.4).

### A.5 Analysis: mechanism-oriented constant comparison

Classification proceeded by **constant comparison**. Each new postmortem's "true root cause" was
matched against the existing classes; two consecutive non-matches forced a class split or a new
class. We classify by mechanism rather than location because a location taxonomy proved to have
no defensive value (the same mechanism recurred across unrelated components) whereas a
mechanism-level defense immunizes a whole class. The scheme stabilized in mid-May and was stable
over the final 9 incidents — an emergent **saturation** signal appropriate to an exploratory
design. A distinguishing strength of the coding is that the classes are *load-bearing, not merely
descriptive*: each class drives a mechanized scanner whose findings are objective and independently
checkable, so a misclassification has a falsifiable consequence rather than only a narrative one.

### A.6 The generalization argument (analytical, not statistical)

We make the generalization basis explicit because it is the crux of a single-case study. We claim
**analytical generalization** in the sense of Runeson & Höst (generalization to *theory*), not
statistical generalization (to a population). Concretely:

- **What generalizes (theory-level):** the five mechanism classes; the *trigger/amplifier/concealer*
  causal structure; the latency ∝ observational-distance relationship; the *point fix → meta-rule →
  scanner* maturation; and *audit-as-regression-engine*. These are claims about **mechanisms**, and
  mechanisms are not artifacts of one stack — Classes A, C, and E have direct ancestry in
  distributed-systems and hardware failure literature, and Class D follows from a property
  (fluent completion over context) common to all LLM agent runtimes with synthesis-and-push
  pipelines.
- **What does not generalize (population-level):** the *frequencies, shares, and latencies* are
  descriptive statistics of this case (one system, one operator pair, one OS, eight weeks) and are
  reported as such — point estimates with no claim of populational validity. The ~70% user-view
  discovery share in particular is presented as an **existence proof** (a mature automated stack
  *can* be out-detected by a user's eye), not as a constant.

This is the standard and defensible posture for a revelatory case study, and it is the framing we
ask reviewers to evaluate the work against.

---

## Part B — Threats to validity (Runeson–Höst four-category schema)

We organize threats under the four standard case-study validity categories and state the
mitigation for each.

### B.1 Construct validity (are we measuring what we claim?)

The central construct, *silent failure*, requires a silence span with concurrently green
indicators. It was applied at postmortem time, which risks hindsight framing. Borderline cases —
loud-but-cause-free alerts (the "HTTP 502" with no actionable cause) — were included only when the
*actionable* signal was absent, a judgment a reasonable observer could draw differently for 2–3
incidents. The *fail-plausible* construct is defined by **absence of grounding**, not by
inaccuracy of output; we note explicitly that a fabrication is not refuted by occasional
coincidental relevance (one fabricated remediation direction later proved tangentially correct),
because grounding, not correctness, is the discriminating property. **Mitigation:** every construct
is operationalized against repository artifacts (a silence span is a timestamp gap in logs against
green CI/audit records; a fabrication is a claim with no source in the input context), so the
classification is checkable rather than purely interpretive.

### B.2 Internal validity (are the causal claims sound?)

Each postmortem asserts a causal chain. The principal threat is **confirmation bias in mechanism
assignment**, compounded by the coders being the system's two operators (one human, one AI
collaborator) with no independent annotators — we therefore report **no inter-annotator agreement
(no κ)**, which is the most significant internal-validity limitation of the study. Three factors
partially mitigate it: (1) the classes are load-bearing — each drives a scanner whose findings are
objective, so an incorrect class produces a falsifiable scanner result, not just a label; (2)
causal claims are required to be triangulated across ≥2 independent data sources (logs, OS audit
log, VCS history, test results) before being recorded; and (3) the *sabotage-validation*
discipline independently confirms the proposed amplifier/concealer of many incidents by
deliberately re-introducing the mechanism and observing the predicted failure. Postmortem quality
additionally varies with log retention; two early incidents were reconstructed retroactively under
the protocol and are flagged as such.

### B.3 External validity (to what does this generalize?)

One system, one host OS, one operator pair, eight weeks, ~40 jobs. As stated in A.6, we claim
analytical generalization of the **mechanism classes and cross-cutting structures** and explicitly
disclaim generalization of **frequencies and shares**. The two settings that most bound external
validity are (1) systems *without* a synthesis-and-push pipeline, to which Class D frequencies do
not transfer (though the mechanism remains latent wherever an LLM consumes machine-generated
context), and (2) teams *without* an unusually attentive single operator, which likely lowers the
user-view discovery share. **Mitigation:** the taxonomy's mechanism orientation, the public
postmortem corpus, and the project-agnostic release of the governance engine
(`openclaw-ontology-engine` on PyPI) are provided so that other teams can test whether the classes
recur in *their* systems — the empirical replication this case study invites.

### B.4 Reliability (would another researcher reach the same result?)

The repeatability threat in a single-operator study is that the procedure and chain of evidence are
not independently inspectable. **Mitigation:** the study is designed for an unusually strong
reliability audit trail — every incident postmortem is a standalone public document; the
taxonomy's source-of-record is a single public catalog; the governance results, test counts, and
dates are **mechanically derivable from the public repository** at the cited commit window; and a
number-by-number *data inventory* maps every quantitative claim in the paper to its repository
source. A second researcher cannot re-run the eight weeks, but can re-derive every number, re-read
every causal chain, and re-execute every scanner and sabotage test — which is the form of
reliability available to, and appropriate for, a longitudinal field study.

### B.5 Researcher–instrument and AI-involvement threat

A threat specific to this study: the AI collaborator that co-wrote the postmortems also co-wrote
the paper, raising the possibility of **narrative bias toward a tidy story**. We treat this as a
first-class threat rather than a footnote. **Mitigation:** the human author is the sole author and
final arbiter of every claim; all reported counts and dates are mechanically derivable from the
public repository (offered as the primary external check on narrative bias); and the study's own
findings are deliberately *unflattering to the narrator* (0% ex-ante prevention; the best detector
being a human, not the automation the author built; the convergence engine the author is proud of
having *caused* three of its own incidents) — an internal-consistency signal that the account was
not optimized for the system's, or the collaborator's, image.
