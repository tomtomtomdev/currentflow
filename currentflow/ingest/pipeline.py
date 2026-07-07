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
from currentflow.store.integrity import (
    ClearingReport,
    CoverageReport,
    broker_market_clears,
    classify_coverage,
)

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class IngestResult:
    symbol: str
    bars_inserted: int
    broker_rows_inserted: int
    days_skipped_cached: int
    coverage: CoverageReport
    clearing: list[ClearingReport]  # one per fetched day; empty when nothing fetched

    @property
    def unclear(self) -> list[ClearingReport]:
        """Fetched days whose broker rows failed the buy≈sell conservation check —
        a truncated feed, dropped rows, or a broken sign convention (never a gap)."""
        return [c for c in self.clearing if not c.clears]

    @property
    def has_imbalance(self) -> bool:
        return any(not c.clears for c in self.clearing)


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
    clearing: list[ClearingReport] = []
    if missing:
        lo, hi = missing[0], missing[-1]
        if skipped:
            log.info(
                "ingest %s: %d/%d trading days already cached, fetching %s..%s",
                symbol, skipped, len(wanted), lo, hi,
            )
        # Broker rows come one call per missing day (the endpoint aggregates any
        # multi-day range — live-verified, slice 13). Bars are fetched and written
        # LAST as the ingest-once commit marker: `ingested_dates` keys on daily_bar,
        # so a failure anywhere mid-symbol leaves every day still "missing" and a
        # retry re-fetches the whole symbol (deterministic `as_of` makes re-written
        # broker rows exact-key no-ops) — never a permanent broker hole.
        # Conservation is checked PER DAY, on the freshly fetched rows — a top-N
        # truncation on one day is masked once summed across a range. This is the
        # ingest-time guard that stops a broken feed (AK@MEDC) reaching the screen.
        broker: list = []
        for day in missing:
            day_rows = await client.broker_summary(symbol, day)
            broker.extend(day_rows)
            clearing.append(broker_market_clears(symbol, day_rows, date=day))
        broker_inserted = store.write_broker_net(broker)

        bars = await client.ohlcv_foreign(symbol, lo, hi)
        bars_inserted = store.write_daily_bars(bars)
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
        clearing=clearing,
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
