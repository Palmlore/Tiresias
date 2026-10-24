# ADR-0005: Memory stores lessons, not answers, and can be purged

Status: accepted (2026-10-22)

Context: a faster second run on the same question would look like a cache, which is close to "hardcoded output".

Decision:
- H-MEM stores procedural lessons and source shortcuts only.
- Asker preferences go in a separate namespace.
- Purge is exposed (`POST /memory/purge`, `--memory cold|warm`) and recorded in the append-only trust ledger.
- Speed is measured in steps (hops, queries, sources onboarded, model calls, tokens) as well as seconds.

Rationale:
- Shows that lessons transfer across cases (GE HealthCare → GE Vernova).
- Shows that a lesson can be rejected when it doesn't fit (the impairment case).
- Gives judges a cold-vs-warm comparison.

## Addendum (2026-10-24, feat/memory): how the agent uses it

Implemented in `services/agent/src/tiresias_agent/agents/memory.py`, with `critic.judge_lesson` and the hypothesiser's `prefer`.

- **A lesson is a path, not an answer.** Content and tags come only from the rule vocabulary (`knowledge.LESSON_STEP_TEXT`, `step_key`), the kind of effect and the metric family: tags `kind:`, `family:`, one `path:<rule>=<key>><outcome>` per step from the root to the chosen link, `end:`. An `entity_free` check (no `$`, no 4+ digit run, no ticker, CIK or counterparty name of the run) runs before the memory gate, and a test checks the four examples' lessons against the case-literal guard. A path already stored is not stored again (it gets `helpful` feedback).
- **Recall is broad; the critic decides.** The query is the frame's kind and family only, so a spin-off lesson is also recalled for an impairment question (both score above `RECALL_THRESHOLD` 0.25 with the model-free embedder; the threshold is unchanged). `knowledge.STEP_EQUIVALENTS` maps a disposal step to the discontinued-operations component, so the misapplied lesson is actually tested there and the critic prunes it: rejected, `not_helpful` (`wrong` when it was learnt on the same kind of question).
- **Deviation: a lesson may save hops by not testing siblings.** Reordering alone cannot make a run cheaper, because every candidate of a level is tested. So when a lesson's own step holds exactly as recorded, on the same kind of effect and metric family (frontier, or accepted with at least `MOST_OF_EFFECT`), the level's remaining candidates are not tested. The end state, root cause, action and chosen path are unchanged (tested on the four examples), but the warm why-tree omits the skipped siblings; they are listed in `RunResult.memory.skipped`, and the critic still tests every node that is shown. If the shortcut reaches no cause, the orchestrator re-runs Loop A (and B) in full from the frame, the lesson gets `not_helpful` and the stats count both passes (`memory.fallback`).
- **Modes.** `RunOptions.memory` `warm` / `cold`, default `MEMORY_MODE` (`warm`); `MEMORY_MODE=off` forces cold for every run (tests use it). Purge is `POST /memory/purge` on the agent, which reports only the count cognition confirmed (503/502 otherwise). Speed is measured in hops and queries (`stats`); on the sample, 01 after 04 takes 3 hops and 6 queries instead of 5 and 7.
