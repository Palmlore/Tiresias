# AGENTS.md: handover for coding agents (Cursor, Claude Code, others)

**Tiresias**: an evidence-backed "5 Whys" root-cause agent for financial analysts on SEC filings. Open Agent Hackathon 2026, track *The Agent That Can Explain Why*. Solo entry by Palmlore. Repo: github.com/Palmlore/Tiresias (public, Apache-2.0).

**Deadline: 27 Oct 2026 23:45 UTC (28 Oct 10:45 AEDT).** Hard deadline. Submit with hours to spare.

## Read first (in this order)
0. `../workspace/HANDOVER.md`: **start here each session.** It is private, outside this repo (a local git repo with no remote), and covers environment state, how to start it, leftover tasks and next steps. Working data, pinned upstream clones and internal notes live in `../workspace/` too, never in this public repo and never in a temp scratchpad.
1. [docs/CHECKLIST.md](docs/CHECKLIST.md): the progress checklist, cross-referenced to every event requirement. **Tick items only when evidence exists.**
2. [docs/STATUS.md](docs/STATUS.md): open questions and who owns them.
3. [docs/architecture.md](docs/architecture.md), [docs/causal-criteria.md](docs/causal-criteria.md), [docs/decisions/](docs/decisions/) (ADR-0001..0008): the agreed design. Don't re-decide these without a new ADR.
4. [docs/RUBRIC.md](docs/RUBRIC.md): judging criteria → evidence in the repo.
5. Event reference pack (rules, sponsor guides, research notes): `../docs/` (outside this repo). Start with `../docs/MAP.md`, `../docs/SPONSOR_STACK.md`, `../docs/ideas/01-five-whys-feasibility.md`.

## Layout
| Path | What |
|---|---|
| `services/agent/` | Python 3.11+ / FastAPI (uv). Orchestrator, agents, Zetaris adapter, Nightshift client. **Only `/health` so far; `/run` returns a structured 501.** |
| `services/cognition/` | TypeScript / Node ≥22.18, no npm dependencies. H-MEM memory over REST. **Working.** |
| `services/cognition/engine/hmem/` | Vendored Meterless H-MEM reference (commit `1742dec`). **Never edit**; wrap it in `src/`. |
| `contracts/openapi/` | REST contracts. Update them in the same change as the code. |
| `catalog/sources.yaml` | Bounded source catalog: the only sources the agent may discover |
| `zetaris/sql/` | Lightning SQL for registering sources (to fill) |
| `input_examples/` | 3 GE cases (inputs) |
| `output_examples/`, `logs/samples/` | Real outputs and traces from real runs only (never read at runtime) |
| `scripts/` | `check_secrets.sh`, `validate_metadata.sh`, `zetaris_probe.py` |
| `metadata.json` | Sponsor metadata (community schema 0.1). Keep statuses honest. |

## Commands
```bash
# cognition (TypeScript)
cd services/cognition && npm test && npm run conformance   # 6 tests; H-MEM conformance 13/13
# agent (Python)
cd services/agent && uv sync && uv run --group dev pytest -q && uv run --group dev ruff check src tests
# whole stack (sample mode by default)
docker compose up --build            # agent :8000, cognition :8100
# checks before every push
./scripts/check_secrets.sh && ./scripts/validate_metadata.sh
# Zetaris (local, outside this repo)
cd ~/Repos/hackathon2026/zetaris/zetaris-platform && ./verify.sh   # UI localhost:3000, REST :8888
python3 scripts/zetaris_probe.py     # latency and registration probe (reads .env)
```

## External services (live mode only; sample mode needs none)
- **Zetaris**: local Docker bundle at `~/Repos/hackathon2026/zetaris/zetaris-platform` (2.4.3.1-hackathon). REST `http://localhost:8888/api/v1.0`, or `http://host.docker.internal:8888/api/v1.0` from containers. `POST /sql-editor/sqls/run` with `Authorization: Bearer`, `X-Org-ID: 1` and a fresh `X-Request-ID` UUID. Already registered: database `TIRESIAS_SEC`, container `tiresias`, views `tiresias.ge_revenue_cf` and `tiresias.gehc_revenue`.
- **Meterless Nightshift**: runs in an isolated macOS VM, because the installer is ad-hoc signed and not notarized. It's an OpenAI-compatible Runtime reached through hotspot mode at the VM's IP. Headers: `meterless-app: tiresias`, `meterless-scope: user`. Models: `meterless/fast`, `meterless/deep`. Timeout 180 s. The NVIDIA key lives **only** in Nightshift.

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
- The Zetaris admin defaults to `admin@zetaris.com` (change the password in the UI).

## Next work (see docs/CHECKLIST.md for the full list)
1. Agent: Zetaris adapter + tracing + Loop A on `input_examples/01` (sample mode first, then live).
2. Rule-based hypothesiser and critic (sample mode); Loop B onboarding of GE HealthCare.
3. Nightshift client (live mode) once the VM's Runtime URL and token are in `.env`.
4. Memory demo (cold → warm → purge → cold), asker profiles, branching.
