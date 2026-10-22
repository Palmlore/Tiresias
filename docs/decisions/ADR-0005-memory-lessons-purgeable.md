# ADR-0005: Memory stores lessons, not answers, and can be purged

Status: accepted (2026-10-22)

Context: a faster second run on the same question would look like a cache, which is close to "hardcoded output".

Decision:
- H-MEM stores procedural lessons and source shortcuts only.
- Asker preferences go in a separate namespace.
- Purge is exposed (`POST /memory/purge`, `--memory cold|warm`) and recorded in the append-only trust ledger.
- Speed is measured in steps (hops, queries, sources onboarded, model calls, tokens) as well as seconds.

Rationale:
- Shows that lessons transfer across cases (GE HealthCare → GE Vernova).
- Shows that a lesson can be rejected when it doesn't fit (the impairment case).
- Gives judges a cold-vs-warm comparison.
