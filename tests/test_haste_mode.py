"""Slice-16 Haste Mode (spec §6/§8, LD-12) — Fast Mode with a wider candidate cohort.

Haste Mode is Fast Mode (LD-11) with the `ARMED@70` arming cut dropped: it auto-enters the
`WATCH ∪ ARMED` set — every name that already cleared the RULE A phase gate (Wyckoff C/D) and
the §5 veto layer, at *any* internal SMS. Everything else is identical: the same triggerless
marketable-limit entry geometry, the same §6 sizing/caps/breakers, the same §8 exit ladder.

These tests pin, in order of importance:

  * **the firewall (the crux)** — a WATCH name enters under Haste, is skipped by Fast AND by
    the standard [6] path; and a `GATE_REJECTED` / `VETOED` name is NEVER entered under Haste,
    so RULE A and every §5 veto hold by construction (the cohort widens from `res.armed` to
    `res.state in {ARMED, WATCH}`, and both rejected states live *upstream* of that split);
  * **exit unchanged** — a one-name Haste portfolio run reconciles with `runner.run_forward`
    (shared fill engine, §13), and the §6 caps still clamp the book;
  * **persistence** — the per-mode durable book survives a restart with no double-entry, and
    the Fast and Haste books never mix;
  * **arming** — Fast xor Haste, never both over the shared paper book;
  * **RULE B** — Haste trades promote ONLY the `haste_mode` lane (never `fast_mode`, never the
    trigger-based modules), the aggregate stays withheld until validated, and a Haste exit
    surfaces as the pipeline `EXITED` verdict with realized P&L and no score leak.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date as Date
from datetime import datetime, time

import pytest

from tests.builders import brow, phase_b_bars, phase_c_bars
from tests.test_portfolio_runner import _ARCHETYPES, _cfg, _seed

from currentflow.dal.models import Side
from currentflow.signals.engine import EngineState
from currentflow.signals.engine import evaluate as engine_evaluate
from currentflow.store.schema import FastModeStateRow, FastTradeRow
from currentflow.ui import fast_mode_view, pipeline_view
from currentflow.validation import runner
from currentflow.validation.fast_mode import (
    FAST_MODE_MODULE,
    HASTE_MODE_MODULE,
    MODE_FAST,
    MODE_HASTE,
    ModeConflictError,
    accrue_mode,
    run_fast_mode_step,
    set_enabled,
)
from currentflow.validation.portfolio_runner import (
    PortfolioConfig,
    _rank_candidates,
    run_portfolio_forward,
)
from currentflow.validation.promotion import ValidationLedger
from currentflow.validation.state import ModuleState

NOW = datetime(2026, 7, 14, 9, 0)


# --- fixtures ------------------------------------------------------------------------


def _seed_watch(store, symbol: str, kind: str = "win"):
    """A name that is phase C (RULE A passes), un-vetoed, but scores BELOW the arming cut on
    every day → `WATCH` throughout. Bars only, no broker accumulation: the §4 flow components
    have nothing to score, so internal SMS stays under 70 while the phase gate still passes.
    This is the cohort Haste adds and Fast refuses."""
    bars, _spring, days = _ARCHETYPES[kind](symbol)
    store.write_daily_bars(bars)
    return days


def _assert_watch_throughout(store, symbol: str, days: list[Date]) -> None:
    """Guard the fixture's premise — if the engine ever ARMs this name the firewall test
    below would pass for the wrong reason."""
    states = {
        engine_evaluate(store, symbol, runner._decision_ts(d), track="B").state
        for d in days
    }
    assert states == {EngineState.WATCH}, f"fixture drifted: {states}"


def _haste_spec(symbol: str, sector: str):
    """A Haste entry spec: fast (triggerless) geometry + the widened cohort."""
    return replace(_cfg(symbol, sector), fast_mode=True, include_watch=True)


def _haste_cfg(**kw) -> PortfolioConfig:
    return PortfolioConfig(fast_mode=True, include_watch=True, **kw)


# --- the firewall (the crux) ---------------------------------------------------------


def test_watch_name_enters_under_haste_but_not_fast_or_the_standard_path(store):
    """LD-12's whole reason for existing: the WATCH cohort. The SAME name and the SAME days
    produce a trade under Haste and nothing under Fast or the standard [6] path."""
    days = _seed_watch(store, "WCH", "win")
    _assert_watch_throughout(store, "WCH", days)
    spec = _cfg("WCH", "CONSUMER")

    # standard path (LD-3: trigger + R:R ≥ 2:1) — no entry.
    std = run_portfolio_forward(store, {"WCH": spec}, days, PortfolioConfig())
    assert std.trades == () and std.open_positions == ()

    # Fast Mode (LD-11: triggerless, but ARMED-only) — still no entry, the cut excludes it.
    fast_spec = replace(spec, fast_mode=True)
    fast = run_portfolio_forward(store, {"WCH": fast_spec}, days, PortfolioConfig(fast_mode=True))
    assert fast.trades == () and fast.open_positions == ()

    # Haste Mode (LD-12: triggerless + WATCH ∪ ARMED) — enters.
    haste = run_portfolio_forward(
        store, {"WCH": _haste_spec("WCH", "CONSUMER")}, days, _haste_cfg()
    )
    assert haste.trades or haste.open_positions, "Haste must enter the WATCH cohort"


def test_haste_never_enters_a_gate_rejected_name(store):
    """RULE A holds by construction: `GATE_REJECTED` is a distinct state UPSTREAM of the
    WATCH/ARMED split, so widening the cohort cannot reach a non-C/D name."""
    store.write_daily_bars(phase_b_bars("RANGE"))
    day = Date(2026, 7, 1)
    res = engine_evaluate(store, "RANGE", runner._decision_ts(day), track="B")
    assert res.state is EngineState.GATE_REJECTED           # fixture premise

    # neither auto-trader may reach it, nor the standard path (§13 v1.5: "either").
    for spec in (
        _haste_spec("RANGE", "CONSUMER"),                   # Haste  (WATCH ∪ ARMED)
        replace(_cfg("RANGE", "CONSUMER"), fast_mode=True),  # Fast   (ARMED only)
        _cfg("RANGE", "CONSUMER"),                           # standard [6]
    ):
        assert _rank_candidates(store, {"RANGE": spec}, {}, day) == []


def test_haste_never_enters_a_vetoed_name(store):
    """§5 holds by construction: `VETOED` is likewise upstream of the WATCH/ARMED split."""
    bdays = [Date(2026, 6, 24), Date(2026, 6, 25), Date(2026, 6, 26)]
    store.write_daily_bars(phase_c_bars("MONO"))
    store.write_broker_net([
        brow("DX", Side.BUY, 7e9, bdays[-1], symbol="MONO"),
        brow("KI", Side.BUY, 3e9, bdays[-1], symbol="MONO"),
        brow("YP", Side.SELL, 2e9, bdays[-1], symbol="MONO"),
    ])
    day = Date(2026, 7, 1)
    res = engine_evaluate(store, "MONO", runner._decision_ts(day), track="B")
    assert res.state is EngineState.VETOED                  # fixture premise

    for spec in (
        _haste_spec("MONO", "CONSUMER"),                     # Haste  (WATCH ∪ ARMED)
        replace(_cfg("MONO", "CONSUMER"), fast_mode=True),   # Fast   (ARMED only)
        _cfg("MONO", "CONSUMER"),                            # standard [6]
    ):
        assert _rank_candidates(store, {"MONO": spec}, {}, day) == []


# --- exit unchanged: shared fill engine (§13) + §6 caps ------------------------------


def test_haste_portfolio_reconciles_with_run_forward(store):
    """Haste changes the entry cohort, not the engine: a one-name Haste portfolio run
    reproduces the single-name `run_forward` exactly (same §8 exit, same fill engine)."""
    days = _seed(store, "ACC", "win")
    cfg = replace(_cfg("ACC", "CONSUMER"), fast_mode=True, include_watch=True)

    fw = runner.run_forward(store, "ACC", days, cfg)
    pf = run_portfolio_forward(store, {"ACC": cfg}, days, _haste_cfg(equity=cfg.equity))

    assert len(fw) == len(pf.trades) >= 1
    assert fw[0].net_pnl == pf.trades[0].net_pnl
    assert fw[0].exit_reason is pf.trades[0].exit_reason


def test_haste_sector_cap_still_binds(store):
    """Haste relaxes only the arming cut — the §6 30%/sector cap still clamps the book."""
    syms = ["H1", "H2", "H3", "H4"]
    days: set[Date] = set()
    for s in syms:
        days |= set(_seed(store, s, "hold"))
    specs = {s: _haste_spec(s, "ENERGY") for s in syms}     # one sector

    pf = run_portfolio_forward(store, specs, sorted(days), _haste_cfg())
    assert pf.sector_notional("ENERGY") <= 0.305 * 1_000_000_000.0


# --- the live daemon: per-mode durable book ------------------------------------------


def _step_all_days(store, symbols, days, mode=MODE_HASTE):
    for d in days:
        run_fast_mode_step(store, symbols, d, mode=mode, now=datetime.combine(d, time(23, 0)))


def test_haste_daemon_enters_a_watch_name_and_persists(store):
    """The live daemon path (not just the batch run) enters the WATCH cohort and persists it
    to the per-mode book; a re-fire of the last processed day is a durable no-op."""
    days = _seed_watch(store, "WCH", "win")
    set_enabled(store, True, mode=MODE_HASTE, now=datetime.combine(days[0], time(0, 0)))

    _step_all_days(store, ["WCH"], days)

    touched = store.read_fast_positions(mode=MODE_HASTE) or store.read_fast_trades(mode=MODE_HASTE)
    assert touched, "the Haste daemon must have entered the WATCH name"

    r = run_fast_mode_step(
        store, ["WCH"], days[-1], mode=MODE_HASTE,
        now=datetime.combine(days[-1], time(23, 0)),
    )
    assert "already processed" in r.detail                  # no double-entry on restart


def test_haste_and_fast_books_do_not_mix(store):
    """One shared paper store, two lanes: each mode's book + trades + arm state are keyed by
    mode, so a Haste position can never be read (or exited, or accrued) as a Fast one."""
    days = _seed_watch(store, "WCH", "win")
    set_enabled(store, True, mode=MODE_HASTE, now=datetime.combine(days[0], time(0, 0)))
    _step_all_days(store, ["WCH"], days)

    assert store.read_fast_positions(mode=MODE_FAST) == []
    assert store.read_fast_trades(mode=MODE_FAST) == []
    assert store.read_fast_mode_state(mode=MODE_FAST) is None
    assert store.read_fast_mode_state(mode=MODE_HASTE).enabled is True


def test_disarmed_haste_step_is_a_noop(store):
    _seed_watch(store, "WCH", "win")
    r = run_fast_mode_step(
        store, ["WCH"], Date(2026, 3, 2), mode=MODE_HASTE, now=datetime(2026, 3, 3, 9, 12)
    )
    assert r.enabled is False and r.rows_written == 0
    assert store.read_fast_positions(mode=MODE_HASTE) == []


# --- arming: Fast xor Haste over the shared book -------------------------------------


def test_arming_is_mutually_exclusive_with_fast(store):
    """One auto-trader at a time (operator decision 2026-07-14) — the two share a paper book
    and a §6 circuit budget, so arming both would double-count exposure. Fail loud."""
    set_enabled(store, True, mode=MODE_FAST, now=NOW)
    with pytest.raises(ModeConflictError):
        set_enabled(store, True, mode=MODE_HASTE, now=NOW)

    set_enabled(store, False, mode=MODE_FAST, now=NOW)     # disarm → the seat frees
    row = set_enabled(store, True, mode=MODE_HASTE, now=NOW)
    assert row.enabled is True
    with pytest.raises(ModeConflictError):                 # and now the mirror holds
        set_enabled(store, True, mode=MODE_FAST, now=NOW)


def test_disarming_is_always_allowed(store):
    """Disarming is never refused — it is a pause, not a reset, and must not deadlock."""
    set_enabled(store, True, mode=MODE_HASTE, now=NOW)
    row = set_enabled(store, False, mode=MODE_HASTE, now=NOW)
    assert row.enabled is False and row.since_date is not None   # accrued clock preserved


# --- scheduler wiring ----------------------------------------------------------------


def test_haste_feed_is_scheduled_after_eod_over_the_universe():
    from currentflow.scheduler import runner as sched
    from currentflow.scheduler.schedule import (
        FEED_EOD_INGEST,
        FEED_FAST_MODE,
        FEED_HASTE_MODE,
        FEED_SCHEDULES,
        Scope,
    )

    assert FEED_HASTE_MODE in sched._ACTIONS
    feeds = [f.feed for f in FEED_SCHEDULES]
    assert feeds.index(FEED_HASTE_MODE) > feeds.index(FEED_EOD_INGEST)   # reads fresh cache
    assert feeds.index(FEED_HASTE_MODE) > feeds.index(FEED_FAST_MODE)    # a beat after Fast
    sd = next(f for f in FEED_SCHEDULES if f.feed == FEED_HASTE_MODE)
    assert sd.scope is Scope.UNIVERSE and sd.cadence.prior_trading_day is True


def test_scheduler_haste_action_noop_when_disarmed(store):
    import asyncio

    from currentflow.scheduler.runner import OUTCOME_EMPTY, _act_haste_mode

    rows, outcome, _ = asyncio.run(
        _act_haste_mode(None, store, ["ACC"], now=datetime(2026, 3, 2, 9, 12))
    )
    assert rows == 0 and outcome == OUTCOME_EMPTY   # disarmed → no auto-trade, ever


# --- RULE B: the dedicated `haste_mode` lane -----------------------------------------


def _inject_winning_trades(store, n: int, since: Date, mode: str = MODE_HASTE):
    store.write_fast_mode_state(
        FastModeStateRow(True, since, Date(2026, 6, 30), 0.0, 1e9, 1e9), mode=mode
    )
    store.append_fast_trades([
        FastTradeRow(
            symbol=f"S{i}", entry_date=Date(2026, 3, 1), exit_date=Date(2026, 3, 2),
            as_of=datetime(2026, 3, 3), track="B", tilt_kind="NEUTRAL", qty=1000,
            entry_price=100.0, exit_price=106.0 + i, entry_fee=1000.0, exit_fee=1000.0,
            exit_reason="TARGET", stop=90.0, risk_idr=1e7,
        )
        for i in range(n)
    ], mode=mode)


def test_haste_trades_promote_only_the_haste_lane(store):
    """A different entry policy earns its own validation (RULE B honesty): Haste trades move
    the `haste_mode` lane and leave `fast_mode` AND the trigger-based modules untouched."""
    _inject_winning_trades(store, 6, since=Date(2026, 3, 1))
    led = ValidationLedger()

    rec = accrue_mode(store, led, mode=MODE_HASTE, now=NOW)

    assert rec.module == HASTE_MODE_MODULE
    assert led.state(HASTE_MODE_MODULE) is not ModuleState.OBSERVATION_ONLY
    for other in (FAST_MODE_MODULE, "sms", "ai_ranking", "daily_top"):
        assert led.state(other) is ModuleState.OBSERVATION_ONLY


def test_haste_lane_observation_only_without_forward_paper(store):
    led = ValidationLedger()
    accrue_mode(store, led, mode=MODE_HASTE, now=NOW)
    assert led.state(HASTE_MODE_MODULE) is ModuleState.OBSERVATION_ONLY


def test_haste_book_view_withholds_aggregate_until_validated(store):
    """Per-trade / realized P&L are facts (shown); the aggregate hit-rate / expectancy is the
    promotable claim, withheld (`•••`) until the `haste_mode` lane validates."""
    _inject_winning_trades(store, 6, since=Date(2026, 3, 1))
    led = ValidationLedger()

    v = fast_mode_view.build_view(store, led, now=NOW, mode=MODE_HASTE)
    assert v["module"] == HASTE_MODE_MODULE
    assert v["hit_rate_display"] == "•••" and v["expectancy_display"] == "•••"
    assert v["n_closed"] == 6
    assert v["realized_pnl"] == pytest.approx(
        sum((106.0 + i - 100.0) * 1000 - 2000.0 for i in range(6))
    )


def test_fast_view_does_not_see_haste_trades(store):
    """The two panels read disjoint books — a Haste trade must never inflate the Fast record."""
    _inject_winning_trades(store, 6, since=Date(2026, 3, 1), mode=MODE_HASTE)
    led = ValidationLedger()
    v = fast_mode_view.build_view(store, led, now=NOW, mode=MODE_FAST)
    assert v["n_closed"] == 0 and v["realized_pnl"] == 0.0


# --- pipeline EXITED verdict for a Haste exit ----------------------------------------


def test_haste_exit_surfaces_as_exited_with_realized_pnl(store):
    """A closed Haste position surfaces in the Signal Pipeline exactly like a Fast one — the
    reversed-stage `⤶` cell + realized net-of-fee P&L (a fact), with no score leak."""
    _seed(store, "ACC", "win")
    exit = {"pnl": -286910.0, "reason": "SIGNAL_DECAY", "exit_date": Date(2026, 6, 11)}
    res = engine_evaluate(store, "ACC", runner._decision_ts(Date(2026, 3, 20)), track="B")
    row = pipeline_view.build_row({
        "result": res, "name": "ACC", "price": 100.0, "chg": 1.0,
        "adv20": 2e10, "sector": "X", "exit": exit,
    })

    assert row["result"] == "EXITED"
    assert row["exit_pnl"] == -286910.0
    assert row["cells"][3]["state"] == "rev"
    assert "internal_score" not in row and "sms" not in row


def test_app_exit_lookup_unions_both_modes(store):
    """`app._fast_exits` must union both books, or a Haste exit would silently never reach
    the pipeline's EXITED verdict."""
    from currentflow.ui.app import _fast_exits

    store.append_fast_trades([
        FastTradeRow(
            symbol="HST", entry_date=Date(2026, 3, 1), exit_date=Date(2026, 3, 2),
            as_of=datetime(2026, 3, 3), track="B", tilt_kind="NEUTRAL", qty=1000,
            entry_price=100.0, exit_price=110.0, entry_fee=1000.0, exit_fee=1000.0,
            exit_reason="TARGET", stop=90.0, risk_idr=1e7,
        )
    ], mode=MODE_HASTE)

    exits = _fast_exits(store)
    assert "HST" in exits and exits["HST"]["pnl"] == pytest.approx(10.0 * 1000 - 2000.0)
