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
- [C] AGENTS.md (Building blocks, Commands, gotchas) and, if a judge-visible behaviour changed, README describe what this PR adds, so later tasks reuse it instead of rebuilding it.
  FALSIFIED-BY: a new public function, class, CLI or env var that AGENTS.md doesn't mention, or a documented name that doesn't exist.
  VERIFIER: grep -n "<new names>" AGENTS.md README.md

## Checks

- [ ] `uv run --group dev pytest -q` and `ruff check` / `ruff format --check` (services/agent)
- [ ] `npm test` (services/cognition), if touched
- [ ] `./scripts/check_secrets.sh && ./scripts/validate_metadata.sh`
- [ ] AGENTS.md / README updated for any new feature, module, CLI or observability
- [ ] CHECKLIST / STATUS / metadata.json updated in the same change if status moved
