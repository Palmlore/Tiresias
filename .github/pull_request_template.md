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

## Checks

- [ ] `uv run --group dev pytest -q` and `ruff check` / `ruff format --check` (services/agent)
- [ ] `npm test` (services/cognition), if touched
- [ ] `./scripts/check_secrets.sh && ./scripts/validate_metadata.sh`
- [ ] CHECKLIST / STATUS / metadata.json updated in the same change if status moved
