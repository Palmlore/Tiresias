# Status and open questions

Updated: 2026-10-22 (build day 1). Submission deadline: **27 Oct 2026 23:45 UTC**.

Progress is tracked in **[CHECKLIST.md](CHECKLIST.md)**. This file holds only the open questions.

## Open questions and information needed

| # | Question | Owner | Why it matters |
|---|---|---|---|
| 1 | Is Cursor still on the judging rubric now that the event credit is used up? | Organizers (Help Cursor) | Sponsor Tech wording |
| 2 | Official `metadata.json` format? A community draft is used until then. | Organizers | Submission completeness |
| 3 | Judge list: 5 of 6 published on 22 Oct: Michael Hay (CTO, Zetaris), Michael Armentano (Head of Technical Partnerships, SpaceXAI), Sam Cummings (Director of Education, GenAI Works), Shestakov Vladyslav (Head of Engineering, GenAI Works), Remus Ranca (Education Operations Lead, GenAI Works). The sixth is pending. Prior employment isn't on the page, so it needs checking. | Us / HackOS | Case neutrality |
| 4 | NVIDIA credit claim process ("TBC" in the sponsor folder) | NVIDIA help | Nemotron lanes |
| 5 | Nightshift 1.2.1 for Mac is ad-hoc signed and not notarized (no Team ID; Gatekeeper rejects it). It bundles llama.cpp, whisper and sd servers plus a `meterlessd` daemon. Plan: run it in an isolated macOS VM with cloud-only (NVIDIA) lanes and reach it over hotspot mode. Ask Help Meterless for a notarized build or a container/Linux option. | Us / Meterless help | Power rule, host safety |
| 6 | ~~Runtime table registration latency~~ 0.6–2 s per table; Loop B registers live (ADR-0003) | Us | Resolved |
| 7 | Zetaris reachability from Cloud Run, and GCE sizing | Us | Cloud overlay (ADR-0008) |
