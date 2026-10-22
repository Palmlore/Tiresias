# Causal criteria: the critic's rulebook

"Ask why five times" is a heuristic (Taiichi Ohno, Toyota), not a method. Tiresias makes it a method by giving the critic explicit tests. These criteria came from stress-testing a first draft of "root cause" traits; [ADR-0004](decisions/ADR-0004-causal-criteria-and-stopping.md) records that process.

## 1. Intervention test (is X a cause?)

Set X back to its baseline value, **let everything downstream of X follow**, and hold constant only factors that X does not cause. X passes if this removes **most of the effect**. Report the **share explained** and the **residual**.

- Don't freeze downstream intermediate causes. Freezing them blocks X's effect.
- Test **sets** of causes when there are several, any one of which would produce the effect alone.
- X must be an **abnormal change** relative to the baseline. Background conditions ("the company exists") are rejected.
- Where the data allows, the Analyst **re-queries Zetaris counterfactually**, e.g. "remove the spun-off entity's revenue: does the change disappear?"

## 2. Level-counting rule (does this step deserve a new level?)

A step counts as a **causal level** only if the cause is:
1. **not true by definition**: an accounting identity or a breakdown of the total is not a cause;
2. **earlier in time** than the effect;
3. **a different variable or entity**, linked by a mechanism with evidence;
4. **passing the intervention test.**

A deeper cause does **not** need a bigger effect. If all of its effect runs through its child, it can explain at most as much as the child. Levels differ in **kind, not size**.

## 3. Link labels

| Label | Meaning | Counts as a level? |
|---|---|---|
| `breakdown` | Locates the change ("where in the numbers") | No |
| `causal` | Passes §2. Evidence status: `evidenced` / `partially-evidenced` / `hypothesis` | Yes |
| `leverage` | High sensitivity: a small change in it moves the effect a lot. Reported under "where to act". | No (separate output) |
| `background` | A necessary but normal condition | Rejected |

## 4. End states (every run ends in exactly one)

| End state | Meaning | Output |
|---|---|---|
| `actionable` | Reached a cause the **asker** can act on | Root cause, action, evidence chain |
| `branched` | Several causes each explain a share | Shares that add up to the effect, plus a residual |
| `cyclic` | A feedback loop with no single root | The loop and its leverage point |
| `evidence-frontier` | The cause probably exists but no catalog source can show it | Hypothesis plus where to look |
| `external-driver` | The next "why" is outside the asker's control | The external driver: monitor or adapt |
| `budget` | A depth, source or time cap was reached | Best chain so far, marked incomplete |

## 5. Stopping rule: who asks decides where the root is

A root cause depends on **who asks and what they can change**. Each run takes an asker profile (role, levers, decision horizon). The run stops at the **first cause that passes the causal tests and that this asker can act on**. That may be level 2 or level 5. Beyond it, Tiresias records context marked `beyond-scope` but does not keep investigating.

## 6. Memory: lessons, not answers

Each run can produce a reusable **lesson**, e.g. "restatement → look for an 8-K item 2.01 disposal → reconcile against the spun-off entity". The lesson goes into H-MEM after the memory gate. Final answers are never stored. The critic must still reject a recalled lesson that doesn't fit: a spin-off lesson applied to an impairment question is rejected.
