# Output examples

Real outputs saved from real runs of `input_examples/`. They are **never read at runtime** (`grep -rn output_examples services/agent/src` finds nothing). Each output names the `run_id` of its trace in [`logs/samples/`](../logs/samples/). In each trace the reporter's `action: report` line carries the output's `end_state`, `root_cause` and `residual`, except in the two live traces: they were recorded before the reporter traced `root_cause`, so their `report` line carries `end_state` and `residual` and its `output_summary` states the root cause's figures, while the full `root_cause` sentence is in the output only (BACKLOG B-29). A run at depth `thorough`/`deep` adds two later reporter lines (`model_wording`, `reword`) for the action's wording; they carry no end state.

| Output | Mode | End state | Root cause found | Residual (share of the effect left unexplained; negative = over-explained) | Trace |
|---|---|---|---|---|---|
| [01_ge_2022_revenue_restated.json](01_ge_2022_revenue_restated.json) | sample | `actionable` | GE HealthCare spin-off (8-K, 2023-01-04) | 0.0062 | [trace-sample-01-ge-2022.jsonl](../logs/samples/trace-sample-01-ge-2022.jsonl) |
| [02_ge_2023_revenue_restated.json](02_ge_2023_revenue_restated.json) | sample | `actionable` | GE Vernova spin-off (8-K, 2024-04-02) | −0.0194 | [trace-sample-02-ge-2023.jsonl](../logs/samples/trace-sample-02-ge-2023.jsonl) |
| [03_ge_2018_net_loss.json](03_ge_2018_net_loss.json) | sample | `actionable` | Goodwill impairment in continuing operations | 0.0 | [trace-sample-03-ge-2018.jsonl](../logs/samples/trace-sample-03-ge-2018.jsonl) |
| [04_3m_2023_revenue_restated.json](04_3m_2023_revenue_restated.json) | sample | `actionable` | Solventum spin-off (8-K, 2024-04-04) | −0.0156 | [trace-sample-04-3m-2023.jsonl](../logs/samples/trace-sample-04-3m-2023.jsonl) |
| [04_3m_2023_revenue_restated.live-fresh-deep.json](04_3m_2023_revenue_restated.live-fresh-deep.json) | **live**, fresh Zetaris, depth `deep` (Nightshift) | `actionable` | Solventum spin-off (8-K, 2024-04-04) | −0.0156 | [trace-run-20261024T233153Z-c3227e.jsonl](../logs/samples/trace-run-20261024T233153Z-c3227e.jsonl) |
| [04_3m_2023_revenue_restated.live-registered-quick.json](04_3m_2023_revenue_restated.live-registered-quick.json) | **live**, sources already registered, depth `quick` | `actionable` | Solventum spin-off (8-K, 2024-04-04) | −0.0156 | [trace-run-20261024T230646Z-45973c.jsonl](../logs/samples/trace-run-20261024T230646Z-45973c.jsonl) |

## How they were produced (25 Oct 2026 AEDT; the live runs from main at `064c288`, the sample runs re-run on this branch once the reporter traced `root_cause`)

**Sample mode, as a judge runs it.** `cp .env.example .env && docker compose up --build`, then each input posted in order 01 → 04:

```bash
curl -s -X POST localhost:8000/run -H 'Content-Type: application/json' \
     -H 'X-Run-Id: sample-01-ge-2022' -d @input_examples/01_ge_2022_revenue_restated.json
```

Each output's `trace_file` is the container path (`/app/logs/…`); the file was moved from `logs/` to `logs/samples/` unchanged. Memory was on (`warm`, the default) with a fresh H-MEM store, so the runs build on each other: run 01 stores a lesson (`mem-1`), run 02 and run 04 recall it and follow it (`status: supported`), and run 03, a different kind of question, recalls it and rejects it (`status: rejected`, with the reason). See `memory` in each output.

**Live mode, from a fresh Zetaris, with models.** Before this run every Tiresias object in the local Zetaris bundle was dropped (92 views, 76 REST tables and the `TIRESIAS_SEC` database; this Zetaris build has no statement to drop the empty `tiresias` schemastore container) and the Zetaris driver was restarted, so its cache was cold. Then one run of case 04 at depth `deep`, memory off:

```bash
cd services/agent && uv run python -m tiresias_agent.orchestrator \
   input_examples/04_3m_2023_revenue_restated.json --live --depth deep --memory cold
```

The trace (176 lines, every line `mode: live`) shows each Zetaris call with its SQL, outcome, row count, duration and retry count, in order:
- the first read finds no database (`HTTP 400 … 'TIRESIAS_SEC' doesn't exist`; traced `error`, then one `degraded` line: the registered-entity list was served from the sample for that one read, BACKLOG B-28);
- the run then **registers everything it needs, while it runs**: the database, the SEC ticker list (to resolve "MMM"), 3M's company facts and its concept views (21 s), 3M's filing history, one EDGAR full-text search table (to find the filer whose Form 10 names 3M), and, behind the source gate, Solventum's company facts (6 s). The only `exists` replies are the container that could not be dropped and the database once this run had created it;
- every evidence query after that is answered by Zetaris (no other Zetaris fallback).

Four model calls went through Nightshift, each traced with the lane asked for (`meterless/deep` or `meterless/fast`), the model that served it (`routed_model`), the provider and the receipt id: three critic second opinions and the action's wording. The first critic call was served by `nvidia/nemotron-3-ultra-550b-a55b`, which answered in prose instead of the JSON asked for: the trace marks that call's phase `error` (`ModelProtocolError`) and the critic `rejected` the reply ("model reply unusable, rules only for this step"), so that step was decided by the rules alone. The other two critic calls and the wording were served by `nvidia/nemotron-3.5-super-vl-120b-a12b` and used (`second_opinion` lines; `action_source: model`). The hypothesis lane made no call because the three rule candidates filled every branch slot (`MAX_BRANCHES_PER_LEVEL` = 3). 122 s end to end, 4,849 tokens.

**Live mode, sources already registered.** An earlier run of case 04 the same night, depth `quick` (rules only), memory off, when 3M and Solventum were already registered: every read through Zetaris, no `degraded` or `error` line; Solventum's registration re-ran its statements and reused the table (`exists`). 64 s.

Both live runs agree with sample mode on the root cause, the residual and the action's figures. In the two live files only, `trace_file` was changed from the absolute host path to the repo-relative one.

These outputs are produced by the code, not written by hand: re-running the commands above gives the same end states and figures (timings, timestamps and, with memory on, the recalled lessons depend on the run). The tests check that the outputs differ across inputs 01–03 (`tests/test_orchestrator.py::test_outputs_differ_across_cases`) and that no agent module names a case (`test_agent_logic_has_no_case_specific_literals`).
