# ADR-0006: Human-in-the-loop gates with three modes, plus branching

Status: accepted (2026-10-22)

Context: different askers should get different roots and actions, but judges need unattended runs (no interactive input).

Decision:
- Four gates: framing (asker profile), source onboarding, critic escalation, memory write.
- Each gate runs in `interactive`, `auto` (policy, logged) or `scripted` (from the input JSON). Default: `auto`.
- After a run, users can branch from any node (new run with `parent_run_id` and `parent_node_id`).
- Every gate decision is traced with its mode and decider.
