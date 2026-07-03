"""Slice-8 validation metrics (spec §8): net-of-fee performance over a paper record,
walk-forward Sharpe for promotion, and the hard rule that IHSG is never the benchmark."""

from __future__ import annotations

from datetime import date as Date

import pytest

from currentflow.execution.risk import ExitReason
from currentflow.validation import metrics
from currentflow.validation.trade import PaperTrade


def _trade(net_move: float, *, qty=1000, entry=100.0, fee=1000.0, day=1) -> PaperTrade:
    """A synthetic closed trade whose net P&L ≈ (net_move·qty − 2·fee)."""
    return PaperTrade(
        symbol="X", track="B", tilt_kind="NEUTRAL",
        entry_date=Date(2026, 1, day), exit_date=Date(2026, 1, day + 1), qty=qty,
        entry_price=entry, exit_price=entry + net_move, entry_fee=fee, exit_fee=fee,
        exit_reason=ExitReason.TARGET, stop=entry - 5, risk_idr=5 * qty,
    )


def test_net_pnl_is_after_the_full_fee_stack():
    t = _trade(10.0, qty=1000, entry=100.0, fee=1000.0)
    assert t.gross_pnl == 10.0 * 1000
    assert t.net_pnl == 10.0 * 1000 - 2000.0        # both fills' fees subtracted
    m = metrics.compute_metrics([t], equity=1_000_000)
    assert m.net_pnl == t.net_pnl
    assert m.total_return == t.net_pnl / 1_000_000


def test_hit_rate_turnover_and_drawdown():
    trades = [_trade(10, day=1), _trade(-8, day=3), _trade(6, day=5)]
    m = metrics.compute_metrics(trades, equity=1_000_000)
    assert m.n_trades == 3
    assert m.wins == 2 and m.hit_rate == pytest.approx(2 / 3)
    # turnover = Σ entry notional / equity
    assert m.turnover == pytest.approx(3 * 100.0 * 1000 / 1_000_000)
    # the middle losing trade creates a real peak-to-trough dip
    assert m.max_drawdown < 0


def test_ihsg_benchmark_is_refused():
    t = _trade(5)
    for banned in ("IHSG", "ihsg", "JCI", "Composite", "^JKSE"):
        with pytest.raises(ValueError, match="never IHSG|forbidden"):
            metrics.compute_metrics([t], equity=1_000_000, benchmark=banned)


def test_lq45_benchmark_gives_excess_return():
    t = _trade(10, qty=1000, entry=100.0, fee=0.0)   # +1% gross, no fee
    m = metrics.compute_metrics([t], equity=1_000_000, benchmark="LQ45", benchmark_return=0.005)
    assert m.benchmark == "LQ45"
    assert m.excess_return == pytest.approx(m.total_return - 0.005)


def test_annualisation_scales_by_span():
    t = _trade(10)
    m = metrics.compute_metrics([t], equity=1_000_000, trading_days_span=126)  # ~half year
    assert m.ann_return == pytest.approx(m.total_return * (252 / 126))


def test_walk_forward_sharpe_positive_when_every_fold_wins():
    # all net-positive, but with dispersion so a Sharpe is defined per fold
    trades = [_trade(8 + (i % 3) * 4, day=i) for i in range(1, 13)]
    wf = metrics.walk_forward_sharpe(trades, folds=3)
    assert wf is not None and wf > 0


def test_walk_forward_sharpe_negative_when_a_fold_loses():
    winners = [_trade(8 + (i % 3) * 4, day=i) for i in range(1, 9)]
    losers = [_trade(-8 - (i % 3) * 4, day=i) for i in range(9, 17)]  # last fold loses
    wf = metrics.walk_forward_sharpe(winners + losers, folds=3)
    assert wf is not None and wf < 0


def test_walk_forward_needs_enough_trades():
    assert metrics.walk_forward_sharpe([_trade(10)], folds=3) is None
    assert metrics.walk_forward_sharpe([], folds=3) is None


def test_empty_record_is_honest_zero_not_a_fabricated_sharpe():
    m = metrics.compute_metrics([], equity=1_000_000)
    assert m.n_trades == 0 and m.sharpe is None and m.max_drawdown == 0.0
