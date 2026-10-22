# Vendored: Meterless H-MEM reference engine

| | |
|---|---|
| Source | https://github.com/Meterless/Meterless, `engines/hmem/reference/src` and `engines/hmem/conformance` (same relative layout here) |
| Commit | `1742dec` (2026-10-19) |
| Licence | Apache-2.0 ([LICENSE](LICENSE)), Copyright 2026 Meterless |
| Modifications | **None.** The files are byte-identical to the source. Tiresias code wraps this engine in `../../src/` and never edits it. |

The conformance runner is run against the module the service actually loads (`src/engine.ts`): `npm run conformance`.
