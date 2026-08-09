"""Auto paper-trader driver (spec §6/§8, LD-11 + LD-12) — the live, hands-off day-stepper.

Two modes share this driver; they differ **only in the entry cohort**:

  * **Fast Mode** (`MODE_FAST`, LD-11) buys every **ARMED** name at once — no Spring/LPS
    trigger, no R:R gate (the LD-11 relaxation of LD-3);
  * **Haste Mode** (`MODE_HASTE`, LD-12) additionally drops the `ARMED@70` arming cut and
    buys the **`WATCH ∪ ARMED`** cohort — every name past the RULE A phase gate (C/D) and the
    §5 veto layer, at any internal SMS.

Everything else is identical: the same triggerless marketable-limit geometry (`fast_detect`),
the same §6 sizing/caps/circuit-breakers, and the **same §8 exit ladder**. RULE A and §5 hold
by construction in both — see `runner.in_entry_cohort`.

Each mode keeps a **separate durable book, trade record and arm state** (the store's `mode`
discriminator) and promotes its **own** RULE B lane — `fast_mode` xor `haste_mode`, never the
trigger-based modules, and never each other: a different entry policy earns its own validation.
Only one mode is armed at a time (they share one paper book's §6 circuit budget).

Each fire loads that mode's open book, advances ONE trading day through the shared `step_day`
(exits → mark/circuit → entries), and persists the updated book + closed trades.

Persistence is the store's job (`paper_position` / `paper_trade` / `fast_mode_state`, all
keyed by mode); this module only converts between the runner's in-memory `_Held`/`PaperTrade`
and those rows. Both modes are **off by default** — the operator arms one, and a disarmed step
is a no-op (never a silent auto-trade).

Reconciliation (§13): the live daemon and the batch `run_portfolio_forward` drive the SAME
`step_day` over the same shared fill engine, so a symbol walked either way produces identical
trades. Look-ahead-safe: the day-step decides at `combine(day, REPLAY_DECISION_TIME)` and fills
at that day's open — neither mode touches the `as_of` discipline, only the entry rule.
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
from currentflow.store.schema import (
    MODE_FAST,
    MODE_HASTE,
    FastModeStateRow,
    FastPositionRow,
    FastTradeRow,
)
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

# The validation lane each mode's trades promote — NEVER the trigger-based modules, and never
# each other's (RULE B: a different entry policy earns its own validation).
FAST_MODE_MODULE = "fast_mode"
HASTE_MODE_MODULE = "haste_mode"

_MODULE_BY_MODE = {MODE_FAST: FAST_MODE_MODULE, MODE_HASTE: HASTE_MODE_MODULE}

# Haste (LD-12) is exactly Fast plus the widened cohort — the ONLY per-mode entry difference.
_INCLUDE_WATCH_BY_MODE = {MODE_FAST: False, MODE_HASTE: True}


class ModeConflictError(RuntimeError):
    """Raised when arming one auto-trader while the other is already armed.

    Fast and Haste share one paper book and one §6 circuit budget (prev/peak equity, the
    daily-P&L and drawdown breakers), so running both would double-count exposure and make
    neither lane's forward-paper record honest. Fail loud rather than silently pick one."""


def module_for(mode: str) -> str:
    """The RULE B validation lane for `mode` (fail loud on an unknown mode)."""
    try:
        return _MODULE_BY_MODE[mode]
    except KeyError:
        raise ValueError(
            f"unknown auto-trader mode {mode!r} — expected one of {sorted(_MODULE_BY_MODE)}"
        ) from None


def other_mode(mode: str) -> str:
    """The mode that must NOT be armed while `mode` is (they share the paper book)."""
    module_for(mode)                      # validate
    return MODE_HASTE if mode == MODE_FAST else MODE_FAST


@dataclass(frozen=True, slots=True)
class FastStepResult:
    """The outcome of one auto-trader day-step (for the scheduler's audit row + the CLI)."""

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
                sector_map: dict[str, str] | None, registry, include_watch: bool) -> RunConfig:
    """Assemble a `RunConfig` for an in-cohort entry candidate (§3 track + §7 tilt)."""
    bars = store.read_daily_bars(sym, decision_ts)
    sector = (sector_map or {}).get(sym, "UNKNOWN")
    return RunConfig(
        track=track_mod.resolve_track(store, sym, decision_ts, bars),
        tilt=classify_tilt(sym, sector=sector), sector=sector, equity=cfg.equity,
        board=BoardType.MAIN, adv20=track_mod._adv20(bars), registry=registry, fast_mode=True,
        include_watch=include_watch,
    )


def _held_spec(row: FastPositionRow, registry, include_watch: bool) -> RunConfig:
    """A `RunConfig` for a still-held name (exit path — `tilt`/cohort unused on exit)."""
    return RunConfig(
        track=row.track, tilt=classify_tilt(row.symbol, sector=row.sector),
        sector=row.sector, board=BoardType(row.board), adv20=None, registry=registry,
        fast_mode=True, include_watch=include_watch,
    )


# --- RULE B accrual (server-authoritative, derived from persisted facts) --------------


def _months_since(since: Date | None, now: datetime) -> float:
    if since is None:
        return 0.0
    return max(0.0, (now.date() - since).days / _DAYS_PER_MONTH)


def accrue_mode(store, ledger, *, mode: str = MODE_FAST, now: datetime):
    """Feed `mode`'s persisted trades + accrued months into ITS OWN ledger lane (RULE B).

    THE single promotion path for an auto-trader lane, derived entirely from stored facts
    (that mode's trades + its `since_date`) — so both the daemon and the UI resolve the same
    server-authoritative state, never a client toggle. Reads are mode-scoped, so Haste trades
    can never promote `fast_mode` (or vice versa). Returns the `ValidationRecord`."""
    trades = [_paper_trade_from_row(r) for r in store.read_fast_trades(mode=mode)]
    state = store.read_fast_mode_state(mode=mode)
    months = _months_since(state.since_date if state else None, now)
    return ledger.record_forward_paper(
        module_for(mode), trades=trades, months_accrued=months
    )


