"""Index-rebalancing filter (§3) — down-weight 30%, never reject."""

from __future__ import annotations

from datetime import date as Date
from datetime import timedelta

from currentflow.universe.rebalance import near_rebalance, rebalance_downweight

REBAL = Date(2026, 5, 29)
NEAR = Date(2026, 5, 27)
FAR = Date(2026, 4, 1)


def test_pure_beta_move_near_rebalance_downweighted_30pct():
    chk = rebalance_downweight(
        NEAR, stock_return=0.021, sector_return=0.02, beta=1.0,
        tracker_broker_flow_share=0.7,
    )
    assert chk.beta_explained and chk.near_rebalance and chk.tracker_flow_dominant
    assert chk.sms_multiplier == 0.7


def test_alpha_move_keeps_full_weight_even_near_rebalance():
    chk = rebalance_downweight(
        NEAR, stock_return=0.08, sector_return=0.02, beta=1.0,
        tracker_broker_flow_share=0.7,
    )
    assert not chk.beta_explained
    assert chk.sms_multiplier == 1.0


def test_beta_move_away_from_rebalance_keeps_full_weight():
    chk = rebalance_downweight(
        FAR, stock_return=0.02, sector_return=0.02, beta=1.0,
        tracker_broker_flow_share=0.7,
    )
    assert chk.sms_multiplier == 1.0


def test_beta_move_without_tracker_flow_keeps_full_weight():
    chk = rebalance_downweight(
        NEAR, stock_return=0.02, sector_return=0.02, beta=1.0,
        tracker_broker_flow_share=0.2,
    )
    assert chk.sms_multiplier == 1.0


def test_downweight_never_rejects():
    # the filter's only outputs are multipliers 0.7 or 1.0 — there is no reject path
    for share in (0.0, 0.9):
        for day in (NEAR, FAR):
            m = rebalance_downweight(
                day, 0.02, 0.02, 1.0, tracker_broker_flow_share=share
            ).sms_multiplier
            assert m in (0.7, 1.0)


def test_near_rebalance_window():
    assert near_rebalance(REBAL)
    assert near_rebalance(REBAL + timedelta(days=7))
    assert not near_rebalance(REBAL + timedelta(days=8))
