# Limitations

This list is kept honest and current.

- **Correlation and reconciliation, not proof of intent.** Tiresias shows *that* figures reconcile to an event. It cannot show *why* management made a decision unless the evidence exists in structured data.
- **Structured data only (v1).** Narrative text (press releases, MD&A) is not parsed. When the next "why" needs text, the run ends at `evidence-frontier` with a pointer to where to look.
- **Bounded catalog.** The agent discovers sources only from `catalog/sources.yaml`, by design (scope and reproducibility).
- **No segment data** in XBRL `companyfacts`.
- **Residuals are reported, not explained away.** For example, GE HealthCare's revenue is $114M short of GE's restatement delta.
- **Judges can't run Zetaris or Nightshift.** Sample mode uses cached query results and rule-based agents; the live path is shown in the demo video and traces.
- **Sample-mode "registration" does not call Zetaris.** When Loop B registers a discovered company in sample mode, it checks that the company is in `data/sample/` and serves it from there; the trace says `registration: "sample"`. Only live mode registers sources in Zetaris.
- **Runs are remembered in memory only.** `GET /runs/{id}` forgets a run after the agent restarts or once 50 newer runs finished; the trace file in `logs/` stays.
- **Not investment advice.** Outputs support an analyst's own review.
