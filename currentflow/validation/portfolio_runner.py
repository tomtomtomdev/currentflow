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
    of simultaneously-open names; `None` = emergent (the spec-faithful default, no cap)."""

    equity: float = 1_000_000_000.0
    max_concurrent: int | None = None


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
    """ARMED names with a valid trigger, NOT already held, ordered by INTERNAL SMS desc.

    RULE B: `internal_score` is used only to order entries — it is never returned or shown.
    A name still needs `engine.armed` (phase C/D + SMS≥70 + no veto) AND a valid R:R≥2:1
    trigger to be a candidate at all — we never take the whole ARMED list."""
    dts = _decision_ts(day)
    scored: list[tuple[float, str]] = []
    for sym, spec in specs.items():
        if sym in book:
            continue
        res = engine_evaluate(store, sym, dts, track=spec.track, registry=spec.registry)
        if not res.armed:
            continue
        sig = trigger_analyze(store, sym, dts, res.phase)
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


def run_portfolio_forward(
    store,
    specs: dict[str, RunConfig],
    trading_days: list[Date],
    cfg: PortfolioConfig = PortfolioConfig(),
) -> PortfolioResult:
    """Walk the universe day-by-day as live operation accrues (spec §11).

    `specs` maps each candidate symbol → its `RunConfig` (track, tilt, sector, board,
    adv20, registry); the per-run equity comes from `cfg` and overrides each spec's."""
    days = sorted(trading_days)
    bars_idx = {sym: _traded_bars(store, sym) for sym in specs}

    book: dict[str, _Held] = {}
    trades: list[PaperTrade] = []
    realized = 0.0
    prev_equity = cfg.equity
    peak_equity = cfg.equity
    equity_now = cfg.equity
    blocked = 0

    for day in days:
        # [1] EXITS FIRST — free capital / name+sector room before considering entries.
        for sym in list(book):
            dates, by_date = bars_idx[sym]
            scfg = replace(specs[sym], equity=cfg.equity)
            closed = _attempt_exit(store, book[sym], day, scfg, dates, by_date)
            if closed is not None:
                trades.append(closed)
                realized += closed.net_pnl
                del book[sym]

        # [2] MARK the book + derive the §6 circuit state from running P&L.
        equity_now = cfg.equity + realized + _unrealized(book, bars_idx, day)
        peak_equity = max(peak_equity, equity_now)
        daily_pnl_pct = (equity_now - prev_equity) / prev_equity if prev_equity else None
        drawdown_pct = (equity_now - peak_equity) / peak_equity if peak_equity else None
        circuit = circuit_breaker_state(daily_pnl_pct, drawdown_pct)
        prev_equity = equity_now

        # [3] ENTRIES — blocked wholesale by a tripped breaker or a full book.
        at_capacity = cfg.max_concurrent is not None and len(book) >= cfg.max_concurrent
        if circuit is not CircuitState.OK:
            blocked += 1
            continue
        if at_capacity:
            continue

        for sym in _rank_candidates(store, specs, book, day):
            if cfg.max_concurrent is not None and len(book) >= cfg.max_concurrent:
                break
            live_pf = _live_portfolio(store, specs, book, bars_idx, day, cfg.equity)
            scfg = _symbol_cfg(specs[sym], cfg.equity, live_pf, circuit)
            dates, by_date = bars_idx[sym]
            held = _attempt_entry(store, sym, day, scfg, dates, by_date)
            if held is not None:
                book[sym] = held

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
        realized_pnl=realized,
        final_equity=equity_now,
        entries_blocked_by_circuit=blocked,
    )
