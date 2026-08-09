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
#   ./run.sh premise ...  ONE STEP: sign in (if needed) -> strided backfill -> univariate
#                         premise tests. Falsifies individual SMS components before they
#                         are assembled, at ~1/10th the broker calls of a dense pull.
#                         Writes to a throwaway store (research.duckdb), never the live
#                         one. --dry-run prints the call budget and stops. e.g.
#                           ./run.sh premise BBCA BBRI BMRI TLKM --dry-run
#                           ./run.sh premise BBCA BBRI BMRI TLKM --stride 10
#                           ./run.sh premise                      (seeds from currentflow.duckdb)
#   ./run.sh schedule     run the automated per-feed ingestion daemon (slice 12) —
#                         fires each feed on its cadence during Mon–Fri trading hours;
#                         --once runs a single tick and exits. Usually launchd-driven
#                         (deploy/com.currentflow.scheduler.plist). Now also drives the
#                         LD-11 Fast Mode auto paper-trade step once armed.
#   ./run.sh fast ...     Fast Mode auto paper-trader control (slice 15, LD-11; paper only):
#                           ./run.sh fast enable | disable | status
#                           ./run.sh fast run [--day YYYY-MM-DD]   (one manual step)
#   ./run.sh haste ...    Haste Mode auto paper-trader control (slice 16, LD-12; paper only).
#                         Same commands as 'fast', wider cohort (WATCH + ARMED — no arming
#                         cut). Only one of fast/haste may be armed at a time.
#   ./run.sh log          tail the network-error log (logs/net.log; -f to follow)
#   ./run.sh test         run the test suite
#   ./run.sh stop         stop the running terminal (kills the Streamlit on $PORT)
#   ./run.sh python       show the interpreter / .venv this launcher resolved
#   PORT=8502 ./run.sh    launch on a non-default port
#
# Interpreter: the package requires Python >= 3.11 (pyproject requires-python,
# CLAUDE.md §10). macOS ships 3.9, so this script hunts for a newer *and working*
# one (uv-managed builds, PATH pythonX.Y, Homebrew python@X.Y, pyenv, python.org)
# and rebuilds .venv when it was created with an unusable interpreter. Interpreters
# that fail the stdlib smoke test are logged and skipped, never silently used.
# Override the choice with:
#   CF_PYTHON=/path/to/python3.13 ./run.sh
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

# --- interpreter resolution -------------------------------------------------
# pyproject pins requires-python = ">=3.11"; macOS /usr/bin/python3 is 3.9, so
# never assume `python3` is usable. Keep MIN_PY_* in sync with pyproject.
MIN_PY_MAJOR=3
MIN_PY_MINOR=11
MIN_PY="${MIN_PY_MAJOR}.${MIN_PY_MINOR}"
# newest first — the versioned interpreters we'll look for on PATH / in prefixes
PY_SERIES=(3.14 3.13 3.12 3.11)
# what we offer to install when nothing usable exists (uv ships standalone builds
# that vendor libexpat/openssl — see py_healthy for why that matters here)
UV_PY_VERSION="3.13"

py_version() { "$1" -c 'import sys; print(".".join(map(str, sys.version_info[:3])))' 2>/dev/null; }

py_version_ok() {  # $1 = interpreter (name on PATH or absolute path)
  local exe="$1" v maj min
  command -v "$exe" >/dev/null 2>&1 || return 1
  v="$("$exe" -c 'import sys; print("%d %d" % sys.version_info[:2])' 2>/dev/null)" || return 1
  maj="${v%% *}"; min="${v##* }"
  [[ "$maj" =~ ^[0-9]+$ && "$min" =~ ^[0-9]+$ ]] || return 1
  (( maj > MIN_PY_MAJOR || (maj == MIN_PY_MAJOR && min >= MIN_PY_MINOR) ))
}

# New-enough is not the same as working. Homebrew's python bottles link pyexpat
# against the *system* libexpat; when the bottle's build host is newer than this
# machine, `import plistlib` dies on a missing symbol — which takes out
# pip/ensurepip (its vendored truststore calls platform.mac_ver()) and anything
# else touching XML. Smoke-test the modules we actually depend on so a broken
# interpreter is rejected here, not three minutes into a pip install.
py_healthy() { "$1" -c 'import plistlib, ssl, sqlite3, venv, lzma' >/dev/null 2>&1; }

py_ok() { py_version_ok "$1" && py_healthy "$1"; }

# An explicit override is a hard assertion: fail loud now (top level, not inside a
# command substitution) rather than silently searching past it.
if [[ -n "${CF_PYTHON:-}" ]] && ! py_version_ok "$CF_PYTHON"; then
  die "CF_PYTHON='$CF_PYTHON' is not a Python >= $MIN_PY (found: $(py_version "$CF_PYTHON" || echo 'not executable'))"
