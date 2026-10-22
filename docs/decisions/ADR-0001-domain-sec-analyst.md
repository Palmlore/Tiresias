# ADR-0001: Domain: SEC filings, analyst as the primary user

Status: accepted (2026-10-22)

Context: Track 2 needs evidence-backed answers across several sources. Feasibility probes on 2026-10-22 compared SEC EDGAR, Australian Bureau of Statistics CPI data and synthetic operations data. SEC data has organic conflicts: 631 conflicting facts across filings for IBM alone. A real level-4 hop was verified (IBM → Kyndryl, a $266M residual).

Decision:
- Domain: SEC filings. Primary user: financial analyst. Secondary askers: auditor, board/CEO.
- Showcase cases: the GE family (GE, GE HealthCare, GE Vernova), with fallbacks 3M/Solventum and Danaher.

Rationale:
- Entirely real data with natural disagreement.
- Wide appeal to the expected audience.
- One parent with several SEC-filing subsidiaries tells a coherent story.
- Two root-cause types: spin-off and impairment.

Consequences:
- Structured data reaches about 3–4 evidenced levels; deeper levels need text (see limitations).
- No segment data.
