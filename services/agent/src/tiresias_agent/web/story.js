// Tiresias story view: the default page. A run reads as a conversation (You ask, Tiresias proposes,
// the Sphinx tests, decisions are taken) beside a why-tree that grows and prunes, under a five-step
// progress bar. Everything is reduced from the trace lines app.js streams (GET /runs/{id}/events)
// and, at the end, from the RunResult (GET /runs/{id}); nothing is computed that the API did not
// give. The page of app.js stays as the "Debug view". Text goes in with textContent only.
"use strict";

// ---- reducer: pure, one trace line at a time (tests run this section in Node) ---------------

const STEPS = [
  { id: "understand", label: "Understand", icon: "phase-understand.svg" },
  { id: "recall", label: "Recall", icon: "phase-recall.svg" },
  { id: "drill", label: "Drill down", icon: "phase-drill.svg" },
  { id: "fetch", label: "Fetch missing data", icon: "phase-source.svg" },
  { id: "answer", label: "Answer", icon: "phase-reconcile.svg" },
];
// orchestrator phase -> step (loop_a and loop_b are wrappers: frame and recall run inside loop_a)
const PHASE_STEP = {
  resolve_entity: "understand", scope_check: "understand", register_entity: "understand", frame: "understand",
  recall: "recall", model_call: "drill", loop_b: "fetch", scout: "fetch", onboard: "fetch",
  reconcile: "answer", remember: "answer",
};
const DATA_LINE = new Set(["zetaris_adapter", "catalog"]);
const VERDICT = { accept: "accepted", prune: "pruned", frontier: "frontier", escalate: "escalated", retry: "testing" };

function storyInit(request) {
  return {
    question: request ? request.question : "",
    steps: Object.fromEntries(STEPS.map((s) => [s.id, "pending"])),
    nodes: new Map(), // node_id -> {node_id, parent_id, claim, status, reason, opinion, data, rule}
    turns: [], // {id, who: you|tiresias|sphinx|decision|data, kind, node_ids, items|text, data}
    gates: new Map(), // gate_id -> STAR card source (gate waiting line) and its outcome
    pending: { calls: 0, rows: 0, sample: 0 }, // data-layer reads not yet attached to a turn
    modelNode: null, // node a model_call phase is about (model_review lines carry no node_id)
    lines: 0,
    end_state: null,
    finished: false,
    scope: null,
  };
}

function setStep(s, id, status) {
  const order = STEPS.map((x) => x.id);
  if (s.steps[id] === "done" && status === "active") return;
  s.steps[id] = status;
  if (status === "active" || status === "done") {
    // every earlier step that started is done once a later one runs
    for (const earlier of order.slice(0, order.indexOf(id))) {
      if (s.steps[earlier] === "active") s.steps[earlier] = "done";
    }
  }
}

function takeData(s) {
  const d = s.pending;
  s.pending = { calls: 0, rows: 0, sample: 0 };
  return d.calls ? d : null;
}

function turn(s, who, kind, fields) {
  const t = { id: `t${s.turns.length}`, who, kind, node_ids: [], data: takeData(s), ...fields };
  s.turns.push(t);
  return t;
}

// The last turn of this kind about this parent, so a level's proposals or verdicts share one message.
function levelTurn(s, who, kind, parent) {
  const last = s.turns[s.turns.length - 1];
  if (last && last.who === who && last.kind === kind && last.parent === parent) {
    const d = takeData(s);
    if (d) last.data = last.data ? { calls: last.data.calls + d.calls, rows: last.data.rows + d.rows, sample: last.data.sample + d.sample } : d;
    return last;
  }
  return turn(s, who, kind, { parent, items: [] });
}

function node(s, id) {
  if (!s.nodes.has(id)) s.nodes.set(id, { node_id: id, parent_id: null, claim: "", status: "proposed" });
  return s.nodes.get(id);
}

function parentOf(s, id) {
  const n = s.nodes.get(id);
  if (n && n.parent_id) return n.parent_id;
  const cut = id.lastIndexOf(".");
  return cut > 0 ? id.slice(0, cut) : null;
}

