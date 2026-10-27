# Setup

Two ways to run Tiresias locally:

- **Demo mode** (sample mode, the default): offline, no keys, one command. This is what a judge needs.
- **Live mode**: queries a local Zetaris and calls Meterless Nightshift models. It needs both services running on your machine.

The no-install alternative is the hosted demo (sample mode only), linked from the [README](../README.md#quickstart-judges).

## Demo mode

```bash
git clone https://github.com/Palmlore/Tiresias && cd Tiresias
./scripts/setup.sh
```

What `scripts/setup.sh` does, in order (it stops with `setup: FAIL: ...` and a non-zero exit at the first problem):

1. Checks that `docker` and `curl` are installed, that the Docker daemon is running, and that `docker compose version` is 2.24 or later (`compose.yaml` uses `env_file` with `required: false`).
2. Creates `.env` from `.env.example` **only if `.env` does not exist**. An existing `.env` is never overwritten; the script says which case applied. The shipped `.env.example` has `SAMPLE_MODE=true` and needs no keys.
3. Checks that the agent and cognition ports are free (skipped when this Compose project is already running). If one is taken it names the port and the variable to change.
4. Runs `docker compose up --build -d`.
5. Waits for `GET http://localhost:<AGENT_PORT>/health`, printing progress, for up to 300 s (`SETUP_HEALTH_TIMEOUT`).
6. Sends the lead example, [input_examples/04_3m_2023_revenue_restated.json](../input_examples/04_3m_2023_revenue_restated.json), to `POST /run` and prints its `end_state` (`actionable` in sample mode), the first 120 characters of the recommended `action` and the trace file under `logs/`.
7. Prints the web UI URL, `http://localhost:<AGENT_PORT>`.

Then follow the [README Quickstart](../README.md#quickstart-judges) for the other examples, the web UI and the API. Stop the stack with `docker compose down`.

### Ports

| Service | Default | Override |
|---|---|---|
| agent (API and web UI) | 8000 | `AGENT_PORT` in `.env` or the shell |
| cognition (H-MEM memory) | 8100 | `COGNITION_PORT` in `.env` or the shell |

A value in the shell wins over `.env`, as in Compose's own interpolation. `COMPOSE_PROJECT_NAME` is passed through to `docker compose`, so a second copy can run beside the first:

```bash
# .env: AGENT_PORT=8030 and COGNITION_PORT=8130
COMPOSE_PROJECT_NAME=tiresias-second ./scripts/setup.sh
docker compose -p tiresias-second down
```

### Troubleshooting

| Message | Fix |
|---|---|
| `the Docker daemon is not running` | Start Docker Desktop (or `sudo systemctl start docker` on Linux). |
| `docker compose ... is too old` / `not found` | Install Docker Desktop or the Compose plugin, 2.24 or later. |
| `port 8000 (AGENT_PORT) is already in use` | Stop what holds the port, or set `AGENT_PORT` (or `COGNITION_PORT`) to a free port in `.env`. |
| `did not answer /health in 300s` | `docker compose logs agent`; a slow first build can also be waited out with `SETUP_HEALTH_TIMEOUT=600`. |
| `unexpected reply from POST /run` | The reply's first 300 characters are printed; `docker compose logs agent` and the trace under `logs/` show the rest. |

## Live mode

Live mode reads SEC data through Zetaris (every read, discovery included) and, at depth `thorough` or `deep`, calls Nemotron models through Nightshift. What each sponsor does in the system: [sponsor-technology.md](sponsor-technology.md). Endpoints and headers: [agents-reference.md](agents-reference.md#external-services-live-mode-only-sample-mode-needs-none).

### What you need

- **Zetaris**: the local Docker bundle, installed as in the event's participant setup guide (see also [zetaris.github.io/getting-started](https://zetaris.github.io/getting-started)). Give Docker about 8 GB of memory and 4 CPUs; REST listens on `:8888`. Copy an API key from the Zetaris UI. Quirks and the SQL templates: [zetaris/README.md](../zetaris/README.md).
- **Meterless Nightshift**: the desktop app with its Runtime in hotspot mode and NVIDIA (Nemotron) added as a provider in its Providers tab. The NVIDIA key stays in Nightshift, never in `.env`. Copy the Runtime URL and token from its Console.
- `uv` and Python 3.11+ on the host (for `bootstrap`, and for running the agent on the host, below).

### Fill `.env`

`cp .env.example .env` if you have not, then set (names and meanings from [.env.example](../.env.example)):

| Variable | Meaning |
|---|---|
| `SAMPLE_MODE` | `false` for live mode (a request can also ask for it with `"options": {"sample_mode": false}`) |
| `ZETARIS_REST_URL` | Zetaris REST base, e.g. `http://localhost:8888/api/v1.0` from the host, `http://host.docker.internal:8888/api/v1.0` from a container |
| `ZETARIS_ORG_ID` | Zetaris organisation id (usually `1`) |
| `ZETARIS_API_KEY` | Zetaris API key |
| `METERLESS_BASE_URL` | Nightshift Runtime URL, ending in `/v1` |
| `METERLESS_RUNTIME_TOKEN` | Nightshift Runtime token (bearer) |
| `METERLESS_MODEL_FAST`, `METERLESS_MODEL_DEEP` | A lane (`meterless/fast`, `meterless/deep`: Nightshift picks the model) or, on Nightshift 1.2.4, a model id listed by `GET /v1/models` |
| `SEC_USER_AGENT` | `Name contact@example.com`, sent to SEC under its fair-access policy; `bootstrap` needs it |

The other variables (budgets, timeouts, the Zetaris warm and cache limits) have working defaults; `.env.example` explains each one.

### Register the sources

```bash
cd services/agent && uv sync
set -a && . ../../.env && set +a     # export .env; quote values with spaces: SEC_USER_AGENT="Name you@example.com"
ZETARIS_REST_URL=http://localhost:8888/api/v1.0 uv run python -m tiresias_agent.adapters.zetaris bootstrap
```

`bootstrap` registers the GE showcase (GE, GE HealthCare, GE Vernova: examples 01 to 03) and the SEC ticker map; it is safe to run twice ([zetaris/README.md](../zetaris/README.md#apply)). Any other company, 3M and Solventum for example 04 included, is registered during a live run, behind the source gate.

### Check readiness

```bash
./scripts/setup.sh --check-live                 # reads .env; starts nothing
./scripts/setup.sh --check-live --env-file PATH # another env file
```

One line per check, `OK`, `MISSING`, `FAIL` or `NOTE`, each with a fix hint: `.env` present; `SAMPLE_MODE`; the three Zetaris variables and `SELECT 1` over `POST /sql-editor/sqls/run` (401/403 = wrong key or org id; no answer = bundle down; another status = engine down); whether bootstrap's `TIRESIAS_SEC.ge_facts_raw`, `gehc_facts_raw` and `gev_facts_raw` are registered (`SHOW LIGHTNING TABLES TIRESIAS_SEC`), plus a note on 3M and Solventum; the two Nightshift variables and `GET {METERLESS_BASE_URL}/models` (401/403 = wrong token; no answer = Runtime down), and whether pinned model ids appear in that list; `SEC_USER_AGENT`. It ends with `live mode ready` (exit 0) or `live mode not ready: N issue(s)` (exit 1). Secret values are never printed, only whether they are set and the host of each URL. A URL naming `host.docker.internal` (the in-container name) is checked at `localhost` instead, and the report says so. For a model round trip: `cd services/agent && uv run python -m tiresias_agent.adapters.nightshift probe --env-file ../../.env`.

### Run the agent live

With `SAMPLE_MODE=false` and container-reachable URLs in `.env`, `docker compose up --build` runs live. If the container cannot reach the Nightshift Runtime (for example a Nightshift VM on a host-only network, as in our own setup), run the agent on the host with host URLs; the cognition service can stay in Compose:

```bash
docker compose up -d cognition
cd services/agent
set -a && . ../../.env && set +a
SAMPLE_MODE=false \
ZETARIS_REST_URL=http://localhost:8888/api/v1.0 \
METERLESS_BASE_URL=http://<runtime-host>:8088/v1 \
COGNITION_URL=http://localhost:8100 \
AGENT_PORT=8020 \
uv run python -m tiresias_agent.app          # API and web UI on http://localhost:8020
```

The app reads its settings from the environment only, hence the `set -a` line. Or run one question from the CLI: `uv run python -m tiresias_agent.orchestrator ../../input_examples/04_3m_2023_revenue_restated.json --live` (`--depth thorough|deep` adds the model lanes; see [AGENTS.md](../AGENTS.md#commands)).

### After a Zetaris restart

Zetaris loses its table cache on restart, and the first uncached query can pass the 20 s source timeout. A live API re-caches the `ZETARIS_WARM_ENTITIES` tables in the background on start (`ZETARIS_WARM_ON_START=true`; progress in `GET /health` as `zetaris_cache`). By hand:

```bash
cd services/agent && set -a && . ../../.env && set +a
ZETARIS_REST_URL=http://localhost:8888/api/v1.0 uv run python -m tiresias_agent.adapters.zetaris warm
```

Details and limits (heap, the table cap): [zetaris/README.md](../zetaris/README.md#apply).
