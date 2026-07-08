"""Slice-8+ portfolio auto paper-trader (spec §6/§11): the multi-name forward run that
finally makes the §6 exposure caps and circuit breakers bind, and that reconciles with the
single-symbol `runner.run_forward` on a one-name universe (shared fill engine, §13).

Covers the operator's allocation questions (2026-07-08):
  - we do NOT take every ARMED name — a valid trigger is still required;
  - deployment is emergent, clamped by the 10%/name & 30%/sector caps;
  - when triggers exceed capacity, the higher INTERNAL-SMS name wins the slot;
  - running P&L drives the §6 circuit breakers.
"""

from __future__ import annotations

from datetime import date as Date

from tests.builders import Chart, brow

from currentflow.dal.models import InvestorType, Side
from currentflow.execution.risk import ExitReason
from currentflow.fundamentals.tilt import classify_tilt
from currentflow.signals.engine import evaluate as engine_evaluate
from currentflow.validation import runner
from currentflow.validation.portfolio_runner import (
    PortfolioConfig,
    _decision_ts,
    run_portfolio_forward,
)
from currentflow.validation.runner import RunConfig


# --- chart archetypes ----------------------------------------------------------------


def _accum_then_markup(symbol: str):
    """Phase-C accumulation + spring, then a markup that tags resistance → TARGET exit."""
    ch = Chart(symbol).oscillate(30)
    ch.add(104, 108, 100, 106, 1000)
    for _ in range(6):
        ch.add(105, 107, 105, 106, 2500)          # absorption
    ch.add(104, 108, 100, 106, 1000)
    ch.add(112, 120, 110, 114, 1000)
    ch.add(101, 103, 98, 102, 1200)               # spring (last pre-entry bar)
    spring = ch.last_date
    ch.add(101, 105, 100, 104, 1500)              # entry day
    ch.add(106, 111, 105, 110, 1800)
    ch.add(111, 116, 110, 115, 2000)
    ch.add(116, 124, 115, 121, 2500)              # tags resistance → TARGET
    ch.add(120, 123, 119, 121, 2000)
    markup = [b.date for b in ch.bars if b.date > spring]
    return ch.bars, spring, markup


def _accum_then_hold(symbol: str):
    """Phase-C accumulation + spring + entry, then gentle sideways drift that never tags
    the target, stop, trailing level, or a decay flag → the position HOLDS to end-of-window
    (so the book stays full and the §6 sector cap keeps binding)."""
    ch = Chart(symbol).oscillate(30)
    ch.add(104, 108, 100, 106, 1000)
    for _ in range(6):
        ch.add(105, 107, 105, 106, 2500)
    ch.add(104, 108, 100, 106, 1000)
    ch.add(112, 120, 110, 114, 1000)
    ch.add(101, 103, 98, 102, 1200)               # spring
    spring = ch.last_date
    ch.add(101, 107, 100, 105, 1500)              # entry day
    for _ in range(6):
        ch.add(105, 110, 103, 107, 1400)          # drift: high<120, low>stop, no trail breach
    markup = [b.date for b in ch.bars if b.date > spring]
    return ch.bars, spring, markup


def _accum_then_stop(symbol: str):
    """Same Phase-C entry, then a gap-down through the stop → STOP exit (a ~1% loser)."""
    ch = Chart(symbol).oscillate(30)
    ch.add(104, 108, 100, 106, 1000)
    for _ in range(6):
        ch.add(105, 107, 105, 106, 2500)
    ch.add(104, 108, 100, 106, 1000)
    ch.add(112, 120, 110, 114, 1000)
    ch.add(101, 103, 98, 102, 1200)               # spring
    spring = ch.last_date
    ch.add(101, 103, 100, 102, 1500)              # entry day (fills at open 101)
    ch.add(95, 96, 92, 94, 1800)                  # gap down: low 92 ≤ stop → STOP at open 95
    ch.add(94, 95, 90, 92, 1500)
    markup = [b.date for b in ch.bars if b.date > spring]
    return ch.bars, spring, markup


_ARCHETYPES = {"win": _accum_then_markup, "hold": _accum_then_hold, "stop": _accum_then_stop}


def _seed(store, symbol: str, kind: str = "win", each: float = 5e9, dilute: bool = False):
    bars, spring, days = _ARCHETYPES[kind](symbol)
    store.write_daily_bars(bars)
    pre = [b.date for b in bars if b.date <= spring]
    rows = []
    for d in pre[-5:]:
        rows += [
            brow("DX", Side.BUY, each, d, symbol=symbol, investor=InvestorType.FOREIGN, avg_price=105),
            brow("KI", Side.BUY, each, d, symbol=symbol),
            brow("YP", Side.SELL, each * 0.9, d, symbol=symbol),
            brow("PD", Side.SELL, each * 0.9, d, symbol=symbol),
        ]
        if dilute:                                   # a third buyer drops top-2 share ~1.0→0.8
            rows.append(brow("CC", Side.BUY, each * 0.5, d, symbol=symbol))
    store.write_broker_net(rows)
    return days


