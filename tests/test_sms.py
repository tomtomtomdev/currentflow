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


def test_divergence_scores_recent_absorption_not_stale_history():
    """Divergence must reflect the CURRENT window, not a year-long average (the defect
    fix). Same bars in a different order: absorption RECENT → credited; absorption STALE
    (trending recently) → not. Fails on the old whole-history detector, which scored both
    identically."""
    recent = Chart("R")
    c = 100.0
    for _ in range(60):                        # older: high-vol bars WITH big moves (no divergence)
        nxt = c * 1.03
        recent.add(c, nxt + 1, c - 1, nxt, 1000); c = nxt
    for _ in range(20):                        # recent: high volume, price flat (absorption)
        recent.add(c, c + 1, c - 1, c, 3000)

    stale = Chart("S")
    c = 100.0
    for _ in range(20):                        # stale absorption at the START
        stale.add(c, c + 1, c - 1, c, 3000)
    for _ in range(60):                        # …then a recent markup (high vol, big moves)
        nxt = c * 1.03
        stale.add(c, nxt + 1, c - 1, nxt, 1000); c = nxt

    r = compute_sms("R", track="A", bars=recent.bars, broker=_empty_broker(),
                    foreign=None, phase_cls=_neutral_phase(), decision_ts=TS)
    s = compute_sms("S", track="A", bars=stale.bars, broker=_empty_broker(),
                    foreign=None, phase_cls=_neutral_phase(), decision_ts=TS)
    assert r.components_by_key["divergence"].subscore > 0.3
    assert r.components_by_key["divergence"].subscore > s.components_by_key["divergence"].subscore


def test_divergence_corr_is_graduated_not_a_cliff():
    """The corr adjustment is a smooth factor in [0.5, 1.0], not a binary ×0.5 haircut:
    a lower vol/|move| correlation yields a higher factor (more divergence confidence)."""
    from currentflow.signals.sms import _corr_factor
    assert _corr_factor(None) == 0.5
    assert _corr_factor(-0.5) == 1.0
    assert _corr_factor(0.0) == 1.0
    assert _corr_factor(config.SMS_DIVERGENCE_CORR_MAX) == pytest.approx(0.5)
    assert _corr_factor(0.9) == 0.5                                  # floored, never below
    mid = _corr_factor(config.SMS_DIVERGENCE_CORR_MAX / 2)
    assert 0.5 < mid < 1.0                                           # graduated in between


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


# --- block-trade footprint (§4: > IDR 1B or > 1% ADV) --------------------------------


def test_block_trade_grades_by_pct_of_adv_not_a_fixed_floor():
    """Block footprint grades by the max single-broker buy as a fraction of ADV (§4's
    scale-relative "> 1% ADV"), not the fixed IDR-1B floor that saturated to 1.0 on any
    liquid name. Same broker snapshot, larger ADV → smaller %ADV → lower subscore."""
    days = [Date(2026, 6, 24), Date(2026, 6, 25), Date(2026, 6, 26)]
    snap = broker_flow.build_snapshot("X", concentrated_buyer_rows("X", days), decision_ts=TS)
    # max single-broker buy from the builder is 8e9/day.
    diffuse = compute_sms("X", track="B", bars=_flat(10), broker=snap, foreign=None,
                          phase_cls=_neutral_phase(), decision_ts=TS, adv20=8e12)   # 0.1% ADV
    dense = compute_sms("X", track="B", bars=_flat(10), broker=snap, foreign=None,
                        phase_cls=_neutral_phase(), decision_ts=TS, adv20=8e11)      # 1% ADV
    bd = diffuse.components_by_key["block_trade"]
    bc = dense.components_by_key["block_trade"]
    assert bd.subscore < 1.0                    # de-saturated — not everyone maxes out
    assert bc.subscore > bd.subscore            # smaller ADV → larger %ADV → higher credit
    assert bd.observation["pct_of_adv"] == pytest.approx(0.003)   # 3×8e9 aggregated / 8e12


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