fi
if [[ -n "${CF_PYTHON:-}" ]] && ! py_healthy "$CF_PYTHON"; then
  die "CF_PYTHON='$CF_PYTHON' has a broken stdlib — run:
       $CF_PYTHON -c 'import plistlib, ssl, sqlite3, venv, lzma'
     for the failing import."
fi

# Print the path of the first usable interpreter (>= $MIN_PY and healthy), else
# return 1. NOTE: stdout is the return channel — every log here goes to stderr.
find_python() {
  local candidates=() v p resolved

  [[ -n "${CF_PYTHON:-}" ]] && candidates+=("$CF_PYTHON")   # validated above
  # uv-managed standalone builds first: they vendor their own libexpat/openssl,
  # so they don't inherit the system-library skew that breaks brew bottles here.
  if command -v uv >/dev/null 2>&1; then
    p="$(uv python find ">=$MIN_PY" 2>/dev/null || true)"
    [[ -n "$p" ]] && candidates+=("$p")
  fi
  while IFS= read -r p; do [[ -n "$p" ]] && candidates+=("$p"); done < <(
    ls -1d "${UV_PYTHON_INSTALL_DIR:-$HOME/.local/share/uv/python}"/*/bin/python3 2>/dev/null | sort -Vr
  )
  for v in "${PY_SERIES[@]}"; do candidates+=("python$v"); done
  for v in "${PY_SERIES[@]}"; do
    candidates+=(
      "/opt/homebrew/opt/python@$v/libexec/bin/python3"   # brew, apple silicon
      "/usr/local/opt/python@$v/libexec/bin/python3"      # brew, intel
      "/Library/Frameworks/Python.framework/Versions/$v/bin/python3"  # python.org
    )
  done
  # pyenv installs, newest first
  while IFS= read -r p; do [[ -n "$p" ]] && candidates+=("$p"); done < <(
    ls -1d "${PYENV_ROOT:-$HOME/.pyenv}"/versions/*/bin/python3 2>/dev/null | sort -Vr
  )
  candidates+=(python3)  # last resort — only used if it happens to be new enough

  for p in "${candidates[@]}"; do
    resolved="$(command -v "$p" 2>/dev/null)" || continue
    py_version_ok "$resolved" || continue
    if ! py_healthy "$resolved"; then
      # never silently skip: say which interpreter was passed over and why
      log "skipping $resolved (Python $(py_version "$resolved")) — broken stdlib import (plistlib/ssl/sqlite3/venv/lzma)" >&2
      continue
    fi
    printf '%s\n' "$resolved"; return 0
  done
  return 1
}

# No usable interpreter anywhere: offer to fetch one rather than dying blind.
# Interactive-only and opt-in — this installs software on the machine. Called
# from a command substitution, so prompt/log on stderr only.
install_python_interactive() {
  [[ -t 0 ]] || return 1
  local reply="" plan=""
  if command -v uv >/dev/null 2>&1; then
    plan="uv python install $UV_PY_VERSION"
  elif command -v brew >/dev/null 2>&1; then
    plan="brew install uv && uv python install $UV_PY_VERSION"
  else
    return 1
  fi
  printf '\033[36m[run]\033[0m no usable Python >= %s found. Run `%s` now? [y/N] ' "$MIN_PY" "$plan" >&2
  read -r reply || return 1
  [[ "$reply" =~ ^[Yy] ]] || return 1
  command -v uv >/dev/null 2>&1 || { log "brew install uv" >&2; brew install uv >&2 || return 1; }
  log "uv python install $UV_PY_VERSION" >&2
  uv python install "$UV_PY_VERSION" >&2 || return 1
}

no_python_help() {
  die "no usable Python >= $MIN_PY found (this package's requires-python; /usr/bin/python3 here is $(py_version python3)).
     Install a self-contained one, then re-run:
       brew install uv && uv python install $UV_PY_VERSION
     (Homebrew's own python bottles can be unusable on this macOS — their pyexpat
     links the system libexpat; run.sh logs any interpreter it skips for that.)
     Or point the launcher at an existing interpreter:
       CF_PYTHON=/path/to/python$MIN_PY ./run.sh ${cmd:-serve}"
}

# Resolve a base interpreter, installing one only with the operator's consent.
resolve_python() {
  local base
  if base="$(find_python)"; then printf '%s\n' "$base"; return 0; fi
  install_python_interactive || no_python_help
  base="$(find_python)" || no_python_help
  printf '%s\n' "$base"
}

