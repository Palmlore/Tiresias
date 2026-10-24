# Sponsor technology

This is the required sponsor technology explanation, kept honest. Status follows [`metadata.json`](../metadata.json): `planned` → `in-progress` → `working`, and `working` only when a judge can see it run.

## Zetaris: the evidence layer

**Role.** Every claim in a Tiresias report is SQL run through Zetaris plus the rows it returned. The agent never calls SEC APIs directly in live mode.

**How.**
- SEC endpoints (XBRL company facts, filing histories, the ticker map) are registered as Lightning REST tables ([zetaris/sql/](../zetaris/sql/)).
- SQL views flatten the nested JSON, one view per concept (struct paths, never the whole map).
- The parent-vs-spun-off reconciliation reads both entities through Zetaris SQL; the agent computes the add-back from the returned rows. (A cross-source SQL join returning the same $114M residual was measured in [measurements/zetaris-local.md](measurements/zetaris-local.md); the agent does not use it yet.)
- `CACHE TABLE` absorbs SEC rate limits.

**What's distinctive.** In **Loop B** the agent **registers a new source while it runs**: it finds the spun-off entity, then registers that entity's filings (dry run first, one statement at a time, behind the source gate). Source discovery becomes visible and auditable. The analyst also tests each link **counterfactually** on the rows Zetaris returned (reset the candidate to its baseline: does the effect go away?) before the critic approves it.

**Deployment.** Local Docker (vendor bundle `zetaris-platform`, image tag 2.4.3.1-hackathon), REST at `localhost:8888`.

Status: **in-progress**. In live mode the agent drives Zetaris end to end, measured in [measurements/live-any-company.md](measurements/live-any-company.md); the judge's default quickstart is sample mode, which runs the same logic on cached rows without Zetaris ([STATUS.md](STATUS.md) #7).

## Meterless

**H-MEM (engine, Build rule).** The `cognition` service runs the Meterless H-MEM reference engine (Apache-2.0) behind REST.
- It will store **lessons** from past investigations (procedural memory), never answers.
- It will recall them to seed the hypothesiser.
- The memory gate is H-MEM's *programmable approval boundary*.
- Purge will be exposed for the demo (today `/memory/purge` on the agent returns 501); the purge is itself recorded in the append-only trust ledger.
- Evidence: the official H-MEM conformance runner passes against our service module.

**Nightshift (Power rule).** All model calls from the agent will go to the Meterless Runtime (OpenAI-compatible; `meterless-app` and `meterless-scope` headers). It will be the primary provider.

**Candidates for later:** the Markovian engine (each "why" as a bounded step with compressed carryover) and World Model (the why-tree as entities and edges with provenance).

Status: **in-progress**. The H-MEM service works and passes conformance 13/13, but the agent does not call it yet (memory demo planned). Nightshift is live in live mode: the model lanes (ADR-0010) call it for extra candidates, the critic's second opinion and the action wording, traced with the routed model; measured once on case 01 on 2026-10-24 (4 calls). Sample mode stays rule-based.

## NVIDIA

Nemotron models serve the Nightshift lanes: a fast lane for the hypothesiser and reporter, a deep lane for the critic's second opinion. Nightshift 1.2.1 does not let a request pin a model, so the evidence is the receipt: every model call's trace line records `routed_model` and `provider` (on 2026-10-24 every live call was routed to a Nemotron model). The NVIDIA key lives only in Nightshift's Providers tab.

Status: **in-progress** (live lanes measured once; sample mode makes no model call)

## Cursor

Cursor became optional on 23 Oct 2026. The organizers' update to the Build section reads: "Cursor is now optional. You can use any AI coding tool you're comfortable with, such as Cursor, Claude Code, Windsurf or GitHub Copilot." Tiresias is built mainly with Claude Code (see [ai-usage.md](ai-usage.md#development-tools)). The core Build requirement is unchanged: a Meterless engine (H-MEM here) in a working feature, shown with a passing test or a live demo.

Status: **optional, not a Tiresias dependency**

## What was unique

*(To be written from what actually ships.)* Candidate points:
1. Zetaris used as an **auditable evidence layer that grows during the run** (Loop B).
2. H-MEM storing **lessons rather than answers**, with a **purge** that lets judges see the effect.
3. **Asker-relative** stopping, with human-in-the-loop gates that have three modes.
