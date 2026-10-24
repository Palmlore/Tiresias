"""Test-wide isolation: no test reaches a real Nightshift Runtime through the developer's shell.

Model-lane tests inject a fake transport (httpx.MockTransport) or set these variables
themselves with monkeypatch.
"""

import pytest


@pytest.fixture(autouse=True)
def _no_ambient_nightshift(monkeypatch):
    for name in ("METERLESS_BASE_URL", "METERLESS_RUNTIME_TOKEN", "MODEL_LANES"):
        monkeypatch.delenv(name, raising=False)
