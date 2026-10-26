# Coverage sweep, 2026-10-26: spin-off restatements on companies we did not pick as examples

**Status: run on 2026-10-26** (results below). The method section was written and committed before any sweep run; the results section is added afterwards in a later commit, so `git log -p` on this file shows that the list, the rule and the scoring did not move after the results were seen.

## Why

The examples so far (GE, GE HealthCare, GE Vernova, 3M → Solventum, Crane NXT) were chosen by us. This sweep asks the same question of companies chosen by a fixed rule, and reports every outcome, failures included.

## Selection rule (fixed before running)

**Spin-off parents:** US-listed companies that completed a spin-off or separation of a business into a **new SEC registrant** between 2019 and 2024, from public knowledge, excluding every company already used as an example (the GE family, 3M/Solventum, Crane NXT). One parent per separation; where a parent did several, the first one in the window. Twelve:

| # | Parent (ticker) | Separated registrant | Completed |
|---|---|---|---|
| 1 | Johnson & Johnson (JNJ) | Kenvue | 2023 |
| 2 | Danaher (DHR) | Veralto | 2023 |
| 3 | Kellanova, formerly Kellogg (K) | WK Kellogg Co | 2023 |
| 4 | IBM (IBM) | Kyndryl | 2021 |
| 5 | Merck & Co. (MRK) | Organon | 2021 |
| 6 | RTX, formerly United Technologies (RTX) | Carrier, Otis | 2020 |
| 7 | Fortive (FTV) | Vontier | 2020 |
| 8 | Bath & Body Works, formerly L Brands (BBWI) | Victoria's Secret & Co. | 2021 |
| 9 | Pfizer (PFE) | Upjohn, combined with Mylan as Viatris | 2020 |
| 10 | DuPont de Nemours (DD) | Dow, Corteva | 2019 |
| 11 | International Paper (IP) | Sylvamo | 2021 |
| 12 | Becton Dickinson (BDX) | Embecta | 2022 |

Considered and left out by the rule: Honeywell → Garrett / Resideo (2018, before the window); Baxter → Vantive (a sale, not a new registrant).

**Controls (not spin-offs), to show the other end states honestly:**

| # | Company (ticker) | Question |
|---|---|---|
| C1 | Kraft Heinz (KHC) | its first suggested question that is not a restatement |
| C2 | Costco (COST) | its first suggested question that is not a restatement |
| C3 | Apple (AAPL) | "What will Apple's revenue be in FY2030?" (a forecast: the scope check must stop it) |

## Procedure (fixed before running)

For each parent, in the order above, one at a time, against the local Zetaris bundle (live mode, every read through Zetaris):

1. **Resolve** the ticker in the entity directory (`GET /entities?q=<ticker>&sample_mode=false`). A ticker that does not resolve is recorded as `not resolved` and stays in the table.
2. **Register** the company if Zetaris has not seen it: one bootstrap run of the neutral question "Why did <ticker>'s Revenues change in FY<year>?" for the separation year. Registration happens before framing, so this run's own outcome is **not** scored; it only makes the company's filings queryable. Its time is recorded separately.
3. **Ask** the entity's own suggestions (`GET /entities/{cik}/suggestions?role=analyst&sample_mode=false`). Take the **first suggestion of kind `restatement`**; if there is none, record `no restatement suggested` and take the first suggestion of any kind. Its `request` is posted unchanged except for the options below.
4. **Run** it: `POST /run` with `options = {hitl_mode: auto, memory: cold, depth: quick}`. Auto gates take the agent's proposal (the source gate registers a discovered registrant); **cold memory**, so an earlier run's lesson cannot help a later one; **quick** = rules only, no model call. Default budgets (`RUN_BUDGET_SECONDS`, `MAX_NEW_SOURCES_PER_RUN`, `MAX_CAUSAL_DEPTH`).
5. **Record** per run: ticker, CIK, the question, `end_state`, whether Loop B found a counterparty registrant and which one, `share_explained` and `residual` of the reconciling link, `sources_onboarded`, `hops`, `queries`, `seconds`, whether the company was already registered (warm) or registered in step 2 (cold), whether any trace line read the offline sample instead of Zetaris (BACKLOG B-26), and any error.
6. Between runs, check Zetaris liveness (`GET /health` → `zetaris`). If Zetaris is down, record it, restart it as `zetaris/README.md` says (`docker compose start`, then warm), and continue; every restart is listed in the results. No Zetaris setting is changed for the sweep.

