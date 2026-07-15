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
#   ./run.sh              launch the terminal (default; login form if no session).
#                         First launch with an EMPTY store auto-runs the bootstrap
#                         after sign-in: SCR-0 universe + 90-day ingest (slice 13).
#   ./run.sh login        sign in with username/password + OTP (slice 11)
#   ./run.sh paste        fallback: paste a Bearer into the Keychain (slice 10)
#   ./run.sh check        verify the stored token authenticates against exodus
#   ./run.sh ingest ...   backfill / manual fallback (the auto-bootstrap covers the
#                         first run), e.g.
#                           ./run.sh ingest BBCA BBRI --days 90
#                           ./run.sh ingest BBCA --from 2026-04-01 --to 2026-07-03
#   ./run.sh backfill ...  regime-scoped historical backfill (slice 17): fills a
#                         regime-pure 2024→now dataset for the SCR-0 seed (or explicit
#                         names). Resumable/ingest-once. --rosters also loads
#                         data/rosters/ point-in-time index rosters. e.g.
#                           ./run.sh backfill --rosters
#                           ./run.sh backfill BBCA BBRI
#   ./run.sh schedule     run the automated per-feed ingestion daemon (slice 12) —
#                         fires each feed on its cadence during Mon–Fri trading hours;
#                         --once runs a single tick and exits. Usually launchd-driven
#                         (deploy/com.currentflow.scheduler.plist). Now also drives the
#                         LD-11 Fast Mode auto paper-trade step once armed.
#   ./run.sh fast ...     Fast Mode auto paper-trader control (slice 15, LD-11; paper only):
#                           ./run.sh fast enable | disable | status
#                           ./run.sh fast run [--day YYYY-MM-DD]   (one manual step)
#   ./run.sh log          tail the network-error log (logs/net.log; -f to follow)
#   ./run.sh test         run the test suite
#   ./run.sh stop         stop the running terminal (kills the Streamlit on $PORT)
#   PORT=8502 ./run.sh    launch on a non-default port
#
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_ROOT"

VENV="$REPO_ROOT/.venv"
PY="$VENV/bin/python"
APP="currentflow/ui/app.py"
PORT="${PORT:-8501}"
NET_LOG="$REPO_ROOT/logs/net.log"

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
    exec "$PY" -m currentflow.dal.login login
    ;;
  paste)
    ensure_venv; ensure_deps
    exec "$PY" -m currentflow.dal.login paste
    ;;
  check)
    ensure_venv; ensure_deps
    exec "$PY" -m currentflow.dal.login check
    ;;
  ingest)
    ensure_venv; ensure_deps
    # Needs the operator's own session (build_live_client reads the Keychain Bearer).
    if ! "$PY" -m currentflow.dal.login status >/dev/null 2>&1; then
      die "no session — run './run.sh login' before ingesting"
    fi
    shift || true
    [[ $# -ge 1 ]] || die "usage: ./run.sh ingest SYM [SYM ...] [--from YYYY-MM-DD] [--to YYYY-MM-DD] [--days N] [--db PATH]"
    exec "$PY" -m currentflow.ingest "$@"
    ;;
  backfill)
    ensure_venv; ensure_deps
    # Regime-scoped historical backfill (slice 17). Needs the operator's own session
    # (build_live_client reads the Keychain Bearer). Resumable — a re-run is a no-op.
    if ! "$PY" -m currentflow.dal.login status >/dev/null 2>&1; then
      die "no session — run './run.sh login' before backfilling"
    fi
    shift || true
    exec "$PY" -m currentflow.ingest.backfill "$@"
    ;;
  schedule)
    ensure_venv; ensure_deps
    # Headless daemon: needs the operator's own session (build_live_client reads the
    # Keychain access token). A 401 mid-run fails loud — it can't do the OTP re-login.
    if ! "$PY" -m currentflow.dal.login status >/dev/null 2>&1; then
      die "no session — run './run.sh login' before scheduling"
    fi
    shift || true
    exec "$PY" -m currentflow.scheduler "$@"
    ;;
  fast)
    ensure_venv; ensure_deps
    # LD-11 Fast Mode control (slice 15): enable | disable | status | run. Operates on the
    # already-ingested local store (no network), so no session check — arm it, then the
    # scheduler daemon drives the daily step, or 'run' steps once manually. Paper only.
    shift || true
    [[ $# -ge 1 ]] || die "usage: ./run.sh fast {enable|disable|status|run} [--day YYYY-MM-DD] [--db PATH]"
    exec "$PY" -m currentflow.fast "$@"
    ;;
  log)
    # No venv/deps needed — just read the local net-error log (dal/netlog.py).
    [[ -f "$NET_LOG" ]] || die "no log yet — $NET_LOG (written once a net-error occurs)"
    shift || true
    if [[ "${1:-}" == "-f" ]]; then
      log "following $NET_LOG (ctrl-c to stop)"
      exec tail -f "$NET_LOG"
    fi
    exec tail -n "${1:-40}" "$NET_LOG"
    ;;
  test)
    ensure_venv; ensure_deps
    exec "$PY" -m pytest
    ;;
  stop)
    # No venv/deps needed — just find whoever is listening on $PORT and kill it.
    pids="$(lsof -ti "tcp:$PORT" 2>/dev/null || true)"
    if [[ -z "$pids" ]]; then
      log "nothing listening on port $PORT — terminal not running"
      exit 0
    fi
    log "stopping CurrentFlow terminal on port $PORT (pid: $pids)"
    # shellcheck disable=SC2086
    kill $pids 2>/dev/null || true
    # Give it a moment, then hard-kill anything that ignored SIGTERM.
    for _ in 1 2 3 4 5; do
      sleep 0.3
      lsof -ti "tcp:$PORT" >/dev/null 2>&1 || { log "stopped"; exit 0; }
    done
    pids="$(lsof -ti "tcp:$PORT" 2>/dev/null || true)"
    [[ -n "$pids" ]] && { log "forcing (SIGKILL) $pids"; kill -9 $pids 2>/dev/null || true; }
    log "stopped"
    ;;
  serve)
    ensure_venv; ensure_deps
    # Slice 11: always start — the app renders the login form when there's no valid
    # session (fail loud in-UI, never blank/stale modules). Just hint if unauthed.
    if ! "$PY" -m currentflow.dal.login status >/dev/null 2>&1; then
      log "no session yet — the terminal will open on the login form ('./run.sh login')"
    fi
    log "starting CurrentFlow terminal on http://localhost:$PORT"
    # theme lives in .streamlit/config.toml (design tokens from design/SCREENS_terminal.md)
    exec "$PY" -m streamlit run "$APP" \
      --server.port "$PORT" \
      --server.headless true
    ;;
  *)
    die "unknown command '$cmd' — use: serve | login | paste | check | ingest | backfill | schedule | fast | log | test | stop"
    ;;
esac
