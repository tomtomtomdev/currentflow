"""Shared operator CLI for the two auto paper-traders (LD-11 Fast / LD-12 Haste).

`python -m currentflow.fast` and `python -m currentflow.haste` are thin wrappers over this
module, differing only in the `mode` they pass. Both arm/disarm their trader and can step it
once against the already-ingested local store (no network — the step reads the cache the
scheduler/ingest filled). In production the scheduler daemon (`python -m currentflow.scheduler`)
drives the daily step automatically; the CLI is for arming + a manual smoke step.

Only one mode may be armed at a time (they share one paper book and one §6 circuit budget);
arming the second is refused loudly rather than silently resolved. **Paper only — never a live
order (§15).**
"""

from __future__ import annotations

import argparse
from datetime import datetime

from currentflow.ingest.__main__ import DEFAULT_DB
from currentflow.logging_setup import configure_logging
from currentflow.scheduler import calendar as cal
from currentflow.store.db import Store
from currentflow.store.schema import MODE_FAST, MODE_HASTE
from currentflow.universe.sectors import OPERATOR_SECTOR_MAP
from currentflow.validation import fast_mode as fm
from currentflow.validation.promotion import ValidationLedger

# Per-mode CLI copy. The cohort line is the ONLY behavioural difference between the two.
_MODE_UI = {
    MODE_FAST: {
        "prog": "currentflow.fast",
        "name": "fast mode",
        "ld": "LD-11",
        "cohort": "every ARMED name",
        "desc": "Fast Mode auto paper-trader control (LD-11, paper only).",
    },
    MODE_HASTE: {
        "prog": "currentflow.haste",
        "name": "haste mode",
        "ld": "LD-12",
        "cohort": "every WATCH *and* ARMED name (no arming cut)",
        "desc": "Haste Mode auto paper-trader control (LD-12, paper only).",
    },
}


def _print_status(store: Store, mode: str) -> None:
    ui = _MODE_UI[mode]
    state = store.read_fast_mode_state(mode=mode)
    if state is None:
        print(f"{ui['name']}: never armed (disarmed)")
    else:
        print(
            f"{ui['name']}: {'ARMED' if state.enabled else 'disarmed'} · "
            f"since {state.since_date} · last run {state.last_run_day} · "
            f"realized IDR {state.realized_pnl:,.0f}"
        )
        print(
            f"open positions: {len(store.read_fast_positions(mode=mode))} · "
            f"closed trades: {len(store.read_fast_trades(mode=mode))}"
        )
    # Name the rival's arm state — it is what blocks arming this one (no silent conflict).
    rival = fm.other_mode(mode)
    rival_state = store.read_fast_mode_state(mode=rival)
    if rival_state is not None and rival_state.enabled:
        print(f"note: {rival.lower()} mode is ARMED — disarm it before arming {ui['name']}.")


def main(argv: list[str] | None = None, *, mode: str = MODE_FAST,
         store: Store | None = None, now: datetime | None = None) -> int:
    """CLI entry for one auto-trader. `store`/`now` are injectable for tests."""
    configure_logging()
    ui = _MODE_UI[mode]
    parser = argparse.ArgumentParser(prog=ui["prog"], description=ui["desc"])
    parser.add_argument(
        "command", choices=["enable", "disable", "status", "run"],
        help="enable/disable arm the auto-trader; status prints the book; run steps one day",
    )
    parser.add_argument("--db", default=DEFAULT_DB, help=f"DuckDB path (default {DEFAULT_DB})")
    parser.add_argument(
        "--day", default=None,
        help="trading day YYYY-MM-DD to process with 'run' (default = prior trading day)",
    )
    args = parser.parse_args(argv)

    own_store = store is None
    store = store or Store(args.db)
    now = now or datetime.now()
    try:
        if args.command == "enable":
            try:
                fm.set_enabled(store, True, mode=mode, now=now)
            except fm.ModeConflictError as e:
                # One auto-trader at a time over the shared book — refuse, don't resolve.
                print(f"refused: {e}")
                _print_status(store, mode)
                return 1
            print(
                f"{ui['name']} ARMED — auto paper-buys {ui['cohort']}; "
                "same §8 exit (paper only)."
            )
            _print_status(store, mode)
        elif args.command == "disable":
            fm.set_enabled(store, False, mode=mode, now=now)
            print(
                f"{ui['name']} disarmed — book + accrued record preserved "
                "(a pause, not a reset)."
            )
            _print_status(store, mode)
        elif args.command == "status":
            _print_status(store, mode)
        else:  # run — a single day-step against the local store
            day = (
                datetime.strptime(args.day, "%Y-%m-%d").date() if args.day
                else cal.previous_trading_day(now.date())
            )
            symbols = store.scr0_universe(now) or store.symbols("daily_bar")
            ledger = ValidationLedger()
            result = fm.run_fast_mode_step(
                store, symbols, day, mode=mode, sector_map=OPERATOR_SECTOR_MAP,
                ledger=ledger, now=now,
            )
            if not result.enabled:
                cmd = "fast" if mode == MODE_FAST else "haste"
                print(f"{ui['name']} disarmed — run './run.sh {cmd} enable' first.")
            else:
                print(f"{ui['name']} step {day}: {result.detail}")
                module = fm.module_for(mode)
                rec = ledger.record(module)
                print(f"{module} lane: {rec.state.value} ({rec.n_trades} trades, "
                      f"{rec.months_accrued:.1f} mo accrued)")
            _print_status(store, mode)
        return 0
    finally:
        if own_store:
            store.close()
