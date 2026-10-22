# ADR-0007: Offline sample mode with no keys

Status: accepted (2026-10-22)

Context: judges must be able to run the project offline with no API keys. Zetaris and Nightshift can't be assumed on a judge's machine.

Decision: `SAMPLE_MODE=true` runs the same orchestration with:
- cached Zetaris query results (`data/sample/`, with provenance);
- a rule-based hypothesiser (restated items, 8-K items, hierarchy children);
- a rule-based critic (evidence present, share explained, like-for-like, intervention re-query on cached rows);
- H-MEM's model-free path.

Stored outputs are never replayed. Sample mode is labelled in every trace line.

Consequence: the breakdown and reconciliation steps work fully offline. Model-dependent narrative steps are skipped and labelled.
