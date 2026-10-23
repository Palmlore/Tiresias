# AGENTS.md: handover for coding agents (Claude Code is the main driver; Codex reviews via DuelLoop)

**Tiresias**: an evidence-backed "5 Whys" root-cause agent for financial analysts on SEC filings. Open Agent Hackathon 2026, track *Reasoning Architecture* (ADR-0009). Solo entry by Palmlore. Repo: github.com/Palmlore/Tiresias (public, Apache-2.0).

**Deadline: 27 Oct 2026 23:45 UTC (28 Oct 10:45 AEDT).** Hard deadline. Submit with hours to spare.

## Read first (in this order)
0. `../workspace/HANDOVER.md`: **start here each session.** It is private, outside this repo (a local git repo with no remote), and covers environment state, how to start it, leftover tasks and next steps. Working data, pinned upstream clones and internal notes live in `../workspace/` too, never in this public repo and never in a temp scratchpad.
1. [docs/CHECKLIST.md](docs/CHECKLIST.md): the progress checklist, cross-referenced to every event requirement. **Tick items only when evidence exists.**
2. [docs/STATUS.md](docs/STATUS.md): open questions and who owns them.
3. [docs/architecture.md](docs/architecture.md), [docs/causal-criteria.md](docs/causal-criteria.md), [docs/decisions/](docs/decisions/) (ADR-0001..0009): the agreed design. Don't re-decide these without a new ADR.
4. [docs/RUBRIC.md](docs/RUBRIC.md): judging criteria → evidence in the repo.
5. Event reference pack (rules, sponsor guides, research notes): `../docs/` (outside this repo). Start with `../docs/MAP.md`, `../docs/SPONSOR_STACK.md`, `../docs/ideas/01-five-whys-feasibility.md`.

## Layout
| Path | What |
|---|---|
| `services/agent/` | Python 3.11+ / FastAPI (uv). Built: `trace.py`, `models.py`, `config.py`, `gates.py`, `orchestrator.py`, `runs.py`, `app.py`, `agents/`, `adapters/` (see Building blocks). **Loop A + Loop B (`run_investigation`) run as a library, a CLI and over HTTP (sample mode): `POST /run` (sync), `POST /runs` + `GET /runs/{id}` (async) and `GET /runs/{id}/events` (SSE) work; `/runs/{id}/branch` and `/memory/purge` return a structured 501.** |
| `services/cognition/` | TypeScript / Node ≥22.18, no npm dependencies. H-MEM memory over REST. **Working.** |
| `services/cognition/engine/hmem/` | Vendored Meterless H-MEM reference (commit `1742dec`). **Never edit**; wrap it in `src/`. |
| `contracts/openapi/` | REST contracts. Update them in the same change as the code. |
| `catalog/sources.yaml` | Bounded source catalog: the only sources the agent may discover |
| `zetaris/sql/` | Lightning SQL templates (one statement per file, `{{placeholders}}`) that register the showcase entities; see [zetaris/README.md](zetaris/README.md) |
| `input_examples/` | 3 GE cases (inputs) |
| `output_examples/`, `logs/samples/` | Real outputs and traces from real runs only (never read at runtime) |
| `scripts/` | `check_secrets.sh`, `validate_metadata.sh`, `zetaris_probe.py`, `fetch_sample.py` |
| `data/sample/` | Offline SEC slice for sample mode (GE, GEHC, GEV), with `PROVENANCE.json` |
| `BACKLOG.md` | Findings deferred out of a PR, with reason, bound and trigger |
| `metadata.json` | Sponsor metadata (community schema 0.1). Keep statuses honest. |

## Building blocks (reuse these; don't rebuild them)
All under `services/agent/src/tiresias_agent/`. Each sponsor client takes an optional `tracer` and degrades instead of crashing (rule 8).