function storyReduce(s, l) {
  s.lines += 1;
  const a = `${l.agent}/${l.action}`;
  const text = l.output_summary || "";
  if (DATA_LINE.has(l.agent) && l.sql) {
    s.pending.calls += 1;
    s.pending.rows += Number(l.rows) || 0;
    if (l.source === "sample") s.pending.sample += 1;
    return s;
  }
  if (l.agent === "orchestrator") {
    const step = PHASE_STEP[l.phase];
    if (l.action === "phase_start" && step) setStep(s, step, "active");
    if (l.action === "phase_done" && step && l.phase !== "model_call") {
      // a step is done when its last phase is: frame, recall, onboard, remember
      if (["frame", "recall", "onboard", "remember", "reconcile"].includes(l.phase) ||
        (l.phase === "scope_check" && l.outcome !== "in_scope")) setStep(s, step, "done");
    }
    if (l.action === "phase_start" && l.phase === "model_call") s.modelNode = l.node_id || null;
    if (l.action === "phase_done" && l.phase === "loop_a") setStep(s, "drill", "done");
    if (l.action === "stop" && l.end_state) s.end_state = l.end_state;
    return s;
  }
  switch (a) {
    case "scope/check":
      if (l.in_scope === false) {
        s.scope = text;
        turn(s, "tiresias", "scope", { text });
      }
      break;
    case "framer/frame": {
      const n = node(s, l.node_id || "n0");
      n.claim = text;
      n.status = "root";
      turn(s, "tiresias", "frame", { text, node_ids: [n.node_id] });
      break;
    }
    case "memory/recall":
      if ((l.applied || l.memory_ids || []).length) turn(s, "tiresias", "recall", { text });
      break;
    case "memory/off":
      s.steps.recall = "skipped";
      break;
    case "hypothesiser/propose": {
      const n = node(s, l.node_id);
      n.parent_id = l.parent_node_id || parentOf(s, l.node_id);
      n.claim = text;
      n.rule = l.rule || null;
      if (l.proposed_by === "model") n.proposed_by = "model";
      setStep(s, "drill", "active");
      const t = levelTurn(s, "tiresias", "propose", n.parent_id);
      t.items.push({ node_id: n.node_id, text });
      t.node_ids.push(n.node_id);
      break;
    }
    case "analyst/measure": {
      if (!l.node_id) break;
      const isNew = !s.nodes.has(l.node_id);
      const n = node(s, l.node_id);
      if (isNew) {
        // Loop B: the reconciliation is a new link under the frontier node
        n.parent_id = l.parent_node_id || parentOf(s, l.node_id);
        n.claim = text;
        n.status = "testing";
        const t = turn(s, "tiresias", "propose", { parent: n.parent_id, items: [{ node_id: n.node_id, text }] });
        t.node_ids.push(n.node_id);
      } else {
        n.measure = text;
        if (n.status === "proposed") n.status = "testing";
        const d = takeData(s);
        if (d) n.data = d;
      }
      break;
    }
    case "critic/retry":
    case "critic/escalate":
    case "critic/accept":
    case "critic/prune":
    case "critic/frontier": {
      if (!l.node_id) break;
      const n = node(s, l.node_id);
      n.status = VERDICT[l.action];
      n.reason = text;
      if (l.share_explained !== undefined && l.share_explained !== null) n.share_explained = l.share_explained;
      if (l.residual !== undefined && l.residual !== null) n.residual = l.residual;
      if (l.action === "retry") break; // the final verdict follows
      const t = levelTurn(s, "sphinx", "verdicts", parentOf(s, n.node_id));
      const item = t.items.find((x) => x.node_id === n.node_id);
      const v = { node_id: n.node_id, verdict: n.status, text, share_explained: n.share_explained ?? null };
      if (item) Object.assign(item, v); else t.items.push(v);
      if (!t.node_ids.includes(n.node_id)) t.node_ids.push(n.node_id);
      break;
    }
    case "critic/model_review":
      // the reply itself; second_opinion (or reject) carries the outcome for the node
      if (s.modelNode) node(s, s.modelNode).modelRoute = l.routed_model || null;
      break;
    case "critic/second_opinion":
    case "critic/reject": {
      const id = l.node_id || s.modelNode;
      if (!id) break;
      const n = node(s, id);
      const unusable = l.action === "reject";
      n.opinion = {
        routed_model: l.routed_model || n.modelRoute || null,
        agrees: unusable ? null : l.status !== "rejected" && !/disagrees/.test(text),
        unusable,
        text,
      };
      break;
    }
    case "memory/skip":
      for (const id of l.skipped || []) {
        const n = node(s, id);
        if (n.status === "proposed") { n.status = "untested"; n.reason = text; }
      }
      break;
    case "gate/waiting":
      s.gates.set(l.gate_id, { line: l, outcome: null });
      turn(s, "decision", "star", { gate_id: l.gate_id, gate: l.gate, node_ids: l.node_id ? [l.node_id] : [] });
      break;
    case "gate/held": {
      const g = s.gates.get(l.gate_id);
      if (g) g.held = true;
      break;
    }
    case "gate/budget": {  // held, and the run budget ran out: nothing applied on the user's behalf
      const g = s.gates.get(l.gate_id);
      if (g) g.outcome = { choice: null, by: "budget" };
      break;
    }
    case "gate/answered":
    case "gate/expired": {
      const g = s.gates.get(l.gate_id);
      if (g) g.outcome = { choice: l.choice || l.proposal, by: l.action === "answered" ? answeredBy(l.note) : "timeout" };
      break;
    }
    case "gate/decide": {
      const waited = [...s.gates.values()].some((g) => g.line.gate === l.gate && g.line.node_id === l.node_id && !g.closed);
      if (waited) {
        for (const g of s.gates.values()) if (g.line.gate === l.gate && g.line.node_id === l.node_id) g.closed = true;
        break;
      }
      turn(s, "decision", "auto", { gate: l.gate, choice: l.proposal || l.gate_choice || "", decided_by: l.decided_by || "", text, node_ids: l.node_id ? [l.node_id] : [] });
      break;
    }
    case "gap_analyst/information_need":
      turn(s, "tiresias", "need", { text, node_ids: l.node_id ? [l.node_id] : [] });
      break;
    case "source_scout/resolve":
      setStep(s, "fetch", "active");
      turn(s, "tiresias", "found", { text, node_ids: l.node_id ? [l.node_id] : [] });
      break;
    case "onboarder/register":
      if (l.status === "success") {
        turn(s, "tiresias", "registered", { text, registration: l.registration || null, node_ids: l.node_id ? [l.node_id] : [] });
      }
      break;
    case "memory/remember":
      if (l.status === "success") turn(s, "tiresias", "lesson", { text });
      break;
    case "reporter/report":
    case "reporter/reword":
      setStep(s, "answer", "done");
      if (a === "reporter/report") turn(s, "tiresias", "answer", { text, node_ids: l.node_id ? [l.node_id] : [] });
      else {
        const last = [...s.turns].reverse().find((t) => t.kind === "answer");
        if (last) last.reworded = text;
      }
      break;
    default:
      break;
  }
  return s;
}

// The RunResult is the authority at the end: statuses from its why_tree, steps never reached skipped.
function storyFinish(s, result) {
  s.finished = true;
  for (const st of STEPS) {
    if (s.steps[st.id] === "pending") s.steps[st.id] = "skipped";
    else if (s.steps[st.id] === "active") s.steps[st.id] = "done";
  }
  if (result) {
    s.end_state = result.end_state || s.end_state;
    for (const n of result.why_tree || []) {
      const m = node(s, n.node_id);
      m.parent_id = n.parent_id || null;
      m.claim = n.claim || m.claim;
      m.status = n.verdict === "root" ? "root" : (VERDICT[n.verdict] || n.verdict || m.status);
      m.reason = n.reason || m.reason || null;
      m.share_explained = n.share_explained ?? m.share_explained ?? null;
      m.residual = n.residual ?? m.residual ?? null;
      m.evidence = n.evidence || [];
      m.label = n.label || null;
      if (n.model_opinion) {
        m.opinion = { routed_model: n.model_opinion.routed_model || null, agrees: n.model_opinion.agrees ?? null,
          unusable: false, text: n.model_opinion.rationale || "", receipt_id: n.model_opinion.receipt_id || null };
      }
    }
  }
  return s;
}

