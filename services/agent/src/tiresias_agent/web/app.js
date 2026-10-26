// Tiresias web UI: a thin client over the agent's HTTP API (contracts/openapi/agent.yaml).
// It shows what the API returns and nothing else: every line in the trace panel is a trace
// line streamed from GET /runs/{id}/events, every result is GET /runs/{id}. No data is
// invented or cached here. All text is inserted with textContent (trace lines carry filing text).
"use strict";

const DEFAULT_LEVERS = {
  analyst: ["model adjustments", "estimates", "rating"],
  auditor: ["testing scope", "follow-up requests", "sign-off"],
  board: ["portfolio strategy", "capital allocation"],
};
// Who speaks on a trace line: the three brand characters, plus the system (plumbing, data layer).
const SPHINX = new Set(["critic"]);
const YOU = new Set(["gate"]);
const SYSTEM = new Set(["api", "orchestrator", "model_lanes", "memory_client", "Memory",
  "zetaris_adapter", "catalog", "nightshift"]);
// Adapter-level duplicates of a line another agent already writes; not counted in the cast strip.
const NOISY = new Set(["memory_client", "Memory", "heartbeat"]);

const $ = (id) => document.getElementById(id);
const state = { health: null, entity: null, run: null };

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined || c === false) continue;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
}

async function api(path, options = {}) {
  const res = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  let body = null;
  try { body = await res.json(); } catch { /* not JSON */ }
  if (!res.ok) {
    const msg = body && body.error ? `${body.error.type}: ${body.error.message}` : `HTTP ${res.status}`;
    throw new Error(msg);
  }
  return body;
}

// ---- formatting ---------------------------------------------------------------------------

function money(v) {
  if (v === null || v === undefined || Number.isNaN(v)) return "–";
  const a = Math.abs(v), s = v < 0 ? "−" : "";
  if (a >= 1e9) return `${s}$${(a / 1e9).toFixed(2)}B`;
  if (a >= 1e6) return `${s}$${(a / 1e6).toFixed(1)}M`;
  return `${s}$${a.toLocaleString()}`;
}
const pct = (v) => (v === null || v === undefined ? "–" : `${(v * 100).toFixed(1)}%`);
const plural = (n, word, many = `${word}s`) => `${n} ${n === 1 ? word : many}`;
const residualText = (v) => (v < 0 ? `over-explained by ${pct(-v)} of the change`
  : v > 0 ? `${pct(v)} of the change left unexplained` : "none: the change is fully explained");
const badge = (text, cls = "") => el("span", { class: `badge ${cls}`, text });

function endStateClass(s) {
  if (s === "actionable") return "ok";
  if (s === "evidence-frontier" || s === "external-driver" || s === "branched") return "warn";
  if (s === "budget" || s === "cyclic") return "warn";
  return "";
}

// ---- declutter: pure helpers over the API's own fields (tests run them in Node) ------------

// Which trace lines a reader needs first. decision: a verdict, a gate, a registered source, the
// report, a stored lesson or a model dissent; step: the other reasoning steps and model calls;
// evidence: a SQL read in the data layer; plumbing: orchestration, caching, liveness and the
// adapters' duplicates. The timeline shows decision + step; "every line" shows all four.
const DECISIONS = new Set(["critic/accept", "critic/prune", "critic/frontier", "critic/escalate",
  "critic/lesson_support", "critic/lesson_reject", "gate/decide", "gate/waiting", "gate/answered",
  "gate/expired", "onboarder/register", "reporter/report", "memory/remember"]);
const DATA_AGENTS = new Set(["zetaris_adapter", "catalog"]);
const PLUMBING_AGENTS = new Set(["api", "orchestrator", "model_lanes", "memory_client", "Memory"]);
const PLUMBING_DATA = /^(list_|bootstrap:|liveness|uncache|discovery_cache|register_)/;
const SHOWN_BY_DEFAULT = new Set(["decision", "step"]);

function tier(line) {
  if (DECISIONS.has(`${line.agent}/${line.action}`)) return "decision";
  // second_opinion is "rejected" when the model disagrees with the rule verdict
  if (line.agent === "critic" && line.action === "second_opinion" && line.status === "rejected") return "decision";
  if (line.agent === "scope" && line.in_scope === false) return "decision";
  if (PLUMBING_AGENTS.has(line.agent) || line.action === "heartbeat") return "plumbing";
  if (DATA_AGENTS.has(line.agent)) return line.sql && !PLUMBING_DATA.test(line.action) ? "evidence" : "plumbing";
  return "step";
}

