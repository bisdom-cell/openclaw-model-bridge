# Generative-AI Disclosure, Acknowledgments, and Author Contributions (drop-in)

> **Purpose.** Venue-compliant disclosure text for the AI-assisted nature of this paper. Drop the
> relevant blocks into the submission. The governing constraint across IEEE, ACM, and USENIX
> (2025–2026) is the same: **a generative-AI system may not be a listed author; AI-assisted
> writing is permitted if disclosed; the human author is fully responsible for all content.** The
> blocks below are written to that constraint and place Wei Wu as sole author.
>
> **Why disclose prominently rather than minimally.** This paper's §7 already treats
> "AI-assisted operation of an AI system" as one of its findings. Disclosure is therefore part of
> the contribution, not a liability to bury. An honest, specific disclosure also pre-empts the
> single most likely reviewer objection ("was this written by the tool it studies?") by answering
> it directly with a mechanically checkable evidence trail.

---

## 1. Author block (title page)

> **Wei Wu**
> Independent Researcher
> wuweinanonuaa@gmail.com
>
> *No institutional affiliation. This work was conducted independently.*

Single human author. **Do not list Claude / Anthropic / any AI system in the author block** — this
is prohibited by IEEE and ACM author policies and is the safest default everywhere.

---

## 2. Generative-AI Use Disclosure (dedicated statement)

Use this as a standalone "Generative AI Disclosure" section (many 2025–2026 venues request one) or
fold it into Acknowledgments where no dedicated section exists.

> **Generative-AI Use Disclosure.** This research used Anthropic's Claude (a large language model),
> accessed through a coding-agent interface, as an engineering and writing collaborator. Claude was
> used to: assist in triaging and reconstructing incident postmortems; extract and tabulate
> quantitative data from the public source repository; draft and edit prose; and propose document
> structure. Claude was *not* used to generate empirical results, invent data, or make research
> claims: every quantitative figure reported in this paper (incident counts, test and governance
> counts, dates, percentages) is mechanically derivable from the public repository at the cited
> commit window, and was verified by the author against that repository. The author defined all
> research questions, performed all incident classification, drew all conclusions, and takes full
> responsibility for the entire content of the paper, including any errors. No generative-AI system
> is an author of this work.
>
> A second, distinct point of potential confusion is disclosed for clarity: the *runtime system
> under study* is itself an LLM agent system (powered by separate Qwen3-class models). The
> AI collaborator described above (Claude) is the author's development tool; it is not the system
> being studied. The relationship between the two is itself examined as a finding in Section 7.

---

## 3. Acknowledgments

> **Acknowledgments.** I thank the maintainers of the open-source agent framework on which the
> studied system is built, and the open scientific-literature infrastructure (arXiv, Semantic
> Scholar, DBLP) that the runtime depends on. I disclose, as described in the Generative-AI Use
> Disclosure, the use of Anthropic's Claude as an engineering and writing collaborator throughout
> the construction of the studied system and the preparation of this paper; the collaboration is
> itself part of the paper's subject matter (Section 7). All conclusions, and all responsibility
> for them, are mine.

---

## 4. Author Contributions (CRediT-style, optional but recommended)

Several venues (and most journals: EMSE, JSS) accept or request a CRediT contributions statement.
This both satisfies the requirement and makes the human/AI division explicit and honest.

> **Author Contributions.** **Wei Wu** — Conceptualization, Methodology, Investigation (operation
> of the production system; incident postmortems), Formal analysis (taxonomy construction, validity
> analysis), Data curation, Writing – original draft, Writing – review & editing, Software (the
> studied system and its governance engine). Sole author and guarantor.
>
> **AI-assistance statement (non-author).** Anthropic's Claude provided tool-assisted support for
> postmortem drafting, repository data extraction, prose drafting/editing, and document
> structuring, under the author's direction and verification. As a generative-AI system it does not
> meet authorship criteria and is not credited as an author; its use is disclosed above in
> accordance with publisher policy.

---

## 5. Per-venue adaptation notes

| Venue | AI as author? | Required disclosure form | Where to put it |
|---|---|---|---|
| **IEEE Software / IEEE venues** | Prohibited | Disclose AI use; author responsible for content | Acknowledgments + a sentence in the author footnote |
| **ACM venues (CACM, Queue, ACM confs/SEIP via ACM)** | Prohibited | Full disclosure **in Acknowledgments**, naming the tool and how it was used | Acknowledgments section (use §2 + §3) |
| **USENIX (;login:, SREcon write-ups)** | Prohibited | Disclose; human accountable | Acknowledgments / author note |
| **EMSE, JSS (Springer/Elsevier journals)** | Prohibited | Disclosure statement **and** (often) a declaration in the submission system; CRediT statement accepted | Dedicated "Generative-AI Use" statement (§2) + CRediT (§4) + tick the submission-system declaration |
| **ISSRE / ICSE SEIP / FSE Industry (IEEE/ACM)** | Prohibited | Disclose per the sponsoring publisher (IEEE or ACM) above | Acknowledgments; confirm the year's specific CFP wording |
| **arXiv (already posted)** | No prohibition, but academic norm is non-author | Footnote disclosure (already in arXiv:2606.14589) | Title-page footnote + Acknowledgments |

**Action before any submission:** open the target venue's *current-year* author guidelines /
GenAI policy page and confirm two things — (1) the exact required *location* of the disclosure
(acknowledgments vs a dedicated section vs a submission-system checkbox), and (2) whether the
year's policy has tightened. The blocks above satisfy the strictest common denominator
(IEEE+ACM), so adaptation is normally a matter of placement, not rewriting.

---

## 6. One-paragraph version (for tight magazine/letter formats)

When space is scarce (e.g., the IEEE Software article footnote), collapse to:

> *This article and the underlying postmortems were prepared with the assistance of Anthropic's
> Claude, used as an engineering and writing collaborator. Wei Wu is the sole author, verified
> every reported figure against the public repository, and is solely responsible for the content;
> no AI system is an author.*
