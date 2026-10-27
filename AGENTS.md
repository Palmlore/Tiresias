# AGENTS.md: handover for coding agents (Claude Code is the main driver; Codex reviews via DuelLoop)

**Tiresias**: an evidence-backed "5 Whys" root-cause agent for financial analysts on SEC filings. Open Agent Hackathon 2026, track *Reasoning Architecture* (ADR-0009). Solo entry by Palmlore. Repo: github.com/Palmlore/Tiresias (public, Apache-2.0).

**Deadline: 27 Oct 2026 23:45 UTC (28 Oct 10:45 AEDT).** Hard deadline. Submit with hours to spare.

## Read first
0. `../workspace/HANDOVER.md`: **start here each session.** It is private, outside this repo (a local git repo with no remote), and covers environment state, how to start it, leftover tasks and next steps. Working data, pinned upstream clones and internal notes live in `../workspace/` too, never in this public repo and never in a temp scratchpad.

Open the rest only when the task needs them:
- [docs/agents-reference.md](docs/agents-reference.md): every module's API (Building blocks), the run phases for a UI, external service details. **Read the row for a module before you change or reuse it.**
- [BACKLOG.md](BACKLOG.md): open gaps with severity and review point.
- [docs/CHECKLIST.md](docs/CHECKLIST.md) (event requirements; **tick items only when evidence exists**) and [docs/STATUS.md](docs/STATUS.md) (open questions): for status work.
- [docs/architecture.md](docs/architecture.md), [docs/causal-criteria.md](docs/causal-criteria.md), [docs/decisions/](docs/decisions/) (ADR-0001..0011): the agreed design. Don't re-decide these without a new ADR.
- [docs/RUBRIC.md](docs/RUBRIC.md): judging criteria → evidence in the repo.
- Event reference pack (rules, sponsor guides, research notes): `../docs/` (outside this repo). Start with `../docs/MAP.md`.

