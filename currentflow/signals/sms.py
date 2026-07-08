"""Smart Money Score (spec §4, LD-1) — track-specific weights → 0–100.

**INTERNAL until validated (RULE B / LD-9).** The score is computed here and drives
the internal `ARMED` state (see `engine.py`), but it is NOT a displayable number. The
view layer renders the score's *components* as raw observation; the composite number
stays hidden until the module clears `PAPER_VALIDATION_MONTHS` of forward paper
(enforced by `validation.state`). This module therefore exposes both the components
(observation) and `internal_score` (gated) — presentation code must consult the
validation gate before ever showing the latter.

Weights are the ONLY tunable surface and live in `config.SMS_WEIGHTS` (tuned solely by
the walk-forward optimizer — never hand-edited live; CLAUDE.md). Each component yields a
sub-score in [0, 1]; SMS = Σ weightᵢ·subscoreᵢ, then × the §3 rebalance multiplier.
"""

from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass
from datetime import datetime

from currentflow import config
from currentflow.dal.models import DailyBar, RowStatus, Side
from currentflow.signals.broker_flow import BrokerFlowSnapshot
from currentflow.signals.foreign_flow import ForeignFlowSnapshot
from currentflow.signals.phase import PhaseClassification

log = logging.getLogger(__name__)

COMPONENT_KEYS = (
    "divergence", "broker_concentration", "foreign_flow", "rvol", "block_trade", "phase_bonus",
)


@dataclass(frozen=True, slots=True)
class SmsComponent:
    key: str
    weight: int
    subscore: float          # 0..1 strength of this component
    observation: dict        # raw measurements — RULE B: these are shown, the score is not
    available: bool          # False = data missing (never scored as 0-strength silently)

    @property
    def contribution(self) -> float:
        return self.weight * self.subscore


@dataclass(frozen=True, slots=True)
class SmsResult:
    """Component observation + the INTERNAL composite. RULE B: `internal_score` is
    never rendered until the module is VALIDATED — presentation must gate on it."""

    symbol: str
    decision_ts: datetime
    track: str                              # "A" | "B"
    components: tuple[SmsComponent, ...]
    rebalance_multiplier: float
    internal_score: float                   # 0..100 — GATED, do not display pre-validation

    @property
    def components_by_key(self) -> dict[str, SmsComponent]:
        return {c.key: c for c in self.components}


def _clamp01(x: float) -> float:
    return 0.0 if x < 0 else 1.0 if x > 1 else x


def _complete(bars: list[DailyBar]) -> list[DailyBar]:
    return [
        b for b in sorted(bars, key=lambda b: b.date)
        if b.status is RowStatus.TRADED and b.close is not None and b.volume is not None
    ]


# --- components --------------------------------------------------------------------


def _corr_factor(corr: float | None) -> float:
    """Graduated generalization of §4's "corr(vol,|Δprice|) < 0.3" divergence gate.

    Divergence = high volume with NO price result → a LOW vol/|move| correlation. Rather
    than a binary ×0.5 cliff at 0.3 (which — computed over a long window — fired on
    essentially every name, since more volume normally accompanies bigger moves), map the
    correlation to a smooth factor in [0.5, 1.0]: full credit as corr→0 (or negative),
    tapering to 0.5 at the threshold and holding 0.5 above it. Reduces to the old rule at
    the reference points (corr=0 → 1.0, corr=0.3 → 0.5). `None` (unmeasurable) → 0.5."""
    if corr is None:
        return 0.5
    if corr <= 0:
        return 1.0
    factor = 1.0 - 0.5 * (corr / config.SMS_DIVERGENCE_CORR_MAX)
    return 0.5 if factor < 0.5 else 1.0 if factor > 1.0 else factor