// What a waiting gate asks, as Situation / Task / Action / expected Result, in plain words. The
// option ids stay the API's own (sent back as the choice); only their labels are worded here.
const GATE_WORDS = {
  critic_escalation: {
    ask: "Should we look for the missing data?",
    task: "Its size cannot be measured with the data loaded. Look for the missing data, or drop this cause?",
    options: {
      frontier: ["Look for the missing data", "Tiresias hands it to Loop B to find the filings that hold the figure"],
      prune: ["Drop this cause", "the cause is ruled out and the others carry on"],
    },
  },
  source: {
    ask: "Should we load these filings as a new source?",
    task: "The figure is in another registrant's filings. Load them as a new source?",
    options: {
      register: ["Load these filings", "one new source is registered (in Zetaris when live) and the cause is re-tested"],
      deny: ["Don't load them", "the answer stops at the evidence frontier and says where to look"],
    },
  },
  memory: {
    ask: "Should we remember this path for similar questions?",
    task: "Keep the path that worked as a lesson for similar questions?",
    options: {
      store: ["Remember this path", "a generic lesson (no company or figure) is stored in H-MEM"],
      discard: ["Don't remember it", "nothing is stored"],
    },
  },
  dissent: {
    ask: "Should the rules' verdict stand over Nemotron's?",
    task: "Nemotron disagrees with the rules' verdict. Which one stands?",
    options: {
      "keep-rule": ["Keep the rules' verdict", "the rule verdict stands; the model's view stays in the trace"],
      prune: ["Follow Nemotron: prune", "the link is ruled out"],
    },
  },
};

function starCard(line) {
  const words = GATE_WORDS[line.gate] || { task: line.question, options: {} };
  const ctx = line.context || {};
  // the claim is already on the tree node; show the other short facts
  const facts = Object.entries(ctx).filter(([k, v]) => k !== "claim" && v !== null && v !== undefined && typeof v !== "object")
    .slice(0, 3).map(([k, v]) => `${k}: ${v}`);
  return {
    gate: line.gate,
    gate_id: line.gate_id,
    node_id: line.node_id || null,
    situation: line.question,
    facts,
    task: words.task,
    actions: (line.options || []).map((id) => {
      const w = words.options[id] || [id, ""];
      return { id, label: w[0], result: w[1], recommended: id === line.proposal };
    }),
    deadline: line.deadline || null,
  };
}

// Plain words for an automatic decision ("Tiresias decided (policy): ...").
const AUTO_WORDS = {
  critic_escalation: { frontier: "look for the missing data (Loop B)", prune: "drop the cause" },
  source: { register: "load the filings as a new source", deny: "not load the filings" },
  memory: { store: "remember this path", discard: "not remember it" },
  dissent: { "keep-rule": "keep the rules' verdict", prune: "follow Nemotron and prune" },
};
function autoDecision(t) {
  const w = (AUTO_WORDS[t.gate] || {})[t.choice];
  return w || t.choice;
}

// ---- prompt gates: in "Pause at each decision" mode the Sphinx asks on the user's behalf and
// proceeds with the recommendation after PROMPT_SECONDS unless the user takes over. The UI answers
// through POST /runs/{id}/gates/{gate_id}; the countdown's answer carries auto: true, so the API
// records it as decided_by "ui-auto", never "human"; the note says which kind it was (answeredBy).
const PROMPT_SECONDS = 5;
const NOTE_AUTO = `proceeded automatically after ${PROMPT_SECONDS} s (UI)`;
const NOTE_PROCEED = "proceed (UI)";

function answeredBy(note) {
  if (note === NOTE_AUTO) return "auto";
  if (note === NOTE_PROCEED) return "proceed";
  return "you";
}

const OUTCOME_WORDS = {
  you: "You chose",
  proceed: "You proceeded with the recommendation",
  auto: `Proceeded automatically after ${PROMPT_SECONDS} s`,
  timeout: "No answer in time; the recommendation applied",
  budget: "Paused until the run budget ran out; nothing was applied on your behalf",
};

function outcomeText(outcome, c) {
  if (!outcome) return "decided";
  const words = OUTCOME_WORDS[outcome.by] || "Decided";
  if (outcome.choice === null) return words;
  return `${words}: ${(c.actions.find((x) => x.id === outcome.choice) || { label: outcome.choice }).label}`;
}

function sphinxAsks(line) {
  const w = GATE_WORDS[line.gate];
  return (w && w.ask) || line.question || "Proceed with the recommendation?";
}

// One countdown per waiting gate. deps: { post(gateId, body) -> Promise, hold(gateId) -> Promise,
// setTimer(fn, ms) -> id,
// clearTimer(id), onChange() }. States: counting -> posting -> answered | conflict | error, or
// counting -> holding -> takeover (POST .../hold: the gate waits for the user, no countdown, only
// the run budget bounds it; Take over is the user's call, full stop) | conflict | error.
function promptGate(line, deps) {
  const p = { gate_id: line.gate_id, choice: line.proposal, state: "counting", left: PROMPT_SECONDS, message: "" };
  let timer = null;
  const tick = () => {
    if (p.state !== "counting") return;
    p.left -= 1;
    if (p.left <= 0) { send(NOTE_AUTO); return; }
    deps.onChange();
    timer = deps.setTimer(tick, 1000);
  };
  const send = (note) => {
    if (p.state !== "counting") return;
    if (timer !== null) deps.clearTimer(timer);
    timer = null;
    p.state = "posting";
    deps.onChange();
    const body = note === NOTE_AUTO ? { choice: p.choice, note, auto: true } : { choice: p.choice, note };
    return Promise.resolve(deps.post(p.gate_id, body)).then(() => {
      p.state = "answered";
      deps.onChange();
    }, (e) => {
      const msg = String((e && e.message) || e);
      // 409: the gate was already answered or expired; the trace line says what applied
      p.state = /^Gate(AlreadyAnswered|Expired)\b/.test(msg) ? "conflict" : "error";
      p.message = msg;
      deps.onChange();
    });
  };
  p.proceed = () => send(NOTE_PROCEED);
  p.takeOver = () => {
    if (p.state !== "counting") return;
    if (timer !== null) deps.clearTimer(timer);
    timer = null;
    p.state = "holding";
    deps.onChange();
    return Promise.resolve(deps.hold(p.gate_id)).then(() => {
      p.state = "takeover";
      deps.onChange();
    }, (e) => {
      const msg = String((e && e.message) || e);
      p.state = /^Gate(AlreadyAnswered|Expired)\b/.test(msg) ? "conflict" : "error";
      p.message = msg;
      deps.onChange();
    });
  };
  p.cancel = () => { if (timer !== null) deps.clearTimer(timer); timer = null; if (p.state === "counting") p.state = "cancelled"; };
  timer = deps.setTimer(tick, 1000);
  return p;
}

