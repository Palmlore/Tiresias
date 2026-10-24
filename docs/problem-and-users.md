# Problem and users

The day-to-day workflow, the screens a UI needs and the demo stories are in [use-cases.md](use-cases.md).

## Primary user: the financial analyst (equity or credit)

**Job to be done.** Before relying on a company's numbers, understand *why* a reported figure changed between filings or periods, with evidence that can be cited, and decide what to do with the model, the estimate or the rating.

**Pain today.** The answer is spread across several filings, restated values, filing events and, after a spin-off, *another company's* filings. Connecting them is slow manual work, and the shortcut (a guess) is risky.

**Output Tiresias gives.** A why-tree in which every link has a label, the SQL behind it, the rows it returned, the share of the effect it explains, and the residual. It ends with an explicit end state and an **action for this asker**.

## Asker profiles: the same question, different roots (human-in-the-loop by design)

| Asker | Levers / horizon | Where a restatement question typically stops |
|---|---|---|
| **Analyst** (primary) | Model adjustments, rating, estimates; next quarter | "Revenue was restated for the spin-off. Re-base the model on continuing operations; the residual is an open question." |
| **Auditor** | Testing scope, follow-up requests, sign-off | "Reconciles to the carve-out except for the residual. Request the intercompany elimination schedule." |
| **Board / CEO** | Portfolio strategy, capital allocation | Keeps going past the spin-off ("why was that business low-growth?"); often ends at `evidence-frontier` |

## Showcase cases (3M first, then the GE family; figures checked against SEC XBRL on 2026-10-24 and 2026-10-22)

The `#` is the input example's number ([input_examples/](../input_examples/)). All four run offline in sample mode.

| # | Question | Data | Reconciliation | Root-cause type |
|---|---|---|---|---|
| 4 (lead) | Why did 3M's **2023** revenue fall between its 2024 and 2025 10-Ks? | $32,681M → $24,610M (Δ $8,071M) | Solventum 2023 revenue $8,197M (found as the filer whose Form 10 names 3M), residual −$126M (over-explained) | Spin-off |
| 1 | Why did GE's **2022** revenue fall between the 2023 and 2024 10-Ks? | $76,555M → $58,100M (Δ $18,455M) | GE HealthCare 2022 revenue $18,341M, residual $114M | Spin-off |
| 2 | Why did GE's **2023** revenue fall between the 2024 and 2025 10-Ks? | $67,954M → $35,348M (Δ $32,606M) | GE Vernova 2023 revenue $33,239M, residual −$633M | Spin-off |
| 3 | Why did GE post a **$22.4B net loss in 2018**? | NetIncomeLoss −$22,355M; GoodwillImpairmentLoss $22,136M | Points to Power, then to the 2015 Alstom acquisition | Impairment |

Why 3M first: one restatement, one spin-off, one reconciliation a reader can check in a minute, and Loop B finds the spun-off company from filing evidence (Solventum's name shares nothing with 3M's). Why GE as further examples: one parent with several subsidiaries that each file with the SEC, a well-known break-up story, and more than one root-cause type.

## UX principles

- An analyst reads a **report, not a chat**. Every link shows its evidence and label.
- **Branch, don't restart.** Pick any node and ask a different question or switch asker profile.
- The trace shows every decision, including the human-in-the-loop gates.