// One Nightshift call = the client's own line (it carries latency_ms); the critic's second_opinion
// line repeats routed_model and must not be counted twice.
const isModelCall = (line) => Boolean(line.routed_model) && line.latency_ms !== undefined && line.latency_ms !== null;

const isAccepted = (n) => n.verdict === "accept" || n.verdict === "accepted";
const isPruned = (n) => n.verdict === "prune" || n.verdict === "pruned";

// The chain the answer rests on: root -> the deepest accepted link, following parent_id.
function acceptedPath(nodes) {
  const list = nodes || [];
  const byId = new Map(list.map((n) => [n.node_id, n]));
  const up = (n) => {
    const ids = [];
    for (let x = n; x && ids.length <= list.length; x = x.parent_id ? byId.get(x.parent_id) : null) ids.unshift(x.node_id);
    return ids;
  };
  let best = [];
  for (const n of list.filter(isAccepted)) {
    const path = up(n);
    if (path.length > best.length) best = path;
  }
  return best;
}

// What the critic ruled out, with its reason.
function ruledOut(nodes) {
  return (nodes || []).filter(isPruned).map((n) => ({ node_id: n.node_id, claim: n.claim, reason: n.reason || null }));
}

// Per sponsor, from the streamed lines (and H-MEM from RunResult.memory at the end). A data-layer
// read with source "sample" was served from the offline slice, not by Zetaris, and is counted apart.
function sponsorSummary(lines, memory) {
  const s = { zetaris: { sql_calls: 0, rows: 0, ms: 0, registered: 0 },
    sample: { sql_calls: 0, rows: 0 }, nightshift: { calls: 0, models: new Map() }, hmem: null };
  let registered = 0;
  for (const l of lines || []) {
    if (DATA_AGENTS.has(l.agent) && l.sql) {
      if (l.source === "sample") {
        s.sample.sql_calls += 1;
        s.sample.rows += Number(l.rows) || 0;
      } else {
        s.zetaris.sql_calls += 1;
        s.zetaris.rows += Number(l.rows) || 0;
        s.zetaris.ms += Number(l.duration_ms) || 0;
      }
    }
    if (l.agent === "onboarder" && l.action === "register" && l.status === "success") registered += 1;
    if (isModelCall(l)) {
      s.nightshift.calls += 1;
      s.nightshift.models.set(l.routed_model, (s.nightshift.models.get(l.routed_model) || 0) + 1);
    }
  }
  // a source registered with no Zetaris call in the run was registered in the sample slice
  if (s.zetaris.sql_calls) s.zetaris.registered = registered;
  else s.sample.registered = registered;
  if (memory) {
    s.hmem = { available: memory.available !== false, recalled: (memory.recalled || []).length,
      stored: memory.stored && memory.stored.stored ? 1 : 0 };
  }
  return s;
}

function sponsorBadges(s) {
  if (!s) return [];
  const z = s.zetaris, d = s.sample, n = s.nightshift, h = s.hmem;
  return [
    z.sql_calls ? badge(`Zetaris: ${plural(z.sql_calls, "SQL call")}, ${z.rows.toLocaleString()} rows, ${(z.ms / 1000).toFixed(1)} s` +
      (z.registered ? `, ${plural(z.registered, "source")} registered` : ""), "sponsor") : null,
    d.sql_calls ? badge(`Sample data: ${plural(d.sql_calls, "read")}, ${d.rows.toLocaleString()} rows` +
      (d.registered ? `, ${plural(d.registered, "source")} registered` : "") + (z.sql_calls ? "" : " (no Zetaris call)"), "sample") : null,
    n.calls ? badge(`Nightshift: ${plural(n.calls, "model call")}`, "sponsor") : null,
    h ? badge(h.available ? `H-MEM: ${h.recalled} recalled, ${h.stored} stored` : "H-MEM: unavailable", "sponsor") : null,
  ];
}

// replaceChildren/append turn null into the text "null"; pass children through this first.
const present = (...xs) => xs.flat().filter((x) => x !== null && x !== undefined && x !== false);

// ---- header: server mode ------------------------------------------------------------------

