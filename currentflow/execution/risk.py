"""Risk / exit manager (spec §8, pipeline step [10]).

An open paper position exits on the first of (§8):

    - **STOP**          the invalidation stop is hit (never widened, §6)
    - **TARGET**        the first structural target is reached
    - **TRAILING**      price falls a hold-profile-width below its post-entry high
                        (COMPOUNDER rides wide, SPECULATIVE trails tight — §7)
    - **SIGNAL_DECAY**  the flow thesis breaks: NBSA flips negative / dominant broker
                        flips to net sell / VPA prints UTAD or no-demand / phase rolls
                        to distribution. **"Divergence is the single best exit signal"**
                        (price rising while flow falls) — surfaced by `signals.distribution`.

Priority is capital-first: STOP, then TARGET, then TRAILING, then SIGNAL_DECAY. If a bar's
range spans both the stop and the target we assume the worst (STOP).

RULE B: an exit decision is a categorical reason + the reference price, never a score or
probability. `missing ≠ zero`: only complete TRADED bars drive stop/target/trailing, and a
decay exit fires only on an actually-observed decay flag — a data gap never forces an exit.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as Date
from datetime import datetime
from enum import Enum

from currentflow.dal.models import DailyBar, RowStatus
from currentflow.signals import broker_flow, distribution, phase as phase_mod
from currentflow.signals.broker_flow import BrokerDNA
from currentflow.signals.distribution import DecayReport, DecaySeverity
from currentflow.store.db import Store

# INFO < WATCH < WARN — pick the most severe flag's detail for the exit note.
_SEVERITY_ORDER = (DecaySeverity.INFO, DecaySeverity.WATCH, DecaySeverity.WARN)


class ExitReason(str, Enum):
    NONE = "NONE"
    STOP = "STOP"
    TARGET = "TARGET"
    TRAILING = "TRAILING"
    SIGNAL_DECAY = "SIGNAL_DECAY"


@dataclass(frozen=True, slots=True)
class OpenPosition:
    """The levels the exit manager watches for one held paper position."""

    symbol: str
    entry_date: Date
    entry_price: float
    stop: float
    target: float
    trail_pct: float           # hold-profile trailing width (from the §7 tilt)
    qty: int = 0


@dataclass(frozen=True, slots=True)
class ExitDecision:
    symbol: str
    decision_ts: datetime
    should_exit: bool
    reason: ExitReason
    reference_price: float | None   # stop / target / trail level / last close
    detail: str
    decay: DecayReport | None = None


def _complete_since(bars: list[DailyBar], entry_date: Date) -> list[DailyBar]:
    return [
        b for b in sorted(bars, key=lambda b: b.date)
        if b.date >= entry_date
        and b.status is RowStatus.TRADED
        and None not in (b.high, b.low, b.close)
    ]


def evaluate_exit(
    position: OpenPosition,
    bars: list[DailyBar],
    decay: DecayReport,
    decision_ts: datetime,
) -> ExitDecision:
    """Apply the §8 exit rules to one position against its post-entry bars + decay flags."""

    def hold(detail: str = "no exit trigger") -> ExitDecision:
        return ExitDecision(
            symbol=position.symbol, decision_ts=decision_ts, should_exit=False,
            reason=ExitReason.NONE, reference_price=None, detail=detail, decay=decay,
        )

    def exit_(reason: ExitReason, price: float, detail: str) -> ExitDecision:
        return ExitDecision(
            symbol=position.symbol, decision_ts=decision_ts, should_exit=True,
            reason=reason, reference_price=price, detail=detail, decay=decay,
        )

    window = _complete_since(bars, position.entry_date)
    if not window:
        return hold("no complete post-entry bars yet")
    last = window[-1]

    # [1] STOP — capital protection first (worst-case if the bar also tags the target).
    if last.low <= position.stop:
        return exit_(ExitReason.STOP, position.stop, f"low {last.low:.2f} ≤ stop {position.stop:.2f}")

    # [2] TARGET — first structural target reached.
    if last.high >= position.target:
        return exit_(ExitReason.TARGET, position.target, f"high {last.high:.2f} ≥ target {position.target:.2f}")

    # [3] TRAILING — fell a profile-width below the post-entry high close.
    highest_close = max(b.close for b in window)
    trail_level = highest_close * (1 - position.trail_pct)
    if last.close <= trail_level:
        return exit_(
            ExitReason.TRAILING, trail_level,
            f"close {last.close:.2f} ≤ trail {trail_level:.2f} "
            f"({position.trail_pct:.0%} below high {highest_close:.2f})",
        )

    # [4] SIGNAL_DECAY — the §8 flow-thesis break (divergence = the best exit signal).
    if decay.active:
        top = max(decay.flags, key=lambda f: _SEVERITY_ORDER.index(f.severity))
        return exit_(
            ExitReason.SIGNAL_DECAY, last.close,
            f"signal decay: {', '.join(f.kind.value for f in decay.flags)} — {top.detail}",
        )

    return hold()


def analyze(
    store: Store,
    position: OpenPosition,
    decision_ts: datetime,
    *,
    start: Date | None = None,
    end: Date | None = None,
    registry: dict[str, BrokerDNA] | None = None,
) -> ExitDecision:
    """Read look-ahead-safe bars + broker flow + phase, build the §8 decay report, and
    evaluate the exit for `position`."""
    bars = store.read_daily_bars(position.symbol, decision_ts, start=start, end=end)
    broker = broker_flow.analyze(store, position.symbol, decision_ts, start=start, end=end, registry=registry)
    phase_cls = phase_mod.classify(position.symbol, bars, decision_ts)
    decay = distribution.build_decay(
        position.symbol, bars=bars, broker=broker, phase_cls=phase_cls, decision_ts=decision_ts
    )
    return evaluate_exit(position, bars, decay, decision_ts)