// The waiting gates that still need a prompt (none in auto mode: the API only sends gate/waiting
// lines for interactive runs).
function gatesToPrompt(s, prompts) {
  return [...s.gates.values()].filter((g) => !g.outcome && !g.closed && !prompts.has(g.line.gate_id)).map((g) => g.line);
}

function dataText(d) {
  if (!d) return "";
  const where = d.sample === d.calls ? "Sample data" : d.sample ? "Zetaris + sample data" : "Zetaris";
  const what = d.sample === d.calls ? (d.calls === 1 ? "read" : "reads") : (d.calls === 1 ? "query" : "queries");
  return `${where} · ${d.calls} ${what} · ${d.rows.toLocaleString()} rows`;
}

// ---- render (browser only) --------------------------------------------------------------------

const EXAMPLES = [
  {
    label: "3M · FY2023 revenue: $32.68B → $24.61B. Why?",
    request: {
      question: "Why did 3M's reported 2023 revenue fall between its 2024 and 2025 annual reports?",
      entity: { ticker: "MMM" }, metric: "Revenues", period: { start: "2023-01-01", end: "2023-12-31" },
      compare: { filing_a: "10-K filed 2024-02-07", filing_b: "10-K filed 2025-02-05" },
      asker: { role: "analyst", levers: ["model adjustments", "estimates", "rating"], horizon: "next quarter" },
      options: { hitl_mode: "auto", memory: "warm" },
    },
  },
  {
    label: "GE · FY2022 revenue fell between two 10-Ks. Why?",
    request: {
      question: "Why did GE's reported 2022 revenue fall between its 2023 and 2024 annual reports?",
      entity: { ticker: "GE", cik: "0000040545" }, metric: "Revenues", period: { start: "2022-01-01", end: "2022-12-31" },
      compare: { filing_a: "10-K filed 2023-02-10", filing_b: "10-K filed 2024-02-02" },
      asker: { role: "analyst", levers: ["model adjustments", "estimates", "rating"], horizon: "next quarter" },
      options: { hitl_mode: "auto", memory: "warm" },
    },
  },
  {
    label: "GE · a $22B net loss in 2018. Why?",
    request: {
      question: "Why did GE report a net loss of about $22 billion in 2018?",
      entity: { ticker: "GE", cik: "0000040545" }, metric: "NetIncomeLoss", period: { start: "2018-01-01", end: "2018-12-31" },
      compare: { baseline: "2017 and prior-year 10-K values" },
      asker: { role: "board", levers: ["portfolio strategy", "capital allocation"], horizon: "3 years" },
      options: { hitl_mode: "auto", memory: "warm" },
    },
  },
];

const story = { s: null, req: null, runId: null, result: null, lines: [], selected: null, hover: null,
  open: new Set(), scrolled: new Set(), raf: 0, view: "story", consoleTab: "trace", consoleFilter: null,
  prompts: new Map() };

const S = (id) => document.getElementById(id);
const sEl = (...a) => el(...a); // app.js's el(): textContent only

function storyStart(req) {
  for (const p of story.prompts.values()) p.cancel();
  story.prompts = new Map();
  story.s = storyInit(req);
  story.req = req;
  story.runId = null;
  story.error = null;
  story.sponsors = null;
  story.result = null;
  story.lines = [];
  story.selected = null;
  story.open = new Set();
  story.scrolled = new Set();
  S("s-empty").hidden = true;
  S("s-run").hidden = false;
  S("s-result").hidden = true;
  S("s-console").hidden = false;
  scheduleRender();
}

function storySetRun(started) {
  story.runId = started.run_id;
}

function storyLine(line) {
  if (!story.s) return;
  story.lines.push(line);
  storyReduce(story.s, line);
  for (const g of gatesToPrompt(story.s, story.prompts)) {
    story.prompts.set(g.gate_id, promptGate(g, {
      post: (gateId, body) => api(`/runs/${encodeURIComponent(story.runId)}/gates/${encodeURIComponent(gateId)}`,
        { method: "POST", body: JSON.stringify(body) }),
      hold: (gateId) => api(`/runs/${encodeURIComponent(story.runId)}/gates/${encodeURIComponent(gateId)}/hold`,
        { method: "POST", body: JSON.stringify({ note: "taken over (UI)" }) }),
      setTimer: (fn, ms) => setTimeout(fn, ms),
      clearTimer: (id) => clearTimeout(id),
      onChange: scheduleRender,
    }));
  }
  scheduleRender();
}

function storyDone(rec, sponsors) {
  if (!story.s) return;
  story.result = rec && rec.result ? rec.result : null;
  story.sponsors = sponsors || null;
  storyFinish(story.s, story.result);
  scheduleRender();
}

function storyError(message) {
  if (!story.s) return;
  storyFinish(story.s, null);
  story.s.finished = true;
  story.error = message;
  scheduleRender();
}

function scheduleRender() {
  if (story.raf) return;
  story.raf = requestAnimationFrame(() => { story.raf = 0; renderStory(); });
}

function renderStory() {
  const s = story.s;
  if (!s) return;
  S("s-question").textContent = s.question;
  S("s-run").classList.toggle("finished", s.finished); // the tree takes the width, the story goes below
  renderSteps(s);
  renderTreePane(s);
  renderTurns(s);
  renderResultTop(s);
  renderConsole();
  // bring a decision that waits for the user into view, once
  for (const card of document.querySelectorAll("#story .star:not(.done)[data-gate]")) {
    if (story.scrolled.has(card.dataset.gate)) continue;
    story.scrolled.add(card.dataset.gate);
    card.scrollIntoView({ block: "center", behavior: "smooth" });
  }
}

function renderSteps(s) {
  S("s-steps").replaceChildren(...STEPS.map((st) => {
    const status = s.steps[st.id];
    return sEl("li", { class: `step ${status}`, "aria-current": status === "active" ? "step" : null },
      sEl("img", { src: st.icon, alt: "", width: "20", height: "20" }),
      sEl("span", { class: "step-label", text: st.label }),
      sEl("span", { class: "step-state", text: status === "pending" ? "" : status }));
  }));
}

const STATUS_WORD = { root: "the change", accepted: "accepted", pruned: "ruled out", frontier: "needs more data",
  escalated: "can't size it yet", testing: "testing…", proposed: "proposed", untested: "not tested" };

