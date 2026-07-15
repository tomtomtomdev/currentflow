"""The cadence surface — the ONE thing you edit to retune the scheduler (slice 12).

Declarative only: a `FeedSchedule` binds a feed key to a cadence and a scope. It carries no
behaviour — `runner.py` owns the dispatch from feed key → the existing ingest surface. Adding
or retuning a feed is a one-line edit to `FEED_SCHEDULES` here.

Cadence kinds (a small tagged union of frozen dataclasses):
  * `DailyAt(at, prior_trading_day)`  — fires once per day at/after `at`. When
    `prior_trading_day` is set the feed fetches the PRIOR completed trading day (EOD feeds
    publish ~16:15, after the window closes, so a 09:00 fire gets yesterday — the conservative
    look-ahead stamp already in force, LD-5).
  * `WeeklyAt(weekday, at)`           — fires once per week on `weekday` (0=Mon) at/after `at`.
  * `Interval(minutes, session_only)` — fires every `minutes`; `session_only` bounds it to the
    trading-hours window (see `calendar.applies_now`).

Scope resolves WHICH symbols a fire covers:
  * `UNIVERSE`        — the latest cached SCR-0 survivor set (`store.scr0_universe`).
  * `ARMED_WATCHLIST` — ARMED + WATCH names only (bounded, paywall-safe) — used by the deferred
                        intraday status feed.
  * `NONE`            — market-wide, no per-symbol iteration (screener / special-board).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import time
from enum import Enum

from currentflow import config


class Scope(str, Enum):
    UNIVERSE = "UNIVERSE"
    ARMED_WATCHLIST = "ARMED_WATCHLIST"
    NONE = "NONE"


@dataclass(frozen=True, slots=True)
class DailyAt:
    at: time
    prior_trading_day: bool = False


@dataclass(frozen=True, slots=True)
class WeeklyAt:
    weekday: int  # 0 = Monday … 6 = Sunday (datetime.weekday())
    at: time


@dataclass(frozen=True, slots=True)
class Interval:
    minutes: int
    session_only: bool = True


@dataclass(frozen=True, slots=True)
class MonthlyAt:
    day: int  # day of month (clamped to the month's length); fires once per month at/after `at`
    at: time


Cadence = DailyAt | WeeklyAt | Interval | MonthlyAt


@dataclass(frozen=True, slots=True)
class FeedSchedule:
    feed: str  # unique key: the scheduler_runs.feed audit key AND the runner dispatch key
    cadence: Cadence
    scope: Scope


# --- Feed keys ---------------------------------------------------------------------
# Each key maps to one dispatch action in `runner._ACTIONS`.
FEED_EOD_INGEST = "eod_ingest"            # broker_summary + ohlcv_foreign (one ingest_universe pass)
FEED_UNIVERSE_SCREENER = "universe_screener"  # run_scr0 → scr0_eligible
FEED_INDEX_MEMBERSHIP = "index_membership"    # symbol_info.indexes → refresh_membership
FEED_KSEI_OWNERSHIP = "ksei_ownership"        # ksei_ownership → store.write_ksei_ownership
FEED_FAST_MODE = "fast_mode_autotrade"        # LD-11 auto paper-trade step (validation.fast_mode)
FEED_PATTERN_OOS_ACCRUAL = "pattern_oos_accrual"  # LD-14 catalog OOS accrual (cache-only)

MON = 0

# The default cadence table. Ordered so market-wide / universe-refresh feeds run before the
# per-symbol feeds within a single tick (the "screener → cached universe → per-symbol" ordering).
# NOTE the EOD ingest at 09:00 uses the PRIOR screener's cached universe; the 09:05 screener
# refreshes it for the next day (a one-day lag by design — steady state, paywall-safe).
FEED_SCHEDULES: tuple[FeedSchedule, ...] = (
    # Universe refresh (market-wide screener POST).
    FeedSchedule(FEED_UNIVERSE_SCREENER, DailyAt(config.SCHEDULER_SCREENER_TIME), Scope.NONE),
    # EOD per-symbol ingest over the cached universe — broker + OHLCV in one ingest_universe
    # pass (ingest_symbol fetches both atomically), prior completed trading day.
    FeedSchedule(
        FEED_EOD_INGEST,
        DailyAt(config.SCHEDULER_EOD_TIME, prior_trading_day=True),
        Scope.UNIVERSE,
    ),
    # Weekly rosters (Monday morning): index membership (§3 Track source) + KSEI ownership.
    FeedSchedule(FEED_INDEX_MEMBERSHIP, WeeklyAt(MON, config.SCHEDULER_EOD_TIME), Scope.UNIVERSE),
    FeedSchedule(FEED_KSEI_OWNERSHIP, WeeklyAt(MON, config.SCHEDULER_EOD_TIME), Scope.UNIVERSE),
    # Fast Mode (LD-11) auto paper-trade step — LAST, at 09:10, so it reads the day's freshly
    # ingested bars/broker (EOD 09:00) + refreshed universe (09:05). Candidate pool = the SCR-0
    # universe; the ARMED filter + fast entry (§6) run inside the step at the look-ahead-safe
    # decision_ts. Unlike every feed above this one SCORES + auto-executes (paper) — it is a no-op
    # unless the operator has armed `fast_mode_state`. Prior completed trading day (like EOD).
    FeedSchedule(
        FEED_FAST_MODE,
        DailyAt(config.SCHEDULER_FAST_MODE_TIME, prior_trading_day=True),
        Scope.UNIVERSE,
    ),
    # Pattern-catalog OOS accrual (LD-14, slice 21) — MONTHLY, cache-only. Appends new
    # instances + resolves outcomes forward of the holdout seam; it reads the store only
    # (never a network call, never scores/gates/arms — P1/P2). Full re-estimation happens
    # only on a seam move (manual, REGIME.md §3), not here. Scope NONE: it walks the PIT
    # universe itself per day rather than a single cached symbol set.
    FeedSchedule(
        FEED_PATTERN_OOS_ACCRUAL,
        MonthlyAt(1, config.SCHEDULER_EOD_TIME),
        Scope.NONE,
    ),
)

# --- Deferred feeds (no cache sink yet) --------------------------------------------
# The plan's cadence table lists three more feeds, but each lacks a persistence path today,
# so wiring them would either invent a store table or change how a RULE-A gate input is
# sourced — outside this cache-only infra slice. They are NOT scheduled (no silent caps: this
# is the documented reason). Adding one is a one-line FEED_SCHEDULES entry + a dispatch action
# once its store table lands.
#
#   corp_actions        DAILY_AT(09:00) UNIVERSE        — consumed today as an INJECTED input to
#                                                          the universe gate (universe/gate.py), not
#                                                          cached. Caching it + having the gate read
#                                                          the cache would touch RULE-A input sourcing.
#   special_board       DAILY_AT(09:00) NONE            — DAL method + parser exist; no consumer reads
#                                                          it from a cache and no table exists yet.
#   symbol_info(status) INTERVAL(15m, session) ARMED_WL — suspend/UMA/notation flags; only
#                                                          `symbol_info.indexes` (membership) is
#                                                          persisted today (via symbol_index). The
#                                                          status flags have no sink/consumer yet.
#
# `Interval` + `Scope.ARMED_WATCHLIST` are fully implemented and tested for when the status feed
# lands; they simply have no default entry above.
DEFERRED_FEEDS: tuple[str, ...] = ("corp_actions", "special_board", "symbol_status")