Controls C1 and C2 follow steps 1–5 with their rule; C3 posts its fixed question directly.

## Scoring (fixed before running)

- **Reconciled** = `end_state` `actionable` with a Loop B add-back whose residual is within the critic's tolerance (5% of the effect). This is our own definition, not an external ground truth.
- **Right counterparty** = the registrant Loop B found is the separated registrant in the table above (checked by name and CIK).
- **Spot-check**: for three reconciled runs, the counterparty's revenue figure is read from the trace rows and compared with that registrant's own 10-K figure for the same period, by hand; the result is written here.
- Every company appears in the results, including the ones that do not resolve, error, or end at `evidence-frontier` / `external-driver` / a scope stop.

## Results

Run on 2026-10-26 between 18:15 and 18:40 AEDT, main `f7d7880` plus `scripts/coverage_sweep.py`, live mode against the local Zetaris bundle (driver heap 4 GB), depth `quick` (rules only, no model call), auto gates, cold memory. Raw results, one line per company: [`coverage-sweep-2026-10-26.jsonl`](coverage-sweep-2026-10-26.jsonl) (no trace content; traces are kept outside the repository). **Zetaris stayed alive before and after every run (no restart). No run read the offline sample** (0 sample-mode trace lines in every live run, BACKLOG B-26).

Deviation from the procedure: IBM was run first, as the script's trial, instead of fourth; it followed the procedure otherwise, and its result is kept as its scored row. The other companies ran in the listed order, appended to the same file (`--append`).

### Spin-off parents (12)

| # | Parent | Question the rule picked (first restatement suggestion) | End state | Counterparty Loop B found | Share explained / residual | Scored run | Score |
|---|---|---|---|---|---|---|---|
| 1 | JNJ | FY2022 revenue, 10-K filed 2023-02-16 ($93.78B) vs 2024-02-16 ($78.74B) | actionable | none (Loop A only) | 100% / 0%, asserted, not measured (B-30) | 4.4 s | actionable, **not reconciled** |
| 2 | DHR | FY2022 revenue, $31.47B vs $26.64B | evidence-frontier | none: asks for a segment breakdown | — | 4.7 s | frontier; Veralto **not found** |
| 3 | K | — | not resolved: `K` was not in the SEC ticker list at run time | — | — | — | not resolved |
| 4 | IBM | FY2019 revenue, $77.15B vs $57.71B | actionable | **Kyndryl Holdings** (CIK 0001867072) | 104% / −4.35% (over-explained by $846M) | 13.5 s | **reconciled**, right counterparty |
| 5 | MRK | FY2019 revenue, $46.84B vs $39.12B | actionable | none (Loop A only; cites the 8-K filed 2021-06-03) | 100% / 0%, asserted, not measured (B-30) | 7.9 s | actionable, **not reconciled** |
| 6 | RTX | FY2018 revenue, $66.50B vs $34.70B | evidence-frontier | nVent Electric (**wrong**: a Pentair spin-off), registered then rejected (B-31) | — | 30.4 s | frontier; Carrier/Otis **not found** |
| 7 | FTV | FY2023 revenue, $6.07B vs $3.91B | actionable | **Ralliant Corp** (CIK 0002041385) | 100% / −0.2% | 22.3 s | **reconciled**; a 2025 separation, not the listed Vontier (2020) |
| 8 | BBWI | FY2020 revenue, $12.91B vs $5.41B | actionable | **Victoria's Secret & Co.** (CIK 0001856437) | 100% / 0.0% | 18.6 s | **reconciled**, right counterparty |
| 9 | PFE | FY2023 revenue, $50.91B vs $59.55B | evidence-frontier | none: no disposal filing in the window | — | 3.3 s | frontier (the question is not the Upjohn separation) |
| 10 | DD | FY2024 revenue, $12.39B vs $6.72B | actionable | **Qnity Electronics** (CIK 0002058873) | 76% / 23.5% unexplained | 20.8 s | actionable, **not reconciled** (residual above the 5% tolerance); a 2025 separation, not the listed Dow/Corteva |
| 11 | IP | FY2023 revenue, $18.92B vs $16.03B | evidence-frontier | "IP STRATEGY HOLDINGS" (**wrong**, brand match), registered then rejected (B-31) | — | 20.3 s | frontier |
| 12 | BDX | FY2021 revenue, $20.25B vs $19.13B | evidence-frontier | none: asks for a segment breakdown | — | 2.2 s | frontier; Embecta **not found** |

