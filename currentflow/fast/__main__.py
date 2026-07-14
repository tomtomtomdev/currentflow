"""`python -m currentflow.fast` — operator control for the LD-11 auto paper-trader (slice 15).

Arms/disarms Fast Mode and can step it once against the already-ingested local store (no
network — the step reads the cache the scheduler/ingest filled). In production the scheduler
daemon (`python -m currentflow.scheduler`) drives the daily step automatically; this CLI is for
arming + a manual smoke step. **Paper only — never a live order (§15).**
"""

from __future__ import annotations

import argparse
from datetime import datetime

from currentflow.ingest.__main__ import DEFAULT_DB
from currentflow.logging_setup import configure_logging
from currentflow.scheduler import calendar as cal
from currentflow.store.db import Store
from currentflow.universe.sectors import OPERATOR_SECTOR_MAP
from currentflow.validation import fast_mode as fm
from currentflow.validation.promotion import ValidationLedger


def _print_status(store: Store) -> None:
    state = store.read_fast_mode_state()
    if state is None:
        print("fast mode: never armed (disarmed)")
        return
    print(
        f"fast mode: {'ARMED' if state.enabled else 'disarmed'} · since {state.since_date} · "
        f"last run {state.last_run_day} · realized IDR {state.realized_pnl:,.0f}"
    )
    print(
        f"open positions: {len(store.read_fast_positions())} · "
        f"closed trades: {len(store.read_fast_trades())}"
    )


def main(argv: list[str] | None = None, *, store: Store | None = None,
         now: datetime | None = None) -> int:
    """CLI entry. `store`/`now` are injectable for tests."""
    configure_logging()
    parser = argparse.ArgumentParser(
        prog="currentflow.fast",
        description="Fast Mode auto paper-trader control (LD-11, paper only).",
    )
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
            fm.set_enabled(store, True, now=now)
            print("fast mode ARMED — auto paper-buys every ARMED name; same §8 exit (paper only).")
            _print_status(store)
        elif args.command == "disable":
            fm.set_enabled(store, False, now=now)
            print("fast mode disarmed — book + accrued record preserved (a pause, not a reset).")
            _print_status(store)
        elif args.command == "status":
            _print_status(store)
        else:  # run — a single day-step against the local store
            day = (
                datetime.strptime(args.day, "%Y-%m-%d").date() if args.day
                else cal.previous_trading_day(now.date())
            )
            symbols = store.scr0_universe(now) or store.symbols("daily_bar")
            ledger = ValidationLedger()
            result = fm.run_fast_mode_step(
                store, symbols, day, sector_map=OPERATOR_SECTOR_MAP, ledger=ledger, now=now,
            )
            if not result.enabled:
                print("fast mode disarmed — run './run.sh fast enable' first.")
            else:
                print(f"fast step {day}: {result.detail}")
                rec = ledger.record(fm.FAST_MODE_MODULE)
                print(f"fast_mode lane: {rec.state.value} ({rec.n_trades} trades, "
                      f"{rec.months_accrued:.1f} mo accrued)")
            _print_status(store)
        return 0
    finally:
        if own_store:
            store.close()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