async function loadHealth() {
  try {
    state.health = await api("/health");
    const sample = state.health.sample_mode;
    const b = $("mode-badge");
    b.textContent = (state.health.public_demo ? "public demo · " : "") +
      (sample ? "sample mode: cached SEC data, rules only" : "live: Zetaris + Nightshift");
    b.className = `badge ${sample ? "sample" : "live"}`;
    b.title = sample
      ? "Runs the real agent logic on an offline slice of SEC data; no model is called."
      : "Every read goes through Zetaris; depth thorough/deep calls Nemotron models through Nightshift.";
    if (state.health.public_demo) {
      const n = $("demo-note");
      n.textContent = "Demo-grade hosting: this public copy runs on one small server to keep its cost " +
        "bounded, so it can be slow, and a run may be refused for a moment when four are already " +
        "running. Sample data only; run it locally for live mode.";
      n.hidden = false;
    }
    const depth = $("depth");
    depth.disabled = sample;
    $("depth-note").textContent = sample
      ? "Sample mode never calls a model, at any depth."
      : "Thorough and deep call Nightshift models and take minutes.";
  } catch (e) {
    $("mode-badge").textContent = "agent unreachable";
    $("mode-badge").className = "badge bad";
  }
}

// ---- 1. Ask: companies and suggested questions ---------------------------------------------

let entityTimer = null;
async function loadEntities(q = "") {
  const list = $("entity-list");
  try {
    const body = await api(`/entities?limit=12${q ? `&q=${encodeURIComponent(q)}` : ""}`);
    list.replaceChildren(...body.entities.map((e) => el("li", {},
      el("button", {
        type: "button",
        "aria-pressed": state.entity && state.entity.cik === e.cik ? "true" : "false",
        title: `${e.name} (CIK ${e.cik})`,
        onclick: () => selectEntity(e),
      }, e.ticker || e.name, e.tier !== "ready" ? el("span", { class: "tier", text: e.tier }) : null))));
    if (!body.entities.length) list.replaceChildren(el("li", { class: "note", text: "No match." }));
  } catch (e) {
    list.replaceChildren(el("li", { class: "note", text: e.message }));
  }
}

async function selectEntity(e) {
  state.entity = e;
  for (const b of $("entity-list").querySelectorAll("button")) {
    b.setAttribute("aria-pressed", b.title.endsWith(`(CIK ${e.cik})`) ? "true" : "false");
  }
  $("c-ticker").value = e.ticker || "";
  await loadSuggestions();
}

async function loadSuggestions() {
  const box = $("suggestions");
  if (!state.entity) return;
  box.replaceChildren(el("p", { class: "note", text: `Looking at ${state.entity.name}'s filings…` }));
  try {
    const role = $("role").value;
    const body = await api(`/entities/${encodeURIComponent(state.entity.cik)}/suggestions?role=${role}&limit=6`);
    if (!body.suggestions.length) {
      box.replaceChildren(el("p", { class: "note", text: body.note || "No question found in the filings for this company." }));
      return;
    }
    box.replaceChildren(
      el("p", { class: "note", text: `${body.suggestions.length} questions found in ${state.entity.name}'s filings:` }),
      ...body.suggestions.map((s) => el("div", { class: "suggestion" },
        el("span", { class: "kind", text: s.kind }),
        el("p", { text: s.question }),
        el("details", {}, el("summary", { text: "why this question" }),
          el("ul", { class: "checks" }, (s.checks || []).map((c) => el("li", { text: c })))),
        el("button", { type: "button", class: "btn small", onclick: () => startRun(s.request) }, "Investigate"))));
  } catch (e) {
    box.replaceChildren(el("p", { class: "note", text: e.message }));
  }
}

function customRequest() {
  const role = $("role").value;
  const year = Number($("c-year").value);
  return {
    question: $("c-question").value.trim(),
    entity: { ticker: $("c-ticker").value.trim().toUpperCase() },
    metric: $("c-metric").value,
    period: { start: `${year}-01-01`, end: `${year}-12-31` },
    asker: { role, levers: DEFAULT_LEVERS[role] },
  };
}

// ---- 2. Run: start, stream, gates, result --------------------------------------------------

function withSettings(request) {
  const req = structuredClone(request);
  req.asker = { ...(req.asker || {}), role: $("role").value };
  if (!req.asker.levers || !req.asker.levers.length) req.asker.levers = DEFAULT_LEVERS[req.asker.role];
  req.options = { ...(req.options || {}), hitl_mode: $("hitl").value, memory: $("memory").value };
  if (state.health && !state.health.sample_mode) req.options.depth = $("depth").value;
  return req;
}

