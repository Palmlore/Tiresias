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


REPO = WEB.parents[4]


def test_model_chip_comes_from_the_model_call_lines_only():
    # The chip names what Nightshift's receipts say served the run, never the requested model, and
    # counts each call once (the client's line), not the second_opinion line that repeats it.
    js = (WEB / "app.js").read_text()
    chip = js[js.index("function modelChip") : js.index("function renderResult")]
    assert "requested_model" not in chip
    assert "if (!models || !models.size) return null;" in chip
    assert "renderResult(rec.result, sponsors.nightshift.models, sponsors)" in js
    assert "s.nightshift.models.set(l.routed_model" in js


def _run_js(body: str) -> object:
    # Executes the pure helpers cut from the shipped app.js in Node, with a stub `el` instead of
    # the DOM.
    node = shutil.which("node")
    if not node:
        pytest.skip("node not on PATH")
    js = (WEB / "app.js").read_text()
    helpers = js[js.index("// ---- declutter") : js.index("// ---- header")]
    chip = js[js.index("function modelChip") : js.index("function renderResult")]
    keep = ("const badge = ", "const plural = ")
    one_liners = "\n".join(ln for ln in js.splitlines() if ln.startswith(keep))
    prelude = (
        "const el = (tag, attrs = {}) => ({ tag, className: attrs.class, textContent: attrs.text });\n"
        f"{one_liners}\n{helpers}\n{chip}\n"
    )
    out = subprocess.run(
        [node, "-e", prelude + body], capture_output=True, text=True, timeout=30, check=True
    )
    return json.loads(out.stdout)


def _trace(name: str) -> list[dict]:
    lines = (REPO / "logs" / "samples" / name).read_text().splitlines()
    return [json.loads(x) for x in lines if x.strip()]


LIVE = "trace-run-20261024T233153Z-c3227e.jsonl"  # live 3M at depth deep, 4 Nightshift calls


def test_model_chip_behaviour_counts_each_call_once_and_ignores_requested():
    got = _run_js(
        "const lines = [\n"
        "  { agent: 'critic', action: 'model_review', routed_model: 'nvidia/nemotron-a',\n"
        "    requested_model: 'nvidia/asked-x', latency_ms: 10 },\n"
        "  { agent: 'critic', action: 'second_opinion', routed_model: 'nvidia/nemotron-a' },\n"
        "  { agent: 'critic', action: 'model_review', routed_model: 'nvidia/nemotron-a', latency_ms: 9 },\n"
        "  { agent: 'analyst', action: 'x', requested_model: 'nvidia/asked-only' },\n"
        "  { agent: 'reporter', action: 'model_wording', routed_model: 'nvidia/nemotron-b', latency_ms: 5 },\n"
        "];\n"
        "const c = modelChip(sponsorSummary(lines, null).nightshift.models);\n"
        "console.log(JSON.stringify({ text: c.textContent, cls: c.className, title: c.title,\n"
        "  none: [modelChip(new Map()), modelChip(undefined), modelChip(null)] }));\n"
    )
    assert got["text"] == "Nightshift · nemotron-a ×2, nemotron-b"
    assert "model" in got["cls"].split()
    assert "asked" not in got["text"] + got["title"]
    assert got["none"] == [None, None, None]


def test_tier_on_real_traces_keeps_decisions_and_steps_in_front():
    for name in (LIVE, "trace-sample-04-3m-2023.jsonl"):
        lines = _trace(name)
        tiers = _run_js(f"console.log(JSON.stringify({json.dumps(lines)}.map(tier)));")
        by = dict(zip(((x["agent"], x["action"]) for x in lines), tiers, strict=True))
        assert by[("critic", "accept")] == "decision"
        assert by[("onboarder", "register")] == "decision"
        assert by[("reporter", "report")] == "decision"
        assert by[("gate", "decide")] == "decision"
        assert by[("hypothesiser", "propose")] == "step"
        assert by[("analyst", "measure")] == "step"
        assert by[("orchestrator", "phase_done")] == "plumbing"
        assert by[("zetaris_adapter", "concept_series")] == "evidence"
        assert all(t in {"decision", "step", "evidence", "plumbing"} for t in tiers)
        shown = sum(t in {"decision", "step"} for t in tiers) / len(tiers)
        assert 0.1 < shown < 0.5, (name, shown)
    live = _trace(LIVE)
    tiers = _run_js(f"console.log(JSON.stringify({json.dumps(live)}.map(tier)));")
    for line, t in zip(live, tiers, strict=True):
        if line["agent"] in ("zetaris_adapter", "catalog") and line.get("sql"):
            assert t in {"evidence", "plumbing"}
        if line["action"].startswith(("bootstrap:", "liveness", "uncache", "list_")):
            assert t == "plumbing", line["action"]


