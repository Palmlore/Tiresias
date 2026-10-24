# Demo plan (video 1–4 min, target about 3:30) and deck outline

## Video storyboard

| Time | Segment | On screen |
|---|---|---|
| 0:00–0:20 | Problem | 3M's 2023 revenue in two successive 10-Ks: $32.68B (filed 2024-02-07) → $24.61B (filed 2025-02-05). "Why?" Track: *Reasoning Architecture*. |
| 0:20–0:45 | How it works | Architecture diagram: Loop A, Loop B, critic, asker profile |
| 0:45–1:40 | Run 1, empty memory (analyst) | Run of case 04 ([input_examples/04](../input_examples/04_3m_2023_revenue_restated.json)), live, or offline in sample mode with the same result. Loop A traces the drop to 3M's disposal filing (8-K items 2.01, 9.01, 2024-04-04) and reaches the evidence frontier. Loop B searches EDGAR full text for Form 10s naming 3M, finds **Solventum** (a name that shares nothing with 3M's), asks the source gate and registers its filings in Zetaris. The critic reconciles: $24.61B + $8.20B = $32.81B against $32.68B, over-explained by $126M (residual −1.6%, share capped at 100%). |
| 1:40–2:10 | Run 2, with memory | A similar spin-off, GE case 1 (GE HealthCare): the recalled lesson means fewer hops. The step counts are shown side by side. |
| 2:10–2:30 | Purge | `POST /memory/purge`, then GE case 1 again: more hops. The trust ledger shows the purge event. |
| 2:30–2:50 | Asker switch and branch | The same node viewed as auditor, then board: different end states and actions |
| 2:50–3:15 | Sponsor tech in the trace | Zetaris SQL lines, source registration, Nightshift lane and model, H-MEM recall IDs |
| 3:15–3:30 | Output and impact | Report: labelled why-tree, residuals, action |

Further examples: GE cases 1 and 2 (GE HealthCare and GE Vernova spin-offs, $114M and −$633M residuals). Optional: case 3 (GE's 2018 impairment). The recalled spin-off lesson is **rejected** by the critic. To surface that lesson, recall with a broad query (for example "why did GE's reported figure change"); don't lower `RECALL_THRESHOLD` (0.25).

## Deck outline (the 8 required points)

1. Problem and target users
2. Solution
3. Agent workflow and architecture
4. Technical stack
5. How Zetaris and Meterless were used
6. What was unique about the sponsor tech use
7. Product visuals
8. Future enhancements
