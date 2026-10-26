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
    assets = re.findall(r'(?:src|href)="([a-z-]+\.(?:js|css|svg))"', page.text)
    assert {"app.js", "story.js", "empty-investigation.svg"} <= set(assets)
    # the progress bar's icons are named in story.js, not in the page
    assets += re.findall(r'icon: "([a-z-]+\.svg)"', (WEB / "story.js").read_text())
    assert len([a for a in assets if a.startswith("phase-")]) == 5
    for asset in assets:
        assert client.get(f"/ui/{asset}").status_code == 200, asset


def test_api_routes_still_answer_json():
    assert client.get("/health").json()["service"] == "agent"
    assert client.get("/runs/nope").status_code == 404


def test_every_endpoint_the_ui_calls_exists():
    js = (WEB / "app.js").read_text() + (WEB / "story.js").read_text()
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
    js = (WEB / "app.js").read_text() + (WEB / "story.js").read_text()
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


# ---- story view (story.js): one reducer over the trace lines ------------------------------------


def _story(body: str) -> object:
    # Runs story.js's pure reducer section in Node (no DOM).
    node = shutil.which("node")
    if not node:
        pytest.skip("node not on PATH")
    js = (WEB / "story.js").read_text()
    reducer = js[js.index("// ---- reducer") : js.index("// ---- render")]
    out = subprocess.run(
        [node, "-e", reducer + "\n" + body], capture_output=True, text=True, timeout=30, check=True
    )
    return json.loads(out.stdout)


def _replay(name: str, result: dict | None = None) -> dict:
    lines = _trace(name)
    return _story(
        f"const s = storyInit({{ question: 'q' }});\n"
        f"for (const l of {json.dumps(lines)}) storyReduce(s, l);\n"
        f"storyFinish(s, {json.dumps(result)});\n"
        "console.log(JSON.stringify({ steps: s.steps, end: s.end_state,\n"
        "  turns: s.turns.map((t) => ({ who: t.who, kind: t.kind, parent: t.parent || null,\n"
        "    items: (t.items || []).map((x) => x.node_id), verdicts: (t.items || []).map((x) => x.verdict || null),\n"
        "    data: t.data ? dataText(t.data) : null, gate: t.gate || null, choice: t.choice || null })),\n"
        "  nodes: Object.fromEntries([...s.nodes.values()].map((n) => [n.node_id,\n"
        "    { parent: n.parent_id, status: n.status, opinion: n.opinion || null }])) }));"
    )


SAMPLE = "trace-sample-04-3m-2023.jsonl"


def test_story_reducer_on_the_sample_trace():
    got = _replay(SAMPLE)
    assert got["end"] == "actionable"
    assert got["steps"] == {
        "understand": "done",
        "recall": "done",
        "drill": "done",
        "fetch": "done",
        "answer": "done",
    }
    kinds = [(t["who"], t["kind"]) for t in got["turns"]]
    # You ask (rendered from the question), Tiresias frames and proposes, the Sphinx tests,
    # decisions follow, Loop B finds and registers the source, the Sphinx accepts, Tiresias answers
    assert kinds[0] == ("tiresias", "frame")
    assert ("decision", "auto") in kinds and ("tiresias", "registered") in kinds
    assert kinds[-1] == ("tiresias", "answer")
    level1 = [t for t in got["turns"] if t["kind"] == "propose" and t["parent"] == "n0"]
    assert len(level1) == 1 and level1[0]["items"] == ["n0.1", "n0.2", "n0.3"]
    verdicts = [v for t in got["turns"] if t["kind"] == "verdicts" for v in t["verdicts"]]
    assert "accepted" in verdicts and "frontier" in verdicts
    assert [t["choice"] for t in got["turns"] if t["kind"] == "auto"] == ["frontier", "register"]
    nodes = got["nodes"]
    assert nodes["n0"]["status"] == "root"
    assert nodes["n0.1"]["status"] == "frontier"
    assert nodes["n0.1.1"] == {"parent": "n0.1", "status": "accepted", "opinion": None}
    # the recalled lesson held n0.1, so n0.2 and n0.3 were never tested
    assert nodes["n0.2"]["status"] == nodes["n0.3"]["status"] == "untested"
    # sample mode: data lines never credit Zetaris
    data = [t["data"] for t in got["turns"] if t["data"]]
    assert data and all(d.startswith("Sample data") for d in data), data