async function startRun(request) {
  if (state.run && state.run.source) state.run.source.close();
  const req = withSettings(request);
  resetRunView(req);
  let started;
  try {
    started = await api("/runs", { method: "POST", body: JSON.stringify(req) });
  } catch (e) {
    showResultError(e.message);
    return;
  }
  const run = { id: started.run_id, lines: [], parsed: [], gates: new Map(), source: null, finished: false };
  state.run = run;
  const wait = started.depth && started.depth.expected_wait;
  $("run-meta").textContent = `run ${run.id}` +
    (wait ? ` · expected ${wait.min_seconds}–${wait.max_seconds} s` : "") +
    (started.depth ? ` · depth ${started.depth.effective}` : "");
  const src = new EventSource(`/runs/${encodeURIComponent(run.id)}/events`);
  run.source = src;
  src.addEventListener("trace", (msg) => {
    if (state.run !== run) return;
    let line;
    try { line = JSON.parse(msg.data); } catch { return; }
    run.lines.push(msg.data);
    run.parsed.push(line);
    onTrace(run, line);
  });
  src.addEventListener("end", () => {
    src.close();
    if (state.run === run) finishRun(run);
  });
  src.onerror = () => {
    // The server closes the stream after "end"; any other drop: fall back to polling once.
    if (!run.finished && src.readyState === EventSource.CLOSED) finishRun(run);
  };
}

function resetRunView(req) {
  $("run-empty").hidden = true;
  $("run-head").hidden = false;
  $("run-question").textContent = req.question;
  $("run-meta").textContent = "starting…";
  $("gate-slot").replaceChildren();
  $("result").hidden = true;
  $("result").replaceChildren();
  $("tree-wrap").hidden = true;
  $("tree").replaceChildren();
  $("timeline-wrap").hidden = false;
  $("timeline").replaceChildren();
  $("event-count").textContent = "";
  castCounts = { tiresias: 0, sphinx: 0, you: 0, human: 0, data: 0, sample: 0 };
  renderCast();
  $("evidence").className = "empty";
  $("evidence").replaceChildren(el("p", { text: "Select a link in the why-tree to see the SQL and the rows it rests on." }));
  $("memory-panel").hidden = true;
}

function who(line) {
  if (SPHINX.has(line.agent)) return { cls: "sphinx", icon: "sphinx.svg", name: "Sphinx · critic" };
  if (YOU.has(line.agent)) return { cls: "you", icon: "you.svg", name: "Gate" };
  if (SYSTEM.has(line.agent)) {
    const name = line.agent === "zetaris_adapter" || line.agent === "catalog" ? `Zetaris · ${line.agent}` : line.agent;
    return { cls: "system", icon: null, name };
  }
  return { cls: "tiresias", icon: "tiresias.svg", name: `Tiresias · ${line.agent}` };
}

function lineText(line) {
  return line.output_summary || line.input_summary || line.question || line.action;
}

// The cast strip: who did what so far, counted from the trace lines as they stream in.
let castCounts = { tiresias: 0, sphinx: 0, you: 0, human: 0, data: 0, sample: 0 };

function countCast(line) {
  if (line.agent === "orchestrator" || NOISY.has(line.agent)) return;
  if (line.agent === "gate") {
    if (line.action === "decide") {
      castCounts.you += 1;
      if (line.decided_by === "human") castCounts.human += 1;
    }
  } else if (SPHINX.has(line.agent)) castCounts.sphinx += 1;
  else if (DATA_AGENTS.has(line.agent) && line.sql) {
    // a read served from the offline sample slice is not a Zetaris call
    if (line.source === "sample") castCounts.sample += 1;
    else castCounts.data += 1;
  }
  else if (!SYSTEM.has(line.agent)) castCounts.tiresias += 1;
}

function renderCast() {
  const c = castCounts;
  $("cast-tiresias").textContent = plural(c.tiresias, "step");
  $("cast-sphinx").textContent = plural(c.sphinx, "verdict");
  $("cast-you").textContent = plural(c.you, "gate decision") + (c.you ? ` (${c.human} by you)` : "");
  $("cast-data").textContent = plural(c.data, "SQL call") + (c.sample ? ` · ${plural(c.sample, "sample read")}` : "");
}

function eventCount(run) {
  const shown = run.parsed.filter((l) => SHOWN_BY_DEFAULT.has(tier(l))).length;
  $("event-count").textContent = $("show-all").checked
    ? `(${run.lines.length} lines)` : `(${run.lines.length} lines, ${shown} shown)`;
}

