# assets/

Images and the slide deck for the submission. Rows marked **[TBC]** depend on something not built or recorded yet (the UI, the final run, the video): skip them for now. Everything here is our own work or generated for this project, and each file's source and licence is listed in the inventory below, because an unlicensed asset disqualifies a submission. The demo video is **not** stored here (see [Video](#video)).

## Layout

| Path | What | Format and size |
|---|---|---|
| `brand/logo.svg`, `brand/logo-1024.png` | Tiresias mark (square) | SVG + 1024×1024 PNG, transparent background |
| `brand/cover.png` | Project image: HackOS submission card, README header, video thumbnail | 1920×1080 PNG (16:9), ≤ 1 MB |
| `brand/social.png` | Link preview (GitHub social preview) | 1280×640 PNG, ≤ 1 MB |
| `characters/{tiresias,sphinx,you}.svg` | The three characters, as icons for the UI, deck and video | SVG (+ 512×512 PNG each), one colour family |
| `diagrams/architecture.svg` | Loop A / Loop B architecture (redrawn from [docs/architecture.md](../docs/architecture.md)) | SVG + 1920×1080 PNG |
| `screenshots/NN-<what>.png` **[TBC]** (needs the UI and the final runs) | Real captures of the UI, terminal and trace, numbered in demo order | PNG, ≥ 1600 px wide, ≤ 1 MB each |
| `deck/tiresias-deck.pdf` | The slide deck, as submitted. Draft it now; slide 7 (product visuals) is **[TBC]**, and the sponsor-status wording is re-checked at freeze | PDF, 16:9, fonts embedded, ≤ 20 MB |
| `deck/source/` | Deck source (Marp Markdown, HTML or the tool's native file) so the PDF can be rebuilt | text where possible |
| `video/poster.png` **[TBC]** (needs the recorded video) | Video poster frame (may equal `brand/cover.png`) | 1920×1080 PNG |
| `video/tiresias-demo.en.srt` **[TBC]** (needs the recorded video) | Captions for the demo video | SRT |

Keep the whole folder under about 30 MB; no Git LFS. The agent image never includes `assets/` (the root `.dockerignore` is an allow-list).

## Video

- Upload, don't commit: GitHub refuses files over 100 MB and every clone would download it. Host it unlisted on YouTube (or wherever HackOS asks), and check that the link opens in a private window.
- Link it in the README header table and in `metadata.json` → `submission.demo_video`. The deck PDF goes in `submission.slide_deck` as its GitHub URL.
- Keep the master file and a backup copy outside this repo.
- Spec: 1–4 min (target about 3:30), 1080p, 16:9, clear narration, terminal font enlarged, browser zoom 110–125 %, no keys on screen (`.env` closed; masked values only).

## Rules for every asset

1. **Real numbers only.** Every figure on a slide, screenshot or image comes from a real run of this code (a committed file under `output_examples/` or `logs/samples/`, or a trace from that day's run), and the inventory says which. Known-good figures today: GE's 2022 revenue as reported in three 10-Ks, $76.6B → $58.1B → $29.1B (`data/sample/`); the GE HealthCare reconciliation, $18,455M vs $18,341M, residual $114M or 0.6 % ([docs/measurements/zetaris-local.md](../docs/measurements/zetaris-local.md), test `test_restatements_continue_into_loop_b_and_reconcile`).
2. **No overclaiming.** A slide may show a feature as working only if the README and `metadata.json` say it works. Memory, Nightshift models and the UI are shown as "planned" or left out until they ship. [docs/CHECKLIST.md](../docs/CHECKLIST.md) is the reference.
3. **No secrets, no personal data.** Masked values only (`ZETARIS_API_KEY=***`); no email addresses, tokens or hostnames from `.env` in a screenshot.
4. **No third-party logos or likenesses** unless the owner's brand guidelines allow it: name GE, Zetaris, Meterless and NVIDIA in text. No depiction of a real person. Classical artwork only if it is in the public domain, with its source recorded.
5. **AI-generated assets are disclosed**: record the tool and model in the inventory, and add a line to [docs/ai-usage.md](../docs/ai-usage.md).

## Visual direction

- **Characters, introduced gradually and never more than three:** **Tiresias**, the blind seer and the agent's voice (veiled eyes, a staff; calm, deliberate). **The Sphinx**, the critic who asks "Why?" and guards uncomfortable truths. **You**, the analyst at the gates who judges and decides. Simple, flat icons that read at 32 px; no gore, no mythological clutter.
- **Tone:** evidence over oracle. The tagline: *"Tiresias named the cause first, but had no evidence and was not believed. This Tiresias brings the evidence."*
- **Palette:** a dark ink background, one warm accent (bronze or gold) for Tiresias, one cool accent for the Sphinx, and neutral text; WCAG AA contrast for any text. Use the same palette in the deck, diagrams and UI.
- **Type:** one sans-serif family for text, one monospace for SQL and trace lines; deck body text ≥ 24 pt.

## Deck: the eight required points (SR §3.2)

One slide or more per point, in this order, about 10–12 slides in total:

1. The problem and target users (financial analysts; GE's restated revenue as the hook)
2. The solution (an evidence-backed "5 Whys": ask why, get a why-tree where every link is SQL plus rows)
3. The agent workflow or architecture (Loop A drill-down, Loop B source discovery, critic, human gates; `diagrams/architecture.svg`)
4. The technical stack (Python/FastAPI agent, TypeScript H-MEM service, Zetaris Lightning SQL, Docker)
5. How Zetaris and Meterless were used (status wording **[TBC]** at feature freeze, 26 Oct; from [docs/sponsor-technology.md](../docs/sponsor-technology.md), at the status it states)
6. What was unique about the sponsor tech use (registering a new Zetaris source while the agent runs; the counterfactual test on the returned rows)
7. Product visuals (`screenshots/`) **[TBC]**: leave a placeholder slide; never mock up a UI as if it were real
8. Future enhancements (from [BACKLOG.md](../BACKLOG.md) and the open CHECKLIST items)

Plus a title slide (`brand/cover.png`) and a closing slide with the repo URL and track (*Reasoning Architecture*).

## Inventory

Add one row per file when it lands.

| File | Source (tool, model, or real run + date) | Figures shown and where they come from | Licence |
|---|---|---|---|
| | | | |