function chipsFor(n) {
  const chips = [badge(STATUS_WORD[n.status] || n.status, n.status === "accepted" || n.status === "root" ? "ok"
    : n.status === "pruned" || n.status === "untested" ? "muted" : "warn")];
  if (n.share_explained !== null && n.share_explained !== undefined) chips.push(badge(`explains ${pct(n.share_explained)}`));
  if (n.opinion && n.opinion.routed_model) {
    chips.push(badge(n.opinion.unusable ? `${shortModel(n.opinion.routed_model)}: reply unusable`
      : `${shortModel(n.opinion.routed_model)}: ${n.opinion.agrees ? "agrees" : "disagrees"}`, n.opinion.agrees === false ? "warn" : "model-op"));
  }
  return chips;
}

const shortModel = (id) => `Nemotron ${(id || "").replace(/^nvidia\/nemotron-/, "").replace(/-a\d+b$/, "").replace(/-/g, " ")}`;

function evidenceChip(n) {
  const evs = n.evidence || [];
  if (evs.length) {
    const rows = evs.reduce((k, e) => k + (e.row_count ?? (e.rows || []).length), 0);
    const sample = evs.every((e) => e.source === "sample");
    return sEl("button", { type: "button", class: "ev-chip", onclick: (e) => { e.stopPropagation(); toggleOpen(`ev-${n.node_id}`); } },
      `${rows.toLocaleString()} rows · ${sample ? "sample data" : "Zetaris"} ${story.open.has(`ev-${n.node_id}`) ? "▾" : "▸"}`);
  }
  if (n.data) return sEl("span", { class: "ev-chip static", text: dataText(n.data) });
  return null;
}

function renderTreePane(s) {
  const nodes = [...s.nodes.values()];
  const box = S("s-tree");
  if (!nodes.length) { box.replaceChildren(sEl("p", { class: "note", text: "The why-tree grows here." })); return; }
  const kids = new Map();
  for (const n of nodes) {
    const k = n.parent_id && s.nodes.has(n.parent_id) ? n.parent_id : "";
    if (!kids.has(k)) kids.set(k, []);
    kids.get(k).push(n);
  }
  const onPath = new Set(acceptedPath(nodes.map((n) => ({ node_id: n.node_id, parent_id: n.parent_id,
    verdict: n.status === "accepted" ? "accepted" : n.status }))));
  const build = (pid) => {
    const list = kids.get(pid) || [];
    const live = list.filter((n) => n.status !== "pruned" && n.status !== "untested");
    const out = list.filter((n) => n.status === "pruned" || n.status === "untested");
    const items = live.map((n) => nodeItem(n, onPath, build));
    if (out.length) {
      const key = `out-${pid || "root"}`;
      const open = story.open.has(key);
      items.push(sEl("li", { class: "ruled" },
        sEl("button", { type: "button", class: "linkish", onclick: () => toggleOpen(key) }, `${out.length} ruled out ${open ? "▾" : "▸"}`),
        open ? sEl("ol", {}, out.map((n) => nodeItem(n, onPath, build))) : null));
    }
    return items;
  };
  box.replaceChildren(sEl("ol", { class: "s-tree" }, build("")));
}

function nodeItem(n, onPath, build) {
  const sub = build(n.node_id);
  const cls = ["s-node", n.status, onPath.has(n.node_id) ? "on-path" : "", story.hover === n.node_id ? "hl" : "",
    story.selected === n.node_id ? "sel" : ""].filter(Boolean).join(" ");
  const openEv = story.open.has(`ev-${n.node_id}`);
  return sEl("li", { class: cls, "data-node": n.node_id },
    sEl("div", { class: "s-node-card", role: "button", tabindex: "0",
      onmouseenter: () => setHover(n.node_id), onmouseleave: () => setHover(null),
      onclick: () => selectStoryNode(n.node_id),
      onkeydown: (e) => { if (e.key === "Enter") selectStoryNode(n.node_id); } },
    sEl("div", { class: "claim", text: n.claim || n.node_id }),
    sEl("div", { class: "chips" }, present(chipsFor(n), evidenceChip(n)))),
    openEv ? sEl("div", { class: "ev-inline" }, (n.evidence || []).map((e) => evidenceItem(e))) : null,
    sub.length ? sEl("ol", {}, sub) : null);
}

function setHover(id) {
  if (story.hover === id) return;
  story.hover = id;
  for (const x of document.querySelectorAll("#story [data-node]")) {
    const ids = (x.dataset.node || "").split(" ");
    x.classList.toggle("hl", Boolean(id) && ids.includes(id));
  }
}

function toggleOpen(key) {
  if (story.open.has(key)) story.open.delete(key); else story.open.add(key);
  scheduleRender();
}

function selectStoryNode(id) {
  story.selected = id;
  story.consoleTab = "evidence";
  openConsole(true);
  scheduleRender();
}

const WHO = {
  you: { name: "You", icon: "you-portrait.png" },
  tiresias: { name: "Tiresias", icon: "tiresias-portrait.png" },
  sphinx: { name: "The Sphinx", icon: "sphinx-portrait.png" },
  decision: { name: "Decision", icon: "you-portrait.png" },
};

function renderTurns(s) {
  const list = [sEl("li", { class: "msg you" }, avatar("you"), sEl("div", { class: "bubble" },
    sEl("span", { class: "who", text: "You" }), sEl("p", { text: s.question })))];
  for (const t of s.turns) list.push(turnItem(s, t));
  if (!s.finished) list.push(sEl("li", { class: "msg typing" }, avatar("tiresias"), sEl("div", { class: "bubble", text: "…" })));
  if (story.error) list.push(sEl("li", { class: "msg error" }, sEl("div", { class: "bubble", text: `The run did not finish: ${story.error}` })));
  const ol = S("s-turns");
  const atBottom = ol.scrollHeight - ol.scrollTop - ol.clientHeight < 60;
  ol.replaceChildren(...list);
  if (atBottom && !s.finished) ol.scrollTop = ol.scrollHeight;
  S("s-convo").open = !s.finished || story.open.has("convo");
}

function avatar(who) {
  return sEl("img", { class: "avatar", src: WHO[who].icon, alt: "" });
}

