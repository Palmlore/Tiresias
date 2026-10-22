# GCP plan (not yet deployed)

| Component | Target | Notes |
|---|---|---|
| `agent`, `cognition` | Cloud Run (two services) | Images from Artifact Registry. Configured through env vars and Secret Manager. `cognition` memory needs a persistent store (Cloud Run volume on GCS, or Firestore) rather than a local volume. |
| Zetaris | GCE VM (x86, e.g. e2-standard-4 or larger) | Vendor `.env.server.example`: HTTPS through Caddy and `sslip.io` on ports 80/443; set the admin password. Restrict ingress to the Cloud Run egress (Serverless VPC Access or Direct VPC egress). Not verified on GCP; the vendor documents AWS. |
| Nightshift | Developer machine through hotspot mode, or a Meterless-compatible lane | Cloud reachability is an open question (docs/STATUS.md). |

Logs: JSONL to stdout, collected by Cloud Logging. `logs/` is local only.
