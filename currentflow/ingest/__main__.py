"""Ingest CLI — populate the local store so the terminal has data to render.

This is the runnable entry point behind the terminal's "run the ingest pipeline
first" warning: the UI reads only from the local DuckDB store, and login only
establishes the session — it never pulls data. Run this once per universe to fill
the store, then reload the terminal.

    python -m currentflow.ingest BBCA BBRI TLKM          # last --days into currentflow.duckdb
    python -m currentflow.ingest BBCA --from 2026-04-01 --to 2026-07-03
    python -m currentflow.ingest BBCA --days 30 --db currentflow.duckdb

Uses the operator's OWN authenticated Stockbit session (Keychain Bearer, own risk
§15) via `build_live_client` — so `./run.sh login` must have succeeded first. Writes
to the SAME db path the terminal reads (default `currentflow.duckdb`, override with
`--db`; the terminal takes `--db` too). Ingest-once: only missing trading days are
fetched, re-runs are no-ops. Coverage gaps and cached-skip counts are printed, never
swallowed (no silent caps — CLAUDE.md).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import date as Date
from datetime import datetime, timedelta
from typing import Callable

from currentflow.dal.errors import AuthError, ExodusError
from currentflow.dal.session import build_live_client
from currentflow.ingest.bootstrap import DEFAULT_DAYS
from currentflow.ingest.pipeline import ingest_universe
from currentflow.logging_setup import configure_logging
from currentflow.store.db import Store

# Same default the terminal reads (currentflow/ui/app.py:_db_path). Keep in lockstep:
# ingesting to a different path than the UI reads is the silent way to keep the
# "run the ingest pipeline first" warning even after a successful ingest.
DEFAULT_DB = "currentflow.duckdb"


def _resolve_range(from_iso: str | None, to_iso: str | None, days: int) -> tuple[Date, Date]:
    """[start, end]; end defaults to today, start to end − `days` when unset."""
    end = Date.fromisoformat(to_iso) if to_iso else datetime.now().date()
    start = Date.fromisoformat(from_iso) if from_iso else end - timedelta(days=days)
    return start, end


async def _ingest(
    symbols: list[str],
    start: Date,
    end: Date,
    db_path: str,
    *,
    now: datetime,
    client_factory: Callable = build_live_client,
    store_factory: Callable[[str], Store] = Store,
) -> int:
    """Wire the live client + store, ingest every symbol, report coverage. Fail loud
    on auth/transport errors (never leave a half-open pool). Factories are injectable
    so tests drive a scripted transport + in-memory store without network/Keychain."""
    transport = store = None
    try:
        client, transport = client_factory()
        store = store_factory(db_path)
        results = await ingest_universe(client, store, symbols, start, end, now=now)
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

    total_bars = sum(r.bars_inserted for r in results)
    total_broker = sum(r.broker_rows_inserted for r in results)
    gapped = []
    for r in results:
        line = (
            f"  {r.symbol}: +{r.bars_inserted} bars, +{r.broker_rows_inserted} broker "
            f"rows, {r.days_skipped_cached} cached-skip"
        )
        if r.coverage.has_gaps:
            line += f", GAPS on {len(r.coverage.gaps)} day(s)"
            gapped.append(r.symbol)
        print(line)

    print(
        f"done — {total_bars} bars, {total_broker} broker rows across "
        f"{len(results)} symbol(s) [{start}..{end}] into {db_path}"
    )
    if gapped:
        # Missing ≠ zero: surface gaps rather than let an empty read read as "no flow".
        print(f"coverage gaps in: {', '.join(gapped)} (missing ≠ zero — see logs/net.log)")
    return 0


def main(argv: list[str] | None = None, **overrides) -> int:
    """CLI entry. `overrides` (client_factory/store_factory) are for tests only."""
    configure_logging()  # persist dal `net-error` lines to logs/net.log
    parser = argparse.ArgumentParser(
        prog="currentflow.ingest",
        description="Fill the local store for the terminal (ingest-once).",
    )
    parser.add_argument("symbols", nargs="+", help="IDX ticker(s), e.g. BBCA BBRI")
    parser.add_argument("--from", dest="from_iso", metavar="YYYY-MM-DD",
                        help="range start (default: end − --days)")
    parser.add_argument("--to", dest="to_iso", metavar="YYYY-MM-DD",
                        help="range end (default: today)")
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS,
                        help=f"lookback when --from omitted (default {DEFAULT_DAYS})")
    parser.add_argument("--db", default=DEFAULT_DB,
                        help=f"DuckDB path — must match the terminal (default {DEFAULT_DB})")
    args = parser.parse_args(argv)

    try:
        start, end = _resolve_range(args.from_iso, args.to_iso, args.days)
    except ValueError as exc:
        print(f"bad date — use YYYY-MM-DD: {exc}", file=sys.stderr)
        return 1
    if start > end:
        print(f"empty range: start {start} is after end {end}", file=sys.stderr)
        return 1

    symbols = [s.upper() for s in args.symbols]
    return asyncio.run(
        _ingest(symbols, start, end, args.db, now=datetime.now(), **overrides)
    )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