function turnItem(s, t) {
  const pr = t.kind === "star" ? story.prompts.get(t.gate_id) : null;
  const g0 = t.kind === "star" ? s.gates.get(t.gate_id) : null;
  const asking = pr && pr.state !== "takeover" && !(g0 && (g0.outcome || g0.closed));
  const who = asking ? "sphinx" : t.who === "decision" ? "decision" : t.who;
  const body = [sEl("span", { class: "who", text: WHO[who].name })];
  if (t.kind === "propose") {
    body.push(sEl("p", { text: t.items.length > 1 ? `Why? ${t.items.length} possible causes to test:` : "Why? One cause to test:" }));
    body.push(sEl("ol", { class: "causes" }, t.items.map((x) => sEl("li", { text: x.text }))));
  } else if (t.kind === "verdicts") {
    body.push(sEl("ul", { class: "verdicts" }, t.items.map((x) => {
      const n = s.nodes.get(x.node_id) || {};
      const op = n.opinion && n.opinion.routed_model && !n.opinion.unusable
        ? sEl("div", { class: "opinion-line", text: `${shortModel(n.opinion.routed_model)} (via Nightshift): ${n.opinion.agrees ? "agrees" : "disagrees"}` }) : null;
      return sEl("li", { class: `v ${x.verdict}` },
        sEl("b", { text: `${STATUS_WORD[x.verdict] || x.verdict}: ` }), x.text, op);
    })));
  } else if (t.kind === "star") {
    body.push(starItem(s, t));
  } else if (t.kind === "auto") {
    const key = `why-${t.id}`;
    body.push(sEl("p", {}, `Tiresias decided (${t.decided_by.startsWith("policy") ? "policy" : t.decided_by || "policy"}): ${autoDecision(t)} `,
      sEl("button", { type: "button", class: "linkish", onclick: () => toggleOpen(key) }, story.open.has(key) ? "why ▾" : "why ▸")));
    if (story.open.has(key)) body.push(sEl("p", { class: "note", text: t.text }));
  } else if (t.kind === "answer") {
    body.push(sEl("p", { class: "answer-text", text: t.reworded || t.text.replace(/^[a-z-]+: /, "") }));
  } else {
    const lead = { frame: "", recall: "From memory: ", need: "", found: "Found it: ", registered: "", lesson: "Lesson stored: ", scope: "Out of scope: " }[t.kind] || "";
    body.push(sEl("p", { text: lead + (t.text || "") }));
  }
  if (t.data) {
    body.push(sEl("button", { type: "button", class: "data-line", onclick: () => { story.consoleTab = "exchange"; openConsole(true); scheduleRender(); } },
      `${dataText(t.data)} ▸`));
  }
  return sEl("li", { class: `msg ${t.who} k-${t.kind}`, "data-node": t.node_ids.join(" ") || null,
    onmouseenter: () => t.node_ids[0] && setHover(t.node_ids[0]), onmouseleave: () => setHover(null) },
  avatar(who), sEl("div", { class: "bubble" }, body));
}

function starItem(s, t) {
  const g = s.gates.get(t.gate_id);
  if (!g) return null;
  const c = starCard(g.line);
  const done = g.outcome || g.closed;
  const pr = story.prompts.get(t.gate_id);
  if (!done && pr && pr.state !== "takeover") return sphinxPrompt(g.line, c, pr);
  const held = g.held || (pr && pr.state === "takeover");
  const box = sEl("div", { class: `star${done ? " done" : ""}`, "data-gate": t.gate_id },
    sEl("dl", { class: "star-dl" },
      sEl("dt", { text: "Situation" }), sEl("dd", {}, c.situation, c.facts.length ? sEl("span", { class: "note", text: ` (${c.facts.join(" · ")})` }) : null),
      sEl("dt", { text: "Task" }), sEl("dd", { text: c.task }),
      sEl("dt", { text: "Action" }), sEl("dd", {}, done
        ? sEl("span", { text: outcomeText(g.outcome, c) })
        : sEl("div", { class: "star-actions" }, c.actions.map((x) => sEl("button", {
          type: "button", class: `btn small${x.recommended ? (held ? " nudge" : "") : " alt"}`,
          onclick: (ev) => answerGate(t.gate_id, x.id, ev.currentTarget) },
        sEl("span", { text: x.label + (x.recommended ? " (recommended)" : "") }), sEl("small", { class: "choice-id", text: x.id }))))),
      sEl("dt", { text: "Result" }), sEl("dd", {}, sEl("ul", { class: "results" }, c.actions.map((x) => sEl("li", {}, sEl("b", { text: `${x.label}: ` }), x.result))))),
    !done && held ? sEl("p", { class: "note paused", text: "Paused · your call. Nothing proceeds until you choose; the recommended option is highlighted." }) : null,
    !done && !held && c.deadline ? sEl("p", { class: "note countdown", "data-deadline": c.deadline, text: countdownText(c.deadline) }) : null);
  return box;
}

// The Sphinx asks on the user's behalf: the question, the recommendation, a 5 s countdown,
// Proceed (now) and Take over (the STAR card).
function sphinxPrompt(line, c, pr) {
  const rec = c.actions.find((x) => x.recommended) || { label: line.proposal, result: "" };
  const busy = pr.state !== "counting";
  return sEl("div", { class: "sphinx-ask", "data-gate": line.gate_id },
    sEl("p", { class: "ask-q" }, sEl("span", { class: "on-behalf", text: "On your behalf: " }), sphinxAsks(line)),
    sEl("p", { class: "note" }, `Recommended: ${rec.label}`, rec.result ? ` (${rec.result}).` : "."),
    sEl("div", { class: "star-actions" },
      sEl("button", { type: "button", class: "btn small", disabled: busy || null, onclick: () => pr.proceed() },
        sEl("span", { text: "Proceed" }), sEl("small", { class: "choice-id", text: line.proposal })),
      sEl("button", { type: "button", class: "btn small alt", disabled: busy || null, onclick: () => { pr.takeOver(); scheduleRender(); } }, "Take over")),
    sEl("p", { class: "note countdown-prompt", text: promptText(pr) }));
}

function promptText(pr) {
  if (pr.state === "counting") return `Proceeding in ${pr.left} s…`;
  if (pr.state === "posting") return "Proceeding…";
  if (pr.state === "holding") return "Taking over…";
  if (pr.state === "conflict") return `Already decided: ${pr.message.replace(/^Gate\w+: /, "")}`;
  if (pr.state === "error") return `Could not answer: ${pr.message}`;
  return "";
}

function countdownText(deadline) {
  const left = Math.max(0, Math.round((new Date(deadline).getTime() - Date.now()) / 1000));
  return `${left} s left, then the recommendation applies.`;
}

