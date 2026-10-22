# ADR-0003: Zetaris as the evidence layer (local Docker)

Status: accepted (2026-10-22)

Context: Zetaris is required. For about 1 MB of data it is heavy (8 GB Docker, x86 emulation on Apple Silicon). Its value depends on how it is used.

Decision: Zetaris is the only path to evidence in live mode.
- Every claim is SQL plus rows.
- Loop B registers new sources at runtime from the bounded catalog.
- The critic re-queries counterfactually.
- Deployment: local Docker (`zetaris-platform` 2.4.3.1-hackathon), REST on :8888.

Rationale: turns a requirement into the product's audit trail and makes source discovery visible.

Open measurements (day 1–2):
- query latency;
- time to register a REST table while the agent runs;
- whether the nested `companyfacts` maps can be exploded.

If registration is slow, pre-register the case entities and keep live registration for the single discovered entity.
