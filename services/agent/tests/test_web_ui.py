"""The web UI (tiresias_agent/web): static files served by the agent at /ui, / redirects there.

The UI is a thin client: every request it makes must be an endpoint the API has, and it inserts
text with textContent only (trace lines carry filing text), never as HTML.
"""

import re

from fastapi.testclient import TestClient

from tiresias_agent import app as app_module

WEB = app_module.WEB_DIR
client = TestClient(app_module.app)


def test_root_redirects_to_the_ui():
    res = client.get("/", follow_redirects=False)
    assert res.status_code in (302, 307)
    assert res.headers["location"] == "/ui/"


def test_ui_page_and_assets_are_served():
    page = client.get("/ui/")
    assert page.status_code == 200
    assert "text/html" in page.headers["content-type"]
    for asset in re.findall(r'(?:src|href)="([a-z]+\.(?:js|css|svg))"', page.text):
        assert client.get(f"/ui/{asset}").status_code == 200, asset


def test_api_routes_still_answer_json():
    assert client.get("/health").json()["service"] == "agent"
    assert client.get("/runs/nope").status_code == 404


def test_every_endpoint_the_ui_calls_exists():
    js = (WEB / "app.js").read_text()
    literals = re.findall(r"`(/(?:health|runs|entities|memory)[^`]*)`", js)
    literals += re.findall(r"\"(/(?:health|runs|entities|memory)[^\"]*)\"", js)
    # ${...} placeholders become one path segment; a query string is not part of the route
    called = {re.sub(r"\$\{[^}]+\}", "x", lit).split("?")[0] for lit in literals}
    assert {"/health", "/entities", "/runs", "/runs/x/events", "/runs/x/gates/x"} <= called, called
    routes = [r.path for r in app_module.app.routes if hasattr(r, "methods")]
    patterns = [re.compile("^" + re.sub(r"\{[^}]+\}", "[^/]+", p) + "$") for p in routes]
    for path in called:
        assert any(p.match(path) for p in patterns), path


def test_ui_never_writes_html_from_data():
    js = (WEB / "app.js").read_text()
    for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write"):
        assert sink not in js, sink


def test_ui_loads_nothing_from_another_origin():
    page = (WEB / "index.html").read_text()
    assert not re.search(r'(?:src|href)="(?:https?:)?//(?!github\.com/Palmlore)', page)


def test_model_chip_comes_from_routed_model_only():
    # The chip names what Nightshift's receipts say served the run, never the requested model.
    js = (WEB / "app.js").read_text()
    chip = js[js.index("function modelChip") : js.index("function renderResult")]
    assert "requested_model" not in chip
    assert "if (line.routed_model) run.models.set(line.routed_model" in js
    assert "renderResult(rec.result, run.models)" in js
    assert "if (!models || !models.size) return null;" in chip
