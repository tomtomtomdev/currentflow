"""The scheduler loop (slice 12): tick → find due feeds → run them through the EXISTING
ingest surface → record durable run-state.

Most feeds only fill the DuckDB cache the calc engine reads — they don't score or rank. The
**one exception is the LD-11 Fast Mode feed** (`_act_fast_mode`), which scores + auto-executes
(paper) the ARMED watchlist; it still honours RULE A (the phase gate runs in `engine.evaluate`)
and RULE B (it feeds the `ValidationLedger`, never displays a number), and it is a no-op unless
the operator has armed `fast_mode_state`. All the look-ahead/`as_of`/ingest-once discipline lives
in the surfaces the feeds call (`ingest.pipeline`, `screeners.scr0`, `validation.fast_mode`),
unchanged.

Error policy:
  * **401 → fail loud.** An `AuthError` propagates out of the tick (the run-state row is NOT
    written, so the feed stays due) and halts the daemon — a headless process cannot do the
    interactive OTP re-login. The operator re-runs `./run.sh login`. Never a stale/empty write.
  * **Any other feed error** (paywall/rate-limit/transport, already retried+backed-off inside
    the client) is logged and recorded with outcome=ERROR, and DOES advance the run clock — so
    a persistently failing endpoint is retried on its next natural cadence, never hammered every
    tick. The missed day is recoverable via the manual `./run.sh ingest` backfill.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime

from currentflow import config
from currentflow.dal.client import ExodusClient
from currentflow.dal.errors import AuthError, ExodusError
from currentflow.ingest.bootstrap import last_weekday
from currentflow.ingest.pipeline import ingest_universe, refresh_membership
from currentflow.scheduler import calendar as cal
from currentflow.scheduler.schedule import (
    FEED_EOD_INGEST,
    FEED_FAST_MODE,
    FEED_INDEX_MEMBERSHIP,
    FEED_KSEI_OWNERSHIP,
    FEED_SCHEDULES,
    FEED_UNIVERSE_SCREENER,
    FeedSchedule,
    Scope,
)
from currentflow.screeners.scr0 import run_scr0
from currentflow.store.db import Store
from currentflow.store.schema import SchedulerRunRow

log = logging.getLogger(__name__)

# Outcome values recorded in scheduler_runs.outcome (free VARCHAR, no enum/CHECK).
OUTCOME_OK = "OK"
OUTCOME_EMPTY = "SKIPPED_EMPTY"  # ran, but the scope resolved to no symbols (missing ≠ zero)
OUTCOME_ERROR = "ERROR"


@dataclass(frozen=True, slots=True)
class FireResult:
    feed: str
    rows_written: int
    outcome: str
    detail: str = ""


# --- scope resolution --------------------------------------------------------------


def _armed_watchlist(store: Store, decision_ts: datetime) -> list[str]:
    """ARMED + WATCH names (the operator's actionable rail), bounded to the ingested roster.
    Imported lazily so the daemon's base import stays light and free of the signals stack;
    only reached if a feed is scoped ARMED_WATCHLIST (none in the default table today)."""
    from currentflow.signals import engine
    from currentflow.universe import track as track_mod

    watch = []
    for sym in store.symbols("daily_bar"):
        bars = store.read_daily_bars(sym, decision_ts)
        track = track_mod.resolve_track(store, sym, decision_ts, bars)
        result = engine.evaluate(store, sym, decision_ts, track=track)
        if result.state in (engine.EngineState.ARMED, engine.EngineState.WATCH):
            watch.append(sym)
    return watch


def resolve_scope(store: Store, scope: Scope, now: datetime) -> list[str]:
    """The symbol list a scope covers at `now`. `NONE` → [] (market-wide feeds ignore it)."""
    if scope is Scope.NONE:
        return []
    if scope is Scope.UNIVERSE:
        return store.scr0_universe(now)
    if scope is Scope.ARMED_WATCHLIST:
        return _armed_watchlist(store, now)
    raise ValueError(f"unknown scope: {scope!r}")


# --- feed actions ------------------------------------------------------------------
# Each action: async (client, store, symbols, *, now) -> (rows_written, outcome, detail).


async def _act_eod_ingest(client, store, symbols, *, now):
    if not symbols:
        log.warning(
            "scheduler %s: empty universe (no cached SCR-0 survivors yet) — nothing to ingest",
            FEED_EOD_INGEST,
        )
        return 0, OUTCOME_EMPTY, "empty universe"
    day = cal.previous_trading_day(now.date())
    results = await ingest_universe(client, store, symbols, day, day, now=now)
    rows = sum(r.bars_inserted + r.broker_rows_inserted for r in results)
    return rows, OUTCOME_OK, f"{len(symbols)} names, day {day}"


async def _act_screener(client, store, symbols, *, now):
    # Market-wide POST; `trading_day` is a storage key only (SCR-0 runs "as of now"
    # server-side). Use the same last-weekday key convention as the bootstrap so the
    # cached sets line up for `scr0_universe` read-back.
    rows = await run_scr0(client, store, trading_day=last_weekday(now.date()), now=now)
    return len(rows), OUTCOME_OK, f"{len(rows)} eligible"


async def _act_membership(client, store, symbols, *, now):
    if not symbols:
        log.warning("scheduler %s: empty universe — no rosters to refresh", FEED_INDEX_MEMBERSHIP)
        return 0, OUTCOME_EMPTY, "empty universe"
    total = 0
    for sym in symbols:
        total += await refresh_membership(client, store, sym, now=now)
    return total, OUTCOME_OK, f"{len(symbols)} names"


async def _act_ksei(client, store, symbols, *, now):
    if not symbols:
        log.warning("scheduler %s: empty universe — no ownership to refresh", FEED_KSEI_OWNERSHIP)
        return 0, OUTCOME_EMPTY, "empty universe"
    total = 0
    for sym in symbols:
        slices = await client.ksei_ownership(sym)
        total += store.write_ksei_ownership(slices)
    return total, OUTCOME_OK, f"{len(symbols)} names"


async def _act_fast_mode(client, store, symbols, *, now):
    """LD-11 Fast Mode: advance the auto paper-trade book by one trading day. Unlike every
    other feed this SCORES + auto-executes (paper) — but only when the operator has armed
    `fast_mode_state`; otherwise it is a no-op. Makes no network call (reads the freshly cached
    store), so it never raises AuthError. Look-ahead-safe: the step decides at the prior day's
    pre-open decision_ts and fills at that day's open."""
    from currentflow.universe.sectors import OPERATOR_SECTOR_MAP
    from currentflow.validation.fast_mode import run_fast_mode_step

    day = cal.previous_trading_day(now.date())
    result = run_fast_mode_step(
        store, symbols, day, sector_map=OPERATOR_SECTOR_MAP, now=now,
    )
    if not result.enabled:
        return 0, OUTCOME_EMPTY, "fast mode disarmed"
    return result.rows_written, OUTCOME_OK, result.detail


_Action = Callable[..., Awaitable[tuple[int, str, str]]]

_ACTIONS: dict[str, _Action] = {
    FEED_EOD_INGEST: _act_eod_ingest,
    FEED_UNIVERSE_SCREENER: _act_screener,
    FEED_INDEX_MEMBERSHIP: _act_membership,
    FEED_KSEI_OWNERSHIP: _act_ksei,
    FEED_FAST_MODE: _act_fast_mode,
}


# --- the tick ----------------------------------------------------------------------


async def _run_feed(
    sched: FeedSchedule, client: ExodusClient, store: Store, *, now: datetime
) -> FireResult:
    symbols = resolve_scope(store, sched.scope, now)
    action = _ACTIONS[sched.feed]
    try:
        rows, outcome, detail = await action(client, store, symbols, now=now)
        log.info("scheduler %s: %s — %d row(s) [%s]", sched.feed, outcome, rows, detail)
        return FireResult(sched.feed, rows, outcome, detail)
    except AuthError:
        # Fail loud: do NOT record (feed stays due), let it halt the daemon.
        log.error(
            "scheduler %s: AUTHENTICATION FAILED — session dead; run './run.sh login'",
            sched.feed,
        )
        raise
    except ExodusError as exc:
        # Already retried+backed-off in the client; record + advance the clock so the next
        # attempt waits for the natural cadence rather than hammering a failing endpoint.
        log.error(
            "scheduler %s: feed error %s — recorded; retries on next cadence "
            "(manual './run.sh ingest' can backfill)",
            sched.feed, type(exc).__name__,
        )
        return FireResult(sched.feed, 0, OUTCOME_ERROR, type(exc).__name__)


async def tick(
    client: ExodusClient,
    store: Store,
    *,
    now: datetime,
    schedules: tuple[FeedSchedule, ...] = FEED_SCHEDULES,
) -> list[FireResult]:
    """One scheduler pass. For each feed: fire iff it is both due (schedule math against the
    durable run-state) AND applicable now (weekday/window gate). A fire records one
    `scheduler_runs` row. An `AuthError` propagates (fail loud); nothing is recorded for it."""
    fired: list[FireResult] = []
    for sched in schedules:
        last = store.read_scheduler_run_latest(sched.feed)
        last_fired = last.last_fired_at if last else None
        if not cal.is_due(sched.cadence, last_fired, now):
            continue
        if not cal.applies_now(sched.cadence, now):
            log.debug("scheduler %s: due but outside trading window — waiting", sched.feed)
            continue
        result = await _run_feed(sched, client, store, now=now)  # may raise AuthError
        store.write_scheduler_run(
            [SchedulerRunRow(
                feed=result.feed,
                last_fired_at=now,
                rows_written=result.rows_written,
                outcome=result.outcome,
            )]
        )
        fired.append(result)
    return fired


# --- the daemon loop ---------------------------------------------------------------


async def run_loop(
    store: Store,
    *,
    clock: Callable[[], datetime] = datetime.now,
    tick_seconds: float = config.SCHEDULER_TICK_SECONDS,
    schedules: tuple[FeedSchedule, ...] = FEED_SCHEDULES,
    client: ExodusClient | None = None,
    transport=None,
    max_ticks: int | None = None,
) -> int:
    """Run the scheduler until interrupted (or `max_ticks` for a bounded run/test).

    Builds the live client itself when not injected (401 fails loud — no interactive re-login
    in a headless daemon). Returns 0 on a clean stop, 1 if authentication failed."""
    from currentflow.dal.session import build_live_client

    own_client = client is None
    if own_client:
        client, transport = build_live_client()

    ticks = 0
    try:
        while max_ticks is None or ticks < max_ticks:
            now = clock()
            try:
                await tick(client, store, now=now, schedules=schedules)
            except AuthError:
                log.error(
                    "scheduler halted — authentication failed; run './run.sh login' to "
                    "re-establish the session, then restart the scheduler"
                )
                return 1
            ticks += 1
            if max_ticks is not None and ticks >= max_ticks:
                break
            await asyncio.sleep(tick_seconds)
    finally:
        if own_client and transport is not None:
            await transport.aclose()
    return 0
