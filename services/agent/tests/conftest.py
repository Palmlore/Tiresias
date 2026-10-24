"""Test-wide isolation: no test reaches a real Nightshift Runtime through the developer's shell.

Model-lane tests inject a fake transport (httpx.MockTransport) or set these variables
themselves with monkeypatch.
"""

import pytest


@pytest.fixture(autouse=True)
def _no_ambient_nightshift(monkeypatch):
    for name in ("METERLESS_BASE_URL", "METERLESS_RUNTIME_TOKEN", "MODEL_LANES"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def _no_ambient_memory(monkeypatch):
    """No test reaches a real cognition service by default (the input examples ask for warm
    memory): MEMORY_MODE=off forces cold. Memory tests set MEMORY_MODE and inject a fake
    transport, or opt in to a real service with monkeypatch."""
    monkeypatch.setenv("MEMORY_MODE", "off")
    monkeypatch.delenv("MEMORY_NAMESPACE", raising=False)


@pytest.fixture(autouse=True)
def _fresh_zetaris_process_state():
    """The liveness verdict, the companyfacts cache LRU and the released discovery tables are
    per process; each test starts with none of them."""
    from tiresias_agent.adapters import catalog, zetaris

    for reset in (zetaris.LIVENESS.reset, zetaris.FACTS_CACHE.reset, catalog.RELEASED.clear):
        reset()
    yield
    for reset in (zetaris.LIVENESS.reset, zetaris.FACTS_CACHE.reset, catalog.RELEASED.clear):
        reset()
