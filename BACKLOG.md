# Backlog

Known gaps that a change deliberately left for later. Each pull request is reviewed by an adversarial second model ([DuelLoop](docs/ai-usage.md#development-tools)); when a real finding is out of scope for that change, it is **deferred** here rather than dropped, in the same commit as the deferral.

Every item states all four: why it is out of scope for the change that found it, what bounds the harm meanwhile, where it was found (PR), and what would make it urgent. Items that survive to submission are summarised in [docs/limitations.md](docs/limitations.md).

Format:

```
## B-<n>: <title>
Found in: #<PR>  ·  Status: open | done (#<PR>)
- Out of scope because: …
- Safe to leave because: …
- Urgent if: …
- Evidence: <command, file:line, or trace>
```

---

## B-1: Agent Docker image can't see `data/sample/` or `zetaris/sql/`
Found in: #5  ·  Status: done (#7)
- Out of scope because: #5 builds the adapter; the Dockerfile and `docker-compose.yml` belong to the packaging task (judge setup and quickstart).
- Safe to leave because: nothing calls the adapter inside the container yet (`/run` returns 501); the adapter reads `SAMPLE_DATA_DIR` and `ZETARIS_SQL_DIR`, so a mount or a `COPY` is enough.
- Urgent if: `/run` is wired to the adapter, or before the first `docker compose up` test of sample mode.
- Evidence: `services/agent/Dockerfile` copies only `src/`; `sample_dir()` and `sql_dir()` in `services/agent/src/tiresias_agent/adapters/zetaris.py`.
- Resolved in #7: the agent is built from the repo root (`compose.yaml`, allow-list `.dockerignore`); the image copies `data/sample/`, `zetaris/sql/`, `catalog/`, `input_examples/` under `/app` and sets `SAMPLE_DATA_DIR`, `ZETARIS_SQL_DIR`, `LOGS_DIR`; checked by `services/agent/tests/test_packaging.py` and a `docker compose up --build` run (PR #7 Evidence).

## B-2: Rule-based hypothesiser knows a small, fixed rule set; `cyclic` is only a guard
Found in: #6  ·  Status: open
- Out of scope because: #6 builds Loop A in sample mode with the rule-based agents of ADR-0007. A wider rule set (segments, restructuring, tax items) needs more concepts in the sample trim and live views, and feedback-loop detection needs a model-backed hypothesiser (Nightshift lane).
- Safe to leave because: a question no rule explains ends in `evidence-frontier` with an `InformationNeed` for the unexplained variable, never in a crash or an invented cause; the hypothesiser drops any candidate already on the path, so the loop cannot cycle.
- Urgent if: a demo or judge question needs a driver outside `agents/knowledge.py` (e.g. segment revenue), or the `cyclic` end state must be shown.
- Evidence: `services/agent/src/tiresias_agent/agents/knowledge.py` (`COMPONENTS`, `EVENT_CONCEPTS`, `FILING_EVENTS`); `orchestrator.LoopA._no_more_rules`.

## B-3: Loop A not yet run against live Zetaris
Found in: #6  ·  Status: done (#9)
- Out of scope because: #6 is sample mode first (task scope); `--live` goes through `make_source(False)` = `FallbackSource(LiveZetarisSource, SampleSource)` but was only exercised by the existing adapter tests, not end to end.
- Safe to leave because: an outage degrades per call to the sample (traced `degraded`); a non-recoverable `SourceError` (e.g. a concept view that does not exist) is caught by the analyst, traced `error` and the candidate is pruned as "not testable", so the run still ends in an end state.
- Urgent if: the demo or `/run` uses live mode, or a pruned-as-not-testable candidate hides a real SQL bug (check `analyst`/`measure` lines with `status: error` in the trace).
- Evidence: `services/agent/src/tiresias_agent/agents/analyst.py` (`except SourceError`); `orchestrator.main --live`.
- Resolved in #9: the three input examples and seven other companies (IBM, J&J, 3M, ExxonMobil, Hilton, Caterpillar, Crane NXT) were run live end to end against local Zetaris, cold and warm, with no `degraded` line; timings and end states in [docs/measurements/live-any-company.md](docs/measurements/live-any-company.md) (`scripts/perf_smoke.py`).

## B-4: Live Loop B discovery reads SEC directly, not through Zetaris
Found in: #8  ·  Status: done (#9)
- Out of scope because: the scout's two discovery reads (`sec_tickers`, `sec_submissions` per candidate) would each need a Zetaris REST table per candidate, which would spend the source budget (`MAX_NEW_SOURCES_PER_RUN`) on discovery instead of evidence. This PR is sample mode first.
- Safe to leave because: discovery only picks *which* entity to onboard; every number the critic reconciles is queried through `EvidenceSource` (Zetaris in live mode, with sample fallback). Live discovery uses only catalog endpoints, with `SEC_USER_AGENT`, a 20 s timeout, bounded retries and the catalog rate limit; an outage degrades to the sample (traced `degraded`), a non-recoverable error is raised.
- Urgent if: ADR-0003 ("Zetaris is the only path to evidence in live mode") is read as covering discovery, or a judge's live run must show discovery SQL.
- Evidence: `services/agent/src/tiresias_agent/adapters/catalog.py` (`LiveDiscovery`, `FallbackDiscovery`).
- Resolved in #9: `LiveDiscovery` (httpx GETs to SEC) is gone. Live discovery is `ZetarisDiscovery`, Lightning SQL through `ZetarisClient` only: `sec_tickers` is a Zetaris REST table + view (`zetaris/sql/13_tickers_table.sql`, `22_tickers_view.sql`, registered by `bootstrap` or on first use) queried with `34`..`36_tickers_by_*.sql`; a candidate's `sec_submissions` is the same per-ticker REST table as bootstrap's (`11_submissions_table.sql`, CREATE + CACHE on demand) read with `37_submissions_profile.sql`. A REST table binds one URL, so a per-CIK table is the Zetaris-only way to read one registrant's submissions; these are discovery objects (at most one tickers table and `MAX_CANDIDATES` submissions tables per scout run), not evidence sources, so they never count toward `MAX_NEW_SOURCES_PER_RUN`. Guarded by `tests/test_any_company.py::test_only_the_sponsor_adapters_import_an_http_client_and_no_code_names_sec` and the live tests that refuse any non-mocked HTTP.

## B-5: Source scout resolves only separations into a new, same-brand registrant
Found in: #8  ·  Status: done (#__PR__)
- Out of scope because: the rules (brand pre-filter; registrant by the disposal date, no annual report before it, own 8-K within 7 days, item 5.01 preferred) cover spin-offs and split-offs. A sale to an existing company needs the 8-K exhibit text (the counterparty's name), which is not in the catalog's structured data.
- Safe to leave because: an unmatched or ambiguous hint is refused and the run stays at `evidence-frontier` with the reason in `information_need.reason`; nothing is guessed by name.
- Urgent if: a judge's restatement comes from a sale to an existing company, or from a spin-off with a new brand.
- Evidence: `services/agent/src/tiresias_agent/agents/source_scout.py` (`assess`, `_resolve`); `tests/test_loop_b.py::test_scout_refuses_a_tie_and_an_unmatched_disposal`.
- Resolved in #__PR__: the counterparty now comes from filing evidence. A new catalog template, `sec_fulltext` (EDGAR full-text search, queried through Zetaris: `zetaris/sql/14_fulltext_table.sql`, `40_fulltext_total.sql`, `41_fulltext_hits.sql`), finds the filers whose **Form 10** (10-12B/10-12G) in the 540 days before the disposal contains the source's name as it was at that date; those are the first candidates, whatever their own name, and the brand is only a ranking signal. If nothing qualifies, a second search looks at **8-Ks within 7 days** of the disposal that name the source: an existing registrant there with item 2.01 is reported as *a sale to an existing company* and refused, with the buyer and the reason. Live (`docs/measurements/live-any-company.md`, 2026-10-23 section): IBM FY2020 → Kyndryl, 3M FY2023 → Solventum and Crane NXT FY2022 → Crane Co now reconcile within 5% and end `actionable`; DuPont FY2020 (N&B merged into IFF) is refused as a sale to IFF. Tests: `tests/test_spinoff_match.py`. Remaining limits: B-15..B-18.

## B-6: The add-back test is defined for restatements only
Found in: #8  ·  Status: open
- Out of scope because: for a period-over-period change, the disposed business's contribution to each period depends on the completion date, which needs quarterly data that the sample slice and the concept views do not hold.
- Safe to leave because: for a `change` frame, `reconciler.measure` records the missing test and the critic returns `frontier`, so the run ends at `evidence-frontier` with the need recorded, never with an invented share.
- Urgent if: an input asks why a metric changed year on year because of a disposal and Loop A hands it to Loop B.
- Evidence: `services/agent/src/tiresias_agent/agents/reconciler.py` (`frame.kind != "restatement"`); `tests/test_loop_b.py::test_reconcile_small_share_is_pruned_and_missing_data_is_a_frontier`.

## B-7: Loop B not yet run against live Zetaris
Found in: #8  ·  Status: done (#9)
- Out of scope because: this PR is sample mode first; the live registration path (`bootstrap(..., tables=("facts",))`, view refresh, outage → degraded, non-recoverable → failed) is exercised with a mocked Zetaris transport only.
- Safe to leave because: sample mode is the default and what judges run; in live mode a failed registration ends the run at `evidence-frontier` (non-recoverable) or degrades to the sample with a `degraded` trace line (outage).
- Urgent if: the demo uses `--live`, or `/run` is switched to `run_investigation` with `SAMPLE_MODE=false`.
- Evidence: `tests/test_loop_b.py::test_onboarder_live_registers_one_statement_at_a_time_and_refreshes_views`, `::test_onboarder_live_outage_degrades_but_a_bad_request_never_uses_the_sample`.
- Resolved in #9: input examples 01 and 02 run live end to end (Zetaris discovery → scout → onboard → reconcile) and end `actionable`, as in sample mode, and a company outside the showcase (Hilton FY2016, asked by name) reaches Loop B cold and reconciles to Hilton Grand Vacations; see [docs/measurements/live-any-company.md](docs/measurements/live-any-company.md).

## B-8: A registered entity gets no view for a concept outside `CONCEPT_SLUGS`
Found in: #9  ·  Status: open
- Out of scope because: #9 registers an **unregistered** asked entity with views for `CONCEPT_SLUGS` plus the asked metric and its aliases. For an entity already registered (the showcase, or one a previous run onboarded) it only checks that the filings view and a metric view exist; when the metric view is missing it re-runs the registration, but does not add a view for a concept outside that list on an otherwise complete entity later in Loop A (the hypothesiser only uses `CONCEPT_SLUGS` concepts anyway).
- Safe to leave because: a metric with no view ends at `evidence-frontier` (`MetricNotFound`) with the reason, never a crash or an invented value; the entity resolver re-registers when the asked metric's view is missing.
- Urgent if: a judge asks about a metric outside the seven showcase concepts for GE/GEHC/GEV in live mode, or the hypothesiser gains rules over new concepts.
- Evidence: `services/agent/src/tiresias_agent/agents/entity_resolver.py` (`_ready`); `onboarder.onboard_entity` (`concepts`).

## B-9: "already exists" registration steps are traced as `status: error`
Found in: #9  ·  Status: open
- Out of scope because: the client traces every failed statement as `error` before `bootstrap` classifies the reply ("already exists" → `exists`, `FIELD_NOT_FOUND` → `absent`); changing the client's trace contract touches every adapter test.
- Safe to leave because: the outcome is on the `onboarder` `register` line and on `phase_done` (`outcome`), and `bootstrap` reports `exists`/`absent` correctly; nothing reads the client's per-statement status to decide anything.
- Urgent if: a UI colours trace lines by `status` (a live registration shows red lines for `database`, `container` and absent concepts).
- Evidence: `services/agent/src/tiresias_agent/adapters/zetaris.py` (`ZetarisClient.run_sql` error trace, `bootstrap`); any live trace's `bootstrap:database` line.

## B-10: Only SEC's `filings.recent` block is read (about the last 1,000 filings)
Found in: #9  ·  Status: open
- Out of scope because: SEC's submissions JSON lists older filings in separate `filings.files` pages (one URL each); reading them means one more REST table per page. The filings view (`21_filings_view.sql`) and the scout's profile query (`37_submissions_profile.sql`) read `filings.recent` only.
- Safe to leave because: for a large filer whose `recent` block no longer reaches the event, the hypothesiser finds no 8-K for the window and the run ends at `evidence-frontier`, never with an invented event; a candidate whose early filings fell out of `recent` fails the scout's "registrant by the disposal" rule and is refused, not guessed.
- Urgent if: a judge's question is about an event several years back at a filer with many filings (for example insider Forms 3/4/5 push old 8-Ks out of `recent`).
- Evidence: `zetaris/sql/21_filings_view.sql`, `zetaris/sql/37_submissions_profile.sql`; see the end states in `docs/measurements/live-any-company.md`.

## B-11: Revenue is only recognised under three us-gaap concepts
Found in: #9  ·  Status: open
- Out of scope because: the concept alias group is `Revenues`, `RevenueFromContractWithCustomerExcludingAssessedTax` and (added in #9) `…IncludingAssessedTax`. Some filers report total revenue under another concept (ExxonMobil's companyfacts holds only four recent `Revenues` facts); finding the right concept needs a concept-discovery step over the companyfacts map, which is the slow full explode (18 s, AGENTS.md gotcha).
- Safe to leave because: the framer ends at `evidence-frontier` (`MetricNotFound`, "no Revenues value for … in the registered sources") in under a second; nothing is guessed.
- Urgent if: a judge asks about revenue for an energy or financial filer, or for any company whose total revenue sits under another concept.
- Evidence: `docs/measurements/live-any-company.md` (case s4, XOM); `adapters/zetaris.py` (`CONCEPT_ALIASES`).

## B-12: Peers come only from registered entities; a non-SEC company gets no industry
Found in: #12  ·  Status: open
- Out of scope because: the catalog has no SIC index (SEC publishes SIC per registrant in its submissions, not a list by code), so the scope check reads the SIC of the asked entity and of at most `MAX_PEER_PROBES` registered entities. A company outside SEC has no SIC in our data at all; proposing its industry needs a model (Nightshift: the model proposes an industry or peers, the agent validates each against filed data, the model never answers).
- Safe to leave because: the alternatives that are offered are each validated; with no peer the check still offers registrants named in the question and the same entity's past period, and the `note` says why there are no peers.
- Urgent if: the demo or a judge expects same-industry peers for a non-SEC company (e.g. a hardware maker outside SEC) or for an industry with no registered entity yet.
- Evidence: `services/agent/src/tiresias_agent/agents/scope.py` (`alternatives`, `_sic`, `MAX_PEER_PROBES`); `zetaris/sql/38_registrant_profile.sql`.

## B-13: Suggestions and alternatives for an unregistered registrant are thin
Found in: #12  ·  Status: open
- Out of scope because: suggestions are derived from the company's own companyfacts, which exist only once it is registered (12–16 s cold), and a GET should not register sources. `GET /entities/{cik}/suggestions` therefore answers 409 `EntityNotRegistered` for a searchable entity, and a scope-check alternative about an unregistered registrant takes its fiscal year from its submissions (two 10-K report dates) without checking that the metric is present.
- Safe to leave because: the 409 says what to do (ask any question, which registers it, then call again); an alternative whose metric turns out to be missing ends at `evidence-frontier` (`MetricNotFound`) with the reason, never an invented value.
- Urgent if: a UI needs suggestions for a company before its first run (add an explicit `register=true`, behind the source gate and `MAX_NEW_SOURCES_PER_RUN`).
- Evidence: `services/agent/src/tiresias_agent/entities.py` (`_refuse`); `agents/scope.py` (`_alternative`, `_fiscal_year`).

## B-14: The scope check's text rules are English keyword rules
Found in: #12  ·  Status: open
- Out of scope because: forecast/what-if detection (`forward_cue`) and named-company extraction (`name_phrases`: capitalised phrases matched to exactly one CIK in `sec_tickers`) are fixed rules (ADR-0007: rule-based without a model). They miss a lowercase company name, a brand shared by several registrants, or a forward question phrased without a cue word; parsing free text with a Nightshift model is planned (model proposes, agent validates).
- Safe to leave because: false positives are kept low by design and tested (`tests/test_scope.py`); a missed forward question still runs and ends on the evidence (usually `MetricNotFound` or `NoEffect` for a future period, which the period-after-today rule also catches); an unmatched name just yields no alternative.
- Urgent if: judges type free-form questions in other phrasings, or the UI accepts questions without a structured entity.
- Evidence: `services/agent/src/tiresias_agent/agents/scope.py` (`_STRONG`, `_WEAK`, `_PAST_FRAME`, `_NOT_A_NAME`); `tests/test_scope.py` (`FORWARD`, `IN_SCOPE`).

## B-15: Loop B accepts an add-back that over-explains far beyond tolerance
Found in: #__PR__  ·  Status: open
- Out of scope because: the reconciler and its `critic_escalation` proposal (`accept-capped`) are the reconcile step's design (#8); this PR changes only who is reconciled. Found while a scout rule was too loose: DuPont FY2020 was matched to Dow Inc. (an earlier spin-off), the add-back explained 636% of the restatement, and the auto gate accepted it capped at 100% (`actionable`). The scout rule was fixed in the same PR (a Form 10 older than the lookback no longer excuses an annual report), so that run now ends at the frontier.
- Safe to leave because: the reason text and the trace state the over-explanation ("explains 636% … over-explains beyond tolerance"), and a scripted or interactive gate can prune it; with the scout's rules a gross mismatch needs a wrong counterparty that also passes every new-registrant test.
- Urgent if: any live run shows an `actionable` Loop B link with a share far above 100% (for example over 150%), or a judge reads the 100%-capped share without the residual.
- Evidence: `services/agent/src/tiresias_agent/agents/reconciler.py` (`"proposal": "accept-capped"`); trace of the DuPont run before the fix (scratch, not committed).

## B-16: One counterparty per disposal
Found in: #__PR__  ·  Status: open
- Out of scope because: the onboarder and reconciler take one entity per scout result; following two separations at one date (Hilton 2017: Hilton Grand Vacations and Park Hotels & Resorts, both new registrants named by Hilton's name in their Form 10s) needs a multi-entity add-back and two registrations against `MAX_NEW_SOURCES_PER_RUN`.
- Safe to leave because: the scout ranks the qualifying candidates (Form 10 naming, item 5.01, brand, distance), follows the best and says which also qualified ("also qualified, ranked lower: PK"); the unexplained part stays as the residual (Hilton: 63%) and a tie on every signal ends at `evidence-frontier`.
- Urgent if: a judge's restatement comes from two or more simultaneous spin-offs and the residual is read as unexplained.
- Evidence: `source_scout.Assessment.rank`; Hilton rows in `docs/measurements/live-any-company.md`.

## B-17: Full-text search bounds and blind spots
Found in: #__PR__  ·  Status: open
- Out of scope because: each search is one Zetaris REST table over one page (at most 100 matching documents, `FULLTEXT_LIMIT` = 20 filers), and only the source's name in force at the disposal is searched. Separations registered on another form (an IPO carve-out on S-1, an exchange offer or Reverse Morris Trust on S-4) are found only by the 8-K fallback; a counterparty no longer in `sec_tickers` (delisted, acquired) cannot be registered; sample mode has no full-text data, so it keeps the brand rule only.
- Safe to leave because: every miss is a refusal with the searched phrase and the candidates checked in `information_need.reason` and the trace (`source_scout` `candidates`); nothing is guessed, and every registration still goes through the `source` gate.
- Urgent if: a judge asks about a carve-out IPO or a split-off by exchange offer, a spin-off whose company was later acquired, or a parent with a very common name (the page fills with passing mentions).
- Evidence: `catalog/sources.yaml` (`sec_fulltext`); `source_scout._named`, `REGISTRATION_FORMS`; `tests/test_spinoff_match.py`.

## B-18: A registration that fails with an empty HTTP 400 is not retried
Found in: #__PR__  ·  Status: open
- Out of scope because: in the 2026-10-23 live pass, DuPont's first registration (`bootstrap:submissions_table`) took 79 s and Zetaris answered HTTP 400 with an empty message; the client treats every 4xx as non-recoverable, so the run ended at `evidence-frontier` while the next run registered it fine. Telling a Zetaris-side fetch timeout from a bad statement needs a look at Zetaris's error shapes, unrelated to this PR's scout change.
- Safe to leave because: the run ends at `evidence-frontier` with the error in the reason; nothing is served from the sample, and a second run succeeds (registration is idempotent).
- Urgent if: cold registrations fail this way more than occasionally, or during a demo.
- Evidence: `adapters/zetaris.py` (`ZetarisClient.run_sql`, `recoverable=response.status_code >= 500`); DuPont cold row in `docs/measurements/live-any-company.md`.