def test_tier_marks_a_model_dissent_and_an_out_of_scope_check_as_decisions():
    got = _run_js(
        "console.log(JSON.stringify([\n"
        "  tier({ agent: 'critic', action: 'second_opinion', status: 'rejected' }),\n"
        "  tier({ agent: 'critic', action: 'second_opinion', status: 'success' }),\n"
        "  tier({ agent: 'scope', action: 'check', in_scope: false }),\n"
        "  tier({ agent: 'scope', action: 'check', in_scope: true }),\n"
        "  tier({ agent: 'orchestrator', action: 'heartbeat' }),\n"
        "]));"
    )
    assert got == ["decision", "step", "decision", "step", "plumbing"]


def test_accepted_path_and_ruled_out_on_committed_why_trees():
    for name in (
        "04_3m_2023_revenue_restated.json",
        "04_3m_2023_revenue_restated.live-fresh-deep.json",
    ):
        tree = json.loads((REPO / "output_examples" / name).read_text())["why_tree"]
        got = _run_js(
            f"const t = {json.dumps(tree)};\n"
            "console.log(JSON.stringify({ path: acceptedPath(t), out: ruledOut(t) }));"
        )
        assert got["path"] == ["n0", "n0.1", "n0.1.1"], name
        pruned = [n["node_id"] for n in tree if n.get("verdict") in ("prune", "pruned")]
        assert [x["node_id"] for x in got["out"]] == pruned
        assert all(x["reason"] for x in got["out"])
    got = _run_js(
        "console.log(JSON.stringify([acceptedPath([]), acceptedPath(null), ruledOut(undefined),\n"
        "  acceptedPath([{ node_id: 'a', parent_id: 'b', verdict: 'accepted' },\n"
        "                { node_id: 'b', parent_id: 'a', verdict: 'root' }]).length <= 3]));"
    )
    assert got == [[], [], [], True]


def _sponsors(lines, memory):
    return _run_js(
        f"const s = sponsorSummary({json.dumps(lines)}, {json.dumps(memory)});\n"
        "console.log(JSON.stringify({ z: s.zetaris, d: s.sample, calls: s.nightshift.calls,\n"
        "  models: Object.fromEntries(s.nightshift.models), h: s.hmem,\n"
        "  badges: sponsorBadges(s).filter(Boolean).map((b) => b.textContent),\n"
        "  none: sponsorSummary([], null).hmem }));"
    )


def _data(lines, sample):
    return [
        x
        for x in lines
        if x["agent"] in ("zetaris_adapter", "catalog")
        and x.get("sql")
        and (x.get("source") == "sample") == sample
    ]


def test_sponsor_summary_counts_from_the_live_trace():
    lines = _trace(LIVE)
    memory = {"available": True, "recalled": [{"memory_id": "mem-1"}], "stored": {"stored": True}}
    got = _sponsors(lines, memory)
    zetaris, sample = _data(lines, False), _data(lines, True)
    assert zetaris and sample  # this live run also read a few rows from the offline slice
    assert got["z"]["sql_calls"] == len(zetaris)
    assert got["z"]["rows"] == sum(int(x.get("rows") or 0) for x in zetaris)
    assert got["d"]["sql_calls"] == len(sample)
    assert got["z"]["registered"] == sum(
        1
        for x in lines
        if (x["agent"], x["action"], x["status"]) == ("onboarder", "register", "success")
    )
    calls = [x for x in lines if x.get("routed_model") and x.get("latency_ms") is not None]
    assert got["calls"] == len(calls) == 4
    assert sum(got["models"].values()) == 4
    assert got["h"] == {"available": True, "recalled": 1, "stored": 1}
    assert got["badges"][0].startswith(f"Zetaris: {len(zetaris)} SQL calls")
    assert got["badges"][1].startswith(f"Sample data: {len(sample)} read")
    assert "no Zetaris call" not in got["badges"][1]
    assert got["badges"][2] == "Nightshift: 4 model calls"
    assert got["badges"][3] == "H-MEM: 1 recalled, 1 stored"
    assert got["none"] is None


def test_sponsor_summary_never_credits_zetaris_in_sample_mode():
    lines = _trace("trace-sample-04-3m-2023.jsonl")
    got = _sponsors(lines, {"available": False})
    assert got["z"]["sql_calls"] == 0
    assert got["d"]["sql_calls"] == len(_data(lines, True)) > 0
    assert not any(b.startswith("Zetaris") for b in got["badges"])
    assert got["badges"][0].endswith("(no Zetaris call)")
    assert got["calls"] == 0
    assert got["badges"][-1] == "H-MEM: unavailable"


def test_memory_panel_and_evidence_never_insert_null():
    js = (WEB / "app.js").read_text()
    memory = js[js.index("function renderMemory") : js.index("// ---- wiring")]
    node = js[js.index("function selectNode") : js.index("function evidenceItem")]
    assert "box.replaceChildren(...present(" in memory
    assert "box.replaceChildren(...present(parts))" in node
    got = _run_js("console.log(JSON.stringify(present(null, 'a', [undefined, 'b', false], null)));")
    assert got == ["a", "b"]


def test_cast_strip_does_not_credit_zetaris_with_sample_reads():
    js = (WEB / "app.js").read_text()
    cast = js[js.index("function countCast") : js.index("function renderCast")]
    assert 'if (line.source === "sample") castCounts.sample += 1;' in cast
    assert "else castCounts.data += 1;" in cast
