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

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "LoopBudgets":
        env = os.environ if env is None else env
        return cls(
            max_causal_depth=_bounded_int(env, "MAX_CAUSAL_DEPTH", 5, 1, 10),
            max_branches_per_level=_bounded_int(env, "MAX_BRANCHES_PER_LEVEL", 3, 1, 6),
            max_retries_per_node=_bounded_int(env, "MAX_RETRIES_PER_NODE", 1, 0, 3),
            run_seconds=_bounded_float(env, "RUN_BUDGET_SECONDS", 600.0, 1.0, 3600.0),
            max_new_sources=_bounded_int(env, "MAX_NEW_SOURCES_PER_RUN", 2, 0, 5),
        )


# Expected wall time of a first full investigation, in seconds (GET /entities, the scope check's
# alternatives). Measured live on 2026-10-23, docs/measurements/live-any-company.md ("What a UI
# should expect"): a company Zetaris has never registered (cold) takes 15-21 s end to end, a
# registered one (warm) 3-11 s. Sample mode runs every phase in milliseconds (AGENTS.md, "Run
# phases (for UI)"). Re-measure with scripts/perf_smoke.py before changing these.
FIRST_RUN_SECONDS: dict[str, tuple[float, float, str]] = {
    "sample": (0.0, 1.0, "sample mode runs every phase in milliseconds (offline slice)"),
    "ready": (3.0, 11.0, "registered in Zetaris (warm), measured 2026-10-23"),
    "searchable": (
        15.0,
        21.0,
        "not registered yet (cold: the run registers it first), measured 2026-10-23",
    ),
}
