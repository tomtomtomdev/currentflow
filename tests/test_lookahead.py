"""Look-ahead test (acceptance criterion, spec §1): a read for `decision_ts` returns
ONLY rows with `as_of < decision_ts`, collapsed to the latest visible `as_of` per date.
No signal may ever consume a datum stamped available at/after the decision instant."""

from __future__ import annotations

from datetime import date, datetime

from currentflow.dal.models import DailyBar, RowStatus


def _bar(d: date, as_of: datetime, close: float) -> DailyBar:
    return DailyBar(
        symbol="BBCA", date=d, as_of=as_of, status=RowStatus.TRADED,
        open=close, high=close, low=close, close=close, volume=100, value=close * 100,
        frequency=5, vwap=close, foreign_buy=None, foreign_sell=None, net_foreign=None,
        change_percentage=None,
    )


def test_read_excludes_records_not_yet_available(store):
    d = date(2026, 6, 1)
    store.write_daily_bars([_bar(d, datetime(2026, 6, 1, 16, 15), 100.0)])

    # decision before availability → nothing visible
    assert store.read_daily_bars("BBCA", decision_ts=datetime(2026, 6, 1, 16, 0)) == []
    # decision after availability → visible
    got = store.read_daily_bars("BBCA", decision_ts=datetime(2026, 6, 1, 17, 0))
    assert [b.close for b in got] == [100.0]


def test_read_boundary_is_strict_less_than(store):
    d = date(2026, 6, 1)
    as_of = datetime(2026, 6, 1, 16, 15)
    store.write_daily_bars([_bar(d, as_of, 100.0)])
    # as_of == decision_ts must be EXCLUDED (strict <, look-ahead-safe)
    assert store.read_daily_bars("BBCA", decision_ts=as_of) == []


def test_latest_visible_as_of_wins(store):
    d = date(2026, 6, 1)
    # original bar + a later revision of the SAME trading day
    store.write_daily_bars([
        _bar(d, datetime(2026, 6, 1, 16, 15), 100.0),
        _bar(d, datetime(2026, 6, 2, 10, 0), 105.0),  # revision, published next morning
    ])

    # decision sees only the original revision
    got = store.read_daily_bars("BBCA", decision_ts=datetime(2026, 6, 1, 17, 0))
    assert [b.close for b in got] == [100.0]

    # decision after both → the later revision wins, and only ONE row per date
    got = store.read_daily_bars("BBCA", decision_ts=datetime(2026, 6, 3))
    assert [b.close for b in got] == [105.0]
