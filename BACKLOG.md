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
Found in: #6  ·  Status: done (#PR_ANY)
- Out of scope because: #6 is sample mode first (task scope); `--live` goes through `make_source(False)` = `FallbackSource(LiveZetarisSource, SampleSource)` but was only exercised by the existing adapter tests, not end to end.
- Safe to leave because: an outage degrades per call to the sample (traced `degraded`); a non-recoverable `SourceError` (e.g. a concept view that does not exist) is caught by the analyst, traced `error` and the candidate is pruned as "not testable", so the run still ends in an end state.
- Urgent if: the demo or `/run` uses live mode, or a pruned-as-not-testable candidate hides a real SQL bug (check `analyst`/`measure` lines with `status: error` in the trace).
- Evidence: `services/agent/src/tiresias_agent/agents/analyst.py` (`except SourceError`); `orchestrator.main --live`.
- Resolved in #PR_ANY: the three input examples and five surprise companies were run live end to end against local Zetaris, cold and warm, with no `degraded` line; timings and end states in [docs/measurements/live-any-company.md](docs/measurements/live-any-company.md) (`scripts/perf_smoke.py`).

## B-4: Live Loop B discovery reads SEC directly, not through Zetaris
Found in: #8  ·  Status: done (#PR_ANY)
- Out of scope because: the scout's two discovery reads (`sec_tickers`, `sec_submissions` per candidate) would each need a Zetaris REST table per candidate, which would spend the source budget (`MAX_NEW_SOURCES_PER_RUN`) on discovery instead of evidence. This PR is sample mode first.
- Safe to leave because: discovery only picks *which* entity to onboard; every number the critic reconciles is queried through `EvidenceSource` (Zetaris in live mode, with sample fallback). Live discovery uses only catalog endpoints, with `SEC_USER_AGENT`, a 20 s timeout, bounded retries and the catalog rate limit; an outage degrades to the sample (traced `degraded`), a non-recoverable error is raised.
- Urgent if: ADR-0003 ("Zetaris is the only path to evidence in live mode") is read as covering discovery, or a judge's live run must show discovery SQL.
- Evidence: `services/agent/src/tiresias_agent/adapters/catalog.py` (`LiveDiscovery`, `FallbackDiscovery`).
- Resolved in #PR_ANY: `LiveDiscovery` (httpx GETs to SEC) is gone. Live discovery is `ZetarisDiscovery`, Lightning SQL through `ZetarisClient` only: `sec_tickers` is a Zetaris REST table + view (`zetaris/sql/13_tickers_table.sql`, `22_tickers_view.sql`, registered by `bootstrap` or on first use) queried with `34`..`36_tickers_by_*.sql`; a candidate's `sec_submissions` is the same per-ticker REST table as bootstrap's (`11_submissions_table.sql`, CREATE + CACHE on demand) read with `37_submissions_profile.sql`. A REST table binds one URL, so a per-CIK table is the Zetaris-only way to read one registrant's submissions; these are discovery objects (at most one tickers table and `MAX_CANDIDATES` submissions tables per scout run), not evidence sources, so they never count toward `MAX_NEW_SOURCES_PER_RUN`. Guarded by `tests/test_any_company.py::test_only_the_sponsor_adapters_import_an_http_client_and_no_code_names_sec` and the live tests that refuse any non-mocked HTTP.

