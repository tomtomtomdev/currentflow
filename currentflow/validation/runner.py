"""Paper-trade runner (spec §11) — the forward-paper *run* deferred from slice 7.

Walks one symbol over a list of trading days through the whole decision pipeline and the
ONE shared IDX fill engine, emitting closed `PaperTrade`s:

    [3-5] engine.evaluate → ARMED?   → [6] trigger.analyze → valid R:R≥2:1?
       → [7] fundamental tilt (injected) → [8] order.generate_order → LIMIT accepted?
       → [9] paper fill at the next open      (shared engine)
       → [10] risk.evaluate_exit each day → exit fill at the next open (shared engine)

**Backtest ⇄ forward-paper are two code paths sharing one fill engine** (§11/§13):
`run_backtest` sweeps the window in one batch; `run_forward` steps day-by-day the way live
operation accrues (flat → look for entry; holding → manage the exit). Both call the same
`_attempt_entry` / `_attempt_exit` helpers (hence the same `paper.fill.fill_order`), so over
identical data they **reconcile** — the acceptance invariant. The only intended divergence
is the fundamentals source (backtest = point-in-time parsed statements with a publication
lag; forward = live JSON); no feed is wired yet, so the tilt is injected identically to both.

Look-ahead-safe by construction: every decision is taken at `combine(day, REPLAY_DECISION_TIME)`
so it sees only bars/broker with `as_of` before that pre-open moment (D-1 EOD bar + the LD-5
conservative broker publish); the fill then executes at that day's *open*. `missing ≠ zero`:
an un-fillable leg (locked ARB, gap, limit not reached) is no trade, never a zero-P&L fill.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass
from datetime import date as Date
from datetime import datetime

from currentflow import config
from currentflow.dal.models import BoardType, DailyBar, RowStatus, Side
from currentflow.execution import risk as risk_mod
from currentflow.execution import trigger as trigger_mod
from currentflow.execution.order import Order, generate_order
from currentflow.execution.risk import ExitReason, OpenPosition
from currentflow.fundamentals.tilt import FundamentalTilt
from currentflow.paper.fill import Fill, LiquidityTier, fill_order
from currentflow.signals import engine as engine_mod
from currentflow.signals.broker_flow import BrokerDNA
from currentflow.signals.risk_monitor import CircuitState, Portfolio
from currentflow.validation import trade as trade_mod
from currentflow.validation.trade import PaperTrade

# A protective/target exit is a market-style next-open sell. Modelled as a sell limit at a
# price no real IDX quote can sit below, so it always fills at the next open net of adverse
# slippage — while a genuinely locked ARB still REJECTS (§12) and the position carries a day.
_EXIT_MARKET_LIMIT = 1.0

# Reading "all" bars for fill mechanics (the actual open is knowable at the fill moment);
# look-ahead safety lives in the per-day decision_ts, not here.
_FAR_FUTURE = datetime(2100, 1, 1)


@dataclass(frozen=True, slots=True)
class _Held:
    """An open position plus the context needed to close it into a PaperTrade."""

    position: OpenPosition
    order: Order
    entry_fill: Fill
    tilt_kind: str


@dataclass(frozen=True, slots=True)
class RunConfig:
    """Everything the pipeline needs beyond the store + symbol, held constant for a run."""

    track: str
    tilt: FundamentalTilt
    sector: str
    equity: float = 1_000_000_000.0
    board: BoardType = BoardType.MAIN
    adv20: float | None = None
    portfolio: Portfolio | None = None
    circuit: CircuitState = CircuitState.OK
    registry: dict[str, BrokerDNA] | None = None


def _decision_ts(day: Date) -> datetime:
    """The look-ahead-safe pre-open moment for `day` (D+1 09:15 WIB semantics, §slice-3)."""
    return datetime.combine(day, config.REPLAY_DECISION_TIME)


def _traded_bars(store, symbol: str) -> tuple[list[Date], dict[Date, DailyBar]]:
    bars = [
        b for b in store.read_daily_bars(symbol, _FAR_FUTURE)
        if b.status is RowStatus.TRADED and b.open is not None and b.close is not None
    ]
    bars.sort(key=lambda b: b.date)
    return [b.date for b in bars], {b.date: b for b in bars}


def _prev_close(dates: list[Date], by_date: dict[Date, DailyBar], day: Date) -> float | None:
    i = bisect.bisect_left(dates, day)
    if i == 0:
        return None
    return by_date[dates[i - 1]].close


def _attempt_entry(store, symbol: str, day: Date, cfg: RunConfig,
                   dates: list[Date], by_date: dict[Date, DailyBar]) -> _Held | None:
    """Run [3]→[9] for one candidate day. Returns the open position or None (no trade)."""
    dts = _decision_ts(day)
    res = engine_mod.evaluate(store, symbol, dts, track=cfg.track, registry=cfg.registry)
    if not res.armed:
        return None
    sig = trigger_mod.analyze(store, symbol, dts, res.phase)
    if not sig.valid:
        return None
    order = generate_order(
        sig, equity=cfg.equity, tilt=cfg.tilt, sector=cfg.sector,
        board=cfg.board, adv20=cfg.adv20, portfolio=cfg.portfolio, circuit=cfg.circuit,
    )
    if not order.accepted:
        return None

    entry_bar = by_date.get(day)
    prev_close = _prev_close(dates, by_date, day)
    if entry_bar is None or prev_close is None:
        return None  # can't source a fillable open — no trade (missing ≠ zero)

    fill = fill_order(
        symbol=symbol, side=Side.BUY, limit_price=order.limit_price, qty=order.qty,
        order_date=day, next_open=entry_bar.open, prev_close=prev_close,
        board=cfg.board, tier=order.tier,
    )
    if not fill.filled:
        return None

    position = OpenPosition(
        symbol=symbol, entry_date=day, entry_price=fill.fill_price,
        stop=order.stop, target=order.target, trail_pct=cfg.tilt.trail_pct, qty=order.qty,
    )
    return _Held(position=position, order=order, entry_fill=fill, tilt_kind=cfg.tilt.kind.value)


def _attempt_exit(store, held: _Held, day: Date, cfg: RunConfig,
                  dates: list[Date], by_date: dict[Date, DailyBar]) -> PaperTrade | None:
    """Evaluate [10] for `day`; on an exit that also fills, return the closed trade.

    A should-exit that cannot fill (locked ARB / gap) returns None — the position carries
    to the next day, exactly as in live operation."""
    dts = _decision_ts(day)
    decision = risk_mod.analyze(store, held.position, dts, registry=cfg.registry)
    if not decision.should_exit:
        return None

    exit_bar = by_date.get(day)
    prev_close = _prev_close(dates, by_date, day)
    if exit_bar is None or prev_close is None:
        return None

    exit_fill = fill_order(
        symbol=held.position.symbol, side=Side.SELL, limit_price=_EXIT_MARKET_LIMIT,
        qty=held.position.qty, order_date=day, next_open=exit_bar.open,
        prev_close=prev_close, board=cfg.board, tier=held.order.tier,
    )
    if not exit_fill.filled:
        return None  # e.g. locked ARB — carry the position a day

    return trade_mod.from_fills(
        symbol=held.position.symbol, track=cfg.track, tilt_kind=held.tilt_kind,
        entry=held.entry_fill, exit=exit_fill, exit_reason=decision.reason,
        stop=held.order.stop, risk_idr=held.order.risk_idr,
    )


def run_backtest(store, symbol: str, trading_days: list[Date], cfg: RunConfig) -> list[PaperTrade]:
    """Batch code path: sweep the whole window, one position at a time (spec §11)."""
    dates, by_date = _traded_bars(store, symbol)
    days = sorted(trading_days)
    trades: list[PaperTrade] = []
    i = 0
    while i < len(days):
        held = _attempt_entry(store, symbol, days[i], cfg, dates, by_date)
        if held is None:
            i += 1
            continue
        for j in range(i + 1, len(days)):
            closed = _attempt_exit(store, held, days[j], cfg, dates, by_date)
            if closed is not None:
                trades.append(closed)
                i = j + 1
                break
        else:
            break  # position still open at the window's end — no closed trade
    return trades


def run_forward(store, symbol: str, trading_days: list[Date], cfg: RunConfig) -> list[PaperTrade]:
    """Stepping code path: advance one day at a time as live operation accrues — flat days
    look for an entry, holding days manage the exit (spec §11). Shares the fill engine with
    `run_backtest` via the same helpers, so the two paths reconcile over identical data."""
    dates, by_date = _traded_bars(store, symbol)
    days = sorted(trading_days)
    trades: list[PaperTrade] = []
    held: _Held | None = None
    for day in days:
        if held is None:
            held = _attempt_entry(store, symbol, day, cfg, dates, by_date)
            continue
        closed = _attempt_exit(store, held, day, cfg, dates, by_date)
        if closed is not None:
            trades.append(closed)
            held = None
    return trades
