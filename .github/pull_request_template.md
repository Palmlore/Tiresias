## What and why

<!-- One paragraph. Link the CHECKLIST item(s) this moves. -->

## ACCEPTANCE CRITERIA

<!--
Read by DuelLoop's reviewer and worker. Each criterion is a [C] bullet with exactly one
FALSIFIED-BY and one VERIFIER; an entry ends at a blank line. Entries without FALSIFIED-BY are refused.
-->

- [C] <the statement the change must satisfy>
  FALSIFIED-BY: <the observation that would show it false>
  VERIFIER: <test name or command that makes that observation>

<!-- Required when the PR adds a feature, module, CLI or observability: -->
- [C] docs/agents-reference.md (Building blocks, phases, external calls), AGENTS.md Commands if a command changed, and, if a judge-visible behaviour changed, README describe what this PR adds, so later tasks reuse it instead of rebuilding it. Findings go where AGENTS.md "Where findings go" says, not into AGENTS.md.
  FALSIFIED-BY: a new public function, class, CLI or env var that docs/agents-reference.md doesn't mention, a documented name that doesn't exist, or a new non-invariant note in AGENTS.md.
  VERIFIER: grep -n "<new names>" docs/agents-reference.md AGENTS.md README.md; git diff main -- AGENTS.md

## Checks

CI runs all of these on the PR; tick only what you also ran yourself.

- [ ] `uv run --group dev pytest -q` and `ruff check` / `ruff format --check` (services/agent)
- [ ] `npm test` (services/cognition), if touched
- [ ] `./scripts/check_secrets.sh && ./scripts/validate_metadata.sh`
- [ ] docs/agents-reference.md / README updated for any new feature, module, CLI or observability
- [ ] CHECKLIST / STATUS / metadata.json updated in the same change if status moved
