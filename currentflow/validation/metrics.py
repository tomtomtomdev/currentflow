"""Performance metrics over a paper track record (spec §8) — the validation numbers.

The §8 metrics set, computed over a list of `PaperTrade` (every P&L already net of the
full fee stack, §12):

    - total / annualised return       (net of fees — "the only number that counts")
    - Sharpe                          (risk-adjusted mean trade return)
    - max drawdown                    (peak-to-trough of the net-P&L equity curve)
    - hit rate                        (winning trades / total)
    - turnover                        (gross notional traded / equity — fees punish churn)
    - excess vs benchmark             (Track A → LQ45, Track B → sector — NEVER IHSG, §8)

`walk_forward_sharpe` splits the record into sequential out-of-sample folds and returns
the **worst** fold's Sharpe — the promotion engine (`validation.promotion`) requires this
to be positive across every fold before it will earn a module its number (RULE B).

Benchmark discipline (§8, LD): IHSG is refused as a headline benchmark — passing it raises,
so a report can never accidentally be benchmarked to the composite index.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass

from currentflow.validation.trade import PaperTrade

# §8: never IHSG (nor its aliases) as the headline benchmark.
FORBIDDEN_BENCHMARKS = frozenset({"IHSG", "JCI", "COMPOSITE", "IDX COMPOSITE", "^JKSE"})

TRADING_DAYS_PER_YEAR = 252


@dataclass(frozen=True, slots=True)
class PerfMetrics:
    n_trades: int
    wins: int
    hit_rate: float
    net_pnl: float
    total_return: float
    ann_return: float | None
    sharpe: float | None
    max_drawdown: float
    turnover: float
    benchmark: str | None
    benchmark_return: float | None
    excess_return: float | None


def _sharpe(returns: list[float]) -> float | None:
    """Risk-adjusted mean per-trade return. `None` with < 2 trades or zero dispersion —
    a Sharpe is not defined there (missing ≠ zero)."""
    if len(returns) < 2:
        return None
    sd = statistics.stdev(returns)
    if sd == 0:
        return None
    return statistics.fmean(returns) / sd


def _max_drawdown(trades: list[PaperTrade], equity: float) -> float:
    """Deepest peak-to-trough of the running net-P&L equity curve, as a fraction of the
    starting equity. 0.0 if the curve never draws down."""
    balance = equity
    peak = equity
    worst = 0.0
    for t in trades:
        balance += t.net_pnl
        peak = max(peak, balance)
        dd = (balance - peak) / peak if peak else 0.0
        worst = min(worst, dd)
    return worst


def compute_metrics(
    trades: list[PaperTrade],
    *,
    equity: float,
    benchmark: str | None = None,
    benchmark_return: float | None = None,
    trading_days_span: int | None = None,
) -> PerfMetrics:
    """The §8 metric set over `trades`. Returns are net of the full fee stack.

    `benchmark` is the headline bar to beat (Track A → LQ45, Track B → sector). Passing
    IHSG (or an alias) raises — the spec forbids benchmarking to the composite (§8).
    `trading_days_span` (calendar span of the record in trading days) enables annualisation.
    """
    if benchmark is not None and benchmark.strip().upper() in FORBIDDEN_BENCHMARKS:
        raise ValueError(
            f"benchmark {benchmark!r} is forbidden (§8): never IHSG — use LQ45 (Track A) "
            "or the relevant sector index (Track B)"
        )

    n = len(trades)
    if n == 0:
        return PerfMetrics(
            n_trades=0, wins=0, hit_rate=0.0, net_pnl=0.0, total_return=0.0,
            ann_return=None, sharpe=None, max_drawdown=0.0, turnover=0.0,
            benchmark=benchmark, benchmark_return=benchmark_return, excess_return=None,
        )

    net_pnl = sum(t.net_pnl for t in trades)
    total_return = net_pnl / equity if equity else 0.0
    wins = sum(1 for t in trades if t.won)
    returns = [t.net_return for t in trades]
    turnover = sum(t.entry_notional for t in trades) / equity if equity else 0.0

    ann_return: float | None = None
    if trading_days_span and trading_days_span > 0:
        ann_return = total_return * (TRADING_DAYS_PER_YEAR / trading_days_span)

    excess = None if benchmark_return is None else total_return - benchmark_return

    return PerfMetrics(
        n_trades=n, wins=wins, hit_rate=wins / n, net_pnl=net_pnl,
        total_return=total_return, ann_return=ann_return, sharpe=_sharpe(returns),
        max_drawdown=_max_drawdown(trades, equity), turnover=turnover,
        benchmark=benchmark, benchmark_return=benchmark_return, excess_return=excess,
    )


def walk_forward_sharpe(trades: list[PaperTrade], *, folds: int = 3) -> float | None:
    """Worst per-fold Sharpe over `folds` sequential out-of-sample slices (§8 walk-forward).

    The record is cut into `folds` contiguous slices in trade order; each slice's Sharpe is
    computed and the **minimum** returned — so a "positive walk-forward Sharpe" means every
    fold was risk-adjusted-positive, not just the aggregate. `None` when there are too few
    trades to form folds with ≥ 2 trades each (can't honestly validate — missing ≠ zero)."""
    if folds < 1 or len(trades) < 2 * folds:
        return None
    size = len(trades) // folds
    fold_sharpes: list[float] = []
    for k in range(folds):
        lo = k * size
        hi = len(trades) if k == folds - 1 else (k + 1) * size
        s = _sharpe([t.net_return for t in trades[lo:hi]])
        if s is None:
            return None
        fold_sharpes.append(s)
    return min(fold_sharpes)
