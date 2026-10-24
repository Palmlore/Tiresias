# ADR-0011: Investigation depth: rules by default, model lanes when the user asks for them

Status: accepted (2026-10-24). Replaces the `MODEL_LANES` switch semantics of [ADR-0010](ADR-0010-model-lanes.md); the lanes themselves (what each one may do, validation, bounds, tracing) are unchanged.

Context: ADR-0010 turned the three model lanes on by default in live mode whenever a Nightshift Runtime was configured (`MODEL_LANES=auto`). Measured live on 2026-10-24 on input example 01 (two runs, Zetaris down, so the evidence came from the sample fallback), the lanes dominate the wall time:

| What | Measured |
|---|---|
| Three deep critic calls (second opinion) | 91–94 s in total (24–41 s each) |
| One fast reporter call (action wording) | about 51 s |
| Hypothesiser lane | no call (the rules filled the root level) |
| Whole run, model lanes on | 169–173 s end to end |
| Whole run, rules only (docs/measurements/live-any-company.md) | 3–11 s warm, 15–21 s for a company Zetaris has not registered yet |

A user who wants a quick answer should not wait three minutes for an advisory second opinion, and should know the wait before the run starts.

Decision: the user picks the **investigation depth** per question, `RunRequest.options.depth`:

| Depth | Model lanes (agents/model_lanes.py `DEPTH_LANES`) | Added model time (config.py `MODEL_SECONDS`) |
|---|---|---|
| `quick` (default) | none: rules only | 0 s |
| `thorough` | critic (deep-lane second opinion) | 91–94 s |
| `deep` | hypothesiser, critic, reporter | 142–145 s (91–94 s + about 51 s) |

Precedence (`model_lanes.resolve`, first match wins):
1. Sample mode runs the rules only at every depth (no model call; `RunResult.depth.note` says so). This drops ADR-0010's `MODEL_LANES=on` in sample mode; a demo of the lanes on cached rows runs live with Zetaris down (the fallback serves the sample, as in the measurement above).
2. `MODEL_LANES=off` (operator override) forces `quick` at every depth.
3. `options.depth`.
4. The deprecated `options.model_lanes`: `on` = `deep`, `off` = `quick`, `auto` = as if absent. Still accepted (contract: `deprecated: true`); ignored when `depth` is set.
5. `MODEL_LANES=on` = `deep` when the request names no depth.
6. Otherwise `quick`. `MODEL_LANES=auto` (the default) now means "the request decides".

A depth with model lanes and no configured Runtime, or `MAX_MODEL_CALLS_PER_RUN=0`, runs as `quick` with a `model_lanes`/`degrade` line (`status: degraded`) and `RunResult.depth.degraded: true`. Nightshift failing during the run keeps the existing behaviour (one `<lane>`/`degrade` line, rules for the rest of the run) and also sets `degraded`.

Expected wait: `config.expected_seconds(tier, depth, sample)` = `FIRST_RUN_SECONDS[tier]` + `MODEL_SECONDS[depth]`; sample mode is milliseconds at every depth. It is returned before the run starts as `depth.expected_wait` in the `POST /runs` 202 body (the tier is not known yet, so the range spans warm and cold: 3–21 s at `quick`), on `RunResult.depth`, and per depth on every `GET /entities` entry (`expected_first_run_by_depth`; `expected_first_run` stays the `quick` wait). `RunDepth` carries `requested`, `requested_by`, `effective`, `lanes`, `degraded`, `note` and `expected_wait`.

Consequences:
- Live runs are fast by default again (3–21 s); the model is something the user asks for and is told the price of.
- The model time is one measured case, not a bound: the critic is asked once per judged link, and each hypothesiser call (only where the rules leave a free branch slot) adds up to `MODEL_TIMEOUT_SECONDS`. `MAX_MODEL_CALLS_PER_RUN` and `RUN_BUDGET_SECONDS` stay the bounds. Re-measure (live, `scripts/perf_smoke.py` on a request that sets `options.depth`) before changing `MODEL_SECONDS`.
- `options.model_lanes: "on"` no longer overrides `MODEL_LANES=off`, and `MODEL_LANES=on` no longer runs the lanes in sample mode.
