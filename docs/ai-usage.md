# AI and model usage

This file gives the disclosure the event's AI Usage Policy §6 asks for. It is kept current as the build progresses.

## Models (in the product)
- **Live mode:** Meterless Nightshift (Runtime, OpenAI-compatible) routing to NVIDIA Nemotron lanes. Exact model IDs are recorded here once pinned.
- **Sample mode:** no model calls. A rule-based hypothesiser and critic run the same orchestration over cached data (ADR-0007).

## Agent framework
A custom orchestrator in Python (FastAPI). No third-party agent framework so far.

## Sponsor technology
See [sponsor-technology.md](sponsor-technology.md).

## Development tools
Claude Code (Anthropic's CLI coding agent) is the main development tool, used for scaffolding, code, tests and documentation. Cursor became optional on 23 Oct 2026 (organizer update) and is not required by this build; its IDE was used for a few manual edits. All generated code is reviewed and tested before commit.

From 23 Oct 2026 every pull request is also reviewed by **DuelLoop**, the author's own two-model review loop: the OpenAI **Codex CLI** acts as an adversarial reviewer that must demonstrate each blocking finding by running it, and Claude Code acts as the worker that fixes, rebuts or defers each finding (deferrals go to [BACKLOG.md](../BACKLOG.md)). Using two different model providers is deliberate: a model reviewing its own work mostly measures agreement with itself. The review history is visible on the repository's pull requests. Neither tool is part of the product.

## Data
Public SEC EDGAR and company_dns data. See [data-sources.md](data-sources.md). No synthetic data so far; any synthetic data will be labelled.

## Sample mode
`SAMPLE_MODE=true` runs on cached data in `data/sample/` with no external calls and no credentials. It never replays stored outputs.
