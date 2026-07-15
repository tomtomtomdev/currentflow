"""Regime-scoped historical backfill CLI (slice 20, §17.2).

Fills the local store with a *regime-pure* 2024→now dataset — the substrate every base
rate, backtest, and derived statistic stands on (slice 21). For each seed name (the
current SCR-0 pull — seed only; point-in-time correctness comes from `pit_universe`,
not the seed) it ingests daily bars + per-day broker summary from `regime_start(track)`
→ today via the existing `ingest_symbol` path.

    python -m currentflow.ingest.backfill                 # SCR-0 seed → currentflow.duckdb
    python -m currentflow.ingest.backfill BBCA BBRI       # explicit seed
    python -m currentflow.ingest.backfill --rosters       # also (re)load data/rosters/

Resumable by construction: `ingest_symbol` is ingest-once (bars are the commit marker),
so a re-run over a completed range issues zero broker calls and an interrupted run
resumes at the failed name — never a wedged store (slice-13 posture). The paywall budget
is printed up front so the operator arms it knowingly (no silent caps).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass
from datetime import date as Date
from datetime import datetime, timedelta
from typing import Awaitable, Callable

from currentflow import config
from currentflow.dal.errors import AuthError, ExodusError
from currentflow.dal.session import build_live_client
from currentflow.ingest.bootstrap import last_weekday
from currentflow.ingest.pipeline import IngestResult, ingest_symbol, refresh_membership
from currentflow.logging_setup import configure_logging
from currentflow.screeners.scr0 import run_scr0
from currentflow.store.db import Store
from currentflow.universe.roster import RosterValidationError, load_rosters

DEFAULT_DB = "currentflow.duckdb"


def _provisional_track(store: Store, symbol: str, now: datetime) -> str:
    """The window start needs a track before ADV is known. Use current index membership
    only: an LQ45/IDX80 member gets the earlier Track A boundary (so its full window is
    covered); everything else the Track B boundary. If ADV later proves it Track B, the
    read-side clamp floors the extra early bars away — over-fetching is harmless, under-
    fetching would leave a hole (missing ≠ zero)."""
    # `refresh_membership` stamped the snapshot at `as_of=now`; the read firewall is a
    # strict `<`, so clear it by an instant to see the row we just wrote this run.
    row = store.read_symbol_index_latest(symbol, now + timedelta(seconds=1))
    indexes = set(row.indexes) if row is not None else set()
    return "A" if indexes & config.TRACK_A_INDEXES else "B"


@dataclass(frozen=True, slots=True)
class BackfillReport:
    symbols: tuple[str, ...]
    results: tuple[IngestResult, ...]
    failed_symbol: str | None
    total_bars: int
    total_broker: int


async def backfill(
    client,
    store: Store,
    symbols: list[str],
    *,
    now: datetime,
    today: Date,
    pause_s: float = config.BACKFILL_BATCH_PAUSE_S,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    log: Callable[[str], None] = print,
) -> BackfillReport:
    """Backfill each seed name from its regime boundary → `today`. Sequential + paced;
    resumable via ingest-once. A per-name transport error stops the run and names the
    resume point (never a wedged store); an auth error fails loud immediately."""
    results: list[IngestResult] = []
    failed: str | None = None
    for i, symbol in enumerate(symbols):
        # Current membership first (the §3 Track source + the window-start decision).
        try:
            await refresh_membership(client, store, symbol, now=now)
        except AuthError:
            raise
        except ExodusError as exc:
            log(f"  {symbol}: index membership fetch failed ({exc}) — Track B window")

        track = _provisional_track(store, symbol, now)
        start = config.regime_start(track)
        try:
            result = await ingest_symbol(client, store, symbol, start, today, now=now)
        except AuthError:
            raise
        except ExodusError as exc:
            failed = symbol
            log(f"  {symbol}: FAILED ({exc}) — ingest-once: a re-run resumes here")
            break

        results.append(result)
        flags = ""
        if result.coverage.has_gaps:
            flags += f", GAPS {len(result.coverage.gaps)}d"
        if result.has_imbalance:
            flags += f", IMBALANCE {len(result.unclear)}d"
        log(
            f"  [{i + 1}/{len(symbols)}] {symbol} (Track {track}, from {start}): "
            f"+{result.bars_inserted} bars, +{result.broker_rows_inserted} broker, "
            f"{result.days_skipped_cached} cached-skip{flags}"
        )
        if pause_s and i < len(symbols) - 1:
            await sleep(pause_s)

    return BackfillReport(
        symbols=tuple(symbols),
        results=tuple(results),
        failed_symbol=failed,
        total_bars=sum(r.bars_inserted for r in results),
        total_broker=sum(r.broker_rows_inserted for r in results),
    )


async def _seed_symbols(client, store: Store, *, now: datetime) -> list[str]:
    """The backfill seed = current SCR-0 pull. Prefer the store's cached survivor set;
    fall back to a live SCR-0 run when the store has none yet."""
    cached = store.scr0_universe(now)
    if cached:
        return cached
    rows = await run_scr0(client, store, trading_day=last_weekday(now.date()), now=now)
    return [r.symbol for r in rows]


def _budget_line(symbols: list[str], today: Date) -> str:
    """Up-front paywall budget: ≈ trading_days × names broker calls (the worst case;
    ingest-once collapses it to only the missing days on a resume)."""
    span_a = max((today - config.REGIME_START_TRACK_A).days, 0)
    approx_days = round(span_a * 5 / 7)  # weekdays only (the trading-day proxy)
    return (
        f"backfill budget: {len(symbols)} names × up to ~{approx_days} trading days "
        f"≈ {len(symbols) * approx_days} broker-day calls worst case "
        f"(ingest-once → only missing days on a resume)"
    )


async def _run(
    symbols: list[str] | None,
    db_path: str,
    load_roster: bool,
    *,
    now: datetime,
    client_factory: Callable = build_live_client,
    store_factory: Callable[[str], Store] = Store,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> int:
    transport = store = None
    today = now.date()
    try:
        client, transport = client_factory()
        store = store_factory(db_path)

        if load_roster:
            try:
                report = load_rosters(store, now=now)
                print(
                    f"rosters: +{report.rows_written} periods from "
                    f"{len(report.files_read)} file(s) {list(report.indexes)}"
                )
            except RosterValidationError as exc:
                print(f"ROSTER LOAD FAILED: {exc}", file=sys.stderr)
                return 3

        seed = [s.upper() for s in symbols] if symbols else await _seed_symbols(
            client, store, now=now
        )
        if not seed:
            print("empty seed universe — run the screener/bootstrap first", file=sys.stderr)
            return 1
        print(_budget_line(seed, today))

        report = await backfill(client, store, seed, now=now, today=today, sleep=sleep)
    except AuthError as exc:
        print(f"AUTH FAILED — run `./run.sh login` first: {exc}", file=sys.stderr)
        return 1
    except ExodusError as exc:
        print(f"transport/exodus error: {exc}", file=sys.stderr)
        return 2
    finally:
        if transport is not None:
            await transport.aclose()
        if store is not None:
            store.close()

    print(
        f"done — {report.total_bars} bars, {report.total_broker} broker rows across "
        f"{len(report.results)}/{len(report.symbols)} names into {db_path}"
    )
    if report.failed_symbol is not None:
        print(
            f"stopped at {report.failed_symbol} — re-run to resume "
            f"(completed names are cached no-ops)",
            file=sys.stderr,
        )
        return 2
    return 0


def main(argv: list[str] | None = None, **overrides) -> int:
    """CLI entry. `overrides` (client_factory/store_factory/sleep) are for tests only."""
    configure_logging()
    parser = argparse.ArgumentParser(
        prog="currentflow.ingest.backfill",
        description="Regime-scoped historical backfill (slice 20).",
    )
    parser.add_argument("symbols", nargs="*",
                        help="seed ticker(s); default = current SCR-0 pull")
    parser.add_argument("--db", default=DEFAULT_DB,
                        help=f"DuckDB path — must match the terminal (default {DEFAULT_DB})")
    parser.add_argument("--rosters", action="store_true",
                        help="also (re)load point-in-time rosters from data/rosters/")
    args = parser.parse_args(argv)
    return asyncio.run(
        _run(args.symbols or None, args.db, args.rosters, now=datetime.now(), **overrides)
    )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
