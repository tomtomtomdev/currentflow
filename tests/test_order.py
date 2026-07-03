"""Order generation (§6): 1%-risk sizing, conviction multiplier, exposure caps,
circuit breakers, limit-only + defined-stop invariant."""

from __future__ import annotations

from datetime import datetime

from currentflow import config
from currentflow.dal.models import BoardType, Side
from currentflow.execution.order import OrderStatus, generate_order
from currentflow.execution.trigger import TriggerKind, TriggerSignal
from currentflow.fundamentals.tilt import classify_tilt
from currentflow.signals.risk_monitor import CircuitState, Portfolio, Position

TS = datetime(2026, 7, 3, 9, 15)


def _trigger(entry=100.0, stop=80.0, target=160.0, valid=True):
    rr = (target - entry) / (entry - stop)
    return TriggerSignal(
        symbol="TEST", decision_ts=TS, kind=TriggerKind.SPRING, trigger_price=entry,
        entry_limit=entry, stop=stop, target=target, rr=rr, valid=valid, reason="test",
    )


COMPOUNDER = classify_tilt("TEST", sector="CONSUMER", mf_rank_pct=90)   # ×1.0
SPECULATIVE = classify_tilt("TEST", sector="CONSUMER", mf_rank_pct=10)  # ×0.5


def test_one_percent_risk_sizing():
    # equity 100mn, risk/share 20 (entry 100, stop 80), ×1.0 → risk budget 1mn →
    # 50,000 shares; notional 5mn < 10% name cap → no clamp.
    o = generate_order(_trigger(), equity=100_000_000, tilt=COMPOUNDER, sector="CONSUMER")
    assert o.accepted
    assert o.qty == 50_000
    assert o.risk_idr == 1_000_000
    # invariant: limit order, defined stop, R:R ≥ 2 (§13)
    assert o.order_type == "LIMIT" and o.side is Side.BUY
    assert o.stop == 80.0 and o.rr >= config.RR_MIN


def test_conviction_multiplier_halves_speculative_size():
    o = generate_order(_trigger(), equity=100_000_000, tilt=SPECULATIVE, sector="CONSUMER")
    assert o.accepted
    assert o.qty == 25_000            # half of the compounder size (×0.5)
    assert o.risk_idr == 500_000


def test_name_exposure_cap_clamps_qty():
    # Tight 5% stop makes the 1%-risk size huge; the 10%/name cap must bind.
    o = generate_order(_trigger(entry=100, stop=95, target=115), equity=100_000_000,
                       tilt=COMPOUNDER, sector="CONSUMER")
    assert o.accepted
    assert o.notional <= 100_000_000 * config.EXPOSURE_CAP_NAME + 1  # ≤ 10% equity
    assert o.qty == 100_000           # floor(10mn / 100) in whole lots


def test_sector_cap_binds_tighter_than_name_cap():
    # Sector already holds 28mn of 30mn room → only 2mn left for this name.
    held = Position(symbol="OTHER", sector="MINING", qty=280_000, last_price=100)
    pf = Portfolio(positions=(held,), cash=0.0)
    o = generate_order(_trigger(entry=100, stop=80, target=160), equity=100_000_000,
                       tilt=COMPOUNDER, sector="MINING", portfolio=pf)
    assert o.accepted
    assert o.qty == 20_000            # floor(2mn / 100) in whole lots
    assert o.notional <= 2_000_000 + 1


def test_circuit_breaker_blocks_new_entry():
    o = generate_order(_trigger(), equity=100_000_000, tilt=COMPOUNDER, sector="CONSUMER",
                       circuit=CircuitState.HALT_NEW_ENTRIES)
    assert o.status is OrderStatus.REJECTED
    assert "circuit breaker" in o.reason.lower()


def test_invalid_trigger_is_rejected():
    o = generate_order(_trigger(valid=False), equity=100_000_000, tilt=COMPOUNDER, sector="CONSUMER")
    assert o.status is OrderStatus.REJECTED


def test_sub_lot_budget_rejected():
    # Tiny equity: 1% risk buys < 1 lot at a 20/sh risk → typed rejection, not zero fill.
    o = generate_order(_trigger(), equity=100_000, tilt=COMPOUNDER, sector="CONSUMER")
    assert o.status is OrderStatus.REJECTED
    assert o.qty == 0


def test_accepted_order_carries_board_and_tier():
    o = generate_order(_trigger(), equity=100_000_000, tilt=COMPOUNDER, sector="CONSUMER",
                       board=BoardType.MAIN, adv20=200e9)
    assert o.accepted and o.board is BoardType.MAIN
    assert o.tier.value == "LARGE"
