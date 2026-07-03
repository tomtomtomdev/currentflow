"""End-to-end execution vertical (spec §2 steps [6]→[9]): a real ARMED candidate flows
through trigger → fundamental tilt → order gen → paper fill, and the §13 acceptance
invariant holds — every emitted order is a LIMIT with a defined stop and R:R ≥ 2:1."""

from __future__ import annotations

from datetime import date as Date
from datetime import datetime

from currentflow.dal.models import BoardType, Side
from currentflow.execution import order as order_mod
from currentflow.execution import trigger as trigger_mod
from currentflow.fundamentals.tilt import classify_tilt
from currentflow.paper.fill import FillStatus, LiquidityTier, fill_order
from currentflow.signals import engine
from currentflow.signals.engine import EngineState
from tests.builders import strong_phase_c_bars, two_buyer_rows

TS = datetime(2026, 7, 1, 9, 0)
BDAYS = [Date(2026, 6, 24), Date(2026, 6, 25), Date(2026, 6, 26)]


def test_armed_candidate_flows_to_a_filled_limit_order(store):
    store.write_daily_bars(strong_phase_c_bars("STRONG"))
    store.write_broker_net(two_buyer_rows("STRONG", BDAYS))

    # [3]-[5] engine → ARMED
    res = engine.evaluate(store, "STRONG", TS, track="B")
    assert res.state is EngineState.ARMED

    # [6] technical trigger — Phase C spring, R:R ≥ 2:1
    sig = trigger_mod.analyze(store, "STRONG", TS, res.phase)
    assert sig.valid and sig.rr >= 2.0

    # [7] fundamental tilt (top-tercile MF rank → compounder)
    tilt = classify_tilt("STRONG", sector="CONSUMER", mf_rank_pct=80)

    # [8] order gen — sized to 1% risk, capped
    order = order_mod.generate_order(
        sig, equity=1_000_000_000, tilt=tilt, sector="CONSUMER",
        board=BoardType.MAIN, adv20=200e9,
    )
    assert order.accepted
    # §13 acceptance invariant:
    assert order.order_type == "LIMIT"
    assert order.side is Side.BUY
    assert order.stop is not None
    assert order.rr >= 2.0
    assert order.qty % 100 == 0 and order.qty > 0
    assert order.notional <= 1_000_000_000 * 0.10 + 1   # ≤ 10% name cap

    # [9] paper fill at the next open (opens at/below the limit → fills)
    fill = fill_order(
        symbol="STRONG", side=Side.BUY, limit_price=order.limit_price, qty=order.qty,
        order_date=Date(2026, 7, 1), next_open=order.limit_price - 1,
        prev_close=order.limit_price, board=order.board, tier=order.tier,
    )
    assert fill.status is FillStatus.FILLED
    assert fill.fill_price is not None and fill.cash_flow < 0   # cash out on a buy
    assert fill.fees.total > 0
    assert fill.settlement_date > fill.fill_date               # T+2


def test_full_pipeline_respects_circuit_breaker(store):
    store.write_daily_bars(strong_phase_c_bars("STRONG"))
    store.write_broker_net(two_buyer_rows("STRONG", BDAYS))
    res = engine.evaluate(store, "STRONG", TS, track="B")
    sig = trigger_mod.analyze(store, "STRONG", TS, res.phase)
    tilt = classify_tilt("STRONG", sector="CONSUMER", mf_rank_pct=80)

    from currentflow.signals.risk_monitor import CircuitState
    order = order_mod.generate_order(
        sig, equity=1_000_000_000, tilt=tilt, sector="CONSUMER",
        circuit=CircuitState.PAUSE_SYSTEM,
    )
    assert not order.accepted   # §6: no new entries while the system is paused
