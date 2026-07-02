"""Institutional Accumulation Detector (spec §9) — PURE OBSERVATION.

Stealth divergence (price flat/down while net accumulation rises), the accumulator's
VWAP estimate, volume dry-up + price-tightness during consolidation. Absorption needs
L2 depth — it degrades gracefully to `None` rather than being faked.

RULE B: every field here is a raw measurement of the flow/price structure — no score,
no probability, no buy/sell verb. `missing ≠ zero`: only complete TRADED bars count.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as Date
from datetime import datetime

from currentflow.dal.models import DailyBar, RowStatus
from currentflow.signals.broker_flow import BrokerDNA, BrokerFlowSnapshot
from currentflow.signals.broker_flow import analyze as broker_analyze
from currentflow.store.db import Store


@dataclass(frozen=True, slots=True)
class AccumulationSnapshot:
    """Window observation of stealth institutional accumulation. No score, no claim."""

    symbol: str
    start: Date
    end: Date
    decision_ts: datetime
    price_change_pct: float | None       # close-to-close over the window
    accumulator: str | None              # dominant net-buying broker code
    net_accumulation: float | None       # that broker's window net (IDR)
    accumulation_rising: bool            # its daily net rising (2nd half vs 1st)
    stealth_divergence: bool             # price flat/down WHILE accumulation rises
    accumulator_vwap: float | None       # its estimated buy VWAP
    price_vs_vwap_pct: float | None      # last close vs that VWAP
    volume_dryup_ratio: float | None     # recent avg vol / earlier avg vol (<1 = drying up)
    price_tightness: float | None        # recent mean (high-low)/close (smaller = tighter)
    absorption: None = None              # requires L2 depth — unavailable, never faked


def _complete(bars: list[DailyBar]) -> list[DailyBar]:
    return [
        b for b in sorted(bars, key=lambda b: b.date)
        if b.status is RowStatus.TRADED and None not in (b.high, b.low, b.close, b.volume)
    ]


def _rising(daily: dict[Date, float], code: str) -> bool:
    days = sorted(daily)
    if len(days) < 2:
        return False
    mid = len(days) // 2
    first = sum(daily[d].get(code, 0.0) for d in days[:mid])
    second = sum(daily[d].get(code, 0.0) for d in days[mid:])
    return second > first


def build_snapshot(
    symbol: str,
    bars: list[DailyBar],
    broker: BrokerFlowSnapshot,
    *,
    decision_ts: datetime,
) -> AccumulationSnapshot:
    usable = _complete(bars)
    start, end = (usable[0].date, usable[-1].date) if usable else (Date.min, Date.min)

    price_change = None
    if len(usable) >= 2 and usable[0].close:
        price_change = (usable[-1].close - usable[0].close) / usable[0].close

    buyers = broker.top_buyers
    accumulator = buyers[0].broker_code if buyers else None
    net_accum = buyers[0].net_value if buyers else None
    vwap = buyers[0].avg_price if buyers else None
    rising = _rising(broker.daily_nets, accumulator) if accumulator else False

    price_vs_vwap = None
    if vwap and usable:
        price_vs_vwap = (usable[-1].close - vwap) / vwap

    stealth = bool(
        price_change is not None and price_change <= 0.02
        and net_accum is not None and net_accum > 0 and rising
    )

    dryup = tightness = None
    if len(usable) >= 4:
        mid = len(usable) // 2
        early = [b.volume for b in usable[:mid]]
        recent = [b.volume for b in usable[mid:]]
        early_avg = sum(early) / len(early) if early else 0
        recent_avg = sum(recent) / len(recent) if recent else 0
        dryup = recent_avg / early_avg if early_avg > 0 else None
        spans = [(b.high - b.low) / b.close for b in usable[mid:] if b.close]
        tightness = sum(spans) / len(spans) if spans else None

    return AccumulationSnapshot(
        symbol=symbol, start=start, end=end, decision_ts=decision_ts,
        price_change_pct=price_change, accumulator=accumulator, net_accumulation=net_accum,
        accumulation_rising=rising, stealth_divergence=stealth, accumulator_vwap=vwap,
        price_vs_vwap_pct=price_vs_vwap, volume_dryup_ratio=dryup, price_tightness=tightness,
    )


def analyze(
    store: Store,
    symbol: str,
    decision_ts: datetime,
    *,
    start: Date | None = None,
    end: Date | None = None,
    registry: dict[str, BrokerDNA] | None = None,
) -> AccumulationSnapshot:
    """Read look-ahead-safe bars + broker flow and build the observation snapshot."""
    bars = store.read_daily_bars(symbol, decision_ts, start=start, end=end)
    broker = broker_analyze(store, symbol, decision_ts, start=start, end=end, registry=registry)
    return build_snapshot(symbol, bars, broker, decision_ts=decision_ts)