## B-5: Source scout resolves only separations into a new, same-brand registrant
Found in: #8  ·  Status: open
- Out of scope because: the rules (brand pre-filter; registrant by the disposal date, no annual report before it, own 8-K within 7 days, item 5.01 preferred) cover spin-offs and split-offs. A sale to an existing company needs the 8-K exhibit text (the counterparty's name), which is not in the catalog's structured data.
- Safe to leave because: an unmatched or ambiguous hint is refused and the run stays at `evidence-frontier` with the reason in `information_need.reason`; nothing is guessed by name.
- Urgent if: a judge's restatement comes from a sale to an existing company, or from a spin-off with a new brand.
- Evidence: `services/agent/src/tiresias_agent/agents/source_scout.py` (`assess`, `_resolve`); `tests/test_loop_b.py::test_scout_refuses_a_tie_and_an_unmatched_disposal`.

## B-6: The add-back test is defined for restatements only
Found in: #8  ·  Status: open
- Out of scope because: for a period-over-period change, the disposed business's contribution to each period depends on the completion date, which needs quarterly data that the sample slice and the concept views do not hold.
- Safe to leave because: for a `change` frame, `reconciler.measure` records the missing test and the critic returns `frontier`, so the run ends at `evidence-frontier` with the need recorded, never with an invented share.
- Urgent if: an input asks why a metric changed year on year because of a disposal and Loop A hands it to Loop B.
- Evidence: `services/agent/src/tiresias_agent/agents/reconciler.py` (`frame.kind != "restatement"`); `tests/test_loop_b.py::test_reconcile_small_share_is_pruned_and_missing_data_is_a_frontier`.

## B-7: Loop B not yet run against live Zetaris
Found in: #8  ·  Status: done (#PR_ANY)
- Out of scope because: this PR is sample mode first; the live registration path (`bootstrap(..., tables=("facts",))`, view refresh, outage → degraded, non-recoverable → failed) is exercised with a mocked Zetaris transport only.
- Safe to leave because: sample mode is the default and what judges run; in live mode a failed registration ends the run at `evidence-frontier` (non-recoverable) or degrades to the sample with a `degraded` trace line (outage).
- Urgent if: the demo uses `--live`, or `/run` is switched to `run_investigation` with `SAMPLE_MODE=false`.
- Evidence: `tests/test_loop_b.py::test_onboarder_live_registers_one_statement_at_a_time_and_refreshes_views`, `::test_onboarder_live_outage_degrades_but_a_bad_request_never_uses_the_sample`.
- Resolved in #PR_ANY: input examples 01 and 02 run live end to end (Zetaris discovery → scout → onboard → reconcile) and end `actionable`, as in sample mode; see [docs/measurements/live-any-company.md](docs/measurements/live-any-company.md).

## B-8: A registered entity gets no view for a concept outside `CONCEPT_SLUGS`
Found in: #PR_ANY  ·  Status: open
- Out of scope because: #PR_ANY registers an **unregistered** asked entity with views for `CONCEPT_SLUGS` plus the asked metric and its aliases. For an entity already registered (the showcase, or one a previous run onboarded) it only checks that the filings view and a metric view exist; when the metric view is missing it re-runs the registration, but does not add a view for a concept outside that list on an otherwise complete entity later in Loop A (the hypothesiser only uses `CONCEPT_SLUGS` concepts anyway).
- Safe to leave because: a metric with no view ends at `evidence-frontier` (`MetricNotFound`) with the reason, never a crash or an invented value; the entity resolver re-registers when the asked metric's view is missing.
- Urgent if: a judge asks about a metric outside the seven showcase concepts for GE/GEHC/GEV in live mode, or the hypothesiser gains rules over new concepts.
- Evidence: `services/agent/src/tiresias_agent/agents/entity_resolver.py` (`_ready`); `onboarder.onboard_entity` (`concepts`).

## B-9: "already exists" registration steps are traced as `status: error`
Found in: #PR_ANY  ·  Status: open
- Out of scope because: the client traces every failed statement as `error` before `bootstrap` classifies the reply ("already exists" → `exists`, `FIELD_NOT_FOUND` → `absent`); changing the client's trace contract touches every adapter test.
- Safe to leave because: the outcome is on the `onboarder` `register` line and on `phase_done` (`outcome`), and `bootstrap` reports `exists`/`absent` correctly; nothing reads the client's per-statement status to decide anything.
- Urgent if: a UI colours trace lines by `status` (a live registration shows red lines for `database`, `container` and absent concepts).
- Evidence: `services/agent/src/tiresias_agent/adapters/zetaris.py` (`ZetarisClient.run_sql` error trace, `bootstrap`); any live trace's `bootstrap:database` line.

## B-10: Only SEC's `filings.recent` block is read (about the last 1,000 filings)
Found in: #PR_ANY  ·  Status: open
- Out of scope because: SEC's submissions JSON lists older filings in separate `filings.files` pages (one URL each); reading them means one more REST table per page. The filings view (`21_filings_view.sql`) and the scout's profile query (`37_submissions_profile.sql`) read `filings.recent` only.
- Safe to leave because: for a large filer whose `recent` block no longer reaches the event, the hypothesiser finds no 8-K for the window and the run ends at `evidence-frontier`, never with an invented event; a candidate whose early filings fell out of `recent` fails the scout's "registrant by the disposal" rule and is refused, not guessed.
- Urgent if: a judge's question is about an event several years back at a filer with many filings (for example insider Forms 3/4/5 push old 8-Ks out of `recent`).
- Evidence: `zetaris/sql/21_filings_view.sql`, `zetaris/sql/37_submissions_profile.sql`; see the end states in `docs/measurements/live-any-company.md`.
