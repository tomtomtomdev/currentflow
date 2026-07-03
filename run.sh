#!/usr/bin/env bash
#
# run.sh — local launcher for the CurrentFlow terminal.
#
# Single-operator, local-first (per CLAUDE.md): this is NOT a deploy script.
# It resolves the repo's own .venv, makes sure the package + UI extras are
# installed, verifies the operator's Bearer token is present, then starts the
# Streamlit terminal. Nothing leaves the machine.
#
# Usage:
#   ./run.sh              launch the terminal (default)
#   ./run.sh login        capture / paste the Stockbit Bearer into the Keychain
#   ./run.sh check        verify the stored token authenticates against exodus
#   ./run.sh test         run the test suite
#   PORT=8502 ./run.sh    launch on a non-default port
#
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_ROOT"

VENV="$REPO_ROOT/.venv"
PY="$VENV/bin/python"
APP="currentflow/ui/app.py"
PORT="${PORT:-8501}"

log() { printf '\033[36m[run]\033[0m %s\n' "$*"; }
die() { printf '\033[31m[run] %s\033[0m\n' "$*" >&2; exit 1; }

ensure_venv() {
  if [[ ! -x "$PY" ]]; then
    log "no .venv found — creating one"
    python3 -m venv "$VENV"
  fi
}

ensure_deps() {
  # Editable install with dev+ui extras; cheap no-op once satisfied.
  if ! "$PY" -c "import streamlit, currentflow" >/dev/null 2>&1; then
    log "installing package + ui/dev extras into .venv"
    "$PY" -m pip install --quiet --upgrade pip
    "$PY" -m pip install --quiet -e ".[dev,ui]"
  fi
}

cmd="${1:-serve}"
case "$cmd" in
  login)
    ensure_venv; ensure_deps
    exec "$PY" -m currentflow.dal.login paste
    ;;
  check)
    ensure_venv; ensure_deps
    exec "$PY" -m currentflow.dal.login check
    ;;
  test)
    ensure_venv; ensure_deps
    exec "$PY" -m pytest
    ;;
  serve)
    ensure_venv; ensure_deps
    # Fail loud if no token is stored (DAL rule: never emit stale/empty).
    if ! "$PY" -m currentflow.dal.login status >/dev/null 2>&1; then
      die "no Bearer token in Keychain — run './run.sh login' first"
    fi
    log "starting CurrentFlow terminal on http://localhost:$PORT"
    exec "$PY" -m streamlit run "$APP" \
      --server.port "$PORT" \
      --server.headless true \
      --theme.base dark
    ;;
  *)
    die "unknown command '$cmd' — use: serve | login | check | test"
    ;;
esac
