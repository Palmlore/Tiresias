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
Claude Code (Anthropic's CLI coding agent) is the main development tool, used for scaffolding, code, tests and documentation. Cursor became optional on 23 Oct 2026 (organizer update) and is not required by this build. All generated code is reviewed and tested before commit.

## Data
Public SEC EDGAR and company_dns data. See [data-sources.md](data-sources.md). No synthetic data so far; any synthetic data will be labelled.

## Sample mode
`SAMPLE_MODE=true` runs on cached data in `data/sample/` with no external calls and no credentials. It never replays stored outputs.
