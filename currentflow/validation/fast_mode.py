"""Fast Mode driver (spec §6/§8, LD-11) — the live, hands-off auto paper-trader.

Fast Mode buys **every ARMED name at once** (no Spring/LPS trigger, no R:R gate — the LD-11
relaxation) and manages each buy with the **same §8 exit ladder**. It is the operational
wrapper that turns the batch `portfolio_runner` into a live daemon: each fire loads the
durable open book from the store, advances ONE trading day through the shared `step_day`
(exits → mark/circuit → fast entries), and persists the updated book + closed trades. The
closed trades are the real forward-paper record that promotes the **`fast_mode`** module lane
(RULE B) — never the trigger-based modules.

Reconciliation (§13): the live daemon and the batch `run_portfolio_forward` drive the SAME
`step_day` over the same shared fill engine, so a symbol walked either way produces identical
trades. Look-ahead-safe: the day-step decides at `combine(day, REPLAY_DECISION_TIME)` and fills
at that day's open — Fast Mode changes the entry *rule*, never the `as_of` discipline.

Persistence is the store's job (`paper_position` / `paper_trade` / `fast_mode_state`); this
module only converts between the runner's in-memory `_Held`/`PaperTrade` and those rows. The
Fast-Mode run is **off by default** — the operator arms it (`fast_mode_state.enabled`), and a
disabled step is a no-op (never a silent auto-trade).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as Date
from datetime import datetime

from currentflow.dal.models import BoardType, Side
from currentflow.execution.order import Order, OrderStatus
from currentflow.execution.risk import ExitReason, OpenPosition
from currentflow.fundamentals.tilt import classify_tilt
from currentflow.paper.fill import FeeBreakdown, Fill, FillStatus, LiquidityTier
from currentflow.store.schema import FastModeStateRow, FastPositionRow, FastTradeRow
from currentflow.universe import track as track_mod
from currentflow.validation.portfolio_runner import PortfolioConfig, StepState, step_day
from currentflow.validation.runner import (
    RunConfig,
    _decision_ts,
    _Held,
    _traded_bars,
)
from currentflow.validation.trade import PaperTrade

# Approx days per month for the RULE B "months accrued" clock (§8 forward-paper gate).
_DAYS_PER_MONTH = 30.44

# The validation lane Fast-Mode trades promote — NEVER the trigger-based modules (RULE B).
FAST_MODE_MODULE = "fast_mode"


@dataclass(frozen=True, slots=True)
class FastStepResult:
    """The outcome of one Fast-Mode day-step (for the scheduler's audit row + the CLI)."""

    enabled: bool
    day: Date | None
    entered: int          # new positions opened this step
    closed: int           # positions closed this step
    open_positions: int   # book size after the step
    blocked_by_circuit: int
    rows_written: int
    detail: str


# --- (de)serialisation between the runner's in-memory objects and store rows ----------


def _held_from_row(row: FastPositionRow) -> _Held:
    """Rebuild the in-memory open position from its stored row so `_attempt_exit` can run it.

    The reconstructed entry `Fill` carries only what `trade.from_fills` reads (fill price/qty/
    date + `fees.total = entry_fee`), so the closed trade's net-of-fee P&L reconciles exactly
    with the in-memory path (no fee math is redone — the 2026-07-03 invariant)."""
    tier = LiquidityTier(row.tier)
    position = OpenPosition(
        symbol=row.symbol, entry_date=row.entry_date, entry_price=row.entry_price,
        stop=row.stop, target=row.target, trail_pct=row.trail_pct, qty=row.qty,
    )
    order = Order(
        symbol=row.symbol, decision_ts=_decision_ts(row.entry_date),
        status=OrderStatus.ACCEPTED, side=Side.BUY, order_type="LIMIT",
        limit_price=row.entry_price, qty=row.qty, stop=row.stop, target=row.target,
        rr=None, risk_idr=row.risk_idr, tilt_kind=row.tilt_kind,
        board=BoardType(row.board), tier=tier, reason="restored from paper_position",
    )
    entry_fill = Fill(
        symbol=row.symbol, side=Side.BUY, status=FillStatus.FILLED,
        order_date=row.entry_date, fill_date=row.entry_date, requested_limit=row.entry_price,
        qty=row.qty, tier=tier, fill_price=row.entry_price, slippage_pct=0.0,
        gross=row.entry_price * row.qty,
        fees=FeeBreakdown(commission=row.entry_fee, vat=0.0, levy=0.0, sell_tax=0.0),
        cash_flow=-(row.entry_price * row.qty + row.entry_fee),
        settlement_date=None, reason="restored",
    )
    return _Held(position=position, order=order, entry_fill=entry_fill, tilt_kind=row.tilt_kind)


def _position_row(sym: str, held: _Held, spec: RunConfig, as_of: datetime) -> FastPositionRow:
    """Serialise an open `_Held` (+ its spec) to the durable book row."""
    return FastPositionRow(
        symbol=sym, as_of=as_of, track=spec.track, sector=spec.sector,
        board=held.order.board.value, tier=held.order.tier.value, tilt_kind=held.tilt_kind,
        entry_date=held.position.entry_date, entry_price=held.position.entry_price,
        stop=held.position.stop, target=held.position.target,
        trail_pct=held.position.trail_pct, qty=held.position.qty,
        risk_idr=held.order.risk_idr, entry_fee=held.entry_fill.fees.total,
    )


def _trade_row(t: PaperTrade, as_of: datetime) -> FastTradeRow:
    """Serialise a closed `PaperTrade` to the durable trade row."""
    return FastTradeRow(
        symbol=t.symbol, entry_date=t.entry_date, exit_date=t.exit_date, as_of=as_of,
        track=t.track, tilt_kind=t.tilt_kind, qty=t.qty, entry_price=t.entry_price,
        exit_price=t.exit_price, entry_fee=t.entry_fee, exit_fee=t.exit_fee,
        exit_reason=t.exit_reason.value, stop=t.stop, risk_idr=t.risk_idr,
    )


def _paper_trade_from_row(r: FastTradeRow) -> PaperTrade:
    """Rebuild a `PaperTrade` from a stored row (for metrics / ledger accrual)."""
    return PaperTrade(
        symbol=r.symbol, track=r.track, tilt_kind=r.tilt_kind, entry_date=r.entry_date,
        exit_date=r.exit_date, qty=r.qty, entry_price=r.entry_price, exit_price=r.exit_price,
        entry_fee=r.entry_fee, exit_fee=r.exit_fee, exit_reason=ExitReason(r.exit_reason),
        stop=r.stop, risk_idr=r.risk_idr,
    )


# --- spec assembly -------------------------------------------------------------------


def _entry_spec(store, sym: str, decision_ts: datetime, cfg: PortfolioConfig,
                sector_map: dict[str, str] | None, registry) -> RunConfig:
    """Assemble a fast-mode `RunConfig` for an ARMED entry candidate (§3 track + §7 tilt)."""
    bars = store.read_daily_bars(sym, decision_ts)
    sector = (sector_map or {}).get(sym, "UNKNOWN")
    return RunConfig(
        track=track_mod.resolve_track(store, sym, decision_ts, bars),
        tilt=classify_tilt(sym, sector=sector), sector=sector, equity=cfg.equity,
        board=BoardType.MAIN, adv20=track_mod._adv20(bars), registry=registry, fast_mode=True,
    )


def _held_spec(row: FastPositionRow, registry) -> RunConfig:
    """A fast-mode `RunConfig` for a still-held name (exit path — `tilt` unused on exit)."""
    return RunConfig(
        track=row.track, tilt=classify_tilt(row.symbol, sector=row.sector),
        sector=row.sector, board=BoardType(row.board), adv20=None, registry=registry,
        fast_mode=True,
    )


# --- RULE B accrual (server-authoritative, derived from persisted facts) --------------


def _months_since(since: Date | None, now: datetime) -> float:
    if since is None:
        return 0.0
    return max(0.0, (now.date() - since).days / _DAYS_PER_MONTH)


def accrue_fast_mode(store, ledger, *, now: datetime):
    """Feed the persisted Fast-Mode trades + accrued months into the ledger (RULE B).

    THE single promotion path for the `fast_mode` lane, derived entirely from stored facts
    (trades + `since_date`) — so both the daemon and the UI resolve the same server-authoritative
    state, never a client toggle. Returns the `ValidationRecord`."""
    trades = [_paper_trade_from_row(r) for r in store.read_fast_trades()]
    state = store.read_fast_mode_state()
    months = _months_since(state.since_date if state else None, now)
    return ledger.record_forward_paper(FAST_MODE_MODULE, trades=trades, months_accrued=months)


# --- the step ------------------------------------------------------------------------


def run_fast_mode_step(
    store,
    symbols: list[str],
    day: Date,
    *,
    cfg: PortfolioConfig | None = None,
    sector_map: dict[str, str] | None = None,
    registry=None,
    ledger=None,
    now: datetime | None = None,
) -> FastStepResult:
    """Advance the Fast-Mode book by ONE trading `day` over the candidate `symbols`.

    No-op (and records nothing) when Fast Mode is disarmed or `day` was already processed. On a
    live run: reload the book, run `step_day` (exits → mark/circuit → fast entries), then persist
    the book + closed trades + carried §6 circuit state, and (if a `ledger` is given) re-accrue
    the `fast_mode` lane. `now` stamps the `as_of` audit column (defaults to wall-clock)."""
    now = now or datetime.now()
    cfg = cfg or PortfolioConfig(fast_mode=True)

    state_row = store.read_fast_mode_state()
    if state_row is None or not state_row.enabled:
        return FastStepResult(False, None, 0, 0, 0, 0, 0, "fast mode disarmed — no-op")
    if state_row.last_run_day is not None and day <= state_row.last_run_day:
        return FastStepResult(
            True, day, 0, 0, len(store.read_fast_positions()), 0, 0,
            f"day {day} already processed (last {state_row.last_run_day})",
        )

    decision_ts = _decision_ts(day)

    # Reconstruct the open book + specs. Held names get an exit-only spec (so `step_day` can
    # exit a name even after it drops out of today's ARMED candidate set); ARMED candidates get
    # a fresh entry spec. `_rank_candidates` inside `step_day` does the ARMED filter itself.
    pos_rows = store.read_fast_positions()
    book: dict[str, _Held] = {r.symbol: _held_from_row(r) for r in pos_rows}
    specs: dict[str, RunConfig] = {r.symbol: _held_spec(r, registry) for r in pos_rows}
    for sym in symbols:
        if sym not in specs:
            specs[sym] = _entry_spec(store, sym, decision_ts, cfg, sector_map, registry)

    bars_idx = {sym: _traded_bars(store, sym) for sym in specs}
    n_before = len(book)
    state = StepState(
        realized=state_row.realized_pnl, prev_equity=state_row.prev_equity,
        peak_equity=state_row.peak_equity,
    )

    closed_today, blocked, new_state = step_day(store, specs, book, bars_idx, day, cfg, state)

    # Persist the updated book + newly closed trades + carried circuit state.
    new_positions = [_position_row(sym, held, specs[sym], now) for sym, held in book.items()]
    store.replace_fast_positions(new_positions)
    trade_rows = [_trade_row(t, now) for t in closed_today]
    store.append_fast_trades(trade_rows)
    store.write_fast_mode_state(FastModeStateRow(
        enabled=True, since_date=state_row.since_date or day, last_run_day=day,
        realized_pnl=new_state.realized, prev_equity=new_state.prev_equity,
        peak_equity=new_state.peak_equity,
    ))

    if ledger is not None:
        accrue_fast_mode(store, ledger, now=now)

    entered = len(book) - (n_before - len(closed_today))
    return FastStepResult(
        enabled=True, day=day, entered=max(0, entered), closed=len(closed_today),
        open_positions=len(book), blocked_by_circuit=blocked,
        rows_written=len(new_positions) + len(trade_rows),
        detail=f"{len(book)} open, {len(closed_today)} closed, {blocked} circuit-blocked",
    )


# --- operator arm/disarm (the toggle the UI + CLI flip) ------------------------------


def set_enabled(store, enabled: bool, *, now: datetime | None = None) -> FastModeStateRow:
    """Arm/disarm Fast Mode (operator control). Arming stamps `since_date` (the RULE B clock
    start) if not already set; disarming preserves the book + accrued record (a pause, not a
    reset). Returns the new state row."""
    now = now or datetime.now()
    prev = store.read_fast_mode_state()
    since = (prev.since_date if prev else None)
    if enabled and since is None:
        since = now.date()
    row = FastModeStateRow(
        enabled=enabled, since_date=since,
        last_run_day=(prev.last_run_day if prev else None),
        realized_pnl=(prev.realized_pnl if prev else 0.0),
        prev_equity=(prev.prev_equity if prev else PortfolioConfig().equity),
        peak_equity=(prev.peak_equity if prev else PortfolioConfig().equity),
    )
    store.write_fast_mode_state(row)
    return row
