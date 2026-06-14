#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

API_PORT="${API_PORT:-8000}"
DOCKER_START_TIMEOUT_SECONDS="${DOCKER_START_TIMEOUT_SECONDS:-180}"
DB_HEALTH_TIMEOUT_SECONDS="${DB_HEALTH_TIMEOUT_SECONDS:-120}"
SERVER_READY_TIMEOUT_SECONDS="${SERVER_READY_TIMEOUT_SECONDS:-90}"
REBUILD_BACKEND="${REBUILD_BACKEND:-1}"

log() {
  printf '%s\n' "$*"
}

wait_for_docker() {
  if docker info >/dev/null 2>&1; then
    return 0
  fi

  if [[ "$(uname -s)" == "Darwin" ]]; then
    log "Docker is not running. Starting Docker Desktop..."
    open -a Docker
  else
    log "Docker is not running. Start Docker, then rerun this script." >&2
    return 1
  fi

  local waited=0
  while ! docker info >/dev/null 2>&1; do
    if (( waited >= DOCKER_START_TIMEOUT_SECONDS )); then
      log "Docker did not become ready within ${DOCKER_START_TIMEOUT_SECONDS}s." >&2
      return 1
    fi
    sleep 3
    waited=$((waited + 3))
  done
}

wait_for_db() {
  local waited=0
  local db_health=""
  while true; do
    db_health="$(docker inspect -f '{{.State.Health.Status}}' municipality-db-1 2>/dev/null || true)"
    if [[ "$db_health" == "healthy" ]]; then
      return 0
    fi
    if (( waited >= DB_HEALTH_TIMEOUT_SECONDS )); then
      log "PostGIS did not become healthy within ${DB_HEALTH_TIMEOUT_SECONDS}s. Last status: ${db_health:-unknown}" >&2
      return 1
    fi
    sleep 2
    waited=$((waited + 2))
  done
}

wait_for_server() {
  local waited=0
  while ! python3 - <<PY >/dev/null 2>&1
import urllib.request
urllib.request.urlopen("http://127.0.0.1:${API_PORT}/ask", timeout=5).read(1)
PY
  do
    if (( waited >= SERVER_READY_TIMEOUT_SECONDS )); then
      log "UI server did not respond within ${SERVER_READY_TIMEOUT_SECONDS}s." >&2
      return 1
    fi
    sleep 2
    waited=$((waited + 2))
  done
}

verify_gis() {
  python3 - <<PY
import json
import urllib.request

data = json.load(urllib.request.urlopen("http://127.0.0.1:${API_PORT}/api/ui/rag-dashboard/gis-map?profile=overview", timeout=30))
layers = data.get("layers") or {}
print(json.dumps({
    "gis_status": data.get("status"),
    "neighborhoods": (layers.get("neighborhoods") or {}).get("count"),
    "context_pois": (layers.get("context_pois") or {}).get("total_count"),
    "buildings_total": (layers.get("buildings") or {}).get("total_count"),
}, ensure_ascii=False, sort_keys=True))
if data.get("status") != "found":
    raise SystemExit(1)
PY
}

wait_for_docker

log "Starting PostGIS..."
docker compose up -d db
wait_for_db

if [[ "$REBUILD_BACKEND" == "1" ]]; then
  log "Building and starting backend UI server..."
  docker compose up --build -d backend
else
  log "Starting backend UI server..."
  docker compose up -d backend
fi

wait_for_server

log "Verifying real GIS endpoint..."
verify_gis

log "UI server ready: http://127.0.0.1:${API_PORT}/ask"