def test_story_reducer_on_the_live_trace_finds_nemotron_and_credits_zetaris():
    got = _replay(LIVE)
    assert got["steps"]["recall"] == "skipped"  # this run was cold (no memory call)
    assert all(v == "done" for k, v in got["steps"].items() if k != "recall")
    nodes = got["nodes"]
    # Super VL agreed on both prunes; Ultra's reply was unusable (prose, before #39's JSON mode)
    for nid in ("n0.2", "n0.3"):
        assert nodes[nid]["status"] == "pruned"
        assert nodes[nid]["opinion"]["routed_model"] == "nvidia/nemotron-3.5-super-vl-120b-a12b"
        assert nodes[nid]["opinion"]["agrees"] is True
    assert nodes["n0.1"]["opinion"]["unusable"] is True
    assert nodes["n0.1"]["opinion"]["routed_model"] == "nvidia/nemotron-3-ultra-550b-a55b"
    assert nodes["n0.1.1"]["status"] == "accepted"
    data = [t["data"] for t in got["turns"] if t["data"]]
    assert any(d.startswith("Zetaris ·") for d in data), data


def test_story_finish_takes_the_statuses_from_the_run_result():
    result = json.loads((REPO / "output_examples" / "04_3m_2023_revenue_restated.json").read_text())
    got = _replay(SAMPLE, result)
    tree = {n["node_id"]: n for n in result["why_tree"]}
    for nid, n in tree.items():
        want = {"accept": "accepted", "prune": "pruned"}.get(n["verdict"], n["verdict"])
        assert got["nodes"][nid]["status"] == want, (nid, n["verdict"])
        assert got["nodes"][nid]["parent"] == n.get("parent_id")
    assert got["end"] == result["end_state"]


def test_story_steps_never_reached_are_skipped():
    got = _story(
        "const s = storyInit({ question: 'q' });\n"
        "for (const l of [\n"
        "  { agent: 'orchestrator', action: 'phase_start', phase: 'scope_check' },\n"
        "  { agent: 'scope', action: 'check', in_scope: false, output_summary: 'a forecast' },\n"
        "  { agent: 'orchestrator', action: 'phase_done', phase: 'scope_check', outcome: 'out_of_scope' },\n"
        "]) storyReduce(s, l);\n"
        "storyFinish(s, null);\n"
        "console.log(JSON.stringify({ steps: s.steps, turns: s.turns.map((t) => t.kind) }));"
    )
    assert got["steps"] == {
        "understand": "done",
        "recall": "skipped",
        "drill": "skipped",
        "fetch": "skipped",
        "answer": "skipped",
    }
    assert got["turns"] == ["scope"]


def test_star_card_from_a_gate_waiting_line():
    line = {
        "agent": "gate",
        "action": "waiting",
        "gate": "source",
        "gate_id": "g2",
        "node_id": "n0.1",
        "question": "Register catalog template sec_companyfacts for Solventum Corp as 7 statements?",
        "options": ["register", "deny"],
        "proposal": "register",
        "context": {"entity": "Solventum Corp", "claim": "already on the node", "nested": {"a": 1}},
        "deadline": "2026-10-26T07:00:00Z",
    }
    got = _story(f"console.log(JSON.stringify(starCard({json.dumps(line)})));")
    assert got["situation"] == line["question"]
    assert got["facts"] == ["entity: Solventum Corp"]
    assert got["task"].startswith("The figure is in another registrant's filings")
    assert [a["id"] for a in got["actions"]] == ["register", "deny"]  # the API's own choice ids
    assert [a["recommended"] for a in got["actions"]] == [True, False]
    assert all(a["label"] and a["result"] for a in got["actions"])
    assert got["deadline"] == line["deadline"] and got["node_id"] == "n0.1"
    # an unknown gate keeps its question and shows the raw option ids
    got = _story(
        "console.log(JSON.stringify(starCard({ gate: 'new', question: 'Q?', options: ['a'], proposal: 'a' })));"
    )
    assert got["task"] == "Q?" and got["actions"][0]["label"] == "a"


