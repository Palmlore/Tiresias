# Live latency: any company, through Zetaris

> Update: the scout change that closes BACKLOG B-5 (counterparty from filing evidence) was measured the same evening; see [the last section](#2026-10-23-evening-source-scout-by-filing-evidence-b-5). It changes the end states of IBM, 3M and Crane NXT below.

Measured **2026-10-23** with `scripts/perf_smoke.py` (live mode, `run_investigation`, real Zetaris, real SEC data fetched by Zetaris). Every number below comes from the run's own trace (`orchestrator` `phase_done` lines); nothing is estimated. Raw JSON lines and traces stayed in a scratch directory (not committed: rule 1, no replayed outputs).

## Setup

| | |
|---|---|
| Machine | Apple M5, 10 cores, 24 GB, macOS 27.0.1; Docker 29.9.2 |
| Zetaris | local bundle `2.4.3-v2.4.3.1-hackathon` (images `zetaris-ndp-api`, `zetaris-ndp-driver`, `zetaris-new-ui`), REST `http://localhost:8888/api/v1.0`, not restarted during the session |
| Agent | Python 3.11.15, uv 0.9.26; branch `feat/any-company`; pass 1 at commit `7459bc1`, pass 2 at `2fa8af1` (the code in this PR except docs) |
| Budgets | defaults: `MAX_NEW_SOURCES_PER_RUN=2`, `RUN_BUDGET_SECONDS=600`, `SOURCE_TIMEOUT_SECONDS=20`, `MAX_RETRIES=2`, `PHASE_HEARTBEAT_SECONDS=5`, gates `auto` |
| Network | home broadband; Zetaris fetches `data.sec.gov` with `SEC_USER_AGENT` (masked in every trace line) |

**Cold** = first run for an entity Zetaris had never registered (the run registers it and Zetaris fetches its SEC files for the first time). **Warm** = tables already registered and cached. The showcase entities (GE, GEHC, GEV) were registered and cached before this session, so their runs are warm by construction; a truly cold showcase run needs a Zetaris restart, which was not done.

Columns are seconds. *Register* = `register_entity` (asked entity, only when Zetaris lacks it). *First query* = the first evidence query (the framer's concept series). *Frame* is inside *Loop A*; *Scout*, *Onboard*, *Reconcile* are inside *Loop B*. *Max silence* = longest gap between two trace lines (what a UI must cover; heartbeats cap it at 5 s). *Sources* = `stats.sources_onboarded`.

## Pass 2: the code in this PR

| Case | Run | Resolve | Register | First query | Frame | Loop A | Scout | Onboard | Reconcile | Loop B | Total | Max silence | Sources | End state |
|---|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|---|
| s6_cat_2023_revenue_change | cold | 0.6 | 15.6 | 1.4 | 1.5 | 3.0 | 0.0 | - | - | 0.0 | 19.3 | 5.0 | 1 | evidence-frontier |
| s6_cat_2023_revenue_change | warm | 0.2 | - | 1.3 | 1.3 | 2.9 | 0.0 | - | - | 0.0 | 3.1 | 1.3 | 0 | evidence-frontier |
| s7_cxt_2022_revenue_restated | cold | 0.4 | 12.7 | 1.5 | 1.5 | 5.3 | 2.5 | - | - | 2.5 | 21.0 | 5.0 | 1 | evidence-frontier |
| s7_cxt_2022_revenue_restated | warm | 0.2 | - | 1.7 | 1.7 | 5.1 | 0.5 | - | - | 0.5 | 5.8 | 1.6 | 0 | evidence-frontier |
| 01_ge_2022_revenue_restated | run 1 (registered) | 0.1 | - | 2.6 | 2.6 | 7.2 | 1.2 | 1.3 | 1.1 | 3.5 | 10.9 | 2.6 | 1 | actionable |
| 01_ge_2022_revenue_restated | run 2 | 0.2 | - | 2.3 | 2.3 | 7.1 | 0.9 | 1.3 | 1.0 | 3.3 | 10.6 | 2.3 | 1 | actionable |
| 02_ge_2023_revenue_restated | run 1 (registered) | 0.2 | - | 2.3 | 2.3 | 6.8 | 0.5 | 1.2 | 0.9 | 2.6 | 9.7 | 2.3 | 1 | actionable |
| 02_ge_2023_revenue_restated | run 2 | 0.3 | - | 2.0 | 2.0 | 6.6 | 0.5 | 1.6 | 0.6 | 2.8 | 9.7 | 2.0 | 1 | actionable |
| 03_ge_2018_net_loss | run 1 (registered) | 0.1 | - | 1.7 | 1.7 | 9.5 | - | - | - | - | 9.7 | 2.0 | 0 | actionable |
| 03_ge_2018_net_loss | run 2 | 0.3 | - | 1.8 | 1.8 | 9.6 | - | - | - | - | 9.9 | 2.1 | 0 | actionable |
| s1_ibm_2020_revenue_restated | run 1 (registered) | 0.3 | - | 1.5 | 1.5 | 3.6 | 0.3 | - | - | 0.3 | 4.2 | 1.6 | 0 | evidence-frontier |
| s1_ibm_2020_revenue_restated | run 2 | 0.3 | - | 1.2 | 1.2 | 3.4 | 0.3 | - | - | 0.3 | 4.0 | 1.3 | 0 | evidence-frontier |
| s2_jnj_2022_revenue_restated | run 1 (registered) | 0.1 | - | 1.8 | 1.8 | 4.4 | - | - | - | - | 4.5 | 1.9 | 0 | actionable |
| s2_jnj_2022_revenue_restated | run 2 | 0.2 | - | 1.6 | 1.6 | 3.9 | - | - | - | - | 4.1 | 1.6 | 0 | actionable |
| s3b_mmm_2023_revenue_restated | run 1 (registered) | 0.2 | - | 2.2 | 2.2 | 4.4 | 0.3 | - | - | 0.3 | 4.8 | 2.2 | 0 | evidence-frontier |
| s3b_mmm_2023_revenue_restated | run 2 | 0.3 | - | 1.6 | 1.6 | 4.2 | 0.3 | - | - | 0.3 | 4.7 | 1.6 | 0 | evidence-frontier |
| s4_xom_2023_revenue_change | run 1 (registered) | 0.1 | - | 0.2 | 0.2 | 0.2 | - | - | - | - | 0.4 | 0.2 | 0 | evidence-frontier |
| s4_xom_2023_revenue_change | run 2 | 0.1 | - | 0.2 | 0.2 | 0.2 | - | - | - | - | 0.3 | 0.2 | 0 | evidence-frontier |
| s5b_hilton_2016_revenue_restated | run 1 (HLT registered, HGV cold) | 0.2 | - | 1.6 | 1.6 | 3.3 | 2.0 | 8.1 | 1.3 | 11.4 | 14.9 | 2.8 | 1 | actionable |
| s5b_hilton_2016_revenue_restated | run 2 | 0.5 | - | 1.5 | 1.5 | 3.6 | 0.4 | 1.2 | 1.4 | 3.0 | 7.1 | 1.5 | 1 | actionable |

## Pass 1: first contact (cold for IBM, JNJ, MMM, XOM, HLT), before two fixes

| Case | Run | Resolve | Register | First query | Frame | Loop A | Scout | Onboard | Reconcile | Loop B | Total | Max silence | Sources | End state |
|---|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|---|
| 01_ge_2022_revenue_restated | run 1 (registered) | 0.3 | - | 2.2 | 2.2 | 6.9 | 1.6 | 1.2 | 0.6 | 3.4 | 10.6 | 2.2 | 1 | actionable |
| 01_ge_2022_revenue_restated | run 2 | 0.1 | - | 2.0 | 2.0 | 6.0 | 1.6 | 1.1 | 0.7 | 3.4 | 9.4 | 2.0 | 1 | actionable |
| 02_ge_2023_revenue_restated | run 1 (registered) | 0.1 | - | 1.8 | 1.8 | 5.8 | 1.2 | 1.1 | 0.5 | 2.8 | 8.8 | 1.8 | 1 | actionable |
| 02_ge_2023_revenue_restated | run 2 | 0.1 | - | 2.1 | 2.1 | 5.9 | 1.7 | 1.0 | 0.9 | 3.6 | 9.6 | 2.1 | 1 | actionable |
| 03_ge_2018_net_loss | run 1 (registered) | 0.2 | - | 1.2 | 1.2 | 8.0 | - | - | - | - | 8.3 | 1.7 | 0 | actionable |
| 03_ge_2018_net_loss | run 2 | 0.1 | - | 1.8 | 1.8 | 8.3 | - | - | - | - | 8.5 | 1.8 | 0 | actionable |
| s1_ibm_2020_revenue_restated | cold | 0.3 | 14.9 | 1.2 | 1.6 | 3.4 | 10.1 | 4.2 | 0.1 | 14.4 | 33.0 | 5.0 | 2 | evidence-frontier |
| s1_ibm_2020_revenue_restated | warm | 0.1 | - | 1.1 | 1.1 | 2.8 | 1.3 | 0.6 | 0.0 | 2.0 | 4.9 | 1.4 | 1 | evidence-frontier |
| s2_jnj_2022_revenue_restated | cold | 0.2 | 13.3 | 1.5 | 1.5 | 3.5 | - | - | - | - | 17.0 | 5.0 | 1 | actionable |
| s2_jnj_2022_revenue_restated | warm | 0.2 | - | 1.6 | 1.6 | 3.4 | - | - | - | - | 3.6 | 1.6 | 0 | actionable |
| s3_mmm_2023_revenue_restated | cold | 0.5 | 14.5 | 1.6 | 1.7 | 4.1 | 0.0 | - | - | 0.0 | 19.2 | 5.0 | 1 | evidence-frontier |
| s3_mmm_2023_revenue_restated | warm | 0.3 | - | 1.8 | 1.8 | 4.5 | 0.0 | - | - | 0.0 | 4.8 | 2.1 | 0 | evidence-frontier |
| s4_xom_2023_revenue_change | cold | 0.5 | 4.6 | 0.4 | 0.4 | 0.4 | - | - | - | - | 5.4 | 2.3 | 1 | evidence-frontier |
| s4_xom_2023_revenue_change | warm | 0.1 | - | 0.2 | 0.2 | 0.2 | - | - | - | - | 0.3 | 0.2 | 0 | evidence-frontier |
| s5_hilton_2015_revenue_restated | cold | 0.2 | 11.8 | 1.4 | 1.5 | 1.5 | - | - | - | - | 13.5 | 4.8 | 1 | error: NoEffect |
| s5_hilton_2015_revenue_restated | warm | 0.3 | 2.0 | 1.2 | 1.5 | 1.5 | - | - | - | - | 3.9 | 1.2 | 1 | error: NoEffect |

Pass 1 found three problems that pass 2 fixes or works around:
1. **IBM, scout false positive (fixed).** IBM's title starts with "International", so the brand pre-filter also matched *International Media Acquisition Corp.*, a blank-check company that happened to file an 8-K within 7 days of IBM's November 2021 disposal. The scout registered it (10.1 s scout + 4.2 s onboard, 2 sources) and the critic then refused it (no revenue for 2020), so the run still ended honestly at `evidence-frontier`. Fix: generic first words (`knowledge.GENERIC_NAME_WORDS`: international, general, american, …) are never a brand word. In pass 2 IBM's scout finds no same-brand registrant in 0.3 s (Kyndryl has a new brand, BACKLOG B-5).
2. **MMM, wrong filing date in my request (input fixed).** I wrote "10-K filed 2025-02-06"; the framer found no 10-K on or up to 31 days after that date, so it framed a year-on-year change instead (and said so in the evidence note). `s3b` uses an earlier date and frames the restatement.
3. **Hilton FY2015, no effect.** Hilton's FY2015 revenue is the same in its 2016 and 2017 10-Ks (the January 2017 spin-offs are first restated in the 2018 10-K), so the run ended `error: NoEffect` ("equals its baseline: nothing to explain"). `s5b` asks about FY2016, which the 2018 10-K restates.

Pass 2 also adds `RevenueFromContractWithCustomerIncludingAssessedTax` to the revenue aliases and reuses an entity already registered when it is asked by name (the pass-1 Hilton warm run re-registered it: 2.0 s, one source).

## End state of each case (pass 2)

| Case | Asked | End state | Why |
|---|---|---|---|
| 01, 02 (GE) | ticker GE | `actionable` | Loop B through Zetaris discovery finds GE HealthCare / GE Vernova and reconciles the restatement within 5% (as in sample mode). |
| 03 (GE) | ticker GE | `actionable` | Goodwill impairment, Loop A only. |
| s1 IBM FY2020 restated | ticker IBM | `evidence-frontier` | Loop A finds the 8-K 2.01 disposal (Kyndryl, Nov 2021) but cannot measure it; the scout finds no other registrant carrying IBM's name (Kyndryl is a new brand, BACKLOG B-5). |
| s2 JNJ FY2022 restated | ticker JNJ | `actionable` | Loop A accepts the 8-K 2.01 disposal (Kenvue) in the window as the cause: resetting it moves revenue back to $94.94B, 100% of the -$14.95B effect. Loop B not needed. |
| s3b MMM FY2023 restated | ticker MMM | `evidence-frontier` | The disposal (Solventum, 2024) is found; Solventum carries a new brand, so the scout refuses (B-5). |
| s4 XOM FY2023 change (plain) | ticker XOM | `evidence-frontier` | `MetricNotFound`: ExxonMobil's companyfacts holds only 4 recent `Revenues` facts and none under the two contract-revenue aliases; its total revenue sits under a concept outside the alias list (BACKLOG B-11). |
| s6 CAT FY2023 change (plain) | ticker CAT | `evidence-frontier` | No rule explains a plain year-on-year change further (no disposal, impairment or restated comparative); rule set is fixed (BACKLOG B-2). |
| s7 CXT FY2022 restated | ticker CXT | `evidence-frontier` | Crane's 2023 separation: the scout finds *Crane Co* (CIK 0001944013, change-in-control 8-K on 2023-04-03) but refuses it because that CIK filed the FY2022 10-K before the separation (a holding-company reorganisation, not a new registrant). |
| s5b HLT FY2016 restated | name "Hilton Worldwide Holdings Inc." | `actionable` | Resolved by name through `tiresias.sec_tickers`; Loop B finds *Hilton Grand Vacations* (8-K 2017-01-04) and registers it cold (8.1 s). It explains 37% of the -$4.28B restatement; 63% stays as the residual (Park Hotels & Resorts, a different brand, B-5). |

No run in either pass had a `degraded` line (Zetaris was up throughout), and no run errored except pass-1 Hilton FY2015 (`NoEffect`, a correct answer to a question with no effect).

## What a UI should expect

- **Unknown company, cold:** 15–21 s end to end for a full investigation (pass 2: CAT 19.3 s, CXT 21.0 s; pass 1: 13.5–33.0 s, and 5.4 s for XOM, which stops right after framing). Most of it is `register_entity` (11.8–15.6 s; XOM 4.6 s), and most of that is one statement, the companyfacts `CACHE TABLE` (7.0–12.2 s; XOM 2.3 s) while Zetaris downloads the SEC file.
- **Same company again (warm):** 0.3–11 s (3–11 s for a full investigation); no registration, the first query is 0.2–2.6 s, each later evidence query about 1–2 s.
- **Loop B cold** (a new candidate or disposed business): scout 2.0–2.5 s with new candidate tables (10.1 s for 10 candidates before the fix), onboard 8.1 s (new companyfacts) or 1.0–1.6 s (already registered).
- **Silences:** the longest gap between trace lines is 5.0 s during registration (the heartbeat interval) and ≤ 2.8 s otherwise.

Reproduce (needs a running Zetaris and `ZETARIS_API_KEY`, `SEC_USER_AGENT` in `.env`; a second run of the same request is warm):

```bash
cd services/agent && uv run python ../../scripts/perf_smoke.py ../../input_examples/*.json path/to/your_request.json --runs 2
```

## 2026-10-23 (evening): source scout by filing evidence (B-5)

Same machine, same Zetaris bundle (not restarted). Branch `feat/spinoff-match`, pass 3 ran at commit `a54daec` (the code in this PR; the fixes listed under pass A are in it). Defaults as above, gates `auto`. Every number below comes from the run's own trace; raw JSON lines and traces stayed in a scratch directory (not committed).

**What changed:** the scout now asks EDGAR full-text search (catalog `sec_fulltext`, one Zetaris REST table per search) which filers' Form 10 (10-12B/10-12G) in the 540 days before the disposal contains the source's name, and checks those first, whatever their name; the brand only ranks. If nothing qualifies it searches 8-Ks within 7 days of the disposal for a buyer and refuses a sale to an existing company with the buyer named.

**Inputs.** IBM's dates (`10-K filed 2021-02-23` / `2022-02-22`) match IBM's submissions. The earlier 3M input `s3b` had dates written from memory (`2024-02-01`, `2025-01-15`); 3M's 10-Ks were filed **2024-02-07** and **2025-02-05**, so this pass uses `s3c` with those dates. Hilton (`2017-02-01` / `2018-02-01`; actual 2017-02-15 / 2018-02-14) and Crane NXT (`2023-02-01` / `2024-02-01`; actual 2023-03-01 / 2024-02-22) were left as written: the framer takes the first 10-K on or up to 31 days after the given date, which is the actual one in each case. New: `s8_dd_2020_revenue_restated` (DuPont FY2020, `10-K filed 2021-02-12` / `2022-02-11`, both actual), whose disposal (8-K item 2.01 filed 2021-02-04) is the merger of its Nutrition & Biosciences business into International Flavors & Fragrances, an existing registrant.

### Pass 3: final code

All entities were already registered in Zetaris from earlier passes, so run 1 is "registered", not cold (no `register_entity`). Seconds.

| Case | Run | Resolve | First query | Frame | Loop A | Scout | Onboard | Reconcile | Loop B | Total | Max silence | End state |
|---|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|---|
| s1_ibm_2020_revenue_restated | run 1 | 0.2 | 12.1 | 12.1 | 21.2 | 4.7 | 1.1 | 1.2 | 7.0 | 28.4 | 5.0 | actionable |
| s1_ibm_2020_revenue_restated | run 2 | 0.3 | 6.7 | 6.7 | 14.6 | 2.8 | 1.2 | 0.8 | 4.8 | 19.6 | 5.0 | actionable |
| s3c_mmm_2023_revenue_restated | run 1 | 0.3 | 14.9 | 14.9 | 24.6 | 4.0 | 1.0 | 0.5 | 5.5 | 30.4 | 5.0 | actionable |
| s3c_mmm_2023_revenue_restated | run 2 | 0.1 | 10.6 | 10.6 | 19.2 | 2.4 | 0.5 | 0.9 | 3.8 | 23.2 | 5.0 | actionable |
| s8_dd_2020_revenue_restated | run 1 | 0.3 | 1.3 | 1.3 | 3.5 | 2.3 | - | - | 2.3 | 6.0 | 1.4 | evidence-frontier |
| s8_dd_2020_revenue_restated | run 2 | 0.1 | 1.6 | 1.6 | 3.5 | 2.4 | - | - | 2.4 | 6.0 | 1.6 | evidence-frontier |
| 01_ge_2022_revenue_restated | run 1 | 0.4 | 19.8 | 19.8 | 39.2 | 4.8 | 0.8 | 0.6 | 6.2 | 45.7 | 5.0 | actionable |
| 01_ge_2022_revenue_restated | run 2 | 0.1 | 13.5 | 13.5 | 31.8 | 3.1 | 0.7 | 0.6 | 4.4 | 36.4 | 5.0 | actionable |
| 02_ge_2023_revenue_restated | run 1 | 0.3 | 12.7 | 12.7 | 30.6 | 3.9 | 1.3 | 0.9 | 6.1 | 37.0 | 5.0 | actionable |
| 02_ge_2023_revenue_restated | run 2 | 0.3 | 13.6 | 13.6 | 31.5 | 3.6 | 0.7 | 0.8 | 5.2 | 37.0 | 5.0 | actionable |
| 03_ge_2018_net_loss | run 1 | 0.3 | 7.8 | 7.8 | 40.3 | - | - | - | - | 40.6 | 5.0 | actionable |
| 03_ge_2018_net_loss | run 2 | 0.3 | 8.1 | 8.1 | 40.2 | - | - | - | - | 40.5 | 5.0 | actionable |
| s5b_hilton_2016_revenue_restated | run 1 | 1.2 | 11.8 | 11.8 | 18.0 | 4.6 | 0.7 | 0.8 | 6.2 | 25.4 | 5.0 | actionable |
| s5b_hilton_2016_revenue_restated | run 2 | 1.0 | 7.5 | 7.5 | 13.6 | 4.4 | 0.7 | 1.3 | 6.5 | 21.0 | 5.0 | actionable |
| s7_cxt_2022_revenue_restated | run 1 | 0.3 | 12.2 | 12.2 | 24.3 | 4.2 | 0.6 | 0.6 | 5.5 | 30.0 | 5.0 | actionable |
| s7_cxt_2022_revenue_restated | run 2 | 0.1 | 8.0 | 8.0 | 19.4 | 3.1 | 0.6 | 0.6 | 4.3 | 23.9 | 5.0 | actionable |

No run had a `degraded` line. **Zetaris was much slower in this pass than earlier in the day**: the first evidence query (`concept_series` on the asked entity, the slowest statement in every run) took 6.7–19.8 s for the entities registered before this evening (DuPont, registered an hour earlier: 1.3–1.6 s), against 1.2–2.6 s in pass 2 above and 1.3–2.3 s for the same cases in an earlier pass of this evening (pass A below); `docker stats` during the pass showed the Zetaris driver container at 3.4 GB and ~76% CPU. The cause was not investigated (the pattern fits Zetaris evicting older `CACHE TABLE`s and refetching from SEC as more tables are cached, but that was not checked; the scout's own statements are not in that query). The scout phase took 2.3–4.8 s here; in pass A, the first search of each case (registering its full-text table, 0.9–1.5 s) took 2.5–4.6 s and a repeat 0.9–1.7 s.

### End states (pass 3)

| Case | End state | What the scout found |
|---|---|---|
| s1 IBM FY2020 restated | `actionable` | Kyndryl Holdings (CIK 0001867072), named in 25 of its own Form 10 documents; registrant since 2021-06-22, no 10-K before the 2021-11-04 disposal, own 8-K that day with item 5.01. Add-back: restated $55.18B + Kyndryl $19.35B = $74.53B vs original $73.62B (gap $911M, within the 5% tolerance of $3.68B; over-explains by 5%). Was `evidence-frontier` (B-5). |
| s3c 3M FY2023 restated | `actionable` | Solventum (CIK 0001964738), named in 33 Form 10 documents; own 8-K on the 2024-04-04 disposal date (no item 5.01). Add-back $24.61B + $8.20B = $32.81B vs $32.68B, explains 102%. Was `evidence-frontier` (B-5). |
| s8 DuPont FY2020 restated | `evidence-frontier` | No Form 10 filer qualifies (one, not in `sec_tickers`, named twice). The 8-K search finds International Flavors & Fragrances (CIK 0000051253) with item 2.01 on the disposal date and annual reports before it: "sale to an existing company: … its reported figures combine its own business with the acquired one, so they cannot measure the disposed business". Dow Inc. (an earlier DowDuPont spin-off) was checked and refused (an annual report before the disposal). |
| 01, 02 GE | `actionable` | As before (GE HealthCare; GE Vernova), now also named by GE's name in their Form 10s; for 02, GE HealthCare (named in 32 documents) is refused for its annual reports before 2024-04-02. Same reconciliations as sample mode. |
| 03 GE | `actionable` | Goodwill impairment, Loop A only. |
| s5b Hilton FY2016 restated | `actionable` | Hilton Grand Vacations (44 documents) and Park Hotels & Resorts (39) both qualify (new registrants, item 5.01 on 2017-01-04); HGV ranks first on the brand and the reason says "also qualified, ranked lower: PK". HGV explains $1.58B of the restatement; the rest stays as the residual (one counterparty per disposal, BACKLOG B-16). |
| s7 Crane NXT FY2022 restated | `actionable` | Crane Co (CIK 0001944013), named in 13 Form 10 documents; it filed one 10-K (2023-03-31) after its own Form 10 (2022-12-15) and before the 2023-04-03 separation, which the scout now allows. Add-back $1.34B + $2.04B = $3.37B vs $3.37B, explains 100%. Was `evidence-frontier`. |

J&J (Loop A only), Caterpillar and ExxonMobil (no disposal hint, so the scout's searches never run) were not re-run; their end states above are unchanged by this change.

### Pass A and the fixes it led to (same evening, earlier code)

Pass A ran at commit-in-progress code (Form 10 search and brand ranking, no annual-report allowance) on IBM, 3M, GE 01–03, Hilton and Crane NXT: IBM, 3M, GE and Hilton ended `actionable` as above with first queries of 1.3–2.3 s and totals of 7.3–19.0 s (onboarding Kyndryl and Solventum cold: 8.2 s and 5.8 s); Crane NXT ended `evidence-frontier` because Crane Co had one 10-K before the separation. Three fixes followed, each re-run live:
1. **One annual report after the candidate's own Form 10 is allowed** (at most one, and the Form 10 within the 540-day lookback): Crane NXT then reconciled exactly (above).
2. **DuPont matched the wrong company before the lookback bound**: with fix 1 alone, Dow Inc. (Form 10 in 2018–19, one 10-K before 2021-02-04) qualified through the 8-K search, the add-back explained 636% of the restatement and the auto gate accepted it capped (`actionable`). Bounding the allowance to a Form 10 inside the lookback refused Dow (BACKLOG B-15 records the reconciler side). That run also showed a cold registration failing: DuPont's `bootstrap:submissions_table` took 79 s and Zetaris answered HTTP 400 with an empty message, so that run ended at `evidence-frontier`; the next run registered it (BACKLOG B-18).
3. **The 8-K search returned nothing** with `forms=8-K,8-K/A` (any `/A` form empties the result); it now sends root forms only, and amendments still match.

