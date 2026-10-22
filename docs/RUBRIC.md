# Judging criteria → evidence in this repository

This maps each scored criterion and each required component of the Open Agent Hackathon 2026 to where the evidence lives. Status values are `planned`, `in progress` and `done`; each one is updated when its evidence exists, not before.

## Scored criteria (100 points)

| Criterion | Points | What judges look for | Evidence in the repo | Status |
|---|---:|---|---|---|
| **Impact** | 30 | A meaningful real-world problem for its intended users, consistent with the track | [problem-and-users.md](problem-and-users.md): analyst persona, asker profiles, real GE/SEC cases | in progress |
| **Technical** | 20 | Implementation, reliability, architecture | [architecture.md](architecture.md), [decisions/](decisions/), `services/`, tests, the Docker Compose file, timeouts and fallbacks, JSONL traces | planned |
| **Innovation** | 15 | An original approach | [causal-criteria.md](causal-criteria.md): critic rubric (intervention test, breakdown vs causal), asker-relative stopping, Loop B discovery of new evidence, memory that stores lessons rather than answers | in progress |
| **Demo** | 15 | Clear demo of function and value | [demo-plan.md](demo-plan.md), `input_examples/`, `output_examples/`, `logs/samples/` | planned |
| **Product & UX** | 10 | Intuitive and usable for its users | Asker profiles, branching after a run, a readable report with labelled links ([problem-and-users.md](problem-and-users.md)) | planned |
| **Sponsor Tech** | 10 | Effective use of sponsor tech | [sponsor-technology.md](sponsor-technology.md), [`metadata.json`](../metadata.json) | planned |

## Required submission components

| Component | Where | Status |
|---|---|---|
| Demo video (1–4 min) | Link goes in README and `metadata.json` | planned |
| Slide deck (8 points) | `docs/deck/` (outline in [demo-plan.md](demo-plan.md)) | planned |
| Public GitHub repo + README (setup, usage, dependencies) | This repository | in progress |
| Sponsor technology explanation (Zetaris, Meterless, Cursor, NVIDIA, plus what was unique) | [sponsor-technology.md](sponsor-technology.md) | planned |
| Pre-existing components declared | README, `metadata.json` → `pre_existing` (none) | done |

## Hard requirements and disqualification risks

| Requirement | How it's met | Status |
|---|---|---|
| Zetaris integrated and load-bearing | Every piece of evidence is a Zetaris SQL query (`services/agent` Zetaris adapter) | planned |
| Meterless: ≥1 engine in a working feature, with test evidence | H-MEM in `services/cognition`, passing the official conformance runner | planned |
| Meterless Nightshift as primary model provider | `services/agent` model client → Nightshift Runtime (OpenAI-compatible) | planned |
| Developed in Cursor | See [sponsor-technology.md](sponsor-technology.md#cursor) | in progress |
| No hardcoded outputs | Outputs are generated from inputs. `output_examples/` is never read at runtime. Sample mode runs real logic on cached data. | planned |
| Evidence of how the agent reached its output | JSONL traces in `logs/` (schema in [architecture.md](architecture.md#tracing)) | planned |
| No secrets committed | `.env.example` with placeholders only, `.gitignore`, [`scripts/check_secrets.sh`](../scripts/check_secrets.sh) | done |
| Runs from a clean clone, no manual steps | `docker compose up --build`, plus `SAMPLE_MODE=true` with no credentials | planned |
| Public repository | github.com/Palmlore/Tiresias | done |

## Recommended practice (technical execution guide)

| Practice | Where | Status |
|---|---|---|
| Programmatic entry point (`POST /run`, JSON in and out) | `services/agent`, [contracts/openapi/agent.yaml](../contracts/openapi/agent.yaml) | in progress (contract drafted) |
| Docker, no runtime installs, no local GPU | `services/*/Dockerfile`, `compose.yaml` | in progress |
| Bounded runs (timeouts, capped loops) | [architecture.md](architecture.md#budgets) | planned |
| ≥3 sample inputs and outputs | `input_examples/` (3 written), `output_examples/` (after real runs) | in progress |
| Logs to stdout and `logs/` | [architecture.md](architecture.md#tracing) | planned |
| Sample mode with no network or credentials | [ADR-0007](decisions/ADR-0007-offline-sample-mode.md) | planned |
| README disclosure: models, frameworks, data, limitations | [ai-usage.md](ai-usage.md), [data-sources.md](data-sources.md), [limitations.md](limitations.md) | in progress |
