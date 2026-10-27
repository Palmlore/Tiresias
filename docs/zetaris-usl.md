# Zetaris Unified Semantic Layer (USL)

How Tiresias uses the Zetaris USL, laid out against the "Build with Zetaris" evaluation criteria. Every query result below comes from one live run on 27 Oct 2026 (Zetaris 2.4.3.1, local bundle), logged statement by statement in [measurements/zetaris-usl-2026-10-27.jsonl](measurements/zetaris-usl-2026-10-27.jsonl).

- **SQL:** [zetaris/usl/](../zetaris/usl/) (model, activation, data quality, queries) and [zetaris/explore/](../zetaris/explore/) (three queries per source), one statement per file.
- **Run it:** `cd services/agent && uv run python ../../scripts/zetaris_usl.py --out run.jsonl` (needs Zetaris with case 04 registered, i.e. one live run of `input_examples/04_3m_2023_revenue_restated.json`). A re-run is safe.
- **See it:** [zetaris/usl/explorer.html](../zetaris/usl/explorer.html) shows the lineage, ERD, DQ results and one number traced end to end. It is generated from the log by `python3 scripts/render_usl_explorer.py`.

**What the USL does and does not do in Tiresias.** The USL models the evidence of the lead example (3M → Solventum) and checks it independently. It reproduces the agent's answer in one statement and its DQ rules surface two findings of their own. The agent itself does not read the USL yet: it reads per-company views that Loop B registers during a run. Why, and what it would take, is under [Future work](#future-work).

## 1a. Use case and data sources

**Use case.** A financial analyst asks why a reported figure changed between two filings, for example "Why did 3M's reported 2023 revenue fall between its 2024 and 2025 annual reports?". Tiresias answers with a "5 Whys" chain in which every link is SQL run through Zetaris plus the rows it returned. In case 04 the answer is the Solventum spin-off: 3M restated FY2023 revenue from $32.681B to $24.61B, Solventum's own filings report $8.197B for that year, and the add-back leaves a $126M residual.

**Sources.** Four SEC sources, each registered in Zetaris as Lightning REST tables ([catalog/sources.yaml](../catalog/sources.yaml), [zetaris/sql/](../zetaris/sql/)):

| Source | Catalog id | What Tiresias reads from it |
|---|---|---|
| SEC XBRL company facts (`data.sec.gov/api/xbrl/companyfacts`) | `sec_companyfacts` | Reported values per concept, period and filing: the figures being explained |
| SEC submissions (`data.sec.gov/submissions`) | `sec_submissions` | Company profile and filing history with 8-K item codes: the events |
| SEC ticker map (`company_tickers_exchange.json`) | `sec_tickers` | Ticker and name to CIK: entity resolution and the entity directory |
| EDGAR full-text search (`efts.sec.gov`) | `sec_fulltext` | Filings that name the company as a parent: how Loop B finds a spun-off business |

## 1b. Sources onboarded and explored

The sources are registered by the agent itself (`adapters/zetaris.py bootstrap`, or on demand during a run), and three exploratory queries per source ran against them ([zetaris/explore/](../zetaris/explore/), results in the log and the explorer page):

| Source | Query | Result (live) |
|---|---|---|
| company facts | `companyfacts_1_fy_revenue_by_filing` | 3M annual revenue as each 10-K reported it (12 rows, FY2021 onwards) |
| | `companyfacts_2_restated_years` | Two years were reported with two values: FY2022 ($34.229B, then $26.161B) and FY2023 ($32.681B, then $24.61B) |
| | `companyfacts_3_disposal_revenue` | Only two 2020 interim rows are tagged as disposal-group revenue, so the spin-off's revenue has to come from Solventum's own filings |
| submissions | `submissions_1_form_mix` | 3M's recent filings by form: 496 Form 4s and 140 8-Ks lead |
| | `submissions_2_disposal_8k` | Two 8-Ks with item 2.01, one filed 2024-04-04 (items 2.01, 9.01): the spin-off's completion |
| | `submissions_3_spinoff_profile` | Solventum: SIC 3841, ticker SOLV, first filing a DRS on 2023-02-14 |
| tickers | `tickers_1_exchange_mix` | Listed tickers by exchange (Nasdaq 4,358, NYSE 3,276, OTC 2,570, ...) |
| | `tickers_2_lookup` | MMM → CIK 66740, SOLV → CIK 1964738 |
| | `tickers_3_name_search` | The name search the entity directory uses finds both |
| full-text search | `fulltext_1_total` | 51 hits for the 3M spin-off search Loop B registered |
| | `fulltext_2_hits_by_company` | Solventum has 33 hits; the next filers have 3 each |
| | `fulltext_3_forms` | The hits are Form 10 registrations (10-12B, 10-12B/A, 10-12G/A): a spin-off's signature |

## 1c. The USL: `tiresias_usl.sec_evidence`

**DDL** ([10_compile.sql](../zetaris/usl/10_compile.sql)): three tables, three foreign keys.

