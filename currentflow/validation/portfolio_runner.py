"""Portfolio auto paper-trader (spec §6/§11) — the multi-name forward-paper run.

`validation.runner` runs ONE symbol, one position at a time, with a *static* portfolio
and circuit state — so the §6 exposure caps and circuit breakers it passes to
`execution.order` never actually bind. This module is the missing portfolio layer: it
walks a whole candidate universe day-by-day, maintains the open-positions book, and feeds
a **live** portfolio + circuit state into every entry, so the caps and breakers bind for
real.

Per trading day, in order:

    [1] EXITS FIRST      manage every open position (§8) → frees name/sector room + cash
    [2] MARK + CIRCUIT   mark the book at the look-ahead-safe prev close; derive the §6
                         circuit state from running (realised + unrealised) P&L
    [3] ENTRIES          scan the universe for ARMED names with a valid trigger, rank by
                         the INTERNAL SMS score (RULE B: ordering only, never displayed),
                         and enter in that order — each sized against the live book so the
                         §6 caps clamp and a tripped breaker blocks new entries

**Selection (operator decision, 2026-07-08): emergent + internal-SMS priority.** We do NOT
take every ARMED name — a name still needs a valid Spring/LPS trigger (R:R ≥ 2:1). We do NOT
target a gross-exposure %: total deployment is emergent, bounded only by the locked 10%/name
and 30%/sector caps. `MAX_CONCURRENT_POSITIONS` is an OPTIONAL count cap, defaulted OFF
(`None`) — off, this changes no locked behaviour and needs no spec bump. When more triggers
fire than the caps/capacity can hold, the higher internal-SMS name wins the slot.

**Sizing base is a fixed equity notional** (like `runner.RunConfig.equity`), matching §6
as-written and keeping the two runners reconcilable; the running P&L drives only the circuit
breakers, not the per-trade sizing. **Market regime does NOT scale allocation here** — that
would be a new locked decision (spec bump) and is deferred to `signals.regime`, which is
observation-only until forward-paper-validated (RULE B discipline).

Reconciliation (§13): a single-symbol universe run reproduces `runner.run_forward` exactly,
because both drive the ONE shared fill engine through the same `runner._attempt_entry` /
`_attempt_exit` helpers reused here.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date as Date

from currentflow.signals.engine import evaluate as engine_evaluate
from currentflow.signals.risk_monitor import (
    CircuitState,
    Portfolio,
    Position,
    circuit_breaker_state,
)
from currentflow.execution.trigger import analyze as trigger_analyze
from currentflow.execution.trigger import fast_analyze as trigger_fast_analyze
from currentflow.validation.runner import (
    RunConfig,
    _attempt_entry,
    _attempt_exit,
    _decision_ts,
    _Held,
    _prev_close,
    _traded_bars,
)
from currentflow.validation.trade import PaperTrade


@dataclass(frozen=True, slots=True)
class PortfolioConfig:
    """Portfolio-level knobs held constant for a run.

    `equity` is the fixed sizing base (§6 `equity × 1%`). `max_concurrent` caps the number
    of simultaneously-open names; `None` = emergent (the spec-faithful default, no cap).
    `fast_mode` (LD-11) flips every entry to buy-on-ARMED-at-once (no trigger / no R:R gate)
    — applied portfolio-wide to the specs at the start of a run."""

    equity: float = 1_000_000_000.0
    max_concurrent: int | None = None
    fast_mode: bool = False


@dataclass(frozen=True, slots=True)
class StepState:
    """Running portfolio state carried across trading days (so a live daemon can persist it).

    `realized` is cumulative net-of-fee P&L from closed trades; `prev_equity` is the prior
    day's marked equity (drives the §6 daily-P&L breaker); `peak_equity` is the running high
    (drives the §6 drawdown breaker)."""

    realized: float
    prev_equity: float
    peak_equity: float


@dataclass(frozen=True, slots=True)
class OpenBookEntry:
    """One still-open position at the window's end (for inspecting the book / caps)."""

    symbol: str
    sector: str
    qty: int
    entry_price: float

    @property
    def notional(self) -> float:
        return self.qty * self.entry_price


