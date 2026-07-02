"""Stage-2 distribution / trap layer (spec §8 signal-decay) — PURE OBSERVATION.

The credibility layer. Slice 4 built the entry-side veto taxonomy (§5); this slice
adds the exit-side **signal-decay** detectors §8 calls for and that a veto does not
cover — a veto rejects a *candidate*, a decay flag warns on an *open / ARMED* name:

    - PHASE_ROLLOVER      phase rolled to distribution (UTAD / weak up-bars)
    - NO_DEMAND           up bar on shrinking volume, narrow spread (effort, no result)
    - BEARISH_DIVERGENCE  price rising while net flow falls — "the single best exit
                          signal" (§8: price up while CMF / foreign-flow / A-D fall)
    - FOREIGN_OUTFLOW     foreign net selling for a run of days (NBSA flipped negative)

`TrapMonitor` unifies the slice-4 **veto** flags with these **decay** flags so the
whole trap/decay picture is wired into every view from one place.

RULE B: a flag is a categorical *severity* + a reason + the observation that tripped
it — never a score, probability, or buy/sell verb. `missing ≠ zero`: a detector that
needs foreign flow stays silent when `net_foreign` is absent (it never invents an
outflow from a gap); only complete TRADED bars are read.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date as Date
from datetime import datetime
from enum import Enum

from currentflow import config
from currentflow.dal.models import DailyBar, RowStatus
from currentflow.signals import broker_flow, phase as phase_mod
from currentflow.signals.broker_flow import BrokerDNA, BrokerFlowSnapshot
from currentflow.signals.phase import PhaseClassification, WyckoffPhase
from currentflow.signals.veto import VetoResult, evaluate_vetoes
from currentflow.store.db import Store


class DecaySeverity(str, Enum):
    """Ordinal severity — a word, never a number (RULE B)."""

    INFO = "INFO"     # context only
    WATCH = "WATCH"   # early decay — keep an eye on it
    WARN = "WARN"     # strong decay — a primary exit signal


_SEVERITY_RANK = {DecaySeverity.INFO: 0, DecaySeverity.WATCH: 1, DecaySeverity.WARN: 2}


class DecayKind(str, Enum):
    PHASE_ROLLOVER = "PHASE_ROLLOVER"            # phase classified DISTRIBUTION (UTAD / supply)
    NO_DEMAND = "NO_DEMAND"                      # up bar, narrow spread, shrinking volume
    BEARISH_DIVERGENCE = "BEARISH_DIVERGENCE"    # price up while net flow falls (best exit)
    FOREIGN_OUTFLOW = "FOREIGN_OUTFLOW"          # foreign net selling for a run of days


@dataclass(frozen=True, slots=True)
class DecayFlag:
    kind: DecayKind
    severity: DecaySeverity
    detail: str


@dataclass(frozen=True, slots=True)
class DecayReport:
    """The §8 signal-decay observation for one symbol at one decision moment."""

    symbol: str
    decision_ts: datetime
    flags: tuple[DecayFlag, ...] = field(default_factory=tuple)

    @property
    def active(self) -> bool:
        return bool(self.flags)

    @property
    def kinds(self) -> frozenset[DecayKind]:
        return frozenset(f.kind for f in self.flags)

    @property
    def max_severity(self) -> DecaySeverity | None:
        if not self.flags:
            return None
        return max((f.severity for f in self.flags), key=lambda s: _SEVERITY_RANK[s])


@dataclass(frozen=True, slots=True)
class TrapMonitor:
    """Unified trap (§5 veto) + decay (§8) picture for a name — the credibility layer
    surfaced across every view."""

    symbol: str
    decision_ts: datetime
    veto: VetoResult
    decay: DecayReport

    @property
    def active(self) -> bool:
        return self.veto.rejected or self.decay.active


# --- bar hygiene --------------------------------------------------------------------


def _complete(bars: list[DailyBar]) -> list[DailyBar]:
    return [
        b for b in sorted(bars, key=lambda b: b.date)
        if b.status is RowStatus.TRADED
        and None not in (b.open, b.high, b.low, b.close, b.volume)
    ]


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


# --- decay detectors ----------------------------------------------------------------


def _phase_rollover(phase_cls: PhaseClassification) -> DecayFlag | None:
    if phase_cls.phase is WyckoffPhase.DISTRIBUTION:
        return DecayFlag(
            DecayKind.PHASE_ROLLOVER, DecaySeverity.WARN,
            f"phase rolled to distribution ({phase_cls.reason})",
        )
    return None


def _no_demand(usable: list[DailyBar]) -> DecayFlag | None:
    """Classic VSA no-demand: an up bar with a narrow spread on volume below both of
    the two prior bars — an up move the market shows no appetite to support."""
    if len(usable) < 3:
        return None
    last, p1, p2 = usable[-1], usable[-2], usable[-3]
    up = last.close > last.open
    spread = last.high - last.low
    recent = usable[-config.DECAY_WINDOW_DAYS - 1:-1] or usable[:-1]
    avg_spread = _mean([b.high - b.low for b in recent])
    narrow = avg_spread <= 0 or spread <= config.DECAY_NO_DEMAND_SPREAD_MULT * avg_spread
    low_vol = last.volume < p1.volume and last.volume < p2.volume
    if up and narrow and low_vol:
        return DecayFlag(
            DecayKind.NO_DEMAND, DecaySeverity.WATCH,
            "up bar on shrinking volume with a narrow spread (no demand behind the move)",
        )
    return None


def _daily_flow(usable: list[DailyBar], broker: BrokerFlowSnapshot) -> dict[Date, float]:
    """Per-day net flow: foreign net when the bar carries it (§8's foreign-flow lens),
    else the day's total broker net. Days with neither are simply absent (never 0)."""
    flow: dict[Date, float] = {}
    for b in usable:
        if b.net_foreign is not None:
            flow[b.date] = b.net_foreign
        elif b.date in broker.daily_nets:
            flow[b.date] = sum(broker.daily_nets[b.date].values())
    return flow


def _bearish_divergence(
    usable: list[DailyBar], broker: BrokerFlowSnapshot
) -> DecayFlag | None:
    """§8's single best exit signal: price rising over the window while net flow falls.
    First-half vs second-half comparison keeps it robust to a single noisy day."""
    win = usable[-config.DECAY_WINDOW_DAYS:]
    if len(win) < 4 or not win[0].close:
        return None
    mid = len(win) // 2
    price_rise = win[-1].close / win[0].close - 1
    rising = (
        price_rise >= config.DECAY_DIVERGENCE_MIN_PRICE_RISE
        and _mean([b.close for b in win[mid:]]) > _mean([b.close for b in win[:mid]])
    )
    if not rising:
        return None

    flow = _daily_flow(win, broker)
    series = [flow[b.date] for b in win if b.date in flow]
    if len(series) < 4:
        return None
    fmid = len(series) // 2
    first, second = sum(series[:fmid]), sum(series[fmid:])
    if second < first and second <= 0:
        return DecayFlag(
            DecayKind.BEARISH_DIVERGENCE, DecaySeverity.WARN,
            f"price +{price_rise:.1%} over the window while net flow falls "
            "(divergence — the primary exit signal)",
        )
    return None


def _foreign_outflow(usable: list[DailyBar]) -> DecayFlag | None:
    """Foreign net selling for a run of trailing days (NBSA flipped negative, §8).
    Only bars that actually carry `net_foreign` count — a gap is not an outflow."""
    nf = [b.net_foreign for b in usable if b.net_foreign is not None]
    streak = config.DECAY_FOREIGN_SELL_STREAK_DAYS
    if len(nf) < streak:
        return None
    if all(v < 0 for v in nf[-streak:]):
        return DecayFlag(
            DecayKind.FOREIGN_OUTFLOW, DecaySeverity.WATCH,
            f"foreign net selling {streak} days running (NBSA flipped negative)",
        )
    return None


# --- report + monitor ---------------------------------------------------------------


def build_decay(
    symbol: str,
    *,
    bars: list[DailyBar],
    broker: BrokerFlowSnapshot,
    phase_cls: PhaseClassification,
    decision_ts: datetime,
) -> DecayReport:
    """Run every §8 signal-decay detector (no short-circuit — the operator sees the
    full picture). Order is severity-first for display."""
    usable = _complete(bars)
    candidates = [
        _phase_rollover(phase_cls),
        _bearish_divergence(usable, broker),
        _no_demand(usable),
        _foreign_outflow(usable),
    ]
    flags = tuple(f for f in candidates if f is not None)
    return DecayReport(symbol=symbol, decision_ts=decision_ts, flags=flags)


def monitor(
    store: Store,
    symbol: str,
    decision_ts: datetime,
    *,
    start: Date | None = None,
    end: Date | None = None,
    registry: dict[str, BrokerDNA] | None = None,
    has_material_news: bool = False,
) -> TrapMonitor:
    """Read look-ahead-safe inputs once and produce the unified trap (§5) + decay (§8)
    picture for `symbol` — the credibility layer for every view."""
    bars = store.read_daily_bars(symbol, decision_ts, start=start, end=end)
    broker = broker_flow.analyze(store, symbol, decision_ts, start=start, end=end, registry=registry)
    phase_cls = phase_mod.classify(symbol, bars, decision_ts)
    veto = evaluate_vetoes(
        symbol, broker=broker, bars=bars, phase_cls=phase_cls,
        decision_ts=decision_ts, has_material_news=has_material_news,
    )
    decay = build_decay(symbol, bars=bars, broker=broker, phase_cls=phase_cls, decision_ts=decision_ts)
    return TrapMonitor(symbol=symbol, decision_ts=decision_ts, veto=veto, decay=decay)