def _divergence(bars: list[DailyBar]) -> SmsComponent:
    """High volume with a ≤±0.5% price move, low corr(vol, |Δprice|) — effort without
    price result (absorption). LD-1 universal spine.

    Measured over the RECENT `SMS_DIVERGENCE_WINDOW_DAYS` bars: divergence is a current-
    accumulation signal, not a year-long average. Running it over the full passed-in
    history diluted the flat-high-volume ratio toward zero for anything that trended at
    all (the calibration defect that pinned this 30-weight component near 0 on real data)."""
    w = 0  # weight injected later
    usable = _complete(bars)[-config.SMS_DIVERGENCE_WINDOW_DAYS:]
    if len(usable) < 3:
        return SmsComponent("divergence", w, 0.0, {"high_vol_bars": 0}, available=False)

    vols = [b.volume for b in usable]
    avg_vol = sum(vols[:-1]) / max(1, len(vols) - 1)
    rets, hv, flat_hv = [], 0, 0
    for prev, cur in zip(usable, usable[1:]):
        ret = abs((cur.close - prev.close) / prev.close) if prev.close else 0.0
        rets.append((cur.volume, ret))
        if avg_vol > 0 and cur.volume >= config.SMS_DIVERGENCE_HIVOL_MULT * avg_vol:
            hv += 1
            if ret <= config.SMS_DIVERGENCE_FLAT_PCT:
                flat_hv += 1

    corr = None
    xs = [v for v, _ in rets]
    ys = [r for _, r in rets]
    if len(xs) >= 2 and statistics.pstdev(xs) > 0 and statistics.pstdev(ys) > 0:
        mx, my = statistics.mean(xs), statistics.mean(ys)
        cov = sum((x - mx) * (y - my) for x, y in rets) / len(rets)
        corr = cov / (statistics.pstdev(xs) * statistics.pstdev(ys))

    subscore = 0.0 if hv == 0 else (flat_hv / hv) * _corr_factor(corr)
    obs = {"high_vol_bars": hv, "flat_high_vol_bars": flat_hv,
           "vol_price_corr": None if corr is None else round(corr, 3)}
    return SmsComponent("divergence", w, _clamp01(subscore), obs, available=True)


def _recent_return(bars: list[DailyBar], days: int) -> float | None:
    usable = _complete(bars)
    if len(usable) <= days:
        return None
    a, b = usable[-days - 1].close, usable[-1].close
    return (b - a) / a if a else None


def _broker_concentration(broker: BrokerFlowSnapshot, bars: list[DailyBar]) -> SmsComponent:
    """Top-2 net-buy share sustained ≥N consecutive days on flat/down bars — quiet
    accumulation, not a chase. Track B's lead signal (SMS wt 35)."""
    if broker.top2_share is None or not broker.top_buyers:
        return SmsComponent("broker_concentration", 0, 0.0, {"top2_share": None}, available=False)

    persistence = max((b.persistence_days for b in broker.top_buyers[:2]), default=0)
    ret = _recent_return(bars, config.SMS_BROKER_PERSIST_DAYS)
    flat_down = ret is None or ret <= 0.02
    persistent = persistence >= config.SMS_BROKER_PERSIST_DAYS
    subscore = broker.top2_share if (persistent and flat_down) else broker.top2_share * 0.5
    obs = {"top2_share": round(broker.top2_share, 3), "persistence_days": persistence,
           "flat_or_down": flat_down}
    return SmsComponent("broker_concentration", 0, _clamp01(subscore), obs, available=True)


def _foreign_flow(foreign: ForeignFlowSnapshot | None, track: str) -> SmsComponent:
    """NBSA net buy > 2× 20d avg and rising. Track A only (LD-1: foreign flow is
    unreliable on lapis-2 — weight 0 for Track B)."""
    if track != "A":
        return SmsComponent("foreign_flow", 0, 0.0, {"excluded": "Track B — foreign flow excluded (LD-1)"}, available=True)
    if foreign is None or foreign.vs_20d_avg is None or foreign.net_last is None:
        return SmsComponent("foreign_flow", 0, 0.0, {"vs_20d_avg": None}, available=False)

    rising = foreign.persistence_side is Side.BUY and foreign.net_last > 0
    spike = foreign.vs_20d_avg / config.SMS_FOREIGN_SPIKE_MULT   # 1.0 at the 2× threshold
    subscore = _clamp01(spike / 2) if rising else _clamp01(spike / 4)
    obs = {"vs_20d_avg": round(foreign.vs_20d_avg, 2), "rising": rising,
           "zscore_20d": None if foreign.zscore_20d is None else round(foreign.zscore_20d, 2)}
    return SmsComponent("foreign_flow", 0, subscore, obs, available=True)


