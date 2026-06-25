#!/usr/bin/env bash
set -euo pipefail

if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

: "${GOVMAP_CONFIG_PATH:=govmaps.md}"
: "${APP_PUBLIC_PATH_PREFIX:=/govmap-local}"
: "${HOST:=0.0.0.0}"
: "${PORT:=8000}"

if [ ! -f "$GOVMAP_CONFIG_PATH" ]; then
  echo "Missing $GOVMAP_CONFIG_PATH. Copy govmaps.example.md to govmaps.md and add the GovMap API key." >&2
  exit 1
fi

if [ -z "${APP_REVERSE_PROXY_SECRET:-}" ]; then
  if [ ! -f .reverse_proxy_secret ]; then
    if command -v openssl >/dev/null 2>&1; then
      openssl rand -hex 32 > .reverse_proxy_secret
    else
      python3 - <<'PY_SECRET' > .reverse_proxy_secret
import secrets
print(secrets.token_hex(32))
PY_SECRET
    fi
    chmod 600 .reverse_proxy_secret
  fi
  export APP_REVERSE_PROXY_SECRET="$(cat .reverse_proxy_secret)"
fi

echo "Starting GovMap local server for reverse proxy path: $APP_PUBLIC_PATH_PREFIX"
echo "Configure the upstream reverse proxy custom header X-Govmap-Proxy-Secret to this exact value:"
echo "$APP_REVERSE_PROXY_SECRET"

export APP_PUBLIC_PATH_PREFIX
exec python3 -m govmap_proxy.server --host "$HOST" --port "$PORT"
