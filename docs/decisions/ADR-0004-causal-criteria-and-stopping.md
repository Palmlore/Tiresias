# ADR-0004: Causal criteria and an asker-relative stopping rule

Status: accepted (2026-10-22)

Context: naive 5 Whys is a linear chain of opinions with a fixed depth.

The first-draft traits were stress-tested:
1. "Fix it with everything else held constant" → repaired as an intervention test: let the effects downstream of X follow, test sets of causes, require an abnormal change.
2. "Max effect / sensitivity" → reclassified as leverage, a separate output.
3. "A bigger effect is needed for a new level" → rejected. Effects weaken going up a causal chain, so levels differ in kind, not size.
4. "May not converge" → extended into six end states.

Decision:
- Use the critic rulebook in [causal-criteria.md](../causal-criteria.md).
- Stop at the first causal link that the asker can act on, at any depth.

Consequence: breakdown steps don't count as levels. The IBM chain recounts to about 2–3 causal levels.
