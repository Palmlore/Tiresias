"""Runtime configuration, read from the environment only (no secrets in code)."""

import os
from collections.abc import Mapping
from dataclasses import dataclass


def _flag(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes"}


@dataclass(frozen=True)
class Settings:
    sample_mode: bool = _flag("SAMPLE_MODE", True)
    hitl_mode: str = os.getenv("HITL_MODE", "auto")
    cognition_url: str = os.getenv("COGNITION_URL", "http://localhost:8100")
    zetaris_rest_url: str = os.getenv("ZETARIS_REST_URL", "")
    meterless_base_url: str = os.getenv("METERLESS_BASE_URL", "")


settings = Settings()


def _bounded_int(env: Mapping[str, str], name: str, default: int, low: int, high: int) -> int:
    try:
        value = int(env.get(name) or default)
    except ValueError:
        value = default
    return max(low, min(value, high))


def _bounded_float(env: Mapping[str, str], name: str, default: float, low: float, high: float):
    try:
        value = float(env.get(name) or default)
    except ValueError:
        value = default
    return max(low, min(value, high))


@dataclass(frozen=True)
class LoopBudgets:
    """Caps for one run, Loop A and Loop B (AGENTS.md rule 7; docs/architecture.md#budgets).

    Read at call time (not import time) so tests and the CLI can tune them through the
    environment. Out-of-range values are clamped, never trusted.
    """

    max_causal_depth: int = 5
    max_branches_per_level: int = 3
    max_retries_per_node: int = 1
    run_seconds: float = 600.0
    max_new_sources: int = 2  # Loop B: sources onboarded per run (MAX_NEW_SOURCES_PER_RUN)
    max_model_calls: int = 12  # model lanes: Nightshift calls per run (MAX_MODEL_CALLS_PER_RUN)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "LoopBudgets":
        env = os.environ if env is None else env
        return cls(
            max_causal_depth=_bounded_int(env, "MAX_CAUSAL_DEPTH", 5, 1, 10),
            max_branches_per_level=_bounded_int(env, "MAX_BRANCHES_PER_LEVEL", 3, 1, 6),
            max_retries_per_node=_bounded_int(env, "MAX_RETRIES_PER_NODE", 1, 0, 3),
            run_seconds=_bounded_float(env, "RUN_BUDGET_SECONDS", 600.0, 1.0, 3600.0),
            max_new_sources=_bounded_int(env, "MAX_NEW_SOURCES_PER_RUN", 2, 0, 5),
            max_model_calls=_bounded_int(env, "MAX_MODEL_CALLS_PER_RUN", 12, 0, 50),
        )


@dataclass(frozen=True)
class WarmSettings:
    """Zetaris cache warm-up (adapters/zetaris.py ``warm``, BACKLOG B-20), read at call time.

    ``CACHE TABLE`` is a Zetaris session cache, lost when Zetaris restarts; re-caching a raw
    table refetches its SEC response, which took over 20 s (the source timeout) for 3M on
    2026-10-24. Each statement gets ``timeout_seconds`` (ZETARIS_WARM_TIMEOUT_SECONDS, 5..600,
    default 120) and the whole pass ``budget_seconds`` (ZETARIS_WARM_BUDGET_SECONDS, 10..3600,
    default 600). At most ``max_tables`` (ZETARIS_WARM_MAX_TABLES, 0..200, default 7: the
    showcase entities' facts and submissions plus the ticker map) are re-cached, because each
    cached companyfacts table holds Zetaris driver heap (re-caching all 63 registered tables ran
    the 2 GB heap out of memory on 2026-10-24). ``on_start`` (ZETARIS_WARM_ON_START, default
    true) runs a warm in a background thread when the API starts in live mode; sample mode
    ignores it."""

    timeout_seconds: float = 120.0
    budget_seconds: float = 600.0
    max_tables: int = 7
    on_start: bool = True

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "WarmSettings":
        env = os.environ if env is None else env
        flag = (env.get("ZETARIS_WARM_ON_START") or "true").strip().lower()
        return cls(
            timeout_seconds=_bounded_float(env, "ZETARIS_WARM_TIMEOUT_SECONDS", 120.0, 5.0, 600.0),
            budget_seconds=_bounded_float(env, "ZETARIS_WARM_BUDGET_SECONDS", 600.0, 10.0, 3600.0),
            max_tables=_bounded_int(env, "ZETARIS_WARM_MAX_TABLES", 7, 0, 200),
            on_start=flag not in {"0", "false", "no", "off"},
        )


MODEL_LANES_MODES = ("auto", "on", "off")


def model_lanes_mode(env: Mapping[str, str] | None = None) -> str:
    """MODEL_LANES (ADR-0010, ADR-0011), read at call time: the operator's override of the
    investigation depth. auto (default): the request's depth decides (quick unless it asks for
    more); on: deep when the request names no depth; off: quick (rules only) at every depth.
    Anything else is auto."""
    env = os.environ if env is None else env
    value = (env.get("MODEL_LANES") or "auto").strip().lower()
    return value if value in MODEL_LANES_MODES else "auto"


# Loop B critic (agents/reconciler.py): the largest share of the root effect a counterparty's
# add-back may explain before the match itself is suspect. Between OVER_EXPLAINED (1.05, in
# agents/common.py) and this bound an over-explaining add-back goes to the critic_escalation gate
# with the proposal "accept-capped" (offsetting items exist); beyond it the proposal is "prune"
# (the counterparty is likely wrong), so the auto policy ends the run at the evidence frontier. A
# scripted or interactive gate may still accept it. BACKLOG B-15.
MAX_OVER_EXPLAINED_DEFAULT = 1.5


def max_over_explained(env: Mapping[str, str] | None = None) -> float:
    """MAX_OVER_EXPLAINED, read at call time and clamped to 1.05..10.0 (default 1.5 = 150%)."""
    env = os.environ if env is None else env
    return _bounded_float(env, "MAX_OVER_EXPLAINED", MAX_OVER_EXPLAINED_DEFAULT, 1.05, 10.0)


# Expected wall time of a first full investigation, in seconds (GET /entities, the scope check's
# alternatives). Measured live on 2026-10-23, docs/measurements/live-any-company.md ("What a UI
# should expect"): a company Zetaris has never registered (cold) takes 15-21 s end to end, a
# registered one (warm) 3-11 s. Sample mode runs every phase in milliseconds
# (docs/agents-reference.md, "Run phases (for UI)"). Re-measure with scripts/perf_smoke.py
# before changing these.
FIRST_RUN_SECONDS: dict[str, tuple[float, float, str]] = {
    "sample": (0.0, 1.0, "sample mode runs every phase in milliseconds (offline slice)"),
    "ready": (3.0, 11.0, "registered in Zetaris (warm), measured 2026-10-23"),
    "searchable": (
        15.0,
        21.0,
        "not registered yet (cold: the run registers it first), measured 2026-10-23",
    ),
}

# Investigation depth (ADR-0011): which model lanes run (agents/model_lanes.py, DEPTH_LANES).
# quick = rules only (the default, live and sample); thorough = + the critic's deep-lane second
# opinion; deep = every model lane (hypothesiser, critic second opinion, reporter wording).
DEPTHS = ("quick", "thorough", "deep")
DEFAULT_DEPTH = "quick"

# Model time a depth adds to a live run, in seconds, on top of FIRST_RUN_SECONDS. Measured live on
# 2026-10-24 on input example 01 (two runs, Zetaris down, so the evidence came from the sample
# fallback): the three deep critic calls took 91-94 s in total, the one fast wording call about
# 51 s, and the whole run 169-173 s (rules only: 3-11 s warm, 15-21 s cold). The hypothesiser lane
# made no call there (the rules filled the root level); each call it makes adds up to
# MODEL_TIMEOUT_SECONDS. The number of critic calls grows with the links judged, so these are a
# measured case, not a bound (MAX_MODEL_CALLS_PER_RUN and RUN_BUDGET_SECONDS are the bounds).
# Source: docs/decisions/ADR-0011-investigation-depth.md. Re-measure before changing these.
MODEL_SECONDS: dict[str, tuple[float, float, str]] = {
    "quick": (0.0, 0.0, "quick: rules only, no model call"),
    "thorough": (
        91.0,
        94.0,
        (
            "thorough: + the critic's deep-lane second opinion, 91-94 s for three deep calls "
            "(input example 01, live, 2026-10-24)"
        ),
    ),
    "deep": (
        142.0,
        145.0,
        (
            "deep: every model lane, 91-94 s for three deep critic calls + about 51 s for the "
            "fast wording call (input example 01, live, 2026-10-24; 169-173 s end to end with "
            "Zetaris down); each hypothesiser call adds up to MODEL_TIMEOUT_SECONDS"
        ),
    ),
}


def expected_seconds(tier: str | None, depth: str, sample: bool) -> tuple[float, float, str]:
    """Expected wall time of a run at ``depth``: FIRST_RUN_SECONDS for the tier (``None`` = not
    known yet: warm or cold) plus MODEL_SECONDS for the depth. Sample mode makes no model call at
    any depth, so every depth takes the sample's milliseconds."""
    if sample:
        lo, hi, basis = FIRST_RUN_SECONDS["sample"]
        return lo, hi, f"{basis}; no model call at any depth"
    if tier is None:
        (lo, warm_hi, _), (cold_lo, hi, _) = (
            FIRST_RUN_SECONDS["ready"],
            FIRST_RUN_SECONDS["searchable"],
        )
        basis = (
            f"rules: {lo:.0f}-{warm_hi:.0f} s if Zetaris has the entity registered (warm), "
            f"{cold_lo:.0f}-{hi:.0f} s if the run registers it first (cold), measured 2026-10-23"
        )
    else:
        lo, hi, basis = FIRST_RUN_SECONDS[tier]
    mlo, mhi, mbasis = MODEL_SECONDS[depth if depth in MODEL_SECONDS else DEFAULT_DEPTH]
    return lo + mlo, hi + mhi, f"{basis}; {mbasis}"