@dataclass(frozen=True, slots=True)
class PortfolioResult:
    """The outcome of a portfolio forward run at the window's end."""

    trades: tuple[PaperTrade, ...]
    open_positions: tuple[OpenBookEntry, ...]
    realized_pnl: float          # net-of-fee P&L from closed trades
    final_equity: float          # equity mark at the last processed day
    entries_blocked_by_circuit: int

    @property
    def open_symbols(self) -> tuple[str, ...]:
        return tuple(e.symbol for e in self.open_positions)

    def sector_notional(self, sector: str) -> float:
        return sum(e.notional for e in self.open_positions if e.sector == sector)


def _symbol_cfg(spec: RunConfig, equity: float, portfolio: Portfolio, circuit: CircuitState) -> RunConfig:
    """Per-symbol RunConfig with the portfolio equity and the live portfolio/circuit."""
    return replace(spec, equity=equity, portfolio=portfolio, circuit=circuit)


def _rank_candidates(
    store, specs: dict[str, RunConfig], book: dict[str, _Held], day: Date
) -> list[str]:
    """ARMED names with an entry signal, NOT already held, ordered by INTERNAL SMS desc.

    RULE B: `internal_score` is used only to order entries — it is never returned or shown.
    A name always needs `engine.armed` (phase C/D + SMS≥70 + no veto). The standard path
    additionally needs a valid R:R≥2:1 Spring/LPS trigger; **Fast Mode (LD-11, per spec)
    drops the trigger + R:R gate** so every coherent ARMED name is a candidate."""
    dts = _decision_ts(day)
    scored: list[tuple[float, str]] = []
    for sym, spec in specs.items():
        if sym in book:
            continue
        res = engine_evaluate(store, sym, dts, track=spec.track, registry=spec.registry)
        if not res.armed:
            continue
        analyze = trigger_fast_analyze if spec.fast_mode else trigger_analyze
        sig = analyze(store, sym, dts, res.phase)
        if not sig.valid:
            continue
        scored.append((res.sms.internal_score, sym))
    scored.sort(key=lambda t: t[0], reverse=True)
    return [sym for _, sym in scored]


def _live_portfolio(
    store, specs: dict[str, RunConfig], book: dict[str, _Held],
    bars_idx: dict[str, tuple[list[Date], dict]], day: Date, equity: float,
) -> Portfolio:
    """Build the current book as a `Portfolio`, marked at the look-ahead-safe prev close.

    `execution.order` reads only `sector_values(portfolio)` off this (the §6 sector cap);
    the name cap and 1%-risk sizing run off the passed `equity`. Marks use the latest close
    visible at the pre-open decision moment (prev close), never `day`'s own close."""
    positions: list[Position] = []
    for sym, held in book.items():
        dates, by_date = bars_idx[sym]
        pc = _prev_close(dates, by_date, day)
        mark = pc if pc is not None else held.entry_fill.fill_price
        positions.append(
            Position(
                symbol=sym, sector=specs[sym].sector, qty=held.position.qty,
                last_price=mark, entry_price=held.entry_fill.fill_price,
                board=specs[sym].board,
            )
        )
    invested = sum(p.market_value for p in positions)
    return Portfolio(positions=tuple(positions), cash=max(0.0, equity - invested))


def _unrealized(
    book: dict[str, _Held], bars_idx: dict[str, tuple[list[Date], dict]], day: Date
) -> float:
    """Mark-to-(prev-close) unrealised P&L of the open book (look-ahead-safe)."""
    total = 0.0
    for sym, held in book.items():
        dates, by_date = bars_idx[sym]
        pc = _prev_close(dates, by_date, day)
        if pc is not None:
            total += (pc - held.entry_fill.fill_price) * held.position.qty
    return total


