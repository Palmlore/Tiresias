# Sponsor technology

This is the required sponsor technology explanation, kept honest. Status follows [`metadata.json`](../metadata.json): `planned` → `in-progress` → `working`, and `working` only when a judge can see it run.

## Zetaris: the evidence layer

**Role.** Every claim in a Tiresias report is SQL run through Zetaris plus the rows it returned. The agent never calls SEC APIs directly in live mode.

**How.**
- SEC endpoints (XBRL company facts and concepts, filing histories, the ticker map) and company_dns industry codes are registered as Lightning REST tables.
- SQL views flatten the nested JSON.
- The parent-vs-spun-off reconciliation is a cross-source SQL join.
- `CACHE TABLE` absorbs SEC rate limits.

**What's distinctive.** In **Loop B** the agent **registers a new source while it runs**: it finds the spun-off entity, then registers that entity's filings (dry run first, one statement at a time, behind the source gate). Source discovery becomes visible and auditable. The critic also **re-queries Zetaris counterfactually** before approving a link.

**Deployment.** Local Docker (vendor bundle `zetaris-platform`, image tag 2.4.3.1-hackathon), REST at `localhost:8888`.

Status: **planned**

## Meterless

**H-MEM (engine, Build rule).** The `cognition` service runs the Meterless H-MEM reference engine (Apache-2.0) behind REST.
- It stores **lessons** from past investigations (procedural memory), never answers.
- It recalls them to seed the hypothesiser.
- The memory gate is H-MEM's *programmable approval boundary*.
- Purge is exposed for the demo; the purge is itself recorded in the append-only trust ledger.
- Evidence: the official H-MEM conformance runner passes against our service module.

**Nightshift (Power rule).** All model calls from the agent go to the Meterless Runtime (OpenAI-compatible; `meterless-app` and `meterless-scope` headers). It is the primary provider.

**Candidates for later:** the Markovian engine (each "why" as a bounded step with compressed carryover) and World Model (the why-tree as entities and edges with provenance).

Status: **planned**

## NVIDIA

Nemotron models served through Nightshift lanes: a fast lane for the hypothesiser and a deep lane for the critic. Runs pin a Nemotron-backed lane so the evidence is unambiguous. The NVIDIA key lives only in Nightshift's Providers tab.

Status: **planned**

## Cursor

Cursor is the development environment: the editor, plus its terminal and extensions hosting the AI coding assistants used. This section will describe exactly how Cursor was used, and no more.

Status: **in progress**

## What was unique

*(To be written from what actually ships.)* Candidate points:
1. Zetaris used as an **auditable evidence layer that grows during the run** (Loop B).
2. H-MEM storing **lessons rather than answers**, with a **purge** that lets judges see the effect.
3. **Asker-relative** stopping, with human-in-the-loop gates that have three modes.
