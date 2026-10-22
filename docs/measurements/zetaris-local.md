# Zetaris local measurements (2026-10-22)

Setup:
- MacBook (Apple M5, 24 GB), Docker Desktop with 10 GB memory and 6 CPUs, Rosetta on.
- Zetaris bundle 2.4.3.1-hackathon, laptop sizing: driver 2 GB heap on `local[2]`.
- Reproduce with `python3 scripts/zetaris_probe.py` (reads `.env`).

| Operation | Seconds | Notes |
|---|---:|---|
| `SELECT 1` | 2.0 cold, then 0.2–0.4 | |
| `SHOW LIGHTNING DATABASES` | 0.5 | Seeded: `NY_TAXI`, `TPCDS_DB` |
| `CREATE LIGHTNING DATABASE` | 0.3 | |
| `CREATE SCHEMASTORE CONTAINER` | 0.1 | |
| **Register a REST table** (`CREATE LIGHTNING REST TABLE`) | **0.6–2.0** | Covers both endpoints: `companyconcept` (small) and `companyfacts` (~6 MB) |
| Create a flattening view | 0.1–0.4 | |
| Query `companyconcept` table (GE Revenues) | 2.2 first (live fetch), 0.8 second, 0.5 after `CACHE TABLE` | |
| Query `companyfacts` via a struct path (`facts.us-gaap.<Concept>.units.USD`) | 6.1–7.6 first, ~3 second | |
| `CACHE TABLE` on `companyfacts` | 3.8–7.8 | |
| **Cross-source reconciliation join** (GE vs GE HealthCare, both cached) | **3.3** | Returns delta $18,455M, GEHC $18,341M, residual **$114M**, matching the direct API check |
| Explode the whole `us-gaap` map (946 concepts) | 18.3 | Avoid. Use struct paths per concept. |

## Conclusions (input to ADR-0003)

1. **Registering sources at runtime is viable live.** One Loop B hop (register the table, create the view, run the first query) costs about **10–15 s** using `companyfacts`. Loop B can register the discovered entity live during the demo rather than having it pre-registered.
2. **Use `companyfacts` with struct-path views.** On 2026-10-22, SEC's `companyconcept` endpoint returned `"units":{"USD":{}}` (empty) for GE HealthCare's `RevenueFromContractWithCustomerExcludingAssessedTax`, while `companyfacts` held 41 values for the same concept. `companyconcept` stays a fast path, with automatic fallback to `companyfacts` when it comes back empty. This endpoint disagreement is the kind of conflict the critic must detect.
3. **`CACHE TABLE`** after registration keeps repeated critic re-queries in the 0.5–3 s range.
4. A full run with 3–5 levels and one Loop B hop fits well inside the 10-minute budget, with model latency dominating.
