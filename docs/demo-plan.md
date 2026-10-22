# Demo plan (video 1–4 min, target about 3:30) and deck outline

## Video storyboard

| Time | Segment | On screen |
|---|---|---|
| 0:00–0:20 | Problem | GE's 2022 revenue as reported in three successive 10-Ks: $76.6B → $58.1B → $29.1B. "Why?" Track: *The Agent That Can Explain Why*. |
| 0:20–0:45 | How it works | Architecture diagram: Loop A, Loop B, critic, asker profile |
| 0:45–1:40 | Run 1, empty memory (analyst) | Live run of case 1. Loop B finds GE HealthCare and registers its filings in Zetaris. The critic reconciles $18,455M against $18,341M and flags the $114M residual. |
| 1:40–2:10 | Run 2, with memory | Case 2 (Vernova): the recalled lesson means fewer hops. The step counts are shown side by side. |
| 2:10–2:30 | Purge | `POST /memory/purge`, then case 2 again: more hops. The trust ledger shows the purge event. |
| 2:30–2:50 | Asker switch and branch | The same node viewed as auditor, then board: different end states and actions |
| 2:50–3:15 | Sponsor tech in the trace | Zetaris SQL lines, source registration, Nightshift lane and model, H-MEM recall IDs |
| 3:15–3:30 | Output and impact | Report: labelled why-tree, residuals, action |

Optional: case 3 (2018 impairment). The recalled spin-off lesson is **rejected** by the critic.

## Deck outline (the 8 required points)

1. Problem and target users
2. Solution
3. Agent workflow and architecture
4. Technical stack
5. How Zetaris and Meterless were used
6. What was unique about the sponsor tech use
7. Product visuals
8. Future enhancements
