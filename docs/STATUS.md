# Status and open questions

Updated: 2026-10-22, build day 1. Submission deadline: **27 Oct 2026 23:45 UTC**.

## Done
- Problem, user, cases, architecture and the critic rulebook decided ([decisions/](decisions/))
- Repository layout mapped to the judging criteria ([RUBRIC.md](RUBRIC.md))
- Zetaris local stack running (verify.sh 9/9) and measured; GE vs GE HealthCare reconciliation runs inside Zetaris (residual $114M)

## Next
1. ~~Verify and measure Zetaris~~ done: [measurements/zetaris-local.md](measurements/zetaris-local.md)
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
| 3 | Judge list: 5 of 6 published on 22 Oct: Michael Hay (CTO, Zetaris), Michael Armentano (Head of Technical Partnerships, SpaceXAI), Sam Cummings (Director of Education, GenAI Works), Shestakov Vladyslav (Head of Engineering, GenAI Works), Remus Ranca (Education Operations Lead, GenAI Works). The sixth is pending. Prior employment isn't on the page, so it needs checking. | Us / HackOS | Case neutrality |
| 4 | NVIDIA credit claim process ("TBC" in the sponsor folder) | NVIDIA help | Nemotron lanes |
| 5 | Nightshift 1.2.1 for Mac is ad-hoc signed and not notarized (no Team ID; Gatekeeper rejects it). It bundles llama.cpp, whisper and sd servers plus a `meterlessd` daemon. Plan: run it in an isolated macOS VM with cloud-only (NVIDIA) lanes and reach it over hotspot mode. Ask Help Meterless for a notarized build or a container/Linux option. | Us / Meterless help | Power rule, host safety |
| 6 | ~~Runtime table registration latency~~ 0.6–2 s per table; Loop B registers live (ADR-0003) | Us | Resolved |
| 7 | Zetaris reachability from Cloud Run, and GCE sizing | Us | Cloud overlay (ADR-0008) |
