# Tiresias

**An evidence-backed "5 Whys" root-cause agent for financial analysts, built on SEC filings.**

> *Tiresias, the blind seer of Sophocles' Oedipus Rex, named the cause of Thebes' plague first, but had no evidence and was not believed. This Tiresias brings the evidence.*

| | |
|---|---|
| **Event** | Open Agent Hackathon 2026 (GenAI Works), 22–27 October 2026 |
| **Track** | The Agent That Can Explain Why |
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
4. **Stops at the first cause the asker can act on**, which can be before or after level 5. It always reports the end state: `actionable`, `branched`, `cyclic`, `evidence-frontier`, `external-driver` or `budget`.
5. **Remembers lessons, not answers** (Meterless **H-MEM**). The next similar investigation takes fewer steps. The memory can be **purged**, so you can watch the difference.

Every step (query, hypothesis, critique, human-in-the-loop gate, memory recall) is written to a JSONL trace.

## Quickstart (judges)

> ⚠️ **Not runnable yet.** The commands below are the target interface, and [docs/CHECKLIST.md](docs/CHECKLIST.md) tracks progress. Planned: **local first, offline, no API keys needed** (`SAMPLE_MODE=true`).

```bash
git clone https://github.com/Palmlore/Tiresias && cd Tiresias
cp .env.example .env              # leave it empty for sample mode
docker compose up --build         # agent :8000, cognition :8100
curl -X POST localhost:8000/run -H 'Content-Type: application/json' \
     -d @input_examples/01_ge_2022_revenue_restated.json
ls logs/                          # JSONL trace of the run
```

Live mode adds a local Zetaris (Docker) and Meterless Nightshift. See [deploy/README.md](deploy/README.md).

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

## Sponsor technology (summary)

| Sponsor | Role in Tiresias | Status |
|---|---|---|
| **Zetaris** | The evidence layer. Every claim is SQL plus the rows returned. New sources are registered while the agent runs. | planned |
| **Meterless** | **H-MEM** stores lessons from past investigations (purgeable). **Nightshift** is the primary model provider. | in progress: H-MEM service working (conformance 13/13); Nightshift planned |
| **Cursor** | Development environment | in progress |
| **NVIDIA** | Nemotron models served through Nightshift lanes (fast hypothesiser, deep critic) | planned |

Details are in [docs/sponsor-technology.md](docs/sponsor-technology.md). Status is only `working` when a judge can see it run.

## Data

Public SEC EDGAR APIs (XBRL company facts, filing histories, ticker map) and company_dns industry codes. The offline sample is a small cached slice with source URLs and fetch dates. See [docs/data-sources.md](docs/data-sources.md). No personal or confidential data is used.

## AI and model usage, limitations, third-party code

- [docs/ai-usage.md](docs/ai-usage.md)
- [docs/limitations.md](docs/limitations.md)
- [NOTICE](NOTICE)

## Pre-existing components

None. All code in this repository was written during the build window, which opened on 22 Oct 2026 at 00:00 UTC. Third-party open-source components are listed in [NOTICE](NOTICE).
