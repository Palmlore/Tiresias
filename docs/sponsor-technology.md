# Sponsor technology

This is the required sponsor technology explanation, kept honest. Status follows [`metadata.json`](../metadata.json): `planned` → `in-progress` → `working`, and `working` only when a judge can see it run, or verify it in a committed trace from a fresh state that records every call, request and response (whoever made the run).

## Zetaris: the data layer

**Role.** Zetaris is the agent's data layer: every claim in a Tiresias report is SQL run through Zetaris plus the rows it returned. In live mode the agent never calls SEC APIs directly; nothing in `services/agent/src` outside the sponsor adapters opens HTTP (`tests/test_any_company.py`).

**Traceable.** Every Zetaris call is one trace line: the SQL, the outcome (an "already exists" HTTP 400 is recorded as `outcome: exists`), the row count, the duration and the retry count, plus the HTTP exchange of the last attempt (`endpoint`, `method`, `service_host`, `http_status`, `request_bytes`, `response_bytes`; #44, `tests/test_exchange_trace.py`); a fallback to the sample is its own `degraded` line, and a call that was never sent carries no exchange fields. A live run is committed: [logs/samples/](../logs/samples/), described in [output_examples/README.md](../output_examples/README.md).

**How.**
- SEC endpoints (XBRL company facts, filing histories, the ticker map) are registered as Lightning REST tables ([zetaris/sql/](../zetaris/sql/)).
- SQL views flatten the nested JSON, one view per concept (struct paths, never the whole map).
- The parent-vs-spun-off reconciliation reads both entities through Zetaris SQL; the agent computes the add-back from the returned rows. (A cross-source SQL join returning the same $114M residual was measured in [measurements/zetaris-local.md](measurements/zetaris-local.md); the agent does not use it yet.)
- `CACHE TABLE` absorbs SEC rate limits.

**What's distinctive.** In **Loop B** the agent **registers a new source while it runs**: it finds the spun-off entity, then registers that entity's filings (dry run first, one statement at a time, behind the source gate). Source discovery becomes visible and auditable. The analyst also tests each link **counterfactually** on the rows Zetaris returned (reset the candidate to its baseline: does the effect go away?) before the critic approves it.

**Deployment.** Local Docker (vendor bundle `zetaris-platform`, image tag 2.4.3.1-hackathon), REST at `localhost:8888`.

Status: **working**. A live run from a fresh Zetaris (every Tiresias object dropped and the driver restarted first) is committed with every call traced: the run registers what it needs and answers from Zetaris ([output_examples/README.md](../output_examples/README.md)); more runs are measured in [measurements/live-any-company.md](measurements/live-any-company.md), and a pre-registered live sweep of 15 companies ran with Zetaris alive throughout and no fallback ([measurements/coverage-sweep-2026-10-26.md](measurements/coverage-sweep-2026-10-26.md)). The judge's default quickstart is sample mode, which runs the same logic on cached rows without Zetaris.

## Meterless

**H-MEM (engine, Build rule).** The `cognition` service runs the Meterless H-MEM reference engine (Apache-2.0) behind REST.
- It stores **lessons** from past investigations (procedural memory), never answers: the generic path of hypothesiser steps that led to the cause, with no company, ticker, CIK, amount or date (`agents/memory.py`).
- It recalls them after the framer to seed the hypothesiser, which tests a lesson's steps first; the critic judges each recalled lesson and rejects one whose step it prunes, with negative feedback to H-MEM.
- The memory gate is H-MEM's *programmable approval boundary*: the agent's `memory` gate decides, and the decision travels with the lesson (a discarded lesson is ledgered, not stored).
- Purge is exposed (`POST /memory/purge` on the agent, proxied to cognition); the purge is itself recorded in the append-only trust ledger. `scripts/memory_demo.py` shows cold → warm (fewer hops) → rejected lesson → purge → cold, in sample mode.
- Test: the official H-MEM conformance runner passes 13/13 against our service module (`cd services/cognition && npm run conformance`), with the service's own tests (`npm test`); both run in CI. The feature shows in the committed traces: in [logs/samples/](../logs/samples/) run 02 recalls the lesson run 01 stored and follows it, run 03 recalls it and rejects it.

**Nightshift (Power rule): the only AI provider.** Every model call from the agent goes to the Nightshift Runtime: `POST {METERLESS_BASE_URL}/chat/completions` in OpenAI format with the Runtime token as bearer and the `meterless-app: tiresias` and `meterless-scope: user` headers (`adapters/nightshift.py`). There is no other model client in the code. The agent asks for a lane (`meterless/fast` or `meterless/deep`, the shipped defaults) or, on Nightshift 1.2.4, a pinned model id (`METERLESS_MODEL_FAST` / `METERLESS_MODEL_DEEP`; our live setup pins `nvidia/nemotron-3.5-lightning-30b-a3b` and `nvidia/nemotron-3-ultra-550b-a55b`). Every reply is requested as JSON (`response_format: json_object`, #39). The trace line of each call records the model and provider from Nightshift's receipt, the receipt id and the HTTP exchange (endpoint, status, bytes; #44).

**Candidates for later:** the Markovian engine (each "why" as a bounded step with compressed carryover) and World Model (the why-tree as entities and edges with provenance).

Status: **working**, with the caveats below. H-MEM: the service passes conformance 13/13 and the agent uses it in every warm run, sample mode included, so a judge can run it (memory demo in CI; the committed sample traces show a lesson stored, recalled and rejected). Nightshift: a live run from a fresh state is committed with every model call traced ([output_examples/README.md](../output_examples/README.md)); the model lanes (ADR-0010) call it only in live mode at depth `thorough` (critic) or `deep` (all lanes); the default `quick` and sample mode make no model call (ADR-0011).

## NVIDIA

Nemotron models serve the Nightshift lanes: a fast lane for the hypothesiser and reporter, a deep lane for the critic's second opinion. Nightshift 1.2.1 did not let a request pin a model; 1.2.4 does (a model id in `METERLESS_MODEL_FAST` / `METERLESS_MODEL_DEEP`). Either way the evidence is the receipt: every model call's trace line records `routed_model` and `provider`. In the committed fresh-state run all four calls were served by Nemotron models (`nvidia/nemotron-3-ultra-550b-a55b` once, `nvidia/nemotron-3.5-super-vl-120b-a12b` three times). The NVIDIA key lives only in Nightshift's Providers tab.

Measured on 2026-10-26 on Nightshift 1.2.4 with the lanes pinned (traces not committed): 8 of 8 critic-shaped calls to `nemotron-3-ultra-550b-a55b` succeeded in 1.3–3.2 s and 8 of 8 to `nemotron-3.5-lightning-30b-a3b` in 11–46 s; a live run of case 04 at depth `thorough` took 23.5 s and used all 3 Ultra second opinions (one disagreed with the rules and went to the escalation gate). `nemotron-3.5-super-vl-120b-a12b`, which served the committed run, is now excluded by Nightshift itself after repeated server errors; we no longer request it.

Status: **working**, with the caveats below.

### What the model-call trace shows, and what it does not

The claim for Nightshift and Nemotron rests on one committed live run: case 04 (3M) at depth `deep`, from a fresh Zetaris, trace [`logs/samples/trace-run-20261024T233153Z-c3227e.jsonl`](../logs/samples/trace-run-20261024T233153Z-c3227e.jsonl). Read it with these limits:

1. **One run, and a judge cannot repeat it from the quickstart.** The quickstart is sample mode, and the default depth is `quick`: neither calls a model. Repeating the run needs a Nightshift Runtime with an NVIDIA key and a Zetaris bundle. The trace is the evidence; it was not edited (only the output's `trace_file` path was made repo-relative).
2. **Two of the three lanes appear in it.** The critic's second opinion (3 calls, deep lane) and the action's wording (1 call, fast lane). The hypothesis lane made **no** call: it only fills branch slots the rules leave free, and the three rule candidates filled all three (`MAX_BRANCHES_PER_LEVEL` = 3). That lane is covered by tests with a mocked Runtime (`tests/test_model_lanes.py`), not by a live trace.
3. **One of the four replies was unusable.** The first critic call (Nemotron Ultra) answered in prose, not the JSON asked for; the trace marks it `ModelProtocolError` and the critic `rejected` it, so that step was decided by the rules alone. The other three replies were used. Since #39 (26 Oct) every reply is requested as JSON, which made Ultra's replies usable (3 of 3 in the measurement above).
4. **We did not choose the model in this run.** Nightshift 1.2.1 routed by lane and ignored explicit model ids (1.2.4, from 2026-10-26, honours them). We claim only what each receipt names (`routed_model`, `provider`, `receipt_id`); all four were Nemotron in this run, and a different routing would show in the trace.
5. **Prompts and replies are summarised.** Each model call's trace line keeps the first 160 characters of the prompt and the reply, the token count, the duration and the receipt id, not the full text. The receipt id can be matched in Nightshift's own request log on the machine that made the run, which a judge cannot reach.
6. **The models advise; they don't decide.** A second opinion that disagrees goes to the `critic_escalation` gate, whose auto policy keeps the rule verdict; the wording is used only if it adds no number or name. In this run both second opinions agreed with the rules, and the root cause, residual and figures equal sample mode's.

An earlier live run of case 01 at depth `deep` (2026-10-24, 4 calls, timings in the README's Models section) was measured but its trace is not committed, so it is not counted as evidence here.

## Cursor

Cursor became optional on 23 Oct 2026. The organizers' update to the Build section reads: "Cursor is now optional. You can use any AI coding tool you're comfortable with, such as Cursor, Claude Code, Windsurf or GitHub Copilot." Tiresias is built mainly with Claude Code; Cursor's agent (Grok 4.6, medium) drew the brand assets (palette, mark, three characters; `scripts/render_brand.py`) and carries the web UI's visual polish (see [ai-usage.md](ai-usage.md#development-tools)). The core Build requirement is unchanged: a Meterless engine (H-MEM here) in a working feature, shown with a passing test or a live demo.

Status: **in-progress** in metadata.json, one step short of working: the assets are merged, the UI polish is not yet. Cursor stays optional and is not a Tiresias dependency

## What was unique

1. **A data layer that grows during the run.** When the registered sources run out, Loop B finds the missing company from filing evidence (the Form 10 that names the parent, through EDGAR full-text search registered in Zetaris), asks the source gate, and registers that company's filings in Zetaris one statement at a time, then reconciles the two companies in SQL. Source discovery is part of the audited trace, not a step outside it.
2. **H-MEM remembers how, never what.** A lesson is the generic path that found a cause, with no company, figure or date; the critic judges each recalled lesson and can reject it in the open, the memory gate decides what is stored, and a purge is ledgered.
3. **Models that never decide, and say who they are.** Nemotron through Nightshift proposes candidates, a second opinion and wording; SQL and the rule critic decide, and every call records the model Nightshift actually routed it to.
4. **Asker-relative stopping** with human-in-the-loop gates in three modes (auto, scripted, interactive over HTTP).
