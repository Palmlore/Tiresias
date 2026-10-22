# ADR-0002: Split services by boundary, REST/JSON

Status: accepted (2026-10-22)

Context: Python is preferred for the agents. The Meterless engine reference implementations and conformance runners are TypeScript.

Decision: two services.
- `agent` (Python/FastAPI) and `cognition` (TypeScript/Node, H-MEM reference behind REST).
- They talk over REST/JSON, with OpenAPI contracts written first.
- Trace context goes in an `X-Run-Id` header (W3C `traceparent` may follow).

Rationale:
- Reuse the official engine and pass its official conformance suite as Build-rule evidence, while keeping the agent in Python.
- REST rather than gRPC: no codegen in two languages, judges can use `curl`, and streaming isn't needed at this scale.

Consequences:
- Two runtimes, managed by Docker Compose.
- Both services must write the same trace schema to the shared `logs/`.