def accrue_fast_mode(store, ledger, *, now: datetime):
    """Back-compatible alias for the Fast lane (slice-15 call sites)."""
    return accrue_mode(store, ledger, mode=MODE_FAST, now=now)


# --- the step ------------------------------------------------------------------------


def run_fast_mode_step(
    store,
    symbols: list[str],
    day: Date,
    *,
    mode: str = MODE_FAST,
    cfg: PortfolioConfig | None = None,
    sector_map: dict[str, str] | None = None,
    registry=None,
    ledger=None,
    now: datetime | None = None,
) -> FastStepResult:
    """Advance `mode`'s auto-trade book by ONE trading `day` over the candidate `symbols`.

    No-op (and records nothing) when that mode is disarmed or `day` was already processed. On a
    live run: reload the mode's book, run `step_day` (exits → mark/circuit → entries), then
    persist the book + closed trades + carried §6 circuit state, and (if a `ledger` is given)
    re-accrue that mode's lane. Every store read/write is mode-scoped, so a Fast step never
    touches the Haste book. `now` stamps the `as_of` audit column (defaults to wall-clock)."""
    now = now or datetime.now()
    module_for(mode)                                   # fail loud on an unknown mode
    include_watch = _INCLUDE_WATCH_BY_MODE[mode]
    cfg = cfg or PortfolioConfig(fast_mode=True, include_watch=include_watch)
    label = "haste mode" if mode == MODE_HASTE else "fast mode"

    state_row = store.read_fast_mode_state(mode=mode)
    if state_row is None or not state_row.enabled:
        return FastStepResult(False, None, 0, 0, 0, 0, 0, f"{label} disarmed — no-op")
    if state_row.last_run_day is not None and day <= state_row.last_run_day:
        return FastStepResult(
            True, day, 0, 0, len(store.read_fast_positions(mode=mode)), 0, 0,
            f"day {day} already processed (last {state_row.last_run_day})",
        )

    decision_ts = _decision_ts(day)

    # Reconstruct the open book + specs. Held names get an exit-only spec (so `step_day` can
    # exit a name even after it drops out of today's candidate set); candidates get a fresh
    # entry spec. `_rank_candidates` inside `step_day` applies the cohort filter itself.
    pos_rows = store.read_fast_positions(mode=mode)
    book: dict[str, _Held] = {r.symbol: _held_from_row(r) for r in pos_rows}
    specs: dict[str, RunConfig] = {
        r.symbol: _held_spec(r, registry, include_watch) for r in pos_rows
    }
    for sym in symbols:
        if sym not in specs:
            specs[sym] = _entry_spec(
                store, sym, decision_ts, cfg, sector_map, registry, include_watch
            )

    bars_idx = {sym: _traded_bars(store, sym) for sym in specs}
    n_before = len(book)
    state = StepState(
        realized=state_row.realized_pnl, prev_equity=state_row.prev_equity,
        peak_equity=state_row.peak_equity,
    )

    closed_today, blocked, new_state = step_day(store, specs, book, bars_idx, day, cfg, state)

    # Persist the updated book + newly closed trades + carried circuit state (mode-scoped).
    new_positions = [_position_row(sym, held, specs[sym], now) for sym, held in book.items()]
    store.replace_fast_positions(new_positions, mode=mode)
    trade_rows = [_trade_row(t, now) for t in closed_today]
    store.append_fast_trades(trade_rows, mode=mode)
    store.write_fast_mode_state(FastModeStateRow(
        enabled=True, since_date=state_row.since_date or day, last_run_day=day,
        realized_pnl=new_state.realized, prev_equity=new_state.prev_equity,
        peak_equity=new_state.peak_equity,
    ), mode=mode)

    if ledger is not None:
        accrue_mode(store, ledger, mode=mode, now=now)

    entered = len(book) - (n_before - len(closed_today))
    return FastStepResult(
        enabled=True, day=day, entered=max(0, entered), closed=len(closed_today),
        open_positions=len(book), blocked_by_circuit=blocked,
        rows_written=len(new_positions) + len(trade_rows),
        detail=f"{len(book)} open, {len(closed_today)} closed, {blocked} circuit-blocked",
    )


# --- operator arm/disarm (the toggle the UI + CLI flip) ------------------------------


def set_enabled(
    store, enabled: bool, *, mode: str = MODE_FAST, now: datetime | None = None
) -> FastModeStateRow:
    """Arm/disarm one auto-trader (operator control). Arming stamps that mode's `since_date`
    (its RULE B clock start) if not already set; disarming preserves the book + accrued record
    (a pause, not a reset) and is never refused. Returns the new state row.

    **Fast xor Haste:** arming one while the other is armed raises `ModeConflictError` — they
    share a paper book and a §6 circuit budget, so both at once would double-count exposure."""
    now = now or datetime.now()
    module_for(mode)                                   # fail loud on an unknown mode

    if enabled:
        rival = store.read_fast_mode_state(mode=other_mode(mode))
        if rival is not None and rival.enabled:
            raise ModeConflictError(
                f"cannot arm {mode} while {other_mode(mode)} is armed — one auto-trader at a "
                f"time over the shared paper book; disarm {other_mode(mode)} first"
            )

    prev = store.read_fast_mode_state(mode=mode)
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
    store.write_fast_mode_state(row, mode=mode)
    return row
