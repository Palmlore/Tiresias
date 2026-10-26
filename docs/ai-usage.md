# AI and model usage

This file gives the disclosure the event's AI Usage Policy §6 asks for: models (through Nightshift), sponsor integration, the AI coding tools, libraries, data, adapted open-source code, limitations and sample mode.

## Models (in the product)
- **Live mode:** Meterless Nightshift (Runtime, OpenAI-compatible) is the only model provider. The agent asks for a lane (`meterless/fast` for the hypothesiser and reporter lanes, `meterless/deep` for the critic's second opinion, ADR-0010); Nightshift 1.2.1 picked the model itself and ignored explicit ids; 1.2.4 (from 2026-10-26) honours an id pinned in `METERLESS_MODEL_FAST` / `METERLESS_MODEL_DEEP`. Either way each trace line records the `routed_model` from the receipt. On 2026-10-24 the receipts named `nvidia/nemotron-3.5-lightning-30b-a3b`, `nvidia/nemotron-3.5-super-vl-120b-a12b` and `nvidia/nemotron-3-ultra-550b-a55b`. Model output is advisory: candidates are validated and tested on SQL rows, and the rule critic decides.
- **Which runs call a model:** the user picks the investigation depth per question (ADR-0011): `quick` (the default) calls no model, `thorough` asks the deep lane for the critic's second opinion, `deep` uses every lane. `MODEL_LANES=off` turns the model off for every run.
- **Sample mode:** no model calls at any depth. A rule-based hypothesiser and critic run the same orchestration over cached data (ADR-0007).

## Agent framework and libraries
A custom orchestrator in Python (FastAPI, Pydantic, httpx). No third-party agent framework. The cognition service is TypeScript on Node with no npm dependencies. Every third-party component and its licence: [NOTICE](../NOTICE).

## Open-source code adapted
The Meterless H-MEM reference engine (Apache-2.0) is vendored unmodified in `services/cognition/engine/hmem/` (commit `1742dec`) and wrapped by our service; nothing else is adapted.

## Sponsor technology
See [sponsor-technology.md](sponsor-technology.md).

## Development tools
Claude Code (Anthropic's CLI coding agent) is the main development tool, used for scaffolding, code, tests and documentation. Cursor became optional on 23 Oct 2026 (organizer update) and is not required by this build; its IDE was used for a few manual edits. All generated code is reviewed and tested before commit.

From 23 Oct 2026 every pull request is also reviewed by **DuelLoop**, the author's own two-model review loop: the OpenAI **Codex CLI** acts as an adversarial reviewer that must demonstrate each blocking finding by running it, and Claude Code acts as the worker that fixes, rebuts or defers each finding (deferrals go to [BACKLOG.md](../BACKLOG.md)). Using two different model providers is deliberate: a model reviewing its own work mostly measures agreement with itself. The review history is visible on the repository's pull requests. Neither tool is part of the product.

## Assets
The Tiresias mark and the three character icons in [`assets/`](../assets/) were drawn as original geometry on 2026-10-23 in a Cursor coding-agent session (`scripts/render_brand.py`); PNGs are rasterised from those shapes with Pillow. No third-party artwork, likenesses or logos. Each file is listed in [`assets/README.md`](../assets/README.md) (inventory). Cover, social, diagrams, screenshots and the deck are not in the repository yet.

## Data
Public SEC EDGAR and company_dns data. See [data-sources.md](data-sources.md). The sample in `data/sample/` is a cached slice of real filings, not synthetic. No personal or confidential data.

## Limitations
See [limitations.md](limitations.md) and the open items in [BACKLOG.md](../BACKLOG.md).

## Sample mode
`SAMPLE_MODE=true` runs on cached data in `data/sample/` with no calls to Zetaris, Nightshift or SEC and no credentials. It never replays stored outputs. In live mode, an unreachable Zetaris degrades each read to the sample and an unreachable Nightshift degrades the run to the rules, each with a `degraded` trace line.
