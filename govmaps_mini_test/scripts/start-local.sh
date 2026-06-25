#!/usr/bin/env bash
set -euo pipefail

if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

: "${GOVMAP_CONFIG_PATH:=govmaps.md}"
: "${HOST:=0.0.0.0}"
: "${PORT:=8000}"

if [ ! -f "$GOVMAP_CONFIG_PATH" ]; then
  echo "Missing $GOVMAP_CONFIG_PATH. Copy govmaps.example.md to govmaps.md and add the GovMap API key." >&2
  exit 1
fi

exec python3 -m govmap_proxy.server --host "$HOST" --port "$PORT"
