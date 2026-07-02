"""Wyckoff phase classifier (spec §2 step [3], LD-2) — RULE A HARD GATE.

The #1 edge-vs-artifact decision: a volume/flow threshold with no phase context
buys distribution tops. So the classifier runs **before** scoring; only Wyckoff
**Accumulation Phase C (spring/test) or D (SOS + LPS)** is tradeable. Threshold
detectors (selling climax, automatic rally, spring, sign-of-strength, last point of
support, upthrust) *feed* the classifier — they never bypass it.

Method: locate the trading range (anchored on the selling climax / automatic rally
when present, else the recent consolidation base), then read the current structure
against that range's support/resistance.

    DOWNTREND → A (stopping action) → B (cause) → C (test/spring) → D (SOS+LPS) → E (markup)
                                                   └──── TRADEABLE ────┘

RULE B: this is a *gate*, not a score. It emits a phase label and the events behind
it — no probability, no number. `missing ≠ zero`: only complete TRADED bars are used;
incomplete/absent bars are dropped loudly, never read as a flat 0-volume bar.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date as Date
from datetime import datetime
from enum import Enum

from currentflow import config
from currentflow.dal.models import DailyBar, RowStatus
from currentflow.store.db import Store

log = logging.getLogger(__name__)

# Structural (non-tunable) spans — how far back events are read, not signal thresholds.
EVENT_WINDOW = 8     # recent bars examined for spring / SOS / LPS / upthrust
AR_SPAN = 15         # bars after the selling climax that define the automatic-rally high


class WyckoffPhase(str, Enum):
    UNKNOWN = "UNKNOWN"            # insufficient / ambiguous history
    DOWNTREND = "DOWNTREND"       # no stopping action yet
    A = "A"                        # stopping action: SC / AR / ST forming the range
    B = "B"                        # cause-building oscillation inside the range
    C = "C"                        # test: spring / shakeout — TRADEABLE
    D = "D"                        # markup within range: SOS + LPS — TRADEABLE
    E = "E"                        # markup out of the range — too late to arm
    DISTRIBUTION = "DISTRIBUTION"  # up-structure rolling over / UTAD


TRADEABLE_PHASES = frozenset({WyckoffPhase.C, WyckoffPhase.D})


@dataclass(frozen=True, slots=True)
class TradingRange:
    support: float
    resistance: float
    start: Date
    end: Date
    avg_volume: float

    @property
    def width(self) -> float:
        return (self.resistance - self.support) / self.support if self.support else float("inf")

    @property
    def span(self) -> float:
        return self.resistance - self.support


@dataclass(frozen=True, slots=True)
class PhaseEvent:
    kind: str        # SELLING_CLIMAX | SPRING | SOS | LPS | UTAD
    date: Date
    detail: str


@dataclass(frozen=True, slots=True)
class PhaseClassification:
    """The gate's verdict for one symbol at one decision moment. No score."""

    symbol: str
    decision_ts: datetime
    phase: WyckoffPhase
    tradeable: bool                       # RULE A: phase ∈ {C, D}
    trading_range: TradingRange | None
    events: tuple[PhaseEvent, ...]
    reason: str
    bars_used: int


# --- bar hygiene -------------------------------------------------------------------


def _complete(bars: list[DailyBar]) -> list[DailyBar]:
    """Only TRADED bars with full OHLC+volume. Incomplete/absent dropped loudly."""
    out, dropped = [], 0
    for b in sorted(bars, key=lambda b: b.date):
        if b.status is RowStatus.TRADED and None not in (b.open, b.high, b.low, b.close, b.volume):
            out.append(b)
        else:
            dropped += 1
    if dropped:
        log.info("phase: dropped %d incomplete/non-TRADED bar(s) (missing ≠ zero)", dropped)
    return out


def _avg_volume(bars: list[DailyBar]) -> float:
    vols = [b.volume for b in bars if b.volume is not None]
    return sum(vols) / len(vols) if vols else 0.0


def _close_position(b: DailyBar) -> float:
    """Where the close sits in the bar's range: 0 = at the low, 1 = at the high."""
    span = (b.high or 0) - (b.low or 0)
    return 0.5 if span <= 0 else ((b.close or 0) - (b.low or 0)) / span


# --- detectors (feed the classifier) -----------------------------------------------


def _detect_selling_climax(window: list[DailyBar]) -> int | None:
    """Index of the selling-climax bar: a high-volume, wide-spread down bar making a
    new local low in the earlier part of the window, with a rally after it (AR)."""
    if len(window) < config.PHASE_RANGE_MIN_BARS:
        return None
    search_end = int(len(window) * 0.7)
    best: tuple[float, int] | None = None
    for i in range(2, search_end):
        b = window[i]
        prior = window[max(0, i - config.ADV_WINDOW_DAYS):i]
        prior_avg = _avg_volume(prior)
        if prior_avg <= 0 or b.volume is None:
            continue
        is_climactic = b.volume >= config.PHASE_SC_VOLUME_MULT * prior_avg
        is_down = (b.close or 0) < (b.open or 0)
        makes_low = b.low is not None and b.low <= min(p.low for p in prior)
        if is_climactic and is_down and makes_low:
            # require an automatic rally after it (a later bar rallying off the low)
            after = window[i + 1:i + 1 + AR_SPAN]
            if after and max(a.high for a in after) > (b.low or 0) * 1.02:
                if best is None or b.volume > best[0]:
                    best = (b.volume, i)
    return best[1] if best else None


