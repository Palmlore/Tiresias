# Progress checklist

The single list to work from. Every event requirement is merged and de-duplicated, with its source cited. Tick an item only when its evidence exists, and update this file in the same commit as the work.

`[x]` done (evidence linked) · `[~]` in progress · `[ ]` not started

Updated **2026-10-22 22:15 AEDT** (build day 1). Deadline **27 Oct 23:45 UTC = 28 Oct 10:45 AEDT**.

**Source keys**

| Key | File | Location |
|---|---|---|
| OR | Official rules (draft copy) | `../docs/rules_and_requirements/official-rules.md` |
| SR | Submission requirements | `…/submission-requirements.md` §8 |
| SC | Pre-submission self-check | `…/disqualification-checklist.md` |
| TE | Technical execution guide | `…/technical-execution-requirements.md` §13 |
| AI | AI usage policy | `…/ai-usage-policy.md` §13 |
| DV | Demo video guide | `../docs/demo_and_submission_resources/demo-video-guide.md` §10 |
| SF | Submission form preview | `…/submission-form-preview.md` §5 |
| SG | Participant setup guide | `../docs/participant_setup_guide.pdf` |
| SP | Sponsor folders | `../docs/sponsors/` |
| MP | Mentor playbook | `…/mentor-playbook.md` §11 |

---

## A. Eligibility, team, registration
- [x] Registered on HackOS before 22 Oct 23:45 UTC, aged 16+, one account (OR §3)
- [x] Solo team (1–5 members); member on one submission only (OR §5)
- [ ] Roster final **before** creating the first submission, because the team locks at that point (OR §5.4, SF §2)
- [ ] One primary track selected on HackOS: *The Agent That Can Explain Why* (OR §6)

## B. Sponsor technology (required, and scored under Sponsor Tech, 10 pts)
- [~] **Zetaris integrated and load-bearing.** Every claim is a Zetaris SQL query (OR §8.1, §16; SC §10)
  - [x] Local Zetaris running; `verify.sh` 9/9; measured ([measurements/zetaris-local.md](measurements/zetaris-local.md))
  - [x] GE vs GE HealthCare reconciliation runs inside Zetaris (residual $114M), via `scripts/zetaris_probe.py`
  - [ ] Agent's Zetaris adapter (timeout, retry, fallback to cached rows) (TE §9)
  - [ ] Loop B registers a discovered entity's source at runtime from `catalog/sources.yaml`
  - [ ] `zetaris/sql/` holds the registration SQL for the showcase entities
- [~] **Meterless Build rule: at least one engine in a working, user-facing feature, with test evidence** (SG §3.2)
  - [x] H-MEM in `services/cognition`; official conformance 13/13 (`npm run conformance`); 6 tests
  - [ ] User-facing feature: cold → warm → purge → cold memory demo through `/run`
- [ ] **Meterless Power rule: Nightshift is the primary model provider**; real requests go through the Runtime (SG §4)
  - [ ] Nightshift installed in an isolated macOS VM; hotspot mode; reachable from the host and from containers
  - [ ] Agent model client (OpenAI-compatible, `meterless-app` and `meterless-scope` headers, 180 s timeout, offline-state handling)
- [ ] **NVIDIA (optional; DGX Spark award):** a Nemotron lane pinned in Nightshift that materially powers a feature (e.g. a deep critic); evidence of which model served each request (SG §4.3–4.7; event page)
- [~] **Developed in Cursor.** Repo opened and edited in Cursor; describe exactly how it was used (OR §8.1; SP cursor).

## C. Agent system (Technical 20 · Innovation 15)
- [ ] ≥2 named agents with distinct roles that interact more than once (OR §7.3; SC §9)
- [ ] Critique / validation / retry / branching events in the traces (SC §9; AI §13)
- [ ] Critic implements [causal-criteria.md](causal-criteria.md): breakdown vs causal labels, intervention test, share explained, residual
- [ ] Loop A (known sources) and Loop B (evidence frontier) both work on case 01
- [ ] Six end states implemented, with the asker-relative stopping rule
- [ ] Human-in-the-loop gates with `auto` / `interactive` / `scripted` modes, each decision traced (ADR-0006)
- [ ] Branching from a node of a finished run (`/runs/{id}/branch`)
- [ ] Memory gate and lesson write-back from the agent to cognition; recalled lessons seed the hypothesiser; the critic gives feedback on them

## D. Running and reproducibility (any failure here can lose the whole Technical and Demo score)
- [~] Programmatic entry point: `POST /run`, JSON in and out, bound to `0.0.0.0`, structured errors (TE §2; SC §5) (contract drafted; `/run` returns 501)
- [~] Docker: `docker compose up --build` from a clean clone; nothing installed at runtime; no local GPU (TE §3–4) (both services build)
- [ ] **`SAMPLE_MODE=true` runs real agent logic with an empty `.env`**, with no network or keys (SC §7, §14 step 9; ADR-0007)
- [ ] Runs are bounded: a run finishes in ≤10 min, with timeouts and capped loops (TE §5)
- [ ] Failures return a controlled response, not a hang or a stack trace (TE §5; SC §5)
- [ ] Full validation flow from a fresh clone (SC §14), first on **day 4**, again before submitting

