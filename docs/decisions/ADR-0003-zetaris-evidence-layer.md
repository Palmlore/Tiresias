# ADR-0003: Zetaris as the evidence layer (local Docker)

Status: accepted (2026-10-22)

Context: Zetaris is required. For about 1 MB of data it is heavy (8 GB Docker, x86 emulation on Apple Silicon). Its value depends on how it is used.

Decision: Zetaris is the only path to evidence in live mode.
- Every claim is SQL plus rows.
- Loop B registers new sources at runtime from the bounded catalog.
- The critic re-queries counterfactually.
- Deployment: local Docker (`zetaris-platform` 2.4.3.1-hackathon), REST on :8888.

Rationale: turns a requirement into the product's audit trail and makes source discovery visible.

Measured on 2026-10-22 ([measurements/zetaris-local.md](../measurements/zetaris-local.md)):
- Registering a REST table takes 0.6–2.0 s; one full Loop B hop takes about 10–15 s. **Loop B registers the discovered entity live.**
- Use `companyfacts` with struct-path views per concept. SEC's `companyconcept` endpoint returned empty values for GE HealthCare, so `companyconcept` stays a fast path with automatic fallback.
- `CACHE TABLE` after registration.
- The GE vs GE HealthCare reconciliation join runs in 3.3 s and returns a $114M residual.