function onTrace(run, line) {
  eventCount(run);
  countCast(line);
  renderCast();
  if (line.agent === "gate") onGateLine(run, line);
  const t = tier(line);
  const li = renderLine(line);
  li.dataset.tier = t;
  if (!SHOWN_BY_DEFAULT.has(t) && !$("show-all").checked) li.hidden = true;
  const tl = $("timeline");
  const atBottom = tl.scrollHeight - tl.scrollTop - tl.clientHeight < 40;
  tl.append(li);
  if (atBottom) tl.scrollTop = tl.scrollHeight;
}

function renderLine(line) {
  if (line.agent === "orchestrator" && (line.action === "phase_start" || line.action === "phase_done")) {
    const done = line.action === "phase_done";
    const txt = done
      ? `✓ ${line.phase} · ${line.duration_ms ?? "?"} ms${line.outcome ? ` · ${line.outcome}` : ""}${line.status !== "success" ? ` · ${line.status}` : ""}`
      : `▸ ${line.phase}${line.target_agent ? ` (${line.target_agent})` : ""}`;
    return el("li", { class: `ev phase${line.status === "error" ? " err" : ""}`, text: txt });
  }
  const w = who(line);
  const cls = ["ev", w.cls];
  if (line.status === "error") cls.push("err");
  if (line.status === "degraded") cls.push("degraded");
  const meta = [];
  if (line.mode === "sample" && line.source === "sample") meta.push("sample");
  if (line.rows !== undefined) meta.push(`${line.rows} rows`);
  if (line.duration_ms !== undefined) meta.push(`${line.duration_ms} ms`);
  if (line.routed_model) meta.push(`model ${line.routed_model}`);
  if (line.receipt_id) meta.push(`receipt ${line.receipt_id}`);
  if (line.status && line.status !== "success") meta.push(line.status);
  const body = el("div", {},
    el("span", { class: "who", text: w.name }),
    el("span", { class: "text", text: `${line.action}: ${lineText(line)}` }),
    meta.length ? el("span", { class: "meta", text: meta.join(" · ") }) : null);
  if (line.sql) body.append(el("details", {}, el("summary", { text: "SQL" }), el("pre", { text: line.sql })));
  const icon = w.icon ? el("img", { src: w.icon, alt: "" }) : el("span");
  return el("li", { class: cls.join(" ") }, icon, body);
}

$("show-all").addEventListener("change", () => {
  const all = $("show-all").checked;
  for (const li of $("timeline").querySelectorAll("[data-tier]")) {
    li.hidden = !all && !SHOWN_BY_DEFAULT.has(li.dataset.tier);
  }
  if (state.run) eventCount(state.run);
});

// Gates: a "waiting" line opens a card; "answered" or "decide" closes it.
function onGateLine(run, line) {
  if (line.action === "waiting" && line.gate_id) {
    const card = gateCard(run, line);
    run.gates.set(line.gate_id, card);
    $("gate-slot").append(card);
  } else if ((line.action === "answered" || line.action === "expired") && run.gates.has(line.gate_id)) {
    closeGate(run.gates.get(line.gate_id), line.action === "answered"
      ? `You chose “${line.choice}”.` : `No answer in time: the proposal “${line.proposal}” was applied.`);
  }
}

function gateCard(run, line) {
  const ctx = line.context || {};
  const ctxRows = Object.entries(ctx).filter(([, v]) => v !== null && v !== undefined && typeof v !== "object");
  const card = el("div", { class: "gate", role: "group", "aria-label": `${line.gate} gate` },
    el("h4", {}, el("img", { src: "you.svg", alt: "", class: "who-icon" }), `Your call · ${line.gate.replace("_", " ")} gate`),
    el("p", { text: line.question }),
    ctxRows.length ? el("dl", { class: "kv" }, ctxRows.flatMap(([k, v]) => [el("dt", { text: k }), el("dd", { text: String(v) })])) : null,
    el("p", { class: "note", text: `The agent proposes “${line.proposal}”. If you don't answer by ${line.deadline ? new Date(line.deadline).toLocaleTimeString() : "the deadline"}, the proposal is applied.` }));
  const opts = el("div", { class: "options" }, (line.options || []).map((opt) =>
    el("button", {
      type: "button",
      class: `btn small${opt === line.proposal ? "" : " alt"}`,
      onclick: async (ev) => {
        for (const b of opts.querySelectorAll("button")) b.disabled = true;
        try {
          await api(`/runs/${encodeURIComponent(run.id)}/gates/${encodeURIComponent(line.gate_id)}`,
            { method: "POST", body: JSON.stringify({ choice: opt }) });
        } catch (e) {
          card.append(el("p", { class: "note", text: e.message }));
          for (const b of opts.querySelectorAll("button")) b.disabled = false;
        }
      },
    }, opt)));
  card.append(opts);
  return card;
}

