"""Strided backfill — the cheap store the premise tests need.

`ingest.pipeline.ingest_symbol` fetches broker summary for EVERY trading day, because the
live pipeline needs every day. The premise harness does not: it samples decision days
`stride_days` apart and reads broker state only on the trading day before each one. At a
10-day stride that is ~1/10th the broker calls — the difference between ~102k and ~10k
against a paywall-counted endpoint.

That makes falsification affordable *before* the full pull is committed: you can kill a
35-weight component for ~14k calls instead of discovering it was dead after 102k.

**Why this refuses to write to the production store.** `ingest_symbol` keys ingest-once
on `daily_bar` (bars are written last, as the commit marker, so a mid-symbol failure
re-fetches the whole symbol rather than leaving a broker hole). A strided run writes
COMPLETE bars and SPARSE broker — so a later production run would see every day cached
and skip the symbol, leaving permanent broker holes with nothing to detect them. Rather
than weaken that invariant, this path uses its own throwaway store (`guard_target_db`).

Here broker ingest-once keys on `broker_net` itself, not on bars, so a partial run
resumes exactly where it stopped.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date as Date
from datetime import datetime
from pathlib import Path
from typing import Sequence

log = logging.getLogger(__name__)

# Stores the live pipeline owns. A strided pull into one of these would silently
# poison its ingest-once marker (see module docstring).
PRODUCTION_DB_NAMES: tuple[str, ...] = ("currentflow.duckdb",)


class ResearchBackfillError(RuntimeError):
    """A strided run could not proceed."""


@dataclass(frozen=True, slots=True)
class Budget:
    n_symbols: int
    n_trading_days: int
    stride_days: int
    n_decision_days: int
    broker_calls: int
    ohlcv_calls: int

    def line(self) -> str:
        return (
            f"budget: {self.n_symbols} names × {self.n_decision_days} sampled day(s) "
            f"= {self.broker_calls} broker call(s) (stride {self.stride_days}; a dense "
            f"pull would be {self.n_symbols * self.n_trading_days}), plus ~{self.ohlcv_calls} "
            f"OHLCV page call(s)"
        )


@dataclass(frozen=True, slots=True)
class ResearchBackfillReport:
    symbols: tuple[str, ...]
    start: Date
    end: Date
    stride_days: int
    trading_days: int
    decision_days: tuple[Date, ...]
    broker_days: tuple[Date, ...]
    bars_inserted: int
    broker_rows_inserted: int
    broker_days_skipped_cached: int
    failed_symbol: str | None
    broker_sparse: bool = True
    caveats: tuple[str, ...] = field(default_factory=tuple)

    @property
    def warning(self) -> str:
        return (
            f"broker_net in this store is SPARSE BY DESIGN — rows exist only on the "
            f"{len(self.broker_days)} day(s) sampled at stride {self.stride_days}. It is "
            f"valid for premise tests at this stride and INVALID for any dense signal, "
            f"backtest, or base-rate computation. Never point the live pipeline at it."
        )


def guard_target_db(db_path: str) -> None:
    """Refuse to strand a production store with complete bars and sparse broker rows."""
    if Path(db_path).name in PRODUCTION_DB_NAMES:
        raise ValueError(
            f"refusing to write a strided (sparse-broker) backfill into {db_path!r}: "
            "ingest.pipeline keys ingest-once on daily_bar, so complete bars would make "
            "a later production run skip these symbols and leave permanent broker holes. "
            "Use a separate research store (e.g. --db research.duckdb)."
        )


def plan_budget(*, n_symbols: int, n_trading_days: int, stride_days: int) -> Budget:
    """Up-front call budget so the operator arms it knowingly (no silent caps)."""
    stride = max(stride_days, 1)
    n_decision = (n_trading_days + stride - 1) // stride
    # OHLCV pages 50 rows/call, one paginated sweep per symbol.
    ohlcv = n_symbols * max((n_trading_days + 49) // 50, 1)
    return Budget(
        n_symbols=n_symbols,
        n_trading_days=n_trading_days,
        stride_days=stride,
        n_decision_days=n_decision,
        broker_calls=n_symbols * n_decision,
        ohlcv_calls=ohlcv,
    )


def sample_decision_days(
    trading_days: Sequence[Date], start: Date, end: Date, stride_days: int
) -> list[Date]:
    """The days the premise harness will sample — must match `premise._sample_days`."""
    window = [d for d in trading_days if start <= d <= end]
    stride = max(stride_days, 1)
    return window[::stride] if stride > 1 else list(window)


def broker_days_for(
    trading_days: Sequence[Date], decision_days: Sequence[Date]
) -> list[Date]:
    """The trading day immediately BEFORE each decision day — the newest broker state a
    feature may legally see at the 09:15 pre-open frame. A decision day with no prior
    trading day yields nothing (never a silent stand-in from a later day)."""
    ordered = sorted(set(trading_days))
    out: set[Date] = set()
    for day in decision_days:
        prior = [d for d in ordered if d < day]
        if prior:
            out.add(prior[-1])
    return sorted(out)


async def run_research_backfill(
    client,
    store,
    symbols: Sequence[str],
    *,
    start: Date,
    end: Date,
    stride_days: int,
    now: datetime,
    log_fn=log.info,
) -> ResearchBackfillReport:
    """Full bars over [start, end]; broker rows only on the strided days.

    Two passes by necessity: decision days come from the *union* trading calendar (so
    every symbol is ranked on the same days, as `premise._sample_days` assumes), and that
    calendar is only known once bars are in.
    """
    syms = [s.upper() for s in symbols]
    bars_inserted = 0
    failed: str | None = None

    # --- pass 1: bars (cheap, paginated) ---------------------------------------------
    for symbol in syms:
        cached = store.ingested_dates(symbol, "daily_bar")
        if any(d not in cached for d in _weekdays_between(start, end)):
            bars = await client.ohlcv_foreign(symbol, start, end)
            bars_inserted += store.write_daily_bars(bars)

    # --- the shared calendar ---------------------------------------------------------
    trading_days = sorted(
        {b.date for s in syms for b in store.read_daily_bars(s, now, start=start, end=end)}
    )
    if not trading_days:
        raise ResearchBackfillError(
            f"no bars stored for {start}..{end} — cannot derive a trading calendar"
        )
    decision_days = sample_decision_days(trading_days, start, end, stride_days)
    wanted_broker = broker_days_for(trading_days, decision_days)

    # --- pass 2: broker on the strided days only --------------------------------------
    broker_inserted = 0
    skipped_cached = 0
    for symbol in syms:
        # Ingest-once keyed on broker_net itself (NOT the bars marker) — this is what
        # makes a partial strided run resumable.
        have = store.ingested_dates(symbol, "broker_net")
        missing = [d for d in wanted_broker if d not in have]
        skipped_cached += len(wanted_broker) - len(missing)
        rows = []
        try:
            for day in missing:
                rows.extend(await client.broker_summary(symbol, day))
        except Exception as exc:  # noqa: BLE001 — surface the resume point, never wedge
            failed = symbol
            log_fn(f"  {symbol}: FAILED ({exc}) — re-run resumes here")
            if rows:
                broker_inserted += store.write_broker_net(rows)
            break
        if rows:
            broker_inserted += store.write_broker_net(rows)

    report = ResearchBackfillReport(
        symbols=tuple(syms),
        start=start,
        end=end,
        stride_days=stride_days,
        trading_days=len(trading_days),
        decision_days=tuple(decision_days),
        broker_days=tuple(wanted_broker),
        bars_inserted=bars_inserted,
        broker_rows_inserted=broker_inserted,
        broker_days_skipped_cached=skipped_cached,
        failed_symbol=failed,
        caveats=(
            "broker_net is sparse by design — valid only for premise tests at this stride.",
            "Bars are complete; do not reuse this store for the live pipeline.",
        ),
    )
    log_fn(report.warning)
    return report


def _weekdays_between(start: Date, end: Date) -> list[Date]:
    from datetime import timedelta

    out, d = [], start
    while d <= end:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out
