"""Automated ingestion scheduler (slice 12).

Pure due-math + trading-hours gate + the runner's tick, all against scripted transports and
an in-memory store (no network, no launchd). Covers the six acceptance behaviors from PLAN.md
slice 12: next_fire due-math, the trading-hours gate, EOD prior-trading-day, no-double-fire
after restart, the ingest-once zero-calls invariant, and 401 fail-loud — plus scope resolution
and the documented deferred-feed scope decision.
"""

from __future__ import annotations

from datetime import date as Date
from datetime import datetime, time, timedelta

import pytest

from currentflow.dal.client import ExodusClient
from currentflow.dal.errors import AuthError
from currentflow.dal.models import Scr0Row
from currentflow.scheduler import calendar as cal
from currentflow.scheduler import runner
from currentflow.scheduler.runner import (
    OUTCOME_EMPTY,
    OUTCOME_OK,
    resolve_scope,
    run_loop,
    tick,
)
from currentflow.scheduler.schedule import (
    DEFERRED_FEEDS,
    FEED_EOD_INGEST,
    FEED_SCHEDULES,
    FEED_UNIVERSE_SCREENER,
    DailyAt,
    FeedSchedule,
    Interval,
    Scope,
    WeeklyAt,
)
from tests.conftest import broker_payload, ohlcv_payload, scripted_transport

# July 2026: 07-01 Wed … 07-03 Fri, 07-04 Sat, 07-05 Sun, 07-06 Mon, 07-07 Tue, 07-08 Wed.
MON = datetime(2026, 7, 6, 9, 0)
FRI = Date(2026, 7, 3)  # previous_trading_day(MON)
NINE = time(9, 0)


# --- helpers -----------------------------------------------------------------------


def _client(get_steps: list, post_steps: list | None = None, get_calls: list | None = None,
            post_calls: list | None = None):
    return ExodusClient(
        scripted_transport(get_steps, get_calls),
        post_transport=scripted_transport(post_steps or [], post_calls),
    )


def _bar(date_iso: str, close: float = 100.0) -> dict:
    return {
        "date": date_iso, "open": 100, "high": 101, "low": 99, "close": close,
        "volume": 1000, "value": 100000, "frequency": 10, "average": 100.0,
        "foreign_buy": 1, "foreign_sell": 0, "net_foreign": 1, "change_percentage": 0.1,
    }


def _scr0_payload(symbols: list[str]) -> dict:
    return {"data": {"calcs": [
        {"symbol": s, "results": [{"item": 2661, "raw": 4500}]} for s in symbols
    ]}}


def _seed_universe(store, symbols: list[str], *, as_of: datetime) -> None:
    """Pre-populate the SCR-0 survivor set (a prior screener run) so UNIVERSE-scoped feeds
    have names. `as_of` must be < the tick's `now` (the look-ahead firewall)."""
    store.write_scr0_eligible([
        Scr0Row(symbol=s, date=as_of.date(), as_of=as_of,
                adv20=5e10, price=1000.0, free_float=0.4, market_cap=1e12)
        for s in symbols
    ])


def _eod_schedule() -> tuple[FeedSchedule, ...]:
    return (FeedSchedule(FEED_EOD_INGEST, DailyAt(NINE, prior_trading_day=True), Scope.UNIVERSE),)


# =========================================================================================
# 1. next_fire due-math (daily / weekly / interval) — pure, pinned clock
# =========================================================================================


def test_next_fire_daily():
    c = DailyAt(NINE)
    # Never fired: anchored to today at 09:00.
    assert not cal.is_due(c, None, datetime(2026, 7, 6, 8, 0))   # before → wait
    assert cal.is_due(c, None, datetime(2026, 7, 6, 10, 0))      # after → catch up
    # Already fired today at 09:00 → next is tomorrow 09:00.
    last = datetime(2026, 7, 6, 9, 0)
    assert not cal.is_due(c, last, datetime(2026, 7, 6, 15, 0))  # same day → no re-fire
    assert cal.is_due(c, last, datetime(2026, 7, 7, 9, 0))       # next day 09:00 → due
    assert cal.next_fire(c, last, datetime(2026, 7, 6, 15, 0)) == datetime(2026, 7, 7, 9, 0)


def test_next_fire_weekly():
    c = WeeklyAt(0, NINE)  # Monday 09:00
    # Never fired: this week's Monday. Past (or on) → due; future → wait.
    assert cal.is_due(c, None, datetime(2026, 7, 6, 10, 0))      # Mon after 09:00 → due
    assert cal.is_due(c, None, datetime(2026, 7, 8, 10, 0))      # Wed → still catches Monday
    assert not cal.is_due(c, None, datetime(2026, 7, 6, 8, 0))   # Mon before 09:00 → wait
    # Fired this Monday → next is next Monday.
    last = datetime(2026, 7, 6, 9, 0)
    assert not cal.is_due(c, last, datetime(2026, 7, 8, 12, 0))  # mid-week → no re-fire
    assert cal.is_due(c, last, datetime(2026, 7, 13, 9, 0))      # next Monday → due
    assert cal.next_fire(c, last, datetime(2026, 7, 8, 12, 0)) == datetime(2026, 7, 13, 9, 0)


