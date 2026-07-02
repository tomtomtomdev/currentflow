"""Wyckoff phase classifier (RULE A) — the acceptance test: the gate must reject
every non-C/D chart and pass only spring (C) and SOS+LPS (D) accumulation.

Charts are hand-built archetypes (tests/builders.py) so the label is known by
construction.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from builders import (
    Chart,
    distribution_bars,
    downtrend_bars,
    phase_a_bars,
    phase_b_bars,
    phase_c_bars,
    phase_d_bars,
    phase_e_bars,
)

from currentflow.signals import phase
from currentflow.signals.phase import WyckoffPhase

TS = datetime(2026, 6, 1, 9, 15)


# --- the acceptance criterion: only C and D are tradeable ---------------------------


def test_phase_c_spring_is_tradeable():
    cls = phase.classify("TEST", phase_c_bars(), TS)
    assert cls.phase is WyckoffPhase.C
    assert cls.tradeable is True
    assert any(e.kind == "SPRING" for e in cls.events)


def test_phase_d_sos_plus_lps_is_tradeable():
    cls = phase.classify("TEST", phase_d_bars(), TS)
    assert cls.phase is WyckoffPhase.D
    assert cls.tradeable is True
    assert {e.kind for e in cls.events} >= {"SOS", "LPS"}


def test_non_cd_charts_are_all_rejected():
    labels = {
        "downtrend": (downtrend_bars(), {WyckoffPhase.DOWNTREND, WyckoffPhase.UNKNOWN}),
        "phase_a": (phase_a_bars(), {WyckoffPhase.A, WyckoffPhase.B}),
        "phase_b": (phase_b_bars(), {WyckoffPhase.B}),
        "phase_e": (phase_e_bars(), {WyckoffPhase.E}),
        "distribution": (distribution_bars(), {WyckoffPhase.DISTRIBUTION}),
    }
    for name, (bars, allowed) in labels.items():
        cls = phase.classify("TEST", bars, TS)
        assert cls.tradeable is False, f"{name} must NOT be tradeable (got {cls.phase})"
        assert cls.phase in allowed, f"{name}: {cls.phase} not in {allowed}"


def test_insufficient_history_is_unknown_not_tradeable():
    cls = phase.classify("TEST", Chart().oscillate(20).bars, TS)
    assert cls.phase is WyckoffPhase.UNKNOWN
    assert cls.tradeable is False


# --- look-ahead safety through the store read ---------------------------------------


def test_spring_invisible_before_it_publishes(store):
    bars = phase_c_bars()
    store.write_daily_bars(bars)
    spring_day = bars[-1].date
    early = phase.analyze(store, "TEST", decision_ts=datetime.combine(spring_day, datetime.min.time()))
    assert early.phase is not WyckoffPhase.C   # spring bar not yet knowable
    assert early.tradeable is False
    late = phase.analyze(store, "TEST", decision_ts=datetime.combine(spring_day + timedelta(days=1), datetime.min.time()))
    assert late.phase is WyckoffPhase.C
    assert late.tradeable is True