def _establish_range(window: list[DailyBar]) -> tuple[TradingRange | None, int | None]:
    """Anchor the range on the selling climax / automatic rally when present, else on
    the recent consolidation base. Returns (range, selling_climax_index)."""
    sc_idx = _detect_selling_climax(window)
    if sc_idx is not None:
        span = window[sc_idx:sc_idx + AR_SPAN]
        support = min(b.low for b in span)          # SC / ST low
        resistance = max(b.high for b in span)      # automatic-rally high
        rng = TradingRange(
            support=support, resistance=resistance,
            start=window[sc_idx].date, end=window[-1].date,
            avg_volume=_avg_volume(window[sc_idx:]),
        )
        return rng, sc_idx

    base = window[:-EVENT_WINDOW]
    if len(base) < config.PHASE_RANGE_MIN_BARS:
        return None, None
    rng = TradingRange(
        support=min(b.low for b in base), resistance=max(b.high for b in base),
        start=base[0].date, end=base[-1].date, avg_volume=_avg_volume(base),
    )
    return rng, None


def _is_downtrend(bars: list[DailyBar], rng: TradingRange) -> bool:
    """A base that is still making lower lows (strong negative drift, close near the
    bottom of its own range) is not an accumulation range."""
    closes = [b.close for b in bars if b.close is not None]
    if len(closes) < 3:
        return False
    n = len(closes)
    mean_x = (n - 1) / 2
    mean_y = sum(closes) / n
    denom = sum((i - mean_x) ** 2 for i in range(n))
    slope = sum((i - mean_x) * (c - mean_y) for i, c in enumerate(closes)) / denom if denom else 0.0
    norm_slope = slope / mean_y if mean_y else 0.0
    near_bottom = closes[-1] <= rng.support + 0.15 * rng.span
    return norm_slope <= -0.003 and near_bottom


def _detect_spring(window: list[DailyBar], rng: TradingRange) -> PhaseEvent | None:
    """Phase C: a recent bar dips ≤`PHASE_SPRING_PENETRATION` below support on
    non-climactic volume, then closes back inside the range (the shakeout/test)."""
    floor = rng.support * (1 - config.PHASE_SPRING_PENETRATION)
    vol_cap = config.PHASE_SPRING_MAX_VOLUME_MULT * rng.avg_volume
    for b in reversed(window[-EVENT_WINDOW:]):
        if b.low is None or b.close is None:
            continue
        penetrates = floor <= b.low < rng.support
        recovers = b.close >= rng.support
        non_climactic = rng.avg_volume <= 0 or (b.volume or 0) <= vol_cap
        if penetrates and recovers and non_climactic:
            return PhaseEvent("SPRING", b.date, "dip below support recovered on non-climactic volume")
    return None


def _detect_sos(window: list[DailyBar], rng: TradingRange) -> int | None:
    """Sign of Strength: a wide up bar reaching/breaking resistance on expanding
    volume (≥`PHASE_SOS_VOLUME_MULT`× range avg). Returns its window index."""
    vol_floor = config.PHASE_SOS_VOLUME_MULT * rng.avg_volume
    start = len(window) - EVENT_WINDOW
    for i in range(max(1, start), len(window)):
        b = window[i]
        if b.high is None or b.close is None:
            continue
        reaches = b.high >= rng.resistance
        strong = b.close > (b.open or b.close) and _close_position(b) >= 0.5
        expanding = rng.avg_volume <= 0 or (b.volume or 0) >= vol_floor
        if reaches and strong and expanding:
            return i
    return None


def _detect_lps(window: list[DailyBar], rng: TradingRange, sos_idx: int) -> PhaseEvent | None:
    """Last Point of Support: after the SOS, a higher-low pullback holding in the
    upper half of the range (a controlled test of new support, not a breakdown)."""
    mid = rng.support + 0.5 * rng.span
    for b in window[sos_idx + 1:]:
        if b.low is None or b.close is None:
            continue
        higher_low = b.low >= mid
        holds = b.close >= mid
        not_markup = b.close < rng.resistance * (1 + config.PHASE_MARKUP_EXTENSION)
        if higher_low and holds and not_markup:
            return PhaseEvent("LPS", b.date, "higher-low pullback held above mid-range after SOS")
    return None


def _detect_utad(window: list[DailyBar], rng: TradingRange) -> PhaseEvent | None:
    """Upthrust After Distribution: a recent bar poking above resistance but closing
    back below it (rejection) — a distribution marker, not accumulation."""
    for b in reversed(window[-EVENT_WINDOW:]):
        if b.high is None or b.close is None:
            continue
        pokes = b.high > rng.resistance
        rejects = b.close < rng.resistance and _close_position(b) < config.VETO_DIST_CLOSE_POSITION
        if pokes and rejects:
            return PhaseEvent("UTAD", b.date, "poked above resistance and closed back below (rejection)")
    return None


