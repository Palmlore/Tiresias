# Backlog

Known gaps that a change deliberately left for later. Each pull request is reviewed by an adversarial second model ([DuelLoop](docs/ai-usage.md#development-tools)); when a real finding is out of scope for that change, it is **deferred** here rather than dropped, in the same commit as the deferral.

Every item states all four: why it is out of scope for the change that found it, what bounds the harm meanwhile, where it was found (PR), and what would make it urgent. Items that survive to submission are summarised in [docs/limitations.md](docs/limitations.md).

Format:

```
## B-<n>: <title>
Found in: #<PR>  ·  Status: open | done (#<PR>)
- Out of scope because: …
- Safe to leave because: …
- Urgent if: …
- Evidence: <command, file:line, or trace>
```

---

## B-1: Agent Docker image can't see `data/sample/` or `zetaris/sql/`
Found in: #5  ·  Status: done (#7)
- Out of scope because: #5 builds the adapter; the Dockerfile and `docker-compose.yml` belong to the packaging task (judge setup and quickstart).
- Safe to leave because: nothing calls the adapter inside the container yet (`/run` returns 501); the adapter reads `SAMPLE_DATA_DIR` and `ZETARIS_SQL_DIR`, so a mount or a `COPY` is enough.
- Urgent if: `/run` is wired to the adapter, or before the first `docker compose up` test of sample mode.
- Evidence: `services/agent/Dockerfile` copies only `src/`; `sample_dir()` and `sql_dir()` in `services/agent/src/tiresias_agent/adapters/zetaris.py`.
- Resolved in #7: the agent is built from the repo root (`compose.yaml`, allow-list `.dockerignore`); the image copies `data/sample/`, `zetaris/sql/`, `catalog/`, `input_examples/` under `/app` and sets `SAMPLE_DATA_DIR`, `ZETARIS_SQL_DIR`, `LOGS_DIR`; checked by `services/agent/tests/test_packaging.py` and a `docker compose up --build` run (PR #7 Evidence).

## B-2: Rule-based hypothesiser knows a small, fixed rule set; `cyclic` is only a guard
Found in: #6  ·  Status: open
- Out of scope because: #6 builds Loop A in sample mode with the rule-based agents of ADR-0007. A wider rule set (segments, restructuring, tax items) needs more concepts in the sample trim and live views, and feedback-loop detection needs a model-backed hypothesiser (Nightshift lane).
- Safe to leave because: a question no rule explains ends in `evidence-frontier` with an `InformationNeed` for the unexplained variable, never in a crash or an invented cause; the hypothesiser drops any candidate already on the path, so the loop cannot cycle.
- Urgent if: a demo or judge question needs a driver outside `agents/knowledge.py` (e.g. segment revenue), or the `cyclic` end state must be shown.
- Evidence: `services/agent/src/tiresias_agent/agents/knowledge.py` (`COMPONENTS`, `EVENT_CONCEPTS`, `FILING_EVENTS`); `orchestrator.LoopA._no_more_rules`.

## B-3: Loop A not yet run against live Zetaris
Found in: #6  ·  Status: open
- Out of scope because: #6 is sample mode first (task scope); `--live` goes through `make_source(False)` = `FallbackSource(LiveZetarisSource, SampleSource)` but was only exercised by the existing adapter tests, not end to end.
- Safe to leave because: an outage degrades per call to the sample (traced `degraded`); a non-recoverable `SourceError` (e.g. a concept view that does not exist) is caught by the analyst, traced `error` and the candidate is pruned as "not testable", so the run still ends in an end state.
- Urgent if: the demo or `/run` uses live mode, or a pruned-as-not-testable candidate hides a real SQL bug (check `analyst`/`measure` lines with `status: error` in the trace).
- Evidence: `services/agent/src/tiresias_agent/agents/analyst.py` (`except SourceError`); `orchestrator.main --live`.
