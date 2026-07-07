"""Gap-vs-zero test (acceptance criterion): 'no trades' ≠ 'not published' ≠ 'gap',
and a gap is NEVER read as zero flow (CLAUDE.md, DATA_SOURCES §4)."""

from __future__ import annotations

from datetime import date, datetime

from currentflow.dal.models import BrokerNet, DailyBar, InvestorType, RowStatus, Side
from currentflow.dal.timing import ohlcv_as_of
from currentflow.store.integrity import broker_market_clears, classify_coverage


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


# --- broker-summary conservation (the AK@MEDC regression guard) ------------------------


def _bnet(broker: str, side: Side, value: float | None) -> BrokerNet:
    return BrokerNet(
        symbol="MEDC", date=date(2026, 6, 1),
        as_of=datetime(2026, 6, 2, 9, 0), broker_code=broker, side=side,
        investor_type=InvestorType.FOREIGN, avg_price=None, value=value,
        lot=None, frequency=None,
    )


def test_broker_market_clears_when_buy_equals_sell():
    # magnitudes with side carrying direction (post-fix convention): gross buy == gross sell
    rows = [
        _bnet("AK", Side.BUY, 221_380), _bnet("ZP", Side.BUY, 61_340),
        _bnet("AK", Side.SELL, 282_720),
    ]
    report = broker_market_clears("MEDC", rows)
    assert report.gross_buy == 282_720
    assert report.gross_sell == 282_720
    assert report.imbalance == 0.0
    assert report.clears


def test_broker_market_does_not_clear_on_signed_sell_bug():
    # the pre-fix defect: a net-seller's value stored SIGNED-negative makes gross_sell
    # collapse — the imbalance the guard exists to surface.
    rows = [_bnet("AK", Side.BUY, 221_380), _bnet("AK", Side.SELL, -282_720)]
    report = broker_market_clears("MEDC", rows)
    assert not report.clears
    assert report.imbalance > 0.01


def test_broker_market_clears_counts_dropped_never_zero():
    rows = [
        _bnet("AK", Side.BUY, 100_000), _bnet("AK", Side.SELL, 100_000),
        _bnet("XX", Side.SELL, None),  # unknown value: dropped, not read as zero
    ]
    report = broker_market_clears("MEDC", rows)
    assert report.dropped == 1
    assert report.clears