function closeGate(card, text) {
  card.classList.add("done");
  for (const b of card.querySelectorAll("button")) b.disabled = true;
  card.querySelector(".options")?.remove();
  for (const x of card.querySelectorAll("dl, p.note")) x.remove();
  card.append(el("p", { class: "note", text }));
}

async function finishRun(run) {
  if (run.finished) return;
  run.finished = true;
  let rec;
  try {
    rec = await api(`/runs/${encodeURIComponent(run.id)}`);
  } catch (e) {
    showResultError(e.message);
    return;
  }
  if (rec.status === "running") { // stream budget ran out while the run continues
    showResultError("The run is still going; reload its result later.", run.id);
    return;
  }
  if (rec.error && !rec.result) { showResultError(`${rec.error.type}: ${rec.error.message}`); return; }
  const sponsors = sponsorSummary(run.parsed, rec.result && rec.result.memory);
  renderResult(rec.result, sponsors.nightshift.models, sponsors);
}

function showResultError(message) {
  const box = $("result");
  box.hidden = false;
  box.replaceChildren(el("div", { class: "result error" }, el("h4", { text: "The run did not finish" }), el("p", { text: message })));
}

// ---- result, why-tree, evidence, memory ----------------------------------------------------

// The models Nightshift says served this run: one chip from the routed_model of its trace lines,
// never from the request (the receipt is the evidence; ADR-0010).
function modelChip(models) {
  if (!models || !models.size) return null;
  const parts = [...models].map(([id, n]) => `${id.replace(/^nvidia\//, "")}${n > 1 ? ` ×${n}` : ""}`);
  const chip = badge(`Nightshift · ${parts.join(", ")}`, "model");
  chip.title = "Routed model per Nightshift's receipt: " + [...models.keys()].join(", ");
  return chip;
}

function renderResult(r, models, sponsors) {
  const box = $("result");
  box.hidden = false;
  const cls = r.scope ? "scope" : r.end_state === "evidence-frontier" ? "frontier" : "";
  const e = r.effect;
  const card = el("div", { class: `result ${cls}` },
    el("h4", {}, badge(r.end_state || r.status, endStateClass(r.end_state)),
      r.sample_mode ? badge("sample data", "sample") : badge("live", "live"),
      modelChip(models),
      r.action_source === "model" ? badge("wording by model", "muted") : null),
    e ? el("div", { class: "effect" },
      el("span", { text: `${e.entity} ${e.metric}` }),
      el("span", { text: `${money(e.baseline && e.baseline.value)} (${e.baseline ? `${e.baseline.form} filed ${e.baseline.filed}` : ""})` }),
      el("span", { class: "arrow", text: "→" }),
      el("span", { text: `${money(e.target && e.target.value)} (${e.target ? `${e.target.form} filed ${e.target.filed}` : ""})` }),
      el("span", { class: `delta ${e.delta < 0 ? "neg" : "pos"}`, text: `${money(e.delta)} (${pct(e.relative_change)})` })) : null,
    r.action ? el("p", { class: "action", text: r.action }) : null,
    el("dl", { class: "kv" },
      r.root_cause && !r.scope ? [el("dt", { text: "Root cause" }), el("dd", { text: r.root_cause })] : null,
      r.residual !== null && r.residual !== undefined ? [el("dt", { text: "Residual" }),
        el("dd", { text: residualText(r.residual) })] : null,
      r.information_need ? [el("dt", { text: "Evidence needed" }), el("dd", { text: r.information_need.description || r.information_need.question || JSON.stringify(r.information_need) })] : null,
      r.error ? [el("dt", { text: "Error" }), el("dd", { text: `${r.error.type}: ${r.error.message}` })] : null),
    r.stats && !r.scope ? el("div", { class: "stats" },
      badge(`${r.stats.hops} hops`), badge(`${r.stats.queries} queries`),
      badge(plural(r.stats.sources_onboarded, "source") + " registered"), badge(plural(r.stats.gate_decisions, "gate decision")),
      badge(`${r.stats.prunes ?? 0} pruned`), badge(`${r.stats.model_calls} model calls`), badge(`${r.stats.seconds} s`)) : null,
    r.stats && !r.scope && sponsors ? el("div", { class: "stats sponsors" }, sponsorBadges(sponsors)) : null);
  box.replaceChildren(card);
  if (r.scope) box.append(scopeCard(r.scope));
  renderTree(r.why_tree || []);
  renderMemory(r.memory);
}

