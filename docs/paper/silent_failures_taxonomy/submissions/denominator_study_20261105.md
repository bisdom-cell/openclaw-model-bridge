# Denominator Study — discovery channel over *all* production-reached failures in the study window

> **Why this exists.** IEEE Software Reviewer 2 (decision 2026-11-04, SW-2026-06-0312) made the
> sharpest methodological point any reviewer has made about this study: the corpus is defined as
> failures that had a silent phase while automated indicators stayed green, so *by construction*
> nothing the automated stack caught can be in it. "Human observation out-detected the automated
> stack" therefore follows from the inclusion criterion, not from a comparison. Establishing a
> comparison requires the complete population of production failures in the window, including the
> ones automation caught.
>
> This document is that comparison, done on the data the repository already holds. It is a
> **provisional, single-coder pass** (coded by the AI collaborator on 2026-11-05, to be re-coded by
> the author before any number from it enters a submitted manuscript). Every row and its coding is
> in `denominator_coding_20261105.csv` so a second coder can disagree line by line.

## 1. Population and unit

- **Source.** `docs/changelog.md` — every version row dated inside the study window
  **2026-04-09 → 2026-06-02** (the same window as the paper). The changelog is the project's
  append-only record; every production change in the window has a row, and each row's first
  paragraph names what triggered it (the project's postmortem protocol requires it).
- **Unit.** One changelog version row. A row can bundle several changes; it is coded by its
  *primary* trigger. 135 rows fall in the window.
- **Kinds.** Each row is coded into exactly one of:

| kind | meaning | rows |
|---|---|---|
| `corpus` | one of the 22 postmortem-documented silent-failure incidents (the paper's corpus) | 22 |
| `failure` | a production-reached failure that is **not** in the corpus (no standalone postmortem) | 37 |
| `followup` | later work on an incident already counted (e.g. the TCC-sandbox saga has five rows) | 8 |
| `latent` | a defect found by a scanner or code audit before it ever manifested in production | 2 |
| `feature` | new capability, refactor, strategy, user-requested product change — not a failure | 59 |
| `docs` / `dev` | documentation drift or dev-tooling defects that never touched production | 3 / 4 |

"Production-reached" means the defect was present in code or configuration that the Mac Mini was
running or about to run from cron; dev-only tooling and documentation drift are excluded.

## 2. Discovery-channel coding rules

The channel is the **first** signal that caused a human to recognise the failure.

| channel | actor | rule |
|---|---|---|
| `user-view` | human | the operator noticed the failure while *using* the product (reading a pushed digest, a PA reply, an alert's wording, an absence of an expected push) |
| `operator-e2e` | human | the operator found it by running a post-deployment verification on the Mac Mini (smoke test, manual job run, `preflight --full`) — a process step, not product use |
| `log-forensics` | human | the operator found it by reading logs, cache files, status files or the OS audit log, usually while investigating something else |
| `self-observation` | human | found by auditing code or by the governance system auditing itself (dual-run audit, reading a check's source) |
| `auto-check` | automated | a watchdog `[SYSTEM_ALERT]`, a preflight failure/warning, a governance self-check, or a monitor threshold fired and that alert was the first signal |
| `llm-observer` | automated | the daily LLM observer's critique (live from 2026-05-26) named the failure first |

When an automated alert fired *and* the user noticed independently (one row), the row is coded
`auto-check` — i.e. ties go to automation, the conservative direction for this question.

## 3. Results

**TABLE A. Discovery channel, corpus vs. non-corpus vs. combined.**

| channel | corpus (n=22) | non-corpus failures (n=37) | combined (n=59) |
|---|---|---|---|
| user-view | 13 (59%) | 15 (41%) | **28 (47%)** |
| operator-e2e | 0 | 12 (32%) | 12 (20%) |
| log-forensics | 4 (18%) | 4 (11%) | 8 (14%) |
| self-observation | 2 (9%) | 1 (3%) | 3 (5%) |
| auto-check | 3 (14%) | 4 (11%) | 7 (12%) |
| llm-observer | 0 | 1 (3%) | 1 (2%) |
| **human-mediated** | 19 (86%) | 32 (86%) | **51 (86%)** |
| **automated** | 3 (14%) | 5 (14%) | **8 (14%)** |

Three observations, all bounded to this system and this window:

1. **Including the failures automation caught does not reverse the finding; it bounds it.** Across
   all 59 production-reached failures, the runtime automation (watchdog, preflight, governance
   self-checks, monitors, LLM observer) was the first signal for 8 (14%). Humans were the first
   signal for 51 (86%). The corpus's 59% user-view share drops to 47% in the full population, mostly
   because a second *human* channel appears that the corpus could not contain: **post-deployment
   verification by the operator (20%)**. That channel is a process control, not product use, and it
   is where most dev–prod environment mismatches were caught before a user saw them.
2. **The corpus is documentation-selected, not population-complete.** Thirty-seven production-reached
   failures in the same window never received a standalone postmortem, and several of them *did*
   have silent phases (a watchdog that silently aborted for seven days; a daily job that silently
   aborted for five; placeholder digests pushed with `status: ok`). The paper's §3.2 statement that
   the corpus is "the complete population" is true only under criterion (c) — "closed with a complete
   postmortem" — which is an operator decision. The journal version must say so.
3. **There are more fabrication events than the corpus shows.** Three non-corpus rows are Class-D
   fabrications (rows 12, 49, 121 in the CSV: a cross-domain necessity-chain fabrication caught by
   the LLM observer; the "community release v26" fabrication; an earlier "open-source release"
   fabrication). The corpus's four `fail_plausible: yes` incidents are therefore a lower bound.

## 4. What this comparison can and cannot say

- **It cannot measure unit-test prevention.** A defect that `full_regression` blocked before merge
  never becomes a changelog row, so the 4,286 tests' *preventive* contribution is structurally
  invisible here. The fair reading is: *of the failures that reached deployment*, runtime automation
  surfaced 14% first and humans 86%. Claims about tests "catching none" should be dropped, not
  defended.
- **Ties favour automation; the unit favours nothing.** Rows were coded by primary trigger; a row
  that bundles a user-view finding with a feature counts once, as a failure.
- **Single coder.** The same collaborator that helped write the postmortems coded these rows. The
  CSV exposes every coding with a one-line justification; a second coder changing ±5 rows would
  move the automated share by ±8 points and would not move it above one quarter.
- **`operator-e2e` is a human control that a team without a single attentive operator may not have.**
  Its 20% share is the clearest evidence that the human share is partly a property of this
  project's process (mandatory post-deploy verification), not only of human perception.

## 5. Re-running the count

```bash
python3 - <<'PY'
import csv, collections
rows = list(csv.DictReader(open('docs/paper/silent_failures_taxonomy/submissions/denominator_coding_20261105.csv', encoding='utf-8')))
sel = [r for r in rows if r['kind'] in ('corpus', 'failure')]
print(len(rows), 'rows;', len(sel), 'production-reached failures')
for kind in ('corpus', 'failure'):
    c = collections.Counter(r['discovery_channel'] for r in sel if r['kind'] == kind)
    print(kind, dict(c))
print('combined', dict(collections.Counter(r['discovery_channel'] for r in sel)))
PY
```

Rows in the window are re-derivable from `docs/changelog.md` by date; if the changelog is edited,
re-extract before re-coding.
