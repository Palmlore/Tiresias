# Deployment

## Local (primary, ADR-0008)

| Component | How | Needed for |
|---|---|---|
| `agent`, `cognition` | `docker compose up --build` from the repo root | Always |
| Zetaris | Vendor bundle `zetaris-platform` (Docker; 8 GB memory and 4 CPUs for Docker; Rosetta on Apple Silicon). `docker compose up -d`, then `./verify.sh`. REST at `:8888`. | Live mode |
| Meterless Nightshift | Desktop app from meterless.ai. Copy the Runtime URL and token from its Console into `.env`. Add the NVIDIA key in its Providers tab. | Live mode |

Sample mode (`SAMPLE_MODE=true`, the default) needs only the first row.

## Cloud (overlay, not required to run)

- [gcp/](gcp/README.md): Cloud Run for the services; Zetaris on a GCE VM
- [aws/](aws/README.md): Zetaris's documented managed-cluster path
