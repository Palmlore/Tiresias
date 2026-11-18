"""The cold-start screen (web/cold-start.js) and the static front that serves it (GitHub Pages).

The hosted demo scales to zero, so the first request after a quiet spell waits about 7 s while
Cloud Run starts the instance. The page therefore comes from an always-warm static host, paints a
skeleton of the app at once, and treats the first 2xx from GET /health as the only readiness
signal. These tests pin that contract: the timing states, the polling loop, the page wiring, the
Pages build and the Cloud Run settings it relies on.
"""

import json
import os
import re
import shutil
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient

from tiresias_agent import app as app_module

WEB = app_module.WEB_DIR
REPO = WEB.parents[4]
CFG = "const cfg = ColdStart.config({});\n"


def _cold(body: str) -> object:
    # Runs cold-start.js in Node (no DOM: the module exports its pure parts).
    node = shutil.which("node")
    if not node:
        pytest.skip("node not on PATH")
    prelude = f"const ColdStart = require({json.dumps(str(WEB / 'cold-start.js'))});\n"
    out = subprocess.run(
        [node, "-e", prelude + body], capture_output=True, text=True, timeout=30, check=True
    )
    return json.loads(out.stdout)


# A fake clock: sleep and a slow fetch advance it, so a 30 s wait runs in milliseconds.
FAKE = (
    "let clock = 0; const calls = [];\n"
    "const env = (answers) => ({\n"
    "  now: () => clock,\n"
    "  sleep: async (ms) => { clock += ms; },\n"
    "  fetch: async (url) => {\n"
    "    calls.push(url);\n"
    "    const a = answers.length > 1 ? answers.shift() : answers[0];\n"
    "    if (a.after) clock += a.after;\n"
    "    if (a.throw) throw Object.assign(new Error(a.throw), { name: a.throw });\n"
    "    return { ok: a.status >= 200 && a.status < 300, status: a.status, json: async () => { if (a.body === undefined) throw new SyntaxError('no body'); return a.body; } };\n"
    "  },\n"
    "});\n"
)


def test_states_follow_the_promised_and_give_up_times():
    got = _cold(
        CFG + "console.log(JSON.stringify({\n"
        "  phases: [0, 7.9, 8, 29.9, 30, 90].map((s) => ColdStart.phase(s, cfg)),\n"
        "  bar: [0, 4, 8, 20].map((s) => ColdStart.progress(s, cfg)),\n"
        "  d: [cfg.promiseSeconds, cfg.giveUpSeconds, cfg.healthPath] }));"
    )
    assert got["phases"] == ["waiting", "waiting", "slow", "slow", "failed", "failed"]
    # time-based: toward 95% over the promised 8 s, never 100% before the server answers
    assert got["bar"] == [0, 47.5, 95, 95]
    assert got["d"] == [8, 30, "/health"]


def test_a_held_cold_request_counts_as_one_attempt():
    # Cloud Run holds the first request while the instance starts, then answers it.
    got = _cold(
        CFG
        + FAKE
        + "ColdStart.waitForHealth(cfg, env([{ after: 7100, status: 200, body: { service: 'agent' } }]))\n"
        "  .then((r) => console.log(JSON.stringify({ ...r, calls })));"
    )
    assert got["ok"] is True and got["attempts"] == 1 and got["seconds"] == 7.1
    assert got["body"] == {"service": "agent"} and got["calls"] == ["/health"]


def test_fast_failures_are_retried_until_the_first_2xx():
    got = _cold(
        "const cfg = ColdStart.config({ apiBase: 'https://api.example/' });\n"
        + FAKE
        + "ColdStart.waitForHealth(cfg, env([{ throw: 'TypeError' }, { status: 503 }, { status: 429 },"
        " { status: 204 }])).then((r) => console.log(JSON.stringify({ ...r, calls })));"
    )
    assert got["ok"] is True and got["attempts"] == 4 and got["status"] == 204
    assert got["body"] is None  # a 2xx with no JSON body is still healthy
    assert set(got["calls"]) == {"https://api.example/health"}  # trailing slash dropped


def test_it_gives_up_at_the_deadline_and_says_why():
    got = _cold(
        CFG + FAKE + "ColdStart.waitForHealth(cfg, env([{ status: 503 }]))\n"
        "  .then((r) => console.log(JSON.stringify({ ...r, n: calls.length })));"
    )
    assert got["ok"] is False and got["reason"] == "HTTP 503"
    assert got["seconds"] == 30 and got["n"] == got["attempts"] <= 31  # bounded (rule 7)


def test_messages_fill_in_the_promise_and_config_merges_over_defaults():
    got = _cold(
        "const cfg = ColdStart.config({ promiseSeconds: 5, messages: { waiting: 'W' } });\n"
        "console.log(JSON.stringify({ w: cfg.messages.waiting, slow: cfg.messages.slow,"
        " give: cfg.giveUpSeconds, p: cfg.promiseSeconds }));"
    )
    assert got == {"w": "W", "slow": "Taking longer than usual…", "give": 30, "p": 5}