| Module | Use it for |
|---|---|
| `trace.py` | `Tracer(run_id=…, defaults={…}, sink=None)` → `.emit(agent=, action=, status=, **fields)` writes one JSONL line (stdout + `logs/trace-<run_id>.jsonl`); `defaults` are added to every line (Loop A sets `mode="sample"`/`"live"`, ADR-0007); `.span(...)` times a block. Statuses: `success`, `error`, `rejected`, `degraded`. Secret-like keys and Bearer tokens are redacted. `sink` (optional callable) also receives each redacted JSONL line; a failing sink is reported on stderr and never breaks the run. `NullTracer` for tests. |
| `models.py` | Pydantic models for `contracts/openapi/agent.yaml`: `RunRequest`, `RunResult` (leads with `end_state`, `action`; carries `effect`, `information_need`, `why_tree` of `Link`s with `evidence: [EvidenceItem]`, `error`), `InformationNeed` + `EntityHint` (the Loop A → Loop B hand-off), `BranchRequest`, `RunStats`, `ErrorResponse`, … |
| `config.py` | `settings` (env flags) and `LoopBudgets.from_env()`: `MAX_CAUSAL_DEPTH` (5), `MAX_BRANCHES_PER_LEVEL` (3), `MAX_RETRIES_PER_NODE` (1), `RUN_BUDGET_SECONDS` (600, shared by Loop A and B), `MAX_NEW_SOURCES_PER_RUN` (2, clamped 0..5, field `max_new_sources`), read at call time and clamped. |
| `orchestrator.py` | **Entry point: `run_investigation(request, **same kwargs as run_loop_a, discovery=None, zetaris_client=None, data_dir=None)` → `RunResult`**: Loop A, then `LoopB` when Loop A ends at `evidence-frontier` with an `InformationNeed` and a framed effect (otherwise a `loop_b` trace line says why it was skipped). **Loop A only:** `run_loop_a(request, *, source=None, tracer=None, gate=None, budgets=None, logs_dir=None)`. Neither raises for a bad case (structured `error`, or end state `budget`). Loop A: framer → (hypothesiser → analyst ⇄ critic [→ gate])* → gap analyst → reporter; stops at the first accepted causal link the asker's levers can act on, digs under the strongest breakdown otherwise. Loop B: source scout → onboarder [source gate] → `reconciler` (analyst + critic) → the same asker-relative stopping rule (`actionable` / `branched` / `external-driver`); an unresolvable hint, a gate denial, a failed registration or missing data keeps `evidence-frontier` with `Loop B: <why>` appended to `information_need.reason`; the source cap or the wall clock gives `budget`. CLI below. |
| `runs.py` | Run registry behind the HTTP API. `RunRegistry().start(request, run_id=None)` → `RunRecord` (runs in a daemon thread; raises `RunConflict` for a duplicate id, `RegistryBusy` beyond `MAX_CONCURRENT_RUNS`, default 4); keeps the last `RUNS_REGISTRY_SIZE` runs (default 50, oldest finished evicted) and up to `MAX_TRACE_LINES_PER_RUN` lines each (default 5000; the trace file keeps all). `RunRecord.wait(timeout)`, `.follow()` (replay then live lines, for SSE), `.summary()` (the `GET /runs/{id}` body). Adds `api` trace lines `run_accepted` / `run_finished` / `run_failed`. **`runs.execute(...)` is the one place the API calls the pipeline** (`orchestrator.run_investigation`). |
| `app.py` | FastAPI app. `POST /run` = `registry.start` + wait up to `RUN_BUDGET_SECONDS` + 30 s (`runs.wait_budget_seconds()`); `POST /runs` (202), `GET /runs/{id}`, `GET /runs/{id}/events` (SSE: `event: trace` per JSONL line, `: keepalive` every 15 s idle, one final `event: end`). Every error is the contract's `Error` shape via `error_response(...)`: 422 `ValidationError` (body, JSON or `X-Run-Id`), 404 `RunNotFound`, 409 `RunConflict`, 429 `TooManyRuns`, 500 (pipeline raised), 504 `RunTimeout`, 501 `NotImplemented`. `X-Run-Id` (`[A-Za-z0-9._-]{1,100}`) is honoured and echoed. CORS is off unless `CORS_ALLOW_ORIGINS` is set. |
| `agents/framer.py` | `frame(request, source, tracer)` → `Frame`: a like-for-like effect. `restatement` when `compare.filing_a/filing_b` are `"<form> filed YYYY-MM-DD"` (same period, two filings), else `change` (period vs prior period, both from the first annual filing reporting the period). Raises `FrameFailure` (metric not in sources → evidence-frontier) or `LoopError`. |
| `agents/hypothesiser.py` | Rule-based (ADR-0007). `propose(frame, parent, path_variables, max_branches=, tracer=, exclude=)` → `Candidate`s from generic rules in `agents/knowledge.py`: hierarchy children, event concepts (goodwill impairment), 8-K items (2.01 disposal, 4.02 non-reliance / 10-K/A), concept switch, restated comparative. No cycles, capped. |
| `agents/analyst.py` | `measure(candidate, frame, source, attempt=, scope=, tracer=)` → `Measurement`: queries only through `EvidenceSource`, keeps SQL + filter + rows per query, computes the candidate's effect and the counterfactual (intervention re-query on the returned rows). Scopes: `broad` → `window` → `relaxed` (set by the critic). |
| `agents/critic.py` | Pure rules over a `Measurement`: `judge(candidate, m, frame, retries_left=)` → `Verdict` (`accept` / `prune` / `retry` / `frontier`, label, evidence status, share vs the root effect); `apply_gate(verdict, choice)`. Thresholds in `agents/common.py` (`MOST_OF_EFFECT`, `MIN_SHARE`, …). |
| `agents/gap_analyst.py` | `for_candidate(frame, node, tracer)` / `for_variable(...)` → `InformationNeed` (variable + aliases, entity hint from the run's own evidence, period, join key, expected magnitude, the intervention test to run). **Loop B consumes this.** |
| `agents/source_scout.py` | Loop B. `resolve(need, discovery=, tracer=)` → `ScoutResult(entity, template, reason, evidence, candidates)`. Rule-driven, catalog-only: the hint must name an 8-K 2.01 filing; candidates are `sec_tickers` rows carrying the source's brand (newest CIK first, ≤ `MAX_CANDIDATES`); a candidate qualifies if its `sec_submissions` show it became a separate registrant at that disposal (registrant by the date, no 10-K before it, its own 8-K within `SEPARATION_WINDOW_DAYS`, item 5.01 ranks higher); exactly one best match or it refuses. `assess()` and `pick_template()` (registrable XBRL template with the fewest registrations: `sec_companyfacts`) are pure. |
| `agents/onboarder.py` | Loop B. `onboard(scout, need, gate=, registry=, cap=, sample=, tracer=, client=None, data_dir=None)` → `Registration(status registered/degraded/denied/capped/failed, mode zetaris/sample)`: cap check → dry run (`bootstrap_plan(..., tables=("facts",))`, nothing executed) → `source` gate (options `register`/`deny`, proposal `register`) → live `bootstrap(...)` one statement at a time + view refresh, or a **sample-mode registration** (traced `registration: "sample"`, Zetaris not called). Outage → degraded to the sample; non-recoverable → failed. `Registry(source, asked)` is what the Loop B analyst queries: it raises `SourceError` for any entity not registered in this run. |
| `agents/reconciler.py` | Loop B analyst + critic. `measure(frame, need, entity, source, node_id=, tracer=)` → `Reconciliation` (the entity's first annual value of the metric for the period, `restated + disposed` counterfactual, residual amount); `judge(frame, r)` → (`Verdict`, signed residual share): accept `evidenced` if it explains ≥ `MOST_OF_EFFECT` and the add-back is within `RECONCILE_TOLERANCE` (5%) of the original, escalate when it over-explains beyond tolerance, prune below `MIN_SHARE`, frontier when the data or a restatement frame is missing. Traced as `analyst`/`critic` with `loop: "B"`. |
| `agents/reporter.py` | `build(...)` → `RunResult`; action text per (cause category, asker lever family) from `agents/knowledge.py` (`RECONCILED_ACTIONS` for a Loop B `reconcile` link, whose residual is signed). |
| `agents/knowledge.py` | Generic SEC/US-GAAP knowledge only (concept families and components, event concepts, 8-K item codes, lever families, action templates). Never entity-specific; a guard test greps agent logic for case literals. |
| `gates.py` | `Gate(mode, tracer=, policies=, scripted=, ask=)` → `.decide(GateRequest(gate=, question=, options=, proposal=, node_id=, requested_by=))` → `GateDecision`. Modes `auto` (policy, default: follow the proposal), `scripted` (`RunOptions.decisions["<gate>:<node_id>"]` or `["<gate>"]`), `interactive` (`ask` callable). Traced with `gate`, `gate_mode`, `decided_by`. Loop A uses `critic_escalation`; source and memory gates should reuse it. |
| `adapters/zetaris.py` | Evidence. `make_source(sample_mode, tracer)` returns an `EvidenceSource`: `SampleSource` (reads `data/sample/`) or, live, `FallbackSource(LiveZetarisSource, SampleSource)`, which falls back to the sample per call on an outage (timeout, connection error, 5xx after retries) and traces `degraded`; a non-recoverable `SourceError` (bad SQL, auth, malformed reply) is raised, never served from the sample. Methods return `Evidence` (a list of `Fact` / `Filing` / `Entity` carrying `.sql`, `.source`, `.degraded`). Concept aliases (`Revenues` ↔ `RevenueFromContract…`) resolve automatically. `bootstrap(client, entities=, concepts=, tables=)` registers sources from `zetaris/sql/` (idempotent, never drops; `tables` ⊆ `("facts", "submissions")`, failed entries carry `recoverable`); Loop B's onboarder reuses it. `refresh()` on the live/fallback source forgets the cached view list. CLI: `bootstrap`, `smoke [--sample]`. |
| `adapters/nightshift.py` | Models. `NightshiftClient(NightshiftConfig.from_env())` → `.chat(...)`, `.chat_json(...)` on lanes `fast` / `deep`; raises `ModelUnavailable(recoverable=…)` when offline; the receipt says which model served the call. CLI: `probe`. |
| `adapters/catalog.py` | The bounded catalog. `load_catalog()` parses `catalog/sources.yaml` (a strict YAML subset; anything else is `CatalogError`; `CATALOG_PATH` overrides). `make_discovery(sample_mode, tracer)` → `Discovery` with `.tickers()` (`sec_tickers`) and `.submissions(cik10)` (`sec_submissions`): `SampleDiscovery` (data/sample/) or, live, `FallbackDiscovery(LiveDiscovery, SampleDiscovery)` (SEC GET with `SEC_USER_AGENT`, timeout, retries, rate limit; outage → sample, `degraded`; non-recoverable raised). Discovery reads are not registered sources. |
| `adapters/cognition.py` | Memory. `CognitionClient` → `.recall`, `.remember`, `.feedback`, `.purge`, `.health` against `services/cognition`; raises `CognitionUnavailable` so the caller can run without memory. |

## Commands
```bash
# cognition (TypeScript)
cd services/cognition && npm test && npm run conformance   # 6 tests; H-MEM conformance 13/13
# agent (Python)
cd services/agent && uv sync && uv run --group dev pytest -q && uv run --group dev ruff check src tests
# whole stack (sample mode by default)
docker compose up --build            # agent :8000, cognition :8100 (sample mode works with an empty .env)
# HTTP API (Docker, or no Docker: cd services/agent && uv run python -m tiresias_agent.app)
curl -s -X POST localhost:8000/run -H 'Content-Type: application/json' -H 'X-Run-Id: demo-1' \
     -d @input_examples/01_ge_2022_revenue_restated.json            # RunResult JSON; trace in logs/trace-demo-1.jsonl
curl -s -X POST localhost:8000/runs -H 'Content-Type: application/json' -d @input_examples/03_ge_2018_net_loss.json   # 202 {run_id}
curl -s localhost:8000/runs/<run_id>                                 # {status: running|done, result, ...}
curl -sN localhost:8000/runs/<run_id>/events                         # SSE: one trace line per event, then event: end
# checks before every push
./scripts/check_secrets.sh && ./scripts/validate_metadata.sh
# Zetaris (local, outside this repo)
cd ~/Repos/hackathon2026/zetaris/zetaris-platform && ./verify.sh   # UI localhost:3000, REST :8888
python3 scripts/zetaris_probe.py     # latency and registration probe (reads .env)
cd services/agent && uv run python -m tiresias_agent.adapters.zetaris bootstrap   # register sources (needs ZETARIS_* and SEC_USER_AGENT)
cd services/agent && uv run python -m tiresias_agent.adapters.zetaris smoke [--sample]   # entities, revenue facts, filings
# Investigation (Loop A, then Loop B) on one request (sample mode by default; --live queries Zetaris with sample fallback; --loop-a stops after Loop A)
cd services/agent && uv run python -m tiresias_agent.orchestrator input_examples/01_ge_2022_revenue_restated.json [--live] [--loop-a] [--logs-dir DIR]
#   MAX_NEW_SOURCES_PER_RUN=0 shows the source cap (end state budget); scripted {"source": "deny"} shows the source gate
#   stdout: the RunResult JSON only; trace lines go to stderr and <repo>/logs/trace-<run_id>.jsonl
```

## External services (live mode only; sample mode needs none)
- **Zetaris**: local Docker bundle at `~/Repos/hackathon2026/zetaris/zetaris-platform` (2.4.3.1-hackathon). REST `http://localhost:8888/api/v1.0`, or `http://host.docker.internal:8888/api/v1.0` from containers. `POST /sql-editor/sqls/run` with `Authorization: Bearer`, `X-Org-ID: 1` and a fresh `X-Request-ID` UUID. `bootstrap` registers database `TIRESIAS_SEC`, container `tiresias`, raw REST tables `TIRESIAS_SEC.<ticker>_{facts,submissions}_raw` and views `tiresias.<ticker>_<concept slug>` / `tiresias.<ticker>_filings` for `ge`, `gehc`, `gev`. The older probe views `ge_revenue_cf` and `gehc_revenue` still exist but are unused.
- **Meterless Nightshift**: runs in an isolated macOS VM, because the installer is ad-hoc signed and not notarized. It's an OpenAI-compatible Runtime reached through hotspot mode at the VM's IP. Headers: `meterless-app: tiresias`, `meterless-scope: user`. Models: `meterless/fast`, `meterless/deep`. Timeout 180 s. The NVIDIA key lives **only** in Nightshift.

## How work lands (from 23 Oct 2026)
- **Every change goes through a pull request**, one task per branch (`feat/<short-name>`), file-disjoint from other open PRs so they can run in parallel. Process and housekeeping commits may go straight to `main`.
- The PR body follows [.github/pull_request_template.md](.github/pull_request_template.md): an `## ACCEPTANCE CRITERIA` section of `[C]` / `FALSIFIED-BY:` / `VERIFIER:` entries. Make each criterion testable by a named test or command.
- Each PR is reviewed by **DuelLoop** (`~/Repos/duelloop`, outside this repo): Codex as adversarial reviewer, Claude Code as worker, up to 5 rounds (`--rounds 5`). The coordinating session launches it backgrounded (`duelloop go --pr N --worker claude --reviewer codex --repo <worktree>`); run records live in `~/.duelloop/runs/`, never in this repo. The local `verdict.json` is the source of truth, not the PR label.
- On `MERGE_PERMITTED` the coordinator re-runs every verifier itself and merges (squash) only if they pass: `uv` panics inside the reviewer's macOS sandbox, so the reviewer probes behaviour but often cannot run the suite. Escalations go to the human.
- A PR that adds a feature, module, CLI or observability also updates **AGENTS.md** (Building blocks, Commands, gotchas) and, if a judge would see the change, **README**, in the same PR and as an acceptance criterion. Later tasks build on what is documented there.
- Docs-only PRs (status, checklist, README) go through DuelLoop too. Their criteria test **accuracy** (every claim matches the code and git history; nothing is marked working that a judge can't run) and **logical consistency** (CHECKLIST, STATUS, README, metadata.json and AGENTS.md agree with each other).
- Real findings that are out of scope are **deferred** to [BACKLOG.md](BACKLOG.md) in the same commit, with all four fields.
- Worker and reviewer rounds: commit to the PR branch only; don't push, open, approve or merge PRs.
- Parallel PRs don't edit `docs/CHECKLIST.md`, `docs/STATUS.md` or `metadata.json` (they would conflict); the coordinator updates them right after each merge.

## Rules (non-negotiable)
1. **No hardcoded or replayed outputs, no hand-written traces.** Judges test with their own inputs. Sample mode runs real logic on cached data.
2. **Every agent action writes a JSONL trace line** (schema: `docs/architecture.md#tracing`), propagating `X-Run-Id`.
3. **Secrets only in `.env`** (gitignored). Never print keys; mask them in logs and screenshots.
4. **Honest status.** `metadata.json` and the checklist say `working` or `done` only when a judge can see it run. Update `docs/CHECKLIST.md`, `docs/STATUS.md` and `metadata.json` in the same commit as the work.
5. **Destructive operations run alone.** Any `rm`, overwrite, `git reset`, `docker compose down -v` or memory purge goes in its own command with a one-line reason, never inside a larger script.
6. **Don't edit vendored code** (`services/cognition/engine/`). Wrap it.
7. Bound everything: timeouts on external calls, capped loops (`MAX_CAUSAL_DEPTH`, `MAX_BRANCHES_PER_LEVEL`, `MAX_NEW_SOURCES_PER_RUN`).
8. Sponsor calls go behind adapters with a fallback, so an outage degrades the run instead of crashing it.

## Known gotchas
- SEC `companyconcept` returned **empty** `units` for GE HealthCare on 22 Oct while `companyfacts` had the data. Use `companyfacts` with struct-path views (`facts.us-gaap.<Concept>.units.USD`). Don't explode the whole map (18 s).
- The same concept can appear under different names (`Revenues` vs `RevenueFromContractWithCustomerExcludingAssessedTax`).
- H-MEM recall threshold is 0.25 (`RECALL_THRESHOLD`). To show the critic rejecting a misapplied lesson, recall with a broad query.
- Node runs TypeScript with `--experimental-transform-types` (the vendored engine uses parameter properties).
- SEC requires `User-Agent` with a contact email (`SEC_USER_AGENT`), at ≤10 requests/s.
- Zetaris `CACHE TABLE` is lost when Zetaris restarts: re-run `bootstrap`, or the first uncached query takes ~15 s (the source timeout is 20 s).
- A concept view that fails with `FIELD_NOT_FOUND` means SEC has no such concept for that entity; `bootstrap` reports it `absent` and the adapter skips it.
- The agent image is built from the **repo root** (`compose.yaml`: `context: .`, `dockerfile: services/agent/Dockerfile`) and mirrors the repo under `/app` (`/app/services/agent`, `/app/data/sample`, `/app/zetaris/sql`, `/app/catalog`, `/app/input_examples`), so the package's repo-relative defaults resolve; `SAMPLE_DATA_DIR`, `ZETARIS_SQL_DIR` and `LOGS_DIR=/app/logs` are also set. The root `.dockerignore` is an **allow-list**: a new directory the image needs must be added there and to the Dockerfile (`tests/test_packaging.py` checks both). The `RunResult.trace_file` path is the container path; on the host it is `logs/` (mounted).
- The run registry is in memory and per process: `GET /runs/{id}` forgets runs after a restart or once `RUNS_REGISTRY_SIZE` newer runs finished (trace files stay in `logs/`). A run that outlives `POST /run`'s wait (504) keeps running in its thread; poll `GET /runs/{id}`.
- `POST /run` returns 200 for any run that ended (including `RunResult.status: "error"` from a controlled `LoopError`); 500 only when the pipeline itself raised.
- The Zetaris admin defaults to `admin@zetaris.com` (change the password in the UI).
- Loop A only queries the **asked entity** (its concept series and filings). Evidence that lives in another entity (e.g. a spun-off business) is an `InformationNeed` for Loop B, even if that entity happens to be in `data/sample/` or registered in Zetaris.
- A restatement of a closed period is caused by an event **after the period ends and no later than the restating filing** (`Frame.window()`); a change is caused during the period. The critic's temporal test uses this window.
- Shares are measured against the **root** effect and capped at 100%; a candidate that over-explains (offsetting factors) goes through the critic-escalation gate.
- Loop B's sample-mode "registration" is not a Zetaris call: it checks that `data/sample/<ticker>_companyfacts.json` holds that CIK and lets the `Registry` serve it; the trace says `registration: "sample"`. In live mode it runs `bootstrap` for the one entity (facts table + concept views only).
- The source scout's candidate pre-filter is the brand (first word of the ticker-map title equals the source's ticker or first title word). A disposal to an existing company, or into a registrant with a new brand, is refused and stays `evidence-frontier` (BACKLOG B-5).
- A Loop B residual is **signed**: case 02's add-back over-explains (negative residual). Shares stay capped at 100%.
- Live Loop B discovery GETs the SEC catalog endpoints directly, not through Zetaris; the evidence the analyst reconciles still goes through Zetaris (BACKLOG B-4).
- The CLI resolves a relative request path against the cwd, then the repo root, and writes traces to `<repo>/logs/` unless `LOGS_DIR` or `--logs-dir` is set.

## Next work (see docs/CHECKLIST.md for the full list)
1. Run Loop B live against Zetaris end to end (BACKLOG B-7).
2. Nightshift client in live mode once the VM's Runtime URL and token are in `.env` (model-backed hypothesiser/critic lanes).
3. Memory demo (cold → warm → purge → cold) through `adapters.cognition` (recall must degrade to no memory), asker profiles, branching.
