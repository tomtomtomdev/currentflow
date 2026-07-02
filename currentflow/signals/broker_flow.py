"""Broker Flow Analyzer (spec §9, "the differentiator") — PURE OBSERVATION.

Per-stock broker net buy/sell, broker DNA classification, concentration (top-N share
+ Herfindahl), persistence over a rolling window, custom syndicate grouping, and the
buyer-vs-seller matrix across stocks.

RULE B: this module renders observations only. It computes **no score, no
probability, no ranked buy/sell claim** — the numbers here (net values, shares,
HHI) are raw measurements of the flow, not predictions.

Feed semantics: `marketdetectors` lists the top brokers per side. A broker absent
from a side was not a top participant there — its value on that side is treated as
0 *for netting within the listed set*, which is the feed's resolution, not a claim
that it traded nothing. A listed row with a None value is unknown data and is
dropped loudly (never read as zero).
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import date as Date
from datetime import datetime
from enum import Enum

from currentflow.dal.models import BrokerNet, InvestorType, Side
from currentflow.store.db import Store

log = logging.getLogger(__name__)


class BrokerDNA(str, Enum):
    FOREIGN_INST = "FOREIGN_INST"
    LOCAL_INST = "LOCAL_INST"
    SMART_MONEY = "SMART_MONEY"
    RETAIL = "RETAIL"
    PROP = "PROP"
    UNKNOWN = "UNKNOWN"


# Seed registry from the design handoff — ILLUSTRATIVE; verify against the real IDX
# broker registry and maintain as operator knowledge. Overridable per call.
DEFAULT_DNA_REGISTRY: dict[str, BrokerDNA] = {
    **{c: BrokerDNA.FOREIGN_INST for c in ("KZ", "AK", "RX", "ZP", "YU")},
    **{c: BrokerDNA.LOCAL_INST for c in ("CC", "NI", "OD", "DR")},
    **{c: BrokerDNA.SMART_MONEY for c in ("DX", "AI", "KI")},
    **{c: BrokerDNA.PROP for c in ("BQ",)},
    **{c: BrokerDNA.RETAIL for c in ("YP", "PD", "CP", "GR")},
}


def classify_dna(
    broker_code: str,
    investor_type: InvestorType = InvestorType.UNKNOWN,
    registry: dict[str, BrokerDNA] | None = None,
) -> BrokerDNA:
    """Registry first; else fall back to the feed's foreign/local/government tag."""
    reg = DEFAULT_DNA_REGISTRY if registry is None else registry
    dna = reg.get(broker_code.upper())
    if dna is not None:
        return dna
    if investor_type is InvestorType.FOREIGN:
        return BrokerDNA.FOREIGN_INST
    if investor_type is InvestorType.GOVERNMENT:
        return BrokerDNA.LOCAL_INST
    return BrokerDNA.UNKNOWN


# --- daily netting ------------------------------------------------------------------