async function answerGate(gateId, choice, btn) {
  for (const b of btn.parentElement.querySelectorAll("button")) b.disabled = true;
  try {
    await api(`/runs/${encodeURIComponent(story.runId)}/gates/${encodeURIComponent(gateId)}`, { method: "POST", body: JSON.stringify({ choice }) });
  } catch (e) {
    for (const b of btn.parentElement.querySelectorAll("button")) b.disabled = false;
    btn.parentElement.append(sEl("p", { class: "note", text: e.message }));
  }
}

setInterval(() => {
  for (const x of document.querySelectorAll("#story .countdown[data-deadline]")) x.textContent = countdownText(x.dataset.deadline);
}, 1000);

function renderResultTop(s) {
  const box = S("s-result");
  const r = story.result;
  if (!s.finished || !r) { box.hidden = true; return; }
  box.hidden = false;
  const e = r.effect;
  const leaf = (r.why_tree || []).find((n) => n.node_id === acceptedPath(r.why_tree || []).slice(-1)[0]);
  box.replaceChildren(...present(
    sEl("div", { class: "s-verdict" }, badge(r.end_state || r.status, endStateClass(r.end_state)),
      r.sample_mode ? badge("sample data", "sample") : badge("live", "live"),
      e ? sEl("span", { class: "effect-line", text: `${e.entity} ${e.metric}: ${money(e.baseline && e.baseline.value)} → ${money(e.target && e.target.value)} (${money(e.delta)})` }) : null),
    r.action ? sEl("p", { class: "s-action", text: r.action }) : null,
    r.scope ? scopeCard(r.scope) : null,
    r.scope ? null : sEl("div", { class: "s-numbers" },
      number("Share explained", leaf && leaf.share_explained !== null && leaf.share_explained !== undefined ? pct(leaf.share_explained) : "–"),
      number("Residual", r.residual !== null && r.residual !== undefined ? residualText(r.residual) : "–"),
      number("New sources", r.stats ? String(r.stats.sources_onboarded) : "–")),
    proofStrip()));
}

function number(label, value) {
  return sEl("div", { class: "num" }, sEl("span", { class: "num-label", text: label }), sEl("span", { class: "num-value", text: value }));
}

function proofStrip() {
  const sp = story.sponsors || sponsorSummary(story.lines, story.result && story.result.memory);
  const z = sp.zetaris, d = sp.sample, n = sp.nightshift, h = sp.hmem;
  const models = [...n.models].map(([id, k]) => `${shortModel(id)} ×${k}`).join(", ");
  const sample = story.result ? story.result.sample_mode : (story.lines[0] || {}).mode === "sample";
  const chip = (text, filter, cls) => sEl("button", { type: "button", class: `proof ${cls}`,
    onclick: () => { story.consoleTab = "exchange"; story.consoleFilter = filter; openConsole(true); scheduleRender(); } }, text);
  return sEl("div", { class: "proofs", "aria-label": "Proof of work, per sponsor" },
    z.sql_calls ? chip(`Zetaris · ${plural(z.sql_calls, "SQL call")} · ${z.rows.toLocaleString()} rows`, "zetaris", "")
      : chip(`Sample data · ${plural(d.sql_calls, "read")} · ${d.rows.toLocaleString()} rows (no Zetaris call)`, "zetaris", "sample"),
    n.calls ? chip(`Nightshift · ${models}`, "nightshift", "model")
      : chip(sample ? "Nightshift · models off in sample mode (run live at depth thorough)" : "Nightshift · no model call at this depth", "nightshift", "muted"),
    chip(h ? (h.available ? `H-MEM · ${h.recalled} recalled · ${h.stored} stored` : "H-MEM · unavailable") : "H-MEM · –", "hmem", h && h.available ? "" : "muted"));
}

// ---- console drawer: trace, exchange, evidence, memory ----------------------------------------

function openConsole(open) {
  S("s-console").classList.toggle("open", open);
  S("s-console-toggle").setAttribute("aria-expanded", open ? "true" : "false");
}

// Which sponsor a call went to (a sample read is filed under Zetaris's tab but labelled as offline data).
const SPONSOR_OF = (l) => (DATA_LINE.has(l.agent) && l.sql ? "zetaris"
  : isModelCall(l) ? "nightshift" : l.agent === "memory_client" || l.agent === "Memory" ? "hmem" : null);

function renderConsole() {
  for (const b of document.querySelectorAll("#s-console [data-tab]")) b.setAttribute("aria-pressed", b.dataset.tab === story.consoleTab ? "true" : "false");
  const body = S("s-console-body");
  if (story.consoleTab === "trace") {
    const all = S("s-trace-all").checked;
    const shown = story.lines.filter((l) => all || SHOWN_BY_DEFAULT.has(tier(l)));
    body.replaceChildren(sEl("p", { class: "note", text: `${story.lines.length} lines, ${shown.length} shown` }),
      sEl("ol", { class: "timeline" }, shown.map((l) => renderLine(l))));
  } else if (story.consoleTab === "exchange") {
    const f = story.consoleFilter;
    const calls = story.lines.filter((l) => SPONSOR_OF(l) && (!f || SPONSOR_OF(l) === f));
    body.replaceChildren(
      sEl("div", { class: "filters" }, ["all", "zetaris", "nightshift", "hmem"].map((k) => sEl("button", { type: "button",
        class: "btn small ghost", "aria-pressed": (f || "all") === k ? "true" : "false",
        onclick: () => { story.consoleFilter = k === "all" ? null : k; scheduleRender(); } }, { all: "All", zetaris: "Data (Zetaris / sample)", nightshift: "Nightshift", hmem: "H-MEM" }[k]))),
      calls.length ? sEl("table", { class: "rows exchange" },
        sEl("thead", {}, sEl("tr", {}, ["from → to", "call", "request", "response", "ms", "id"].map((c) => sEl("th", { text: c })))),
        sEl("tbody", {}, calls.map(exchangeRow))) : sEl("p", { class: "note", text: "No call of this kind in this run." }));
  } else if (story.consoleTab === "evidence") {
    const n = story.selected && story.s ? story.s.nodes.get(story.selected) : null;
    body.replaceChildren(...present(n ? [sEl("h3", { text: n.claim || n.node_id }),
      n.reason ? sEl("p", { class: "note", text: n.reason }) : null,
      ...(n.evidence || []).map((e) => evidenceItem(e)),
      !(n.evidence || []).length ? sEl("p", { class: "note", text: story.s.finished ? "No query behind this link: it is a hypothesis the critic did not accept." : "Evidence arrives when the run ends." }) : null,
      n.opinion ? sEl("div", { class: "opinion" }, sEl("strong", { text: `${shortModel(n.opinion.routed_model)} via Nightshift ` }),
        sEl("span", { class: "note", text: n.opinion.receipt_id ? `receipt ${n.opinion.receipt_id}` : "" }), sEl("p", { text: n.opinion.text })) : null]
      : sEl("p", { class: "note", text: "Click a link of the why-tree to see the SQL and rows it rests on." })));
  } else if (story.consoleTab === "memory") {
    const m = story.result && story.result.memory;
    body.replaceChildren(...present(!m ? sEl("p", { class: "note", text: "Memory shows when the run ends." }) : [
      sEl("p", { class: "note", text: m.available === false ? (m.note || "Memory service unavailable.") : `Mode ${m.mode}. Lessons are generic paths (no company, figure or date); the critic judges each one.` }),
      ...(m.recalled || []).map((x) => sEl("div", { class: "mem-item" }, badge(x.memory_id), badge(x.status, x.status === "supported" ? "ok" : "muted"), sEl("p", { text: x.content }))),
      m.stored ? sEl("div", { class: "mem-item" }, badge(m.stored.stored ? "lesson stored" : "not stored", m.stored.stored ? "ok" : "muted"), m.stored.content ? sEl("p", { text: m.stored.content }) : null) : null]));
  }
}