def test_page_paints_the_loader_before_the_app_and_starts_on_health():
    page = (WEB / "index.html").read_text()
    head, body = page.split("<body>", 1)
    # config, then cold-start.js, both classic scripts in <head>: the class is set before <body>
    assert head.index('src="config.js') < head.index('src="cold-start.js')
    assert 'href="cold-start.css' in head and "defer" not in head and "async" not in head
    loader = body[body.index('<div id="cold-start"') : body.index("<!-- Story view")]
    for hook in ("title", "detail", "bar", "retry"):
        assert f'data-cs="{hook}"' in loader, hook
    assert 'role="status"' in loader and "q-card" in loader  # the Story view's own geometry
    assert re.search(r'<main id="story"[^>]*data-cold-start-hide', body)
    assert re.search(r'<main class="layout"[^>]*data-cold-start-hide', body)
    js = (WEB / "app.js").read_text()
    assert "ColdStart.ready.then(boot)" in js
    assert "fetch(API_BASE + path" in js and "new EventSource(API_BASE + `/runs/" in js
    # served by the agent too, same origin: config.js ships with an empty apiBase
    assert len(re.findall(r'apiBase: ""', (WEB / "config.js").read_text())) == 1
    client = TestClient(app_module.app)
    for f in ("config.js", "cold-start.js", "cold-start.css"):
        assert client.get(f"/ui/{f}").status_code == 200, f


def test_pages_build_points_the_copy_at_the_api_and_refuses_anything_but_an_origin(tmp_path):
    script = REPO / "scripts" / "build_pages.sh"
    out = tmp_path / "site"
    url = "https://tiresias-tckagkyqxa-as.a.run.app"
    subprocess.run([str(script), url, str(out)], check=True, capture_output=True, cwd=REPO)
    assert f'apiBase: "{url}"' in (out / "config.js").read_text()
    assert (out / ".nojekyll").exists()
    shipped = {p.name for p in WEB.iterdir() if p.is_file()}
    assert shipped <= {p.name for p in out.iterdir()}
    assert (WEB / "config.js").read_text().count('apiBase: ""') == 1  # source untouched
    for bad in ("http://plain.example", "https://x.example/path", 'https://x.example"+1+"', ""):
        res = subprocess.run(
            [str(script), bad, str(tmp_path / "bad")],
            check=False,
            capture_output=True,
            text=True,
            cwd=REPO,
        )
        assert res.returncode == 2, bad
    again = subprocess.run([str(script), url, str(out)], check=False, capture_output=True, cwd=REPO)
    assert again.returncode == 2  # never writes into a non-empty directory


def test_pages_workflow_is_a_no_op_until_configured_and_stays_out_of_ci():
    wf = (REPO / ".github" / "workflows" / "pages.yml").read_text()
    assert "if: vars.DEMO_API_URL != ''" in wf
    assert 'scripts/build_pages.sh "$DEMO_API_URL" _site' in wf
    assert "secrets." not in wf
    assert "pages" not in (REPO / ".github" / "workflows" / "ci.yml").read_text().lower()


def test_hosted_service_allows_only_the_static_front_and_starts_fast():
    service = (REPO / "deploy" / "gcp" / "service.yaml").read_text()
    origins = re.search(r'name: CORS_ALLOW_ORIGINS, value: "([^"]+)"', service).group(1)
    assert origins.split(",") == ["https://tiresias.palmlore.com", "https://palmlore.github.io"]
    assert 'run.googleapis.com/startup-cpu-boost: "true"' in service
    # both containers probe every second, with the same 60 s ceiling as before
    assert len(re.findall(r"periodSeconds: 1\b", service)) == 2
    assert len(re.findall(r"failureThreshold: 60\b", service)) == 2


def test_cors_answers_the_static_front_and_no_one_else():
    # CORS is read when the app module loads, so this runs in a fresh interpreter.
    code = (
        "import json\n"
        "from fastapi.testclient import TestClient\n"
        "from tiresias_agent.app import app\n"
        "c = TestClient(app)\n"
        "ok = c.get('/health', headers={'Origin': 'https://tiresias.palmlore.com'})\n"
        "other = c.get('/health', headers={'Origin': 'https://evil.example'})\n"
        "pre = c.options('/runs', headers={'Origin': 'https://tiresias.palmlore.com',\n"
        "  'Access-Control-Request-Method': 'POST', 'Access-Control-Request-Headers': 'content-type'})\n"
        "print(json.dumps({'ok': ok.headers.get('access-control-allow-origin'),\n"
        "  'other': other.headers.get('access-control-allow-origin'),\n"
        "  'cred': ok.headers.get('access-control-allow-credentials'),\n"
        "  'pre': pre.status_code, 'pre_origin': pre.headers.get('access-control-allow-origin')}))\n"
    )
    env = {**os.environ, "CORS_ALLOW_ORIGINS": "https://tiresias.palmlore.com"}
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, env=env, check=True
    )
    got = json.loads(out.stdout.strip().splitlines()[-1])
    assert got["ok"] == "https://tiresias.palmlore.com"
    assert got["other"] is None and got["cred"] is None
    assert got["pre"] == 200 and got["pre_origin"] == "https://tiresias.palmlore.com"
