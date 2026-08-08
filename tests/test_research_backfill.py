"""Tests for the strided research backfill.

The strided pull deliberately produces a SPARSE `broker_net` — broker rows only on the
days the premise harness samples. That is safe on its own store and dangerous on the
production one, because `ingest.pipeline` keys ingest-once on `daily_bar`: complete bars
would make a later production run believe broker is complete too. These tests pin both
the sparsity and the guard.
"""

from __future__ import annotations

from datetime import date as Date
from datetime import datetime, timedelta

import pytest

from currentflow.dal.models import BrokerNet, DailyBar, InvestorType, RowStatus, Side
from currentflow.research.backfill import (
    PRODUCTION_DB_NAMES,
    broker_days_for,
    guard_target_db,
    plan_budget,
    run_research_backfill,
)

AS_OF = datetime(2026, 8, 8, 6, 0)
NOW = datetime(2026, 8, 8, 18, 0)


def _weekdays(n: int, start: Date = Date(2025, 1, 6)) -> list[Date]:
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _bar(sym: str, d: Date) -> DailyBar:
    return DailyBar(symbol=sym, date=d, as_of=AS_OF, status=RowStatus.TRADED,
                    open=100.0, high=100.0, low=100.0, close=100.0, volume=1000,
                    value=100_000.0, frequency=10, vwap=100.0, foreign_buy=None,
                    foreign_sell=None, net_foreign=0.0, change_percentage=0.0)


def _broker(sym: str, d: Date) -> BrokerNet:
    return BrokerNet(symbol=sym, date=d, as_of=AS_OF, broker_code="AA", side=Side.BUY,
                     investor_type=InvestorType.LOCAL, avg_price=100.0, value=1.0,
                     lot=1, frequency=1)


class FakeClient:
    """Records every call so the tests can assert the CALL BUDGET, which is the whole
    point of striding."""

    def __init__(self, days: list[Date]):
        self._days = days
        self.broker_calls: list[tuple[str, Date]] = []
        self.ohlcv_calls: list[tuple[str, Date, Date]] = []

    async def ohlcv_foreign(self, symbol, date_from, date_to):
        self.ohlcv_calls.append((symbol, date_from, date_to))
        return [_bar(symbol, d) for d in self._days if date_from <= d <= date_to]

    async def broker_summary(self, symbol, day):
        self.broker_calls.append((symbol, day))
        return [_broker(symbol, day)]


class FakeStore:
    def __init__(self):
        self.bars: dict[str, dict[Date, DailyBar]] = {}
        self.broker: dict[str, dict[Date, list[BrokerNet]]] = {}

    def symbols(self, table: str = "daily_bar") -> list[str]:
        return sorted(self.bars)

    def ingested_dates(self, symbol, table: str = "daily_bar") -> set[Date]:
        if table == "broker_net":
            return set(self.broker.get(symbol, {}))
        return set(self.bars.get(symbol, {}))

    def write_daily_bars(self, bars) -> int:
        n = 0
        for b in bars:
            self.bars.setdefault(b.symbol, {}).setdefault(b.date, b)
            n += 1
        return n

    def write_broker_net(self, rows) -> int:
        n = 0
        for r in rows:
            self.broker.setdefault(r.symbol, {}).setdefault(r.date, []).append(r)
            n += 1
        return n

    def read_daily_bars(self, symbol, decision_ts, start=None, end=None, *, clamp_regime=None):
        return sorted(
            (b for b in self.bars.get(symbol, {}).values()
             if b.as_of < decision_ts
             and (start is None or b.date >= start) and (end is None or b.date <= end)),
            key=lambda b: b.date,
        )


# --- the production-store guard -------------------------------------------------------


def test_guard_rejects_the_production_db():
    """A strided pull into the production store would leave permanent broker holes:
    complete bars make `ingest.pipeline`'s ingest-once marker report the symbol done."""
    for name in PRODUCTION_DB_NAMES:
        with pytest.raises(ValueError, match="ingest-once"):
            guard_target_db(name)


