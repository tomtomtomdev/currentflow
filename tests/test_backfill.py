"""Slice 20 §17.4 — regime-scoped backfill acceptance tests."""

from __future__ import annotations

from datetime import date as Date
from datetime import datetime, timedelta
from types import SimpleNamespace

from currentflow.dal.models import BrokerNet, DailyBar, InvestorType, RowStatus, Side
from currentflow.dal.timing import ohlcv_as_of
from currentflow.ingest.backfill import backfill
from currentflow.store.db import Store

NOW = datetime(2026, 1, 10, 9, 0)


async def _noop_sleep(_s: float) -> None:
    return None


class FakeClient:
    """Records broker-summary calls (the transport spy) and serves deterministic bars."""

    def __init__(self, indexes: tuple[str, ...] = ()) -> None:
        self.broker_calls = 0
        self.ohlcv_calls = 0
        self._indexes = indexes

    async def symbol_info(self, symbol: str):
        return SimpleNamespace(indexes=self._indexes)

    async def broker_summary(self, symbol: str, day: Date):
        self.broker_calls += 1
        as_of = datetime.combine(day + timedelta(days=1), datetime.min.time()).replace(hour=9)
        return [
            BrokerNet(symbol, day, as_of, "DX", Side.BUY, InvestorType.LOCAL, 100.0, 5e9, 1, 1),
            BrokerNet(symbol, day, as_of, "YP", Side.SELL, InvestorType.LOCAL, 100.0, 5e9, 1, 1),
        ]

    async def ohlcv_foreign(self, symbol: str, lo: Date, hi: Date):
        self.ohlcv_calls += 1
        out, d = [], lo
        while d <= hi:
            if d.weekday() < 5:
                out.append(DailyBar(
                    symbol=symbol, date=d, as_of=ohlcv_as_of(d), status=RowStatus.TRADED,
                    open=100.0, high=101.0, low=99.0, close=100.0, volume=1000,
                    value=1e11, frequency=100, vwap=100.0, foreign_buy=None,
                    foreign_sell=None, net_foreign=None, change_percentage=None,
                ))
            d += timedelta(days=1)
        return out


async def test_backfill_second_run_issues_zero_broker_calls():
    store = Store(":memory:")
    client = FakeClient()
    today = Date(2024, 8, 7)  # small window past the Track B boundary (2024-07-01)

    r1 = await backfill(client, store, ["AAA"], now=NOW, today=today,
                        sleep=_noop_sleep, log=lambda _m: None)
    assert r1.total_bars > 0
    first = client.broker_calls
    assert first > 0

    # A re-run over the completed range must be a pure cache no-op (transport spy).
    r2 = await backfill(client, store, ["AAA"], now=NOW, today=today,
                        sleep=_noop_sleep, log=lambda _m: None)
    assert client.broker_calls == first  # zero new broker calls
    assert r2.total_bars == 0


async def test_backfill_member_uses_the_earlier_track_a_window():
    store = Store(":memory:")
    client = FakeClient(indexes=("LQ45",))
    today = Date(2024, 8, 7)

    await backfill(client, store, ["BBCA"], now=NOW, today=today,
                   sleep=_noop_sleep, log=lambda _m: None)
    bars = store.read_daily_bars("BBCA", NOW)
    # LQ45 member → Track A window → reaches back to the 2024-01-01 boundary.
    assert min(b.date for b in bars) < Date(2024, 7, 1)


async def test_backfill_paces_between_names():
    store = Store(":memory:")
    client = FakeClient()
    pauses: list[float] = []

    async def spy_sleep(s: float) -> None:
        pauses.append(s)

    await backfill(client, store, ["AAA", "BBB", "CCC"], now=NOW, today=Date(2024, 7, 8),
                   pause_s=1.0, sleep=spy_sleep, log=lambda _m: None)
    assert pauses == [1.0, 1.0]  # paused between names, not after the last
