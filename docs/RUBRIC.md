# Judging criteria → evidence in this repository

This maps each scored criterion and each required component of the Open Agent Hackathon 2026 to where the evidence lives. Status values are `planned`, `in progress` and `done`; each one is updated when its evidence exists, not before. Updated 2026-10-27.

## Scored criteria (100 points)

| Criterion | Points | What judges look for | Evidence in the repo | Status |
|---|---:|---|---|---|
| **Impact** | 30 | A meaningful real-world problem for its intended users, consistent with the track | [problem-and-users.md](problem-and-users.md): analyst persona, asker profiles, real GE/SEC cases; [use-cases.md](use-cases.md): the analyst's working loop; [measurements/coverage-sweep-2026-10-26.md](measurements/coverage-sweep-2026-10-26.md): 12 companies we did not pick, every answer audited against SEC EDGAR | done |
| **Technical** | 20 | Implementation, reliability, architecture | [architecture.md](architecture.md), [decisions/](decisions/), `services/`, tests in CI (`.github/workflows/ci.yml`), the Docker Compose file, timeouts and fallbacks, JSONL traces with the HTTP exchange of every sponsor call | done |
| **Innovation** | 15 | An original approach | [causal-criteria.md](causal-criteria.md): critic rubric (intervention test, breakdown vs causal), asker-relative stopping, Loop B discovery of new evidence, memory that stores lessons rather than answers | done |
| **Demo** | 15 | Clear demo of function and value | [demo-plan.md](demo-plan.md), `input_examples/`, `output_examples/`, `logs/samples/`, the web UI and the hosted demo (README); the video is not recorded yet | in progress |
| **Product & UX** | 10 | Intuitive and usable for its users | The web UI's story view (a conversation beside a why-tree that grows and prunes live, decision prompts with Take over) and its Truth view; asker profiles; a readable report with labelled links ([problem-and-users.md](problem-and-users.md)); branching after a run is not built | in progress |
| **Sponsor Tech** | 10 | Effective use of sponsor tech | [sponsor-technology.md](sponsor-technology.md), [`metadata.json`](../metadata.json) | done |

## Required submission components

| Component | Where | Status |
|---|---|---|
| Demo video (1–4 min) | https://youtu.be/kTxGhLfjdzk (2:46), in README and `metadata.json` (`submission.demo_video`) | done |
| Slide deck (8 points) | `assets/deck/` (a PDF, linked in `metadata.json` `submission.slide_deck`; outline in [demo-plan.md](demo-plan.md) and [assets/README.md](../assets/README.md)); committed 27 Oct, 22 slides | done |
| Public GitHub repo + README (setup, usage, dependencies) | This repository | in progress (final pass after the video and deck links) |
| Sponsor technology explanation (Zetaris, Meterless, Cursor, NVIDIA, plus what was unique) | [sponsor-technology.md](sponsor-technology.md) | done |
| Pre-existing components declared | README, `metadata.json` → `pre_existing` (none) | done |

## Hard requirements and disqualification risks

| Requirement | How it's met | Status |
|---|---|---|
| Zetaris integrated and load-bearing | Every piece of evidence is a Zetaris SQL query in live mode (`adapters/zetaris.py`); a fresh-state live trace in `logs/samples/`; the live sweep | done |
| Zetaris "Build with Zetaris" criteria (USL, sources and queries, data quality, reflection) | [zetaris-usl.md](zetaris-usl.md) maps each item: 4 sources with 3 queries each, the `tiresias_usl.sec_evidence` USL with its DDL, 5 queries showing it active and 5 DQ rules run live ([log](measurements/zetaris-usl-2026-10-27.jsonl)), and the three reflections. The agent does not read the USL (BACKLOG B-37, B-38) | done |
| Meterless: ≥1 engine in a working feature, with test evidence | H-MEM in `services/cognition`; official conformance runner 13/13 (`npm run conformance`); the agent's memory in every warm run, shown by `scripts/memory_demo.py` in CI | done |
| Meterless Nightshift as primary model provider | `adapters/nightshift.py` → Nightshift Runtime (OpenAI-compatible), the only model client; a fresh-state live trace with every call (caveats in [sponsor-technology.md](sponsor-technology.md#what-the-model-call-trace-shows-and-what-it-does-not)) | done |
| Developed in Cursor (optional since 23 Oct) | Claude Code is the main coding agent; Cursor made the brand assets (#42) and the Story UI polish (#58, #60); see [ai-usage.md](ai-usage.md#development-tools) | done |
| No hardcoded outputs | Outputs are generated from inputs; `output_examples/` is never read at runtime; sample mode runs real logic on cached data (`tests/test_orchestrator.py::test_outputs_differ_across_cases`) | done |
| Evidence of how the agent reached its output | JSONL traces in `logs/` (schema in [architecture.md](architecture.md#tracing)); real traces in `logs/samples/` | done |
| No secrets committed | `.env.example` with placeholders only, `.gitignore`, [`scripts/check_secrets.sh`](../scripts/check_secrets.sh) in CI | done |
| Runs from a clean clone, no manual steps | `docker compose up --build` with an empty `.env` (`SAMPLE_MODE=true`), run by CI's quickstart job on every change; a fresh clone followed the README on 24 Oct; the clean-VM install test is still open | done |
| Public repository | github.com/Palmlore/Tiresias | done |

## Recommended practice (technical execution guide)

| Practice | Where | Status |
|---|---|---|
| Programmatic entry point (`POST /run`, JSON in and out) | `services/agent`, [contracts/openapi/agent.yaml](../contracts/openapi/agent.yaml), `tests/test_api.py` | done |
| Docker, no runtime installs, no local GPU | `services/*/Dockerfile`, `compose.yaml`, `tests/test_packaging.py` | done |
| Bounded runs (timeouts, capped loops) | [architecture.md](architecture.md#budgets); `RUN_BUDGET_SECONDS`, `MAX_CAUSAL_DEPTH`, `MAX_BRANCHES_PER_LEVEL`, `MAX_NEW_SOURCES_PER_RUN`, `MAX_MODEL_CALLS_PER_RUN` | done |
| ≥3 sample inputs and outputs | `input_examples/` (4), `output_examples/` (from real runs, each naming its trace) | done |
| Logs to stdout and `logs/` | [architecture.md](architecture.md#tracing) | done |
| Sample mode with no network or credentials | [ADR-0007](decisions/ADR-0007-offline-sample-mode.md) | done |
| README disclosure: models, frameworks, data, limitations | [ai-usage.md](ai-usage.md), [data-sources.md](data-sources.md), [limitations.md](limitations.md) | done |
