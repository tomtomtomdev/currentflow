"""Technical trigger (§6, LD-3): Spring/LPS geometry, R:R ≥ 2:1 gate, and the skip
paths (non-tradeable phase, incoherent geometry). Acceptance: every emitted order has a
defined stop and R:R ≥ 2:1."""

from __future__ import annotations

from datetime import date as Date
from datetime import datetime

from currentflow import config
from currentflow.execution.trigger import TriggerKind, detect
from currentflow.signals.phase import (
    PhaseClassification,
    PhaseEvent,
    TradingRange,
    WyckoffPhase,
    classify,
)
from tests.builders import Chart, phase_c_bars, phase_d_bars

TS = datetime(2026, 7, 3, 9, 15)


def _classification(phase, rng, events, tradeable):
    return PhaseClassification(
        symbol="TEST", decision_ts=TS, phase=phase, tradeable=tradeable,
        trading_range=rng, events=tuple(events), reason="test", bars_used=50,
    )


# --- Phase C spring (integration through the real classifier) ------------------------


def test_phase_c_spring_trigger_geometry():
    bars = phase_c_bars("TEST")
    cls = classify("TEST", bars, TS)
    assert cls.phase is WyckoffPhase.C and cls.tradeable
    sig = detect("TEST", cls, bars, TS)
    assert sig.kind is TriggerKind.SPRING
    assert sig.trigger_price == 102          # spring bar close
    assert abs(sig.stop - 98 * (1 - config.STOP_BUFFER)) < 1e-6   # below the spring low
    assert sig.target == cls.trading_range.resistance             # first target = AR high
    assert sig.entry_limit == 102
    assert sig.rr >= config.RR_MIN and sig.valid


# --- Phase D LPS ---------------------------------------------------------------------


def test_phase_d_lps_trigger_measured_move():
    bars = phase_d_bars("TEST")
    cls = classify("TEST", bars, TS)
    assert cls.phase is WyckoffPhase.D and cls.tradeable
    sig = detect("TEST", cls, bars, TS)
    assert sig.kind is TriggerKind.LPS
    assert sig.trigger_price == 114          # LPS bar close
    # measured-move target = resistance + one range span
    rng = cls.trading_range
    assert abs(sig.target - (rng.resistance + rng.span)) < 1e-6
    assert sig.valid and sig.rr >= config.RR_MIN


# --- R:R gate (skip) -----------------------------------------------------------------


def test_low_rr_is_skipped():
    # Hand-built Phase C where the target sits barely above entry → R:R < 2 → SKIP.
    rng = TradingRange(support=100, resistance=104, start=Date(2026, 1, 1),
                       end=Date(2026, 3, 1), avg_volume=1000)
    spring_day = Date(2026, 3, 2)
    bar = Chart("TEST", start=spring_day)
    bar.add(o=101, h=103, l=99, c=102)       # spring: close 102, low 99
    cls = _classification(
        WyckoffPhase.C, rng, [PhaseEvent("SPRING", spring_day, "spring")], tradeable=True,
    )
    sig = detect("TEST", cls, bar.bars, TS)
    # entry 102, stop 99×0.995≈98.5, target 104 → RR = (104−102)/(102−98.5) ≈ 0.57 < 2.
    assert not sig.valid
    assert sig.rr < config.RR_MIN


def test_non_tradeable_phase_yields_no_trigger():
    rng = TradingRange(support=100, resistance=120, start=Date(2026, 1, 1),
                       end=Date(2026, 3, 1), avg_volume=1000)
    cls = _classification(WyckoffPhase.B, rng, [], tradeable=False)
    sig = detect("TEST", cls, [], TS)
    assert sig.kind is TriggerKind.NONE and not sig.valid


def test_phase_c_without_locatable_spring_bar_skips():
    rng = TradingRange(support=100, resistance=120, start=Date(2026, 1, 1),
                       end=Date(2026, 3, 1), avg_volume=1000)
    cls = _classification(
        WyckoffPhase.C, rng, [PhaseEvent("SPRING", Date(2026, 3, 2), "spring")], tradeable=True,
    )
    sig = detect("TEST", cls, [], TS)   # no bars → spring bar not locatable
    assert not sig.valid