def test_guard_rejects_production_db_via_path():
    with pytest.raises(ValueError, match="ingest-once"):
        guard_target_db("/some/dir/currentflow.duckdb")


def test_guard_allows_a_separate_research_db():
    guard_target_db("research.duckdb")
    guard_target_db("/tmp/scratch.duckdb")


# --- broker-day selection -------------------------------------------------------------


def test_broker_days_are_the_trading_day_before_each_decision_day():
    days = _weekdays(30)
    decision = days[10::5]                       # 10, 15, 20, 25
    got = broker_days_for(days, decision)
    assert got == [days[9], days[14], days[19], days[24]]


def test_broker_days_skip_a_decision_day_with_no_prior_bar():
    days = _weekdays(30)
    assert broker_days_for(days, [days[0]]) == []


def test_broker_days_are_deduplicated_and_sorted():
    days = _weekdays(30)
    got = broker_days_for(days, [days[5], days[5], days[3]])
    assert got == [days[2], days[4]]


# --- the budget -----------------------------------------------------------------------


def test_budget_scales_with_stride_not_with_days():
    """The finding that justifies this module: broker cost falls ~stride-fold."""
    strided = plan_budget(n_symbols=200, n_trading_days=512, stride_days=10)
    dense = plan_budget(n_symbols=200, n_trading_days=512, stride_days=1)
    assert strided.broker_calls < dense.broker_calls / 9
    assert dense.broker_calls == 200 * 512


# --- the run --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_broker_fetched_only_on_strided_days():
    days = _weekdays(40)
    client, store = FakeClient(days), FakeStore()
    report = await run_research_backfill(
        client, store, ["AAA", "BBB"], start=days[0], end=days[-1],
        stride_days=10, now=NOW,
    )
    # bars: every day for both names; broker: only the strided subset
    assert len(store.bars["AAA"]) == 40
    assert len(store.broker["AAA"]) == len(report.broker_days)
    assert len(store.broker["AAA"]) < 40
    assert len(client.broker_calls) == len(report.broker_days) * 2


@pytest.mark.asyncio
async def test_rerun_is_a_no_op_keyed_on_broker_net_not_bars():
    """Resumability: because the strided path keys broker on `broker_net` (not on the
    bars marker), a second run re-issues zero broker calls — and a partial first run
    resumes correctly."""
    days = _weekdays(40)
    client, store = FakeClient(days), FakeStore()
    kw = dict(start=days[0], end=days[-1], stride_days=10, now=NOW)
    await run_research_backfill(client, store, ["AAA"], **kw)
    first = len(client.broker_calls)
    assert first > 0

    await run_research_backfill(client, store, ["AAA"], **kw)
    assert len(client.broker_calls) == first, "re-run should issue no new broker calls"


@pytest.mark.asyncio
async def test_partial_broker_coverage_resumes():
    days = _weekdays(40)
    client, store = FakeClient(days), FakeStore()
    store.write_daily_bars([_bar("AAA", d) for d in days])
    store.write_broker_net([_broker("AAA", days[9])])   # one day pre-seeded

    report = await run_research_backfill(
        client, store, ["AAA"], start=days[0], end=days[-1], stride_days=10, now=NOW,
    )
    assert days[9] not in [d for _, d in client.broker_calls]
    assert report.broker_days_skipped_cached == 1


@pytest.mark.asyncio
async def test_report_names_the_sparsity():
    """No silent caps: the report must state that broker coverage is deliberately
    incomplete, so a later reader cannot mistake it for a full store."""
    days = _weekdays(40)
    client, store = FakeClient(days), FakeStore()
    report = await run_research_backfill(
        client, store, ["AAA"], start=days[0], end=days[-1], stride_days=10, now=NOW,
    )
    assert report.broker_sparse is True
    assert "sparse" in report.warning.lower()
    assert str(report.stride_days) in report.warning
