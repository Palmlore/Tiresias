# Status and open questions

Updated: 2026-10-25 (build day 4: real outputs and traces committed). Submission deadline: **27 Oct 2026 23:45 UTC**.

Progress is tracked in **[CHECKLIST.md](CHECKLIST.md)**. This file holds only the open technical questions.

## Open questions

| # | Question | Owner | Why it matters |
|---|---|---|---|
| 1 | Official `metadata.json` format? A community draft is used until then. | Organizers | Submission completeness |
| 2 | NVIDIA credit claim process | NVIDIA help | Nemotron lanes |
| 3 | ~~Nightshift deployment~~ Runs in a separate macOS VM (1.2.1), reached over hotspot mode from the host and from containers (2026-10-24, after granting macOS Local Network permission). Three model lanes use it (#22), chosen per question by depth (#24). Upgraded to 1.2.4 on 2026-10-26 (VM snapshot kept): explicit model ids are honoured, so a live setup can pin the lanes by setting `METERLESS_MODEL_FAST` / `METERLESS_MODEL_DEEP` to the ids `.env.example` names in its comment (`nvidia/nemotron-3.5-lightning-30b-a3b`, `nvidia/nemotron-3-ultra-550b-a55b`); the shipped defaults stay the lanes `meterless/fast` / `meterless/deep`; `chat_json` asks for JSON mode, without which Ultra's replies were rejected as prose (#39). The UI names the routed models in a result chip (#40). | Us | Power rule, NVIDIA |
| 4 | ~~Runtime table registration latency~~ 0.6–2 s per table; Loop B registers live (ADR-0003). Measured end to end on 2026-10-23: an unknown company's first run takes 15–21 s, 12–16 s of it registration ([measurements/live-any-company.md](measurements/live-any-company.md)) | Us | Resolved |
| 5 | Zetaris reachability from Cloud Run, and GCE sizing | Us | Cloud overlay (ADR-0008) |
| 6 | ~~Does live discovery also go through Zetaris (ADR-0003), or may the scout read SEC directly?~~ Through Zetaris only: `sec_tickers` and per-candidate submissions are Zetaris REST tables; the agent has no SEC HTTP of its own (#9, BACKLOG B-4 done) | Us | Resolved |
| 7 | ~~How can a judge see Zetaris run?~~ Through the committed traces: working means every call is recorded, traceable and verifiable from a fresh state, whoever ran it (the human's definition, 25 Oct). A live run of case 04 from a fresh Zetaris is in `logs/samples/` ([output_examples/README.md](../output_examples/README.md)); Zetaris is `working` in `metadata.json`; Meterless (Nightshift calls) and NVIDIA (Nemotron) are `working` on the same trace, with its caveats listed in [sponsor-technology.md](sponsor-technology.md#what-the-model-call-trace-shows-and-what-it-does-not). | Us | Resolved |