```sql
COMPILE USL IF NOT EXISTS sec_evidence DEPLOY NAMESPACE lightning.metastore.tiresias_usl DDL
CREATE TABLE company (cik bigint NOT NULL PRIMARY KEY, entity_name varchar(200) NOT NULL,
                      sic varchar(4), sic_description varchar(200), fiscal_year_end varchar(4));
CREATE TABLE filing  (accession_number varchar(20) NOT NULL PRIMARY KEY,
                      cik bigint NOT NULL FOREIGN KEY REFERENCES company(cik),
                      form varchar(20), filing_date date, report_date date, items varchar(100));
CREATE TABLE fact    (cik bigint NOT NULL FOREIGN KEY REFERENCES company(cik),
                      concept varchar(200) NOT NULL, unit varchar(10), val double NOT NULL,
                      period_start date, period_end date NOT NULL, fy int, fp varchar(4),
                      form varchar(20), filed date,
                      accn varchar(20) NOT NULL FOREIGN KEY REFERENCES filing(accession_number),
                      frame varchar(20));
```

Each table is activated by one query over the raw REST tables of both companies ([20](../zetaris/usl/20_activate_company.sql), [21](../zetaris/usl/21_activate_filing.sql), [22](../zetaris/usl/22_activate_fact.sql)), with the same flattening our per-company views use.

**Queries showing it is active** ([40–45](../zetaris/usl/)):

| Query | Result (live) |
|---|---|
| `40_row_counts` | company 2, filing 1,262, fact 539 |
| `41_reconcile_2023` | 3M original $32.681B, restated $24.61B, Solventum $8.197B, add-back $32.807B, **residual $126M**: the same residual the agent reports from per-company views ([output_examples/04_3m_2023_revenue_restated.json](../output_examples/04_3m_2023_revenue_restated.json)) |
| `42_shared_filings` | 6 filings in both companies' histories (below) |
| `43_fact_traceability` | Facts per company and fiscal year, traced to a filing or not (below) |
| `44_trace_one_number` | Solventum FY2023 revenue $8.197B → accession `0001964738-25-000019` → the 10-K filed 2025-02-28 → `tiresias_sec.solv_facts_raw` → the SEC companyfacts URL |

**Data-quality rules, run on the live rows** (`RUN DQ`, [31](../zetaris/usl/31_run_dq_filing.sql), [32](../zetaris/usl/32_run_dq_fact.sql)):

| Rule | Type | Total | Valid | Invalid |
|---|---|---|---|---|
| `filing.accession_number` | primary key | 1,262 | 1,250 | 12 |
| `filing.cik` | foreign key → company | 1,262 | 1,262 | 0 |
| `fact.cik` | foreign key → company | 539 | 539 | 0 |
| `fact.accn` | foreign key → filing | 539 | 294 | 245 |
| `fact.period_order` | custom: `period_start IS NULL OR period_end >= period_start` | 539 | 539 | 0 |

The two failing rules are findings, not broken data:

1. **The 12 primary-key failures are 6 filings that sit in both companies' histories**: 3M's Schedule 13G and 13G/A on its retained Solventum stake, plus Forms 3 and 4. They link parent and spin-off directly. Tiresias finds that link today through full-text search; the USL surfaces it as a side effect of declaring a key.
2. **The 245 `fact.accn` failures are facts that cite a filing outside SEC's `filings.recent` list**, which holds only recent filings. They are 3M's older years, FY2009 to FY2018. This puts a number on a known limit (BACKLOG B-10): from this source alone, the filing behind 45% of these facts cannot be traced.

## 2a. Time saved compared with hand-coding

What we can measure: building, activating and checking the USL took about an hour of one session, including two fixes the error messages pointed to. Without a USL, the same work is hand-coded in the agent and spread over several files:

| Job | Hand-coded in Tiresias | In the USL |
|---|---|---|
| Flatten SEC JSON per company and concept | One SchemaStore view per company and concept (`zetaris/sql/20_concept_view.sql`; 3M alone has 7 views: 6 concepts and the filing history) | One `fact` table for every company and concept |
| Reconcile parent and spin-off | Python in `agents/reconciler.py` (`measure`, about 60 lines): fetch each series, filter the period, pick the first annual report, add back | One statement (`41_reconcile_2023.sql`) |
| Drop repeated facts | `_dedupe` in `adapters/zetaris.py` | A key on the table (not declared here: an XBRL fact has no natural key) |
| Check relationships and quality | Not done; nobody wrote the check | Generated by the foreign keys, run with one command |

**Bonus comparison.** The hand-coded approach above is the real one, not a mock: `reconciler.measure` against `41_reconcile_2023.sql`, and both give $126M. The USL version is shorter and declarative, and its checks found two things the hand-coded path never looked for. The hand-coded version does what the USL cannot yet do: it runs on any company Loop B registers mid-run.