function scopeCard(scope) {
  return el("div", { class: "result scope" },
    el("h4", { text: "Outside what Tiresias can answer from filed figures" }),
    el("ul", {}, (scope.reasons || []).map((x) => el("li", { text: x.message }))),
    (scope.alternatives || []).length ? el("p", { class: "note", text: "Questions it can answer instead:" }) : null,
    el("div", { class: "suggestions" }, (scope.alternatives || []).map((a) => el("div", { class: "suggestion" },
      el("p", { text: a.request ? a.request.question : a.basis }),
      a.request ? el("button", { type: "button", class: "btn small", onclick: () => startRun(a.request) }, "Ask this instead") : null))));
}

function renderTree(nodes) {
  const wrap = $("tree-wrap");
  if (!nodes.length) { wrap.hidden = true; return; }
  wrap.hidden = false;
  const kids = new Map();
  for (const n of nodes) {
    const k = n.parent_id || "";
    if (!kids.has(k)) kids.set(k, []);
    kids.get(k).push(n);
  }
  const build = (parent) => (kids.get(parent) || []).map((n) => {
    const pruned = isPruned(n);
    const good = isAccepted(n) || n.verdict === "root";
    const btn = el("button", { type: "button", "aria-pressed": "false", onclick: () => selectNode(n, btn) },
      el("div", { class: "claim", text: n.claim }),
      el("div", { class: "chips" },
        n.verdict ? badge(n.verdict, good ? "ok" : pruned ? "muted" : "warn") : null,
        n.label ? badge(n.label) : null,
        n.evidence_status ? badge(n.evidence_status, "muted") : null,
        n.share_explained !== null && n.share_explained !== undefined ? badge(`explains ${pct(n.share_explained)}`) : null,
        n.proposed_by === "model" ? badge("proposed by model", "muted") : null,
        n.model_opinion ? badge(`model: ${n.model_opinion.decision || "opinion"}`, "muted") : null,
        (n.evidence || []).some((x) => x.degraded) ? badge("degraded", "warn") : null));
    const sub = build(n.node_id);
    return el("li", { class: `node${pruned ? " pruned ruled-out" : ""}${onPath.has(n.node_id) ? " on-path" : ""}` },
      btn, sub.length ? el("ol", {}, sub) : null);
  });
  const roots = nodes.filter((n) => !n.parent_id || !nodes.some((m) => m.node_id === n.parent_id));
  const onPath = new Set(acceptedPath(nodes));
  $("tree").replaceChildren(...roots.flatMap((r) => {
    kids.set("__root_" + r.node_id, [r]);
    return build("__root_" + r.node_id);
  }));
  const first = $("tree").querySelector(".node > button");
  if (first) first.click();
}

function selectNode(n, btn) {
  for (const b of $("tree").querySelectorAll(".node > button")) b.setAttribute("aria-pressed", "false");
  btn.setAttribute("aria-pressed", "true");
  const box = $("evidence");
  box.className = "";
  const parts = [
    el("h3", { text: n.claim }),
    el("dl", { class: "kv" },
      [el("dt", { text: "Node" }), el("dd", { text: n.node_id })],
      n.rule ? [el("dt", { text: "Rule" }), el("dd", { text: n.rule })] : null,
      n.verdict ? [el("dt", { text: "Critic" }), el("dd", { text: n.verdict })] : null,
      n.reason ? [el("dt", { text: "Why" }), el("dd", { text: n.reason })] : null,
      n.share_explained !== null && n.share_explained !== undefined ? [el("dt", { text: "Share explained" }), el("dd", { text: pct(n.share_explained) })] : null,
      n.residual !== null && n.residual !== undefined ? [el("dt", { text: "Residual" }), el("dd", { text: pct(n.residual) })] : null,
      [el("dt", { text: "Attempts" }), el("dd", { text: String(n.attempts ?? 1) })]),
  ];
  const evs = n.evidence || [];
  if (!evs.length && n.sql) parts.push(el("pre", { text: n.sql }));
  if (!evs.length && !n.sql) parts.push(el("p", { class: "note", text: "No query: this link is a hypothesis the critic did not accept." }));
  for (const ev of evs) parts.push(evidenceItem(ev));
  if (n.model_opinion) {
    const m = n.model_opinion;
    parts.push(el("div", { class: "opinion" },
      el("strong", { text: "Model second opinion " }),
      el("span", { class: "note", text: `${m.routed_model || m.requested_model || ""}${m.receipt_id ? ` · receipt ${m.receipt_id}` : ""}` }),
      el("p", { text: `${m.decision || ""}${m.label ? ` (${m.label})` : ""}${m.agrees === false ? " · disagrees with the rules" : m.agrees ? " · agrees with the rules" : ""}` }),
      m.rationale ? el("p", { class: "note", text: m.rationale }) : null));
  }
  box.replaceChildren(...present(parts));
}

