# GCP

## Hosted demo (deployed)

**https://tiresias-tckagkyqxa-ts.a.run.app**: the web UI and the HTTP API in **sample mode**, for anyone to try without installing anything.

| What | How |
|---|---|
| Service | Cloud Run `tiresias`, `australia-southeast1`, project `tiresias-512703`; [service.yaml](service.yaml) |
| Containers | `agent` (port 8000, the same image as `docker compose`) with `cognition` (H-MEM) as a sidecar on `localhost:8100` |
| Mode | `SAMPLE_MODE=true`, `PUBLIC_DEMO=true`: a request asking for live mode gets 403 `LiveModeDisabled`, `POST /memory/purge` gets 403 `PurgeDisabled`. No secrets, no Zetaris, no Nightshift. |
| Scale | exactly one instance (min 1, max 1): runs and H-MEM lessons live in that instance's memory, and the UI follows a run (SSE, gates) on the instance that started it. Lessons are shared by every visitor and lost on restart. At most `MAX_CONCURRENT_RUNS`=4 runs at once (429 beyond). |
| Access | public: `run.googleapis.com/invoker-iam-disabled` (the organisation restricts IAM members to its domain, so `allUsers` cannot be granted) |
| Identities | builds run as `tiresias-build` (push images, write logs, read the uploaded source), because the organisation disables Cloud Build's default service account; the service runs as `tiresias-run`, which has no role |
| Images | Artifact Registry `australia-southeast1-docker.pkg.dev/tiresias-512703/tiresias/{agent,cognition}:<git sha>`, built by Cloud Build ([cloudbuild.yaml](cloudbuild.yaml)) from the repo root |
| Cost guard | project budget A$50 with alerts at 50/90/100 % |

Deploy or update (idempotent; enables the APIs, creates the repository and service accounts if missing, builds, replaces the service, checks `/health`):

```bash
./deploy/gcp/deploy.sh [PROJECT_ID] [REGION]     # defaults: tiresias-512703, australia-southeast1
```

Logs: Cloud Logging (the agent's JSONL trace files stay inside the instance).

## Live mode in the cloud (not deployed)

| Component | Target | Notes |
|---|---|---|
| Zetaris | GCE VM (x86, e.g. e2-standard-4 or larger) running the vendor bundle's `docker compose` | Vendor `.env.server.example`; private network only, reached from Cloud Run through Direct VPC egress. Not verified on GCP; the vendor documents AWS. Hosting rights to be confirmed with Zetaris. |
| Nightshift | Developer machine through hotspot mode | A desktop app; whether it may run on a server is a question for Meterless (docs/STATUS.md). |
