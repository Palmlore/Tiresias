# Verifier audit: merged PRs' acceptance criteria on current `main` (2026-10-24)

Every merged pull request in Palmlore/Tiresias carries an `## ACCEPTANCE CRITERIA` section of `[C]` / `FALSIFIED-BY:` / `VERIFIER:` entries. Before this audit nobody re-ran them after merge: the reviewer's sandbox cannot run `uv`, there is no CI, and the coordinator's pre-merge re-runs were not recorded. This document re-runs every `VERIFIER:` line of the merged PRs against one `main` commit and records the outcome.

## What was run

- **Commit:** first run on `main` at `5790962c06418f565bced7b2a4c78313259f9eeb` (#17), branch `chore/verifier-audit` cut from `origin/main`.
- **Baseline:** the recorded classes are now baselined on `main` at `7da47689712aad9e941cea20507c0bea8e544e58` (#18, merged into the branch). #18 closed B-9 and added a sentence to the `adapters/zetaris.py` row of docs/agents-reference.md, which superseded #9 criterion 11 (again) and #17 criterion 2; both were re-classified with a new-location command. **feat/model-lanes** (ADR-0010, the PR that adds the Nightshift model lanes) superseded three more rows on purpose: #10 criterion 3 and #11 criterion 1 (an agent module now uses the Nightshift adapter, through `agents/model_lanes.py`, only when the lanes are on) and #17 criterion 2 (one new Building blocks row and seven rows with text added). Later merges are expected to supersede more rows: `scripts/verify_audit.py` then reports them `regressed`, and the row is re-classified in the criteria JSON once someone decides whether it is a real fail or an intended change.
- **Date:** 2026-10-24, macOS (Darwin 27), Python through `uv` (the agent's dev group), Node for `services/cognition`.
- **PRs:** #1–#13 and #15–#17 (16 merged PRs). #14 and #18 are open and were skipped.
- **Criteria:** 158 (one `VERIFIER:` line each), taken from `gh pr view <n> -R Palmlore/Tiresias --json body`.
- **Commands.** Verifiers were written for `/tmp/duelloop-py/bin/python` and `/tmp/duelloop-py/bin/ruff`. They were run as `uv run --group dev python -m pytest -q -p no:cacheprovider <same args>` and `uv run --group dev ruff <same args>` from `services/agent`, each under `timeout 300`; shell verifiers (grep, sed, `git show`, the CLI on the three examples, `npm test`, `check_secrets.sh`, `validate_metadata.sh`) were run as written from the repo root, with `timeout` where they execute code. The full suite on this commit: **343 passed, 1 skipped** (the live cognition round trip, skipped without a server), ruff check and format clean.
- **Not run:** nothing needing live Zetaris, Nightshift, Docker or network beyond GitHub. None of the 158 verifiers required it: the live-mode tests use a fake Zetaris over `httpx.MockTransport`. PR #3's live round trip was run against a local cognition server (`npm start` on port 8199 with a temporary `MEMORY_DIR`), not Docker. `.env` was not read (the worktree has none, so the Nightshift probe ran unconfigured as its verifier intends).
- **Branch-diff verifiers.** Several docs-PR verifiers start with `git diff origin/main -- …` ("what this branch changes"). After the merge that diff is empty, so they were **adapted**: the PR's own change was read from its squash-merge commit (`git show <merge sha>`), and the evidence it cites was checked on current `main`. Rows marked "adapted" say so.

### Reproduce

Every row is recorded in [verifier-audit-2026-10-24.criteria.json](verifier-audit-2026-10-24.criteria.json): PR, index, statement, falsifier, the original verifier, the exact command run (with a one-line reason wherever it differs from the original beyond the interpreter path), the recorded class, and for superseded rows the PR that moved the claim and the command that checks it at its new location. [`scripts/verify_audit.py`](../../scripts/verify_audit.py) (stdlib only, Python 3.11, no `uv`, no network, no `gh`) runs every command from the repo root with a 300 s timeout and checks each row's class mechanically: `pass` = exit 0 and every pytest run in the command selected and passed at least one test (otherwise `regressed`); `fail` = the recorded contradiction is still there (a fixed fail is reported `changed`); `superseded` = the new-location command passes (otherwise `broken`); `not-runnable` is reported, not run. An observed `fail` therefore always means a recorded, still-present contradiction, never a pass that stopped holding. It exits non-zero if any row's observed class differs from the recorded one. `--refresh` re-extracts the criteria from the PR bodies with `gh` and reports differences (on 2026-10-24: 16 bodies, 0 differences).

```bash
python3 scripts/verify_audit.py --python /tmp/duelloop-py/bin/python --with-cognition   # any Python with pytest, ruff, fastapi, httpx, pydantic, PyYAML
python3 scripts/verify_audit.py --python services/agent/.venv/bin/python --with-cognition
```

`--with-cognition` starts the cognition server (node, temporary `MEMORY_DIR` and `LOGS_DIR`) for #3.7; without it that row is `skipped`. Rows needing `npx` (`validate_metadata.sh`) or `node` are skipped if the tool is missing. Runs on 2026-10-24, branch `feat/model-lanes` (main 18a652c merged in), both exit 0 with zero mismatches. Before re-classifying, the same run on that branch reported `MISMATCH in 4 row(s): 4.2, 10.3, 11.1, 17.2`: 10.3, 11.1 and 17.2 are the rows the model lanes change on purpose (re-classified `superseded`); 4.2 was a real inconsistency the branch had introduced (NVIDIA marked in progress in README and sponsor-technology.md while metadata.json and CHECKLIST say planned), fixed in the branch's wording, not in the criteria. (At the #18 baseline the runs had reported `MISMATCH in 2 row(s): 9.11, 17.2`.)

| Interpreter | Recorded | Observed |
|---|---|---|
| `/tmp/duelloop-py/bin/python`, `--with-cognition` | pass 141, fail 3, superseded 13, not-runnable 1 | pass 141, fail 3, superseded 13, not-runnable 1 |
| `services/agent/.venv/bin/python` (uv venv), `--with-cognition` | pass 141, fail 3, superseded 13, not-runnable 1 | pass 141, fail 3, superseded 13, not-runnable 1 |
| `/tmp/duelloop-py/bin/python`, no cognition | pass 141, fail 3, superseded 13, not-runnable 1 | pass 140, fail 3, superseded 13, not-runnable 1, skipped 1 (#3.7) |

Making the runs mechanical turned up two verifier problems; neither changes a recorded class. **#4 criterion 3:** `python -m tiresias_agent.adapters.nightshift --help` exits 2 (the CLI has no argparse and prints its usage for anything but `probe`, as it did at #4's merge), so the verifier as written has always exited non-zero; the first pass read its output, and the reproducible command checks the usage line. **#10 criterion 3 (superseded):** `grep -rln … services/agent/src` also matches compiled `__pycache__/*.pyc` files once tests have run, so the new-location command greps `*.py` only. A check that the runner rejects a 0-test selector (`-k no_such_selector_xyz`, pytest exit 5) reported it as a mismatch, as intended.

### Classification rules

- `pass`: the verifier ran and its output supports the statement.
- `fail`: it ran and contradicts the statement on current `main`.
- `superseded`: it fails or no longer applies because a later PR changed that claim on purpose; the row names that PR and where the claim lives now, and says whether the moved claim holds (re-checked there).
- `not-runnable`: the verifier is a placeholder, prose or ambiguous; the row says why.
- `not-run (live)`: it needs live Zetaris, Nightshift, Docker or the network.
- A test selector that selects 0 tests is never a pass.

## Summary

| PR | Title | Criteria | pass | fail | superseded | not-runnable | not-run (live) |
|---|---|---|---|---|---|---|---|
| #1 | Agent: JSONL tracer and run models | 7 | 7 | 0 | 0 | 0 | 0 |
| #2 | Agent: Nightshift Runtime client (lanes, receipt, offline fallback) | 8 | 8 | 0 | 0 | 0 | 0 |
| #3 | Agent: cognition (H-MEM) client with degraded fallback | 7 | 7 | 0 | 0 | 0 | 0 |
| #4 | Status after wave 1 (#1, #2, #3, #5) | 5 | 3 | 1 | 1 | 0 | 0 |
| #5 | Agent: Zetaris adapter (live + sample) and registration SQL | 9 | 8 | 0 | 1 | 0 | 0 |
| #6 | Agent: Loop A (sample mode) as a library and CLI | 25 | 24 | 0 | 1 | 0 | 0 |
| #7 | Agent: POST /run, async /runs + SSE trace stream, Docker sample mode (B-1) [stacked on #6] | 15 | 14 | 0 | 1 | 0 | 0 |
| #8 | Agent: Loop B (sample mode) and run_investigation (stacked on #6) | 20 | 18 | 0 | 2 | 0 | 0 |
| #9 | Agent: discovery through Zetaris, any asked company (live), phase trace lines, live latency measurement | 11 | 10 | 0 | 1 | 0 | 0 |
| #10 | Status after wave 2 (#6, #7, #8, #9) | 7 | 6 | 0 | 1 | 0 | 0 |
| #11 | Docs: claims match the code (memory, sponsor technology, scripts row) | 6 | 4 | 1 | 1 | 0 | 0 |
| #12 | Agent: entity directory (GET /entities + suggestions), scope check with alternatives, breakdown frontier | 12 | 11 | 0 | 1 | 0 | 0 |
| #13 | Source scout: find the counterparty of a disposal from filing evidence (closes B-5) | 9 | 8 | 0 | 1 | 0 | 0 |
| #15 | Loop B: prune an add-back that over-explains beyond MAX_OVER_EXPLAINED (closes B-15) | 6 | 5 | 0 | 1 | 0 | 0 |
| #16 | Status after #12, #13, #15; Meterless wording; BACKLOG B-19 | 4 | 3 | 1 | 0 | 0 | 0 |
| #17 | AGENTS.md holds invariants only: reference doc, gotchas routed, backlog severity | 7 | 5 | 0 | 1 | 1 | 0 |
| **All** | | **158** | **141** | **3** | **13** | **1** | **0** |

Every superseded claim was re-checked at its new location and holds there. Eight of the thirteen come from #17 (the others: #7 and #10 for `/run`, #9 for a renamed test, #12 for `/entities`, #18 for the `adapters/zetaris.py` row, feat/model-lanes for #11 criterion 1; #9 criterion 11 is superseded by #17 and #18, #10 criterion 3 by #12 and feat/model-lanes, #17 criterion 2 by #18 and feat/model-lanes), which moved the Building blocks, Run phases and gotchas out of `AGENTS.md` into `docs/agents-reference.md`, `zetaris/README.md` and `BACKLOG.md`, and turned closed BACKLOG items into one-liners, so `grep … AGENTS.md` and `grep '^## B-n' BACKLOG.md` verifiers no longer find them where they were.

## Per PR

Verifier text is shortened: `pytest` = the pytest invocation, `module::test` = `tests/test_module.py::test`. Statements are cut to their first sentence.

### #1: Agent: JSONL tracer and run models

| # | Criterion | Verifier | Class | Evidence |
|---|---|---|---|---|
| 1 | Each `Tracer.emit` call appends exactly one JSON line to `<logs_dir>/trace-<run_id>.jsonl` and prints the sam… | `pytest trace::test_emit_writes_jsonl_file_and_stdout trace::test_fields_cannot_override_core_keys` | **pass** | 2 passed |
| 2 | A missing run id, or one that does not match `^[A-Za-z0-9._-]+$`, writes no file. | `pytest tests/test_trace.py -k "unsafe_or_missing or write_failure"` | **pass** | 6 passed, 16 deselected |
| 3 | Values under keys containing token, key, authorization or password (case-insensitive, also nested), and `Bear… | `pytest trace::test_secret_looking_fields_are_redacted` | **pass** | 1 passed |
| 4 | `Tracer.span` emits exactly once with an integer `duration_ms`. | `pytest tests/test_trace.py -k "span or null_tracer"` | **pass** | 5 passed, 17 deselected |
| 5 | All three `input_examples/*.json` validate as `RunRequest`. | `pytest tests/test_models.py -k "input_examples or rejects_invalid"` | **pass** | 10 passed, 13 deselected |
| 6 | The models and `contracts/openapi/agent.yaml` agree on the six end states, the link labels, the evidence stat… | `pytest models::test_models_match_contract_enums models::test_all_six_end_states_accepted` | **pass** | 7 passed |
| 7 | The service's full test suite, lint, format check and secret scan all pass. | `pytest && ruff check src tests && ruff format --check src tests && cd ../.. && ./scripts/check_secrets.sh` | **pass** | 343 passed, 1 skipped (cognition live test, no server); ruff check: All checks passed; format: 50 files already formatted; secret scan PASS |

### #2: Agent: Nightshift Runtime client (lanes, receipt, offline fallback)

| # | Criterion | Verifier | Class | Evidence |
|---|---|---|---|---|
| 1 | Every chat request sends `Authorization: | `pytest nightshift::test_sends_meterless_headers_and_lane_model_without_leaking_token` | **pass** | 1 passed |
| 2 | routed_model and provider are read from the receipt (`meterless.routed_model`, `winning_route.provider`), and… | `pytest nightshift::test_routed_model_and_provider_come_from_receipt nightshift::test_missing_receipt_gives_none_not_t…` | **pass** | 2 passed |
| 3 | A 503 is retried exactly once and then succeeds, with retry_count 1 in the trace; | `pytest nightshift::test_retries_once_on_503_then_succeeds nightshift::test_persistent_503_raises_recoverable_unavaila…` | **pass** | 2 passed |
| 4 | A 400 is not retried and raises ModelProtocolError whose message does not contain the token, even when the se… | `pytest nightshift::test_400_is_not_retried_and_is_a_protocol_error` | **pass** | 1 passed |
| 5 | Connection refused raises ModelUnavailable(recoverable=True) after one retry, without the token in the except… | `pytest nightshift::test_connection_error_retries_once_then_unavailable_without_token` | **pass** | 1 passed |
| 6 | Missing URL/token or the `.env.example` placeholder token makes the config not configured, and chat then rais… | `pytest tests/test_nightshift.py -k "not_configured or unconfigured"` | **pass** | 7 passed, 27 deselected |
| 7 | chat_json parses unfenced and fenced JSON and raises ModelProtocolError on prose. | `pytest tests/test_nightshift.py -k chat_json` | **pass** | 4 passed, 30 deselected |
| 8 | The probe CLI exits non-zero with a ✖ message when Nightshift is unconfigured or unreachable, and never print… | `pytest tests/test_nightshift.py -k probe and python -m tiresias_agent.adapters.nightshift probe; echo $? (prints ✖…` | **pass** | `-k probe`: 3 passed, 31 deselected; `nightshift probe` with no env: prints ✖ "Nightshift is not configured", token `missing`, exit 2 |

### #3: Agent: cognition (H-MEM) client with degraded fallback

| # | Criterion | Verifier | Class | Evidence |
|---|---|---|---|---|
| 1 | Every endpoint is called with the server's method, path and body field names, and carries X-Run-Id. | `pytest tests/test_cognition.py -k "test_health or request"` | **pass** | 8 passed, 19 deselected |
| 2 | When cognition is unreachable or times out, recall returns no lessons with available=False and emits a "degra… | `pytest cognition::test_unreachable_recall_degrades_and_is_traced` | **pass** | 2 passed |
| 3 | When cognition is unreachable, remember and feedback report nothing stored or applied, and the run continues. | `pytest cognition::test_unreachable_remember_and_feedback_report_not_stored` | **pass** | 1 passed |
| 4 | A purge that the server did not confirm (refused, timeout, 5xx, or no purged count) raises CognitionError and… | `pytest cognition::test_purge_failure_raises_never_reports_success` | **pass** | 4 passed |
| 5 | The client leaves recall relevance filtering to the server: | `pytest cognition::test_recall_leaves_threshold_to_the_server` | **pass** | 1 passed |
| 6 | Feedback on an unknown memory id returns HTTP 404 NotFound from the cognition server, not 500. | `cd services/cognition && npm test` | **pass** | `npm test`: 6 tests, 6 pass; `test/memory.test.ts:73` asserts 404 for `no-such-id` |
| 7 | Against a real cognition server, remember → recall → feedback → purge → recall round-trips through the Python… | `cd services/cognition && COGNITION_PORT=8199 MEMORY_DIR=$(mktemp -d) npm start & cd services/agent && COGNITION_URL=h…` | **pass** | local cognition server on :8199 (`npm start`, temp `MEMORY_DIR`, not Docker); `test_cognition.py -k live`: 1 passed; verifier as written (`-k live`, whole suite): 24 passed, 320 deselected |

### #4: Status after wave 1 (#1, #2, #3, #5)

| # | Criterion | Verifier | Class | Evidence |
|---|---|---|---|---|
| 1 | Every CHECKLIST line this PR ticks `[x]` or `[~]` cites evidence that exists on this branch: | `git diff origin/main -- docs/CHECKLIST.md \| grep '^+' ; git log --oneline \| grep -E '\(#[0-9]+\)' ; ls services/agent…` | **pass** | adapted (branch diff is empty after merge): the 6 lines #4 added to CHECKLIST (`git show 50c4fb6`) cite #1, #2, #3, #5, all `(#N)` commits in `git log`; the 4 cited modules exist; `zetaris/sql/` has 22 templates (12 at #4, more added later) |
| 2 | The status of each sponsor and feature is the same across CHECKLIST, README, metadata.json and AGENTS.md, and… | `grep -n '"status"' metadata.json ; grep -n '^\| \*\*' README.md ; grep -n -i 'zetaris\\|nightshift\\|/run' docs/CHECKLIS…` | **superseded** | #7 made `/run` work (now `@app.post("/run")`, only `/runs/{id}/branch` and `/memory/purge` are 501) and #10 rewrote the status rows. Moved claim holds: metadata.json, README sponsor table, CHECKLIST and sponsor-technology.md all say Zetaris and Meterless `in-progress`, none `working` |
| 3 | Every module, class, function and CLI command named in AGENTS.md (Layout, Building blocks, Commands) exists w… | `python -c "from tiresias_agent.trace import Tracer, NullTracer; from tiresias_agent.models import RunRequest, RunResu…` | **pass** | imports print `ok`; `adapters.zetaris --help` lists `{bootstrap,smoke}` and `--sample`; `adapters.nightshift --help` prints the `probe` usage but exits 2 (no argparse, same at #4's merge), so the verifier as written exits non-zero; the statement holds and the reproducible command checks the usage line instead (the Building blocks names now live in docs/agents-reference.md, #17) |
| 4 | AGENTS.md and the PR template are internally consistent: | `grep -n -i 'round\\|merge\\|MERGE_PERMITTED\\|AGENTS.md\\|status docs\\|CHECKLIST' AGENTS.md .github/pull_request_template…` | **fail** | AGENTS.md:103 (rule 4) says update CHECKLIST, STATUS and metadata.json "in the same commit as the work"; AGENTS.md:97 says parallel PRs don't edit them and the coordinator does after each merge; the PR template line 27 repeats "in the same change". Both lines were already present at #4's merge |
| 5 | Metadata stays valid against the schema and the secret scan passes. | `./scripts/validate_metadata.sh && ./scripts/check_secrets.sh` | **pass** | `metadata.json valid`; secret scan PASS |

### #5: Agent: Zetaris adapter (live + sample) and registration SQL

| # | Criterion | Verifier | Class | Evidence |
|---|---|---|---|---|
| 1 | The live client sends Bearer auth, X-Org-ID, a UUID X-Request-ID and X-Run-Id, and returns rows keyed by colu… | `pytest zetaris::test_run_sql_sends_auth_org_request_id_and_run_id_headers` | **pass** | 1 passed |
| 2 | 5xx and timeouts are retried at most MAX_RETRIES times with a fresh X-Request-ID, then raise a recoverable So… | `pytest tests/test_zetaris.py -k "retries_503 or does_not_retry_400 or timeout_becomes"` | **pass** | 3 passed, 23 deselected |
| 3 | The Zetaris API key never appears in SourceError text or repr, in the config or client repr, or in any tracer… | `pytest tests/test_zetaris.py -k "never_in_errors or masks_sec_user_agent or hides_key"` | **pass** | 3 passed, 23 deselected |
| 4 | SampleSource, reading the committed data/sample files, returns GE FY2022 revenue facts that include both the … | `pytest tests/test_zetaris.py -k "fy2022 or alias_resolves"` | **pass** | 2 passed, 24 deselected |
| 5 | In live mode, a Zetaris outage degrades the call instead of crashing it: | `pytest zetaris::test_live_outage_falls_back_to_sample_and_is_traced_degraded` | **pass** | 1 passed |
| 6 | Every SQL template renders with no unreplaced placeholder, and identifiers are validated before substitution,… | `pytest tests/test_zetaris.py -k "templates_render or identifiers_are_validated"` | **pass** | 1 passed, 25 deselected |
| 7 | bootstrap is safe to run twice and never sends DROP, DELETE or TRUNCATE. | `pytest zetaris::test_bootstrap_is_idempotent_and_never_drops` | **pass** | 1 passed |
| 8 | AGENTS.md documents what this PR adds so later tasks can build on it: | `grep -n "make_source\\|bootstrap\\|CACHE TABLE\\|SAMPLE_DATA_DIR" AGENTS.md && grep -A6 "## B-1" BACKLOG.md && cd servic…` | **superseded** | #17 moved Building blocks and gotchas out of AGENTS.md (`make_source`, `CACHE TABLE`, `SAMPLE_DATA_DIR`: 0 hits in AGENTS.md now). Moved claims hold: `make_source`/`Evidence`/`bootstrap` in docs/agents-reference.md, bootstrap/smoke commands in AGENTS.md, cache-restart in zetaris/README.md#quirks + B-20, Docker data in agents-reference.md:39, B-1 a closed one-liner (closed in #7); imports print `ok` |
| 9 | The package passes lint, format and the repository secret scan. | `ruff check src tests && ruff format --check src tests && cd ../.. && ./scripts/check_secrets.sh` | **pass** | ruff check: All checks passed; format: 50 files already formatted; secret scan PASS |

### #6: Agent: Loop A (sample mode) as a library and CLI

| # | Criterion | Verifier | Class | Evidence |
|---|---|---|---|---|
| 1 | All three input examples run in sample mode to a valid end state without crashing, and the RunResult validate… | `pytest orchestrator::test_each_example_reaches_a_valid_end_state` | **pass** | 3 passed |
| 2 | Every trace line of a sample run has run_id, a valid status and mode "sample" (ADR-0007), including the adapt… | `pytest orchestrator::test_every_trace_line_has_run_id_status_and_sample_label` | **pass** | 3 passed |
| 3 | Agents interact more than once, the critic sends work back (retry to the analyst or prune to the hypothesiser… | `pytest orchestrator::test_agents_interact_more_than_once_and_critic_sends_work_back` | **pass** | 3 passed |
| 4 | In the restatement cases the critic's retry makes the analyst re-query with a narrower window (temporal test)… | `pytest orchestrator::test_restatement_cases_show_a_retry_with_a_narrower_query` | **pass** | 1 passed |
| 5 | When no registered source can test the candidate, Loop A ends at evidence-frontier with a structured Informat… | `pytest orchestrator::test_restatements_end_at_the_evidence_frontier_with_an_information_need` | **pass** | 1 passed |
| 6 | The net-loss example (different concepts) ends actionable for the board, with breakdown, causal and backgroun… | `pytest orchestrator::test_net_loss_case_is_actionable_for_the_board` | **pass** | 1 passed |
| 7 | Outputs differ meaningfully across the three cases (actions, root causes, effects, at least two end states, d… | `pytest orchestrator::test_outputs_differ_across_cases` | **pass** | 1 passed |
| 8 | Every why-tree node carries its evidence (SQL and rows) per query. | `pytest orchestrator::test_why_tree_links_carry_sql_and_rows` | **pass** | 1 passed |
| 9 | Agent logic (agents/*.py, orchestrator.py, gates.py) contains no case-specific literals (entity names or tick… | `pytest orchestrator::test_agent_logic_has_no_case_specific_literals` | **pass** | 19 passed |
| 10 | The depth cap (MAX_CAUSAL_DEPTH) ends a run in end state budget instead of looping. | `pytest orchestrator::test_depth_cap_ends_in_budget` | **pass** | 1 passed |
| 11 | The wall-clock budget ends a run in end state budget. | `pytest orchestrator::test_wall_clock_budget_ends_in_budget` | **pass** | 1 passed |
| 12 | MAX_BRANCHES_PER_LEVEL caps the candidates tested per level. | `pytest orchestrator::test_branch_cap_is_respected` | **pass** | 1 passed |
| 13 | Budgets are env-tunable and clamped (defaults 5 / 3 / 600 s). | `pytest tests/test_config.py` | **pass** | 3 passed |
| 14 | An unexpected exception inside a run becomes a structured error result, never a stack trace. | `pytest orchestrator::test_unexpected_failure_is_a_structured_error_not_a_stack_trace` | **pass** | 1 passed |
| 15 | An entity or metric absent from the registered sources ends at evidence-frontier with an information need for… | `pytest orchestrator::test_unknown_entity_is_an_evidence_frontier_for_the_metric` | **pass** | 1 passed |
| 16 | The gate interface is general: | `pytest tests/test_gates.py` | **pass** | 6 passed |
| 17 | A scripted gate decision changes the run (pruning the frontier candidate) and is traced as decided_by script. | `pytest orchestrator::test_scripted_gate_can_prune_the_frontier` | **pass** | 1 passed |
| 18 | The critic implements causal-criteria.md: | `pytest tests/test_critic.py` | **pass** | 17 passed |
| 19 | The hypothesiser generates candidates from generic rules (hierarchy children, event concepts under the compon… | `pytest tests/test_hypothesiser.py` | **pass** | 5 passed |
| 20 | The framer frames a restatement from the two named filings, a change like-for-like from one filing, and fails… | `pytest tests/test_framer.py` | **pass** | 6 passed |
| 21 | The CLI prints only the RunResult JSON on stdout and writes logs/trace-<run_id>.jsonl; | `pytest orchestrator::test_cli_prints_result_json_and_writes_trace orchestrator::test_cli_bad_input_is_a_structured_er…` | **pass** | 2 passed |
| 22 | contracts/openapi/agent.yaml matches the extended result models field for field (RunResult, Link, EvidenceIte… | `pytest models::test_result_shapes_match_the_contract_field_for_field` | **pass** | 1 passed |
| 23 | Tracer defaults are added to every line and a call's own fields win. | `pytest trace::test_defaults_are_added_to_every_line_and_fields_win` | **pass** | 1 passed |
| 24 | Lint and format are clean. | `ruff check src tests && ruff format --check src tests` | **pass** | All checks passed!; 50 files already formatted |
| 25 | AGENTS.md (Building blocks, Commands, gotchas, Next work) describes what this PR adds, so later tasks reuse i… | `grep -n "run_loop_a\\|LoopBudgets\\|MAX_RETRIES_PER_NODE\\|RUN_BUDGET_SECONDS\\|agents/framer.py\\|agents/hypothesiser.py\…` | **superseded** | #17 moved the Building blocks rows to docs/agents-reference.md. Moved claim holds: all 15 names grep in AGENTS.md or docs/agents-reference.md (e.g. `run_loop_a` 0 → 1, `agents/critic.py` 0 → 1) |

### #7: Agent: POST /run, async /runs + SSE trace stream, Docker sample mode (B-1) [stacked on #6]

| # | Criterion | Verifier | Class | Evidence |
|---|---|---|---|---|
| 1 | POST /run on each of the three input examples returns 200 with a body that validates as RunResult, the X-Run-… | `pytest api::test_run_on_all_three_examples_returns_distinct_valid_results` | **pass** | 1 passed, 1 warning |
| 2 | A body missing required fields gets 422 in the contract's Error shape ({status: | `pytest api::test_bad_body_is_a_structured_422` | **pass** | 1 passed, 1 warning |
| 3 | Malformed JSON and an X-Run-Id outside [A-Za-z0-9._-]{1,100} (e.g. | `pytest api::test_malformed_json_is_a_structured_422 api::test_invalid_run_id_header_is_a_structured_422` | **pass** | 2 passed, 1 warning |
| 4 | A valid X-Run-Id is honoured and echoed: | `pytest api::test_x_run_id_is_echoed_and_names_the_trace_file` | **pass** | 1 passed, 1 warning |
| 5 | If the pipeline raises, POST /run returns a structured 500 (run_id, error.type, error.recoverable=false, no s… | `pytest api::test_orchestrator_failure_is_a_structured_500` | **pass** | 1 passed, 1 warning |
| 6 | POST /run is bounded by the run budget: | `pytest api::test_run_that_outlives_the_budget_is_a_structured_504` | **pass** | 1 passed, 1 warning |
| 7 | POST /runs returns 202 {run_id} immediately; | `pytest api::test_async_run_lifecycle` | **pass** | 1 passed, 1 warning |
| 8 | GET /runs/{id}/events streams text/event-stream: | `pytest api::test_sse_streams_trace_lines_then_end` | **pass** | 1 passed, 1 warning |
| 9 | The registry is bounded: | `pytest api::test_registry_cap_evicts_the_oldest_finished_run api::test_registry_refuses_beyond_max_concurrent api::te…` | **pass** | 3 passed, 1 warning |
| 10 | Tracer's new sink is backward compatible: | `pytest trace::test_sink_gets_each_line_and_a_failing_sink_never_breaks_emit trace::test_tracer_without_sink_is_unchan…` | **pass** | 2 passed |
| 11 | The agent image's build context is the repo root and includes every file of data/sample, zetaris/sql, catalog… | `pytest tests/test_packaging.py` | **pass** | 3 passed |
| 12 | /runs/{id}/branch and /memory/purge remain structured 501 NotImplemented; | `pytest tests/test_health.py` | **pass** | 2 passed, 1 warning |
| 13 | The whole agent suite passes and lint/format are clean. | `pytest && python -m ruff check src tests` | **pass** | 343 passed, 1 skipped, 1 warning; ruff clean |
| 14 | contracts/openapi/agent.yaml documents /run (200/409/422/429/500/504, X-Run-Id), /runs (202), /runs/{run_id} … | `python3 -c "import yaml;d=yaml.safe_load(open('contracts/openapi/agent.yaml'));p=d['paths'];assert {'/run','/runs','/…` | **pass** | run with the agent's uv Python (PyYAML): prints `ok`; paths also include `/entities`, `/entities/{cik}/suggestions`, `/health` |
| 15 | AGENTS.md (Layout row, Building blocks for runs.py/app.py/Tracer sink, Commands with curl + SSE, gotchas for … | `grep -n "runs.py\\|RunRegistry\\|runs.execute\\|RUNS_REGISTRY_SIZE\\|MAX_CONCURRENT_RUNS\\|MAX_TRACE_LINES_PER_RUN\\|CORS_A…` | **superseded** | #17 moved the runs.py/app.py/sink rows and gotchas to docs/agents-reference.md and turned B-1 into a closed one-liner. Moved claim holds: every name found in AGENTS.md or agents-reference.md; README has the quickstart (`cp .env.example .env`, `docker compose up --build`, uv path); no `Not runnable yet` / `RUN_TIMEOUT_SECONDS` (grep exit 1); `.env.example` has `RUN_BUDGET_SECONDS=600` |

### #8: Agent: Loop B (sample mode) and run_investigation (stacked on #6)

| # | Criterion | Verifier | Class | Evidence |
|---|---|---|---|---|
| 1 | The source scout resolves each restatement case's hint to the disposed business from the data (case 01 → CIK … | `pytest loop_b::test_scout_resolves_the_disposed_business_from_the_data` | **pass** | 2 passed |
| 2 | The scout's rules are generic: | `pytest loop_b::test_scout_assessment_rules_on_synthetic_registrants` | **pass** | 1 passed |
| 3 | The scout refuses (unresolved, with a reason) a tie, a disposal no candidate matches, a hint without a dispos… | `pytest loop_b::test_scout_refuses_a_tie_and_an_unmatched_disposal loop_b::test_scout_refuses_a_hint_without_a_disposa…` | **pass** | 3 passed |
| 4 | Every discovery read the scout makes is a catalog endpoint (catalog-only). | `pytest loop_b::test_scout_reads_only_catalog_endpoints` | **superseded** | `tests/test_loop_b.py::test_scout_reads_only_catalog_endpoints` not found (0 tests): #9 replaced SEC HTTP discovery with Zetaris SQL and renamed it `test_scout_reads_only_catalog_objects_as_zetaris_sql`. Moved claim holds: 1 passed |
| 5 | No case-specific literals (tickers, names, CIKs, dates, amounts of the examples) in agent logic, including th… | `pytest tests/test_orchestrator.py -k "case_specific_literals or guard_covers"` | **pass** | 20 passed, 24 deselected |
| 6 | The onboarder registers only behind the source gate: | `pytest loop_b::test_onboarder_auto_gate_registers_in_sample_mode_and_says_so` | **pass** | 1 passed |
| 7 | A scripted `{"source": | `pytest loop_b::test_onboarder_scripted_deny_registers_nothing loop_b::test_scripted_source_deny_keeps_the_frontier` | **pass** | 2 passed |
| 8 | MAX_NEW_SOURCES_PER_RUN is enforced (default 2, clamped 0..5): | `pytest loop_b::test_onboarder_respects_the_source_cap_without_asking_the_gate loop_b::test_source_cap_zero_ends_in_bu…` | **pass** | 3 passed |
| 9 | Live registration reuses `bootstrap` for the one entity's facts table only, one statement per request, and re… | `pytest loop_b::test_onboarder_live_registers_one_statement_at_a_time_and_refreshes_views loop_b::test_onboarder_live_…` | **pass** | 3 passed |
| 10 | The add-back math is correct: | `pytest tests/test_loop_b.py -k reconcile` | **pass** | 9 passed, 33 deselected |
| 11 | run_investigation over all three input_examples reaches valid end states. | `pytest tests/test_loop_b.py -k "investigation or restatements_continue or net_loss"` | **pass** | 8 passed, 34 deselected |
| 12 | Outputs differ across the three cases (action, root cause, residual). | `pytest loop_b::test_investigation_outputs_differ_across_cases` | **pass** | 1 passed |
| 13 | Missing sample data for the target entity (its companyfacts or its submissions left out of a tmp copy) does n… | `pytest loop_b::test_missing_sample_data_for_the_target_leaves_an_evidence_frontier` | **pass** | 2 passed |
| 14 | A non-recoverable SourceError on the new entity is not turned into sample evidence: | `pytest loop_b::test_non_recoverable_source_error_is_not_turned_into_sample_evidence` | **pass** | 1 passed |
| 15 | Loop B respects the run's wall-clock budget, and an unframed effect skips Loop B with a traced reason. | `pytest loop_b::test_wall_clock_budget_applies_to_loop_b loop_b::test_unframed_effect_skips_loop_b_with_a_trace_line` | **pass** | 2 passed |
| 16 | `run_loop_a` keeps its behaviour and signature (every Loop A test passes unchanged), and the CLI runs `run_in… | `pytest tests/test_orchestrator.py loop_b::test_cli_runs_the_investigation_and_loop_a_only_on_request` | **pass** | 45 passed |
| 17 | The catalog loader parses catalog/sources.yaml and rejects anything outside its YAML subset. | `pytest tests/test_loop_b.py -k catalog` | **pass** | 4 passed, 38 deselected |
| 18 | The whole suite and lint pass. | `pytest && ruff check src tests && ruff format --check src tests` | **pass** | 343 passed, 1 skipped, 1 warning; ruff clean; format clean |
| 19 | AGENTS.md (Building blocks, Commands, gotchas, Next work) describes what this PR adds, contracts/openapi/agen… | `grep -n "run_investigation\\|LoopB\\|source_scout\\|onboarder.py\\|Registry\\|reconciler\\|make_discovery\\|load_catalog\\|MA…` | **superseded** | #17 moved Building blocks to docs/agents-reference.md and closed items to one-liners (`^## B-[4-7]` now matches only B-6). Moved claim holds: every listed name in AGENTS.md or agents-reference.md; 4 `reconcile\|signed` hits in agent.yaml; B-6 open in full, B-4/B-5/B-7 closed lines naming #9/#13 |
| 20 | (added by the coordinator after rebasing onto main, which now has #7) The HTTP API runs the full investigatio… | `pytest tests/test_api.py && grep -n 'run_investigation\\|run_loop_a' src/tiresias_agent/runs.py && grep -n -i 'loop b'…` | **pass** | `test_api.py`: 13 passed; runs.py:52 calls `orchestrator.run_investigation`; no README/AGENTS/contract line says Loop B is planned or `/run` is Loop A only |

### #9: Agent: discovery through Zetaris, any asked company (live), phase trace lines, live latency measurement

| # | Criterion | Verifier | Class | Evidence |
|---|---|---|---|---|
| 1 | In live code paths nothing talks to SEC except through Zetaris: | `pytest any_company::test_only_the_sponsor_adapters_import_an_http_client_and_no_code_names_sec` | **pass** | 1 passed |
| 2 | Live discovery is Zetaris SQL: | `pytest tests/test_any_company.py -k "zetaris_discovery or make_discovery_live"` | **pass** | 3 passed, 18 deselected |
| 3 | Discovery registrations do not count toward MAX_NEW_SOURCES_PER_RUN. | `pytest tests/test_any_company.py -k "live_unknown_company or live_second_run or reuses_it_when_warm"` | **pass** | 3 passed, 18 deselected |
| 4 | A live company that is unresolvable, ambiguous (several registrants by name, or a ticker and CIK that disagre… | `pytest tests/test_any_company.py -k "unresolvable or gate_deny or outage"` | **pass** | 5 passed, 16 deselected |
| 5 | Sample mode, company not in `data/sample/`: | `pytest tests/test_any_company.py -k "sample_mode"` | **pass** | 2 passed, 19 deselected |
| 6 | Registration is bounded by the run budget: | `pytest tests/test_any_company.py -k "run_budget or discovery_objects"` | **pass** | 2 passed, 19 deselected |
| 7 | Phase trace lines for a UI: | `pytest tests/test_any_company.py -k "phase"` | **pass** | 2 passed, 19 deselected |
| 8 | The scout never uses a generic first word ("International", "General", …) as the brand, so a coincidental reg… | `pytest tests/test_loop_b.py` | **pass** | 42 passed |
| 9 | The whole agent suite and lint pass. | `pytest && ruff check src tests && ruff format --check src tests` | **pass** | 343 passed, 1 skipped, 1 warning; ruff clean; format clean |
| 10 | The measurement is real and documented: | `grep -n "IBM\\|JNJ\\|MMM\\|XOM\\|CAT\\|CXT\\|HLT\\|2.4.3-v2.4.3.1\\|2026-10-23" docs/measurements/live-any-company.md; python…` | **pass** | grep hits: IBM 7, JNJ 2, MMM 3, XOM 3, CAT 2, CXT 2, HLT 3, `2.4.3-v2.4.3.1` 1, `2026-10-23` 3; `perf_smoke.py --help` exits 0 (help only, no live run). Numbers not re-compared with the PR body table |
| 11 | AGENTS.md (Building blocks: | `grep -n "entity_resolver\\|onboard_entity\\|ZetarisDiscovery\\|phase(tracer\\|perf_smoke\\|Run phases\\|Cold vs warm" AGENT…` | **superseded** | #17 (and #18) moved Building blocks, Run phases and the cold/warm gotcha to docs/agents-reference.md. Moved claim holds: `onboard_entity`, `ZetarisDiscovery`, `phase(tracer`, `Run phases` found there; cold/warm is agents-reference.md:57; README `Ask about your own company` 1 hit; agent.yaml 16 `name:\|phase_start` hits; B-3/B-4/B-7 closed with resolution, B-8..B-11 open with all fields; #9's merge commit touches none of CHECKLIST/STATUS/metadata.json; #18 then closed B-9 (`- **B-9** (found in #9, closed in #18)`), so the check is B-8, B-10, B-11 open and B-9 closed |

### #10: Status after wave 2 (#6, #7, #8, #9)

| # | Criterion | Verifier | Class | Evidence |
|---|---|---|---|---|
| 1 | Every CHECKLIST line this PR ticks `[x]` or `[~]` cites evidence that exists on this branch: | `git diff origin/main -- docs/CHECKLIST.md \| grep '^+' ; git log --oneline \| grep -E '\(#[1-9]\)' ; ls services/agent/…` | **pass** | adapted: the cited tests (17 selectors): 46 passed; the 9 cited files exist; #6..#9 are `(#N)` commits |
| 2 | The behavioural claims behind the ticks match the code: | `for f in 01_ge_2022_revenue_restated 02_ge_2023_revenue_restated 03_ge_2018_net_loss; do python -m tiresias_agent.orc…` | **pass** | CLI sample mode: 01 `actionable 0.0062`, 02 `actionable -0.0194`, 03 `actionable 0.0`; app.py:115 `@app.post("/run")`, :279 binds `0.0.0.0`; 501 only for `/runs/{run_id}/branch` and `/memory/purge` |
| 3 | Items still marked open or partial are really unbuilt: | `grep -rln 'cognition\\|nightshift' services/agent/src/tiresias_agent \| grep -v adapters/ \| grep -v 'config.py\\|trace.p…` | **superseded** | #12 added `GET /entities` (app.py:224), so "no `/entities` route" no longer holds by design, and feat/model-lanes (ADR-0010) uses the Nightshift adapter on purpose. Moved claim holds: the only importers of `nightshift` outside adapters/ are `agents/model_lanes.py` (`NightshiftClient`) and `orchestrator.py` (one line, `NightshiftConfig` to resolve the switch); no cognition importer; `tests/test_model_lanes.py` shows the lanes are off by default in sample mode (0 model calls) and resolve on only as configured, and each call is traced; `"cyclic"` only in models.py and reporter.py; no `ask=` in orchestrator/app/runs |
| 4 | Sponsor statuses agree across metadata.json, README's sponsor table, CHECKLIST and docs/STATUS.md, and none s… | `grep -n '"status"' metadata.json ; grep -n '^\| \*\*' README.md ; grep -n -i 'zetaris\\|meterless\\|nightshift\\|nvidia\\|…` | **pass** | metadata: zetaris and meterless `in-progress`, cursor and nvidia `planned`, no `working`; README rows say "in progress"/"planned"/"optional"; CHECKLIST and STATUS agree (Cursor's `planned` vs `optional` is outside this criterion's falsifier; see #11 criterion 3) |
| 5 | STATUS.md keeps every earlier question and owner, marks only resolved items as resolved (#4 latency, #6 Zetar… | `git diff origin/main -- docs/STATUS.md ; grep -n 'B-4' -A1 BACKLOG.md ; grep -c . logs/samples/* 2>/dev/null; ls logs…` | **pass** | adapted (branch diff empty): STATUS.md keeps questions 1-7 with owners; #4 and #6 resolved with evidence (measurement doc; #9, B-4 closed); #1, #2, #3, #5, #7 still open and still unbuilt |
| 6 | The PR changes only the four allowed files, and README edits are confined to the sponsor table. | `git diff --name-only origin/main ; git diff origin/main -- README.md` | **pass** | adapted: `git show --name-only bb0e309` = README.md, docs/CHECKLIST.md, docs/STATUS.md, metadata.json; README hunk is only the Zetaris and Meterless sponsor-table rows |
| 7 | Metadata stays valid against the schema and the secret scan passes. | `./scripts/validate_metadata.sh && ./scripts/check_secrets.sh` | **pass** | `metadata.json valid`; secret scan PASS |

### #11: Docs: claims match the code (memory, sponsor technology, scripts row)

| # | Criterion | Verifier | Class | Evidence |
|---|---|---|---|---|
| 1 | No sentence in README or `docs/sponsor-technology.md` claims memory, Nightshift or model calls are used by th… | `grep -n -i 'memory\\|recall\\|lesson\\|nightshift\\|model call\\|purge' README.md docs/sponsor-technology.md ; grep -rln '…` | **superseded** | feat/model-lanes (ADR-0010) makes the agent call Nightshift in live mode on purpose, and README ("Models") and sponsor-technology.md now say so. Moved claim holds: memory is still described as not used (no cognition importer outside adapters/, no "stores lessons"/"recalls them"); the Nightshift claims are backed by code: `agents/model_lanes.py` is the one user of `NightshiftClient`, README says sample mode uses the rule lanes only, and `tests/test_model_lanes.py` (sample default 0 calls, switch, one trace line per call) passes. At #11's merge it was `pass` |
| 2 | Every Zetaris mechanism `docs/sponsor-technology.md` describes exists in `zetaris/sql/` or the agent code. | `sed -n '/## Zetaris/,/## Meterless/p' docs/sponsor-technology.md ; ls zetaris/sql/ ; grep -n -i 'counterfactual' serv…` | **pass** | Zetaris section names REST tables, per-concept views, cache, the add-back over SQL rows and the counterfactual test; `zetaris/sql/` has `10..14` tables, `12_cache_table.sql`, `20..22` views; analyst.py has 6 `counterfactual` hits |
| 3 | Sponsor statuses agree across `docs/sponsor-technology.md`, `metadata.json` and README. | `grep -n 'Status:' docs/sponsor-technology.md ; grep -n '"name"\\|"status"' metadata.json ; grep -n '^\| \*\*' README.md` | **fail** | Zetaris `in-progress`, Meterless `in-progress`, NVIDIA `planned` agree in all three; Cursor is `"status": "planned"` in metadata.json but `optional` in README:118 and `optional, not a Tiresias dependency` in sponsor-technology.md:46 (the schema enum has no `optional`). Same at #11's merge |
| 4 | `cyclic` is marked as not yet produced, and nothing in src emits it. | `grep -n 'cyclic' README.md ; grep -rn 'cyclic' services/agent/src --include=*.py` | **pass** | README:28 lists `cyclic` with "(`cyclic` is not produced yet, BACKLOG B-2)"; in src only the `EndState` literal (models.py:15) and `SUCCESS_STATES` (reporter.py:30) |
| 5 | Every script AGENTS.md lists in `scripts/` exists, and every script in `scripts/` is listed; | `grep -n '^\| scripts/' AGENTS.md ; ls scripts/ ; grep -n 'Local Zetaris running' docs/CHECKLIST.md ; grep -n 'zetari…` | **pass** | AGENTS.md:29 lists check_secrets.sh, validate_metadata.sh, zetaris_probe.py, fetch_sample.py, perf_smoke.py = the five scripts in `scripts/` (plus `metadata.schema.json`, a schema, not a script); CHECKLIST:34 cites zetaris-local.md, which says "Reproduce with `python3 scripts/zetaris_probe.py`" |
| 6 | Only the four docs change, and both checks pass. | `git diff --name-only origin/main ; ./scripts/validate_metadata.sh && ./scripts/check_secrets.sh` | **pass** | adapted: `git show --name-only 20749a1` = AGENTS.md, README.md, docs/CHECKLIST.md, docs/sponsor-technology.md; both checks pass today |

### #12: Agent: entity directory (GET /entities + suggestions), scope check with alternatives, breakdown frontier

| # | Criterion | Verifier | Class | Evidence |
|---|---|---|---|---|
| 1 | `GET /entities` in sample mode lists exactly the slice (GE, GEHC, GEV) as tier `ready` with their metrics and… | `pytest entities_api::test_sample_entities_lists_the_slice_as_ready_and_says_why_nothing_else entities_api::test_sampl…` | **pass** | 2 passed, 1 warning |
| 2 | Live (fake Zetaris), `GET /entities?q=` returns registered entities as `ready` (named from `sec_tickers`, wai… | `pytest entities_api::test_live_entities_ready_and_searchable_tiers_with_measured_waits entities_api::test_live_partly…` | **pass** | 4 passed, 1 warning |
| 3 | The wait constants match the measurement doc (15–21 s cold, 3–11 s warm, measured 2026-10-23); | `pytest entities_api::test_measured_wait_constants_match_the_measurement_doc` | **pass** | 1 passed, 1 warning |
| 4 | GE's suggestions (sample) are derived, not hardcoded: | `pytest entities_api::test_the_three_examples_are_among_ges_derived_suggestions entities_api::test_every_suggestion_is…` | **pass** | 3 passed, 1 warning |
| 5 | `GET /entities/{cik}/suggestions` refuses with the contract's Error shape: | `pytest entities_api::test_suggestions_for_unknown_and_invalid_cik entities_api::test_live_suggestions_registered_regi…` | **pass** | 2 passed, 1 warning |
| 6 | The forecast rule catches forward/what-if questions with their cue and has no false positive on the listed pa… | `pytest tests/test_scope.py -k "forward or past_why or input_examples or after_today"` | **pass** | 21 passed, 8 deselected |
| 7 | A non-SEC what-if (live, fake Zetaris: | `pytest scope::test_live_non_sec_what_if_offers_the_named_registrant_validated_from_its_filings` | **pass** | 1 passed |
| 8 | A forecast about a registrant (sample GE, live ACME) offers the same entity's latest filed change and a same-… | `pytest scope::test_sample_forecast_for_a_registrant_offers_past_period_and_same_sic_peer scope::test_live_forecast_fo…` | **pass** | 5 passed |
| 9 | When every tested candidate is a ruled-out accounting/filing event, the frontier lists what was ruled out, sa… | `pytest scope::test_all_events_ruled_out_asks_for_a_breakdown_not_the_same_total` | **pass** | 1 passed |
| 10 | The three input examples' sample results are unchanged (end state, action, root cause, effect, why-tree) and … | `pytest tests/test_orchestrator.py any_company::test_sample_run_brackets_every_slow_phase` | **pass** | 45 passed |
| 11 | No entity literals in the new logic (`scope.py`, `suggester.py`, `entities.py` are in the guard, which now al… | `pytest tests/test_orchestrator.py -k "guard or literals" && pytest tests/test_models.py` | **pass** | 20 passed, 24 deselected; 23 passed |
| 12 | AGENTS.md (Building blocks: | `grep -n "entities.py\\|suggester\\|scope_check\\|for_breakdown\\|registrant(\\|FIRST_RUN_SECONDS\\|/entities" AGENTS.md REA…` | **superseded** | #17 moved Building blocks and Run phases to docs/agents-reference.md (`scope_check`, `for_breakdown`, `registrant(`, `FIRST_RUN_SECONDS` 0 hits in AGENTS.md now). Moved claim holds: all found in agents-reference.md; README:76 "Pick a company / suggested questions"; agent.yaml has both `/entities` paths; B-12..B-14 open in full |

### #13: Source scout: find the counterparty of a disposal from filing evidence (closes B-5)

| # | Criterion | Verifier | Class | Evidence |
|---|---|---|---|---|
| 1 | A spin-off whose new registrant shares no word with the parent's name is resolved from its Form 10 through Ze… | `pytest spinoff_match::test_a_differently_named_spin_off_is_resolved_from_its_form_10` | **pass** | 1 passed |
| 2 | A sale to an existing company (buyer's own 8-K item 2.01 near the disposal, annual reports before it) is refu… | `pytest spinoff_match::test_a_sale_to_an_existing_company_is_refused_with_the_reason spinoff_match::test_fulltext_endp…` | **pass** | 2 passed |
| 3 | Two counterparties equal on every ranking signal end unresolved with "ambiguous", and a run with no evidence … | `pytest spinoff_match::test_two_equal_counterparties_are_ambiguous_and_stay_at_the_frontier spinoff_match::test_no_evi…` | **pass** | 2 passed |
| 4 | An annual report before the disposal is tolerated only if it is the single one, after the candidate's own For… | `pytest spinoff_match::test_one_annual_report_after_its_own_form_10_is_tolerated` | **pass** | 1 passed |
| 5 | The searched phrase is the name in force at the disposal (former name if the date falls in its range) without… | `pytest spinoff_match::test_search_phrase_uses_the_name_in_force_at_the_disposal spinoff_match::test_fulltext_endpoint…` | **pass** | 2 passed |
| 6 | Sample mode keeps the three examples' results (01 actionable residual +0.0062, 02 actionable −0.0194, 03 acti… | `pytest tests/test_loop_b.py tests/test_orchestrator.py spinoff_match::test_sample_mode_has_no_full_text_search_and_sa…` | **pass** | 87 passed |
| 7 | No direct SEC HTTP and no case literals in agent logic: | `pytest any_company::test_only_the_sponsor_adapters_import_an_http_client_and_no_code_names_sec orchestrator::test_age…` | **pass** | 20 passed |
| 8 | The live measurement is recorded honestly: | `sed -n '/2026-10-23 (evening)/,$p' docs/measurements/live-any-company.md` | **pass** | section present: pass 3 at `a54daec`, corrected 3M dates, the Zetaris slowdown, pass-A failures (Crane refusal, DuPont → Dow 636%, `/A` forms emptying the 8-K search, DuPont's empty HTTP 400 registration, B-18). Scratch traces not available to cross-check the numbers |
| 9 | AGENTS.md (source_scout and catalog Building blocks rows, gotchas), README (the limitation line, not the spon… | `grep -n "filers_naming\\|NamingRow\\|search_phrase\\|REGISTRATION_FORMS\\|REGISTRATION_LOOKBACK_DAYS\\|MAX_NAMED_CANDIDATE…` | **superseded** | #17 moved the source_scout and catalog rows to docs/agents-reference.md. Moved claim holds: `filers_naming`, `NamingRow`, `search_phrase`, `REGISTRATION_*`, `MAX_NAMED_CANDIDATES`, `NAMED_SHARE` in agents-reference.md; `fulltext_endpoint`, `fulltext_table`, `sec_fulltext`, `fts_` in zetaris/README.md; all exist in the code; B-5 closed (#13), B-16..B-18 open with all fields |

### #15: Loop B: prune an add-back that over-explains beyond MAX_OVER_EXPLAINED (closes B-15)

| # | Criterion | Verifier | Class | Evidence |
|---|---|---|---|---|
| 1 | `reconciler.judge` grades over-explanation in three bands: | `pytest loop_b::test_reconcile_over_explained_bands loop_b::test_reconcile_over_explained_beyond_tolerance_escalates l…` | **pass** | 3 passed |
| 2 | The bound is `MAX_OVER_EXPLAINED`: | `pytest loop_b::test_max_over_explained_defaults_to_150_percent_and_is_clamped loop_b::test_reconcile_bound_comes_from…` | **pass** | 2 passed |
| 3 | In a full sample-mode investigation where the counterparty's add-back explains about 6x the restatement, the … | `pytest loop_b::test_beyond_bound_add_back_ends_at_the_frontier_under_auto` | **pass** | 1 passed |
| 4 | A scripted decision `critic_escalation:<node_id>` = `accept-capped` can still accept a beyond-bound add-back: | `pytest loop_b::test_beyond_bound_add_back_can_be_accepted_by_a_scripted_decision` | **pass** | 1 passed |
| 5 | The three input_examples in sample mode are unchanged: | `pytest tests && for f in 01_ge_2022_revenue_restated 02_ge_2023_revenue_restated 03_ge_2018_net_loss; do python -m ti…` | **pass** | `pytest -q tests`: 343 passed, 1 skipped; CLI: 01 `actionable 0.0062`, 02 `actionable -0.0194`, 03 `actionable 0.0` |
| 6 | AGENTS.md (config row, reconciler row, a gotcha), `.env.example` and BACKLOG B-15 (marked done, "Resolved in … | `grep -c "MAX_OVER_EXPLAINED\\|max_over_explained\\|over_explained_bound\\|gate_choice" AGENTS.md && grep -n "^## B-15" -…` | **superseded** | #17 moved the config/reconciler rows to docs/agents-reference.md and closed B-15 as a one-liner (`grep -c` on AGENTS.md is 0; `^## B-15` and "Resolved in #15" 0 hits). Moved claim holds: all four names in agents-reference.md, `MAX_OVER_EXPLAINED=1.5` in .env.example:13, B-15 "closed in #15"; #15's merge commit touches none of CHECKLIST/STATUS/metadata.json |

### #16: Status after #12, #13, #15; Meterless wording; BACKLOG B-19

| # | Criterion | Verifier | Class | Evidence |
|---|---|---|---|---|
| 1 | Every CHECKLIST line this PR adds or ticks cites a merged PR and files/tests that exist on this branch, and t… | `git diff origin/main -- docs/CHECKLIST.md \| grep '^+' ; git log --oneline -8 \| grep -E '\(#1[2-5]\)' ; cd services/ag…` | **pass** | adapted: the 5 lines #16 added to CHECKLIST cite #12, #13, #15 (all `(#N)` commits); cited tests: 41 passed; the 3 full-text templates and agents/scope.py exist |
| 2 | No document says the agent uses memory or calls a model today, and sponsor statuses agree across metadata.jso… | `grep -n -i 'stores lessons\\|is the primary\\|recalls' README.md docs/sponsor-technology.md metadata.json ; grep -n '"s…` | **fail** | no present-tense memory/model claim (grep for `stores lessons\|is the primary\|recalls` finds nothing); but Cursor is `planned` in metadata.json vs `optional` in README and sponsor-technology.md (see #11 criterion 3) |
| 3 | BACKLOG B-19 has all four fields and its claim about Loop A matches `agents/critic.py`. | `sed -n '/^## B-19/,$p' BACKLOG.md ; grep -n -B2 -A12 'raw > OVER_EXPLAINED' services/agent/src/tiresias_agent/agents/…` | **pass** | B-19 has Found in, Status, Severity, Out of scope, Safe to leave, Urgent if, Review at, Evidence; critic.py:141-153 proposes `accept-capped` under `raw > OVER_EXPLAINED` |
| 4 | Only status docs change, metadata stays valid and the secret scan passes. | `git diff --name-only origin/main ; ./scripts/validate_metadata.sh && ./scripts/check_secrets.sh` | **pass** | adapted: `git show --name-only 9a4e6da` = BACKLOG.md, docs/CHECKLIST.md, docs/STATUS.md, docs/sponsor-technology.md, metadata.json (no code); both checks pass |

### #17: AGENTS.md holds invariants only: reference doc, gotchas routed, backlog severity

| # | Criterion | Verifier | Class | Evidence |
|---|---|---|---|---|
| 1 | No information is lost: | `git show origin/main:AGENTS.md \| sed -n '/^## Known gotchas/,/^## Next work/p' compared against grep -n of each fac…` | **not-runnable** | verifier is a manual comparison, not a command. Spot check of 14 old gotcha facts: 12 found in the named destinations; `--experimental-transform-types` is in services/cognition/package.json and the Zetaris admin default went to the private handover, both as #17's routing table says (the handover was not read) |
| 2 | The moved reference is verbatim: | `diff <(git show origin/main:AGENTS.md \| sed -n '/^\| trace.py/,/^\| adapters\/cognition.py/p') <(sed -n '/^\| trace…` | **superseded** | #18 (7da4768) added an `expected=` sentence to the `adapters/zetaris.py` row on purpose, and feat/model-lanes added the `agents/model_lanes.py` row and text to seven rows (config.py, orchestrator.py, models.py, hypothesiser, critic, reporter, nightshift). Moved claim holds: 26 rows, the 25 old ones in the old order; 17 identical to `9a4e6da:AGENTS.md`, the zetaris.py row equals the old one once the #18 sentence is removed, and the seven model-lanes rows only insert text (no old text changed or removed); `run_sql(..., expected=)` exists in adapters/zetaris.py. At #17's merge the diff was empty; the one intended change (trace.py's `See "Run phases (for UI)"` becoming a link) was never made |
| 3 | `AGENTS.md` holds only rules, layout, commands, process and the three invariants, and is under 2,000 words. | `grep -n '^## ' AGENTS.md; wc -w AGENTS.md` | **pass** | headings: Read first, Layout, Building blocks, Commands, External services, How work lands, Rules, Invariants (no Known gotchas or Next work); `wc -w AGENTS.md` = 1784 |
| 4 | Every moved claim is accurate against the code (e.g. | `grep -rn 'timeout_seconds\\|MAX_OVER_EXPLAINED_DEFAULT\\|RUNS_REGISTRY_SIZE' services/agent/src; grep -rn RECALL_THRESH…` | **pass** | zetaris.py:299 `timeout_seconds: float = 20.0`; config.py:72 `MAX_OVER_EXPLAINED_DEFAULT = 1.5`; runs.py:158 `RUNS_REGISTRY_SIZE` default 50; cognition engine.ts:44 `RECALL_THRESHOLD` 0.25; source_scout.py:73 `MAX_CANDIDATES = 10` |
| 5 | Every `BACKLOG.md` open item has a severity and a review point, every closed item is one line naming its PR, … | `grep -c 'Severity:' BACKLOG.md; grep -c '^- Review at:' BACKLOG.md; grep -n '^- \*\*B-' BACKLOG.md` | **pass** | 15 open items + the format template: `Severity:` 16, `- Review at:` 16; 6 closed one-liners (B-1, B-3, B-4, B-5, B-7, B-15); at the #18 baseline with B-22/B-23 added: 16 open items, 7 closed one-liners (B-9 closed in #18), all in form |
| 6 | Links and references resolve: | `grep -rn 'AGENTS.md "Run phases\\|AGENTS.md gotcha' --include='*.md' --include='*.py' --include='*.yaml' . ; grep -n '…` | **pass** | no `AGENTS.md "Run phases` / `AGENTS.md gotcha` left (grep exit 1); headings `## Quirks` (zetaris/README.md:48), `## Building blocks (reuse…` and `## External services` (agents-reference.md:5, :59), `## Closed` (BACKLOG.md) exist |
| 7 | Nothing else changes: | `uv run --group dev pytest -q && uv run --group dev ruff check src tests; ./scripts/check_secrets.sh && ./scripts/vali…` | **pass** | `uv run --group dev pytest -q`: 343 passed, 1 skipped; ruff: All checks passed; secret scan and metadata pass; #17's merge commit touches none of CHECKLIST/STATUS/metadata.json |

## Findings

Three criteria fail on current `main`, from two causes. No superseded claim is broken at its new location.

1. **#4 criterion 4 (AGENTS.md and the PR template are consistent): fail.** `AGENTS.md:103` (rule 4) says to update `docs/CHECKLIST.md`, `docs/STATUS.md` and `metadata.json` "in the same commit as the work"; `AGENTS.md:97` says parallel PRs don't edit them and the coordinator updates them after each merge; `.github/pull_request_template.md:27` says "updated in the same change". Both AGENTS.md lines already existed when #4 merged. Deferred as BACKLOG B-22. One line: reword rule 4 to defer to the parallel-PR rule.
2. **#11 criterion 3 and #16 criterion 2 (sponsor statuses agree across metadata.json, README and sponsor-technology.md): fail.** Cursor is `"status": "planned"` in `metadata.json` but `optional` in `README.md:118` and `optional, not a Tiresias dependency` in `docs/sponsor-technology.md:46`; the schema enum (`scripts/metadata.schema.json:111`) has no `optional`. Zetaris, Meterless and NVIDIA agree everywhere. Deferred as BACKLOG B-23. One line: make README and sponsor-technology.md say `planned (optional)`, or record the reason next to the metadata value.

Observations that are not fails:

- **#17 criterion 2** claims one intended change in the moved `trace.py` row (`See "Run phases (for UI)"` becoming a link). The row was moved verbatim and the link was never made. On 5790962 its falsifier was not triggered (`pass`); since #18 the `adapters/zetaris.py` row also differs, on purpose, so it is now `superseded` by #18 (and by feat/model-lanes, which adds one row and inserts text into seven), and the check allows exactly those changes.
- **#8 criterion 4**'s test was renamed by #9 (`test_scout_reads_only_catalog_endpoints` → `test_scout_reads_only_catalog_objects_as_zetaris_sql`) when discovery moved to Zetaris SQL; the old node id selects 0 tests.
- **#17 criterion 1** has no runnable verifier (a manual comparison); a 14-fact spot check found every fact in the destination #17's routing table names, except the Zetaris admin default, which went to the private handover outside the repo and was not checked.
- Branch-diff verifiers of docs PRs (#4, #10, #11, #16 and the "untouched" parts of #9, #15, #17) only make sense before merge; they were adapted to the merge commits as described above. A future verifier that should survive merge can name the merge commit or a file check instead of `git diff origin/main`.
