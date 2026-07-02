"""Smart Money Score (§4) — component math, track-specific weighting, and the
rebalance multiplier. The SMS *number* itself is exercised as an internal value here;
its RULE B non-display is asserted in test_rule_b.py.
"""

from __future__ import annotations

from datetime import date as Date
from datetime import datetime

import pytest
from builders import Chart, concentrated_buyer_rows, phase_c_bars

from currentflow import config
from currentflow.signals import broker_flow, foreign_flow, phase
from currentflow.signals.sms import compute_sms

TS = datetime(2026, 7, 1, 9, 0)


def _empty_broker():
    return broker_flow.build_snapshot("X", [], decision_ts=TS)


def _neutral_phase():
    from builders import phase_b_bars
    return phase.classify("X", phase_b_bars(), TS)


def _flat(n: int, v: float = 1000.0) -> list:
    ch = Chart("X")
    for _ in range(n):
        ch.add(100, 101, 99, 100, v)
    return ch.bars


# --- locked weights ------------------------------------------------------------------


def test_weights_sum_to_100_and_track_b_excludes_foreign():
    for track in ("A", "B"):
        assert sum(config.SMS_WEIGHTS[track].values()) == 100
    assert config.SMS_WEIGHTS["B"]["foreign_flow"] == 0
    assert config.SMS_WEIGHTS["A"]["foreign_flow"] == 25


# --- divergence (the universal spine, LD-1) -----------------------------------------


def test_divergence_credits_flat_price_on_high_volume():
    diverge = Chart("X")
    for _ in range(18):
        diverge.add(100, 101, 99, 100, 1000)
    for _ in range(4):
        diverge.add(100, 101, 99, 100, 2500)      # high volume, price stays flat (absorption)

    no_diverge = Chart("X")
    for _ in range(18):
        no_diverge.add(100, 101, 99, 100, 1000)
    c = 100.0
    for _ in range(4):                             # high volume drives ~8% moves each bar
        nxt = c * 1.08
        no_diverge.add(c, nxt + 1, c - 1, nxt, 2500)
        c = nxt

    hi = compute_sms("X", track="A", bars=diverge.bars, broker=_empty_broker(),
                     foreign=None, phase_cls=_neutral_phase(), decision_ts=TS)
    lo = compute_sms("X", track="A", bars=no_diverge.bars, broker=_empty_broker(),
                     foreign=None, phase_cls=_neutral_phase(), decision_ts=TS)
    assert hi.components_by_key["divergence"].subscore > lo.components_by_key["divergence"].subscore
    assert lo.components_by_key["divergence"].subscore == 0.0


# --- rvol ----------------------------------------------------------------------------


def test_rvol_reaches_full_credit_at_3x():
    ch = Chart("X")
    for _ in range(20):
        ch.add(100, 101, 99, 100, 1000)
    ch.add(100, 101, 99, 100, 3000)               # last bar 3× the 20d average
    res = compute_sms("X", track="A", bars=ch.bars, broker=_empty_broker(),
                      foreign=None, phase_cls=_neutral_phase(), decision_ts=TS)
    rvol = res.components_by_key["rvol"]
    assert rvol.observation["rvol"] == pytest.approx(3.0)
    assert rvol.subscore == pytest.approx(1.0)


# --- broker concentration (Track B lead) --------------------------------------------


def test_broker_concentration_rewards_persistent_top2_on_flat_price():
    days = [Date(2026, 6, 24), Date(2026, 6, 25), Date(2026, 6, 26)]
    snap = broker_flow.build_snapshot("X", concentrated_buyer_rows("X", days), decision_ts=TS)
    res = compute_sms("X", track="B", bars=_flat(10), broker=snap,
                      foreign=None, phase_cls=_neutral_phase(), decision_ts=TS)
    comp = res.components_by_key["broker_concentration"]
    assert comp.available
    assert comp.observation["persistence_days"] == 3
    assert comp.observation["flat_or_down"] is True
    assert comp.subscore > 0.8           # top-2 share ~0.9, persistent, on flat bars
    assert comp.weight == 35             # Track B weight


# --- foreign flow (Track A only) -----------------------------------------------------


def _foreign_snapshot():
    ch = Chart("BBRI")
    for _ in range(6):
        ch.add(100, 101, 99, 100, 1000, nf=1e9)
    ch.add(100, 101, 99, 100, 1000, nf=5e9)       # 5× the 20d average, rising
    return foreign_flow.build_snapshot("BBRI", ch.bars, decision_ts=TS)


def test_foreign_flow_scores_track_a_but_is_excluded_for_track_b():
    fs = _foreign_snapshot()
    a = compute_sms("BBRI", track="A", bars=_flat(10), broker=_empty_broker(),
                    foreign=fs, phase_cls=_neutral_phase(), decision_ts=TS)
    b = compute_sms("BBRI", track="B", bars=_flat(10), broker=_empty_broker(),
                    foreign=fs, phase_cls=_neutral_phase(), decision_ts=TS)
    ca, cb = a.components_by_key["foreign_flow"], b.components_by_key["foreign_flow"]
    assert ca.weight == 25 and ca.subscore > 0 and ca.contribution > 0
    assert cb.weight == 0 and cb.contribution == 0
    assert "excluded" in cb.observation


# --- assembly & the rebalance multiplier --------------------------------------------


def test_internal_score_scales_by_rebalance_multiplier():
    days = [Date(2026, 6, 24), Date(2026, 6, 25), Date(2026, 6, 26)]
    snap = broker_flow.build_snapshot("TEST", concentrated_buyer_rows("TEST", days), decision_ts=TS)
    bars = phase_c_bars()
    phase_cls = phase.classify("TEST", bars, TS)

    full = compute_sms("TEST", track="B", bars=bars, broker=snap, foreign=None,
                       phase_cls=phase_cls, decision_ts=TS, rebalance_multiplier=1.0)
    down = compute_sms("TEST", track="B", bars=bars, broker=snap, foreign=None,
                       phase_cls=phase_cls, decision_ts=TS, rebalance_multiplier=config.REBALANCE_DOWNWEIGHT)
    assert full.internal_score > 0
    assert down.internal_score == pytest.approx(full.internal_score * config.REBALANCE_DOWNWEIGHT, rel=1e-6)
    assert 0 <= full.internal_score <= 100
