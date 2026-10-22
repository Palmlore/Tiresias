# Logs

Each run writes `trace-<run_id>.jsonl` here (one JSON object per agent action) and prints it to stdout. Runtime logs are gitignored. `samples/` holds committed traces from real runs, referenced by `output_examples/`. The schema is in [docs/architecture.md](../docs/architecture.md#tracing).