def daily_broker_net(rows: list[BrokerNet]) -> dict[Date, dict[str, float]]:
    """{date: {broker_code: net_value}} — buy value minus sell value per broker/day.

    Rows with a None value are unknown data: dropped and logged, never read as zero.
    """
    dropped = 0
    net: dict[Date, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for r in rows:
        if r.value is None:
            dropped += 1
            continue
        signed = r.value if r.side is Side.BUY else -r.value
        net[r.date][r.broker_code] += signed
    if dropped:
        log.warning("broker_flow: dropped %d row(s) with unknown value (missing ≠ zero)", dropped)
    return {d: dict(b) for d, b in net.items()}


# --- concentration -------------------------------------------------------------------


def top_n_share(net_by_broker: dict[str, float], n: int = 2) -> float | None:
    """Top-N brokers' share of total net buying (among net buyers). None if no buyers."""
    buys = sorted((v for v in net_by_broker.values() if v > 0), reverse=True)
    if not buys:
        return None
    return sum(buys[:n]) / sum(buys)


def herfindahl(net_by_broker: dict[str, float]) -> float | None:
    """HHI over net buyers' shares, 0–1. 1.0 = one buyer; ~0 = fully dispersed."""
    buys = [v for v in net_by_broker.values() if v > 0]
    if not buys:
        return None
    total = sum(buys)
    return sum((v / total) ** 2 for v in buys)


# --- persistence ---------------------------------------------------------------------


def persistence(
    daily_nets: dict[Date, dict[str, float]], broker_code: str
) -> int:
    """Consecutive trading days (ending at the most recent day) the broker was a
    net buyer. Days where the broker is absent break the streak (not observed as
    a top participant ≠ still accumulating)."""
    streak = 0
    for day in sorted(daily_nets, reverse=True):
        if daily_nets[day].get(broker_code, 0.0) > 0:
            streak += 1
        else:
            break
    return streak


# --- syndicate grouping ----------------------------------------------------------------


def syndicate_nets(
    net_by_broker: dict[str, float], groups: dict[str, tuple[str, ...]]
) -> dict[str, float]:
    """Aggregate broker nets by custom operator-defined syndicate groups.

    Brokers not covered by any group are returned under their own code, so the
    total is preserved — nothing silently dropped.
    """
    grouped: dict[str, float] = defaultdict(float)
    member_of = {code: name for name, codes in groups.items() for code in codes}
    for code, v in net_by_broker.items():
        grouped[member_of.get(code, code)] += v
    return dict(grouped)


# --- snapshot ------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BrokerStat:
    broker_code: str
    dna: BrokerDNA
    investor_type: InvestorType
    net_value: float            # window aggregate, IDR
    buy_value: float
    sell_value: float
    persistence_days: int       # consecutive net-buy days ending at window end
    avg_price: float | None     # last seen accumulator VWAP (buy side)


@dataclass(frozen=True, slots=True)
class BrokerFlowSnapshot:
    """Window observation of broker flow for one symbol. No score, no claim."""

    symbol: str
    start: Date
    end: Date
    decision_ts: datetime
    brokers: tuple[BrokerStat, ...]        # sorted by net_value desc
    daily_nets: dict[Date, dict[str, float]]
    top2_share: float | None               # of net buying, most recent day
    hhi: float | None                      # Herfindahl, most recent day

    @property
    def top_buyers(self) -> tuple[BrokerStat, ...]:
        return tuple(b for b in self.brokers if b.net_value > 0)

    @property
    def top_sellers(self) -> tuple[BrokerStat, ...]:
        return tuple(sorted(
            (b for b in self.brokers if b.net_value < 0), key=lambda b: b.net_value
        ))


def build_snapshot(
    symbol: str,
    rows: list[BrokerNet],
    *,
    decision_ts: datetime,
    registry: dict[str, BrokerDNA] | None = None,
) -> BrokerFlowSnapshot:
    """Aggregate look-ahead-safe broker rows into the observation snapshot."""
    daily = daily_broker_net(rows)
    days = sorted(daily)
    start, end = (days[0], days[-1]) if days else (Date.min, Date.min)

    buy_val: dict[str, float] = defaultdict(float)
    sell_val: dict[str, float] = defaultdict(float)
    inv_type: dict[str, InvestorType] = {}
    last_avg_price: dict[str, tuple[Date, float]] = {}
    for r in sorted(rows, key=lambda r: r.date):
        if r.value is None:
            continue
        if r.side is Side.BUY:
            buy_val[r.broker_code] += r.value
            if r.avg_price is not None:
                last_avg_price[r.broker_code] = (r.date, r.avg_price)
        else:
            sell_val[r.broker_code] += r.value
        if r.investor_type is not InvestorType.UNKNOWN:
            inv_type[r.broker_code] = r.investor_type

    codes = sorted(set(buy_val) | set(sell_val))
    stats = [
        BrokerStat(
            broker_code=c,
            dna=classify_dna(c, inv_type.get(c, InvestorType.UNKNOWN), registry),
            investor_type=inv_type.get(c, InvestorType.UNKNOWN),
            net_value=buy_val[c] - sell_val[c],
            buy_value=buy_val[c],
            sell_value=sell_val[c],
            persistence_days=persistence(daily, c),
            avg_price=last_avg_price[c][1] if c in last_avg_price else None,
        )
        for c in codes
    ]
    stats.sort(key=lambda s: s.net_value, reverse=True)

    latest = daily[days[-1]] if days else {}
    return BrokerFlowSnapshot(
        symbol=symbol,
        start=start,
        end=end,
        decision_ts=decision_ts,
        brokers=tuple(stats),
        daily_nets=daily,
        top2_share=top_n_share(latest, 2),
        hhi=herfindahl(latest),
    )


def analyze(
    store: Store,
    symbol: str,
    decision_ts: datetime,
    *,
    start: Date | None = None,
    end: Date | None = None,
    registry: dict[str, BrokerDNA] | None = None,
) -> BrokerFlowSnapshot:
    """Read look-ahead-safe broker rows (`as_of < decision_ts` enforced by the
    store) and build the observation snapshot."""
    rows = store.read_broker_net(symbol, decision_ts, start=start, end=end)
    return build_snapshot(symbol, rows, decision_ts=decision_ts, registry=registry)


def buyer_seller_matrix(
    snapshots: dict[str, BrokerFlowSnapshot],
    *,
    n_buyers: int = 3,
    n_sellers: int = 2,
) -> dict[str, dict[str, float]]:
    """{broker_code: {symbol: net_value}} for the union of each snapshot's top-N
    buyers and sellers — the Broker × Stock matrix (design module 1)."""
    brokers: list[str] = []
    for snap in snapshots.values():
        for b in list(snap.top_buyers[:n_buyers]) + list(snap.top_sellers[:n_sellers]):
            if b.broker_code not in brokers:
                brokers.append(b.broker_code)
    matrix: dict[str, dict[str, float]] = {c: {} for c in brokers}
    for sym, snap in snapshots.items():
        nets = {b.broker_code: b.net_value for b in snap.brokers}
        for c in brokers:
            if c in nets:
                matrix[c][sym] = nets[c]
    return matrix
