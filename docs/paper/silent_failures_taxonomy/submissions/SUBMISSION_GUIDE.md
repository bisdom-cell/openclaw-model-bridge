# Submission Guide — Silent Failures Taxonomy paper

> **Status:** the paper is published as a preprint (arXiv:2606.14589). This package converts it
> into a *formally publishable* version for a single, unaffiliated, AI-assisted author who is
> willing to do major revisions and for whom a recognized practitioner magazine counts as
> "formal publication."
>
> **What's in this folder**
> - `ieee_software_experience_report.md` — **(a)** ready-to-submit IEEE Software feature
>   (experience report, ~3,400 words within the 4,200 budget, 150-word abstract, 3 practitioner
>   takeaways, 12 refs). **Primary target.**
> - `threats_to_validity_and_case_study_method.md` — **(b)** Runeson–Höst single-case-study
>   methodology + four-category threats-to-validity section. Drop into the *journal/conference*
>   versions (ISSRE / EMSE / JSS / SEIP) to defend "n=1" as a deliberate design.
> - `ai_disclosure_and_acknowledgments.md` — **(c)** venue-compliant AI disclosure, acknowledgments,
>   and CRediT contributions (AI not an author; Wei Wu sole author).
> - This guide — per-venue checklist, cover notes, and the policy items you must verify yourself.
>
> **The one thing only you can do:** confirm each venue's *current-year* CFP deadline and the
> exact placement of the GenAI disclosure. Author name/affiliation are already filled (Wei Wu,
> Independent Researcher). Everything else here is drafted to submit.

---

## 0. Two things to do once, before any submission

1. **Re-verify every number.** Re-run the repository's data inventory (`data_inventory.md`) so the
   counts in whichever version you submit match the repo at submission time (the magazine version
   uses 4,286 tests / 827 checks / 90 invariants / 22 incidents / ~40 jobs / 8 providers — the
   study-cutoff snapshot; keep that snapshot consistent, do *not* silently update to today's
   higher numbers, and say "at the study cutoff" as the paper does).
2. **Flip the AI from co-author to disclosed assistant.** The arXiv version lists Claude in a
   title-page footnote as a collaborator. For IEEE/ACM/USENIX/Springer/Elsevier venues, Claude must
   **not** appear in the author block. Use the blocks in `ai_disclosure_and_acknowledgments.md`.

---

## 1. Probability-ranked targets (recap)

| Tier | Venue | Why it fits you | Deliverable to use |
|---|---|---|---|
| 🟢 High | **IEEE Software** (feature / experience report) | Magazine explicitly welcomes "limitations and failures of past projects"; practitioner bar; rolling submission; not double-blind; magazines count for you | `ieee_software_experience_report.md` |
| 🟢 High | **ISSRE Industry Track** | Reliability flagship; track seeks "experiences and lessons learned"; silent failures = reliability; single author / no affiliation fine for industry track | arXiv full paper + section **(b)** |
| 🟢 High | **AGENT workshop @ ICSE** (or similar agent/reliability workshop) | Scope = risk/robustness/observability in agentic systems; accepts experience reports + position papers; workshops have high acceptance | condensed full paper + **(b)** |
| 🟡 Medium | **JSS "In Practice" track** | Journal; accepts Experience Reports; no affiliation requirement; archival | full paper + **(b)** + **(c)** |
| 🟡 Medium | **EMSE** | Accepts industrial experience reports; rigorous; n=1 defensible via **(b)** | full paper + **(b)** + **(c)** |
| 🟡 Medium | **ICSE SEIP** | Practice track; **not** double-blind (favorable); arXiv OK; competitive | full paper + **(b)** |
| 🔴 Lower (for you) | FSE Industry | Good fit but **double-anonymous + discourages arXiv** → your public preprint creates an anonymization conflict | — |
| 🔴 Lower (cold) | ACM Queue / CACM Practice | **Invitation/commissioned** — can't cold-submit; path is to interest an editor in the topic | pitch, not submit |

> Sources for the above (verified at search time; confirm current deadlines/policies yourself):
> IEEE Software author info `computer.org/csdl/magazine/so/write-for-us/14426`; ACM Queue guidelines
> `queue.acm.org/author_guidelines.cfm`; ACM policy FAQ `acm.org/publications/policies/frequently-asked-questions`;
> ICSE 2026 SEIP & AGENT, FSE 2026 Industry on `conf.researchr.org`; ISSRE 2026 industry track
> `cyprusconferences.org/issre2026/industry-track/`; EMSE `emsejournal.github.io`; GenAI-policy
> landscape study arXiv:2410.11977. **Note:** the deep-research verification pass could not complete
> (account spend limit), so treat venue specifics as search-sourced and re-confirm on the official
> pages before submitting.

---

## 2. Primary submission: IEEE Software — step by step