def test_next_fire_interval():
    c = Interval(15)
    assert cal.is_due(c, None, MON)                              # never fired → due now
    last = datetime(2026, 7, 6, 10, 0)
    assert not cal.is_due(c, last, datetime(2026, 7, 6, 10, 10))  # 10 min < 15 → wait
    assert cal.is_due(c, last, datetime(2026, 7, 6, 10, 15))      # 15 min → due
    assert cal.next_fire(c, last, MON) == datetime(2026, 7, 6, 10, 15)


# =========================================================================================
# 2. trading-hours gate: weekends, outside-window, session_only intervals
# =========================================================================================


def test_is_trading_time_window_and_weekends():
    assert not cal.is_trading_time(datetime(2026, 7, 4, 12, 0))   # Saturday
    assert not cal.is_trading_time(datetime(2026, 7, 5, 12, 0))   # Sunday
    assert not cal.is_trading_time(datetime(2026, 7, 6, 8, 59))   # before 09:00
    assert cal.is_trading_time(datetime(2026, 7, 6, 9, 0))        # 09:00 inclusive
    assert cal.is_trading_time(datetime(2026, 7, 6, 16, 0))       # 16:00 inclusive
    assert not cal.is_trading_time(datetime(2026, 7, 6, 16, 1))   # after close
    assert cal.is_trading_time(datetime(2026, 7, 8, 12, 0))       # Wed midday


def test_applies_now_session_only_interval():
    session = Interval(15, session_only=True)
    always = Interval(15, session_only=False)
    daily = DailyAt(NINE)
    # session_only interval respects the window; a non-session interval fires outside it…
    assert cal.applies_now(session, datetime(2026, 7, 6, 12, 0))
    assert not cal.applies_now(session, datetime(2026, 7, 6, 17, 0))
    assert cal.applies_now(always, datetime(2026, 7, 6, 17, 0))
    # …but NOTHING fires on a weekend, session_only or not.
    assert not cal.applies_now(session, datetime(2026, 7, 4, 12, 0))
    assert not cal.applies_now(always, datetime(2026, 7, 4, 12, 0))
    assert not cal.applies_now(daily, datetime(2026, 7, 4, 9, 0))   # Saturday EOD → blocked


def test_previous_trading_day():
    assert cal.previous_trading_day(Date(2026, 7, 6)) == Date(2026, 7, 3)  # Mon → Fri
    assert cal.previous_trading_day(Date(2026, 7, 7)) == Date(2026, 7, 6)  # Tue → Mon


# =========================================================================================
# 3. EOD feed fetches the PRIOR completed trading day at 09:00
# =========================================================================================


async def test_eod_fetches_prior_trading_day(store):
    _seed_universe(store, ["BBCA"], as_of=MON - timedelta(hours=1))
    get_calls: list = []
    client = _client(
        get_steps=[(200, broker_payload([], [])), (200, ohlcv_payload([_bar(FRI.isoformat())]))],
        get_calls=get_calls,
    )

    fired = await tick(client, store, now=MON, schedules=_eod_schedule())

    assert [f.outcome for f in fired] == [OUTCOME_OK]
    # The client was asked for Friday (yesterday's completed session), not Monday.
    broker_path, broker_params = get_calls[0]
    assert broker_path == "marketdetectors/BBCA"
    assert broker_params["from"] == FRI.isoformat() == broker_params["to"]
    _, ohlcv_params = get_calls[1]
    assert ohlcv_params["start_date"] == FRI.isoformat() == ohlcv_params["end_date"]
    # And Friday's bar landed in the store the terminal reads.
    assert store.ingested_dates("BBCA") == {FRI}


# =========================================================================================
# 4. Durable state: a feed already fired today is skipped after a restart (no double-fire)
# =========================================================================================


async def test_no_double_fire_after_restart(store):
    _seed_universe(store, ["BBCA"], as_of=MON - timedelta(hours=1))
    first = _client(
        get_steps=[(200, broker_payload([], [])), (200, ohlcv_payload([_bar(FRI.isoformat())]))]
    )
    fired = await tick(first, store, now=datetime(2026, 7, 6, 9, 1), schedules=_eod_schedule())
    assert [f.feed for f in fired] == [FEED_EOD_INGEST]
    run = store.read_scheduler_run_latest(FEED_EOD_INGEST)
    assert run is not None and run.outcome == OUTCOME_OK

    # "Restart" later the same morning: a fresh runner, same store. The empty transport would
    # StopIteration on any call — so a call means a wrongful re-fire.
    get_calls: list = []
    restart = _client(get_steps=[], get_calls=get_calls)
    fired2 = await tick(restart, store, now=datetime(2026, 7, 6, 9, 30), schedules=_eod_schedule())
    assert fired2 == []
    assert get_calls == []


# =========================================================================================
# 5. Ingest-once invariant under the scheduler: a second fire makes ZERO network calls
# =========================================================================================


