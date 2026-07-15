"""Pattern feature vocabulary (slice 21, PATTERN-CATALOG-SPEC §4).

Patterns compose ONLY these named features, computed by this one shared module so every
pattern and every re-estimation uses identical math (one math, everywhere). Each feature
reuses the existing broker_flow / foreign_flow / phase / sms-divergence / distribution
internals rather than re-deriving them — the catalog measures the *system's own* current
beliefs, not a parallel definition.

All reads are look-ahead-safe: `compute_features` reads the store at a historical
`decision_ts` (the store enforces `as_of < decision_ts`), so a feature value at a flag
date consumes no future datum. Adding a feature = adding a pure function + tests here;
patterns never inline ad-hoc math.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as Date
from datetime import datetime

from currentflow import config
from currentflow.dal.models import RowStatus, Side
from currentflow.paper.fill import tier_for_adv
from currentflow.signals import broker_flow, distribution, foreign_flow, phase, sms


def _window_net_by_broker(snap: broker_flow.BrokerFlowSnapshot, dates: list[Date]) -> dict[str, float]:
    """Cumulative net value per broker over `dates` (window-level, not last-day-only)."""
    agg: dict[str, float] = {}
    for d in dates:
        for code, net in snap.daily_nets.get(d, {}).items():
            agg[code] = agg.get(code, 0.0) + net
    return agg


def dominant_broker_flip(
    snap: broker_flow.BrokerFlowSnapshot, *, min_days: int = config.VETO_FLIP_MIN_DAYS
) -> bool:
    """The §5 veto's claim as a measurable predicate: the window's top net buyer is net
    -selling on every one of the last `min_days` days (the accumulator turned seller).
    Identical logic to `veto._distribution`'s flip leg — reused, not re-derived."""
    buyers = snap.top_buyers
    if not buyers:
        return False
    dominant = buyers[0].broker_code
    recent = sorted(snap.daily_nets)[-min_days:]
    return len(recent) >= min_days and all(
        snap.daily_nets[d].get(dominant, 0.0) < 0 for d in recent
    )


@dataclass(frozen=True, slots=True)
class FeatureSet:
    """The named §4 features at one (symbol, decision_ts). Fields are None when a feature
    is not computable from the stored data (missing ≠ zero)."""

    symbol: str
    decision_ts: datetime
    day: Date
    window_days: int
    n_bars: int
    top1_buy_share: float | None
    top3_buy_share: float | None
    buy_hhi: float | None
    cum_net_broker_pct_float: float | None
    nbsa_zscore: float | None
    nbsa_streak: int
    nbsa_buy_streak: int
    price_range_pct: float | None
    price_return: float | None
    value_rising: bool
    flow_price_divergence: float | None
    dominant_broker_flip: bool
    stealth_divergence: bool
    phase_label: str
    tradeable: bool
    liquidity_tier: str
    free_float_pct: float | None


def _price_range_pct(bars) -> float | None:
    closes = [b.close for b in bars if b.close is not None]
    if len(closes) < 2:
        return None
    lo = min(closes)
    return (max(closes) - lo) / lo if lo else None


def _price_return(bars) -> float | None:
    closes = [b.close for b in bars if b.close is not None]
    if len(closes) < 2 or not closes[0]:
        return None
    return closes[-1] / closes[0] - 1.0


def _value_rising(bars) -> bool:
    vals = [b.value for b in bars if b.value is not None]
    if len(vals) < 4:
        return False
    half = len(vals) // 2
    first, second = vals[:half], vals[half:]
    return sum(second) / len(second) > sum(first) / len(first)


def _cum_net_pct_float(dominant_net: float | None, scr0) -> float | None:
    if dominant_net is None or scr0 is None or not scr0.free_float or not scr0.market_cap:
        return None
    ff = scr0.free_float
    frac = ff / 100.0 if ff > 1 else ff  # accept pct or fraction
    float_mcap = scr0.market_cap * frac
    return dominant_net / float_mcap if float_mcap else None


def compute_features(
    store, symbol: str, decision_ts: datetime, *, window: int = config.SMS_DIVERGENCE_WINDOW_DAYS
) -> FeatureSet | None:
    """Read once at `decision_ts` and compute the §4 feature vocabulary. Returns None when
    the name has no visible bars (no data → no features, never fabricated zeros)."""
    bars = store.read_daily_bars(symbol, decision_ts)
    if not bars:
        return None
    traded = [b for b in bars if b.status is RowStatus.TRADED]
    win = traded[-window:]
    day = bars[-1].date

    bsnap = broker_flow.analyze(store, symbol, decision_ts)
    fsnap = foreign_flow.analyze(store, symbol, decision_ts)
    pcls = phase.analyze(store, symbol, decision_ts)
    decay = distribution.build_decay(
        symbol, bars=bars, broker=bsnap, phase_cls=pcls, decision_ts=decision_ts
    )
    scr0 = store.read_scr0_latest(symbol, decision_ts)

    win_dates = [b.date for b in win]
    agg = _window_net_by_broker(bsnap, win_dates)
    top1 = broker_flow.top_n_share(agg, 1)
    top3 = broker_flow.top_n_share(agg, 3)
    hhi = broker_flow.herfindahl(agg)
    dominant = bsnap.top_buyers[0].broker_code if bsnap.top_buyers else None
    cum_pct_float = _cum_net_pct_float(agg.get(dominant) if dominant else None, scr0)

    stealth = any(
        f.kind is distribution.DecayKind.BEARISH_DIVERGENCE for f in decay.flags
    )

    return FeatureSet(
        symbol=symbol,
        decision_ts=decision_ts,
        day=day,
        window_days=window,
        n_bars=len(traded),
        top1_buy_share=top1,
        top3_buy_share=top3,
        buy_hhi=hhi,
        cum_net_broker_pct_float=cum_pct_float,
        nbsa_zscore=fsnap.zscore_20d,
        nbsa_streak=fsnap.persistence_days if fsnap.persistence_side is Side.BUY else 0,
        nbsa_buy_streak=fsnap.persistence_days if fsnap.persistence_side is Side.BUY else 0,
        price_range_pct=_price_range_pct(win),
        price_return=_price_return(win),
        value_rising=_value_rising(win),
        flow_price_divergence=sms._divergence(bars).subscore,
        dominant_broker_flip=dominant_broker_flip(bsnap),
        stealth_divergence=stealth,
        phase_label=pcls.phase.value,
        tradeable=pcls.tradeable,
        liquidity_tier=tier_for_adv(scr0.adv20 if scr0 else None).value,
        free_float_pct=scr0.free_float if scr0 else None,
    )