def step_day(
    store,
    specs: dict[str, RunConfig],
    book: dict[str, _Held],
    bars_idx: dict[str, tuple[list[Date], dict]],
    day: Date,
    cfg: PortfolioConfig,
    state: StepState,
) -> tuple[list[PaperTrade], int, StepState]:
    """One trading day of the portfolio auto-trader — exits → mark/circuit → entries.

    Mutates `book` in place (removes exited names, adds new entries) and returns the trades
    closed today, the circuit-block count (0/1), and the updated running `state`. The batch
    `run_portfolio_forward` loops this; the live Fast-Mode daemon (`validation.fast_mode`)
    calls it **once per day** with the book loaded from the store — the same code path, so the
    two reconcile (§13)."""
    # [1] EXITS FIRST — free capital / name+sector room before considering entries.
    closed_today: list[PaperTrade] = []
    for sym in list(book):
        dates, by_date = bars_idx[sym]
        scfg = replace(specs[sym], equity=cfg.equity)
        closed = _attempt_exit(store, book[sym], day, scfg, dates, by_date)
        if closed is not None:
            closed_today.append(closed)
            del book[sym]
    realized = state.realized + sum(c.net_pnl for c in closed_today)

    # [2] MARK the book + derive the §6 circuit state from running P&L.
    equity_now = cfg.equity + realized + _unrealized(book, bars_idx, day)
    peak_equity = max(state.peak_equity, equity_now)
    daily_pnl_pct = (equity_now - state.prev_equity) / state.prev_equity if state.prev_equity else None
    drawdown_pct = (equity_now - peak_equity) / peak_equity if peak_equity else None
    circuit = circuit_breaker_state(daily_pnl_pct, drawdown_pct)
    new_state = StepState(realized=realized, prev_equity=equity_now, peak_equity=peak_equity)

    # [3] ENTRIES — blocked wholesale by a tripped breaker or a full book.
    at_capacity = cfg.max_concurrent is not None and len(book) >= cfg.max_concurrent
    if circuit is not CircuitState.OK:
        return closed_today, 1, new_state
    if at_capacity:
        return closed_today, 0, new_state

    for sym in _rank_candidates(store, specs, book, day):
        if cfg.max_concurrent is not None and len(book) >= cfg.max_concurrent:
            break
        live_pf = _live_portfolio(store, specs, book, bars_idx, day, cfg.equity)
        scfg = _symbol_cfg(specs[sym], cfg.equity, live_pf, circuit)
        dates, by_date = bars_idx[sym]
        held = _attempt_entry(store, sym, day, scfg, dates, by_date)
        if held is not None:
            book[sym] = held
    return closed_today, 0, new_state


def run_portfolio_forward(
    store,
    specs: dict[str, RunConfig],
    trading_days: list[Date],
    cfg: PortfolioConfig = PortfolioConfig(),
) -> PortfolioResult:
    """Walk the universe day-by-day as live operation accrues (spec §11).

    `specs` maps each candidate symbol → its `RunConfig` (track, tilt, sector, board,
    adv20, registry); the per-run equity comes from `cfg` and overrides each spec's.
    `cfg.fast_mode` (LD-11) applies the buy-on-ARMED-at-once entry to every spec."""
    days = sorted(trading_days)
    if cfg.fast_mode:
        specs = {sym: replace(spec, fast_mode=True) for sym, spec in specs.items()}
    bars_idx = {sym: _traded_bars(store, sym) for sym in specs}

    book: dict[str, _Held] = {}
    trades: list[PaperTrade] = []
    state = StepState(realized=0.0, prev_equity=cfg.equity, peak_equity=cfg.equity)
    blocked = 0

    for day in days:
        closed_today, blk, state = step_day(store, specs, book, bars_idx, day, cfg, state)
        trades.extend(closed_today)
        blocked += blk

    open_positions = tuple(
        OpenBookEntry(
            symbol=sym, sector=specs[sym].sector,
            qty=held.position.qty, entry_price=held.entry_fill.fill_price,
        )
        for sym, held in book.items()
    )
    return PortfolioResult(
        trades=tuple(trades),
        open_positions=open_positions,
        realized_pnl=state.realized,
        final_equity=state.prev_equity,
        entries_blocked_by_circuit=blocked,
    )
