# ADR-0008: Local first, ready for the cloud

Status: accepted (2026-10-22)

Context: local-first operation is valued, and judges run locally. A cloud deployment helps the demo. The docs mention AWS for Zetaris but don't forbid other clouds.

Decision:
- Local Docker Compose is the primary target.
- Services are 12-factor: configured by environment, stateless except for H-MEM storage on a volume, bound to `0.0.0.0`.
- Cloud plan:
  - GCP: Cloud Run for `agent` and `cognition`; Zetaris on a GCE VM using the vendor's server `.env` (HTTPS through Caddy and sslip.io); Nightshift either kept on the developer machine through hotspot mode or replaced by a Meterless-compatible cloud lane.
  - AWS: Zetaris's managed-cluster guide.

See [deploy/](../../deploy/).

Consequence: no cloud-only dependencies in the code path; cloud is an overlay.