Where the USL saved less than it could: the flattening still has to be written (the activation query is the same `LATERAL VIEW explode` as the view), and `NOT NULL` columns need `COALESCE` in the activation query. Zetaris's error message suggested that fix itself, which turned a first failure into a one-minute change.

## 2b. Would the USL help in this work?

**Where it helps.**
- **Evidence an analyst can audit.** A typed model with keys and DQ results is what a reviewer asks for: which rows were checked, which failed and why. A traced fact versus an untraced one is a grade Tiresias could show on each link of its why-tree.
- **One model per question type instead of objects per company.** Restatement questions always need the same three things (companies, filings, facts). Modelling them once replaces the per-company views with three tables. Materialized, it could also replace the per-company cached tables behind our Zetaris memory trouble (BACKLOG B-24); we did not try materialization.
- **Relationships for free.** Declaring a key exposed the parent and spin-off link. An agent that runs `RUN DQ` before it reasons gets those hints at no extra cost.

**Where it does not help, yet.**
- **Open-ended discovery.** Tiresias does not know which companies it needs until Loop B finds them. A USL is compiled and activated up front, so a new company means re-activating every table.
- **Sample mode.** Judges run Tiresias without Zetaris by default; the USL needs a live instance.

**A project that would benefit, with a time forecast.** A coverage universe: the 15 companies of our live sweep ([measurements/coverage-sweep-2026-10-26.md](measurements/coverage-sweep-2026-10-26.md)), each with its spin-offs, kept current for an analyst team. Today each company costs 2 REST tables and about 7 views (one per concept), so 15 parents and about 15 spin-offs mean roughly 60 REST tables and 200 views. With a USL, the same universe is 60 REST tables and **3 tables**: adding a company is one more branch in each activation query, and quality checks cover every company at once. Our estimate, not a measurement: onboarding a new company drops from about 20 minutes (register, add views for each concept, check them by hand) to about 5, and the quality review that is not done at all today comes with the model.

## 2c. How to make the USL stronger

**Requirement: incremental activation, so an agent can grow a semantic layer while it runs.**

*Problem.* An agent discovers sources during a run. Tiresias registers a spun-off company's filings mid-investigation. The USL assumes the sources are known when the model is activated: adding a company means rewriting and re-running each table's whole activation query, which re-reads every source. So agents fall back to per-entity views, losing the types, keys and DQ that make the USL worth having.

*Requirement.* A USL table can take an extra source without re-activating the others, and DQ re-runs on the new rows only.

*Acceptance.* (1) Adding one company to a 30-company `fact` table reads only the new company's REST table. (2) `RUN DQ` reports per source, so a bad new source does not hide behind good old ones. (3) A failed source addition leaves the table as it was.

*Mock of the feature* (proposed syntax; **does not run** on 2.4.3.1):

```sql
-- Loop B has just registered Solventum's REST tables; the model grows by one source.
ALTER USL TABLE lightning.metastore.tiresias_usl.sec_evidence.fact
  ADD SOURCE solv AS
  SELECT COALESCE(CAST(cik AS bigint), 0) AS cik, 'Revenues' AS concept, ...
  FROM tiresias_sec.solv_facts_raw
  LATERAL VIEW explode(facts.`us-gaap`.Revenues.units.USD) AS f;

RUN DQ TABLE lightning.metastore.tiresias_usl.sec_evidence.fact SOURCE solv;
-- name        source  total  valid  invalid
-- accn        solv       43     43        0
```

The resulting USL is the one in this repo, with `fact` and `filing` growing a source per company instead of one hand-edited `UNION ALL`.

**Two smaller requests from this build.**
- **Cross-table DQ rules.** A rule such as "a fact's `filed` date equals its filing's `filing_date`" needs a join. In Zetaris's getting-started repo, attempts to register a cross-table check failed and it stays a plain join (its SQL companion, section 8.6); we did not retry it here.
- **DQ results as a queryable table with lineage.** `SHOW DQ INVALID RECORD` returns the failing rows. Returning them with the source REST table and the rule as columns would let an agent cite "this fact failed rule X" as evidence, which it could then show in an audit trail.

## Future work

Two ways to make the agent use the USL, both after this submission unless picked up earlier (BACKLOG B-37, B-38):

- **Agent cross-check (B-37).** In live mode, after Loop B reconciles, the Zetaris adapter runs the USL reconciliation and `RUN DQ` on the facts the answer cites, and records one trace line ("USL cross-check agrees; source filing traced / untraced"). It is behind the adapter with a fallback; when the USL does not cover the companies, the line says "skipped". About 2 hours. Limited to companies the USL covers.
- **Loop B grows the USL (B-38).** When Loop B registers a company, it re-activates `company`, `filing` and `fact` with that company's branch added, so the agent reads all evidence through the USL. This is the long-table redesign deferred after the Zetaris memory triage, done with the USL. It needs the incremental activation above, or re-activation of every table per new company. Half a day or more.
