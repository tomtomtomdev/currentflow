"""Gap-vs-zero test (acceptance criterion): 'no trades' ≠ 'not published' ≠ 'gap',
and a gap is NEVER read as zero flow (CLAUDE.md, DATA_SOURCES §4)."""

from __future__ import annotations

from datetime import date, datetime

from currentflow.dal.models import DailyBar, RowStatus
from currentflow.dal.timing import ohlcv_as_of
from currentflow.store.integrity import classify_coverage


def _bar(d: date, *, volume: int, frequency: int) -> DailyBar:
    status = RowStatus.TRADED if (volume > 0 or frequency > 0) else RowStatus.NO_TRADES
    return DailyBar(
        symbol="XBIG", date=d, as_of=ohlcv_as_of(d), status=status,
        open=None, high=None, low=None, close=None, volume=volume, value=None,
        frequency=frequency, vwap=None, foreign_buy=None, foreign_sell=None,
        net_foreign=None, change_percentage=None,
    )


def test_classify_distinguishes_all_four_states():
    start, end = date(2026, 6, 1), date(2026, 6, 5)  # Mon..Fri
    bars = [
        _bar(date(2026, 6, 1), volume=1000, frequency=10),  # TRADED
        _bar(date(2026, 6, 2), volume=0, frequency=0),      # NO_TRADES (present, all-zero)
        # 06-03 is a holiday (injected) → excluded
        # 06-04 missing, past its publish horizon → GAP
        # 06-05 missing, before its publish horizon → NOT_PUBLISHED
    ]
    now = datetime(2026, 6, 4, 20, 0)  # after 06-04 close, before 06-05 close
    report = classify_coverage(
        "XBIG", start, end, bars, now=now, holidays=frozenset({date(2026, 6, 3)})
    )

    assert report.traded == [date(2026, 6, 1)]
    assert report.no_trades == [date(2026, 6, 2)]
    assert report.gaps == [date(2026, 6, 4)]
    assert report.not_published == [date(2026, 6, 5)]

    # holiday is neither a gap nor a fabricated zero — it is simply not expected
    assert date(2026, 6, 3) not in report.status_by_date
    assert report.has_gaps is True


def test_no_trades_is_not_a_gap_and_not_dropped():
    # The whole point of 'empty ≠ zero': an all-zero illiquid day is a real,
    # classified observation (NO_TRADES) — NOT a gap, NOT silently zeroed.
    d = date(2026, 6, 2)  # a Tuesday
    bar = _bar(d, volume=0, frequency=0)
    report = classify_coverage(
        "XBIG", d, d, [bar], now=datetime(2026, 6, 3, 20, 0)
    )
    assert report.status_by_date[d] is RowStatus.NO_TRADES
    assert report.gaps == []


def test_missing_recent_day_is_not_published_not_gap():
    d = date(2026, 6, 5)  # Friday
    # now is before Friday's close → data legitimately not out yet
    report = classify_coverage(
        "XBIG", d, d, [], now=datetime(2026, 6, 5, 10, 0)
    )
    assert report.not_published == [d]
    assert report.gaps == []
