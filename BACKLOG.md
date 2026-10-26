# Backlog

Known gaps that a change deliberately left for later. Each pull request is reviewed by an adversarial second model ([DuelLoop](docs/ai-usage.md#development-tools)); when a real finding is out of scope for that change, it is **deferred** here rather than dropped, in the same commit as the deferral.

Only **open** items are kept in full. When one is resolved, it becomes one line under [Closed](#closed) naming the PR; git keeps the full text. Items that survive to submission are summarised in [docs/limitations.md](docs/limitations.md).

**Severity**: `demo-risk` (could break the demo or a judge's run), `judge-visible` (a judge would notice the gap), `later` (after submission). Only the first two can block submission. **Review at** names the task or date when the item is looked at again; the session handover lists the items whose review point has come.

Format:

```
## B-<n>: <title>
Found in: #<PR>  ·  Status: open  ·  Severity: demo-risk | judge-visible | later
- Out of scope because: …
- Safe to leave because: …
- Urgent if: …
- Review at: <task or date>
- Evidence: <command, file:line, or trace>
```

---

## B-2: Rule-based hypothesiser knows a small, fixed rule set; `cyclic` is only a guard
Found in: #6  ·  Status: open  ·  Severity: judge-visible
- Out of scope because: #6 builds Loop A in sample mode with the rule-based agents of ADR-0007. A wider rule set (segments, restructuring, tax items) needs more concepts in the sample trim and live views, and feedback-loop detection needs a model-backed hypothesiser (Nightshift lane).
- Safe to leave because: a question no rule explains ends in `evidence-frontier` with an `InformationNeed` for the unexplained variable, never in a crash or an invented cause; the hypothesiser drops any candidate already on the path, so the loop cannot cycle.
- Urgent if: a demo or judge question needs a driver outside `agents/knowledge.py` (e.g. segment revenue), or the `cyclic` end state must be shown.
- Review at: demo rehearsal (Mon 26 Oct): pick demo questions inside the rule set
- Evidence: `services/agent/src/tiresias_agent/agents/knowledge.py` (`COMPONENTS`, `EVENT_CONCEPTS`, `FILING_EVENTS`); `orchestrator.LoopA._no_more_rules`.

## B-6: The add-back test is defined for restatements only
Found in: #8  ·  Status: open  ·  Severity: later
- Out of scope because: for a period-over-period change, the disposed business's contribution to each period depends on the completion date, which needs quarterly data that the sample slice and the concept views do not hold.
- Safe to leave because: for a `change` frame, `reconciler.measure` records the missing test and the critic returns `frontier`, so the run ends at `evidence-frontier` with the need recorded, never with an invented share.
- Urgent if: an input asks why a metric changed year on year because of a disposal and Loop A hands it to Loop B.
- Review at: demo rehearsal (Mon 26 Oct)
- Evidence: `services/agent/src/tiresias_agent/agents/reconciler.py` (`frame.kind != "restatement"`); `tests/test_loop_b.py::test_reconcile_small_share_is_pruned_and_missing_data_is_a_frontier`.

## B-8: A registered entity gets no view for a concept outside `CONCEPT_SLUGS`
Found in: #9  ·  Status: open  ·  Severity: later
- Out of scope because: #9 registers an **unregistered** asked entity with views for `CONCEPT_SLUGS` plus the asked metric and its aliases. For an entity already registered (the showcase, or one a previous run onboarded) it only checks that the filings view and a metric view exist; when the metric view is missing it re-runs the registration, but does not add a view for a concept outside that list on an otherwise complete entity later in Loop A (the hypothesiser only uses `CONCEPT_SLUGS` concepts anyway).
- Safe to leave because: a metric with no view ends at `evidence-frontier` (`MetricNotFound`) with the reason, never a crash or an invented value; the entity resolver re-registers when the asked metric's view is missing.
- Urgent if: a judge asks about a metric outside the seven showcase concepts for GE/GEHC/GEV in live mode, or the hypothesiser gains rules over new concepts.
- Review at: when the hypothesiser gains rules over new concepts
- Evidence: `services/agent/src/tiresias_agent/agents/entity_resolver.py` (`_ready`); `onboarder.onboard_entity` (`concepts`).

## B-10: Only SEC's `filings.recent` block is read (about the last 1,000 filings)
Found in: #9  ·  Status: open  ·  Severity: later
- Out of scope because: SEC's submissions JSON lists older filings in separate `filings.files` pages (one URL each); reading them means one more REST table per page. The filings view (`21_filings_view.sql`) and the scout's profile query (`37_submissions_profile.sql`) read `filings.recent` only.
- Safe to leave because: for a large filer whose `recent` block no longer reaches the event, the hypothesiser finds no 8-K for the window and the run ends at `evidence-frontier`, never with an invented event; a candidate whose early filings fell out of `recent` fails the scout's "registrant by the disposal" rule and is refused, not guessed.
- Urgent if: a judge's question is about an event several years back at a filer with many filings (for example insider Forms 3/4/5 push old 8-Ks out of `recent`).
- Review at: after submission
- Evidence: `zetaris/sql/21_filings_view.sql`, `zetaris/sql/37_submissions_profile.sql`; see the end states in `docs/measurements/live-any-company.md`.

## B-11: Revenue is only recognised under three us-gaap concepts
Found in: #9  ·  Status: open  ·  Severity: judge-visible
- Out of scope because: the concept alias group is `Revenues`, `RevenueFromContractWithCustomerExcludingAssessedTax` and (added in #9) `…IncludingAssessedTax`. Some filers report total revenue under another concept (ExxonMobil's companyfacts holds only four recent `Revenues` facts); finding the right concept needs a concept-discovery step over the companyfacts map, which is the slow full explode (18 s, [zetaris/README.md](zetaris/README.md#quirks)).
- Safe to leave because: the framer ends at `evidence-frontier` (`MetricNotFound`, "no Revenues value for … in the registered sources") in under a second; nothing is guessed.
- Urgent if: a judge asks about revenue for an energy or financial filer, or for any company whose total revenue sits under another concept.
- Review at: demo rehearsal (Mon 26 Oct): try an energy and a financial filer live
- Evidence: `docs/measurements/live-any-company.md` (case s4, XOM); `adapters/zetaris.py` (`CONCEPT_ALIASES`).

## B-12: Peers come only from registered entities; a non-SEC company gets no industry
Found in: #12  ·  Status: open  ·  Severity: later
- Out of scope because: the catalog has no SIC index (SEC publishes SIC per registrant in its submissions, not a list by code), so the scope check reads the SIC of the asked entity and of at most `MAX_PEER_PROBES` registered entities. A company outside SEC has no SIC in our data at all; proposing its industry needs a model (Nightshift: the model proposes an industry or peers, the agent validates each against filed data, the model never answers).
- Safe to leave because: the alternatives that are offered are each validated; with no peer the check still offers registrants named in the question and the same entity's past period, and the `note` says why there are no peers.
- Urgent if: the demo or a judge expects same-industry peers for a non-SEC company (e.g. a hardware maker outside SEC) or for an industry with no registered entity yet.
- Review at: when Nightshift lanes land
- Evidence: `services/agent/src/tiresias_agent/agents/scope.py` (`alternatives`, `_sic`, `MAX_PEER_PROBES`); `zetaris/sql/38_registrant_profile.sql`.

## B-13: Suggestions and alternatives for an unregistered registrant are thin
Found in: #12  ·  Status: open  ·  Severity: judge-visible
- Out of scope because: suggestions are derived from the company's own companyfacts, which exist only once it is registered (12–16 s cold), and a GET should not register sources. `GET /entities/{cik}/suggestions` therefore answers 409 `EntityNotRegistered` for a searchable entity, and a scope-check alternative about an unregistered registrant takes its fiscal year from its submissions (two 10-K report dates) without checking that the metric is present.
- Safe to leave because: the 409 says what to do (ask any question, which registers it, then call again); an alternative whose metric turns out to be missing ends at `evidence-frontier` (`MetricNotFound`) with the reason, never an invented value.
- Urgent if: a UI needs suggestions for a company before its first run (add an explicit `register=true`, behind the source gate and `MAX_NEW_SOURCES_PER_RUN`).
- Review at: when the UI picker is built
- Evidence: `services/agent/src/tiresias_agent/entities.py` (`_refuse`); `agents/scope.py` (`_alternative`, `_fiscal_year`).

## B-14: The scope check's text rules are English keyword rules
Found in: #12  ·  Status: open  ·  Severity: judge-visible
- Out of scope because: forecast/what-if detection (`forward_cue`) and named-company extraction (`name_phrases`: capitalised phrases matched to exactly one CIK in `sec_tickers`) are fixed rules (ADR-0007: rule-based without a model). They miss a lowercase company name, a brand shared by several registrants, or a forward question phrased without a cue word; parsing free text with a Nightshift model is planned (model proposes, agent validates).
- Safe to leave because: false positives are kept low by design and tested (`tests/test_scope.py`); a missed forward question still runs and ends on the evidence (usually `MetricNotFound` or `NoEffect` for a future period, which the period-after-today rule also catches); an unmatched name just yields no alternative.
- Urgent if: judges type free-form questions in other phrasings, or the UI accepts questions without a structured entity.
- Review at: when Nightshift lanes land, or the UI accepts free-text questions
- Evidence: `services/agent/src/tiresias_agent/agents/scope.py` (`_STRONG`, `_WEAK`, `_PAST_FRAME`, `_NOT_A_NAME`); `tests/test_scope.py` (`FORWARD`, `IN_SCOPE`).

## B-16: One counterparty per disposal
Found in: #13  ·  Status: open  ·  Severity: later
- Out of scope because: the onboarder and reconciler take one entity per scout result; following two separations at one date (Hilton 2017: Hilton Grand Vacations and Park Hotels & Resorts, both new registrants named by Hilton's name in their Form 10s) needs a multi-entity add-back and two registrations against `MAX_NEW_SOURCES_PER_RUN`.
- Safe to leave because: the scout ranks the qualifying candidates (Form 10 naming, item 5.01, brand, distance), follows the best and says which also qualified ("also qualified, ranked lower: PK"); the unexplained part stays as the residual (Hilton: 63%) and a tie on every signal ends at `evidence-frontier`.
- Urgent if: a judge's restatement comes from two or more simultaneous spin-offs and the residual is read as unexplained.
- Review at: after submission
- Evidence: `source_scout.Assessment.rank`; Hilton rows in `docs/measurements/live-any-company.md`.

## B-17: Full-text search bounds and blind spots
Found in: #13  ·  Status: open  ·  Severity: later
- Out of scope because: each search is one Zetaris REST table over one page (at most 100 matching documents, `FULLTEXT_LIMIT` = 20 filers), and only the source's name in force at the disposal is searched. Separations registered on another form (an IPO carve-out on S-1, an exchange offer or Reverse Morris Trust on S-4) are found only by the 8-K fallback; a counterparty no longer in `sec_tickers` (delisted, acquired) cannot be registered; sample mode has no full-text data, so it keeps the brand rule only.
- Safe to leave because: every miss is a refusal with the searched phrase and the candidates checked in `information_need.reason` and the trace (`source_scout` `candidates`); nothing is guessed, and every registration still goes through the `source` gate.
- Urgent if: a judge asks about a carve-out IPO or a split-off by exchange offer, a spin-off whose company was later acquired, or a parent with a very common name (the page fills with passing mentions).
- Review at: after submission
- Evidence: `catalog/sources.yaml` (`sec_fulltext`); `source_scout._named`, `REGISTRATION_FORMS`; `tests/test_spinoff_match.py`.

## B-18: A registration that fails with an empty HTTP 400 is not retried
Found in: #13  ·  Status: open  ·  Severity: demo-risk
- Out of scope because: in the 2026-10-23 live pass, DuPont's first registration (`bootstrap:submissions_table`) took 79 s and Zetaris answered HTTP 400 with an empty message; the client then treated every 4xx as non-recoverable, so the run ended at `evidence-frontier` while the next run registered it fine. Since fix/zetaris-catch-all an empty 4xx body is `unclassified`: recoverable, traced `cause: "unclassified"` with its status, so Loop B's registration degrades to the sample instead of failing (the asked entity's registration, which has no sample, still ends at `evidence-frontier`, now marked an outage). It is still not retried: a retry of a statement that already took 79 s would spend most of the run budget, and whether this reply was a Zetaris-side fetch timeout is unmeasured.
- Safe to leave because: the run ends at `evidence-frontier` with the error in the reason; nothing is served from the sample, and a second run succeeds (registration is idempotent).
- Urgent if: cold registrations fail this way more than occasionally, or during a demo.
- Review at: before recording (Tue 27 Oct): warm every demo company first
- Evidence: `adapters/zetaris.py` (`classify_reply`; `run_sql` retries only `cause == "outage"`); `tests/test_zetaris_matrix.py::test_unclassified_is_an_outage_traced_with_status_and_excerpt[empty-body]`; DuPont cold row in `docs/measurements/live-any-company.md`.

## B-19: Loop A's critic also proposes `accept-capped` for any over-explaining component
Found in: #15  ·  Status: open  ·  Severity: later
- Out of scope because: #15 bounds Loop B's add-back, where an over-explanation means the wrong counterparty; in Loop A a component that explains more than 100% means offsetting factors inside the same entity (`agents/critic.py`, "it over-explains, so offsetting factors exist"), which can be legitimate, so the same bound needs its own reasoning and tests.
- Safe to leave because: Loop A only measures the asked entity's own filed figures, so a gross over-explanation is real arithmetic on its own numbers, not a mismatched entity; the reason text and the `critic_escalation` trace line state the raw share, and a scripted or interactive gate can prune it.
- Urgent if: a Loop A link ends `actionable` with a raw share far above 100% (for example over 150%) on a question a judge or the demo asks, or the 100%-capped share is shown without the raw one.
- Review at: demo rehearsal (Mon 26 Oct): check the demo questions' raw shares
- Evidence: `services/agent/src/tiresias_agent/agents/critic.py` (`"proposal": "accept-capped"` under `raw > OVER_EXPLAINED`); `MAX_OVER_EXPLAINED` in `config.py` (#15).

## B-21: Discovery tables are never dropped
Found in: housekeeping (24 Oct)  ·  Status: open  ·  Severity: later
- Out of scope because: found while moving the old AGENTS.md "Known gotchas" list into zetaris/README.md; `bootstrap` never drops anything by design (idempotent, safe to re-run), and a clean-up needs its own rule for which `<ticker>_submissions_raw` and `fts_<hash>_raw` tables are still in use.
- Safe to leave because: each scout run adds at most two full-text tables and `MAX_CANDIDATES` submissions tables, and later runs reuse them; none counts toward `MAX_NEW_SOURCES_PER_RUN`. The growth is in the local Zetaris only.
- Urgent if: the Zetaris table list slows queries or registration, or a shared Zetaris is used by others.
- Review at: after submission.
- Evidence: [zetaris/README.md](zetaris/README.md) ("Discovery goes through Zetaris too"); `adapters/catalog.py` (`ZetarisDiscovery`).

## B-24: Cached companyfacts tables can run the Zetaris driver out of heap
Found in: fix/b20-cache-warmup (24 Oct)  ·  Status: open  ·  Severity: later (was demo-risk; bounded in fix/zetaris-resilience)
- Out of scope because: fix/zetaris-resilience (this PR) bounds it: at most `ZETARIS_MAX_CACHED_FACTS` (5) companyfacts tables cached per agent process, the least recently used one `UNCACHE`d first (never the run's asked entity or Loop B counterparty), `warm` counted toward the same cap, discovery tables uncached after the scout, a dead-engine HTTP 400 treated as an outage (sample fallback, `degraded`) and a liveness probe before each live run. What remains needs Zetaris itself or a live experiment: the cap only knows the tables this process cached (not those cached before it started or by another process); the heap per table could be cut at the source by caching a narrow projection instead of the raw companyfacts row (triage fix A1, experiment E3); the probe and the dead-engine signatures are unverified against a real stopped driver (E5); the driver lacks `-XX:+ExitOnOutOfMemoryError` (vendor bundle).
- Safe to leave because: with the cap a process holds about 5 x 150 MB of companyfacts heap, under the 7 to 10 tables that filled the 2 GB heap (being raised to 4 GB); a dead engine now degrades runs to the sample instead of erroring them; restarting the driver container (`docker restart zetaris-driver-1`) restores Zetaris, and registrations survive it.
- Urgent if: the driver runs out of heap again with the cap in place, or a stopped SparkContext answers with a body the signatures miss (since fix/zetaris-catch-all such a reply is `unclassified` and still degrades to the sample, but each call waits for its reply because the liveness probe calls an unclassified reply `inconclusive`, not down).
- Review at: before recording (Tue 27 Oct): restart the driver, run `warm`, watch `GET /health` `zetaris` during the rehearsal.
- Evidence: `docker logs zetaris-driver-1`, 2026-10-24 05:02:15 UTC: `java.lang.OutOfMemoryError: Java heap space` ... `stopping SparkContext` (G1 at 2046M of 2048M), while `warm` cached `ibm_facts_raw` after ten other companyfacts tables; every later statement failed in about 0.1 s. The bound: `services/agent/tests/test_zetaris_resilience.py`.

## B-25: A 2xx object without `headers` or `data` still reads as "no rows"
Found in: fix/zetaris-catch-all (24 Oct)  ·  Status: open  ·  Severity: later
- Out of scope because: fix/zetaris-catch-all makes every unexpected 2xx shape `unclassified` (a non-object, `headers`/`data` that are not lists, a row that does not match the headers, a non-JSON body), but an empty body and an object with neither key are kept as an empty result, because that is how a successful DDL statement (CREATE, CACHE TABLE) is read and the real bundle's DDL reply has never been captured. Tightening it without that measurement could turn every live registration into a sample fallback.
- Safe to leave because: a query that should return rows and returns none ends its link as "not testable" or at the frontier with the reason, never with invented data; the error lines and the matrix tests cover every other shape.
- Urgent if: a live trace shows a `success` line with `rows: 0` for a SELECT that should have rows, while Zetaris's own UI shows an error for the same statement.
- Review at: after submission, or the next live pass with a packet capture of one DDL reply.
- Evidence: `adapters/zetaris.py` (`ZetarisClient._parse`: `payload.get("headers") or []`); `tests/test_zetaris_matrix.py` (2xx rows).

## B-26: A live run that falls back to sample data still reports `success` with no run-level flag
Found in: feat/real-outputs (25 Oct)  ·  Status: open  ·  Severity: judge-visible
- Out of scope because: found while producing `output_examples/`. A live run with a wrong `ZETARIS_REST_URL` (path missing, every call HTTP 404) degraded every read to the sample slice, as rule 8 requires, and ended `status: success`, `end_state: actionable`, with the same answer as a real live run. The fallback shows only per evidence item (`degraded: true` on 13 items) and in the trace (`degraded` lines); `RunResult` has no top-level field saying the run did not reach Zetaris, and the liveness probe called the 404 "refused, run goes ahead live". Adding a run-level field changes the contract (`contracts/openapi/agent.yaml`) and the UI, which is a feature change, not part of committing outputs.
- Safe to leave because: sample mode, the judge's default, is labelled (`sample_mode: true`, `mode: sample` on every trace line); a live run's fallback is in its trace and on each evidence item, never hidden, and the data it falls back to is the real cached SEC slice.
- Urgent if: a judge or the demo runs live mode, or the UI shows a live result without reading the evidence items.
- Review at: UI integration (the UI should show a "served from sample" badge), or before the demo rehearsal (Mon 26 Oct).
- Evidence: a live run of `input_examples/04_3m_2023_revenue_restated.json` with `ZETARIS_REST_URL=http://localhost:8888` (no `/api/v1.0`): 20 `error` and 15 `degraded` trace lines, result `success`/`actionable`; `RunResult` in `services/agent/src/tiresias_agent/models.py` (no run-level `degraded`).

## B-27: The agent test suite reads a running cognition service
Found in: feat/real-outputs (25 Oct)  ·  Status: open  ·  Severity: later
- Out of scope because: with `docker compose up` running, `pytest` fails 16 tests in `test_depth.py` and `test_model_lanes.py`: their sample runs use the default memory mode (warm) and the default `COGNITION_URL`, so they recall lessons that earlier runs stored in the live H-MEM service and the result shape changes. With the stack stopped all tests pass. The fix (tests default to `MEMORY_MODE=cold` or an unreachable `COGNITION_URL` in a conftest fixture) is a test change, not part of committing outputs.
- Safe to leave because: CI runs pytest without the stack, and the failures are in the tests' environment, not the agent; `tests/test_memory.py` covers memory with its own fake service.
- Urgent if: a judge or reviewer runs pytest with the stack up and reports failures.
- Review at: the final README pass (state "stop the stack before pytest" or land the fixture).
- Evidence: `docker compose up` then `cd services/agent && uv run --group dev pytest -q` gives 16 failed, 578 passed; after `docker compose stop`, 594 passed.

## B-28: On an empty Zetaris the first entity read is served from the sample
Found in: feat/real-outputs (25 Oct)  ·  Status: open  ·  Severity: later
- Out of scope because: in the fresh-state live run (`logs/samples/trace-run-20261024T233153Z-c3227e.jsonl`) the first read, `SHOW LIGHTNING TABLES TIRESIAS_SEC`, answered HTTP 400 "Lightning Database 'TIRESIAS_SEC' doesn't exist"; the catch-all classes that as `unclassified`, so the registered-entity list for that read came from the sample (`degraded`). The run then registered the database and every source itself and answered from Zetaris. Reading "doesn't exist" as an empty list is a classifier change in `adapters/zetaris.py` plus a matrix test.
- Safe to leave because: it happens once per Zetaris install (before the first registration), the line is traced, and entity resolution goes through the Zetaris ticker table, not that list.
- Urgent if: a judge's first live run on a new bundle needs the entity list from Zetaris (`GET /entities` live on an empty bundle lists the sample's companies).
- Review at: after submission.
- Evidence: lines 6–7 of the trace above; `classify_reply` in `adapters/zetaris.py`.

## B-29: The two committed live traces do not carry the output's `root_cause`

- **Found in:** #33, round 3 (review of PR #33 against C1).
- **Evidence:** the reporter's `report` line in `trace-run-20261024T233153Z-c3227e.jsonl` and `trace-run-20261024T230646Z-45973c.jsonl` has `end_state` and `residual` but no `root_cause`; the reporter now traces it (`tests/test_report_trace.py`) and the four sample outputs were re-run, but a trace cannot be edited (rule 1), so the live pair can only be fixed by new runs.
- **Severity:** low. The `end_state`, `residual` and the root cause's figures (`output_summary`) are on the line; only the full sentence is unverifiable from the trace. `output_examples/README.md` says so.
- **Why not in #33:** the fix is two new live runs, one from a fresh Zetaris: that needs every Tiresias object in Zetaris dropped (a destructive step the human runs) and the Nightshift VM, and it replaces the trace C3 and C8 are pinned to.
- **Review point:** before submission, or if a judge or reviewer cites the live root cause as unverified; re-run both live cases and update C3/C8's run id.

## B-31: Source scout can register a wrong counterparty (name/brand match), which the critic then rejects
Found in: coverage sweep (feat/coverage-sweep)  ·  Status: open  ·  Severity: judge-visible
- Out of scope because: the sweep measures only. RTX FY2018 → the scout picked nVent Electric (a Pentair spin-off) and IP FY2023 → "IP STRATEGY HOLDINGS, INC." ("carries the source's brand"); both were registered in Zetaris behind the auto source gate, then the critic found no reconciling add-back and both ended at evidence-frontier.
- Safe to leave because: no wrong answer was accepted; the cost is one wasted registration per run (bounded by MAX_NEW_SOURCES_PER_RUN).
- Urgent if: a wrong counterparty's figure happens to fall inside tolerance (a false reconciliation), or a demo uses RTX or IP.
- Review at: after submission (scout ranking: require the parent to be named in the candidate's Form 10, down-rank brand-only matches).
- Evidence: docs/measurements/coverage-sweep-2026-10-26.md (RTX, IP rows).

## B-32: The period label follows the end date, so a 52/53-week year is labelled one year late
Found in: fix/b30-unmeasured-acceptance (B-30 investigation)  ·  Status: open  ·  Severity: judge-visible
- Out of scope because: the B-30 fix changes the analyst's measurement text and the reporter's citation only; relabelling periods touches the framer, the suggester and every question text.
- Safe to leave because: the dates and values are right and every question names both filings; only the "FY" label is off. JNJ's year to 2022-01-02 is called "FY2022" (JNJ calls it fiscal 2021).
- Urgent if: a demo or judge question lands on a 52/53-week filer (JNJ, many retailers) and the label is compared with the company's own "fiscal year".
- Review at: after submission (use the XBRL `fy` of the baseline fact, or show the period dates).
- Evidence: docs/measurements/coverage-sweep-2026-10-26.md (Post-fix rerun, JNJ); `Frame.period_label`.

## B-33: A disposal measured on the parent's own fact still says "keep the residual open until … reconciled"
Found in: fix/b30-unmeasured-acceptance  ·  Status: open  ·  Severity: judge-visible
- Out of scope because: the action template for a Loop A disposal is shared with the unmeasured case; rewording it changes committed example outputs, which needs a separate, reviewed change.
- Safe to leave because: the measurement and the 0% residual are shown right next to the action; the sentence asks for a check that is already done, it does not claim anything false.
- Urgent if: a judge reads the action for JNJ or MRK and asks why a 0% residual is "open".
- Review at: after submission.
- Evidence: docs/measurements/coverage-sweep-2026-10-26.md (Post-fix rerun); `agents/knowledge.py` `ACTIONS[("portfolio_change", "model")]`.

## B-34: A Loop A disposal action lists an acquisition filing as a candidate disposal
Found in: docs/sweep-correctness-audit  ·  Status: open  ·  Severity: judge-visible
- Out of scope because: found by the independent audit (no code change in that PR); the fix belongs to the reporter/analyst event filter and changes example outputs only where several 2.01 filings match.
- Safe to leave because: the figures and the measured 100% share are right, and the action presents the filings as "one of 2 filings in the window", not as the cause.
- Urgent if: a judge reads JNJ's action: it offers the 8-K of 2022-12-22, whose item 2.01 is J&J's tender-offer acquisition of Abiomed, as a possible disposal next to the Kenvue separation (2023-08-25).
- Review at: after submission.
- Evidence: docs/measurements/coverage-sweep-2026-10-26.md (Correctness audit); coverage-sweep-2026-10-26-audit.jsonl, JNJ `e.cited` (`item_2_01_is_an_acquisition: true`).

## B-35: Suggested restatement questions can compare two different revenue concepts
Found in: docs/sweep-correctness-audit  ·  Status: open  ·  Severity: judge-visible
- Out of scope because: found by the audit; the suggester's concept matching is product code.
- Safe to leave because: the investigation of such a question ends at the evidence frontier and asserts no cause.
- Urgent if: a judge picks PFE's suggestion: "$50.91B vs $59.55B for FY2023" sets `RevenueFromContractWithCustomerExcludingAssessedTax` in one 10-K against `Revenues` in the next; the first 10-K's own `Revenues` for the year is $58,496M.
- Review at: after submission.
- Evidence: coverage-sweep-2026-10-26-audit.jsonl, PFE (`same_concept: false`).

## B-36: Separations not filed under 8-K item 2.01, or with two counterparties, stop at the frontier though XBRL could reconcile them
Found in: docs/sweep-correctness-audit  ·  Status: open  ·  Severity: judge-visible
- Out of scope because: a recall improvement to Loop A's event search and Loop B's single-counterparty add-back, not a fix for a wrong answer.
- Safe to leave because: the agent stops without asserting a cause, and says what is missing.
- Urgent if: a judge tries Danaher (Veralto), Becton Dickinson (Embecta) or RTX (Carrier + Otis): each counterparty's own figure would reconcile within 0.3% of the original, but the run ends at the evidence frontier.
- Review at: after submission.
- Evidence: docs/measurements/coverage-sweep-2026-10-26.md (Correctness audit, frontier table); audit JSONL `would_reconcile: true` for DHR, RTX, BDX.

## Closed

- **B-1** (found in #5, closed in #7): Agent Docker image can't see `data/sample/` or `zetaris/sql/`: image built from the repo root, allow-list `.dockerignore`, `tests/test_packaging.py`.
- **B-3** (found in #6, closed in #9): Loop A not yet run against live Zetaris: three examples and seven other companies run live, [docs/measurements/live-any-company.md](docs/measurements/live-any-company.md).
- **B-4** (found in #8, closed in #9): Live Loop B discovery reads SEC directly: discovery is Lightning SQL through Zetaris only ([zetaris/README.md](zetaris/README.md)), guarded by `tests/test_any_company.py`.
- **B-5** (found in #8, closed in #13): Source scout resolves only same-brand separations: the counterparty comes from Form 10 full-text evidence, with an 8-K fallback that refuses a sale to an existing company; `tests/test_spinoff_match.py`. Remaining limits: B-16, B-17.
- **B-7** (found in #8, closed in #9): Loop B not yet run against live Zetaris: examples 01 and 02 and Hilton reconcile live, [docs/measurements/live-any-company.md](docs/measurements/live-any-company.md).
- **B-9** (found in #9, closed in #18): "already exists" registration steps were traced as `status: error`: `ZetarisClient.run_sql(..., expected=)` traces a benign reply `success` with `outcome` `exists`/`absent` (bootstrap and discovery registration); `tests/test_zetaris.py`, `tests/test_any_company.py`.
- **B-15** (found in #13, closed in #15): Loop B accepts an add-back that over-explains far beyond tolerance: bounded by `MAX_OVER_EXPLAINED` (150%), beyond it the gate proposes `prune`; [docs/causal-criteria.md](docs/causal-criteria.md) §7, `tests/test_loop_b.py`.
- **B-20** (found in housekeeping (24 Oct), closed in #27): Zetaris lost its table cache on restart and the first query could time out (3M: three 20 s timeouts, then the sample): `python -m tiresias_agent.adapters.zetaris warm` re-caches the raw tables of `ZETARIS_WARM_ENTITIES` (default 3M, Solventum, the GE showcase) first, one at a time, with a 120 s statement timeout, and the API runs it in the background on a live start (`ZETARIS_WARM_ON_START`, `GET /health` `zetaris_cache`); [zetaris/README.md](zetaris/README.md#apply), `tests/test_warm.py`. Heap limit found on the way: B-24.
- **B-22** (found in the verifier audit #20, closed in #25): AGENTS.md rule 4 and the parallel-PR rule disagreed on who updates the status docs: rule 4 now says the same commit for a change that lands alone, the coordinator right after the merge for parallel PRs.
- **B-23** (found in the verifier audit #20, closed in #25): Cursor read `planned` in metadata.json and `optional` elsewhere: README and sponsor-technology.md now say `planned` and why (the schema has no "optional").
- **B-30** (found in #46, closed in fix/b30-unmeasured-acceptance): the JNJ/MRK share was measured after all, on each parent's own `DisposalGroupIncludingDiscontinuedOperationRevenue` fact (the first write-up said asserted); the real defect was JNJ's action citing the oldest of two 8-K 2.01 filings (the Abiomed acquisition, not the Kenvue separation). The measurement now names the fact it rests on, and several matching filings are all named; `tests/test_b30_disposal.py`, [coverage-sweep post-fix rerun](docs/measurements/coverage-sweep-2026-10-26.md#post-fix-rerun-b-30).