def test_default_run_mode_is_auto_and_examples_are_the_input_examples():
    page = (WEB / "index.html").read_text()
    hitl = page[
        page.index('<select id="hitl">') : page.index("</select>", page.index('<select id="hitl">'))
    ]
    first = re.search(r'<option value="([a-z]+)"( selected)?', hitl)
    assert first.group(1) == "auto" and first.group(2) == " selected"
    assert "Run settings" not in page[page.index('<section class="panel ask"') :]
    node = shutil.which("node")
    if not node:
        pytest.skip("node not on PATH")
    js = (WEB / "story.js").read_text()
    examples = js[js.index("const EXAMPLES") : js.index("const story =")]
    out = subprocess.run(
        [node, "-e", examples + "console.log(JSON.stringify(EXAMPLES.map((x) => x.request)));"],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    got = json.loads(out.stdout)
    want = [
        json.loads((REPO / "input_examples" / f).read_text())
        for f in (
            "04_3m_2023_revenue_restated.json",
            "01_ge_2022_revenue_restated.json",
            "03_ge_2018_net_loss.json",
        )
    ]
    assert got == want
    assert all(r["options"]["hitl_mode"] == "auto" for r in got)


def test_story_hooks_read_the_same_run_as_the_debug_view():
    js = (WEB / "app.js").read_text()
    for hook in (
        "storyStart(req)",
        "storySetRun(started)",
        "storyLine(line)",
        "storyDone(rec, sponsors)",
        "storyError(message)",
    ):
        assert hook in js, hook
    page = (WEB / "index.html").read_text()
    assert page.index('src="app.js"') < page.index('src="story.js"')
    assert '<main class="layout" hidden>' in page and 'data-view="debug"' in page


# ---- prompt gates: the Sphinx asks on the user's behalf, proceeds after 5 s unless taken over ----

GATE_LINE = {
    "agent": "gate",
    "action": "waiting",
    "gate": "critic_escalation",
    "gate_id": "g1",
    "node_id": "n0.1",
    "question": "Cause is plausible but its share cannot be measured. Hand it to Loop B, or prune it?",
    "options": ["frontier", "prune"],
    "proposal": "frontier",
    "deadline": "2026-10-26T07:00:00Z",
}

# A fake clock and POST: timers run when the test says so; every POST is recorded.
_HARNESS = f"""
const posts = [], timers = new Map(); let nextId = 1, changes = 0, reject = null;
const deps = {{
  post: (gateId, body) => {{ posts.push({{ gateId, body }}); return reject ? Promise.reject(new Error(reject)) : Promise.resolve({{}}); }},
  setTimer: (fn, ms) => {{ const id = nextId++; timers.set(id, fn); return id; }},
  clearTimer: (id) => timers.delete(id),
  onChange: () => {{ changes += 1; }},
}};
const tickAll = () => {{ const fns = [...timers.values()]; timers.clear(); fns.forEach((f) => f()); }};
const line = {json.dumps(GATE_LINE)};
"""


def test_prompt_proceeds_automatically_after_five_seconds():
    got = _story(
        _HARNESS + "const p = promptGate(line, deps);\n"
        "const seen = [p.left];\n"
        "for (let i = 0; i < 4; i++) { tickAll(); seen.push(p.left); }\n"
        "const before = posts.length;\n"
        "tickAll();\n"
        "setTimeout(() => console.log(JSON.stringify({ seen, before, posts, state: p.state,\n"
        "  timers: timers.size, ask: sphinxAsks(line), by: answeredBy(posts[0].body.note) })), 0);"
    )
    assert got["seen"] == [5, 4, 3, 2, 1] and got["before"] == 0  # no POST before 5 s
    assert got["posts"] == [
        {
            "gateId": "g1",
            "body": {
                "choice": "frontier",
                "note": "proceeded automatically after 5 s (UI)",
                "auto": True,
            },
        }
    ]
    assert "automatically" in got["posts"][0]["body"]["note"]
    assert got["state"] == "answered" and got["timers"] == 0
    assert got["ask"] == "Should we look for the missing data?"
    assert got["by"] == "auto"


def test_prompt_proceed_posts_now_and_take_over_cancels_the_timer():
    got = _story(
        _HARNESS + "const a = promptGate(line, deps);\n"
        "a.proceed(); a.proceed();\n"  # a second click sends nothing more
        "const b = promptGate({ ...line, gate_id: 'g2' }, deps);\n"
        "b.takeOver();\n"
        "for (let i = 0; i < 10; i++) tickAll();\n"
        "setTimeout(() => console.log(JSON.stringify({ posts, a: a.state, b: b.state,\n"
        "  card: starCard(line).actions.map((x) => [x.id, x.recommended]),\n"
        "  byProceed: answeredBy('proceed (UI)'), byYou: answeredBy(null) })), 0);"
    )
    assert got["posts"] == [
        {"gateId": "g1", "body": {"choice": "frontier", "note": "proceed (UI)"}}
    ]
    assert got["a"] == "answered"
    assert got["b"] == "takeover"  # no POST: the STAR card takes over
    assert got["card"] == [["frontier", True], ["prune", False]]
    assert got["byProceed"] == "proceed" and got["byYou"] == "you"


def test_prompt_409_shows_what_applied():
    got = _story(
        _HARNESS + "reject = 'GateExpired: the gate expired; the proposal applied';\n"
        "const p = promptGate(line, deps); p.proceed();\n"
        "setTimeout(() => console.log(JSON.stringify({ state: p.state, message: p.message })), 0);"
    )
    assert got["state"] == "conflict" and got["message"].startswith("GateExpired")


def test_prompt_answers_read_back_from_the_trace_and_auto_runs_never_prompt():
    answered = {**GATE_LINE, "action": "answered", "choice": "frontier", "decided_by": "human"}
    got = _story(
        "const s = storyInit({ question: 'q' });\n"
        f"storyReduce(s, {json.dumps(GATE_LINE)});\n"
        "const waiting = gatesToPrompt(s, new Map()).map((g) => g.gate_id);\n"
        f"storyReduce(s, {json.dumps({**answered, 'note': 'proceeded automatically after 5 s (UI)'})});\n"
        "const after = gatesToPrompt(s, new Map()).length;\n"
        "const by = s.gates.get('g1').outcome.by;\n"
        "const auto = storyInit({ question: 'q' });\n"
        f"for (const l of {json.dumps(_trace(SAMPLE))}) storyReduce(auto, l);\n"
        "console.log(JSON.stringify({ waiting, after, by, autoPrompts: gatesToPrompt(auto, new Map()).length }));"
    )
    assert got == {"waiting": ["g1"], "after": 0, "by": "auto", "autoPrompts": 0}


def test_pause_toggle_says_five_seconds_and_hosted_demo_has_a_30_s_backstop():
    page = (WEB / "index.html").read_text()
    assert "Pause at each decision (5 s to take over)" in page
    service = (REPO / "deploy" / "gcp" / "service.yaml").read_text()
    assert '{name: GATE_TIMEOUT_SECONDS, value: "30"}' in service
