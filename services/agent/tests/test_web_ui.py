"""The web UI (tiresias_agent/web): static files served by the agent at /ui, / redirects there.

The UI is a thin client: every request it makes must be an endpoint the API has, and it inserts
text with textContent only (trace lines carry filing text), never as HTML.
"""

import json
import re
import shutil
import subprocess

import pytest
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


def _run_js(body: str) -> object:
    # Executes snippets cut from the shipped app.js in Node, with a stub `el` instead of the DOM.
    node = shutil.which("node")
    if not node:
        pytest.skip("node not on PATH")
    js = (WEB / "app.js").read_text()
    chip = js[js.index("function modelChip") : js.index("function renderResult")]
    badge = next(ln for ln in js.splitlines() if ln.startswith("const badge = "))
    collect = next(
        ln.strip() for ln in js.splitlines() if "if (line.routed_model) run.models.set" in ln
    )
    prelude = (
        "const el = (tag, attrs = {}) => ({ tag, className: attrs.class, textContent: attrs.text });\n"
        f"{badge}\n{chip}\n"
        f"function collect(run, line) {{ {collect} }}\n"
    )
    out = subprocess.run(
        [node, "-e", prelude + body], capture_output=True, text=True, timeout=30, check=True
    )
    return json.loads(out.stdout)


def test_model_chip_behaviour_counts_routed_models_and_ignores_requested():
    got = _run_js(
        "const run = { models: new Map() };\n"
        "for (const line of [\n"
        "  { agent: 'critic', routed_model: 'nvidia/nemotron-a', requested_model: 'nvidia/asked-x' },\n"
        "  { agent: 'critic', routed_model: 'nvidia/nemotron-a' },\n"
        "  { agent: 'analyst', requested_model: 'nvidia/asked-only' },\n"
        "  { agent: 'framer', routed_model: 'nvidia/nemotron-b' },\n"
        "]) collect(run, line);\n"
        "const c = modelChip(run.models);\n"
        "console.log(JSON.stringify({ text: c.textContent, cls: c.className, title: c.title,\n"
        "  none: [modelChip(new Map()), modelChip(undefined), modelChip(null)] }));\n"
    )
    assert got["text"] == "Nightshift · nemotron-a ×2, nemotron-b"
    assert "model" in got["cls"].split()
    assert "asked" not in got["text"] + got["title"]
    assert got["none"] == [None, None, None]
