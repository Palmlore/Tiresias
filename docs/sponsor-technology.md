# Sponsor technology

This is the required sponsor technology explanation, kept honest. Status follows [`metadata.json`](../metadata.json): `planned` → `in-progress` → `working`, and `working` only when a judge can see it run, or verify it in a committed trace from a fresh state that records every call, request and response (whoever made the run).

## Zetaris: the data layer

**Role.** Zetaris is the agent's data layer: every claim in a Tiresias report is SQL run through Zetaris plus the rows it returned. In live mode the agent never calls SEC APIs directly; nothing in `services/agent/src` outside the sponsor adapters opens HTTP (`tests/test_any_company.py`).

**Traceable.** Every Zetaris call is one trace line: the SQL, the outcome (an "already exists" HTTP 400 is recorded as `outcome: exists`), the row count, the duration and the retry count; a fallback to the sample is its own `degraded` line. A live run is committed: [logs/samples/](../logs/samples/), described in [output_examples/README.md](../output_examples/README.md).

**How.**
- SEC endpoints (XBRL company facts, filing histories, the ticker map) are registered as Lightning REST tables ([zetaris/sql/](../zetaris/sql/)).
- SQL views flatten the nested JSON, one view per concept (struct paths, never the whole map).
- The parent-vs-spun-off reconciliation reads both entities through Zetaris SQL; the agent computes the add-back from the returned rows. (A cross-source SQL join returning the same $114M residual was measured in [measurements/zetaris-local.md](measurements/zetaris-local.md); the agent does not use it yet.)
- `CACHE TABLE` absorbs SEC rate limits.

**What's distinctive.** In **Loop B** the agent **registers a new source while it runs**: it finds the spun-off entity, then registers that entity's filings (dry run first, one statement at a time, behind the source gate). Source discovery becomes visible and auditable. The analyst also tests each link **counterfactually** on the rows Zetaris returned (reset the candidate to its baseline: does the effect go away?) before the critic approves it.

**Deployment.** Local Docker (vendor bundle `zetaris-platform`, image tag 2.4.3.1-hackathon), REST at `localhost:8888`.

Status: **working**. A live run from a fresh Zetaris (every Tiresias object dropped and the driver restarted first) is committed with every call traced: the run registers what it needs and answers from Zetaris ([output_examples/README.md](../output_examples/README.md)); more runs are measured in [measurements/live-any-company.md](measurements/live-any-company.md). The judge's default quickstart is sample mode, which runs the same logic on cached rows without Zetaris.

## Meterless

**H-MEM (engine, Build rule).** The `cognition` service runs the Meterless H-MEM reference engine (Apache-2.0) behind REST.
- It stores **lessons** from past investigations (procedural memory), never answers: the generic path of hypothesiser steps that led to the cause, with no company, ticker, CIK, amount or date (`agents/memory.py`).
- It recalls them after the framer to seed the hypothesiser, which tests a lesson's steps first; the critic judges each recalled lesson and rejects one whose step it prunes, with negative feedback to H-MEM.
- The memory gate is H-MEM's *programmable approval boundary*: the agent's `memory` gate decides, and the decision travels with the lesson (a discarded lesson is ledgered, not stored).
- Purge is exposed (`POST /memory/purge` on the agent, proxied to cognition); the purge is itself recorded in the append-only trust ledger. `scripts/memory_demo.py` shows cold → warm (fewer hops) → rejected lesson → purge → cold, in sample mode.
- Test: the official H-MEM conformance runner passes 13/13 against our service module (`cd services/cognition && npm run conformance`), with the service's own tests (`npm test`); both run in CI. The feature shows in the committed traces: in [logs/samples/](../logs/samples/) run 02 recalls the lesson run 01 stored and follows it, run 03 recalls it and rejects it.

**Nightshift (Power rule): the only AI provider.** Every model call from the agent goes to the Nightshift Runtime: `POST {METERLESS_BASE_URL}/chat/completions` in OpenAI format with the Runtime token as bearer and the `meterless-app: tiresias` and `meterless-scope: user` headers (`adapters/nightshift.py`). There is no other model client in the code. The agent asks for a lane (`meterless/fast` or `meterless/deep`); the trace line of each call records the model and provider from Nightshift's receipt.

**Candidates for later:** the Markovian engine (each "why" as a bounded step with compressed carryover) and World Model (the why-tree as entities and edges with provenance).

Status: **in-progress**. The H-MEM service works and passes conformance 13/13, and the agent uses it in warm runs, sample mode included (memory demo in CI). Nightshift is live in live mode: the model lanes (ADR-0010) call it for extra candidates, the critic's second opinion and the action wording, traced with the routed model; measured once on case 01 on 2026-10-24 (4 calls). A live run calls it only when the user asks for depth `thorough` (critic) or `deep` (all lanes); the default `quick` is rules only (ADR-0011). Sample mode stays rule-based.

## NVIDIA

Nemotron models serve the Nightshift lanes: a fast lane for the hypothesiser and reporter, a deep lane for the critic's second opinion. Nightshift 1.2.1 does not let a request pin a model, so the evidence is the receipt: every model call's trace line records `routed_model` and `provider` (on 2026-10-24 every live call was routed to a Nemotron model). The NVIDIA key lives only in Nightshift's Providers tab.

Status: **in progress** (the lanes are built and were measured live once on 2026-10-24; sample mode makes no model call, so a judge sees them only in a live run at depth `thorough` or `deep`)

## Cursor

Cursor became optional on 23 Oct 2026. The organizers' update to the Build section reads: "Cursor is now optional. You can use any AI coding tool you're comfortable with, such as Cursor, Claude Code, Windsurf or GitHub Copilot." Tiresias is built mainly with Claude Code (see [ai-usage.md](ai-usage.md#development-tools)). The core Build requirement is unchanged: a Meterless engine (H-MEM here) in a working feature, shown with a passing test or a live demo.

Status: **planned** in metadata.json, the closest value the community schema allows (planned / in-progress / working); in practice Cursor is optional and not a Tiresias dependency

## What was unique

1. **A data layer that grows during the run.** When the registered sources run out, Loop B finds the missing company from filing evidence (the Form 10 that names the parent, through EDGAR full-text search registered in Zetaris), asks the source gate, and registers that company's filings in Zetaris one statement at a time, then reconciles the two companies in SQL. Source discovery is part of the audited trace, not a step outside it.
2. **H-MEM remembers how, never what.** A lesson is the generic path that found a cause, with no company, figure or date; the critic judges each recalled lesson and can reject it in the open, the memory gate decides what is stored, and a purge is ledgered.
3. **Models that never decide, and say who they are.** Nemotron through Nightshift proposes candidates, a second opinion and wording; SQL and the rule critic decide, and every call records the model Nightshift actually routed it to.
4. **Asker-relative stopping** with human-in-the-loop gates in three modes (auto, scripted, interactive over HTTP).