function evidenceItem(ev) {
  const rows = ev.rows || [];
  const cols = rows.length ? Object.keys(rows[0]) : [];
  return el("div", { class: "ev-item" },
    el("div", { class: "stats" },
      badge(ev.source === "sample" ? "sample data" : ev.source || "source", ev.source === "sample" ? "sample" : "live"),
      badge(`${ev.row_count ?? rows.length} rows`),
      ev.matched !== undefined && ev.matched !== null ? badge(`${ev.matched} matched`) : null,
      ev.degraded ? badge("degraded: served from the sample", "warn") : null),
    el("pre", { text: ev.sql }),
    ev.filter ? el("p", { class: "note", text: `Filter: ${ev.filter}` }) : null,
    ev.note ? el("p", { class: "note", text: ev.note }) : null,
    rows.length ? el("div", { class: "rows-wrap" }, el("table", { class: "rows" },
      el("thead", {}, el("tr", {}, cols.map((c) => el("th", { text: c })))),
      el("tbody", {}, rows.slice(0, 50).map((row) => el("tr", {}, cols.map((c) => el("td", { text: fmtCell(row[c]) }))))))) : null,
    rows.length > 50 ? el("p", { class: "note", text: `Showing 50 of ${rows.length} rows.` }) : null);
}

function fmtCell(v) {
  if (v === null || v === undefined) return "";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

function renderMemory(m) {
  const box = $("memory-panel");
  const mb = $("memory-badge");
  if (!m) { box.hidden = true; mb.hidden = true; return; }
  mb.hidden = false;
  mb.textContent = `memory ${m.mode}${m.available === false ? " (unavailable)" : ""}`;
  const recalled = m.recalled || [];
  box.hidden = false;
  box.replaceChildren(...present(
    el("h3", { text: "Memory (H-MEM)" }),
    el("p", { class: "note", text: m.mode === "cold" ? "Cold run: no lesson recalled or stored."
      : m.available === false ? (m.note || "Memory service unavailable: the run went on without it.")
      : "Lessons are generic paths that worked before (no company, figure or date). The critic judges each one." +
        (state.health && state.health.public_demo ? " In this public demo, memory is shared by every visitor and resets when the server restarts." : "") }),
    ...recalled.map((x) => el("div", { class: "mem-item" },
      el("div", { class: "stats" }, badge(x.memory_id), badge(x.status, x.status === "supported" ? "ok" : x.status === "rejected" ? "bad" : "muted"),
        badge(`relevance ${Number(x.relevance).toFixed(2)}`, "muted")),
      el("p", { text: x.content }),
      x.reason ? el("p", { class: "note", text: x.reason }) : null)),
    m.stored ? el("div", { class: "mem-item" },
      el("div", { class: "stats" }, badge(m.stored.stored ? "lesson stored" : "not stored", m.stored.stored ? "ok" : "muted"),
        m.stored.memory_id ? badge(m.stored.memory_id) : null),
      m.stored.content ? el("p", { text: m.stored.content }) : null,
      m.stored.reason ? el("p", { class: "note", text: m.stored.reason }) : null) : null));
}

// ---- wiring --------------------------------------------------------------------------------

$("entity-q").addEventListener("input", (e) => {
  clearTimeout(entityTimer);
  entityTimer = setTimeout(() => loadEntities(e.target.value.trim()), 250);
});
$("role").addEventListener("change", loadSuggestions);
$("custom-form").addEventListener("submit", (e) => {
  e.preventDefault();
  startRun(customRequest());
});
$("download").addEventListener("click", () => {
  if (!state.run) return;
  const blob = new Blob([state.run.lines.join("\n") + "\n"], { type: "application/x-ndjson" });
  const a = el("a", { href: URL.createObjectURL(blob), download: `trace-${state.run.id}.jsonl` });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
});

loadHealth();
loadEntities();
