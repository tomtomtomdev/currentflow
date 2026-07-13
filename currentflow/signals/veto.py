"""Veto filters (spec §5) — hard reject regardless of SMS.

The v1.1 trap taxonomy: single-bandar monopoly, distribution-dressed-as-accumulation,
markup-on-thin-volume, wash/churn, broker rotation — plus the noise/context filters
(retail-FOMO, event-driven, phase mismatch). Any hit kills the candidate: it never
reaches `ARMED` no matter how high the (internal) score.

RULE B: a veto is a categorical reason, not a number. Each carries the observation
that tripped it. `missing ≠ zero`: absent data cannot fire a veto (we never invent a
reason from a gap), but it is logged where relevant.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from currentflow import config
from currentflow.dal.models import DailyBar, RowStatus
from currentflow.signals.broker_flow import BrokerDNA, BrokerFlowSnapshot, top_n_share
from currentflow.signals.phase import TRADEABLE_PHASES, PhaseClassification


class VetoReason(str, Enum):
    SINGLE_BANDAR_MONOPOLY = "SINGLE_BANDAR_MONOPOLY"       # one broker > 60% net-buy
    DISTRIBUTION_DRESSED = "DISTRIBUTION_DRESSED"           # UTAD / up-bars closing weak / buyer flip
    MARKUP_ON_THIN_VOLUME = "MARKUP_ON_THIN_VOLUME"        # price spike, no volume behind it
    WASH_CHURN = "WASH_CHURN"                               # same broker high buy AND high sell
    BROKER_ROTATION = "BROKER_ROTATION"                    # baton passed between broker codes
    RETAIL_FOMO = "RETAIL_FOMO"                             # retail buy ratio > 60%
    EVENT_DRIVEN = "EVENT_DRIVEN"                           # material news — flow is reacting
    PHASE_MISMATCH = "PHASE_MISMATCH"                       # not Phase C/D (restated from [3])


@dataclass(frozen=True, slots=True)
class Veto:
    reason: VetoReason
    detail: str


@dataclass(frozen=True, slots=True)
class VetoResult:
    symbol: str
    decision_ts: datetime
    vetoes: tuple[Veto, ...] = field(default_factory=tuple)

    @property
    def rejected(self) -> bool:
        return bool(self.vetoes)

    @property
    def reasons(self) -> frozenset[VetoReason]:
        return frozenset(v.reason for v in self.vetoes)


def _complete(bars: list[DailyBar]) -> list[DailyBar]:
    return [
        b for b in sorted(bars, key=lambda b: b.date)
        if b.status is RowStatus.TRADED and b.close is not None and b.volume is not None
    ]


def _monopoly(broker: BrokerFlowSnapshot) -> Veto | None:
    latest = broker.daily_nets.get(broker.end, {})
    share = top_n_share(latest, 1)
    if share is not None and share > config.VETO_MONOPOLY_SHARE:
        return Veto(VetoReason.SINGLE_BANDAR_MONOPOLY,
                    f"top broker holds {share:.0%} of net buying (> {config.VETO_MONOPOLY_SHARE:.0%})")
    return None


def _distribution(broker: BrokerFlowSnapshot, phase_cls: PhaseClassification) -> Veto | None:
    if phase_cls.phase.value == "DISTRIBUTION":
        return Veto(VetoReason.DISTRIBUTION_DRESSED,
                    f"phase classifier flags distribution ({phase_cls.reason})")
    # dominant window buyer flipping to net sell — must be *sustained*, not a one-day
    # blip. A single red day is noise (profit-taking, rebalancing); only a run of
    # consecutive net-sell days across the latest window is real distribution.
    buyers = broker.top_buyers
    if buyers:
        dominant = buyers[0].broker_code
        recent = sorted(broker.daily_nets)[-config.VETO_FLIP_MIN_DAYS:]
        if len(recent) >= config.VETO_FLIP_MIN_DAYS and all(
            broker.daily_nets[d].get(dominant, 0.0) < 0 for d in recent
        ):
            span = f"{recent[0]}→{recent[-1]}"
            return Veto(VetoReason.DISTRIBUTION_DRESSED,
                        f"dominant accumulator {dominant} net-selling {len(recent)} days running ({span})")
    return None


def _markup_thin(bars: list[DailyBar]) -> Veto | None:
    usable = _complete(bars)
    if len(usable) < 2:
        return None
    prior = [b.volume for b in usable[-config.ADV_WINDOW_DAYS - 1:-1]]
    avg = sum(prior) / len(prior) if prior else 0
    last, prev = usable[-1], usable[-2]
    if avg <= 0 or not prev.close:
        return None
    # Markup = an UP spike (pump). A downward shakeout (a spring) is a Phase C event,
    # not a pump — so only positive moves qualify here.
    change = (last.close - prev.close) / prev.close
    rvol = last.volume / avg
    if change >= config.VETO_MARKUP_PRICE_PCT and rvol <= config.VETO_MARKUP_THIN_RVOL:
        return Veto(VetoReason.MARKUP_ON_THIN_VOLUME,
                    f"+{change:.1%} price spike on {rvol:.1f}× volume (no demand behind it)")
    return None


def _wash_churn(broker: BrokerFlowSnapshot) -> Veto | None:
    gross_total = sum(b.buy_value + b.sell_value for b in broker.brokers)
    if gross_total <= 0:
        return None
    for b in broker.brokers:
        hi, lo = max(b.buy_value, b.sell_value), min(b.buy_value, b.sell_value)
        material = (b.buy_value + b.sell_value) >= 0.10 * gross_total
        if hi > 0 and material and lo / hi >= config.VETO_WASH_RATIO:
            return Veto(VetoReason.WASH_CHURN,
                        f"{b.broker_code} bought and sold near-equally ({lo / hi:.0%}) — manufactured volume")
    return None


def _broker_rotation(broker: BrokerFlowSnapshot) -> Veto | None:
    """Concentration held every day but by a *different* top buyer each day — one
    player disguised as many (heuristic; correlated-broker registry refines it)."""
    days = sorted(broker.daily_nets)[-config.VETO_ROTATION_MIN_DAYS:]
    if len(days) < config.VETO_ROTATION_MIN_DAYS:
        return None
    top_each_day = []
    for d in days:
        nets = broker.daily_nets[d]
        buyers = {c: v for c, v in nets.items() if v > 0}
        if not buyers or (top_n_share(nets, 1) or 0) <= 0.4:
            return None  # not concentrated every day → not the rotation pattern
        top_each_day.append(max(buyers, key=buyers.get))
    if len(set(top_each_day)) == len(top_each_day):
        return Veto(VetoReason.BROKER_ROTATION,
                    f"top buyer rotated daily ({' → '.join(top_each_day)}) while staying concentrated")
    return None


def _retail_fomo(broker: BrokerFlowSnapshot) -> Veto | None:
    total_buy = sum(b.buy_value for b in broker.brokers)
    if total_buy <= 0:
        return None
    retail_buy = sum(b.buy_value for b in broker.brokers if b.dna is BrokerDNA.RETAIL)
    ratio = retail_buy / total_buy
    if ratio > config.VETO_RETAIL_FOMO_SHARE:
        return Veto(VetoReason.RETAIL_FOMO,
                    f"retail brokers are {ratio:.0%} of buying (> {config.VETO_RETAIL_FOMO_SHARE:.0%})")
    return None


def evaluate_vetoes(
    symbol: str,
    *,
    broker: BrokerFlowSnapshot,
    bars: list[DailyBar],
    phase_cls: PhaseClassification,
    decision_ts: datetime,
    has_material_news: bool = False,
) -> VetoResult:
    """Run every §5 filter. Returns all reasons that fired (no silent short-circuit —
    the operator sees the full picture of why a name was rejected)."""
    candidates = [
        _monopoly(broker),
        _distribution(broker, phase_cls),
        _markup_thin(bars),
        _wash_churn(broker),
        _broker_rotation(broker),
        _retail_fomo(broker),
    ]
    if has_material_news:
        candidates.append(Veto(VetoReason.EVENT_DRIVEN, "material news in window — flow is reacting, not leading"))
    if phase_cls.phase not in TRADEABLE_PHASES:
        candidates.append(Veto(VetoReason.PHASE_MISMATCH,
                               f"phase {phase_cls.phase.value} is not tradeable (only C/D)"))
    return VetoResult(symbol=symbol, decision_ts=decision_ts,
                      vetoes=tuple(v for v in candidates if v is not None))