ensure_venv() {
  # Good venv already? nothing to do.
  if [[ -x "$PY" ]] && py_ok "$PY"; then return 0; fi

  local base why
  if [[ -x "$PY" ]]; then
    if py_version_ok "$PY"; then why="broken stdlib import"; else why="below the required $MIN_PY"; fi
    log ".venv is Python $(py_version "$PY") — $why; it needs rebuilding"
    base="$(resolve_python)"  # resolve BEFORE discarding the old venv
    log "discarding $VENV (derived artifact — reinstalled from pyproject below)"
    rm -rf "$VENV"
  else
    [[ -e "$VENV" ]] && die "$VENV exists but has no usable python — remove it and re-run"
    log "no .venv found — creating one"
    base="$(resolve_python)"
  fi

  log "creating .venv with $base (Python $(py_version "$base"))"
  "$base" -m venv "$VENV"
  [[ -x "$PY" ]] || die "venv creation failed — $PY missing after 'python -m venv'"
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
  premise)
    ensure_venv; ensure_deps
    # One-step falsification pass: sign in if needed -> strided backfill (full bars,
    # broker only on sampled days) -> univariate premise tests. Writes to a THROWAWAY
    # store, never currentflow.duckdb — a sparse-broker store would poison the live
    # pipeline's ingest-once marker (currentflow/research/backfill.py).
    shift || true
    syms=""; stride=10; horizon=""; db="research.duckdb"; dry=0
    while [[ $# -gt 0 ]]; do
      case "$1" in
        --stride)  stride="${2:?--stride needs a value}";  shift 2 ;;
        --horizon) horizon="${2:?--horizon needs a value}"; shift 2 ;;
        --db)      db="${2:?--db needs a value}";           shift 2 ;;
        --dry-run) dry=1; shift ;;
        -*) die "unknown flag $1 — usage: ./run.sh premise [SYM ...] [--stride N] [--horizon N] [--db PATH] [--dry-run]" ;;
        *)  syms="${syms:+$syms,}$1"; shift ;;
      esac
    done
    # Default the horizon to the stride: a horizon shorter than the backfill stride
    # samples days with no broker rows and silently shrinks the sample.
    [[ -n "$horizon" ]] || horizon="$stride"

    if [[ -n "$syms" ]]; then
      seed_args=(--symbols "$syms")
    elif [[ -f "$REPO_ROOT/currentflow.duckdb" ]]; then
      log "no symbols given — seeding the list from currentflow.duckdb"
      seed_args=(--seed-from "$REPO_ROOT/currentflow.duckdb")
    else
      die "no symbols and no currentflow.duckdb to seed from — try: ./run.sh premise BBCA BBRI BMRI TLKM"
    fi

    if [[ "$dry" -eq 1 ]]; then
      log "dry run — printing the call budget only, nothing spent"
      "$PY" -m currentflow.research backfill --db "$db" --stride "$stride" "${seed_args[@]}" \
        | grep -v -e '--yes' -e '^$' || true
      log "to spend it, re-run the same command without --dry-run"
      exit 0
    fi

    # Needs the operator's own session; sign in inline so this stays one step.
    if ! "$PY" -m currentflow.dal.login status >/dev/null 2>&1; then
      log "no session — signing in first (OTP may be required)"
      "$PY" -m currentflow.dal.login login
    fi

    log "step 1/2 — strided backfill (stride $stride) into $db"
    "$PY" -m currentflow.research backfill \
      --db "$db" --stride "$stride" "${seed_args[@]}" --yes

    log "step 2/2 — premise tests (horizon $horizon)"
    exec "$PY" -m currentflow.research test --db "$db" --horizon "$horizon"
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
  haste)
    ensure_venv; ensure_deps
    # LD-12 Haste Mode control (slice 16): same commands as 'fast', wider cohort
    # (WATCH + ARMED — the arming cut dropped). Only one of the two may be armed at a
    # time; arming the second is refused. Local store only, paper only.
    shift || true
    [[ $# -ge 1 ]] || die "usage: ./run.sh haste {enable|disable|status|run} [--day YYYY-MM-DD] [--db PATH]"
    exec "$PY" -m currentflow.haste "$@"
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
  python)
    # Diagnostic: which interpreter would this launcher use, and is .venv healthy?
    if [[ -x "$PY" ]]; then
      if py_ok "$PY"; then
        log ".venv: $PY (Python $(py_version "$PY")) — ok, >= $MIN_PY"
      elif py_version_ok "$PY"; then
        log ".venv: $PY (Python $(py_version "$PY")) — BROKEN stdlib import (will be rebuilt)"
      else
        log ".venv: $PY (Python $(py_version "$PY")) — TOO OLD, needs >= $MIN_PY (will be rebuilt)"
      fi
    else
      log ".venv: none yet ($VENV)"
    fi
    if base="$(find_python)"; then
      log "base interpreter: $base (Python $(py_version "$base"))"
    else
      log "base interpreter: none usable >= $MIN_PY — 'uv python install $UV_PY_VERSION' or set CF_PYTHON"
      exit 1
    fi
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
    die "unknown command '$cmd' — use: serve | login | paste | check | ingest | backfill | schedule | fast | haste | log | test | stop | python"
    ;;
esac
