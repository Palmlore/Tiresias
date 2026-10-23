# Status and open questions

Updated: 2026-10-23 (build day 2). Submission deadline: **27 Oct 2026 23:45 UTC**.

Progress is tracked in **[CHECKLIST.md](CHECKLIST.md)**. This file holds only the open technical questions.

## Open questions

| # | Question | Owner | Why it matters |
|---|---|---|---|
| 1 | Official `metadata.json` format? A community draft is used until then. | Organizers | Submission completeness |
| 2 | NVIDIA credit claim process | NVIDIA help | Nemotron lanes |
| 3 | Nightshift deployment: runs in a separate macOS VM and is reached over hotspot mode. Reachability from containers is still to verify. | Us | Power rule |
| 4 | ~~Runtime table registration latency~~ 0.6–2 s per table; Loop B registers live (ADR-0003) | Us | Resolved |
| 5 | Zetaris reachability from Cloud Run, and GCE sizing | Us | Cloud overlay (ADR-0008) |
