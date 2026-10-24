# Output examples

Real outputs saved from real runs of `input_examples/`. They are **never read at runtime** (`grep -rn output_examples services/agent/src` finds nothing). Each output names the `run_id` of its trace in [`logs/samples/`](../logs/samples/).

| Output | Mode | End state | Root cause found | Residual (share of the effect left unexplained; negative = over-explained) | Trace |
|---|---|---|---|---|---|
| [01_ge_2022_revenue_restated.json](01_ge_2022_revenue_restated.json) | sample | `actionable` | GE HealthCare spin-off (8-K, 2023-01-04) | 0.0062 | [trace-sample-01-ge-2022.jsonl](../logs/samples/trace-sample-01-ge-2022.jsonl) |
| [02_ge_2023_revenue_restated.json](02_ge_2023_revenue_restated.json) | sample | `actionable` | GE Vernova spin-off (8-K, 2024-04-02) | −0.0194 | [trace-sample-02-ge-2023.jsonl](../logs/samples/trace-sample-02-ge-2023.jsonl) |
| [03_ge_2018_net_loss.json](03_ge_2018_net_loss.json) | sample | `actionable` | Goodwill impairment in continuing operations | 0.0 | [trace-sample-03-ge-2018.jsonl](../logs/samples/trace-sample-03-ge-2018.jsonl) |
| [04_3m_2023_revenue_restated.json](04_3m_2023_revenue_restated.json) | sample | `actionable` | Solventum spin-off (8-K, 2024-04-04) | −0.0156 | [trace-sample-04-3m-2023.jsonl](../logs/samples/trace-sample-04-3m-2023.jsonl) |
| [04_3m_2023_revenue_restated.live.json](04_3m_2023_revenue_restated.live.json) | **live** (Zetaris) | `actionable` | Solventum spin-off (8-K, 2024-04-04) | −0.0156 | [trace-run-20261024T230646Z-45973c.jsonl](../logs/samples/trace-run-20261024T230646Z-45973c.jsonl) |

## How they were produced (25 Oct 2026 AEDT, main at `064c288`)

**Sample mode, as a judge runs it.** `cp .env.example .env && docker compose up --build`, then each input posted in order 01 → 04:

```bash
curl -s -X POST localhost:8000/run -H 'Content-Type: application/json' \
     -H 'X-Run-Id: sample-01-ge-2022' -d @input_examples/01_ge_2022_revenue_restated.json
```

Each output's `trace_file` is the container path (`/app/logs/…`); the file was moved from `logs/` to `logs/samples/` unchanged. Memory was on (`warm`, the default) with a fresh H-MEM store, so the runs build on each other: run 01 stores a lesson (`mem-1`), run 02 and run 04 recall it and follow it (`status: supported`), and run 03, a different kind of question, recalls it and rejects it (`status: rejected`, with the reason). See `memory` in each output.

**Live mode, through local Zetaris.** One run of case 04, rules only (depth `quick`) and memory off, with every read going through the Zetaris bundle (the trace has no `degraded` or `error` line). Solventum's table was already registered from an earlier live run, so Loop B re-ran its registration statements and reused it (`outcome: registered`, `exists`):

```bash
cd services/agent && uv run python -m tiresias_agent.orchestrator \
   input_examples/04_3m_2023_revenue_restated.json --live --memory cold
```

Live and sample agree on the root cause, the residual and the action; the live run took 64 s (more hops and queries: it pruned two candidates that had no evidence in the registered sources). In this file only, `trace_file` was changed from the absolute host path to the repo-relative one.

These outputs are produced by the code, not written by hand: re-running the commands above gives the same end states and figures (timings, timestamps and, with memory on, the recalled lessons depend on the run). The tests check that the outputs differ across inputs 01–03 (`tests/test_orchestrator.py::test_outputs_differ_across_cases`) and that no agent module names a case (`test_agent_logic_has_no_case_specific_literals`).
