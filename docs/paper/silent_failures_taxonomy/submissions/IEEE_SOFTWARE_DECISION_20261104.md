# IEEE Software decision record — SW-2026-06-0312 (2026-11-04): REJECT

> **Manuscript:** *When Errors Become Narratives: A Longitudinal Taxonomy of Silent Failures in a
> Production LLM Agent Runtime* — Regular (feature / experience report), the compliant 4-page version
> `../latex/ieee_software_manuscript.tex` resubmitted 2026-07-01 after the 06-xx desk-return.
> **Decision:** not accepted (associate editor recommendation, accepted by the EiC). Three reviewers.
> **Where the full letter lives:** with the author (ScholarOne). This record paraphrases and quotes
> only what is needed to act; it is not a verbatim copy.
>
> **Status of the paper after this decision (single truth, mirrored in `SUBMISSION_GUIDE.md`):**
> arXiv:2606.14589 published (v1, 2026-06-15) · IEEE Software **rejected 2026-11-04** · ISSRE
> Industry Track rejected 2026-08-14 · conference/workshop track closed by venue filter (paid
> registration + attendance, V37.9.307) · **next = arXiv v2 + one archival journal**, built on
> `journal_submission_v3.md` (this session) which absorbs both review rounds.

---

## 1. What the three reviewers said (condensed)

All three rate the topic relevant/very relevant, the writing clear, the opening incident effective,
and the practitioner recommendations (diagnostic separation, error provenance, sabotage validation)
useful. All three rate technical soundness *partial*. The rejection is about evidence and framing,
not about the subject or the concept.

| # | Reviewer point (condensed) | Severity | Verdict | Where it is answered |
|---|---|---|---|---|
| R1-a | Restore per-class incident counts in Table I; without them "largest class" cannot be checked | must | **accept** | v3 §4 Table I-b (A 4 / B 3 / C 4 / D 5 / E 6) |
| R1-b | Add an explicit threats-to-validity paragraph: single system / single operator / 8 weeks; no independent coding; selection + survivorship bias (only discovered & documented failures are in the corpus) | must | **accept** | v3 §8 (all four named threats, incl. survivorship, in one place) |
| R1-c | Taxonomy built by operator + AI collaborator, no independent annotators | known | **accept, cannot fully fix** | v3 §3.4 + §8: no κ; mitigation = load-bearing labels + public corpus; an independent re-coding is invited via the released label file |
| R2-1 | **Sampling design predetermines the headline finding**: the corpus admits only failures automation missed, so "humans out-detected the automated stack" follows from the inclusion rule; a comparison needs the full population including failures automation caught | must | **accept — the strongest point in both review rounds** | new `denominator_study_20261105.md` (59 production-reached failures; automation first-signal 14%, humans 86%, user-view 47%, operator post-deploy verification 20%) → v3 §6.5 + reframed abstract/§1/§9; "unit tests caught none" dropped as unmeasurable |
| R2-2 | 70% undefined (no count, no incident-level table); 87% is a retrospective judgment about guards built in response, not prospective evidence; "regression engines not prediction engines" plausible but not demonstrated | must | **accept** | 13/22 = 59% already corrected in the workshop rewrite; v3 Table I is the incident-level table; §6.4 relabels Q3 as *ex-post design judgment* and states what would count as prospective evidence |
| R2-3 | Taxonomy lacks transparent construction: classes not mutually exclusive (env quirk → swallowing → fabrication in one chain); unit of classification unclear; need explicit coding rules, incident-by-category matrix, overlap treatment, negative-case analysis, independent validation | must | **accept (independent validation excepted)** | v3 §3.4 coding rules (unit = incident; primary class = the mechanism that produced the silence; D takes precedence when a fabrication step exists; secondary classes recorded) + §4 Table I-c secondary-class matrix + §3.2 negative cases (3 excluded docs, 2 post-cutoff, 37 undocumented production failures) |
| R2-4 | Over-claims: "latency tracked mechanism rather than code complexity" (complexity never measured); "structurally impossible" recurrence; "fail-plausible is specific to LLM systems" from four incidents | must | **accept** | v3 §6.2 (no complexity measure → claim withdrawn; latency reported against *observational distance* only), §6.6 ("impossible" → "no recurrence recorded through cutoff"), §5.4 (specific to *generative* components; D3 shows a shell fallback can produce the shape) |
| R2-5 | Either a stronger empirical design or reframe as a bounded first-person experience report without unsupported percentages | — | **reframe + add the one denominator we can compute** | v3 abstract and §9 bounded to this system; every percentage carries its denominator |
| R3-W1 | Under-engages hallucination literature: Ji et al. 2023 (ACM CSUR, intrinsic/extrinsic) and Kalai et al. 2025 (*Why Language Models Hallucinate*, structural incentives) | must | **accept** | v3 §2 "Hallucination" rewritten around both; both verified 2026-11-05 (CSUR 55(12) Art. 248, DOI 10.1145/3571730; arXiv:2509.04664) |
| R3-W2 | "Human out-detected the automated stack" is under-specified: depends entirely on what the automated checks assert | must | **accept** | v3 §3.1 "What the automated stack asserted" (tests / governance checks / preflight / watchdog / health: none asserted a semantic property of pushed content before 2026-05-26) |
| R3-W3 | "Systems failure, not the model" is overstated; the model actively fabricated | partial | **accept as reframing** | v3 §2 + §5.4: shared causation; Kalai et al. explain the model's side (guessing is rewarded); the *leverage* is on the system side because the operator controls the pipeline, not the training — a claim about where to intervene, not blame |
| R3-W4 | N=1 system / N=1 LLM; reproducibility details missing ("how the agent was run") | must | **accept** | v3 §3.1 runtime configuration (Gateway 2026.3.13-1; Qwen3-235B-A22B W8A8 text + Qwen2.5-VL-72B vision, self-hosted OpenAI-compatible endpoint; macOS host, bash 3.2, system + framework cron, launchd; 7 built-in providers + 1 plugin by cutoff) + artifact pointers; N=1 stays N=1 and is said so |
| R3-7 | Too short for the topic | — | n/a for a journal | journal length removes the 4,200-word constraint that forced the omissions R1-b names |

