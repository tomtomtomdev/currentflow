"""Slice-8 paper-trade runner (spec §11): a real ARMED candidate runs entry → fill →
manage → exit through the ONE shared fill engine, and the backtest and forward-paper code
paths **reconcile** over identical data (the §13 acceptance invariant)."""

from __future__ import annotations

from datetime import date as Date

from tests.builders import Chart, brow

from currentflow.dal.models import InvestorType, Side
from currentflow.execution.risk import ExitReason
from currentflow.fundamentals.tilt import classify_tilt
from currentflow.validation import runner
from currentflow.validation.runner import RunConfig


def _accumulation_then_markup(symbol: str):
    """A Phase C accumulation (absorption cluster + spring) then a markup that tags the
    range resistance — one clean entry that exits at TARGET. Mirrors the strong-Phase-C
    archetype so the classifier arms, then extends past the spring with the markup."""
    ch = Chart(symbol).oscillate(30)
    ch.add(104, 108, 100, 106, 1000)
    for _ in range(6):
        ch.add(105, 107, 105, 106, 2500)          # absorption: flat close, high volume
    ch.add(104, 108, 100, 106, 1000)
    ch.add(112, 120, 110, 114, 1000)              # probe resistance
    ch.add(101, 103, 98, 102, 1200)               # spring — Phase C, last pre-entry bar
    spring_date = ch.last_date
    # markup — entry day onwards (opens at/below the 102 spring-close limit, then rises)
    ch.add(101, 105, 100, 104, 1500)              # entry day
    ch.add(106, 111, 105, 110, 1800)
    ch.add(111, 116, 110, 115, 2000)
    ch.add(116, 124, 115, 121, 2500)              # tags resistance 120 → TARGET (seen D)
    ch.add(120, 123, 119, 121, 2000)              # …exit fills at this day's open (D+1, §12)
    markup_dates = [b.date for b in ch.bars if b.date > spring_date]
    bar_dates = [b.date for b in ch.bars if b.date <= spring_date]
    return ch.bars, spring_date, markup_dates, bar_dates


def _seed(store, symbol="ACC"):
    bars, spring_date, markup_dates, bar_dates = _accumulation_then_markup(symbol)
    store.write_daily_bars(bars)
    # two persistent accumulators over the last 5 bar days up to (and incl.) the spring
    rows = []
    for d in bar_dates[-5:]:
        rows += [
            brow("DX", Side.BUY, 5e9, d, symbol=symbol, investor=InvestorType.FOREIGN, avg_price=105),
            brow("KI", Side.BUY, 5e9, d, symbol=symbol),
            brow("YP", Side.SELL, 4.5e9, d, symbol=symbol),
            brow("PD", Side.SELL, 4.5e9, d, symbol=symbol),
        ]
    store.write_broker_net(rows)
    return markup_dates


def _cfg():
    return RunConfig(
        track="B",
        tilt=classify_tilt("ACC", sector="CONSUMER", mf_rank_pct=80),  # COMPOUNDER
        sector="CONSUMER", equity=1_000_000_000.0, adv20=200e9,
    )


def test_runner_closes_one_trade_at_target(store):
    days = _seed(store)
    trades = runner.run_backtest(store, "ACC", days, _cfg())

    assert len(trades) == 1
    t = trades[0]
    assert t.exit_reason is ExitReason.TARGET
    assert t.qty % 100 == 0 and t.qty > 0
    assert t.entry_price <= 102                 # filled at/below the spring-close limit
    assert t.holding_days > 0
    # P&L is net of the full fee stack (both fills charged), and this trade won
    assert t.fee_total > 0
    assert t.net_pnl < t.gross_pnl              # fees drag the gross
    assert t.net_pnl > 0 and t.won


def test_backtest_and_forward_reconcile(store):
    days = _seed(store)
    cfg = _cfg()
    bt = runner.run_backtest(store, "ACC", days, cfg)
    fw = runner.run_forward(store, "ACC", days, cfg)

    # Two code paths, one fill engine → identical trades (spec §13 reconciliation).
    assert len(bt) == len(fw) == 1
    for a, b in zip(bt, fw):
        assert (a.entry_date, a.exit_date, a.qty, a.entry_price, a.exit_price) == \
               (b.entry_date, b.exit_date, b.qty, b.entry_price, b.exit_price)
        assert a.net_pnl == b.net_pnl
        assert a.exit_reason is b.exit_reason


def test_no_entry_before_the_spring_is_visible(store):
    """Look-ahead firewall: an entry on the first markup day cannot be pre-dated. If the
    candidate window starts before any accumulation exists, no trade is produced."""
    days = _seed(store)
    # Only offer candidate days far before the accumulation formed → nothing to arm on.
    early = [Date(2026, 1, 6), Date(2026, 1, 7)]
    assert runner.run_backtest(store, "ACC", early, _cfg()) == []
