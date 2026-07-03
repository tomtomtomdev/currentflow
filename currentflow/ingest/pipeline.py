"""Nightly-incremental ingest for one symbol (Slice 1: OHLCV + broker summary).

Ingest-once: only the trading days not already stored are fetched (DATA_SOURCES §4).
No silent caps: the number of already-cached days skipped and any coverage gaps are
logged, never swallowed.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date as Date
from datetime import datetime, timedelta

from currentflow.dal.client import ExodusClient
from currentflow.store.db import Store
from currentflow.store.integrity import CoverageReport, classify_coverage

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class IngestResult:
    symbol: str
    bars_inserted: int
    broker_rows_inserted: int
    days_skipped_cached: int
    coverage: CoverageReport


def _weekdays(start: Date, end: Date) -> list[Date]:
    days, d = [], start
    while d <= end:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return days


async def ingest_symbol(
    client: ExodusClient,
    store: Store,
    symbol: str,
    start: Date,
    end: Date,
    *,
    now: datetime,
) -> IngestResult:
    """Fetch only the missing trading days in [start, end], store them, report coverage."""
    already = store.ingested_dates(symbol)
    wanted = _weekdays(start, end)
    missing = [d for d in wanted if d not in already]
    skipped = len(wanted) - len(missing)

    bars_inserted = 0
    broker_inserted = 0
    if missing:
        lo, hi = missing[0], missing[-1]
        if skipped:
            log.info(
                "ingest %s: %d/%d trading days already cached, fetching %s..%s",
                symbol, skipped, len(wanted), lo, hi,
            )
        bars = await client.ohlcv_foreign(symbol, lo, hi)
        bars_inserted = store.write_daily_bars(bars)

        broker = await client.broker_summary(symbol, lo, hi)
        broker_inserted = store.write_broker_net(broker)
    else:
        log.info("ingest %s: nothing to fetch, all %d days cached", symbol, len(wanted))

    # Coverage is judged over what is now stored (point-in-time as of `now`).
    stored_bars = store.read_daily_bars(symbol, decision_ts=now, start=start, end=end)
    coverage = classify_coverage(symbol, start, end, stored_bars, now=now)

    return IngestResult(
        symbol=symbol,
        bars_inserted=bars_inserted,
        broker_rows_inserted=broker_inserted,
        days_skipped_cached=skipped,
        coverage=coverage,
    )


async def ingest_universe(
    client: ExodusClient,
    store: Store,
    symbols: Iterable[str],
    start: Date,
    end: Date,
    *,
    now: datetime,
) -> list[IngestResult]:
    """Ingest each symbol in turn over [start, end], returning one result per symbol.

    Sequential by design: a single operator session, one feed method per call
    (CLAUDE.md DAL rules) — the shared retry/backoff already paces the endpoint, so
    fanning out would only risk tripping paywall/rate-limit backoff. Ingest-once means
    re-runs are cheap no-ops.
    """
    return [
        await ingest_symbol(client, store, symbol, start, end, now=now)
        for symbol in symbols
    ]
