"""Runtime configuration, read from the environment only (no secrets in code)."""

import os
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
