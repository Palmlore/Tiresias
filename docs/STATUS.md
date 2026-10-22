# Status and open questions

Updated: 2026-10-22, build day 1. Submission deadline: **27 Oct 2026 23:45 UTC**.

## Done
- Problem, user, cases, architecture and the critic rulebook decided ([decisions/](decisions/))
- Repository layout mapped to the judging criteria ([RUBRIC.md](RUBRIC.md))
- Zetaris local stack pulled and started (vendor bundle 2.4.3.1-hackathon)

## Next
1. Verify Zetaris (`verify.sh`). Measure query latency and runtime registration; test flattening the nested `companyfacts` maps.
2. Register the GE family sources in Zetaris (`zetaris/sql/`).
3. `cognition`: mount the H-MEM reference behind REST and pass the conformance runner.
4. `agent`: Zetaris adapter, Nightshift client, tracing, Loop A on case 1.
5. Loop B (onboard GE HealthCare), the critic rulebook, end states.
6. Sample mode, 3 examples end to end, Docker clean-clone test.
7. Memory demo (cold, warm, purge), asker profiles, branching.
8. README, deck and video.

## Open questions and information needed

| # | Question | Owner | Why it matters |
|---|---|---|---|
| 1 | Is Cursor still on the judging rubric now that the event credit is used up? | Organizers (Help Cursor) | Sponsor Tech wording |
| 2 | Official `metadata.json` format? A community draft is used until then. | Organizers | Submission completeness |
| 3 | Judge list, to check neutrality of the case companies | Organizers / HackOS | Case selection |
| 4 | NVIDIA credit claim process ("TBC" in the sponsor folder) | NVIDIA help | Nemotron lanes |
| 5 | Nightshift on macOS 27 (the edition card lists Tahoe) | Meterless help | Power rule |
| 6 | Runtime table registration latency in local Zetaris | Us (measure) | Loop B design (ADR-0003) |
| 7 | Zetaris reachability from Cloud Run, and GCE sizing | Us | Cloud overlay (ADR-0008) |
