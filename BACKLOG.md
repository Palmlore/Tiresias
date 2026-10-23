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
Found in: #5  ·  Status: open
- Out of scope because: #5 builds the adapter; the Dockerfile and `docker-compose.yml` belong to the packaging task (judge setup and quickstart).
- Safe to leave because: nothing calls the adapter inside the container yet (`/run` returns 501); the adapter reads `SAMPLE_DATA_DIR` and `ZETARIS_SQL_DIR`, so a mount or a `COPY` is enough.
- Urgent if: `/run` is wired to the adapter, or before the first `docker compose up` test of sample mode.
- Evidence: `services/agent/Dockerfile` copies only `src/`; `sample_dir()` and `sql_dir()` in `services/agent/src/tiresias_agent/adapters/zetaris.py`.
