"""Cache-idempotency test (acceptance criterion): never re-pull / re-insert a stored
`(symbol, date, as_of)` (DATA_SOURCES §4)."""

from __future__ import annotations

from datetime import date, datetime

from currentflow.dal.models import DailyBar, RowStatus
from currentflow.dal.timing import ohlcv_as_of


def _bar(sym: str, d: date, close: float) -> DailyBar:
    return DailyBar(
        symbol=sym, date=d, as_of=ohlcv_as_of(d), status=RowStatus.TRADED,
        open=close, high=close, low=close, close=close, volume=100, value=close * 100,
        frequency=5, vwap=close, foreign_buy=0.0, foreign_sell=0.0, net_foreign=0.0,
        change_percentage=0.0,
    )


def test_ingest_once_second_write_is_noop(store):
    bars = [_bar("BBCA", date(2026, 6, d), 100 + d) for d in (1, 2, 3)]

    assert store.write_daily_bars(bars) == 3          # first write inserts all
    assert store.write_daily_bars(bars) == 0          # identical re-write: no-op
    # count is stable — no duplicates
    rows = store.read_daily_bars("BBCA", decision_ts=datetime(2030, 1, 1))
    assert len(rows) == 3


def test_ingested_dates_reflects_stored(store):
    store.write_daily_bars([_bar("BBRI", date(2026, 6, d), 200 + d) for d in (1, 2)])
    assert store.ingested_dates("BBRI") == {date(2026, 6, 1), date(2026, 6, 2)}
    assert store.ingested_dates("NONE") == set()


def test_partial_overlap_inserts_only_new(store):
    store.write_daily_bars([_bar("TLKM", date(2026, 6, 1), 300)])
    # re-submit day 1 (cached) + day 2 (new): only day 2 is inserted
    inserted = store.write_daily_bars([
        _bar("TLKM", date(2026, 6, 1), 300),
        _bar("TLKM", date(2026, 6, 2), 301),
    ])
    assert inserted == 1
    assert store.ingested_dates("TLKM") == {date(2026, 6, 1), date(2026, 6, 2)}
