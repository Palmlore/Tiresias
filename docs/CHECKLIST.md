# Progress checklist

The single list to work from. Every event requirement is merged and de-duplicated, with its source cited. Tick an item only when its evidence exists, and update this file in the same commit as the work.

`[x]` done (evidence linked) · `[~]` in progress · `[ ]` not started

Updated **2026-10-24 AEDT** (build day 3, after #17–#30). Work now lands through DuelLoop-reviewed PRs (AGENTS.md → How work lands). Deadline **27 Oct 23:45 UTC = 28 Oct 10:45 AEDT**.

**Source keys**

| Key | File | Location |
|---|---|---|
| OR | Official rules (draft copy) | `../docs/rules_and_requirements/official-rules.md` |
| SR | Submission requirements | `…/submission-requirements.md` §8 |
| SC | Pre-submission self-check | `…/disqualification-checklist.md` |
| TE | Technical execution guide | `…/technical-execution-requirements.md` §13 |
| AI | AI usage policy | `…/ai-usage-policy.md` §13 |
| DV | Demo video guide | `../docs/demo_and_submission_resources/demo-video-guide.md` §10 |
| SF | Submission form preview | `…/submission-form-preview.md` §5 |
| SG | Participant setup guide | `../docs/participant_setup_guide.pdf` |
| SP | Sponsor folders | `../docs/sponsors/` |
| MP | Mentor playbook | `…/mentor-playbook.md` §11 |

---

## A. Eligibility, team, registration
- [x] Registered on HackOS before 22 Oct 23:45 UTC, aged 16+, one account (OR §3)
- [x] Solo team (1–5 members); member on one submission only (OR §5)
- [ ] Roster final **before** creating the first submission, because the team locks at that point (OR §5.4, SF §2)
- [ ] One primary track selected on HackOS: *Reasoning Architecture* (OR §6; ADR-0009)

## B. Sponsor technology (required, and scored under Sponsor Tech, 10 pts)
- [~] **Zetaris integrated and load-bearing.** Every claim is a Zetaris SQL query (OR §8.1, §16; SC §10) (live mode: every read, discovery included, is Zetaris SQL (#9); sample mode, the judge default, runs the same logic on cached rows, so a judge sees Zetaris only by running `--live` against a local bundle; no live trace committed yet)
  - [x] Local Zetaris running and measured ([measurements/zetaris-local.md](measurements/zetaris-local.md); reproduce with `scripts/zetaris_probe.py`)
  - [x] GE vs GE HealthCare reconciliation runs inside Zetaris (residual $114M), via `scripts/zetaris_probe.py`
  - [x] Agent's Zetaris adapter (timeout, retry, fallback to cached rows) (TE §9) (#5: live and sample sources, falls back to `data/sample/` on outage and traces `degraded`; live smoke row counts match sample mode)
  - [x] Loop B registers a discovered entity's source at runtime from `catalog/sources.yaml` (#8: `agents/onboarder.py` dry run → `source` gate → `bootstrap(..., tables=("facts",))` one statement at a time, `tests/test_loop_b.py::test_onboarder_live_registers_one_statement_at_a_time_and_refreshes_views`; #9: run live against local Zetaris, e.g. Hilton's spun-off HGV registered cold in 8.1 s, [measurements/live-any-company.md](measurements/live-any-company.md))
  - [x] Live discovery and any asked SEC registrant go through Zetaris only (#9: `ZetarisDiscovery` over the `sec_tickers` and per-candidate submissions REST tables, `onboarder.onboard_entity` registers an unknown asked company behind the `source` gate; `tests/test_any_company.py::test_only_the_sponsor_adapters_import_an_http_client_and_no_code_names_sec`; measured cold 15–21 s, warm 3–11 s; BACKLOG B-4 done)
  - [x] Spin-off discovery searches EDGAR full text through Zetaris too: one `fts_<hash>_raw` REST table per search, at most two per run (#13: `zetaris/sql/14_fulltext_table.sql`, `40_fulltext_total.sql`, `41_fulltext_hits.sql`)
  - [x] `zetaris/sql/` holds the registration SQL for the showcase entities (#5: 12 templates; `bootstrap` registered GE, GEHC, GEV and is idempotent)
- [x] **Meterless Build rule: at least one engine in a working, user-facing feature, with test evidence** (SG §3.2) (#30: H-MEM is investigation memory in every warm run, sample mode included)
  - [x] H-MEM in `services/cognition`; official conformance 13/13 (`npm run conformance`); 6 tests
  - [x] User-facing feature: cold → warm → purge → cold memory demo through `/run` (#30: `scripts/memory_demo.py` in sample mode, run in CI's quickstart job: 3M cold stores a lesson; GE spin-off warm 5 → 3 hops; the lesson is rejected by the critic on the impairment case; purge confirmed by cognition; cold again 5 hops; `tests/test_memory.py`, 32 tests)
- [~] **Meterless Power rule: Nightshift is the primary model provider**; real requests go through the Runtime (SG §4) (#22: every model call goes through Nightshift, three lanes (hypothesiser, critic second opinion, reporter wording), the model proposes and the rules decide, ADR-0010; #24: the user picks the depth, `quick` (default, rules only) / `thorough` / `deep`, ADR-0011; measured live once on case 01 on 2026-10-24: 4 calls, ~170 s; sample mode, the judges' default, makes no model call by design, so a judge sees the lanes only live)
  - [x] Nightshift installed in an isolated macOS VM; hotspot mode; reachable from the host and from containers (2026-10-24: Nightshift 1.2.1 in the Tart VM; host access needed macOS Local Network permission for the terminal and for Docker; `/v1/models` answers from the host and from a container)
  - [x] Agent model client (OpenAI-compatible, `meterless-app` and `meterless-scope` headers, 180 s timeout, offline-state handling) (merged #2; 2026-10-24: `python -m tiresias_agent.adapters.nightshift probe` passes on both lanes, receipts name the served model)
- [~] **NVIDIA (optional; DGX Spark award):** a Nemotron lane pinned in Nightshift that materially powers a feature (e.g. a deep critic); evidence of which model served each request (SG §4.3–4.7; event page) (#22: Nemotron serves the lanes through Nightshift, every call traces `routed_model`, `provider` and the receipt id; live 2026-10-24: nemotron-3.5-lightning-30b-a3b and nemotron-3.5-super-vl-120b-a12b served the critic and wording lanes, one critic disagreement kept by the rules. Not pinned: Nightshift 1.2.1 routes on its own and does not honour explicit model ids)
- [x] ~~**Developed in Cursor.**~~ Optional since the organizers' 23 Oct update ("Cursor is now optional … use any AI coding tool"). Claude Code is the main coding agent. Describe only the tools actually used ([ai-usage.md](ai-usage.md)) (OR §8.1 as amended; SP cursor).

## C. Agent system (Technical 20 · Innovation 15)
- [x] ≥2 named agents with distinct roles that interact more than once (OR §7.3; SC §9) (#6: framer, hypothesiser, analyst, critic, gap analyst, reporter in `agents/`; #8: source scout, onboarder, reconciler; `tests/test_orchestrator.py::test_agents_interact_more_than_once_and_critic_sends_work_back`)
- [~] Critique / validation / retry / branching events in the traces (SC §9; AI §13) (#6: critic `accept`/`prune`/`retry` lines with `target_agent` and `critic_escalation` gate lines in every example; branching from a node (`/runs/{id}/branch`) not built)
  - [x] Agent JSONL tracer (`trace.py`): same line format as cognition, secret redaction, spans with `duration_ms` (#1)
- [x] Critic implements [causal-criteria.md](causal-criteria.md): breakdown vs causal labels, intervention test, share explained, residual (#6 rule-based critic, ADR-0007: `agents/critic.py`, `tests/test_critic.py`; #8 add-back test with signed residual: `agents/reconciler.py`, `tests/test_loop_b.py -k reconcile`). Model-backed critic lane: see Nightshift (B)
- [x] Loop A (known sources) and Loop B (evidence frontier) both work on case 01 (#6, #8: Loop A ends at `evidence-frontier`, Loop B reconciles it to the spun-off business, `actionable`, residual 0.6%; `tests/test_loop_b.py::test_restatements_continue_into_loop_b_and_reconcile`; also live in #9's measurement)
- [~] Six end states implemented, with the asker-relative stopping rule (#6, #8: the stopping rule in `orchestrator.py` produces `actionable`, `branched`, `external-driver`, `evidence-frontier` and `budget`; `cyclic` is in the model but no path emits it, because the hypothesiser drops candidates already on the path (BACKLOG B-2); the examples reach `actionable` only, plus `budget` and `evidence-frontier` in tests)
- [~] Human-in-the-loop gates with `auto` / `interactive` / `scripted` modes, each decision traced (ADR-0006) (#6: `gates.py`, `tests/test_gates.py`; `critic_escalation` and (#8) `source` gates run in `auto`, and `scripted` through `RunOptions.decisions` on `/run`, e.g. `tests/test_loop_b.py::test_scripted_source_deny_keeps_the_frontier`; `interactive` has no channel yet (no CLI prompt or UI), so it falls back to policy; no memory gate)
- [ ] Branching from a node of a finished run (`/runs/{id}/branch`)
- [x] Memory gate and lesson write-back from the agent to cognition; recalled lessons seed the hypothesiser; the critic gives feedback on them (#30: entity-free lessons behind the `memory` gate; recall reorders the hypothesiser and, when a lesson's step holds, skips siblings listed in `memory.skipped` (ADR-0005 addendum); critic `helpful` / `not_helpful` / `wrong` feedback; `POST /memory/purge` reports only confirmed purges)
  - [x] Agent-side cognition client: every endpoint, `X-Run-Id`, degrades to no-memory when cognition is down; purge never reports a purge that didn't happen (#3; live round trip in the PR). Not called by the orchestrator yet; `/memory/purge` returns 501
- [ ] Disclosure guardrail: a sensitive finding (non-reliance, auditor change, late filing, large impairment) is stated only with a direct filing marker and the measured effect, behind a disclosure gate; otherwise it stays a frontier lead
- [x] Entity list and question suggestions for the UI (`GET /entities`, `GET /entities/{cik}/suggestions`) (#12: ready/searchable tiers with expected first-run waits from the live measurement; suggestions derived from the entity's own filings and each one checked by the framer before it is returned, `tests/test_entities_api.py::test_the_three_examples_are_among_ges_derived_suggestions`, `::test_every_suggestion_is_neutral_validated_and_runnable`)
- [x] Out-of-scope questions (forecast, not an SEC registrant, not in the sample) end with the reason and up to 3 checked alternatives (`RunResult.scope`) (#12: `agents/scope.py`, `tests/test_scope.py`; keyword rules, BACKLOG B-14)
- [x] Loop B finds a disposal's counterparty from filing evidence, not its name (#13: EDGAR full-text search through Zetaris, Form 10 first, then the 8-K buyer check; `tests/test_spinoff_match.py`; live: IBM→Kyndryl, 3M→Solventum, Crane NXT→Crane Co end `actionable`, DuPont's sale to IFF is refused, [measurements/live-any-company.md](measurements/live-any-company.md); BACKLOG B-5 done)
- [x] An add-back that over-explains beyond `MAX_OVER_EXPLAINED` (150%) is pruned as a likely wrong match, unless a human accepts it at the gate (#15: `tests/test_loop_b.py::test_reconcile_over_explained_bands`, `::test_beyond_bound_add_back_ends_at_the_frontier_under_auto`; BACKLOG B-15 done)
- [ ] UI over `GET /runs/{id}/events` using the phase trace lines (#9 emits `phase_start` / `heartbeat` / `phase_done`; no UI yet)

## D. Running and reproducibility (any failure here can lose the whole Technical and Demo score)
- [x] Programmatic entry point: `POST /run`, JSON in and out, bound to `0.0.0.0`, structured errors (TE §2; SC §5) (#7: `app.py`, `runs.py`, contract in `contracts/openapi/agent.yaml`; `tests/test_api.py`; #8: `/run` runs `run_investigation`, `tests/test_api.py::test_run_goes_through_loop_b_for_a_restatement`)
- [~] Docker: `docker compose up --build` from a clean clone; nothing installed at runtime; no local GPU (TE §3–4) (#7: image built from the repo root with `data/sample/`, `zetaris/sql/`, `catalog/`, `input_examples/`, `tests/test_packaging.py`, BACKLOG B-1 done; #19: CI's quickstart job builds and runs the stack from a fresh checkout with an empty `.env` on every PR; 2026-10-24 fresh clone on the build Mac followed the README literally and worked (#21 fixed the friction it found); clean-machine test on the Tahoe VM still open)
- [x] **`SAMPLE_MODE=true` runs real agent logic with an empty `.env`**, with no network or keys (SC §7, §14 step 9; ADR-0007) (#6, #8: the agents run on `data/sample/`, nothing replayed; #7: `cp .env.example .env && docker compose up --build` then `POST /run` on the three examples (PR #7 Evidence); CLI: `python -m tiresias_agent.orchestrator input_examples/01_ge_2022_revenue_restated.json`)
- [x] Runs are bounded: a run finishes in ≤10 min, with timeouts and capped loops (TE §5) (#6, #8: `RUN_BUDGET_SECONDS`=600 shared by Loop A and B, `MAX_CAUSAL_DEPTH`, `MAX_BRANCHES_PER_LEVEL`, `MAX_NEW_SOURCES_PER_RUN`; `tests/test_orchestrator.py::test_wall_clock_budget_ends_in_budget`, `tests/test_loop_b.py::test_wall_clock_budget_applies_to_loop_b`; #7: `POST /run` gives a 504 past the budget; #9: registration honours the run deadline; slowest measured live run 33 s (pass 1, IBM cold), 21 s after the scout fix)
- [x] Failures return a controlled response, not a hang or a stack trace (TE §5; SC §5) (#6: `tests/test_orchestrator.py::test_unexpected_failure_is_a_structured_error_not_a_stack_trace`; #7: structured 422/404/409/500/504 in `tests/test_api.py`, 501 in `tests/test_health.py`, 429 from `RegistryBusy`; #9: an unknown, ambiguous or unreachable company ends at `evidence-frontier` with a reason, `tests/test_any_company.py`)
- [~] Full validation flow from a fresh clone (SC §14), first on **day 4**, again before submitting (2026-10-24: fresh clone from GitHub, README only, sample mode: every Quickstart step matches, 7 s of command time with cached base images; 8 friction points, README fixed in #21; again before submitting)
- [x] CI on every PR and on `main`: agent tests and lint, cognition tests and H-MEM conformance, secrets and metadata checks, and the judge quickstart in Docker (#19, `.github/workflows/ci.yml`)
- [x] Merged PRs' acceptance criteria re-run on `main` on demand (#20: `scripts/verify_audit.py`, 158 criteria from 16 PRs; findings B-22, B-23)
- [ ] **Judge installation test on a clean macOS Tahoe VM** (Tart `macos-tahoe-base`, nothing preinstalled): a newcomer following only the README gets the system running. Rule: `submission/` is all a judge sees; assume no cache, no local setup.
  - [ ] Every setup step is a script where possible (one command per step, idempotent, prints ✔/✖)
  - [ ] Steps that can't be scripted (e.g. Nightshift install, access gate, NVIDIA key) have a screenshot and a one-line reason why they're manual
  - [ ] Local scaffolding used during the build is documented and removed before submitting

## E. Data, secrets, integrity
- [x] `.env.example` with placeholders only; `.env` gitignored; secrets scan passes (`scripts/check_secrets.sh`) (OR §11; SC §11)
- [x] Data sources, licences and redistribution notes documented ([data-sources.md](data-sources.md)) (OR §11; TE §7)
- [x] `data/sample/` slice with provenance (URL, fetch time, SHA-256) and `scripts/fetch_sample.py`; repo stays small (TE §7) (676 KB after #26 added 3M and Solventum with a cached full-text search; `data/sample/PROVENANCE.json`)
- [x] No hardcoded outputs: outputs differ meaningfully across the 3 inputs; `output_examples/` is never read at runtime (SC §8; AI §4.1) (#6, #8: `tests/test_orchestrator.py::test_outputs_differ_across_cases`, `tests/test_loop_b.py::test_investigation_outputs_differ_across_cases`, the case-literal guard `test_agent_logic_has_no_case_specific_literals`; `grep -rn output_examples services/agent/src` finds nothing)
- [x] Traces generated by the run, never hand-written, matching the input processed (SC §9; AI §4.2) (#6, #7: every run writes `logs/trace-<run_id>.jsonl` with its `run_id` and `mode`, `tests/test_orchestrator.py::test_every_trace_line_has_run_id_status_and_sample_label`, `tests/test_api.py::test_x_run_id_is_echoed_and_names_the_trace_file`; real traces in `logs/samples/`: four sample-mode runs through the judge quickstart and one live run through Zetaris, see [output_examples/README.md](../output_examples/README.md))
- [ ] No keys in the video, deck, screenshots or logs (SC §11; DV §10)

## F. Repository documentation
- [x] Public GitHub repo (OR §10.1; SR)
- [~] README: setup, usage, dependencies, agent roles, examples, limitations, track named (SR §3.3; SC §3) (written; quickstart runnable in sample mode since #7, live any-company notes since #9; final pass after the UI and memory demo)
- [~] AI and model usage disclosure ([ai-usage.md](ai-usage.md)) (AI §6); third-party code and licences ([NOTICE](../NOTICE))
- [x] Pre-existing components declared: none (OR §8.2; SF §3.8)
- [x] ≥3 sample inputs **and** matching real outputs (OR §10.2; TE §13) (four inputs; five outputs in `output_examples/`, four sample mode and one live, each naming its trace)
- [x] `logs/samples/` traces from real runs, referenced by the outputs (TE §8) ([output_examples/README.md](../output_examples/README.md))
- [~] Limitations stated honestly ([limitations.md](limitations.md)) (SC §13)
- [x] `AGENTS.md` handover for coding agents

## G. Demo video (Demo, 15 pts): 1–4 min, target 2:30–3:30
- [ ] Storyboard final ([demo-plan.md](demo-plan.md)): problem, solution, agents, live run, sponsor tech in the trace, output and impact (DV §2–3)
- [ ] Shows a real run, at least one collaboration moment (critic rejects or sends back), and Zetaris + Meterless doing real work (DV §4)
- [~] Memory demo: cold vs warm step counts, plus the purge event (ADR-0005) (the script exists and runs in CI (#30); recording it is part of the video)
- [ ] Clear audio, readable text, 1080p, no secrets on screen (DV §5, §10)
- [ ] Uploaded; link opens in a private window; backup copy; link in README and `metadata.json` (DV §7–8)

## H. Slide deck: all 8 points required (SR §3.2; SF §3.6)
- [ ] 1 Problem and users · 2 Solution · 3 Agent workflow and architecture · 4 Tech stack
- [ ] 5 How Zetaris and Meterless were used · 6 What was unique about the sponsor tech use
- [ ] 7 Product visuals · 8 Future enhancements
- [ ] Consistent with the repo and demo; no claims the code doesn't support

## I. Sponsor tech explanation and metadata (Sponsor Tech, 10 pts)
- [~] [sponsor-technology.md](sponsor-technology.md) covers Zetaris, Meterless, Cursor and NVIDIA, plus "what was unique" (SR §3.4) (drafted; finalize from what ships)
- [~] `metadata.json` valid against the community schema; statuses honest (SP; `scripts/validate_metadata.sh`) (valid; Zetaris, Meterless and NVIDIA `in-progress`; Cursor `planned`, the schema has no "optional")
- [ ] Switch to the official metadata format if the organizers publish one (STATUS #1)

## J. Submission (HackOS)
- [ ] Project name, one-sentence description and paragraph prepared (SF §3.1)
- [ ] All four components linked: video, deck, repo URL, sponsor explanation (OR §10.1)
- [ ] README, deck and demo name the same track (SF §5)
- [ ] Submitted **hours** before 27 Oct 23:45 UTC; update allowed until the deadline (OR §10; SC §15)

## K. Delivery milestones (MP §11; adapted)
| When (AEDT) | Gate | Status |
|---|---|---|
| Thu 22 Oct (day 1) | Zetaris up and measured; repo scaffolded; cognition service working | [x] |
| Fri 23 Oct | Agent: Zetaris adapter, tracing, Loop A on case 01 in sample mode | [x] tracing (#1), Zetaris adapter (#5), Loop A (#6); ahead of plan: `/run` + SSE + Docker sample mode (#7), Loop B (#8), any company live through Zetaris (#9) |
| Sat 24 Oct | Loop B live; critic rules; Nightshift client; 3 cases end to end | [x] Loop B live against local Zetaris (#8, #9 measurement); critic rules (#6, #8); 3 cases end to end in sample and live mode; Nightshift live and used by three model lanes (#22), depth levels (#24); CI (#19); audit of merged PRs (#20) |
| Sun 25 Oct (day-4 checkpoint) | Runs locally **and** in Docker; sample mode with an empty `.env`; memory demo; fresh-clone validation; first judge install test on the clean Tahoe VM. More than 3 gaps means cut scope. | [~] local and Docker sample mode with an empty `.env` (#7); memory demo, fresh-clone validation and Tahoe VM test open |
| Mon 26 Oct | Freeze architecture. Branching and asker profiles polish; outputs and traces committed; README final | [ ] |
| Tue 27 Oct | Record the demo, finish the deck and sponsor explanation | [ ] |
| Wed 28 Oct, before 06:00 AEDT | **Submit** (deadline 10:45 AEDT) | [ ] |