def _detect_distribution(window: list[DailyBar], rng: TradingRange) -> bool:
    """Recent high-volume up bars closing in the lower half of their range — effort
    without result at the top of the structure (supply overcoming demand)."""
    recent = window[-EVENT_WINDOW:]
    weak_up = [
        b for b in recent
        if (b.close or 0) > (b.open or 0)
        and (b.volume or 0) >= rng.avg_volume
        and _close_position(b) < config.VETO_DIST_CLOSE_POSITION
    ]
    return len(weak_up) >= 2


# --- classifier --------------------------------------------------------------------


def classify(
    symbol: str, bars: list[DailyBar], decision_ts: datetime
) -> PhaseClassification:
    """Assign the Wyckoff phase and the RULE A tradeable verdict for `symbol`."""
    usable = _complete(bars)

    def verdict(phase: WyckoffPhase, reason: str, rng=None, events=()) -> PhaseClassification:
        return PhaseClassification(
            symbol=symbol, decision_ts=decision_ts, phase=phase,
            tradeable=phase in TRADEABLE_PHASES, trading_range=rng,
            events=tuple(events), reason=reason, bars_used=len(usable),
        )

    if len(usable) < config.PHASE_MIN_BARS:
        return verdict(WyckoffPhase.UNKNOWN, f"insufficient history ({len(usable)} < {config.PHASE_MIN_BARS} bars)")

    window = usable[-config.PHASE_RANGE_LOOKBACK:]
    rng, sc_idx = _establish_range(window)
    if rng is None or rng.support <= 0:
        return verdict(WyckoffPhase.UNKNOWN, "no trading range could be established")

    last = window[-1]
    events: list[PhaseEvent] = []
    if sc_idx is not None:
        events.append(PhaseEvent("SELLING_CLIMAX", window[sc_idx].date, "high-volume wide-spread down bar making a new low"))

    # [E] markup already extended out of the range — too late to arm.
    if last.close is not None and last.close >= rng.resistance * (1 + config.PHASE_MARKUP_EXTENSION):
        return verdict(WyckoffPhase.E, "price extended above resistance — markup underway", rng, events)

    # [DISTRIBUTION] up-structure rolling over / upthrust.
    utad = _detect_utad(window, rng)
    if utad is not None:
        return verdict(WyckoffPhase.DISTRIBUTION, "upthrust above resistance rejected", rng, events + [utad])
    if _detect_distribution(window, rng):
        return verdict(WyckoffPhase.DISTRIBUTION, "high-volume up bars closing in lower half (supply)", rng, events)

    # Not an accumulation range: too wide, or still trending down.
    base = window[:sc_idx] if sc_idx is not None else window[:-EVENT_WINDOW]
    if rng.width > config.PHASE_RANGE_MAX_WIDTH or (base and _is_downtrend(base, rng)):
        if last.close is not None and last.close < rng.support * (1 - config.PHASE_SPRING_PENETRATION):
            return verdict(WyckoffPhase.DOWNTREND, "no stopping action — price below range support", rng, events)

    # [C] spring / test — the classic Phase C event.
    spring = _detect_spring(window, rng)
    if spring is not None:
        return verdict(WyckoffPhase.C, "spring: shakeout below support recovered (test)", rng, events + [spring])

    # [D] sign of strength followed by a last point of support.
    sos_idx = _detect_sos(window, rng)
    if sos_idx is not None:
        lps = _detect_lps(window, rng, sos_idx)
        sos_ev = PhaseEvent("SOS", window[sos_idx].date, "wide up bar to resistance on expanding volume")
        if lps is not None:
            return verdict(WyckoffPhase.D, "sign of strength confirmed by a last point of support", rng, events + [sos_ev, lps])
        # SOS without a confirming LPS — breakout not yet tested; not tradeable (conservative).
        return verdict(WyckoffPhase.B, "sign of strength printed but no last-point-of-support test yet", rng, events + [sos_ev])

    # Range with recent stopping action but no test/SOS → Phase A (young) else B.
    if sc_idx is not None and (len(window) - sc_idx) <= AR_SPAN:
        return verdict(WyckoffPhase.A, "range forming after selling climax (no test yet)", rng, events)
    return verdict(WyckoffPhase.B, "cause-building oscillation inside the range (no test)", rng, events)


def analyze(
    store: Store,
    symbol: str,
    decision_ts: datetime,
    *,
    start: Date | None = None,
    end: Date | None = None,
) -> PhaseClassification:
    """Read look-ahead-safe bars (`as_of < decision_ts` enforced by the store) and
    classify the Wyckoff phase — the RULE A gate for `symbol`."""
    bars = store.read_daily_bars(symbol, decision_ts, start=start, end=end)
    return classify(symbol, bars, decision_ts)