**Format check (already met by the draft):** feature article ≤4,200 words incl. 250/table+figure;
150-word abstract; ≤15 references; 3 actionable practitioner takeaways. The draft uses 1 table
(taxonomy) and no figure, leaving budget if you want to add the D1 pollution-chain figure
(Fig. 3 from the arXiv version) — optional; the prose already carries the D1 story.

**Checklist**
- [ ] Confirm IEEE Software's current submission portal and template (ScholarOne via `computer.org`).
- [ ] Paste `ieee_software_experience_report.md` into the IEEE Software Word/LaTeX template.
- [ ] Author block: Wei Wu, Independent Researcher (no affiliation is fine — state "Independent
      Researcher").
- [ ] Keep the 3 "practitioner takeaways" — IEEE Software requires actionable insights; they are
      a scored element.
- [ ] Move the AI acknowledgment to the template's Acknowledgments field; keep the one-paragraph
      version (`ai_disclosure_and_acknowledgments.md` §6) as a footnote if space allows.
- [ ] Cite the arXiv extended version (it's allowed and expected; arXiv ≠ prior publication for
      IEEE — but confirm the IEEE preprint clause on the author page, as their wording on
      prior/simultaneous publication is stricter than ACM's).
- [ ] Optional: add Fig. 3 (D1 chain) — redraw the ASCII diagram as a clean figure.
- [ ] Run the repository security scan once on the final text (no keys / phone numbers / internal IPs).

**Cover note (paste into the submission "comments to editor"):**

> Dear IEEE Software Editors,
>
> I submit "When Errors Become Narratives: A Longitudinal Taxonomy of Silent Failures in a
> Production LLM Agent Runtime" as a feature-length experience report. It distills an eight-week longitudinal
> study of silent failures in a continuously operating LLM agent runtime into three findings and a
> set of practices practitioners can apply directly: that a human reading the product caught ~70%
> of failures the automated stack missed; that the governance audit prevented 0% of novel
> incidents while blocking 87% from recurring; and that the longest failures lived in the seams
> between simple components. The article introduces *fail-plausible* — the failure mode in which an
> agent narrates an internal error into confident, false output — which I believe names a problem
> the IEEE Software readership is actively hitting in production. An extended version with all 22
> postmortems is available as a preprint (arXiv:2606.14589); all reported figures are mechanically
> derivable from the public source repository. I disclose the use of an AI writing/engineering
> collaborator in the Acknowledgments; I am the sole author and take full responsibility for the
> content.
>
> Wei Wu, Independent Researcher

---

## 3. Parallel submission: ISSRE Industry Track (strongest topical fit)

ISSRE's Industry Track explicitly seeks experiences and lessons learned in software reliability,
which is exactly this paper. Run it **in parallel** with IEEE Software — a non-overlapping
audience, and the industry-track format differs enough from a magazine feature that there is no
dual-publication conflict for the *condensed* presentations (do **not** submit the identical
archival manuscript to two archival venues simultaneously; the magazine article and the
industry-track paper are different documents).

### ✅ CFP confirmed — 2026-06-28 (ISSRE 2026 Industry Track) — TIME-SENSITIVE

Cross-checked via the EAPLS CFP mirror and the EasyChair listing (`easychair.org/cfp/ISSRE2026`); the
official `cyprusconferences.org/issre2026/industry-track/` page returned HTTP 403 to our fetcher, so
**re-confirm on the official page before submitting.**

| Item | Confirmed value |
|---|---|
| Page limit | **6 pages (full) / 4 pages (short) — *including references*** (IEEE CS 2-column; first page counts; over-limit = desk reject) |
| Anonymity | **Not double-blind** — title + author name/affiliation required; abstract ≤150 words; **≤4 keywords** |
| Abstract deadline | **2026-06-28 → extended 2026-07-03** |
| Paper deadline | **2026-07-05 → extended 2026-07-12** |
| Notification | **2026-08-12** |
| Submission | single PDF via **EasyChair** — `easychair.org/conferences?conf=issre2026` |
| GenAI policy | AI-assisted writing allowed **with disclosure**; AI not a listed author — already satisfied |

> ⚠️ **The abstract is due first (today, 2026-06-28, extended to 2026-07-03).** EasyChair "abstract
> submission" registers title + abstract + authors + keywords to hold the slot; the 6-page PDF follows
> by 2026-07-05/07-12. Register the abstract now (block below); upload the paper after typesetting.

**Manuscript status**
- [x] **Full-fidelity manuscript assembled** — `issre_industry_track_paper.md` (Runeson–Höst method as
      §3, four-category threats as §8, GenAI disclosure, ref [15], names kept; numbers frozen at the
      2026-06-11 study cutoff).
- [x] **6-page camera-ready-target derived** — `issre_6page_submission.md`, trimmed to the confirmed
      6-pages-incl-refs limit per the "Trim priorities" appendix (150-word abstract, ≤4 keywords). Paste
      this into IEEEtran; **verify the typeset PDF is ≤6 pages** and apply the in-file next-cut order if
      it runs over.
- [x] **AI disclosure integrated**; **author/system names kept** (not double-blind).
- [ ] **(author)** Paste `issre_6page_submission.md` into the IEEEtran conference template; redraw
      Fig. 1 as a vector figure; run the repo security scan on the final text; confirm ≤6 pages.
- [ ] **(author)** Re-confirm IEEE's current-year GenAI disclosure wording on the publisher policy page.
- [ ] **(author)** Register the abstract on EasyChair **by 2026-07-03**; upload the PDF **by 2026-07-12**.

**EasyChair abstract-registration block (paste verbatim):**

> **Title:** When Errors Become Narratives: A Longitudinal Taxonomy of Silent Failures in a Production
> LLM Agent Runtime
>
> **Author:** Wei Wu — Independent Researcher — wuweinanonuaa@gmail.com
>
> **Keywords (≤4):** software reliability; LLM agent systems; silent failures; fault taxonomy
>
> **Abstract (150 words):** LLM agent systems increasingly run as long-running autonomous runtimes:
> scheduling jobs, calling tools, maintaining memory, messaging humans. We present an eight-week
> longitudinal study of silent failures in one production personal-assistant runtime (~40 scheduled
> jobs, 8 LLM providers, a tool-governance proxy, a knowledge-base memory plane; 4,286 unit tests, 827
> governance checks). We documented 22 incidents with full postmortems; one meta-pattern — an error
> signal that never reaches a human in actionable form — recurred at least 28 times. We derive a
> five-class mechanism taxonomy: (A) environment quirks, (B) design-assumption mismatches, (C) error
> swallowing, (D) chained hallucination, (E) operational omission. Class D is LLM-specific and most
> dangerous: the model transforms errors into fluent, false narrative — we name this fail-plausible.
> Three findings challenge assumptions: ~70% of failures surfaced through human user-view observation,
> not tests; audits showed 0% prevention but 87% regression-blocking; latency (13 hours to 60 days)
> tracks mechanism, not code complexity.

**Pitch line (cover note):** *"A reliability-engineering field study: eight weeks, 22 silent-failure
postmortems from one production LLM agent runtime, a mechanism taxonomy including the LLM-specific
fail-plausible class, and an audited defense framework (0% prevention / 87% regression-blocking)."*

---

## 4. If you want an archival journal credential (slower, higher durability)

**JSS "In Practice" track** (first choice — explicitly accepts Experience Reports) or **EMSE**
(accepts industrial experience reports; higher rigor bar). For either:
- Submit the **full paper** with section **(b)** as the methodology+threats backbone — for a
  journal the analytical-generalization argument is not optional, it is the spine.
- Include the **CRediT statement** (`ai_disclosure_and_acknowledgments.md` §4) and tick the
  submission system's GenAI declaration.
- Expect a revise-and-resubmit cycle; the public repository + data inventory are your strongest
  reviewer-trust asset (reliability validity, §B.4).

---

## 5. Recommended first three moves

1. **This week — IEEE Software.** It is rolling (no deadline), the best magazine fit, and the
   `ieee_software_experience_report.md` draft is submission-ready. Do the two once-only steps (§0),
   paste into the template, submit with the §2 cover note.
2. **In parallel — find the ISSRE Industry Track (or an agent/reliability workshop) CFP** and put
   its deadline on your calendar; prepare the trimmed full paper with section **(b)**. This is your
   highest topical-fit peer-reviewed venue.
3. **Hold the journal (JSS/EMSE) as the durable credential** and submit there only after you have
   the magazine and/or industry-track feedback — it lets you incorporate one review cycle's
   improvements into the slowest, highest-bar version, and avoids tying up the work in a long
   review while faster venues are open.

**Do not** submit the same archival manuscript to two archival venues at once. The safe parallel
pattern is: one practitioner magazine (IEEE Software) + one workshop/industry-track presentation +
later one archival journal — three *different* documents derived from the same study.

---

## 6. Policy items you must verify on official pages (do not take on faith)

| Item | Default assumption (search-sourced) | Verify at |
|---|---|---|
| arXiv preprint blocks later publication? | No for ACM/ICSE SEIP; **confirm IEEE's wording** (stricter on prior/simultaneous) | each venue's author page |
| AI as listed author? | **Prohibited** at IEEE/ACM/USENIX/Springer/Elsevier | publisher policy page |
| AI-assisted writing allowed? | Yes, **with disclosure** | publisher policy page |
| Double-blind? | IEEE Software no; ICSE SEIP no; **FSE Industry yes** (arXiv conflict) | venue CFP |
| GenAI disclosure location | Acknowledgments (IEEE/ACM); dedicated statement + checkbox (journals) | venue CFP / submission system |
| Current deadline | IEEE Software rolling; others per CFP | venue CFP |

*This guide's venue facts come from the search phase of a deep-research run whose adversarial
verification could not complete (account spend limit). They are consistent with established norms
and primary-source URLs, but confirm anything load-bearing on the official page before you submit.*
