"""Stage-2 distribution / decay layer (spec §8) — each signal-decay detector fires on
its labeled chart, a clean accumulation stays clean (credibility: no false alarms),
and `monitor` unifies §5 veto traps with §8 decay look-ahead-safely.
"""

from __future__ import annotations

from datetime import datetime

from builders import Chart, distribution_bars, phase_c_bars

from currentflow.dal.timing import ohlcv_as_of
from currentflow.signals import broker_flow, distribution, phase
from currentflow.signals.distribution import DecayKind, DecaySeverity

TS = datetime(2026, 12, 1, 9, 0)


def _empty_broker():
    return broker_flow.build_snapshot("X", [], decision_ts=TS)


def _decay(bars, broker=None):
    broker = broker if broker is not None else _empty_broker()
    return distribution.build_decay(
        "X", bars=bars, broker=broker,
        phase_cls=phase.classify("X", bars, TS), decision_ts=TS,
    )


# --- clean accumulation stays clean --------------------------------------------------


def test_clean_phase_c_has_no_decay():
    report = _decay(phase_c_bars())
    assert report.active is False
    assert report.flags == ()
    assert report.max_severity is None


# --- distribution / UTAD -------------------------------------------------------------


def test_phase_rollover_flags_distribution():
    report = _decay(distribution_bars())          # oscillation + UTAD → DISTRIBUTION
    assert DecayKind.PHASE_ROLLOVER in report.kinds
    assert report.max_severity is DecaySeverity.WARN


# --- no-demand rally -----------------------------------------------------------------


def test_no_demand_up_bar_on_shrinking_volume():
    ch = Chart("X")
    for _ in range(12):
        ch.add(100, 104, 98, 101, 1000)          # normal bars, spread 6, vol 1000
    ch.add(101, 102, 100, 101.5, 400)            # up, narrow spread 2, vol 400 < both prior
    report = _decay(ch.bars)
    assert DecayKind.NO_DEMAND in report.kinds


def test_no_demand_absent_when_volume_expands():
    ch = Chart("X")
    for _ in range(12):
        ch.add(100, 104, 98, 101, 1000)
    ch.add(101, 102, 100, 101.5, 5000)           # up + narrow BUT volume expands → not no-demand
    assert DecayKind.NO_DEMAND not in _decay(ch.bars).kinds


# --- bearish divergence (the primary exit signal, §8) --------------------------------


def test_bearish_divergence_price_up_flow_down():
    ch = Chart("X")
    for i in range(12):
        c = 100 + 1.5 * i                        # steadily rising price
        ch.add(c - 1, c + 1, c - 2, c, 1000, nf=-(i + 1) * 1e9)  # foreign net ever more negative
    report = _decay(ch.bars)
    assert DecayKind.BEARISH_DIVERGENCE in report.kinds
    assert report.max_severity is DecaySeverity.WARN


def test_no_divergence_when_price_flat():
    ch = Chart("X")
    for _ in range(12):
        ch.add(100, 101, 99, 100, 1000, nf=-2e9)  # flat price even though flow negative
    assert DecayKind.BEARISH_DIVERGENCE not in _decay(ch.bars).kinds


# --- foreign outflow -----------------------------------------------------------------


def test_foreign_outflow_on_sell_streak():
    ch = Chart("X")
    for i in range(6):
        ch.add(100, 101, 99, 100, 1000, nf=(-1e9 if i >= 3 else 2e9))  # last 3 days net sell
    assert DecayKind.FOREIGN_OUTFLOW in _decay(ch.bars).kinds


def test_foreign_outflow_silent_when_flow_absent():
    ch = Chart("X")
    for _ in range(6):
        ch.add(100, 101, 99, 100, 1000)          # net_foreign None → missing ≠ zero, no outflow
    assert DecayKind.FOREIGN_OUTFLOW not in _decay(ch.bars).kinds


# --- monitor: unified trap + decay, look-ahead-safe ----------------------------------


def test_monitor_unifies_veto_and_decay_lookahead_safe(store):
    bars = distribution_bars("X")
    store.write_daily_bars(bars)

    before = ohlcv_as_of(bars[-1].date)           # decision moment BEFORE the last bar is knowable
    mon_blind = distribution.monitor(store, "X", before)
    assert DecayKind.PHASE_ROLLOVER not in mon_blind.decay.kinds   # last bar invisible → no UTAD yet

    mon = distribution.monitor(store, "X", TS)    # everything visible now
    assert mon.active is True
    assert DecayKind.PHASE_ROLLOVER in mon.decay.kinds
