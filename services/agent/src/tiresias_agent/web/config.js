// Settings for cold-start.js and the API base app.js calls. Served as is by the agent (same
// origin, apiBase ""); the GitHub Pages build (scripts/build_pages.sh) rewrites the apiBase line
// to the Cloud Run URL. Keep that line's shape: the build requires exactly one such line.
window.COLD_START = {
  apiBase: "",
  healthPath: "/health",
  promiseSeconds: 8,
  giveUpSeconds: 30,
  messages: {
    waiting: "Starting the demo server…",
    waitingDetail: "It sleeps when nobody is using it, to keep its cost down. The first visit takes up to {promise} seconds.",
    failedDetail: "It usually starts in under {promise} seconds. Try again in a minute, or run Tiresias locally with docker compose (see Source on GitHub).",
  },
};
