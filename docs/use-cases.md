# Use cases: how an analyst uses Tiresias day to day

Who the users are and why they need this: [problem-and-users.md](problem-and-users.md). This page is about the **workflow**: what happens on an ordinary working day, which screens a UI needs, and which stories the demo and deck tell. Every behaviour below is marked **works today**, **in progress** or **planned**; a measured number cites its source.

## The working loop

| Step | What the analyst does | What Tiresias does | Status |
|---|---|---|---|
| 1. Notice | A model stops reconciling after a new 10-K: last year's revenue is not what it was. Or a monthly sweep of the coverage list. | — | — |
| 2. Pick a company | Types a ticker, CIK or name. | `GET /entities?q=…`: companies ready now, and (live) any SEC registrant, each with its expected first-run wait. | works today |
| 3. Pick a question | Takes a suggestion or writes one, and says who is asking (analyst, auditor, board). | `GET /entities/{cik}/suggestions`: restatements, the largest year-on-year changes and extremes found in the company's own filings, each checked by the framer and ready to post. A forecast or a non-SEC company is stopped with a reason and runnable alternatives. | works today |
| 4. Choose depth | Quick, or a slower and more thorough look. | Shows the expected wait before starting. Quick = rules only; thorough adds the model critic's second opinion; deep adds model hypotheses and wording (Nemotron through Meterless Nightshift). | rules: works today; depth choice: in progress |
| 5. Watch | Watches the why-tree build. | `GET /runs/{id}/events` streams every step: phase start, heartbeat, done; each query, hypothesis, critic verdict and gate. | works today (API); UI in progress |
| 6. Decide at a gate | Approves or denies, e.g. "Register the spun-off company's filings as a new source?" | The source gate and the critic-escalation gate pause for a person in interactive mode; judges' runs use `auto`. | gates and modes work; answering a gate over HTTP: planned |
| 7. Act | Reads the decision first, then the evidence. | `end_state` and the recommended `action` for this asker, then every link's SQL, rows, share explained and the residual left over. | works today |
| 8. Follow up | Asks again as an auditor, or digs under one node. | Same question, different asker stops at a different cause; branching from a node of a finished run. | asker profiles work; branching planned |
| 9. Next time | — | Lessons (not answers) from past runs shorten the next similar investigation; memory can be purged. | planned (H-MEM service works; the agent does not use it yet) |

## Where it fits in an analyst's week

- **Results season (the main case).** A company files its 10-K and the history in the analyst's model no longer matches: a spin-off or a sale restated the past. The analyst needs the add-back that makes the series like-for-like, and evidence to cite. Tiresias's restatement path does exactly this.
- **Coverage sweep.** Once a month, run the suggestions for each company on the coverage list. Restatements and outsized changes surface on their own; only the interesting ones get a full investigation.
- **Credit or audit review.** A large impairment or a non-reliance filing (8-K item 4.02) changes how far the numbers can be trusted. The asker's role (analyst, auditor, board) and levers decide which cause the investigation stops at.
- **Before a meeting.** Five minutes to answer "why did this number move?" with the filing evidence, instead of an afternoon in EDGAR.

## Demo and deck stories

Smaller, less famous companies make the point better than a household name: the analyst covering them has no press coverage to lean on.

| Story | Company | What it shows | Measured |
|---|---|---|---|
| **Spin-off restatement** | **Crane NXT** (FY2022 revenue restated) | The original 10-K says $3.37B; a year later $1.34B. Loop A finds the separation; Loop B finds **Crane Co** from 13 Form 10 documents that name the parent, registers its filings in Zetaris behind the source gate, and reconciles: $1.34B + $2.04B = $3.37B, 100% explained. | live, 2026-10-23, `actionable` ([live-any-company.md](measurements/live-any-company.md), B-5 section) |
| **Same question, two askers** | any restatement | The analyst stops at "re-base the model"; the auditor at "request the carve-out schedule". | asker levers work today |
| **Honest refusal** | any | "What will revenue be next year?" is refused with a reason and up to three questions that can be answered from filings. | works today |
| **The model is challenged** | Crane NXT or GE at depth *deep* | Nemotron, through Nightshift, gives the critic a second opinion; when it disagrees, the gate keeps the rule verdict and the trace shows both. | measured once on GE case 01 (2026-10-24): one disagreement, rule verdict kept |
| **Impairment** | GE FY2018 | A net loss traced to a $22.1B goodwill impairment. A smaller-company impairment is still to be chosen at rehearsal. | sample and live |
| **Memory** | two similar spin-offs | Cold run, warm run with fewer steps, purge, cold again. | planned |

**Decision needed for the no-key demo.** Sample mode, which judges run without keys, holds only the GE family ([data/sample/](../data/sample/)). To make Crane NXT the no-key demo too, add Crane NXT and Crane Co to the slice with `scripts/fetch_sample.py` (it reads only SEC's public endpoints and records provenance) and add an input example. Live mode already handles it.

## Screens a UI needs (for the UI work)

1. **Find**: one search box over `GET /entities`, with the expected wait per company; ready companies first.
2. **Ask**: suggested questions as cards; a free-form question; asker role; depth with its expected wait.
3. **Investigate**: the why-tree growing from `GET /runs/{id}/events` (a report that builds itself, not a chat); phase spinners from `phase_start` / `heartbeat` / `phase_done`; the critic's rejections and retries visible; a gate dialog when one is waiting.
4. **Result**: decision first (`end_state`, `action`), then the evidence panel per link (SQL, rows, share, label, the model's opinion when there is one), the residual, and the trace file.
5. **Follow up**: ask as another role; later, branch from a node.

## Out of scope (said plainly)

- Narrative text (MD&A, press releases) is not read; a cause that needs it ends at `evidence-frontier` with where to look.
- No segment data in XBRL company facts.
- Not investment advice; it supports an analyst's own review.
- Full list: [limitations.md](limitations.md).
