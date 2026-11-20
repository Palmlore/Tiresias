# GCP

## Hosted demo (deployed)

**https://tiresias.palmlore.com**: the web UI and the HTTP API in **sample mode**, for anyone to try without installing anything.

**Demo grade, on purpose.** It runs on deliberately small resources so its cost stays bounded: at most one instance (1 vCPU and 1 GiB for the agent, 1 vCPU and 512 MiB for the memory service), scaled to zero when idle, at most 4 runs at a time (a fifth gets 429 `TooManyRuns`), no CDN, no login. Under load it is slow or busy rather than more expensive. It is not a production deployment: see "Exposure" below.

| What | How |
|---|---|
| Service | Cloud Run `tiresias` ([service.yaml](service.yaml)), region from `GCP_REGION` (default `asia-southeast1`, a region where Cloud Run maps custom domains) |
| Containers | `agent` (port 8000, the same image as `docker compose`) with `cognition` (H-MEM) as a sidecar on `localhost:8100` |
| Mode | `SAMPLE_MODE=true`, `PUBLIC_DEMO=true`: a request asking for live mode gets 403 `LiveModeDisabled`, `POST /memory/purge` gets 403 `PurgeDisabled`. No secrets, no Zetaris, no Nightshift. |
| Scale | at most one instance (min 0, max 1). Max 1: runs and H-MEM lessons live in that instance's memory, and the UI follows a run (SSE, gates) on the instance that started it. Min 0: with no traffic the service scales to zero and bills nothing, so the first visit after a quiet spell waits a few seconds while it starts (about 8 s end to end on 17 Nov 2026: cognition starts, then the agent passes its `/health` probe). A run the UI is following keeps its event stream (SSE) open, and a `POST /run` waits for its result, so either holds a request and the instance stays up until the run ends; a run started with `POST /runs` that nobody follows holds none, and its result can be lost if the instance scales down before `GET /runs/{id}` reads it. Lessons are shared by every visitor and lost on restart or scale-down. Trace files stay inside the instance and are cleared likewise. |
| Access | public, without an `allUsers` IAM binding (`run.googleapis.com/invoker-iam-disabled`), which also works where an organisation policy restricts IAM members to its own domain |
| Identities | least privilege: builds run as `tiresias-build` (push images, write logs, read the uploaded source), which also works where an organisation policy disables Cloud Build's default service account; the service runs as `tiresias-run`, which has no role |
| Images | Artifact Registry `<region>-docker.pkg.dev/<project>/tiresias/{agent,cognition}:<git sha>`, built by Cloud Build ([cloudbuild.yaml](cloudbuild.yaml)) from the repo root |
| Domain | `tiresias.palmlore.com`, a Cloud Run domain mapping (`DEMO_DOMAIN`; DNS: `CNAME tiresias → ghs.googlehosted.com.`; Google-managed certificate). The service's own `run.app` URL also answers. |
| Cost guard | billing is per use: scale to zero means idle time costs no compute (an always-on instance at min 1 cost about US$1.06 a day at the asia-southeast1 idle rate, measured Nov 2026), and the instance cap above bounds the spend under load; a project budget with alerts at 50/90/100 % warns (a budget alerts, it does not stop spending) |

Deploy or update (idempotent: enables the APIs, creates the repository and service accounts if missing, builds, replaces the service, checks `/health`). The project is read from `GCP_PROJECT` in `.env` (gitignored; see `.env.example`) and never committed:

```bash
./deploy/gcp/deploy.sh                 # or: deploy/gcp/deploy.sh PROJECT_ID [REGION]
# optional: DEMO_DOMAIN in .env maps that domain to the service (create the CNAME it prints)
```

Logs: Cloud Logging.

## Static front (GitHub Pages): built, not switched on

A cold start makes the first request wait about 7 s, and when that request is the page itself the visitor sees a blank tab (measured 18 Nov 2026: `GET /` answered after 7.58 s, nothing before). The fix is to serve the page from an always-warm host and let it wake the API:

| What | How |
|---|---|
| Page | The web UI's files, built by [scripts/build_pages.sh](../../scripts/build_pages.sh) `API_URL OUT_DIR` (a copy of `web/` with `config.js` pointed at the API) and published by [.github/workflows/pages.yml](../../.github/workflows/pages.yml) on a push to `main` that touches the UI |
| First screen | `cold-start.js` paints the header and a skeleton of the Story view at once, sends `GET /health`, and gives way to the app on the first 2xx. Past 8 s it says it is taking longer than usual; past 30 s it says the server did not start and offers Try again. The agent serves the same page at `/ui/`, where the server is already up and the loader clears at once. |
| API | this Cloud Run service, called cross-origin: `CORS_ALLOW_ORIGINS` in [service.yaml](service.yaml) lists exactly `https://tiresias.palmlore.com` and `https://palmlore.github.io`; no credentials (the demo has no login) |
| Start-up | `startup-cpu-boost` and startup probes every 1 s (was 2 s), to keep the cold start inside the page's 8 s promise. Expected, not yet measured: measure with a cold `GET /health` after the next deploy. |

**Switching it on** (each step is a change outside this repo):

1. Deploy this service (`./deploy/gcp/deploy.sh`) so CORS and the start-up settings are live, then time a cold `GET /health`.
2. In the repository settings: Pages, source **GitHub Actions**; Variables, `DEMO_API_URL` = the service's `run.app` URL (an https origin, no path). Run the `pages` workflow and test the page at `https://palmlore.github.io/Tiresias/`.
3. Move the domain: delete the Cloud Run domain mapping for `tiresias.palmlore.com`, point its CNAME at `palmlore.github.io.`, and set it as the Pages custom domain (HTTPS on). The API stays on its `run.app` URL.

Until step 2 the workflow is a no-op (`if: vars.DEMO_API_URL != ''`).

## Exposure

The service is reachable directly on its `run.app` URL. What a visitor can and cannot do:

| A visitor can | A visitor cannot |
|---|---|
| run sample-mode investigations on the cached SEC slice (public data), answer gates, read results and traces | reach Zetaris, Nightshift, SEC or any live service (`PUBLIC_DEMO`), or read any secret (the service has none, and its identity has no role) |
| store an entity-free lesson in the shared memory (the agent writes its text, never the visitor) | purge or edit memory |
| send many requests: they queue on one instance (429 beyond 4 concurrent runs) | raise the cost beyond one instance running flat out (max 1) |

Not in place: per-client rate limits, bot filtering or a WAF. For those, put an external HTTPS load balancer with Cloud Armor in front and set the service's ingress to `internal-and-cloud-load-balancing`, so the `run.app` URL stops answering. That is the step to take before anything beyond a demo.

## Live mode in the cloud (not deployed)

| Component | Target | Notes |
|---|---|---|
| Zetaris | GCE VM (x86, e.g. e2-standard-4 or larger) running the vendor bundle's `docker compose` | Vendor `.env.server.example`; private network only, reached from Cloud Run through Direct VPC egress. Not verified on GCP; the vendor documents AWS. Hosting rights to be confirmed with Zetaris. |
| Nightshift | Developer machine through hotspot mode | A desktop app; whether it may run on a server is a question for Meterless (docs/STATUS.md). |