async def test_ingest_once_zero_calls_on_second_fire(store):
    universe = ["BBCA"]
    _seed_universe(store, universe, as_of=MON - timedelta(hours=1))
    # First fire populates Friday.
    populated = _client(
        get_steps=[(200, broker_payload([], [])), (200, ohlcv_payload([_bar(FRI.isoformat())]))]
    )
    rows1, outcome1, _ = await runner._act_eod_ingest(populated, store, universe, now=MON)
    assert outcome1 == OUTCOME_OK and rows1 >= 1

    # Second fire for the SAME prior day: everything is already cached → no calls at all.
    calls: list = []
    empty = _client(get_steps=[], get_calls=calls)
    rows2, outcome2, _ = await runner._act_eod_ingest(empty, store, universe, now=MON)
    assert calls == []            # ingest-once: nothing re-pulled
    assert rows2 == 0
    assert outcome2 == OUTCOME_OK


# =========================================================================================
# 6. A 401 during a scheduled fire FAILS LOUD (propagates; nothing recorded → stays due)
# =========================================================================================


async def test_401_fails_loud(store):
    _seed_universe(store, ["BBCA"], as_of=MON - timedelta(hours=1))
    client = _client(get_steps=[401])  # broker_summary GET → 401

    with pytest.raises(AuthError):
        await tick(client, store, now=MON, schedules=_eod_schedule())

    # Fail loud, not silent: no run recorded, so the feed stays due for a retry post-relogin.
    assert store.read_scheduler_run_latest(FEED_EOD_INGEST) is None


# =========================================================================================
# scope resolution + universe-refresh + the documented deferred-feed scope decision
# =========================================================================================


def test_resolve_scope(store):
    _seed_universe(store, ["BBRI", "BBCA"], as_of=MON - timedelta(hours=1))
    assert resolve_scope(store, Scope.UNIVERSE, MON) == ["BBCA", "BBRI"]  # sorted
    assert resolve_scope(store, Scope.NONE, MON) == []


async def test_universe_screener_refreshes_scr0(store):
    post_calls: list = []
    client = _client(get_steps=[], post_steps=[(200, _scr0_payload(["BBCA", "BBRI"]))],
                     post_calls=post_calls)
    screener = (FeedSchedule(FEED_UNIVERSE_SCREENER, DailyAt(time(9, 5)), Scope.NONE),)

    fired = await tick(client, store, now=datetime(2026, 7, 6, 9, 6), schedules=screener)

    assert [f.outcome for f in fired] == [OUTCOME_OK]
    assert len(post_calls) == 1
    # The refreshed survivor set is now readable as the universe for the next EOD fire.
    later = datetime(2026, 7, 6, 9, 7)
    assert store.scr0_universe(later) == ["BBCA", "BBRI"]


async def test_empty_universe_is_skipped_not_errored(store):
    # No screener has run → UNIVERSE resolves empty. The EOD feed must not invent a universe;
    # it records SKIPPED_EMPTY and makes no calls (missing ≠ zero).
    calls: list = []
    client = _client(get_steps=[], get_calls=calls)
    fired = await tick(client, store, now=MON, schedules=_eod_schedule())
    assert [f.outcome for f in fired] == [OUTCOME_EMPTY]
    assert calls == []


def test_every_scheduled_feed_has_an_action():
    # No FEED_SCHEDULES entry may point at a missing dispatch action.
    for sched in FEED_SCHEDULES:
        assert sched.feed in runner._ACTIONS


def test_deferred_feeds_are_documented_and_unscheduled():
    # The three feeds without a cache sink are named (no silent caps) and NOT scheduled.
    assert DEFERRED_FEEDS == ("corp_actions", "special_board", "symbol_status")
    scheduled = {s.feed for s in FEED_SCHEDULES}
    assert scheduled.isdisjoint(DEFERRED_FEEDS)


# =========================================================================================
# daemon loop (run_loop): bounded run, injected client (own_client=False → transport kept)
# =========================================================================================


async def test_run_loop_single_tick(store):
    _seed_universe(store, ["BBCA"], as_of=MON - timedelta(hours=1))
    client = _client(
        get_steps=[(200, broker_payload([], [])), (200, ohlcv_payload([_bar(FRI.isoformat())]))]
    )
    rc = await run_loop(
        store, clock=lambda: MON, tick_seconds=0, schedules=_eod_schedule(),
        client=client, max_ticks=1,
    )
    assert rc == 0
    assert store.read_scheduler_run_latest(FEED_EOD_INGEST).outcome == OUTCOME_OK
    assert store.ingested_dates("BBCA") == {FRI}


async def test_run_loop_halts_on_auth_failure(store):
    _seed_universe(store, ["BBCA"], as_of=MON - timedelta(hours=1))
    client = _client(get_steps=[401])
    rc = await run_loop(
        store, clock=lambda: MON, tick_seconds=0, schedules=_eod_schedule(),
        client=client, max_ticks=1,
    )
    assert rc == 1  # fail loud → non-zero exit
    assert store.read_scheduler_run_latest(FEED_EOD_INGEST) is None
