# GCP

## Hosted demo (deployed)

**https://tiresias.palmlore.com**: the web UI in **sample mode**, for anyone to try without installing anything. The page is served by GitHub Pages (see "Static front" below) and calls the HTTP API on this Cloud Run service's `run.app` URL, https://tiresias-tckagkyqxa-as.a.run.app (API docs at `/docs`).

**Demo grade, on purpose.** It runs on deliberately small resources so its cost stays bounded: at most one instance (1 vCPU and 1 GiB for the agent, 1 vCPU and 512 MiB for the memory service), scaled to zero when idle, at most 4 runs at a time (a fifth gets 429 `TooManyRuns`), no CDN, no login. Under load it is slow or busy rather than more expensive. It is not a production deployment: see "Exposure" below.

| What | How |
|---|---|
| Service | Cloud Run `tiresias` ([service.yaml](service.yaml)), region from `GCP_REGION` (default `asia-southeast1`, a region where Cloud Run maps custom domains) |
| Containers | `agent` (port 8000, the same image as `docker compose`) with `cognition` (H-MEM) as a sidecar on `localhost:8100` |
| Mode | `SAMPLE_MODE=true`, `PUBLIC_DEMO=true`: a request asking for live mode gets 403 `LiveModeDisabled`, `POST /memory/purge` gets 403 `PurgeDisabled`. No secrets, no Zetaris, no Nightshift. |
| Scale | at most one instance (min 0, max 1). Max 1: runs and H-MEM lessons live in that instance's memory, and the UI follows a run (SSE, gates) on the instance that started it. Min 0: with no traffic the service scales to zero and bills nothing, so the first visit after a quiet spell waits a few seconds while it starts (cognition starts, then the agent passes its `/health` probe: about 8 s end to end on 17 Nov 2026, 2.70 s on 22 Nov 2026 after the start-up tuning below). A run the UI is following keeps its event stream (SSE) open, and a `POST /run` waits for its result, so either holds a request and the instance stays up until the run ends; a run started with `POST /runs` that nobody follows holds none, and its result can be lost if the instance scales down before `GET /runs/{id}` reads it. Lessons are shared by every visitor and lost on restart or scale-down. Trace files stay inside the instance and are cleared likewise. |
| Access | public, without an `allUsers` IAM binding (`run.googleapis.com/invoker-iam-disabled`), which also works where an organisation policy restricts IAM members to its own domain |
| Identities | least privilege: builds run as `tiresias-build` (push images, write logs, read the uploaded source), which also works where an organisation policy disables Cloud Build's default service account; the service runs as `tiresias-run`, which has no role |
| Images | Artifact Registry `<region>-docker.pkg.dev/<project>/tiresias/{agent,cognition}:<git sha>`, built by Cloud Build ([cloudbuild.yaml](cloudbuild.yaml)) from the repo root |
| Domain | the service answers on its own `run.app` URL only. `tiresias.palmlore.com` serves the static front from GitHub Pages (DNS: `CNAME tiresias → palmlore.github.io.`); until 22 Nov 2026 it was a Cloud Run domain mapping (`DEMO_DOMAIN`, `CNAME tiresias → ghs.googlehosted.com.`), so leave `DEMO_DOMAIN` unset or the deploy maps the domain back. |
| Cost guard | billing is per use: scale to zero means idle time costs no compute (an always-on instance at min 1 cost about US$1.06 a day at the asia-southeast1 idle rate, measured Nov 2026), and the instance cap above bounds the spend under load; a project budget with alerts at 50/90/100 % warns (a budget alerts, it does not stop spending) |

Deploy or update (idempotent: enables the APIs, creates the repository and service accounts if missing, builds, replaces the service, checks `/health`). The project is read from `GCP_PROJECT` in `.env` (gitignored; see `.env.example`) and never committed:

```bash
./deploy/gcp/deploy.sh                 # or: deploy/gcp/deploy.sh PROJECT_ID [REGION]
# DEMO_DOMAIN in .env maps that domain to the service; leave it unset (the domain is on GitHub Pages)
```

Logs: Cloud Logging.

## Static front (GitHub Pages)

A cold start makes the first request wait about 7 s, and when that request is the page itself the visitor sees a blank tab (measured 18 Nov 2026: `GET /` answered after 7.58 s, nothing before). The fix is to serve the page from an always-warm host and let it wake the API:

| What | How |
|---|---|
| Page | https://tiresias.palmlore.com (Pages custom domain, HTTPS enforced; also `https://palmlore.github.io/Tiresias/`): the web UI's files, built by [scripts/build_pages.sh](../../scripts/build_pages.sh) `API_URL OUT_DIR` (a copy of `web/` with `config.js` pointed at the API) and published by [.github/workflows/pages.yml](../../.github/workflows/pages.yml) on a push to `main` that touches the UI |
| First screen | `cold-start.js` paints the header and a skeleton of the Story view at once, sends `GET /health`, and gives way to the app on the first 2xx. Past 8 s it says it is taking longer than usual; past 30 s it says the server did not start and offers Try again. The agent serves the same page at `/ui/`, where the server is already up and the loader clears at once. |
| API | this Cloud Run service, called cross-origin: `CORS_ALLOW_ORIGINS` in [service.yaml](service.yaml) lists exactly `https://tiresias.palmlore.com` and `https://palmlore.github.io`; no credentials (the demo has no login) |
| Start-up | `startup-cpu-boost` and startup probes every 1 s (was 2 s), to keep the cold start inside the page's 8 s promise. Measured 22 Nov 2026 (revision `tiresias-00005`, after 15 min without a request, startup probes logged at the request): the cold `GET /health` took 2.70 s on the server; in Chrome the skeleton showed at once and the page was ready 3.19 s after the health request left, on the first attempt (it was a blank tab for 7.58 s before). One measurement, not an average. |

**How it was switched on** (22 Nov 2026; each step is a change outside this repo, kept here to redo or undo it):

1. Deployed this service (`./deploy/gcp/deploy.sh`) so CORS and the start-up settings are live, then timed a cold `GET /health` (above).
2. Repository settings: Pages, source **GitHub Actions**; Variables, `DEMO_API_URL` = the service's `run.app` URL (an https origin, no path). Ran the `pages` workflow and tested `https://palmlore.github.io/Tiresias/` (warm and cold, a full run, no CORS errors).
3. Moved the domain: set `tiresias.palmlore.com` as the Pages custom domain, pointed its CNAME (Namecheap DNS) at `palmlore.github.io.`, enforced HTTPS once GitHub issued the certificate, then deleted the Cloud Run domain mapping. The API stays on its `run.app` URL.

To undo: point the CNAME back at `ghs.googlehosted.com.`, remove the Pages custom domain, and redeploy with `DEMO_DOMAIN=tiresias.palmlore.com`. Without `DEMO_API_URL` the workflow is a no-op (`if: vars.DEMO_API_URL != ''`).

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