function exchangeRow(l) {
  const sp = SPONSOR_OF(l);
  const to = sp === "zetaris" ? (l.source === "sample" ? "sample data (offline)" : "Zetaris") : sp === "nightshift" ? "Nightshift" : "H-MEM";
  const req = [l.method, l.endpoint].filter(Boolean).join(" ") || (l.sql ? `SQL ${l.action}` : l.model || l.requested_model || l.action);
  const res = [l.http_status ? `HTTP ${l.http_status}` : null, l.rows !== undefined ? `${l.rows} rows` : null,
    l.routed_model ? l.routed_model : null, l.response_bytes ? `${l.response_bytes.toLocaleString()} B` : null,
    l.status && l.status !== "success" ? l.status : null].filter(Boolean).join(" · ");
  const row = sEl("tr", {},
    sEl("td", { text: `${l.agent} → ${to}` }), sEl("td", { text: l.action }),
    sEl("td", {}, req, l.sql ? sEl("details", {}, sEl("summary", { text: "SQL" }), sEl("pre", { text: l.sql })) : null,
      l.request_bytes ? sEl("span", { class: "note", text: ` ${l.request_bytes.toLocaleString()} B` }) : null),
    sEl("td", { text: res }), sEl("td", { text: String(l.latency_ms ?? l.duration_ms ?? "") }),
    sEl("td", { text: l.receipt_id || l.run_id || "" }));
  return row;
}

// ---- ask: example bubbles, search, suggestion cards --------------------------------------------

function renderExamples() {
  S("s-examples").replaceChildren(...EXAMPLES.map((x) => sEl("button", { type: "button", class: "bubble-btn",
    onclick: () => startRun(x.request) }, x.label)));
}

let sTimer = null;
async function storySearch(q) {
  const box = S("s-companies");
  if (!q) { box.replaceChildren(); S("s-suggestions").replaceChildren(); return; }
  try {
    const body = await api(`/entities?limit=6&q=${encodeURIComponent(q)}`);
    box.replaceChildren(...body.entities.map((e) => sEl("button", { type: "button", class: "chip-btn", onclick: () => storySuggest(e) },
      `${e.ticker || ""} ${e.name}`.trim())));
    if (!body.entities.length) box.replaceChildren(sEl("p", { class: "note", text: "No company found." }));
  } catch (e) {
    box.replaceChildren(sEl("p", { class: "note", text: e.message }));
  }
}

async function storySuggest(e) {
  const box = S("s-suggestions");
  box.replaceChildren(sEl("p", { class: "note", text: `Reading ${e.name}'s filings…` }));
  try {
    const body = await api(`/entities/${encodeURIComponent(e.cik)}/suggestions?role=${S("role").value}&limit=6`);
    box.replaceChildren(...(body.suggestions.length ? body.suggestions.map((sg) => sEl("div", { class: "s-card" },
      sEl("button", { type: "button", class: "s-card-btn", onclick: () => startRun(sg.request) },
        sEl("span", { class: "kind", text: sg.kind }), sEl("span", { class: "q", text: sg.question }), sEl("span", { class: "go", text: "Investigate →" })),
      sEl("details", {}, sEl("summary", { text: "why this question" }), sEl("ul", { class: "checks" }, (sg.checks || []).map((c) => sEl("li", { text: c }))))))
      : [sEl("p", { class: "note", text: body.note || "No question found in this company's filings." })]));
  } catch (err) {
    box.replaceChildren(sEl("p", { class: "note", text: err.message }));
  }
}

function setView(view) {
  story.view = view;
  S("story").hidden = view !== "story";
  document.querySelector("main.layout").hidden = view !== "debug";
  S("s-console").hidden = view !== "story" || !story.s;
  for (const b of document.querySelectorAll("[data-view]")) b.setAttribute("aria-pressed", b.dataset.view === view ? "true" : "false");
}

if (typeof document !== "undefined" && S("story")) {
  renderExamples();
  S("s-q").addEventListener("input", (e) => { clearTimeout(sTimer); sTimer = setTimeout(() => storySearch(e.target.value.trim()), 250); });
  for (const b of document.querySelectorAll("[data-view]")) b.addEventListener("click", () => setView(b.dataset.view));
  for (const b of document.querySelectorAll("#s-console [data-tab]")) b.addEventListener("click", () => { story.consoleTab = b.dataset.tab; openConsole(true); scheduleRender(); });
  S("s-console-toggle").addEventListener("click", () => openConsole(!S("s-console").classList.contains("open")));
  S("s-trace-all").addEventListener("change", scheduleRender);
  S("s-download").addEventListener("click", () => S("download").click());
  S("s-convo").addEventListener("toggle", () => { if (story.s && story.s.finished) { if (S("s-convo").open) story.open.add("convo"); else story.open.delete("convo"); } });
  setView("story");
}
