# GCP

## Hosted demo (deployed)

**https://tiresias-tckagkyqxa-ts.a.run.app**: the web UI and the HTTP API in **sample mode**, for anyone to try without installing anything.

**Demo grade, on purpose.** It runs on deliberately small resources so its cost stays bounded: one instance (1 vCPU, 1 GiB for the agent, 512 MiB for memory), at most 4 runs at a time (a fifth gets 429 `TooManyRuns`), no autoscaling, no CDN, no login. Under load it is slow or busy rather than more expensive. It is not a production deployment: see "Exposure" below.

| What | How |
|---|---|
| Service | Cloud Run `tiresias` ([service.yaml](service.yaml)), region from `GCP_REGION` (default `australia-southeast1`) |
| Containers | `agent` (port 8000, the same image as `docker compose`) with `cognition` (H-MEM) as a sidecar on `localhost:8100` |
| Mode | `SAMPLE_MODE=true`, `PUBLIC_DEMO=true`: a request asking for live mode gets 403 `LiveModeDisabled`, `POST /memory/purge` gets 403 `PurgeDisabled`. No secrets, no Zetaris, no Nightshift. |
| Scale | exactly one instance (min 1, max 1): runs and H-MEM lessons live in that instance's memory, and the UI follows a run (SSE, gates) on the instance that started it. Lessons are shared by every visitor and lost on restart. Trace files stay inside the instance and are cleared on restart. |
| Access | public, without an `allUsers` IAM binding (`run.googleapis.com/invoker-iam-disabled`), which also works where an organisation policy restricts IAM members to its own domain |
| Identities | least privilege: builds run as `tiresias-build` (push images, write logs, read the uploaded source), which also works where an organisation policy disables Cloud Build's default service account; the service runs as `tiresias-run`, which has no role |
| Images | Artifact Registry `<region>-docker.pkg.dev/<project>/tiresias/{agent,cognition}:<git sha>`, built by Cloud Build ([cloudbuild.yaml](cloudbuild.yaml)) from the repo root |
| Cost guard | the instance cap above bounds the spend; a project budget with alerts at 50/90/100 % warns (a budget alerts, it does not stop spending) |

Deploy or update (idempotent: enables the APIs, creates the repository and service accounts if missing, builds, replaces the service, checks `/health`). The project is read from `GCP_PROJECT` in `.env` (gitignored; see `.env.example`) and never committed:

```bash
./deploy/gcp/deploy.sh                 # or: deploy/gcp/deploy.sh PROJECT_ID [REGION]
```

Logs: Cloud Logging.

## Exposure

The service is reachable directly on its `run.app` URL. What a visitor can and cannot do:

| A visitor can | A visitor cannot |
|---|---|
| run sample-mode investigations on the cached SEC slice (public data), answer gates, read results and traces | reach Zetaris, Nightshift, SEC or any live service (`PUBLIC_DEMO`), or read any secret (the service has none, and its identity has no role) |
| store an entity-free lesson in the shared memory (the agent writes its text, never the visitor) | purge or edit memory |
| send many requests: they queue on one instance (429 beyond 4 concurrent runs) | raise the cost beyond one always-on instance (max 1) |

Not in place: per-client rate limits, bot filtering or a WAF. For those, put an external HTTPS load balancer with Cloud Armor in front and set the service's ingress to `internal-and-cloud-load-balancing`, so the `run.app` URL stops answering. That is the step to take before anything beyond a demo.

## Live mode in the cloud (not deployed)

| Component | Target | Notes |
|---|---|---|
| Zetaris | GCE VM (x86, e.g. e2-standard-4 or larger) running the vendor bundle's `docker compose` | Vendor `.env.server.example`; private network only, reached from Cloud Run through Direct VPC egress. Not verified on GCP; the vendor documents AWS. Hosting rights to be confirmed with Zetaris. |
| Nightshift | Developer machine through hotspot mode | A desktop app; whether it may run on a server is a question for Meterless (docs/STATUS.md). |
