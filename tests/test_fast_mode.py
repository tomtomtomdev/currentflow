"""Slice-15 Fast Mode (spec §6/§8, LD-11) — the operator-armed auto paper-trader.

Fast Mode buys every ARMED name AT ONCE (no Spring/LPS trigger, no R:R gate — the LD-11
relaxation of LD-3) and manages each with the SAME §8 exit. These tests pin: the fast entry
geometry, the "R:R < 2:1 still enters" contrast with the standard path, exit reconciliation
with the shared fill engine (§13), the durable book across daemon steps, scheduler wiring,
the dedicated `fast_mode` validation lane (RULE B isolation), and the pipeline EXITED verdict.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date as Date
from datetime import datetime, time

import pytest

from tests.builders import Chart
from tests.test_portfolio_runner import _cfg, _seed

from currentflow import config
from currentflow.execution import trigger as trig
from currentflow.signals.engine import evaluate as engine_evaluate
from currentflow.signals.phase import PhaseClassification, TradingRange, WyckoffPhase
from currentflow.store.schema import FastModeStateRow, FastTradeRow
from currentflow.ui import fast_mode_view, pipeline_view
from currentflow.validation import runner
from currentflow.validation.fast_mode import (
    FAST_MODE_MODULE,
    accrue_fast_mode,
    run_fast_mode_step,
    set_enabled,
)
from currentflow.validation.portfolio_runner import PortfolioConfig, run_portfolio_forward
from currentflow.validation.promotion import ValidationLedger
from currentflow.validation.state import ModuleState


# --- fast entry geometry (unit) ------------------------------------------------------


def _phase(phase: WyckoffPhase, support: float, resistance: float) -> PhaseClassification:
    rng = TradingRange(
        support=support, resistance=resistance,
        start=Date(2026, 1, 5), end=Date(2026, 2, 1), avg_volume=1000.0,
    )
    return PhaseClassification(
        symbol="X", decision_ts=datetime(2026, 2, 2, 9, 15), phase=phase,
        tradeable=phase in (WyckoffPhase.C, WyckoffPhase.D), trading_range=rng,
        events=(), reason="test", bars_used=30,
    )


def _bars_last_close(close: float):
    ch = Chart("X").oscillate(20)
    ch.add(close - 1, close + 1, close - 2, close, 1000)
    return ch.bars


def test_fast_entry_needs_no_trigger_and_hangs_stop_on_range():
    dts = datetime(2026, 2, 2, 9, 15)
    bars = _bars_last_close(108.0)
    sig = trig.fast_detect("X", _phase(WyckoffPhase.D, 100.0, 110.0), bars, dts)

    assert sig.valid and sig.kind is trig.TriggerKind.FAST_ARMED
    assert sig.entry_limit == pytest.approx(108.0 * (1 + config.FAST_MODE_LIMIT_PREMIUM))
    assert sig.stop == pytest.approx(100.0 * (1 - config.STOP_BUFFER))  # range support − buffer
    # Phase D → measured move = resistance + span
    assert sig.target == pytest.approx(110.0 + config.TARGET_MEASURED_MOVE_MULT * 10.0)

    # the STANDARD path skips the same chart — there is no locatable Spring/LPS event.
    std = trig.detect("X", _phase(WyckoffPhase.D, 100.0, 110.0), bars, dts)
    assert not std.valid


def test_fast_entry_phase_c_targets_resistance():
    dts = datetime(2026, 2, 2, 9, 15)
    sig = trig.fast_detect("X", _phase(WyckoffPhase.C, 100.0, 120.0), _bars_last_close(105.0), dts)
    assert sig.valid and sig.target == pytest.approx(120.0)  # AR high, no measured move


def test_fast_entry_ignores_the_rr_gate():
    """An ARMED name whose R:R < 2:1 STILL enters in Fast Mode (LD-11) — the standard path
    would skip it. Last close near resistance makes the reward small vs the risk."""
    dts = datetime(2026, 2, 2, 9, 15)
    sig = trig.fast_detect("X", _phase(WyckoffPhase.C, 100.0, 110.0), _bars_last_close(108.0), dts)
    assert sig.valid and sig.rr is not None and sig.rr < config.RR_MIN


def test_fast_entry_skips_incoherent_geometry():
    dts = datetime(2026, 2, 2, 9, 15)
    # support above the entry → stop ≥ entry → no coherent invalidation → skip (missing ≠ invented)
    sig = trig.fast_detect("X", _phase(WyckoffPhase.C, 200.0, 210.0), _bars_last_close(108.0), dts)
    assert not sig.valid


def test_fast_entry_skips_non_tradeable_phase():
    dts = datetime(2026, 2, 2, 9, 15)
    sig = trig.fast_detect("X", _phase(WyckoffPhase.B, 100.0, 110.0), _bars_last_close(105.0), dts)
    assert not sig.valid


# --- reconciliation: same exit, shared fill engine (§13) -----------------------------


def test_fast_portfolio_reconciles_with_fast_run_forward(store):
    """Fast Mode changes the entry, not the engine: a one-name fast portfolio run reproduces
    the fast single-name `run_forward` exactly (shared fill engine + same §8 exit)."""
    days = _seed(store, "ACC", "win")
    cfg = replace(_cfg("ACC", "CONSUMER"), fast_mode=True)

    fw = runner.run_forward(store, "ACC", days, cfg)
    pf = run_portfolio_forward(
        store, {"ACC": cfg}, days, PortfolioConfig(equity=cfg.equity, fast_mode=True)
    )

    assert len(fw) == len(pf.trades) >= 1
    assert fw[0].net_pnl == pf.trades[0].net_pnl
    assert fw[0].exit_reason is pf.trades[0].exit_reason


# --- the live daemon: durable book across steps --------------------------------------


def _step_all_days(store, symbols, days):
    for d in days:
        run_fast_mode_step(store, symbols, d, now=datetime.combine(d, time(23, 0)))


def test_daemon_opens_then_closes_and_persists(store):
    """Arming + stepping day-by-day (each step reloads the book from the store) opens a
    position mid-window and closes it by the end — the durable-book path, no double-entry."""
    days = _seed(store, "ACC", "win")
    set_enabled(store, True, now=datetime.combine(days[0], time(0, 0)))

    _step_all_days(store, ["ACC"], days)

    closed = store.read_fast_trades()
    assert len(closed) == 1
    t = closed[0]
    assert t.exit_reason == "TARGET"
    net = (t.exit_price - t.entry_price) * t.qty - (t.entry_fee + t.exit_fee)
    assert net > 0                                   # a winner (markup tagged resistance)
    assert store.read_fast_positions() == []         # flat after the close
    # a re-fire of the last processed day is a no-op (durable last_run_day guard)
    r = run_fast_mode_step(store, ["ACC"], days[-1], now=datetime.combine(days[-1], time(23, 0)))
    assert "already processed" in r.detail


def test_daemon_holds_position_across_steps(store):
    """A position that never exits stays open in the persisted book across steps (survives
    the reload each step makes) — the restart-safe book."""
    days = _seed(store, "HLD", "hold")
    set_enabled(store, True, now=datetime.combine(days[0], time(0, 0)))
    _step_all_days(store, ["HLD"], days)
    assert [p.symbol for p in store.read_fast_positions()] == ["HLD"]
    assert store.read_fast_trades() == []            # held to end → no closed trade


def test_disarmed_step_is_a_noop(store):
    _seed(store, "ACC", "win")
    r = run_fast_mode_step(store, ["ACC"], Date(2026, 3, 2), now=datetime(2026, 3, 3, 9, 10))
    assert r.enabled is False and r.rows_written == 0
    assert store.read_fast_positions() == [] and store.read_fast_trades() == []


# --- §6 caps still bind under Fast Mode ----------------------------------------------


def test_fast_mode_sector_cap_still_binds(store):
    """Fast Mode relaxes only the entry rule — the §6 30%/sector cap still clamps the book."""
    syms = ["F1", "F2", "F3", "F4"]
    days: set[Date] = set()
    for s in syms:
        days |= set(_seed(store, s, "hold"))
    specs = {s: replace(_cfg(s, "ENERGY"), fast_mode=True) for s in syms}  # one sector

    pf = run_portfolio_forward(store, specs, sorted(days), PortfolioConfig(fast_mode=True))
    equity = 1_000_000_000.0
    assert pf.sector_notional("ENERGY") <= 0.305 * equity   # 30%/sector cap holds


# --- scheduler wiring ----------------------------------------------------------------


def test_fast_feed_is_scheduled_after_eod_over_the_universe():
    from currentflow.scheduler import runner as sched
    from currentflow.scheduler.schedule import (
        FEED_EOD_INGEST,
        FEED_FAST_MODE,
        FEED_SCHEDULES,
        Scope,
    )

    assert FEED_FAST_MODE in sched._ACTIONS
    feeds = [f.feed for f in FEED_SCHEDULES]
    assert feeds.index(FEED_FAST_MODE) > feeds.index(FEED_EOD_INGEST)  # reads fresh cache
    sd = next(f for f in FEED_SCHEDULES if f.feed == FEED_FAST_MODE)
    assert sd.scope is Scope.UNIVERSE and sd.cadence.prior_trading_day is True


def test_scheduler_fast_action_noop_when_disarmed(store):
    import asyncio

    from currentflow.scheduler.runner import OUTCOME_EMPTY, _act_fast_mode

    rows, outcome, _ = asyncio.run(
        _act_fast_mode(None, store, ["ACC"], now=datetime(2026, 3, 2, 9, 10))
    )
    assert rows == 0 and outcome == OUTCOME_EMPTY   # disarmed → no auto-trade, ever


# --- RULE B: the dedicated `fast_mode` lane, isolated from the trigger modules --------


def _inject_winning_trades(store, n: int, since: Date):
    store.write_fast_mode_state(
        FastModeStateRow(True, since, Date(2026, 6, 30), 0.0, 1e9, 1e9)
    )
    rows = [
        FastTradeRow(
            symbol=f"S{i}", entry_date=Date(2026, 3, 1), exit_date=Date(2026, 3, 2),
            as_of=datetime(2026, 3, 3), track="B", tilt_kind="NEUTRAL", qty=1000,
            entry_price=100.0, exit_price=106.0 + i, entry_fee=1000.0, exit_fee=1000.0,
            exit_reason="TARGET", stop=90.0, risk_idr=1e7,
        )
        for i in range(n)
    ]
    store.append_fast_trades(rows)


def test_fast_trades_promote_only_the_fast_lane(store):
    """Accruing Fast-Mode trades advances the `fast_mode` lane past OBSERVATION_ONLY but
    leaves the trigger-based modules untouched (RULE B — each claim earns its own validation)."""
    _inject_winning_trades(store, 6, since=Date(2026, 3, 1))
    led = ValidationLedger()

    rec = accrue_fast_mode(store, led, now=datetime(2026, 7, 14, 9, 0))

    assert rec.module == FAST_MODE_MODULE
    assert led.state("fast_mode") is not ModuleState.OBSERVATION_ONLY   # months>0 → ≥ VALIDATING
    for other in ("sms", "ai_ranking", "daily_top"):
        assert led.state(other) is ModuleState.OBSERVATION_ONLY


def test_fast_lane_observation_only_without_forward_paper(store):
    led = ValidationLedger()
    accrue_fast_mode(store, led, now=datetime(2026, 7, 14, 9, 0))
    assert led.state("fast_mode") is ModuleState.OBSERVATION_ONLY   # no trades, no months


# --- pipeline EXITED verdict ---------------------------------------------------------


def _candidate_with_exit(store, symbol, exit):
    res = engine_evaluate(store, symbol, runner._decision_ts(Date(2026, 3, 20)), track="B")
    return {
        "result": res, "name": symbol, "price": 100.0, "chg": 1.0,
        "adv20": 2e10, "sector": "X", "exit": exit,
    }


def test_pipeline_exited_verdict_shows_realized_pnl(store):
    _seed(store, "ACC", "win")
    exit = {"pnl": -286910.0, "reason": "SIGNAL_DECAY", "exit_date": Date(2026, 6, 11)}
    row = pipeline_view.build_row(_candidate_with_exit(store, "ACC", exit))

    assert row["result"] == "EXITED"
    assert row["exit_pnl"] == -286910.0
    assert row["cells"][3]["state"] == "rev"           # the reversed-stage ⤶ cell
    # RULE B: no composite SMS number leaks into the row
    assert "internal_score" not in row and "sms" not in row


def test_pipeline_without_exit_is_a_normal_verdict(store):
    _seed(store, "ACC", "win")
    row = pipeline_view.build_row(_candidate_with_exit(store, "ACC", None))
    assert row["result"] in ("ARMED", "WATCH", "REJECTED")
    assert row["exit_pnl"] is None


# --- book view-model (RULE B) --------------------------------------------------------


def test_book_view_withholds_aggregate_until_validated(store):
    """Per-trade / realized P&L are facts (shown); the aggregate hit-rate / expectancy is a
    claim, withheld (`•••`) until the fast_mode lane validates."""
    _inject_winning_trades(store, 6, since=Date(2026, 3, 1))
    led = ValidationLedger()

    # pre-accrual: OBSERVATION_ONLY → aggregate withheld, realized P&L still shown (fact)
    v = fast_mode_view.build_view(store, led, now=datetime(2026, 7, 14, 9, 0))
    assert v["hit_rate_display"] == "•••" and v["expectancy_display"] == "•••"
    assert v["realized_pnl"] == pytest.approx(sum(
        (106.0 + i - 100.0) * 1000 - 2000.0 for i in range(6)
    ))
    assert v["n_closed"] == 6

    # after accrual promotes the lane, the aggregate number is revealed for THIS module only
    accrue_fast_mode(store, led, now=datetime(2026, 7, 14, 9, 0))
    if led.state("fast_mode") is ModuleState.VALIDATED:
        v2 = fast_mode_view.build_view(store, led, now=datetime(2026, 7, 14, 9, 0))
        assert v2["hit_rate_display"] != "•••" and "%" in v2["hit_rate_display"]
