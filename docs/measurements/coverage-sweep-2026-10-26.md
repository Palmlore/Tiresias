# Coverage sweep, 2026-10-26: spin-off restatements on companies we did not pick as examples

**Status: pre-registered.** This section was written and committed before any sweep run; the results section is added afterwards in a later commit, so `git log -p` on this file shows that the list, the rule and the scoring did not move after the results were seen.

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

Not run yet.
