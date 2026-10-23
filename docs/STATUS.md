# Status and open questions

Updated: 2026-10-23 (build day 2, after #6–#13 and #15). Submission deadline: **27 Oct 2026 23:45 UTC**.

Progress is tracked in **[CHECKLIST.md](CHECKLIST.md)**. This file holds only the open technical questions.

## Open questions

| # | Question | Owner | Why it matters |
|---|---|---|---|
| 1 | Official `metadata.json` format? A community draft is used until then. | Organizers | Submission completeness |
| 2 | NVIDIA credit claim process | NVIDIA help | Nemotron lanes |
| 3 | Nightshift deployment: runs in a separate macOS VM and is reached over hotspot mode. Reachability from containers is still to verify. The agent's model client exists (#2) but no agent calls a model yet (`stats.model_calls` is 0). | Us | Power rule |
| 4 | ~~Runtime table registration latency~~ 0.6–2 s per table; Loop B registers live (ADR-0003). Measured end to end on 2026-10-23: an unknown company's first run takes 15–21 s, 12–16 s of it registration ([measurements/live-any-company.md](measurements/live-any-company.md)) | Us | Resolved |
| 5 | Zetaris reachability from Cloud Run, and GCE sizing | Us | Cloud overlay (ADR-0008) |
| 6 | ~~Does live discovery also go through Zetaris (ADR-0003), or may the scout read SEC directly?~~ Through Zetaris only: `sec_tickers` and per-candidate submissions are Zetaris REST tables; the agent has no SEC HTTP of its own (#9, BACKLOG B-4 done) | Us | Resolved |
| 7 | How can a judge see Zetaris run? The default quickstart is sample mode (no Zetaris); live mode needs the vendor's local bundle plus `bootstrap`. Until a judge can run that path from the README, or a live trace is committed to `logs/samples/`, Zetaris stays `in-progress` in `metadata.json`. | Us | Sponsor Tech score; honest status |