## E. Data, secrets, integrity
- [x] `.env.example` with placeholders only; `.env` gitignored; secrets scan passes (`scripts/check_secrets.sh`) (OR §11; SC §11)
- [x] Data sources, licences and redistribution notes documented ([data-sources.md](data-sources.md)) (OR §11; TE §7)
- [ ] `data/sample/` slice with provenance (URL, fetch time, SHA-256) and `scripts/fetch_sample.py`; repo stays small (TE §7)
- [ ] No hardcoded outputs: outputs differ meaningfully across the 3 inputs; `output_examples/` is never read at runtime (SC §8; AI §4.1)
- [ ] Traces generated by the run, never hand-written, matching the input processed (SC §9; AI §4.2)
- [ ] No keys in the video, deck, screenshots or logs (SC §11; DV §10)

## F. Repository documentation
- [x] Public GitHub repo (OR §10.1; SR)
- [~] README: setup, usage, dependencies, agent roles, examples, limitations, track named (SR §3.3; SC §3) (written; quickstart not runnable yet)
- [~] AI and model usage disclosure ([ai-usage.md](ai-usage.md)) (AI §6); third-party code and licences ([NOTICE](../NOTICE))
- [x] Pre-existing components declared: none (OR §8.2; SF §3.8)
- [ ] ≥3 sample inputs **and** matching real outputs (inputs done; outputs after real runs) (OR §10.2; TE §13)
- [ ] `logs/samples/` traces from real runs, referenced by the outputs (TE §8)
- [~] Limitations stated honestly ([limitations.md](limitations.md)) (SC §13)
- [x] `AGENTS.md` handover for coding agents

## G. Demo video (Demo, 15 pts): 1–4 min, target 2:30–3:30
- [ ] Storyboard final ([demo-plan.md](demo-plan.md)): problem, solution, agents, live run, sponsor tech in the trace, output and impact (DV §2–3)
- [ ] Shows a real run, at least one collaboration moment (critic rejects or sends back), and Zetaris + Meterless doing real work (DV §4)
- [ ] Memory demo: cold vs warm step counts, plus the purge event (ADR-0005)
- [ ] Clear audio, readable text, 1080p, no secrets on screen (DV §5, §10)
- [ ] Uploaded; link opens in a private window; backup copy; link in README and `metadata.json` (DV §7–8)

## H. Slide deck: all 8 points required (SR §3.2; SF §3.6)
- [ ] 1 Problem and users · 2 Solution · 3 Agent workflow and architecture · 4 Tech stack
- [ ] 5 How Zetaris and Meterless were used · 6 What was unique about the sponsor tech use
- [ ] 7 Product visuals · 8 Future enhancements
- [ ] Consistent with the repo and demo; no claims the code doesn't support

## I. Sponsor tech explanation and metadata (Sponsor Tech, 10 pts)
- [~] [sponsor-technology.md](sponsor-technology.md) covers Zetaris, Meterless, Cursor and NVIDIA, plus "what was unique" (SR §3.4) (drafted; finalize from what ships)
- [~] `metadata.json` valid against the community schema; statuses honest (SP; `scripts/validate_metadata.sh`) (valid; Meterless `in-progress`)
- [ ] Switch to the official metadata format if the organizers publish one (STATUS #1)

## J. Submission (HackOS)
- [ ] Project name, one-sentence description and paragraph prepared (SF §3.1)
- [ ] All four components linked: video, deck, repo URL, sponsor explanation (OR §10.1)
- [ ] README, deck and demo name the same track (SF §5)
- [ ] Submitted **hours** before 27 Oct 23:45 UTC; update allowed until the deadline (OR §10; SC §15)

## K. Delivery milestones (MP §11; adapted)
| When (AEDT) | Gate | Status |
|---|---|---|
| Thu 22 Oct (day 1) | Zetaris up and measured; repo scaffolded; cognition service working | [x] |
| Fri 23 Oct | Agent: Zetaris adapter, tracing, Loop A on case 01 in sample mode | [ ] |
| Sat 24 Oct | Loop B live; critic rules; Nightshift client; 3 cases end to end | [ ] |
| Sun 25 Oct (day-4 checkpoint) | Runs locally **and** in Docker; sample mode with an empty `.env`; memory demo; fresh-clone validation. More than 3 gaps means cut scope. | [ ] |
| Mon 26 Oct | Freeze architecture. Branching and asker profiles polish; outputs and traces committed; README final | [ ] |
| Tue 27 Oct | Record the demo, finish the deck and sponsor explanation | [ ] |
| Wed 28 Oct, before 06:00 AEDT | **Submit** (deadline 10:45 AEDT) | [ ] |
