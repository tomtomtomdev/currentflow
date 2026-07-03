"""Risk / exit manager (§8): stop / target / trailing / signal-decay, priority order,
and the missing-≠-zero hold when there is nothing to evaluate."""

from __future__ import annotations

from datetime import datetime

from currentflow.execution.risk import ExitReason, OpenPosition, evaluate_exit
from currentflow.signals.distribution import DecayFlag, DecayKind, DecayReport, DecaySeverity
from tests.builders import Chart

TS = datetime(2026, 7, 3, 9, 15)


def _empty_decay():
    return DecayReport(symbol="TEST", decision_ts=TS, flags=())


def _decay(*flags):
    return DecayReport(symbol="TEST", decision_ts=TS, flags=tuple(flags))


def _pos(ch, *, stop, target, trail_pct=0.10, entry_price=100.0):
    return OpenPosition(
        symbol="TEST", entry_date=ch.bars[0].date, entry_price=entry_price,
        stop=stop, target=target, trail_pct=trail_pct, qty=10_000,
    )


def test_stop_hit():
    ch = Chart("TEST").add(o=96, h=97, l=94, c=95)
    d = evaluate_exit(_pos(ch, stop=95, target=120), ch.bars, _empty_decay(), TS)
    assert d.should_exit and d.reason is ExitReason.STOP
    assert d.reference_price == 95


def test_target_hit():
    ch = Chart("TEST").add(o=118, h=121, l=117, c=120)
    d = evaluate_exit(_pos(ch, stop=90, target=120), ch.bars, _empty_decay(), TS)
    assert d.should_exit and d.reason is ExitReason.TARGET
    assert d.reference_price == 120


def test_stop_dominates_target_in_same_bar():
    # A wide bar tags both stop and target — assume the worst (STOP).
    ch = Chart("TEST").add(o=100, h=125, l=89, c=110)
    d = evaluate_exit(_pos(ch, stop=90, target=120), ch.bars, _empty_decay(), TS)
    assert d.reason is ExitReason.STOP


def test_trailing_stop():
    ch = (
        Chart("TEST")
        .add(o=99, h=101, l=98, c=100)      # entry
        .add(o=108, h=111, l=107, c=110)
        .add(o=118, h=121, l=117, c=120)
        .add(o=128, h=131, l=127, c=130)    # post-entry high close 130
        .add(o=116, h=118, l=114, c=115)    # falls 11.5% below 130 → trail (10%) hit
    )
    d = evaluate_exit(_pos(ch, stop=90, target=200, trail_pct=0.10), ch.bars, _empty_decay(), TS)
    assert d.should_exit and d.reason is ExitReason.TRAILING
    assert abs(d.reference_price - 130 * 0.9) < 1e-6


def test_signal_decay_exit():
    ch = (
        Chart("TEST")
        .add(o=99, h=102, l=98, c=100)
        .add(o=100, h=103, l=99, c=101)
        .add(o=101, h=104, l=100, c=102)
    )
    decay = _decay(
        DecayFlag(DecayKind.BEARISH_DIVERGENCE, DecaySeverity.WARN, "price up while flow falls"),
    )
    d = evaluate_exit(_pos(ch, stop=90, target=200, trail_pct=0.50), ch.bars, decay, TS)
    assert d.should_exit and d.reason is ExitReason.SIGNAL_DECAY
    assert "BEARISH_DIVERGENCE" in d.detail


def test_clean_position_holds():
    ch = (
        Chart("TEST")
        .add(o=99, h=102, l=98, c=100)
        .add(o=100, h=103, l=99, c=101)
        .add(o=101, h=104, l=100, c=102)
    )
    d = evaluate_exit(_pos(ch, stop=90, target=200, trail_pct=0.50), ch.bars, _empty_decay(), TS)
    assert not d.should_exit and d.reason is ExitReason.NONE


def test_no_post_entry_bars_holds():
    ch = Chart("TEST").add(o=99, h=102, l=98, c=100)
    pos = OpenPosition(
        symbol="TEST", entry_date=ch.bars[0].date.replace(year=2027),  # entry in the future
        entry_price=100.0, stop=90, target=120, trail_pct=0.10, qty=100,
    )
    d = evaluate_exit(pos, ch.bars, _empty_decay(), TS)
    assert not d.should_exit


def test_most_severe_decay_flag_named():
    ch = Chart("TEST").add(o=99, h=102, l=98, c=100).add(o=100, h=103, l=99, c=101)
    decay = _decay(
        DecayFlag(DecayKind.FOREIGN_OUTFLOW, DecaySeverity.WATCH, "foreign selling"),
        DecayFlag(DecayKind.PHASE_ROLLOVER, DecaySeverity.WARN, "rolled to distribution"),
    )
    d = evaluate_exit(_pos(ch, stop=90, target=200, trail_pct=0.50), ch.bars, decay, TS)
    assert d.reason is ExitReason.SIGNAL_DECAY
    assert "rolled to distribution" in d.detail   # WARN flag's detail wins
