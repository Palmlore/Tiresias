#!/usr/bin/env bash
# One-command setup for judges (docs/setup.md).
#
#   ./scripts/setup.sh                 demo (sample mode): check prerequisites, create .env if absent,
#                                      docker compose up --build -d, wait for /health, run example 04
#   ./scripts/setup.sh --check-live    live-mode readiness report; starts nothing
#   ./scripts/setup.sh --help
#
# Needs bash, docker (Compose >= 2.24) and curl; python3 is optional (nicer JSON parsing).
# Secret values are never printed: only whether a variable is set, and hosts without credentials.
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="$ROOT/.env"
MODE=demo
HEALTH_TIMEOUT="${SETUP_HEALTH_TIMEOUT:-300}"   # seconds to wait for GET /health after compose up
CURL_TIMEOUT="${SETUP_CURL_TIMEOUT:-5}"         # per check in --check-live
EXAMPLE="input_examples/04_3m_2023_revenue_restated.json"

usage() {
  sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'
  cat <<'EOF'
Options:
  --check-live       report live-mode readiness (Zetaris, Nightshift, SEC_USER_AGENT); exit 1 if not ready
  --env-file PATH    env file to read (default: <repo>/.env); demo mode always uses <repo>/.env
  -h, --help         show this help
Environment: AGENT_PORT / COGNITION_PORT (or in .env) change the published ports;
SETUP_HEALTH_TIMEOUT (default 300 s); COMPOSE_PROJECT_NAME is passed through to docker compose.
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --check-live) MODE=live ;;
    --env-file) [ $# -ge 2 ] || { echo "--env-file needs a path" >&2; exit 2; }; ENV_FILE="$2"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown option: $1 (see --help)" >&2; exit 2 ;;
  esac
  shift
done

die() { echo "setup: FAIL: $*" >&2; exit 1; }

# envget KEY: the last KEY=value in the env file, inline " # comment" and surrounding quotes removed.
# Parsed, never sourced, so nothing in the file is executed.
envget() {
  [ -f "$ENV_FILE" ] || return 0
  grep -E "^[[:space:]]*$1[[:space:]]*=" "$ENV_FILE" | tail -n 1 | sed -e 's/^[^=]*=//' \
    -e 's/[[:space:]]#.*$//' -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' \
    -e 's/^"\(.*\)"$/\1/' -e "s/^'\(.*\)'\$/\1/"
}

# ---------------------------------------------------------------- live-mode readiness report
ISSUES=0
ok()      { printf '  OK       %-24s %s\n' "$1" "$2"; }
missing() { printf '  MISSING  %-24s %s\n' "$1" "$2"; ISSUES=$((ISSUES + 1)); }
fail()    { printf '  FAIL     %-24s %s\n' "$1" "$2"; ISSUES=$((ISSUES + 1)); }
note()    { printf '  NOTE     %-24s %s\n' "$1" "$2"; }

# is_set NAME VALUE PLACEHOLDER: set and not the .env.example placeholder
is_set() { [ -n "$2" ] && [ "$2" != "$3" ]; }

# http_auth URL TOKEN [JSON_BODY]: prints the HTTP status (000 = unreachable); body in $BODY_FILE.
# The token goes to curl on stdin (-K -), so it never appears in the process list.
http_auth() {
  local url="$1" token="$2" body="${3:-}"; local data=()
  [ -n "$body" ] && data=(--data "$body")
  {
    printf 'header = "Authorization: Bearer %s"\n' "$token"
    [ -n "$body" ] && printf 'header = "X-Org-ID: %s"\nheader = "Content-Type: application/json"\n' "$ZORG"
  } | curl -s -o "$BODY_FILE" -w '%{http_code}' --connect-timeout 2 --max-time "$CURL_TIMEOUT" \
        -K - ${data[@]+"${data[@]}"} "$url" 2>/dev/null || true
}

# uuid: Zetaris refuses a queryId that is not a UUID (HTTP 400)
uuid() {
  { uuidgen 2>/dev/null || cat /proc/sys/kernel/random/uuid 2>/dev/null \
    || echo "00000000-0000-4000-8000-$(printf '%012d' "$RANDOM$RANDOM")"; } | tr '[:upper:]' '[:lower:]'
}

# hostpart URL: scheme://host:port only (no path, no userinfo)
hostpart() { echo "$1" | sed -E 's#^([a-z]+://)([^@/]*@)?([^/]*).*#\1\3#'; }

check_live() {
  BODY_FILE="$(mktemp "${TMPDIR:-/tmp}/tiresias-setup.XXXXXX")"
  trap 'rm -f "$BODY_FILE"' EXIT   # the response bodies of this report only
  echo "Live-mode readiness (env file: $ENV_FILE)"
  if [ -f "$ENV_FILE" ]; then ok ".env" "present"; else
    missing ".env" "not found; cp .env.example .env and fill the live-mode variables (docs/setup.md)"; fi

  local sample; sample="$(envget SAMPLE_MODE)"
  case "$(echo "$sample" | tr '[:upper:]' '[:lower:]')" in
    false|0|no) ok "SAMPLE_MODE" "false (live)" ;;
    *) note "SAMPLE_MODE" "${sample:-unset (true)}: live needs SAMPLE_MODE=false, or options.sample_mode=false per request" ;;
  esac

  # --- Zetaris
  local zurl zkey code; zurl="$(envget ZETARIS_REST_URL)"; ZORG="$(envget ZETARIS_ORG_ID)"; zkey="$(envget ZETARIS_API_KEY)"
  if [ -n "$zurl" ]; then ok "ZETARIS_REST_URL" "$(hostpart "$zurl")"; else missing "ZETARIS_REST_URL" "set it, e.g. http://host.docker.internal:8888/api/v1.0"; fi
  if [ -n "$ZORG" ]; then ok "ZETARIS_ORG_ID" "set"; else missing "ZETARIS_ORG_ID" "set it (usually 1)"; fi
  if is_set ZETARIS_API_KEY "$zkey" your-zetaris-api-key; then ok "ZETARIS_API_KEY" "set"; else
    missing "ZETARIS_API_KEY" "unset or the .env.example placeholder; copy the key from the Zetaris UI"; fi
  if [ -n "$zurl" ] && is_set ZETARIS_API_KEY "$zkey" your-zetaris-api-key && [ -n "$ZORG" ]; then
    local hurl="${zurl%/}"
    case "$hurl" in *host.docker.internal*)
      hurl="$(echo "$hurl" | sed 's/host\.docker\.internal/localhost/')"
      note "Zetaris URL" "host.docker.internal is the in-container name; checking $(hostpart "$hurl") from the host" ;;
    esac
    code="$(http_auth "$hurl/sql-editor/sqls/run" "$zkey" \
      \
      "{\"queryId\":\"$(uuid)\",\"sql\":\"SELECT 1 AS alive\",\"source\":\"SqlEditor\",\"limit\":1}")"
    case "$code" in
      200) ok "Zetaris REST" "SELECT 1 answered (HTTP 200)" ;;
      401|403) fail "Zetaris REST" "HTTP $code: the API key or org id is wrong" ;;
      000) fail "Zetaris REST" "unreachable at $(hostpart "$hurl"); start the bundle (docker compose up -d, ./verify.sh)" ;;
      *) fail "Zetaris REST" "HTTP $code (reachable, but SELECT 1 failed: engine down? docker restart zetaris-driver-1; zetaris/README.md#quirks)" ;;
    esac
    if [ "$code" = 200 ]; then
      # Registered REST tables (zetaris/sql/32_entities.sql). bootstrap registers the GE showcase
      # (SHOWCASE_ENTITIES); other companies, 3M and Solventum included, are registered by a live run.
      code="$(http_auth "$hurl/sql-editor/sqls/run" "$zkey" \
        "{\"queryId\":\"$(uuid)\",\"sql\":\"SHOW LIGHTNING TABLES TIRESIAS_SEC\",\"source\":\"SqlEditor\",\"limit\":1000}")"
      if [ "$code" = 200 ] && grep -q '"ge_facts_raw"' "$BODY_FILE" && grep -q '"gehc_facts_raw"' "$BODY_FILE" \
         && grep -q '"gev_facts_raw"' "$BODY_FILE"; then
        ok "showcase sources" "TIRESIAS_SEC ge/gehc/gev_facts_raw registered"
      else
        missing "showcase sources" "not registered (HTTP $code); cd services/agent && uv run python -m tiresias_agent.adapters.zetaris bootstrap"
      fi
      if [ "$code" = 200 ] && grep -q '"mmm_facts_raw"' "$BODY_FILE" && grep -q '"solv_facts_raw"' "$BODY_FILE"; then
        note "example 04 sources" "3M and Solventum registered"
      elif [ "$code" = 200 ]; then
        note "example 04 sources" "3M/Solventum not registered yet; the first live run of 04 registers them (source gate)"
      fi
    fi
  fi

  # --- Nightshift
  local murl mtok; murl="$(envget METERLESS_BASE_URL)"; mtok="$(envget METERLESS_RUNTIME_TOKEN)"
  if [ -n "$murl" ]; then ok "METERLESS_BASE_URL" "$(hostpart "$murl")"; else missing "METERLESS_BASE_URL" "copy the Runtime URL from the Nightshift Console (ends in /v1)"; fi
  if is_set METERLESS_RUNTIME_TOKEN "$mtok" your-nightshift-runtime-token; then ok "METERLESS_RUNTIME_TOKEN" "set"; else
    missing "METERLESS_RUNTIME_TOKEN" "unset or the .env.example placeholder; copy it from the Nightshift Console"; fi
  if [ -n "$murl" ] && is_set METERLESS_RUNTIME_TOKEN "$mtok" your-nightshift-runtime-token; then
    local hm="${murl%/}"
    case "$hm" in *host.docker.internal*)
      hm="$(echo "$hm" | sed 's/host\.docker\.internal/localhost/')"
      note "Nightshift URL" "host.docker.internal is the in-container name; checking $(hostpart "$hm") from the host" ;;
    esac
    code="$(http_auth "$hm/models" "$mtok")"
    case "$code" in
      200) ok "Nightshift /models" "HTTP 200"
           local lane id
           for lane in FAST DEEP; do
             id="$(envget "METERLESS_MODEL_$lane")"
             case "$id" in
               "") note "METERLESS_MODEL_$lane" "unset (default lane meterless/$(echo "$lane" | tr '[:upper:]' '[:lower:]'))" ;;
               meterless/*) note "METERLESS_MODEL_$lane" "$id (a lane: Nightshift picks the model)" ;;
               *) if grep -qF "\"$id\"" "$BODY_FILE"; then ok "METERLESS_MODEL_$lane" "$id listed by /models"
                  else fail "METERLESS_MODEL_$lane" "$id not listed by /models; enable it in Nightshift or use meterless/$(echo "$lane" | tr '[:upper:]' '[:lower:]')"; fi ;;
             esac
           done ;;
      401|403) fail "Nightshift /models" "HTTP $code: the runtime token is wrong" ;;
      000) fail "Nightshift /models" "unreachable at $(hostpart "$hm"); start the Nightshift VM and its Runtime" ;;
      *) fail "Nightshift /models" "HTTP $code from $(hostpart "$hm")" ;;
    esac
  fi

  # --- SEC fair access (bootstrap and discovery send it to SEC through Zetaris)
  local ua; ua="$(envget SEC_USER_AGENT)"
  if is_set SEC_USER_AGENT "$ua" "Tiresias your-contact-email@example.com"; then ok "SEC_USER_AGENT" "set"; else
    missing "SEC_USER_AGENT" "set it to \"Name contact@example.com\" (SEC fair-access policy; needed by bootstrap)"; fi

  if [ "$ISSUES" -eq 0 ]; then echo "live mode ready"; exit 0; fi
  echo "live mode not ready: $ISSUES issue(s)"; exit 1
}

[ "$MODE" = live ] && check_live

# ---------------------------------------------------------------- demo (sample mode)
cd "$ROOT" || die "cannot cd to $ROOT"
ENV_FILE="$ROOT/.env"
step() { echo "==> $*"; }

step "Checking prerequisites"
command -v docker >/dev/null 2>&1 || die "docker not found; install Docker Desktop or Docker Engine"
command -v curl >/dev/null 2>&1 || die "curl not found; install curl"
docker info >/dev/null 2>&1 || die "the Docker daemon is not running; start Docker Desktop (or: sudo systemctl start docker)"
ver="$(docker compose version --short 2>/dev/null | sed 's/^v//')"
[ -n "$ver" ] || die "docker compose (v2) not found; install the Compose plugin, 2.24 or later"
major="${ver%%.*}"; rest="${ver#*.}"; minor="${rest%%.*}"
if [ "$major" -lt 2 ] 2>/dev/null || { [ "$major" -eq 2 ] && [ "$minor" -lt 24 ]; } 2>/dev/null; then
  die "docker compose $ver is too old; compose.yaml needs 2.24 or later (env_file 'required: false')"
fi
echo "    docker compose $ver"

if [ -f .env ]; then echo "    .env exists; leaving it as is"
else cp .env.example .env || die "could not create .env"; echo "    created .env from .env.example (SAMPLE_MODE=true, no keys needed)"; fi

# Shell environment wins over .env, as in docker compose's own interpolation.
AGENT_PORT="${AGENT_PORT:-$(envget AGENT_PORT)}"; AGENT_PORT="${AGENT_PORT:-8000}"
COGNITION_PORT="${COGNITION_PORT:-$(envget COGNITION_PORT)}"; COGNITION_PORT="${COGNITION_PORT:-8100}"
export AGENT_PORT COGNITION_PORT

# A port is busy if something accepts a connection on it; skip when this project's stack already runs.
if [ -z "$(docker compose ps -q --status running 2>/dev/null)" ]; then
  for pair in "AGENT_PORT:$AGENT_PORT" "COGNITION_PORT:$COGNITION_PORT"; do
    name="${pair%%:*}"; port="${pair#*:}"
    if (exec 3<>"/dev/tcp/127.0.0.1/$port") 2>/dev/null; then
      die "port $port ($name) is already in use; free it, or set $name to a free port in .env (e.g. $name=$((port + 30)))"
    fi
  done
fi
echo "    ports: agent $AGENT_PORT, cognition $COGNITION_PORT"

step "Building and starting the stack (the first build pulls base images and can take a few minutes)"
docker compose up --build -d || die "docker compose up failed (see the output above)"

step "Waiting for http://localhost:$AGENT_PORT/health (up to ${HEALTH_TIMEOUT}s)"
start=$(date +%s)
until curl -sf --max-time 3 "http://localhost:$AGENT_PORT/health" >/dev/null 2>&1; do
  elapsed=$(( $(date +%s) - start ))
  [ "$elapsed" -ge "$HEALTH_TIMEOUT" ] && die "the agent did not answer /health in ${HEALTH_TIMEOUT}s; see: docker compose logs agent"
  printf '    still waiting (%ss)\n' "$elapsed"; sleep 3
done
echo "    healthy after $(( $(date +%s) - start ))s"

step "Running the lead example: $EXAMPLE (why did 3M's 2023 revenue fall?)"
run_id="setup-04-$(date +%Y%m%d%H%M%S)"   # unique: the registry refuses a reused X-Run-Id (409)
resp="$(curl -s --max-time 660 -X POST "http://localhost:$AGENT_PORT/run" \
  -H 'Content-Type: application/json' -H "X-Run-Id: $run_id" -d @"$EXAMPLE")" || die "POST /run failed"
if command -v python3 >/dev/null 2>&1; then
  parsed="$(printf '%s' "$resp" | python3 -c 'import json,sys
d=json.load(sys.stdin); print(d.get("end_state") or ""); print((d.get("action") or "")[:120])' 2>/dev/null)"
  end_state="$(echo "$parsed" | sed -n 1p)"; action="$(echo "$parsed" | sed -n 2p)"
else
  end_state="$(printf '%s' "$resp" | sed -n 's/.*"end_state"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')"
  action="$(printf '%s' "$resp" | sed -n 's/.*"action"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | cut -c1-120)"
fi
[ -n "$end_state" ] || die "unexpected reply from POST /run: $(printf '%s' "$resp" | cut -c1-300)"
echo "    end_state: $end_state"
echo "    action:    $action..."
echo "    trace:     logs/trace-$run_id.jsonl"

echo
echo "Tiresias is running (sample mode). Web UI: http://localhost:$AGENT_PORT"
echo "More examples: README.md Quickstart. Stop with: docker compose down. Live mode: docs/setup.md"
