#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8000}"
VENV_DIR="${VENV_DIR:-.venv}"

python_is_compatible() {
  "$1" - <<'PY' >/dev/null 2>&1
import sys
raise SystemExit(0 if sys.version_info >= (3, 11) else 1)
PY
}

find_python() {
  local candidate
  for candidate in \
    "${PYTHON:-}" \
    /opt/homebrew/bin/python3.14 \
    /opt/homebrew/bin/python3.13 \
    /opt/homebrew/bin/python3.12 \
    /opt/homebrew/bin/python3.11 \
    /usr/local/bin/python3.14 \
    /usr/local/bin/python3.13 \
    /usr/local/bin/python3.12 \
    /usr/local/bin/python3.11 \
    python3.14 \
    python3.13 \
    python3.12 \
    python3.11 \
    python3
  do
    if [ -n "$candidate" ] && command -v "$candidate" >/dev/null 2>&1 && python_is_compatible "$candidate"; then
      command -v "$candidate"
      return 0
    fi
  done
  return 1
}

PYTHON_BIN="$(find_python || true)"
RESTORE_EGG_INFO=0

if command -v git >/dev/null 2>&1 && git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  if git diff --quiet -- src/municipality.egg-info && git diff --cached --quiet -- src/municipality.egg-info; then
    RESTORE_EGG_INFO=1
  fi
fi

if [ -z "$PYTHON_BIN" ]; then
  if ! command -v brew >/dev/null 2>&1; then
    echo "Python >=3.11 is required and Homebrew is not available to install it." >&2
    exit 1
  fi

  echo "Python >=3.11 not found. Installing latest Homebrew Python..."
  brew install python
  PYTHON_BIN="$(find_python || true)"
fi

if [ -z "$PYTHON_BIN" ]; then
  echo "Could not find Python >=3.11 after installation." >&2
  exit 1
fi

if [ ! -x "$VENV_DIR/bin/python" ] || ! python_is_compatible "$VENV_DIR/bin/python"; then
  echo "Creating virtual environment with $PYTHON_BIN..."
  rm -rf "$VENV_DIR"
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

echo "Installing/updating project dependencies..."
"$VENV_DIR/bin/python" -m pip install --upgrade pip setuptools wheel
"$VENV_DIR/bin/pip" install .

rm -rf build
if [ "$RESTORE_EGG_INFO" -eq 1 ]; then
  git restore --worktree -- src/municipality.egg-info
fi

echo "Starting Ask server at http://$HOST:$PORT/ask"
exec "$VENV_DIR/bin/python" -m uvicorn municipality.api:app --host "$HOST" --port "$PORT"
