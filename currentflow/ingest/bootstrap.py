"""First-run bootstrap: SCR-0 eligible universe → initial ingest (slice 13).

The terminal reads only from the local store, and login never pulls data — so a
fresh machine lands authenticated in front of empty modules. This module closes
that gap: resolve the eligible universe with the SCR-0 server-side screener, then
ingest OHLCV + broker data for every survivor (ingest-once, so any interrupted or
repeated run is a cheap resume, never a re-pull).

Pure/async and Streamlit-free: the UI (`ui/app.py:_maybe_bootstrap`) drives it on
the session event loop with a progress callback; tests drive it with a scripted
transport + in-memory store. The caller owns the client/transport lifecycle.

Error policy: `AuthError` always propagates (fail loud — the operator must
re-login). Any other `ExodusError` is captured into the returned summary — the
screener failing yields an empty universe, a symbol failing stops the loop but
keeps everything already ingested — so a partial first pull degrades to the
existing "run the ingest pipeline first" state instead of bricking the terminal.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date as Date
from datetime import datetime, timedelta
from typing import Callable

from currentflow.dal.client import ExodusClient
from currentflow.dal.errors import AuthError, ExodusError
from currentflow.ingest.pipeline import IngestResult, ingest_symbol, refresh_membership
from currentflow.screeners.scr0 import run_scr0
from currentflow.store.db import Store

log = logging.getLogger(__name__)

DEFAULT_DAYS = 90  # single source of truth — the ingest CLI imports this back


@dataclass(frozen=True, slots=True)
class BootstrapProgress:
    """One progress event: the screener stage, or one symbol's pre/post-fetch tick.

    `result` is None on the pre-fetch event and carries the `IngestResult` on the
    post-fetch event for the same (index, symbol).
    """

    stage: str  # "screener" | "ingest"
    index: int  # 0-based symbol index; 0 during the screener stage
    total: int  # universe size; 0 until the screener has run
    symbol: str | None = None
    result: IngestResult | None = None


@dataclass(frozen=True, slots=True)
class BootstrapSummary:
    """Outcome of one bootstrap run. `error is None` ⇒ the run completed; a set
    `error` with `failed_symbol=None` means the screener itself failed."""

    trading_day: Date
    start: Date
    end: Date
    eligible: list[str] = field(default_factory=list)
    results: list[IngestResult] = field(default_factory=list)
    failed_symbol: str | None = None
    error: str | None = None


def last_weekday(day: Date) -> Date:
    """`day` if Mon–Fri, else back to Friday (the repo's weekday trading-calendar
    proxy — see `pipeline._weekdays`). SCR-0 runs "as of now" server-side; this
    only keys the `scr0_eligible` cache row."""
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


async def bootstrap_ingest(
    client: ExodusClient,
    store: Store,
    *,
    now: datetime,
    days: int = DEFAULT_DAYS,
    on_progress: Callable[[BootstrapProgress], None] | None = None,
) -> BootstrapSummary:
    """Resolve the SCR-0 universe, then ingest [end − days, end] for each survivor.

    Sequential by design (one operator session; the shared retry/backoff paces the
    endpoint). `now` is stamped once so every `as_of` in the run is consistent.
    """
    trading_day = last_weekday(now.date())
    end = now.date()
    start = end - timedelta(days=days)

    def emit(event: BootstrapProgress) -> None:
        if on_progress is not None:
            on_progress(event)

    emit(BootstrapProgress(stage="screener", index=0, total=0))
    try:
        rows = await run_scr0(client, store, trading_day=trading_day, now=now)
    except AuthError:
        raise  # fail loud — subclass of ExodusError, so catch it first
    except ExodusError as exc:
        log.error("bootstrap: SCR-0 screener failed (%s) — nothing ingested", exc)
        return BootstrapSummary(trading_day, start, end, error=str(exc))

    symbols = [r.symbol for r in rows]
    if not symbols:
        log.warning(
            "bootstrap: SCR-0 returned 0 eligible names for %s — nothing ingested",
            trading_day,
        )
        return BootstrapSummary(trading_day, start, end)

    results: list[IngestResult] = []
    total = len(symbols)
    for i, symbol in enumerate(symbols):
        emit(BootstrapProgress(stage="ingest", index=i, total=total, symbol=symbol))
        try:
            result = await ingest_symbol(client, store, symbol, start, end, now=now)
        except AuthError:
            raise
        except ExodusError as exc:
            # No silent caps: name the casualty and how much of the universe is left.
            log.error(
                "bootstrap: ingest failed at %s (%s) — %d/%d symbols ingested, "
                "%d not attempted (ingest-once: a retry resumes here)",
                symbol, exc, len(results), total, total - i,
            )
            return BootstrapSummary(
                trading_day, start, end,
                eligible=symbols, results=results,
                failed_symbol=symbol, error=str(exc),
            )
        if result.coverage.has_gaps:
            log.warning(
                "bootstrap: %s has gaps on %d day(s) (missing ≠ zero)",
                symbol, len(result.coverage.gaps),
            )
        if result.has_imbalance:
            log.warning(
                "bootstrap: %s broker feed does not clear on %d day(s) — "
                "truncated/dropped rows (caught at ingest, not on screen)",
                symbol, len(result.unclear),
            )
        results.append(result)
        emit(
            BootstrapProgress(
                stage="ingest", index=i, total=total, symbol=symbol, result=result
            )
        )

    # §3 Track source: a separate phase after every name is ingested — snapshot index
    # membership for the offline watchlist. Overlay, not a gate: a per-name failure is
    # tolerated (missing roster → Track B), but a dead session (AuthError) fails loud.
    for symbol in (r.symbol for r in results):
        try:
            await refresh_membership(client, store, symbol, now=now)
        except AuthError:
            raise
        except ExodusError as exc:
            log.warning(
                "bootstrap: %s index membership fetch failed (%s) — resolves to Track B",
                symbol, exc,
            )

    return BootstrapSummary(trading_day, start, end, eligible=symbols, results=results)
