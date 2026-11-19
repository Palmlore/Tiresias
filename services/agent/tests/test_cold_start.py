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


def _cold(body: str, before: str = "") -> object:
    # Runs cold-start.js in Node (no DOM: the module exports its pure parts). `before` runs ahead
    # of the require, e.g. a stub DOM the script then finds as a page would.
    node = shutil.which("node")
    if not node:
        pytest.skip("node not on PATH")
    prelude = before + f"const ColdStart = require({json.dumps(str(WEB / 'cold-start.js'))});\n"
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


def test_a_request_held_past_any_per_request_timeout_is_not_aborted_or_retried():
    # Real timers and a fetch that honours the abort signal: the request is cut only at the give-up
    # deadline, so a platform holding it for longer than a retry would take still counts as one attempt.
    got = _cold(
        "const cfg = ColdStart.config({ giveUpSeconds: 3, requestTimeoutSeconds: 0.3 });\n"
        "let n = 0;\n"
        "const env = { now: () => performance.now(), sleep: (ms) => new Promise((r) => setTimeout(r, ms)),\n"
        "  fetch: (u, o) => { n += 1; return new Promise((res, rej) => {\n"
        "    const t = setTimeout(() => res({ ok: true, status: 200, json: async () => ({}) }), 1000);\n"
        "    o.signal.addEventListener('abort', () => { clearTimeout(t); rej(Object.assign(new Error('a'), { name: 'AbortError' })); });\n"
        "  }); } };\n"
        "ColdStart.waitForHealth(cfg, env).then((r) => console.log(JSON.stringify({ ok: r.ok, n })));"
    )
    assert got == {"ok": True, "n": 1}


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
    bad_urls = (
        "http://plain.example",
        "https://x.example/path",
        'https://x.example"+1+"',
        'https://x.example\n"+alert(1)+"',  # grep would pass the first line
        "",
    )
    for bad in bad_urls:
        res = subprocess.run(
            [str(script), bad, str(tmp_path / "bad")],
            check=False,
            capture_output=True,
            text=True,
            cwd=REPO,
        )
        assert res.returncode == 2, bad
        assert not (tmp_path / "bad").exists(), bad  # refused before writing anything
    again = subprocess.run([str(script), url, str(out)], check=False, capture_output=True, cwd=REPO)
    assert again.returncode == 2  # never writes into a non-empty directory
    # a relative OUT_DIR is the caller's, wherever the script lives
    (tmp_path / "cwd").mkdir()
    subprocess.run([str(script), url, "rel"], check=True, capture_output=True, cwd=tmp_path / "cwd")
    assert (tmp_path / "cwd" / "rel" / "config.js").exists() and not (REPO / "rel").exists()


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


# A stub DOM: just what start() touches. textContent counts its writes (the card is a live region).
DOM = (
    "const node = () => { const n = { _t: '', writes: 0, hidden: true, style: {}, dataset: {},\n"
    "  focused: false, focus() { this.focused = true; } };\n"
    "  Object.defineProperty(n, 'textContent', { get() { return this._t; },\n"
    "    set(v) { this._t = v; this.writes += 1; } }); return n; };\n"
    "const hooks = { title: node(), detail: node(), bar: node(), retry: node() };\n"
    'const box = { dataset: {}, style: {}, querySelector: (s) => hooks[s.match(/data-cs="(\\w+)"/)[1]] };\n'
    "let markup = true; const classes = new Set(); const on = {};\n"
    "globalThis.document = { readyState: 'complete',\n"
    "  documentElement: { classList: { add: (...c) => c.forEach((x) => classes.add(x)),\n"
    "    remove: (...c) => c.forEach((x) => classes.delete(x)) } },\n"
    "  getElementById: (id) => (markup && id === 'cold-start' ? box : null),\n"
    "  addEventListener: (t, f) => { on[t] = f; } };\n"
    "let healthy = false, calls = 0;\n"
    "globalThis.fetch = async () => { calls += 1; return { ok: healthy, status: healthy ? 200 : 503,\n"
    "  json: async () => ({}) }; };\n"
    "globalThis.COLD_START_MANUAL = true;\n"
    "const until = (f) => new Promise((r) => { const t = () => (f() ? r() : setTimeout(t, 5)); t(); });\n"
    "const fast = { promiseSeconds: 0.1, giveUpSeconds: 0.4, retryDelaySeconds: 0.05, fadeMs: 1, fastPathMs: 0 };\n"
)


