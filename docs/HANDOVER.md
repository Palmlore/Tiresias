# Session handover

Rewrite this at the end of each session; the history lives in git. The standing rules are in [../AGENTS.md](../AGENTS.md) and the full task list is in [CHECKLIST.md](CHECKLIST.md).

**Last session:** Thu 22 Oct 2026 (build day 1), ended about 22:30 AEDT.
**Next session:** Fri 23 Oct (day 2). Milestone: **agent service + Loop A on case 01 in sample mode.**
**Deadline:** 27 Oct 23:45 UTC = **28 Oct 10:45 AEDT**. Target submitting by 28 Oct 06:00 AEDT.

---

## 1. Start of session

```bash
# Open the session in the build repo, with the event reference pack readable:
cd ~/Repos/hackathon2026/submission && claude --add-dir ../docs      # or open this folder in Cursor

# Containers were STOPPED (not removed) at the end of day 1. Start them:
cd ~/Repos/hackathon2026/zetaris/zetaris-platform && docker compose start && ./verify.sh   # wait for 9/9
cd ~/Repos/hackathon2026/submission && docker compose start
curl -s localhost:8100/health        # cognition: lessons should be 0 (smoke lesson purged)
python3 scripts/zetaris_probe.py     # optional: re-measures; "exists" errors on re-run are expected
```

Environment facts:
- Docker Desktop: 10 GB memory, 6 CPUs, 2 GB swap, Rosetta on. 24 GB machine.
- Zetaris REST: `localhost:8888`. Registered objects persist in its volume:
  - database `TIRESIAS_SEC`, container `tiresias`;
  - raw tables `ge_revenues_raw` (companyconcept), `gehc_revenues_raw` (companyconcept, **returns empty units**), `ge_facts_raw`, `gehc_facts_raw` (companyfacts, cached);
  - views `tiresias.ge_revenues`, `tiresias.ge_revenue_cf`, `tiresias.gehc_revenue`.
- `.env` exists (gitignored). It contains `ZETARIS_API_KEY` and `SEC_USER_AGENT=Tiresias hao@palmlore.com`. **Nightshift values are still placeholders.**
- Zetaris admin is still on the bundle's default password. The user is changing it.

## 2. State at hand-off

| Area | State |
|---|---|
| Repo scaffold, docs, ADRs, rubric map, checklist, AGENTS.md | Done, pushed (latest commit `d4f5fd5`) |
| Zetaris | Running locally; measured ([measurements/zetaris-local.md](measurements/zetaris-local.md)); reconciliation join works (GE vs GEHC residual $114M) |
| `services/cognition` | **Working.** H-MEM behind REST; conformance 13/13; 6 tests; Docker persistence verified |
| `services/agent` | **Stub only.** `/health`; `/run` returns a structured 501 |
| Nightshift | Not running. Plan: isolated macOS VM (see §4) |
| Sample data, outputs, traces, demo, deck | Not started |

## 3. Decisions made in conversation (not recorded elsewhere)

1. **Nightshift's role:** it is the mandatory gateway for every agent model call (the "Power" rule) and our route to Nemotron for the DGX Spark award. It is **not** needed in sample mode. The **web edition can't be used** because it has no app hotspot. Lanes:
   - `meterless/fast` (Nemotron Lightning): hypothesiser, gap analyst, reporter;
   - `meterless/deep` (Nemotron Super): critic.

   Pin Nemotron lanes, because `meterless/auto` may route to other models.
2. **Recalling a lesson for the "critic rejects a misapplied lesson" demo** (GE 2018 impairment): recall with a broad query such as "why did GE's reported figure change". The impairment question scores 0.19, below the 0.25 threshold. Don't lower the threshold globally.
3. **Judges** (5 of 6 published): Michael Hay (CTO, **Zetaris**), Michael Armentano (SpaceXAI), Sam Cummings, Shestakov Vladyslav, Remus Ranca (all GenAI Works).
   - The Zetaris CTO will scrutinize Zetaris depth. Using the semantic layer's data-quality rules is a candidate stretch goal.
   - GE neutrality: no conflicts among current employers; past employment is unchecked.
4. **Session location:** work from `submission/`. Sessions started in `../docs` trigger the cross-repo write hook.
5. **Builder name in `metadata.json`:** "Hao Li", confirmed by the user.
6. **Possible addition:** the Meterless **Markovian** engine (each why as one bounded step), because Meterless's `examples/memory-compounding-research` already wires H-MEM into Markovian. Only if time allows. Re-clone at commit `1742dec` and vendor it unmodified, the same way as H-MEM.

## 4. User-owned tasks (in progress)