Points we **do not** accept, with reasons:

- *None rejected outright.* One nuance: R2 reads the 87% as if the paper claimed prospective
  prevention; the paper labels it ex-post. The fix is wording (call it a design judgment at audit
  time), not data — see R2-2.

## 2. What converges across ISSRE (08-14) and IEEE Software (11-04)

Six reviewers, two venues, the same four complaints: (1) no incident-level table / counts;
(2) percentages without denominators and a comparison the design cannot support; (3) N=1 presented
with the authority of a population study; (4) reproducibility. The V37.9.307 rewrite
(`qa4agents_workshop_paper.md`) already fixed (1) and the denominator for the *corpus*; it did not
touch the selection-effect argument (R2-1), which is new and correct. The venue filter
(no paid registration / attendance) is unchanged and both remaining paths satisfy it.

## 3. What was done in this session (2026-11-05)

1. **Denominator study** — all 135 changelog rows in the window coded (`denominator_coding_20261105.csv`),
   59 production-reached failures identified (22 corpus + 37 without a standalone postmortem),
   discovery channel tabulated for both and combined (`denominator_study_20261105.md`). Provisional,
   single coder; the CSV is the re-coding surface.
2. **Journal base draft v3** — `journal_submission_v3.md`, derived from the workshop rewrite, with
   every row in §1 applied. Numbers from the denominator study are marked *provisional* in the text
   and must be re-coded by the author before submission.
3. **Project state** — `SUBMISSION_GUIDE.md` status/venue table, README publication row,
   `docs/INDEX.md`, `CLAUDE.md` (file table + discourse table + changelog), `status.json`.

## 4. Decisions only the author can make (registered in `status.json` unfinished)

| # | Decision | Default if nothing is said |
|---|---|---|
| D1 | **Re-code the denominator CSV** (or accept the single-coder pass with the caveat kept in the paper) | keep the caveat; numbers stay marked provisional |
| D2 | **arXiv v2 now?** (no gate, no fee; replaces the v1 that still says ~70% and "complete population") | yes — v2 from `journal_submission_v3.md` after D1 |
| D3 | **Journal target**: EMSE (Springer; industrial experience reports; rigor bar matches these reviews) vs. JSS "In Practice" (Elsevier; explicit experience-report track) vs. TOSEM/TSE (longer cycle). All satisfy the venue filter. | EMSE first, JSS fallback; verify current author guidelines and GenAI policy on the official page before formatting |
| D4 | **Title** for the journal version: keep the workshop title (*A Labelled Incident Corpus and a Detection Benchmark*) or return to *When Errors Become Narratives* | keep the workshop title (claims match the evidence) |
| D5 | Whether to invite an **independent re-coder** for the 22 labels (addresses R1-c/R2-3's last item); the released label file is the interface | not required for arXiv v2; recommended before a journal submission |
