#!/usr/bin/env bash
# Validate metadata.json against the community schema (docs: metadata.schema.json, 0.1-community).
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
npx -y -p ajv-cli -p ajv-formats ajv validate --spec=draft2020 -c ajv-formats \
  -s scripts/metadata.schema.json -d metadata.json
