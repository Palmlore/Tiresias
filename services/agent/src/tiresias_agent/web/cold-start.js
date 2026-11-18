// cold-start.js (cold-start-loader v0.1.1): the first screen of a web app whose API server
// scales to zero. The page itself comes from an always-warm static host, paints a skeleton of
// the app at once, and wakes the server with GET <apiBase><healthPath>. The first 2xx from that
// endpoint is the only readiness signal: the loader then gives way and `ColdStart.ready`
// resolves. Assumptions and setup: the cold-start-loader README.
//
// Load it as a classic script in <head>, after its stylesheet and after any config script that
// sets `window.COLD_START`, so the `cs-waiting` class is on <html> before <body> is parsed.
"use strict";
(function (root) {
  const DEFAULTS = {
    apiBase: "",                // "" = same origin; else the API's origin, e.g. https://api.example.com
    healthPath: "/health",
    promiseSeconds: 8,          // the wait the page promises; past it the page says "longer than usual"
    giveUpSeconds: 30,          // past it the page says the server did not start and offers a retry
    requestTimeoutSeconds: 12,  // one /health attempt; a cold platform holds the request while it starts
    retryDelaySeconds: 1,       // between attempts that failed fast (connection refused, 5xx, 429)
    fastPathMs: 300,            // a server that answers within this hides the loader without a fade
    fadeMs: 250,
    messages: {
      waiting: "Starting the server…",
      waitingDetail: "It sleeps when nobody is using it, to keep its cost down. The first visit takes up to {promise} seconds.",
      slow: "Taking longer than usual…",
      slowDetail: "Still starting the server. This page keeps trying for you.",
      failed: "The server did not start",
      failedDetail: "It usually starts in under {promise} seconds. Try again in a minute.",
      ready: "Ready",
      retry: "Try again",
    },
  };

  function config(user) {
    const u = user || {};
    return { ...DEFAULTS, ...u, messages: { ...DEFAULTS.messages, ...(u.messages || {}) } };
  }

  function fill(text, cfg) {
    return String(text).replace(/\{promise\}/g, String(cfg.promiseSeconds));
  }

  // What the page shows after `elapsed` seconds without a healthy answer.
  function phase(elapsed, cfg) {
    if (elapsed >= cfg.giveUpSeconds) return "failed";
    if (elapsed >= cfg.promiseSeconds) return "slow";
    return "waiting";
  }

  // The bar is time-based (the browser cannot see the server start): it fills toward 95% over
  // the promised wait and reaches 100% only when the server answers.
  function progress(elapsed, cfg) {
    return Math.max(0, Math.min(95, (elapsed / cfg.promiseSeconds) * 95));
  }

  // Polls the health endpoint until a 2xx or the give-up deadline. `env` injects fetch, the clock
  // and sleep so the loop runs under test. Resolves {ok, status, body, seconds, attempts, reason}.
  async function waitForHealth(cfg, env) {
    const url = cfg.apiBase.replace(/\/$/, "") + cfg.healthPath;
    const start = env.now();
    const left = () => cfg.giveUpSeconds - (env.now() - start) / 1000;
    let attempts = 0, reason = "no attempt";
    while (left() > 0) {
      attempts += 1;
      const ctrl = typeof AbortController === "function" ? new AbortController() : null;
      const timer = ctrl ? setTimeout(() => ctrl.abort(), Math.min(cfg.requestTimeoutSeconds, left()) * 1000) : null;
      try {
        const res = await env.fetch(url, { cache: "no-store", signal: ctrl ? ctrl.signal : undefined });
        if (res.ok) {
          let body = null;
          try { body = await res.json(); } catch { /* not JSON: still healthy */ }
          return { ok: true, status: res.status, body, seconds: (env.now() - start) / 1000, attempts, reason: "" };
        }
        reason = `HTTP ${res.status}`;
      } catch (e) {
        reason = e && e.name === "AbortError" ? "timed out" : "unreachable";
      } finally {
        if (timer) clearTimeout(timer);
      }
      if (left() > 0) await env.sleep(Math.min(cfg.retryDelaySeconds, left()) * 1000);
    }
    return { ok: false, status: 0, body: null, seconds: (env.now() - start) / 1000, attempts, reason };
  }

  // ---- DOM ------------------------------------------------------------------------------------
  // Expects one element with id="cold-start" holding the app's skeleton and these hooks:
  //   [data-cs="title"] [data-cs="detail"] [data-cs="bar"] [data-cs="retry"]
  // Elements marked data-cold-start-hide are hidden while the loader shows.

  function start(userConfig) {
    const cfg = config(userConfig);
    const doc = root.document;
    const html = doc.documentElement;
    html.classList.add("cs-waiting");
    let resolveReady;
    const ready = new Promise((r) => { resolveReady = r; });
    const env = {
      fetch: (u, o) => root.fetch(u, o),
      now: () => root.performance.now(),
      sleep: (ms) => new Promise((r) => root.setTimeout(r, ms)),
    };
    let running = false, settled = false, round = 0;

    function ui() {
      const box = doc.getElementById("cold-start");
      if (!box) return null;
      const q = (k) => box.querySelector(`[data-cs="${k}"]`);
      return { box, title: q("title"), detail: q("detail"), bar: q("bar"), retry: q("retry") };
    }

    // Writes only what changed: the card is a live region, and a node rewritten with the same
    // text can be announced again on every 100 ms tick.
    function show(u, state, elapsed) {
      if (!u) return;
      if (u.box.dataset.state !== state) u.box.dataset.state = state;
      const m = cfg.messages;
      const [t, d] = state === "ready" ? [m.ready, ""]
        : state === "failed" ? [m.failed, m.failedDetail]
        : state === "slow" ? [m.slow, m.slowDetail] : [m.waiting, m.waitingDetail];
      if (u.title && u.title.textContent !== fill(t, cfg)) u.title.textContent = fill(t, cfg);
      if (u.detail && u.detail.textContent !== fill(d, cfg)) u.detail.textContent = fill(d, cfg);
      if (u.bar) u.bar.style.width = state === "ready" ? "100%" : `${progress(elapsed, cfg)}%`;
      if (u.retry) {
        if (u.retry.hidden !== (state !== "failed")) u.retry.hidden = state !== "failed";
        if (u.retry.textContent !== m.retry) u.retry.textContent = m.retry;
      }
    }

    // One wait at a time: a second click on Try again, or a call after success, does nothing.
    function attempt() {
      if (running || settled) return;
      running = true;
      const mine = ++round; // a tick left over from an earlier, failed wait stops itself
      const t0 = env.now();
      const tick = () => {
        if (!running || mine !== round) return;
        const s = (env.now() - t0) / 1000;
        show(ui(), phase(s, cfg) === "failed" ? "slow" : phase(s, cfg), s); // "failed" only on the result
        root.setTimeout(tick, 100);
      };
      tick();
      waitForHealth(cfg, env).then((r) => {
        running = false;
        const u = ui();
        if (!r.ok) {
          show(u, "failed", r.seconds);
          if (u && u.retry) u.retry.focus();
          return;
        }
        settled = true;
        show(u, "ready", r.seconds);
        const leave = () => html.classList.remove("cs-waiting", "cs-leaving");
        if (!u || r.seconds * 1000 <= cfg.fastPathMs) leave();
        else {
          u.box.style.transitionDuration = `${cfg.fadeMs}ms`;
          html.classList.add("cs-leaving");
          root.setTimeout(leave, cfg.fadeMs);
        }
        resolveReady(r);
      });
    }

    doc.addEventListener("click", (e) => {
      const b = e.target && e.target.closest && e.target.closest('#cold-start [data-cs="retry"]');
      if (b) attempt();
    });
    // Without the loader's markup there is nothing to show while waiting, so the page's own
    // regions stay visible rather than hidden behind an empty screen; `ready` still waits.
    const checkMarkup = () => { if (!ui()) html.classList.remove("cs-waiting"); };
    if (doc.readyState === "loading") doc.addEventListener("DOMContentLoaded", checkMarkup);
    else checkMarkup();
    attempt(); // the health request leaves now, while the body is still being parsed
    return { ready, config: cfg, apiBase: cfg.apiBase.replace(/\/$/, "") };
  }

  const api = { DEFAULTS, config, phase, progress, waitForHealth, start };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  // In a page: window.ColdStart, started from window.COLD_START, or, with COLD_START_MANUAL set,
  // the bare API so the page can call ColdStart.start(config) itself.
  if (root.document) root.ColdStart = root.COLD_START_MANUAL ? { ...api } : { ...api, ...start(root.COLD_START) };
})(typeof window !== "undefined" ? window : globalThis);
