# ADR-0009: Track: Reasoning Architecture

Status: accepted (2026-10-23). Supersedes the track named in ADR-0001's context.

Context: The entry was registered for *The Agent That Can Explain Why*. The design that emerged from ADR-0003..0007 is less about retrieving and citing evidence and more about how the agent reasons to a decision: a bounded causal loop, explicit causal criteria, memory that is checked before it is reused, and a stopping rule that ends in an action. The Reasoning Architecture track asks for "a novel ability to turn data, knowledge, memory, and reasoning into reliable decisions and actions". Teams may change track until the submission deadline.

Decision:
- Primary track: **Reasoning Architecture**.
- Positioning: Tiresias is a reasoning architecture for "why" questions. Each why is a bounded, evidence-tested step; a critic applies explicit causal criteria; memory compounds but every recalled lesson is checked before it is used; the output is a decision (stop or dig deeper, and what to do now) with the explained share and the residual quantified.

Rationale (how the design maps to the track):

| Track asks for | Tiresias |
|---|---|
| Data | Every claim is a Zetaris query; new sources are registered at runtime (Loop B) |
| Knowledge | [causal-criteria.md](../causal-criteria.md): breakdown vs causal labels, intervention test, share explained, residual |
| Memory | H-MEM lessons (ADR-0005): warm runs take fewer steps; the critic rejects a lesson that doesn't fit the case |
| Reasoning | Hypothesise → test → critique → go deeper, capped by `MAX_CAUSAL_DEPTH` and `MAX_BRANCHES_PER_LEVEL` |
| Reliable decisions | Six end states and an asker-relative stopping rule (ADR-0004); quantified share explained and residual |
| Actions | Registers a source, writes back a lesson, recommends an action (e.g. "re-base on continuing ops; residual open") |

Consequences:
- No change to the architecture, services or build order. Evidence-backed explanation stays; it becomes the support for the decision rather than the headline.
- The run result leads with the end state and the recommended action; the why-chain is its evidence.
- The demo centres on reliability: the critic rejecting a misapplied lesson, cold vs warm memory, the stop decision.
- The Meterless Markovian engine (each why as one bounded step) fits this track well; it stays a stretch goal.
- Track wording updated in README, `metadata.json`, AGENTS.md, CHECKLIST and the demo plan. The problem framing, deck and sponsor explanation follow the positioning above.
- The track must also be selected on HackOS (CHECKLIST §A).
