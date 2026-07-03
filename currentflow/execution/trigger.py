"""Technical entry trigger (spec §6, LD-3) — Grimes discipline, no market-on-signal.

A passing engine result sets state = ARMED, **not ENTER**. Entry requires a confirmation
trigger and a favourable structure or it is skipped:

    - **Phase C — Spring test:** trigger = close of the spring-test bar; stop below the
      spring low (thesis-invalidation); first target = the automatic-rally high (range
      resistance).
    - **Phase D — LPS:** trigger = close of the last-point-of-support pullback after the
      sign-of-strength; stop below the LPS swing low; first target = a measured move
      (resistance + the range span, a Wyckoff count) since price already reached resistance.

The order is a **limit at/below the trigger** (never a market order — next-open + ARA/ARB
make market fills fiction anyway, LD-3). **R:R ≥ 2:1 to the first structural target or the
trade is skipped** (§6). The stop is the invalidation level and is never widened.

RULE A upstream: only Phase C/D reach here (the classifier gates first). This module adds
no score — it emits price levels and a boolean go/skip on the R:R rule. `missing ≠ zero`:
without a locatable spring/LPS bar or a valid range there is simply no trigger.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as Date
from datetime import datetime
from enum import Enum

from currentflow import config
from currentflow.dal.models import DailyBar, RowStatus
from currentflow.signals.phase import PhaseClassification, WyckoffPhase
from currentflow.store.db import Store


class TriggerKind(str, Enum):
    SPRING = "SPRING"   # Phase C spring-test close
    LPS = "LPS"         # Phase D last-point-of-support pullback
    NONE = "NONE"       # no confirmation trigger


@dataclass(frozen=True, slots=True)
class TriggerSignal:
    """The §6 entry geometry for one ARMED candidate. No score."""

    symbol: str
    decision_ts: datetime
    kind: TriggerKind
    trigger_price: float | None   # the confirmation bar's close
    entry_limit: float | None     # limit at/below the trigger (never a market order)
    stop: float | None            # invalidation level (never widened)
    target: float | None          # first structural target (AR high / measured move)
    rr: float | None              # (target − entry) / (entry − stop)
    valid: bool                   # R:R ≥ 2:1 and a coherent stop < entry < target
    reason: str


def _bar_on(bars: list[DailyBar], day: Date) -> DailyBar | None:
    for b in bars:
        if b.date == day and b.status is RowStatus.TRADED:
            return b
    return None


def _event_date(phase_cls: PhaseClassification, kind: str) -> Date | None:
    for e in phase_cls.events:
        if e.kind == kind:
            return e.date
    return None


def detect(
    symbol: str,
    phase_cls: PhaseClassification,
    bars: list[DailyBar],
    decision_ts: datetime,
) -> TriggerSignal:
    """Compute the entry trigger, stop, target, and R:R for a Phase C/D candidate."""

    def skip(reason: str, kind: TriggerKind = TriggerKind.NONE) -> TriggerSignal:
        return TriggerSignal(
            symbol=symbol, decision_ts=decision_ts, kind=kind, trigger_price=None,
            entry_limit=None, stop=None, target=None, rr=None, valid=False, reason=reason,
        )

    rng = phase_cls.trading_range
    if not phase_cls.tradeable or rng is None:
        return skip(f"not tradeable / no range (phase {phase_cls.phase.value})")

    if phase_cls.phase is WyckoffPhase.C:
        day = _event_date(phase_cls, "SPRING")
        bar = _bar_on(bars, day) if day else None
        if bar is None or bar.close is None or bar.low is None:
            return skip("Phase C but the spring bar is not locatable", TriggerKind.SPRING)
        kind = TriggerKind.SPRING
        trigger_price = bar.close
        stop = bar.low * (1 - config.STOP_BUFFER)          # below the spring low
        target = rng.resistance                            # first target = AR high
    else:  # Phase D
        day = _event_date(phase_cls, "LPS")
        bar = _bar_on(bars, day) if day else None
        if bar is None or bar.close is None or bar.low is None:
            return skip("Phase D but the LPS bar is not locatable", TriggerKind.LPS)
        kind = TriggerKind.LPS
        trigger_price = bar.close
        stop = bar.low * (1 - config.STOP_BUFFER)          # below the LPS swing low
        target = rng.resistance + config.TARGET_MEASURED_MOVE_MULT * rng.span  # measured move

    entry = trigger_price * (1 - config.LIMIT_UNDERCUT)    # limit at/below the trigger
    if not (stop < entry < target):
        return skip(
            f"incoherent geometry (stop {stop:.2f}, entry {entry:.2f}, target {target:.2f})", kind
        )
    rr = (target - entry) / (entry - stop)
    valid = rr >= config.RR_MIN
    reason = (
        f"{kind.value}: entry {entry:.2f}, stop {stop:.2f}, target {target:.2f}, "
        f"R:R {rr:.2f} {'≥' if valid else '<'} {config.RR_MIN:g} → {'ENTER' if valid else 'SKIP'}"
    )
    return TriggerSignal(
        symbol=symbol, decision_ts=decision_ts, kind=kind, trigger_price=trigger_price,
        entry_limit=entry, stop=stop, target=target, rr=rr, valid=valid, reason=reason,
    )


def analyze(
    store: Store,
    symbol: str,
    decision_ts: datetime,
    phase_cls: PhaseClassification,
    *,
    start: Date | None = None,
    end: Date | None = None,
) -> TriggerSignal:
    """Read look-ahead-safe bars and compute the entry trigger for `symbol`."""
    bars = store.read_daily_bars(symbol, decision_ts, start=start, end=end)
    return detect(symbol, phase_cls, bars, decision_ts)
