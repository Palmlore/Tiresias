#!/usr/bin/env bash
# Build the static front for GitHub Pages: the web UI's files as the agent serves them at /ui/,
# with config.js pointed at the hosted API, so the first screen (cold-start.js) paints from a warm
# host while Cloud Run starts. Writes nothing outside OUT_DIR.
#   scripts/build_pages.sh API_URL OUT_DIR      e.g. https://tiresias-tckagkyqxa-as.a.run.app _site
set -euo pipefail

API_URL="${1:-}"
OUT="${2:-}"
if [ -z "$API_URL" ] || [ -z "$OUT" ]; then
  echo "usage: scripts/build_pages.sh API_URL OUT_DIR" >&2
  exit 2
fi
# An origin only: https, a host, no path, no trailing slash, nothing that could break the JS string.
# Matched on the whole value ([[ =~ ]]), not line by line as grep would: a newline cannot slip by.
re='^https://[A-Za-z0-9.-]+(:[0-9]+)?$'
if ! [[ "$API_URL" =~ $re ]]; then
  echo "API_URL must be an https origin with no path, e.g. https://api.example.com (got: $API_URL)" >&2
  exit 2
fi
# A relative OUT_DIR is the caller's, not the repo root's.
case "$OUT" in /*) ;; *) OUT="$PWD/$OUT" ;; esac
cd "$(dirname "${BASH_SOURCE[0]}")/.."
WEB=services/agent/src/tiresias_agent/web
if [ -e "$OUT" ] && [ -n "$(ls -A "$OUT" 2>/dev/null)" ]; then
  echo "OUT_DIR $OUT exists and is not empty" >&2
  exit 2
fi
mkdir -p "$OUT"
cp -R "$WEB"/. "$OUT"/

# Exactly one `apiBase: ""` in config.js becomes the hosted API; anything else is a build error.
n="$(grep -c 'apiBase: ""' "$WEB/config.js" || true)"
if [ "$n" != "1" ]; then
  echo "config.js must hold exactly one 'apiBase: \"\"' line (found $n)" >&2
  exit 1
fi
# (writes over the copy this script just made in the empty OUT_DIR)
sed "s#apiBase: \"\"#apiBase: \"$API_URL\"#" "$WEB/config.js" > "$OUT/config.js"
# Pages serves a directory as is: no Jekyll processing.
touch "$OUT/.nojekyll"
echo "built $OUT (api $API_URL)"