**The rule picked the most recent restatement, not always the separation in the table.** For FTV and DD the restatement behind the picked question is a later separation (Ralliant, Qnity) that Loop B identified correctly by filing evidence; for PFE the picked question is a different change. The table scores what the run did with the question the rule picked.

### Controls (3)

| # | Company | Question | End state | Note |
|---|---|---|---|---|
| C1 | KHC | first non-restatement suggestion: net income FY2013 $2.71B → FY2014 $657.0M | evidence-frontier | asks for a source the registered filings do not have; no cause asserted |
| C2 | COST | — | no question | registered (17.2 s), but the suggester found no question to ask |
| C3 | AAPL | "What will Apple's revenue be in FY2030?" | scope stop (`in_scope: false`) | refused in 4.3 s with a reason and alternatives |

### Summary (spin-off parents, N = 12)

| Outcome | Count | Companies |
|---|---|---|
| Reconciled within tolerance, right counterparty for the question asked | **3** | IBM (Kyndryl), BBWI (Victoria's Secret), FTV (Ralliant) |
| Actionable, right counterparty, not reconciled (residual 23.5%) | 1 | DD (Qnity) |
| Actionable from Loop A only, share asserted (B-30) | 2 | JNJ, MRK |
| Evidence-frontier, no cause asserted | 5 | DHR, RTX, PFE, IP, BDX |
| Not resolved | 1 | K |
| **Wrong answer accepted** | **0** | (two wrong counterparties were registered and rejected by the critic: RTX, IP, B-31) |

- **Time per scored run** (11 runs, excluding the registration run): median **13.5 s**, p90 22.3 s (nearest rank), max 30.4 s.
- **Registration run** for a company Zetaris had not seen (13 companies): median 27.2 s, max 55.6 s. So a first question about a new company takes about 30–80 s in total; a second takes the scored time.
- **Residual of the reconciled runs:** median |residual| 0.2% (IBM 4.35%, FTV 0.2%, BBWI 0.0%).

### Spot-check of the three reconciled add-backs

Each add-back figure is the counterparty's own XBRL fact, read through Zetaris from its own 10-K (form and filing date are on the trace line): Kyndryl FY2019 revenue $20.28B (10-K filed 2022-03-10), Victoria's Secret fiscal 2019 (year to 2020-02-01) $7.51B (10-K filed 2022-03-18), Ralliant FY2023 $2.16B (10-K filed 2026-02-26). The period matches the parent's restated period in all three. A comparison with each filing's printed income statement was **not** made in this session.

### What this does and does not show

- It shows that, on spin-off parents chosen by a rule rather than by us, the agent reconciled 3 of 12 within tolerance with the right counterparty found from filing evidence, stopped without asserting a cause on 5, and accepted **no wrong reconciliation**; it also shows two weaknesses, both deferred: an unmeasured Loop A acceptance on 2 of 12 (B-30) and a wrong counterparty registered (then rejected) on 2 of 12 (B-31).
- N is small (12 + 3 controls), one run each, one day, one Zetaris bundle; spin-offs only; XBRL company facts only (no segment data, no narrative text: DHR and BDX stopped on that).
- "Reconciled" is our own scoring (add-back within the critic's 5% tolerance), not an external ground truth, and the spot-check checks source and period, not the printed statements.
- Depth `quick` only: the Nemotron critic (depth `thorough`) was not part of this sweep.