- [ ] **Nightshift VM** (Parallels):
  1. New → Download macOS; 6 GB memory, 4 CPUs, 64 GB disk, Shared Network.
  2. **Snapshot "clean"** before installing.
  3. Install from `~/Downloads/Nightshift-Setup-mac-m5.dmg` (SHA-256 `823e0ee0…2e2a4`; ad-hoc signed, bundles llama.cpp and the `meterlessd` daemon).
  4. In Nightshift: NVIDIA key under Setup → Providers; a Nemotron lane; hotspot mode on.
  5. From its Console, put `METERLESS_BASE_URL` (the VM's IP, e.g. `http://10.211.55.x:8088/v1`) and `METERLESS_RUNTIME_TOKEN` into `.env`.
  6. Then the agent tests reachability **from the host and from containers**. Containers reaching the VM's IP is unverified.
  7. If the installer refuses to run in a VM, note it in STATUS and ask Help Meterless.
- [ ] Change the Zetaris admin password at localhost:3000.
- [ ] HackOS: the sixth judge; whether Cursor is still judged; an official `metadata.json` format; how to claim the NVIDIA credit; ask Help Meterless about a notarized or container build.
- [ ] Find a way to run Opus through Cursor in agent mode (Cursor credit is used up).

## 5. Leftover tasks for the agent, in order

Full list with sources: [CHECKLIST.md](CHECKLIST.md) sections B–K. The first vertical slice for **day 2**:

**5.1 Sample data (`data/sample/`, `scripts/fetch_sample.py`).** It doesn't exist yet. The day-1 probe JSON was in a session scratchpad and is gone.
- Fetch and trim to the needed concepts:
  - GE `companyfacts`: Revenues, NetIncomeLoss, GoodwillImpairmentLoss, discontinued-ops concepts;
  - GE `submissions`: 8-K rows with `items`, around 2022-11 to 2023-02 and 2024-03 to 2024-04;
  - GE HealthCare and GE Vernova `companyfacts`: the RevenueFromContractWithCustomerExcludingAssessedTax concept;
  - the ticker-map subset (GE, GEHC, GEV).
- Record source URL, fetch time (UTC) and SHA-256 for each file. Keep the slice small; the SEC licence is ambiguous, so no bulk copies.
- Use `SEC_USER_AGENT`. Note: Python's `urllib` failed TLS verification on this Mac (certificate store); `curl` works. Use `httpx` (in the agent's dependencies) or certifi.

**5.2 Agent service skeleton (`services/agent/src/tiresias_agent/`).**
- `trace.py`: JSONL to stdout and `logs/trace-<run_id>.jsonl`, same schema as `services/cognition/src/trace.ts`.
- `models.py`: pydantic models matching `contracts/openapi/agent.yaml` (`RunRequest`, `RunResult`, `Link`).
- `adapters/zetaris.py`: `run_sql()` (live: REST; sample: cached rows keyed by query template), with timeout, retry and logged fallback.
- `adapters/cognition.py`: recall, remember, feedback, purge over HTTP, propagating `X-Run-Id`.
- `adapters/meterless.py`: later (needs Nightshift). In sample mode the agents are rule-based.

**5.3 Loop A + Loop B on case 01, sample mode, rule-based.** Target trace:
1. Framer: GE, Revenues, FY2022, 10-K 2023-02-10 vs 2024-02-02.
2. Analyst: three values for FY2022, so a restatement is detected (`breakdown`).
3. Hypothesiser: candidates are disposition/spin-off, accounting change, error correction. Seeded by recalled lessons when memory is warm.
4. Analyst + critic: an 8-K with item 2.01 near the change marks the disposition hypothesis as the frontier. The known sources can't test it further, so Loop B starts.
5. Gap analyst → source scout: the spun-off entity is resolved via the ticker map → GEHC (CIK 0001932393).
6. Onboarder: register GEHC `companyfacts` (live) or load the cached slice (sample). Source gate `auto`.
7. Analyst reconciles: Δ $18,455M vs GEHC $18,341M, residual $114M. Critic labels it `causal`, `partially-evidenced`; share explained ≈ 99.4%.
8. Stop rule for the analyst asker → `actionable`: "re-base on continuing ops; residual open".
9. Memory gate `auto` → store the lesson. Reporter → `RunResult` plus the trace.

**5.4 Then:** cases 02 (Vernova, warm memory) and 03 (2018 impairment) → the memory demo (cold / warm / purge / cold) → the Nightshift client and live mode → asker profiles and branching → `output_examples/` and `logs/samples/` from real runs → fresh-clone validation (**day-4 checkpoint, Sun 25 Oct**) → README, deck, video.

## 6. Risks to watch
- **Nightshift in a VM is unproven.** If it fails, live-mode model calls have no compliant provider. Escalate early to Help Meterless. Sample mode and the rest of the build don't depend on it.
- **Memory budget:** Docker 10 GB + VM 6 GB + macOS/apps is about 24 GB. Shut the VM down when not testing live mode.
- **Scope:** if more than 3 day-4 checkpoint items are missing on Sun 25 Oct, cut stretch goals (Markovian, semantic-layer DQ, branching UI) before core items.