def test_the_page_loader_waits_once_at_a_time_and_stays_quiet():
    got = _cold(
        "const manual = typeof globalThis.ColdStart.start === 'function' && !('ready' in globalThis.ColdStart);\n"
        "const cs = ColdStart.start(fast);\n"
        "const atOnce = calls;\n"  # the health request leaves before the body is parsed
        "let readies = 0; cs.ready.then(() => { readies += 1; });\n"
        "(async () => {\n"
        "  await until(() => !hooks.retry.hidden);\n"
        "  const failed = { state: box.dataset.state, focused: hooks.retry.focused,\n"
        "    title: hooks.title.writes, retry: hooks.retry.writes };\n"
        "  healthy = true; const before = calls;\n"
        "  const click = { target: { closest: () => hooks.retry } };\n"
        "  on.click(click); on.click(click);\n"  # a double click on Try again
        "  await cs.ready; await until(() => !classes.has('cs-waiting'));\n"
        "  on.click(click);\n"  # after success: nothing to do
        "  await new Promise((r) => setTimeout(r, 50));\n"
        "  console.log(JSON.stringify({ manual, atOnce, failed, retried: calls - before, readies,\n"
        "    state: box.dataset.state, classes: [...classes] }));\n"
        "})();",
        before=DOM,
    )
    assert got["manual"] is True  # COLD_START_MANUAL still exposes the API
    assert got["atOnce"] == 1
    # about 40 ticks ran before the failure; each text was written once per change, not per tick
    assert got["failed"]["state"] == "failed" and got["failed"]["focused"] is True
    assert got["failed"]["title"] <= 3 and got["failed"]["retry"] <= 1
    assert got["retried"] == 1 and got["readies"] == 1
    assert got["state"] == "ready" and got["classes"] == []
    # and the Node process exited by itself: no tick or timer left running


def test_without_its_markup_the_loader_hides_nothing_but_still_waits():
    got = _cold(
        "markup = false; healthy = true;\n"
        "const cs = ColdStart.start(fast); const shown = !classes.has('cs-waiting');\n"
        "cs.ready.then((r) => console.log(JSON.stringify({ shown, ok: r.ok })));",
        before=DOM,
    )
    assert got == {"shown": True, "ok": True}


def test_every_api_call_goes_through_the_base_and_gets_reads_send_no_content_type():
    js = (WEB / "app.js").read_text()
    story = (WEB / "story.js").read_text()
    # no relative fetch or EventSource anywhere in the page's scripts
    assert re.findall(r"\bfetch\([^,)]*", js) == ["fetch(API_BASE + path"]
    assert re.findall(r"EventSource\(\S+", js) == ["EventSource(API_BASE"]
    assert "fetch(" not in story and "EventSource(" not in story
    # a GET with Content-Type is not a simple request: cross-origin, it would be preflighted
    api = js[js.index("async function api") : js.index("// ---- formatting")]
    got = _cold(
        "const API_BASE = 'https://api.example'; const seen = [];\n"
        "globalThis.fetch = async (url, o) => { seen.push([url, o.headers]);\n"
        "  return { ok: true, json: async () => ({}) }; };\n"
        + api
        + "\n(async () => { await api('/health'); await api('/runs', { method: 'POST', body: '{}' });\n"
        "  console.log(JSON.stringify(seen)); })();"
    )
    assert got == [
        ["https://api.example/health", {}],
        ["https://api.example/runs", {"Content-Type": "application/json"}],
    ]