def _rvol(bars: list[DailyBar]) -> SmsComponent:
    """Relative volume vs 20d average; full credit at ≥3× (§4)."""
    usable = _complete(bars)
    if len(usable) < 2:
        return SmsComponent("rvol", 0, 0.0, {"rvol": None}, available=False)
    prior = [b.volume for b in usable[-config.ADV_WINDOW_DAYS - 1:-1]]
    avg = sum(prior) / len(prior) if prior else 0
    if avg <= 0:
        return SmsComponent("rvol", 0, 0.0, {"rvol": None}, available=False)
    rvol = usable[-1].volume / avg
    subscore = _clamp01((rvol - 1) / (config.SMS_RVOL_MULT - 1))
    return SmsComponent("rvol", 0, subscore, {"rvol": round(rvol, 2)}, available=True)


def _block_trade(broker: BrokerFlowSnapshot, adv20: float | None) -> SmsComponent:
    """Block-trade footprint: a single broker's buy graded by its size relative to ADV
    (§4's "> 1% ADV", full credit at `SMS_BLOCK_ADV_PCT`).

    Scale-relative by construction: the earlier fixed IDR-1B floor (OR'd with the %ADV
    test) saturated to 1.0 on any liquid name — a 1B single-broker buy is noise for a
    large-cap — so the component added a flat, non-discriminating +weight to everyone.
    Grading by %ADV separates the genuinely concentrated day from the diffuse one. The
    absolute IDR floor is kept only as the fallback scale when ADV is unknown (missing ≠
    zero)."""
    if not broker.brokers:
        return SmsComponent("block_trade", 0, 0.0, {"max_broker_buy": None, "pct_of_adv": None}, available=False)
    max_buy = max((b.buy_value for b in broker.brokers), default=0.0)
    if adv20 and adv20 > 0:
        pct = max_buy / adv20
        subscore = _clamp01(pct / config.SMS_BLOCK_ADV_PCT)
        obs = {"max_broker_buy": max_buy, "pct_of_adv": round(pct, 4)}
    else:
        subscore = _clamp01(max_buy / config.SMS_BLOCK_VALUE_IDR)
        obs = {"max_broker_buy": max_buy, "pct_of_adv": None}
    return SmsComponent("block_trade", 0, _clamp01(subscore), obs, available=True)


def _phase_bonus(phase_cls: PhaseClassification) -> SmsComponent:
    """Wyckoff phase-alignment bonus: spring (C) or LPS (D) proximity (§4)."""
    kinds = {e.kind for e in phase_cls.events}
    if kinds & {"SPRING", "LPS"}:
        subscore = 1.0
    elif "SOS" in kinds:
        subscore = 0.5
    else:
        subscore = 0.0
    obs = {"phase": phase_cls.phase.value, "events": sorted(kinds)}
    return SmsComponent("phase_bonus", 0, subscore, obs, available=True)


# --- assembly ----------------------------------------------------------------------


def compute_sms(
    symbol: str,
    *,
    track: str,
    bars: list[DailyBar],
    broker: BrokerFlowSnapshot,
    foreign: ForeignFlowSnapshot | None,
    phase_cls: PhaseClassification,
    decision_ts: datetime,
    adv20: float | None = None,
    rebalance_multiplier: float = 1.0,
    weights: dict[str, int] | None = None,
) -> SmsResult:
    """Assemble the track-weighted SMS. `internal_score` is GATED by RULE B.

    `weights` overrides the locked `config.SMS_WEIGHTS[track]` for THIS call only — the seam
    the slice-9 walk-forward optimizer uses to score candidate weights without ever mutating
    the global surface (weights are never hand-edited live; CLAUDE.md / §4). Defaults to the
    locked track weights."""
    if track not in config.SMS_WEIGHTS:
        raise ValueError(f"unknown track {track!r} — expected 'A' or 'B'")
    weights = config.SMS_WEIGHTS[track] if weights is None else weights

    raw = {
        "divergence": _divergence(bars),
        "broker_concentration": _broker_concentration(broker, bars),
        "foreign_flow": _foreign_flow(foreign, track),
        "rvol": _rvol(bars),
        "block_trade": _block_trade(broker, adv20),
        "phase_bonus": _phase_bonus(phase_cls),
    }
    components = tuple(
        SmsComponent(c.key, weights[c.key], c.subscore, c.observation, c.available)
        for c in (raw[k] for k in COMPONENT_KEYS)
    )
    score = sum(c.contribution for c in components) * rebalance_multiplier
    return SmsResult(
        symbol=symbol, decision_ts=decision_ts, track=track,
        components=components, rebalance_multiplier=rebalance_multiplier,
        internal_score=round(score, 2),
    )