## Layout
| Path | What |
|---|---|
| `services/agent/` | Python 3.11+ / FastAPI (uv). Built: `trace.py`, `models.py`, `config.py`, `gates.py`, `orchestrator.py`, `runs.py`, `app.py`, `agents/`, `adapters/` (see Building blocks). **Loop A + Loop B (`run_investigation`) run as a library, a CLI and over HTTP (sample mode): `POST /run` (sync), `POST /runs` + `GET /runs/{id}` (async) and `GET /runs/{id}/events` (SSE) work, plus a web UI at `/` (`web/`), the entity directory `GET /entities` and `GET /entities/{cik}/suggestions`; `POST /memory/purge` purges H-MEM lessons through cognition; `/runs/{id}/branch` returns a structured 501.** |
| `services/cognition/` | TypeScript / Node ≥22.18, no npm dependencies. H-MEM memory over REST. **Working.** |
| `services/cognition/engine/hmem/` | Vendored Meterless H-MEM reference (commit `1742dec`). **Never edit**; wrap it in `src/`. |
| `contracts/openapi/` | REST contracts. Update them in the same change as the code. |
| `catalog/sources.yaml` | Bounded source catalog: the only sources the agent may discover |
| `zetaris/sql/` | Lightning SQL templates (one statement per file, `{{placeholders}}`) that register the showcase entities; see [zetaris/README.md](zetaris/README.md) |
| `input_examples/` | 4 cases (inputs): three GE and 3M (04, the lead example) |
| `output_examples/`, `logs/samples/` | Real outputs and traces from real runs only (never read at runtime) |
| `scripts/` | `check_secrets.sh`, `validate_metadata.sh`, `zetaris_probe.py`, `fetch_sample.py`, `perf_smoke.py` (live latency per phase), `verify_audit.py` (re-checks the verifier audit's recorded classes), `memory_demo.py` (H-MEM demo against a running stack), `render_brand.py`, `render_art.py`, `verify_brand_assets.py` |
| `assets/` | Submission brand, characters, illustrations, icons and the deck PDF (to come) ([assets/README.md](assets/README.md)). Not in the agent image. |
| `data/sample/` | Offline SEC slice for sample mode (GE, GEHC, GEV, and 3M/Solventum for example 04, with the one cached EDGAR full-text search `fts_<hash>.json` its scout reads), with `PROVENANCE.json` |
| `BACKLOG.md` | Open gaps deferred out of a PR (severity, reason, bound, review point); closed ones as one line each |
| `metadata.json` | Sponsor metadata (community schema 0.1). Keep statuses honest. |


## Building blocks
All under `services/agent/src/tiresias_agent/`; full signatures and behaviour in [docs/agents-reference.md](docs/agents-reference.md#building-blocks-reuse-these-dont-rebuild-them). **Reuse these; don't rebuild them.** Each sponsor client takes an optional `tracer` and degrades instead of crashing (rule 8).

| Area | Modules |
|---|---|
| Run plumbing | `trace.py` (JSONL tracer, `phase(...)` lines), `models.py` (contract models), `config.py` (env flags, `LoopBudgets`), `gates.py` (human-in-the-loop gates) |
| Pipeline | `orchestrator.py` (**entry point `run_investigation`**, Loop A then Loop B; CLI) |
| HTTP | `app.py` (FastAPI), `runs.py` (run registry; `runs.execute` is the one place the API calls the pipeline), `entities.py` (entity directory) |
| Loop A agents (`agents/`) | `entity_resolver`, `scope`, `framer`, `hypothesiser`, `analyst`, `critic`, `gap_analyst`, `reporter`, `suggester`; generic knowledge in `knowledge.py` |
| Loop B agents (`agents/`) | `source_scout`, `onboarder`, `reconciler` |
| Sponsor adapters (`adapters/`) | `zetaris.py` (evidence, `bootstrap`), `catalog.py` (bounded catalog, discovery), `nightshift.py` (models), `cognition.py` (memory) |

## Commands
```bash
# cognition (TypeScript)
cd services/cognition && npm test && npm run conformance   # 6 tests; H-MEM conformance 13/13
# agent (Python)
cd services/agent && uv sync && uv run --group dev pytest -q && uv run --group dev ruff check src tests
# whole stack (sample mode by default)
docker compose up --build            # agent :8000, cognition :8100 (sample mode works with an empty .env)
# Web UI: open http://localhost:8000 (redirects to /ui/; static files in services/agent/src/tiresias_agent/web)
# HTTP API (Docker, or no Docker: cd services/agent && uv run python -m tiresias_agent.app)
curl -s -X POST localhost:8000/run -H 'Content-Type: application/json' -H 'X-Run-Id: demo-1' \
     -d @input_examples/01_ge_2022_revenue_restated.json            # RunResult JSON; trace in logs/trace-demo-1.jsonl
curl -s -X POST localhost:8000/runs -H 'Content-Type: application/json' -d @input_examples/03_ge_2018_net_loss.json   # 202 {run_id}
curl -s localhost:8000/runs/<run_id>                                 # {status: running|done, result, ...}
curl -sN localhost:8000/runs/<run_id>/events                         # SSE: one trace line per event, then event: end
curl -s localhost:8000/runs/<run_id>/gates/g1 -H 'Content-Type: application/json' -d '{"choice":"frontier"}'   # answer-a-gate (hitl_mode=interactive)
python3 scripts/memory_demo.py [--agent URL]                         # H-MEM demo (stack up, sample mode): purge, 04, 01 warm (fewer hops), 03 (lesson rejected), purge, 01
# Entity directory (sample: the slice only; live adds sec_tickers type-ahead)
curl -s 'localhost:8000/entities?q=ge'                              # EntityList: ready/searchable, expected_first_run
curl -s 'localhost:8000/entities/40545/suggestions?role=auditor&limit=6'   # SuggestionList; each .request posts to /run as-is
curl -s 'localhost:8000/entities?q=super%20micro&sample_mode=false'  # live: needs Zetaris + .env
#   scope check: a what-if or a non-SEC company ends at evidence-frontier with RunResult.scope (reasons + alternatives)
curl -s -X POST localhost:8000/run -H 'Content-Type: application/json' -d '{"question": "What will GE revenue be in FY2027?", "entity": {"ticker": "GE"}, "metric": "Revenues", "period": {"start": "2027-01-01", "end": "2027-12-31"}, "asker": {"role": "analyst"}}'
# checks before every push
./scripts/check_secrets.sh && ./scripts/validate_metadata.sh
python3 scripts/verify_audit.py [--python services/agent/.venv/bin/python] [--with-cognition]   # re-run docs/measurements/verifier-audit-2026-10-24 (no uv, no network)
# Hosted demo (sample mode, Cloud Run; needs gcloud and GCP_PROJECT in .env): idempotent build + deploy, prints the URL
./deploy/gcp/deploy.sh [PROJECT_ID] [REGION]
python3 scripts/verify_brand_assets.py   # palette, logo, characters, illustrations, icons (stdlib)
# rebuild committed brand SVGs/PNGs from the geometry (optional; PNGs need Pillow)
cd services/agent && uv run --with pillow python ../../scripts/render_brand.py && uv run python ../../scripts/render_art.py
# Zetaris (local, outside this repo)
cd ~/Repos/hackathon2026/zetaris/zetaris-platform && ./verify.sh   # UI localhost:3000, REST :8888
python3 scripts/zetaris_probe.py     # latency and registration probe (reads .env)
cd services/agent && uv run python -m tiresias_agent.adapters.zetaris bootstrap   # register sources (needs ZETARIS_* and SEC_USER_AGENT)
cd services/agent && uv run python -m tiresias_agent.adapters.zetaris smoke [--sample]   # entities, revenue facts, filings
cd services/agent && uv run python -m tiresias_agent.adapters.zetaris warm   # after a Zetaris restart: re-cache the ZETARIS_WARM_ENTITIES raw tables (zetaris/README.md#apply)
# Investigation (Loop A, then Loop B) on one request (sample mode by default; --live queries Zetaris with sample fallback; --loop-a stops after Loop A)
cd services/agent && uv run python -m tiresias_agent.orchestrator input_examples/01_ge_2022_revenue_restated.json [--live] [--loop-a] [--logs-dir DIR] [--depth quick|thorough|deep] [--interactive]
#   --depth (ADR-0011): quick = rules only (default); thorough/deep add model lanes live (minutes); sample mode never calls a model
#   MAX_NEW_SOURCES_PER_RUN=0 shows the source cap (end state budget); scripted {"source": "deny"} shows the source gate
#   stdout: the RunResult JSON only; trace lines go to stderr and <repo>/logs/trace-<run_id>.jsonl
#   a relative request path resolves against the cwd, then the repo root
#   any SEC registrant live: {"entity": {"ticker": "IBM"}} or {"entity": {"name": "Hilton Worldwide Holdings Inc."}}
# Coverage sweep (pre-registered companies, live; API in live mode on the host): docs/measurements/coverage-sweep-2026-10-26.md
python3 scripts/coverage_sweep.py --api http://localhost:8020 --out FILE.jsonl [--only JNJ,IBM]
cd services/agent && uv run python ../../scripts/audit_sweep.py --env-file ../../.env   # independent SEC audit of the sweep (offline tooling)
# Live latency per phase (needs Zetaris + .env; cold = first run for an entity, then warm). Writes no repo files
cd services/agent && uv run python ../../scripts/perf_smoke.py [request.json ...] [--runs 2] [--logs-dir DIR] [--out FILE.jsonl] [--env-file PATH]
#   defaults: the four input_examples, 2 runs each, traces in logs/perf/; prints one JSON line per run + a Markdown table
```

## External services (live mode only; sample mode needs none)
Zetaris (local Docker bundle, REST `:8888`) and Meterless Nightshift (macOS VM, OpenAI-compatible Runtime). Endpoints, headers and what `bootstrap` registers: [docs/agents-reference.md](docs/agents-reference.md#external-services-live-mode-only-sample-mode-needs-none). Zetaris quirks: [zetaris/README.md](zetaris/README.md#quirks).

## How work lands (from 23 Oct 2026)
- **Every change goes through a pull request**, one task per branch (`feat/<short-name>`), file-disjoint from other open PRs so they can run in parallel. Process and housekeeping commits may go straight to `main`.
- The PR body follows [.github/pull_request_template.md](.github/pull_request_template.md): an `## ACCEPTANCE CRITERIA` section of `[C]` / `FALSIFIED-BY:` / `VERIFIER:` entries. Make each criterion testable by a named test or command.
- Each PR is reviewed by **DuelLoop** (`~/Repos/duelloop`, outside this repo): Codex as adversarial reviewer, Claude Code as worker, up to 5 rounds (`--rounds 5`). The coordinating session launches it backgrounded (`duelloop go --pr N --worker claude --reviewer codex --repo <worktree>`); run records live in `~/.duelloop/runs/`, never in this repo. The local `verdict.json` is the source of truth, not the PR label.
- **CI** ([.github/workflows/ci.yml](.github/workflows/ci.yml)) runs on every PR and on `main`: agent pytest + ruff, cognition tests + H-MEM conformance, secrets and metadata checks, and the judge quickstart (`docker compose up` with an empty `.env`, then the four input examples through `POST /run`). **A PR merges only when CI is green**; CI is the one verification that is neither the worker's nor the reviewer's word.
- On `MERGE_PERMITTED` the coordinator re-runs every verifier itself and merges (squash) only if they pass: `uv` panics inside the reviewer's macOS sandbox, so the reviewer probes behaviour but often cannot run the suite. Escalations go to the human.
- A PR that adds or changes a feature, module, CLI or observability updates **[docs/agents-reference.md](docs/agents-reference.md)** (its Building blocks row, phases or external calls), the **Commands** below if a command changed, and, if a judge would see the change, **README**, in the same PR and as an acceptance criterion. Later tasks build on what is documented there.
- Docs-only PRs (status, checklist, README) go through DuelLoop too. Their criteria test **accuracy** (every claim matches the code and git history; nothing is marked working that a judge can't run) and **logical consistency** (CHECKLIST, STATUS, README, metadata.json and AGENTS.md agree with each other).
- **Where findings go.** A real finding that is out of scope is **deferred** to [BACKLOG.md](BACKLOG.md) in the same commit, with every field (severity and review point included). A quirk of a sponsor or data source goes next to its code (`zetaris/README.md`, the module's docstring); a design rule goes in `docs/architecture.md`, `docs/causal-criteria.md` or an ADR; a judge-visible limit goes in `docs/limitations.md`. **AGENTS.md takes only invariants**, and adding one needs the human's agreement.
- Worker and reviewer rounds: commit to the PR branch only; don't push, open, approve or merge PRs.
- Parallel PRs don't edit `docs/CHECKLIST.md`, `docs/STATUS.md` or `metadata.json` (they would conflict); the coordinator updates them right after each merge.

## Rules (non-negotiable)
1. **No hardcoded or replayed outputs, no hand-written traces.** Judges test with their own inputs. Sample mode runs real logic on cached data.
2. **Every agent action writes a JSONL trace line** (schema: `docs/architecture.md#tracing`), propagating `X-Run-Id`.
3. **Secrets only in `.env`** (gitignored). Never print keys; mask them in logs and screenshots.
4. **Honest status.** `metadata.json` and the checklist say `working` or `done` only when a judge can see it run, or can verify it in a committed trace from a fresh state that records every call and response (whoever made the run; the human, 25 Oct). Update `docs/CHECKLIST.md`, `docs/STATUS.md` and `metadata.json` together with the work: in the same commit for a change that lands alone, or, for parallel PRs, by the coordinator right after the merge (How work lands).
5. **Destructive operations run alone.** Any `rm`, overwrite, `git reset`, `docker compose down -v` or memory purge goes in its own command with a one-line reason, never inside a larger script.
6. **Don't edit vendored code** (`services/cognition/engine/`). Wrap it.
7. Bound everything: timeouts on external calls, capped loops (`MAX_CAUSAL_DEPTH`, `MAX_BRANCHES_PER_LEVEL`, `MAX_NEW_SOURCES_PER_RUN`).
8. Sponsor calls go behind adapters with a fallback, so an outage degrades the run instead of crashing it.

## Invariants (every change must respect these)
- Loop A only queries the **asked entity** (its concept series and filings). Evidence that lives in another entity (e.g. a spun-off business) is an `InformationNeed` for Loop B, even if that entity happens to be in `data/sample/` or registered in Zetaris.
- In live mode **every read goes through Zetaris**, discovery included: nothing in `services/agent/src` names an SEC endpoint or opens HTTP except the three sponsor adapters (`tests/test_any_company.py` guards it). SEC URLs live only in `catalog/sources.yaml` and `zetaris/sql/`. `scripts/fetch_sample.py` is offline tooling that rebuilds `data/sample/`, not a runtime path.
- The agent image is built from the **repo root** and mirrors it under `/app`. The root `.dockerignore` is an **allow-list**: a new directory the image needs must be added there and to `services/agent/Dockerfile` (`tests/test_packaging.py` checks both). `RunResult.trace_file` is the container path; on the host it is `logs/` (mounted).

Anything else you learn (a quirk, a limit, a surprise) is **not** added here; see "Where findings go" under How work lands.
