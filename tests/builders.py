"""Shared test builders: labeled OHLCV charts and broker-flow rows.

Kept out of conftest so both the phase archetypes and the engine/SMS/veto tests use
one source of truth for what a "Phase C chart" or a "concentrated buyer" looks like.
"""

from __future__ import annotations

from datetime import date as Date
from datetime import datetime, timedelta

from currentflow.dal.models import BrokerNet, DailyBar, InvestorType, RowStatus, Side
from currentflow.dal.timing import ohlcv_as_of


class Chart:
    """Weekday-stamped OHLCV bar builder for labeled Wyckoff phase archetypes."""

    def __init__(self, symbol: str = "TEST", start: Date = Date(2026, 1, 5)) -> None:
        self.symbol = symbol
        self.d = start
        self.bars: list[DailyBar] = []

    def add(
        self, o: float, h: float, l: float, c: float, v: float = 1000.0,
        nf: float | None = None,
    ) -> "Chart":
        while self.d.weekday() >= 5:
            self.d += timedelta(days=1)
        self.bars.append(
            DailyBar(
                symbol=self.symbol, date=self.d, as_of=ohlcv_as_of(self.d),
                status=RowStatus.TRADED, open=o, high=h, low=l, close=c,
                volume=int(v), value=c * v, frequency=100, vwap=(h + l + c) / 3,
                foreign_buy=None, foreign_sell=None, net_foreign=nf,
                change_percentage=None,
            )
        )
        self.d += timedelta(days=1)
        return self

    def oscillate(self, n: int, support: float = 100, resistance: float = 120, v: float = 1000) -> "Chart":
        for i in range(n):
            if i % 2 == 0:
                self.add(support + 4, support + 8, support, support + 6, v)
            else:
                self.add(resistance - 4, resistance, resistance - 8, resistance - 6, v)
        return self

    @property
    def last_date(self) -> Date:
        return self.bars[-1].date


def downtrend_bars(symbol: str = "TEST") -> list[DailyBar]:
    ch = Chart(symbol)
    for i in range(45):
        c = 200 - 2.6 * i
        ch.add(o=c + 2, h=c + 3, l=c - 1, c=c)
    return ch.bars


def phase_a_bars(symbol: str = "TEST") -> list[DailyBar]:
    ch = Chart(symbol)
    for i in range(20):
        c = 160 - 2.7 * i
        ch.add(o=c + 2, h=c + 3, l=c - 1, c=c)
    ch.add(o=110, h=111, l=100, c=101, v=3000)
    for i in range(1, 8):
        c = 100 + 4 * i
        ch.add(o=c - 3, h=c + 2, l=c - 4, c=c, v=1200)
    for i in range(16):
        if i % 2 == 0:
            ch.add(o=106, h=110, l=104, c=108, v=800)
        else:
            ch.add(o=126, h=126, l=118, c=120, v=800)
    return ch.bars


def phase_b_bars(symbol: str = "TEST") -> list[DailyBar]:
    return Chart(symbol).oscillate(44).bars


def phase_c_bars(symbol: str = "TEST") -> list[DailyBar]:
    ch = Chart(symbol).oscillate(40)
    ch.add(o=101, h=103, l=98, c=102, v=1200)   # spring
    return ch.bars


def phase_d_bars(symbol: str = "TEST") -> list[DailyBar]:
    ch = Chart(symbol).oscillate(36)
    ch.add(o=119, h=123, l=118, c=121, v=2500)  # SOS
    for _ in range(4):
        ch.add(o=112, h=116, l=112, c=114, v=1000)  # LPS
    return ch.bars


def strong_phase_c_bars(symbol: str = "STRONG") -> list[DailyBar]:
    """A Phase C chart that also carries price-volume divergence: a cluster of
    high-volume bars with flat closes (absorption) inside the range, then a spring."""
    ch = Chart(symbol).oscillate(30)
    ch.add(104, 108, 100, 106, 1000)              # bridge close ~106
    for _ in range(6):
        ch.add(105, 107, 105, 106, 2500)          # absorption: flat close, high volume
    ch.add(104, 108, 100, 106, 1000)              # bridge back
    ch.add(112, 120, 110, 114, 1000)              # probe resistance
    ch.add(101, 103, 98, 102, 1200)               # spring (Phase C)
    return ch.bars


def phase_e_bars(symbol: str = "TEST") -> list[DailyBar]:
    ch = Chart(symbol).oscillate(40)
    ch.add(o=121, h=136, l=120, c=135, v=2000)
    return ch.bars


def distribution_bars(symbol: str = "TEST") -> list[DailyBar]:
    ch = Chart(symbol).oscillate(40)
    ch.add(o=119, h=125, l=115, c=117, v=1500)  # UTAD
    return ch.bars


# --- broker rows -------------------------------------------------------------------


def brow(
    broker: str,
    side: Side,
    value: float,
    day: Date,
    *,
    symbol: str = "TEST",
    investor: InvestorType = InvestorType.LOCAL,
    as_of: datetime | None = None,
    avg_price: float | None = None,
) -> BrokerNet:
    return BrokerNet(
        symbol=symbol, date=day,
        as_of=as_of or datetime.combine(day + timedelta(days=1), datetime.min.time().replace(hour=9)),
        broker_code=broker, side=side, investor_type=investor,
        avg_price=avg_price, value=value, lot=None, frequency=None,
    )


def two_buyer_rows(
    symbol: str, days: list[Date], *, each: float = 5e9
) -> list[BrokerNet]:
    """Two near-equal persistent accumulators (top-2 share ~1.0, top-1 = 50% — no
    monopoly), retail on the sell side. The clean ARMED archetype."""
    rows: list[BrokerNet] = []
    for d in days:
        rows += [
            brow("DX", Side.BUY, each, d, symbol=symbol, investor=InvestorType.FOREIGN, avg_price=105),
            brow("KI", Side.BUY, each, d, symbol=symbol),
            brow("YP", Side.SELL, each * 0.9, d, symbol=symbol),
            brow("PD", Side.SELL, each * 0.9, d, symbol=symbol),
        ]
    return rows


def concentrated_buyer_rows(
    symbol: str,
    days: list[Date],
    *,
    buyer: str = "DX",
    buy_value: float = 8e9,
) -> list[BrokerNet]:
    """Two persistent smart-money accumulators + a small third — high top-2 share but
    NO single-broker monopoly (top-1 = 50% < 60%). Retail sits on the sell side."""
    rows: list[BrokerNet] = []
    for d in days:
        rows += [
            brow(buyer, Side.BUY, buy_value, d, symbol=symbol, investor=InvestorType.FOREIGN, avg_price=105),
            brow("KI", Side.BUY, buy_value * 0.8, d, symbol=symbol),
            brow("CC", Side.BUY, buy_value * 0.2, d, symbol=symbol),
            brow("YP", Side.SELL, buy_value * 0.9, d, symbol=symbol),
            brow("PD", Side.SELL, buy_value * 0.9, d, symbol=symbol),
        ]
    return rows
