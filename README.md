# Tiresias

[![CI](https://github.com/Palmlore/Tiresias/actions/workflows/ci.yml/badge.svg)](https://github.com/Palmlore/Tiresias/actions/workflows/ci.yml)

**An evidence-backed "5 Whys" root-cause agent for financial analysts, built on SEC filings.**

> *Tiresias, the blind seer of Sophocles' Oedipus Rex, named the cause of Thebes' plague first, but had no evidence and was not believed. This Tiresias brings the evidence.*

| | |
|---|---|
| **Event** | Open Agent Hackathon 2026 (GenAI Works), 22–27 October 2026 |
| **Track** | Reasoning Architecture |
| **Team** | Palmlore (solo) |
| **Status** | 🚧 **In development during the build window.** Each section says what works today. Progress: [docs/CHECKLIST.md](docs/CHECKLIST.md). |
| **Licence** | Apache-2.0 |

---

## The problem

A company's reported figures change between filings. GE's 2022 revenue was **$76.6B** in its 2023 annual report, **$58.1B** in 2024 and **$29.1B** in 2025. An analyst has to find out *why* before trusting a model built on those numbers. Today that means reading filings by hand across the parent company and the companies it spun off.

## What Tiresias does

You ask *"why did this figure change?"* and say **who is asking** (analyst, auditor, board). Tiresias:

1. **Drills down** through the sources it knows (filings, restated values, filing events), using SQL through **Zetaris**.
2. **Goes looking for missing evidence** when the known sources run out. It finds the entity it needs (for example the spun-off company), registers that company's filings as a new Zetaris source while it runs, and reconciles the numbers.
3. **Challenges every link** with a critic. Is it causal, or just a breakdown of the total? Would the effect disappear without it? How much does it explain, and what's left over?
4. **Stops at the first cause the asker can act on**, which can be before or after level 5. It always reports the end state: `actionable`, `branched`, `cyclic`, `evidence-frontier`, `external-driver` or `budget` (`cyclic` is not produced yet, [BACKLOG](BACKLOG.md) B-2).
5. **Remembers lessons, not answers** (Meterless **H-MEM**), *planned*: the H-MEM service runs, but the agent does not use it yet. The goal is that the next similar investigation takes fewer steps and the memory can be **purged**, so you can watch the difference.

Every step (query, hypothesis, critique, human-in-the-loop gate, source registration) is written to a JSONL trace; memory recall will be too once memory is wired in.

## Quickstart (judges)

**Works today: Loop A (the drill-down through registered sources) and Loop B (finding the missing entity in the SEC catalog, registering it behind a source gate and re-running the add-back test) in sample mode, offline, with no API keys** (`SAMPLE_MODE=true`, the default). It runs the real agent logic on a cached SEC slice ([data/sample/](data/sample/)); nothing is replayed. In live mode the agent also calls Nightshift models (see Models below); memory, branching and a UI are still **planned**; [docs/CHECKLIST.md](docs/CHECKLIST.md) tracks them.

Needs Docker Desktop (or Docker Engine) with **Compose v2.24 or later** (`docker compose version`), ports 8000 and 8100 free, and about 750 MB of disk for the two images. The first build also pulls the Python and Node base images, which can take a few minutes; later builds take seconds.

```bash
git clone https://github.com/Palmlore/Tiresias && cd Tiresias
cp .env.example .env              # keep SAMPLE_MODE=true; no keys needed
docker compose up --build         # agent :8000, cognition :8100 (add -d to run in the background)

# in another terminal: wait until the agent answers, then run the three examples (each prints a RunResult JSON)
until curl -sf localhost:8000/health >/dev/null; do sleep 1; done
for f in input_examples/*.json; do
  curl -s -X POST localhost:8000/run -H 'Content-Type: application/json' -d @"$f"; echo
done
ls logs/                          # one JSONL trace per run: logs/trace-<run_id>.jsonl
```

The `trace_file` in each reply is the path inside the container (`/app/logs/…`); on your machine the same file is under `logs/`.

Each result leads with the decision: `end_state` (`actionable`, `evidence-frontier`, …) and the recommended `action`, then the `effect`, the `why_tree` (each link with its SQL, rows and the critic's verdict), the `residual` (share of the effect left unexplained; negative when over-explained) and `stats`. In sample mode all three examples end `actionable`: 01 and 02 after Loop B reconciles the restatement to a spun-off business, 03 at the goodwill impairment. Write your own question in the same shape as [input_examples/](input_examples/) (schema: `RunRequest` in [contracts/openapi/agent.yaml](contracts/openapi/agent.yaml)); a malformed body gets a structured 422.

**Watch a run live.** Pass your own run id with `X-Run-Id` (it is echoed, stamped on every trace line and names the trace file), start the run in the background, and stream its trace as Server-Sent Events. A run id can be used once (reusing it returns 409 `RunConflict`), so pick a new one each time you repeat this:

```bash
curl -s -X POST localhost:8000/runs -H 'Content-Type: application/json' \
     -H 'X-Run-Id: my-run-1' -d @input_examples/03_ge_2018_net_loss.json   # 202 {"run_id": "my-run-1", ...}
curl -sN localhost:8000/runs/my-run-1/events      # one trace line per event, then "event: end"
curl -s  localhost:8000/runs/my-run-1             # {"status": "done", "result": {...RunResult...}}
```

**Without Docker** ([uv](https://docs.astral.sh/uv/); it fetches Python 3.11+ if needed). Stop the Docker stack first (`docker compose down`), or give the app another port, because both use :8000. The app runs in the foreground, so run the CLI line in a second terminal:

```bash
cd services/agent && uv sync
uv run python -m tiresias_agent.app                       # same API on :8000 (or: AGENT_PORT=8001 uv run python -m tiresias_agent.app)
uv run python -m tiresias_agent.orchestrator ../../input_examples/01_ge_2022_revenue_restated.json   # CLI: RunResult on stdout
```

Live mode (`SAMPLE_MODE=false` or `"options": {"sample_mode": false}`) queries a local Zetaris and falls back to the sample per call if it is unreachable. Every read, including the discovery of a missing entity, goes through Zetaris. See [deploy/README.md](deploy/README.md).

### Ask about your own company

- **Sample mode (default, offline) covers the GE family only**: GE, GE HealthCare and GE Vernova, because [data/sample/](data/sample/) is a cached slice of their SEC filings (that is what lets it run with no keys). Any other company ends in well under a second at `evidence-frontier`, with a reason that says the slice covers only those three and how to run live.
- **Live mode works for any SEC registrant.** Name it by ticker, CIK or name (`"entity": {"ticker": "IBM"}`, `{"cik": "0000051143"}` or `{"name": "Hilton Worldwide Holdings Inc."}`). Tiresias resolves it through the SEC ticker list registered in Zetaris, asks the source gate, and registers the company's filings in Zetaris while the run is going. Measured on 2026-10-23 ([docs/measurements/live-any-company.md](docs/measurements/live-any-company.md)): **the first question about a new company takes about 15–21 s** (12–16 s of it is Zetaris downloading the company's SEC facts once); **the next one takes 3–11 s**. Progress shows on the trace stream as `phase_start` / `heartbeat` / `phase_done` lines.
- **Pick a company / suggested questions.** `GET /entities?q=…` lists the companies ready now (registered; in sample mode the GE slice, and it says why nothing else) and, live, type-ahead over every SEC registrant, each with its expected first-run wait (the measured 3–11 s or 15–21 s). `GET /entities/{cik}/suggestions` derives questions from that company's own filings (restatements, the largest year-on-year changes), each checked by the framer and returned as a request you can post to `/run` as-is. Some end at `evidence-frontier`: that shows where the rule set stops, with the reason, not a failure.
- **Out-of-scope questions stop at once, with alternatives.** A forecast or what-if ("What would … if …?"), or a company that is not an SEC registrant, ends at `evidence-frontier` with a plain reason in `RunResult.scope` and up to three runnable alternatives checked against filed data (an SEC registrant the question names, the same company's latest filed change, a same-industry peer). In sample mode a company outside the GE slice gets the reason and how to run live, usually with no alternatives (the slice has no peers to offer).
- Not every company gets a full answer: the rule set is small (disposals, impairments, restatements). Loop B finds a spun-off business by its filings, not its name: the new registrant whose Form 10 names the parent (IBM → Kyndryl, 3M → Solventum), found with EDGAR full-text search through Zetaris; a sale to an existing company is refused with the buyer named (DuPont → IFF), and two spin-offs on one day are followed one at a time (Hilton: the best-ranked, the rest stays in the residual). In the measurements GE, IBM, 3M, Crane NXT, J&J and Hilton end `actionable`; DuPont (a sale), Caterpillar and ExxonMobil end at `evidence-frontier`, each with the reason.

## Where to look (judging criteria → repository)

The full cross-reference is in **[docs/RUBRIC.md](docs/RUBRIC.md)**.

| Criterion | Start here |
|---|---|
| Impact (30) | [docs/problem-and-users.md](docs/problem-and-users.md) |
| Technical (20) | [docs/architecture.md](docs/architecture.md), [docs/decisions/](docs/decisions/), `services/` |
| Innovation (15) | [docs/causal-criteria.md](docs/causal-criteria.md) |
| Demo (15) | [docs/demo-plan.md](docs/demo-plan.md), `input_examples/`, `output_examples/`, `logs/samples/` |
| Product & UX (10) | [docs/problem-and-users.md](docs/problem-and-users.md) (asker profiles, branching) |
| Sponsor Tech (10) | [docs/sponsor-technology.md](docs/sponsor-technology.md), [metadata.json](metadata.json) |

## Repository layout

```text
services/agent/       Python (FastAPI): orchestrator, agents, Zetaris adapter, Nightshift client, /run, tracing
services/cognition/   TypeScript (Node): Meterless H-MEM engine service and its conformance tests
contracts/openapi/    REST contracts between the services (written before the code)
catalog/              Bounded source catalog: the only sources the agent may discover
zetaris/sql/          Lightning SQL that registers the sources in Zetaris
data/                 Offline sample slice (cached SEC data) and how it was fetched
input_examples/       Sample questions (≥3)
output_examples/      Real outputs from real runs (never read at runtime)
logs/                 Run traces (logs/samples/ holds committed example traces)
deploy/               Local (Docker Compose) and cloud notes (GCP, AWS)
docs/                 Checklist, architecture, decisions (ADRs), criteria, sponsor tech, data sources, status
AGENTS.md             Handover for coding agents (CLAUDE.md imports it)
scripts/              Checks: secrets scan, metadata validation
metadata.json         Sponsor and submission metadata (community schema 0.1)
```

## Models

**The model proposes, the rules and the data decide** ([ADR-0010](docs/decisions/ADR-0010-model-lanes.md)). In live mode (`SAMPLE_MODE=false` with a configured Nightshift Runtime) three lanes call Meterless Nightshift: a fast lane proposes extra candidate causes from the evidence vocabulary the source can query (each is then measured with SQL and judged by the rule critic like any other), a deep lane gives the critic an independent second opinion (a disagreement goes to the `critic_escalation` gate, whose auto policy keeps the rule verdict; the opinion is on `why_tree[].model_opinion`), and a fast lane may reword the action (used only when it adds no number or name). Every call is traced with the lane, the requested lane id and the `routed_model` from Nightshift's receipt (Nemotron models on 2026-10-24), and counted in `stats.model_calls` (cap `MAX_MODEL_CALLS_PER_RUN`, default 12). If Nightshift is down, the run continues on the rules. **Sample mode uses the rule lanes only** and makes no model call (`MODEL_LANES=on` turns them on there too; `off` turns them off everywhere).

## Sponsor technology (summary)

| Sponsor | Role in Tiresias | Status |
|---|---|---|
| **Zetaris** | The evidence layer. Every claim is SQL plus the rows returned. New sources are registered while the agent runs. | in progress: in live mode the agent drives Zetaris end to end (resolves the asked company, registers it and the spun-off business at runtime when Zetaris lacks them; discovery and evidence are SQL), measured in [docs/measurements/live-any-company.md](docs/measurements/live-any-company.md); the default quickstart is sample mode, which runs the same logic on cached rows without Zetaris |
| **Meterless** | **H-MEM** will store lessons from past investigations (purgeable). **Nightshift** will be the primary model provider. | in progress: H-MEM service working (conformance 13/13), not yet used by the agent (memory demo planned); Nightshift model lanes (extra hypotheses, a second opinion for the critic, action wording) run in live mode, measured once on case 01 (2026-10-24); the rules still decide, and sample mode stays rule-based |
| **Cursor** | Optional since 23 Oct (organizer update); Claude Code is the main coding agent | optional |
| **NVIDIA** | Nemotron models served through Nightshift lanes (fast hypothesiser and reporter, deep critic) | planned (the lanes are built, ADR-0010; on 2026-10-24 Nightshift routed every live lane call to a Nemotron model, `routed_model` in the trace; Nightshift picks the model, not Tiresias) |

Details are in [docs/sponsor-technology.md](docs/sponsor-technology.md). Status is only `working` when a judge can see it run.

## Data

Public SEC EDGAR APIs (XBRL company facts, filing histories, ticker map) and company_dns industry codes. The offline sample is a small cached slice with source URLs and fetch dates. See [docs/data-sources.md](docs/data-sources.md). No personal or confidential data is used.

## AI and model usage, limitations, third-party code

- [docs/ai-usage.md](docs/ai-usage.md)
- [docs/limitations.md](docs/limitations.md)
- [NOTICE](NOTICE)

## Pre-existing components

None. All code in this repository was written during the build window, which opened on 22 Oct 2026 at 00:00 UTC. Third-party open-source components are listed in [NOTICE](NOTICE).