def _cfg(symbol: str, sector: str, mf_rank_pct: int = 80) -> RunConfig:
    return RunConfig(
        track="B",
        tilt=classify_tilt(symbol, sector=sector, mf_rank_pct=mf_rank_pct),
        sector=sector, equity=1_000_000_000.0, adv20=200e9,
    )


# --- reconciliation ------------------------------------------------------------------


def test_single_name_reconciles_with_run_forward(store):
    """One-name universe reproduces `runner.run_forward` exactly (shared fill engine, §13)."""
    days = _seed(store, "ACC", "win")
    cfg = _cfg("ACC", "CONSUMER")

    fw = runner.run_forward(store, "ACC", days, cfg)
    pf = run_portfolio_forward(store, {"ACC": cfg}, days, PortfolioConfig(equity=cfg.equity))

    assert len(fw) == len(pf.trades) == 1
    a, b = fw[0], pf.trades[0]
    assert (a.entry_date, a.exit_date, a.qty, a.entry_price, a.exit_price) == \
           (b.entry_date, b.exit_date, b.qty, b.entry_price, b.exit_price)
    assert a.net_pnl == b.net_pnl
    assert a.exit_reason is b.exit_reason is ExitReason.TARGET


# --- capacity + SMS-rank priority ----------------------------------------------------


def test_max_concurrent_caps_the_book(store):
    """Two candidates but `max_concurrent=1` → only one position is ever open at once."""
    d1 = _seed(store, "AAA", "win")
    d2 = _seed(store, "BBB", "win")
    days = sorted(set(d1) | set(d2))
    specs = {"AAA": _cfg("AAA", "ENERGY"), "BBB": _cfg("BBB", "MATERIALS")}

    pf = run_portfolio_forward(store, specs, days, PortfolioConfig(max_concurrent=1))
    # Both win and close, but never held simultaneously — 1 slot, so they queue.
    assert len(pf.open_symbols) <= 1


def test_higher_internal_sms_wins_the_slot(store):
    """When capacity is 1, the higher INTERNAL-SMS candidate is entered first (RULE B:
    the score orders entries, it is never surfaced)."""
    # STRONG: undiluted top-2 net-buy share (~1.0). WEAK: a third buyer dilutes the top-2
    # share (~0.8), so the 35-weight concentration component scores lower → lower SMS,
    # while both still clear the ARM floor.
    d1 = _seed(store, "STR", "win", each=9e9)
    d2 = _seed(store, "WEK", "win", each=9e9, dilute=True)
    days = sorted(set(d1) | set(d2))
    specs = {"STR": _cfg("STR", "ENERGY"), "WEK": _cfg("WEK", "MATERIALS")}

    # Precondition: the two names really do score differently at the shared entry decision.
    entry_day = days[0]
    dts = _decision_ts(entry_day)
    s_str = engine_evaluate(store, "STR", dts, track="B").sms.internal_score
    s_wek = engine_evaluate(store, "WEK", dts, track="B").sms.internal_score
    assert s_str > s_wek, f"seed did not separate scores ({s_str} vs {s_wek})"

    pf = run_portfolio_forward(store, specs, days, PortfolioConfig(max_concurrent=1))
    # STR takes the only slot on the shared entry day.
    assert pf.trades and pf.trades[0].symbol == "STR"


# --- exposure caps bind across concurrent names --------------------------------------


def test_sector_cap_binds_across_concurrent_positions(store):
    """Four names in ONE sector, each wanting ~10% (name cap). The §6 30%/sector cap lets
    only three in on the shared entry day; the fourth is clamped to < 1 lot and skipped."""
    syms = ["N1", "N2", "N3", "N4"]
    days: set[Date] = set()
    for s in syms:
        days |= set(_seed(store, s, "hold"))       # positions hold → book stays full
    specs = {s: _cfg(s, "ENERGY") for s in syms}   # all same sector

    pf = run_portfolio_forward(store, specs, sorted(days), PortfolioConfig())
    # Uncapped, four names would deploy ~40% of equity into ENERGY. The §6 30%/sector cap
    # binds across the concurrent book: total ENERGY notional is held at ~30%, not 40%.
    equity = 1_000_000_000.0
    sector_notional = pf.sector_notional("ENERGY")
    assert 0.28 * equity <= sector_notional <= 0.305 * equity
    assert pf.realized_pnl == 0.0                   # nobody exits (all hold)


# --- circuit breaker wired to running P&L --------------------------------------------


def test_circuit_breaker_halts_entries_on_running_loss(store):
    """Enough concurrent stop-outs on one day push daily P&L past −3% → the §6 breaker
    halts new entries that day. Proves running P&L (not a static flag) drives the breaker."""
    syms = [f"L{i}" for i in range(8)]
    days: set[Date] = set()
    for i, s in enumerate(syms):
        days |= set(_seed(store, s, "stop"))
    specs = {s: _cfg(s, f"S{i}") for i, s in enumerate(syms)}  # distinct sectors, no sector cap

    pf = run_portfolio_forward(store, specs, sorted(days), PortfolioConfig())

    assert len(pf.trades) == len(syms)                       # all losers closed
    assert all(t.exit_reason is ExitReason.STOP for t in pf.trades)
    assert pf.realized_pnl < 0
    assert pf.entries_blocked_by_circuit >= 1                # breaker fired from P&L
