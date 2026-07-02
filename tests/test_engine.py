"""Decision engine (§2 steps [3]→[5] + ARMED) — the state machine end-to-end through
the store, and its look-ahead safety. RULE A: the phase gate precedes scoring; RULE B:
ARMED is a state, never a displayed number.
"""

from __future__ import annotations

from datetime import date as Date
from datetime import datetime

from builders import (
    brow,
    phase_b_bars,
    phase_c_bars,
    strong_phase_c_bars,
    two_buyer_rows,
)

from currentflow.dal.models import Side
from currentflow.signals import engine
from currentflow.signals.engine import EngineState
from currentflow.signals.veto import VetoReason

TS = datetime(2026, 7, 1, 9, 0)
BDAYS = [Date(2026, 6, 24), Date(2026, 6, 25), Date(2026, 6, 26)]


def _run(store, symbol, track="B", **kw):
    return engine.evaluate(store, symbol, TS, track=track, **kw)


# --- the four states -----------------------------------------------------------------


def test_armed_when_phase_cd_no_veto_and_score_over_threshold(store):
    store.write_daily_bars(strong_phase_c_bars("STRONG"))
    store.write_broker_net(two_buyer_rows("STRONG", BDAYS))
    res = _run(store, "STRONG")
    assert res.phase.tradeable is True
    assert res.veto.rejected is False
    assert res.sms.internal_score >= 70
    assert res.state is EngineState.ARMED
    assert res.armed is True


def test_gate_rejects_non_cd_before_scoring(store):
    # A perfect flow behind a Phase B (non-tradeable) chart is still rejected (RULE A).
    store.write_daily_bars(phase_b_bars("RANGE"))
    store.write_broker_net(two_buyer_rows("RANGE", BDAYS))
    res = _run(store, "RANGE")
    assert res.phase.tradeable is False
    assert res.state is EngineState.GATE_REJECTED
    assert res.armed is False


def test_veto_blocks_armed_even_in_phase_c(store):
    store.write_daily_bars(phase_c_bars("MONO"))
    store.write_broker_net([
        brow("DX", Side.BUY, 7e9, BDAYS[-1], symbol="MONO"),
        brow("KI", Side.BUY, 3e9, BDAYS[-1], symbol="MONO"),
        brow("YP", Side.SELL, 2e9, BDAYS[-1], symbol="MONO"),
    ])
    res = _run(store, "MONO")
    assert res.phase.tradeable is True
    assert VetoReason.SINGLE_BANDAR_MONOPOLY in res.veto.reasons
    assert res.state is EngineState.VETOED


def test_watch_when_phase_c_clean_but_score_below_threshold(store):
    # dispersed buyers, only one day (no persistence) → clean but weak signal
    store.write_daily_bars(phase_c_bars("WEAK"))
    store.write_broker_net([
        brow("DX", Side.BUY, 0.5e9, BDAYS[-1], symbol="WEAK"),
        brow("KI", Side.BUY, 0.4e9, BDAYS[-1], symbol="WEAK"),
    ])
    res = _run(store, "WEAK")
    assert res.phase.tradeable is True
    assert res.veto.rejected is False
    assert res.sms.internal_score < 70
    assert res.state is EngineState.WATCH


# --- look-ahead safety ---------------------------------------------------------------


def test_armed_only_after_the_spring_publishes(store):
    bars = strong_phase_c_bars("STRONG")
    store.write_daily_bars(bars)
    store.write_broker_net(two_buyer_rows("STRONG", BDAYS))
    spring_day = bars[-1].date
    # decision the morning of the spring day — spring not yet knowable → not ARMED
    early = engine.evaluate(
        store, "STRONG", datetime.combine(spring_day, datetime.min.time()), track="B"
    )
    assert early.state is not EngineState.ARMED
    assert early.phase.tradeable is False


# --- rebalance down-weight can drop an otherwise-armed name to WATCH -----------------


def test_rebalance_downweight_can_disarm(store):
    store.write_daily_bars(strong_phase_c_bars("BETA"))
    store.write_broker_net(two_buyer_rows("BETA", BDAYS))
    full = _run(store, "BETA", rebalance_multiplier=1.0)
    down = _run(store, "BETA", rebalance_multiplier=0.7)
    assert full.state is EngineState.ARMED
    assert down.sms.internal_score < full.sms.internal_score
    assert down.state is EngineState.WATCH   # pure-beta move no longer clears the bar
