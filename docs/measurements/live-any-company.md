# Live latency: any company, through Zetaris

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
