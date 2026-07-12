"""Trading-hours gate + pure due-math for the scheduler (slice 12).

Everything here is a pure function of its arguments (the clock is passed in as `now`), so
daily/weekly/interval decisions are deterministic and unit-testable without a real loop.

`is_due` / `next_fire` are pure SCHEDULE math — they ignore weekends and the window. The
weekday/window applicability is a SEPARATE gate (`applies_now`); the runner requires BOTH
(`is_due(...) and applies_now(...)`) before firing. Keeping them separate means a feed whose
scheduled instant lands on a weekend simply waits: it becomes due, `applies_now` blocks it
through the weekend, and it fires on the next weekday (fetching the prior completed trading day),
without ever double-firing.

IDX holidays are a known gap for now: a fire on a holiday finds no new data (ingest-once no-op)
and is logged. A `holidays.txt` calendar is a documented deferral.
"""

from __future__ import annotations

from datetime import date as Date
from datetime import datetime, timedelta

from currentflow import config
from currentflow.scheduler.schedule import Cadence, DailyAt, Interval, WeeklyAt


def is_trading_time(now: datetime) -> bool:
    """Mon–Fri within [SCHEDULER_WINDOW_OPEN, SCHEDULER_WINDOW_CLOSE] (WIB, inclusive).
    Weekends are always out. Holidays are not modelled (known gap)."""
    if now.weekday() >= 5:  # Saturday / Sunday
        return False
    return config.SCHEDULER_WINDOW_OPEN <= now.time() <= config.SCHEDULER_WINDOW_CLOSE


def applies_now(cadence: Cadence, now: datetime) -> bool:
    """Trading-hours applicability gate for a cadence at `now`.

    All cadences respect Mon–Fri and the session window, EXCEPT an `Interval` explicitly
    marked `session_only=False` (which may fire any weekday, any time). No feed ever fires on
    a weekend."""
    if isinstance(cadence, Interval) and not cadence.session_only:
        return now.weekday() < 5
    return is_trading_time(now)


def previous_trading_day(day: Date) -> Date:
    """The most recent weekday strictly BEFORE `day` (the repo's Mon–Fri trading-calendar
    proxy). On Monday → Friday; on Tuesday → Monday. EOD feeds fire in the morning window and
    ingest this day (yesterday's completed session)."""
    d = day - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def _daily_next(cadence: DailyAt, last_run: datetime | None, now: datetime) -> datetime:
    if last_run is None:
        # Never fired: anchored to today's scheduled time. Due iff now has reached it.
        return datetime.combine(now.date(), cadence.at)
    # Next scheduled instant strictly after the last fire.
    same_day = datetime.combine(last_run.date(), cadence.at)
    if same_day > last_run:
        return same_day
    return datetime.combine(last_run.date() + timedelta(days=1), cadence.at)


def _weekly_occurrence(ref: Date, weekday: int, at) -> datetime:
    """The scheduled instant on `weekday` in the ISO week containing `ref`."""
    monday = ref - timedelta(days=ref.weekday())
    return datetime.combine(monday + timedelta(days=weekday), at)


def _weekly_next(cadence: WeeklyAt, last_run: datetime | None, now: datetime) -> datetime:
    if last_run is None:
        # Never fired: this week's occurrence. Past → due now (catch up); future → wait for it.
        return _weekly_occurrence(now.date(), cadence.weekday, cadence.at)
    occ = _weekly_occurrence(last_run.date(), cadence.weekday, cadence.at)
    while occ <= last_run:
        occ += timedelta(days=7)
    return occ


def _interval_next(cadence: Interval, last_run: datetime | None, now: datetime) -> datetime:
    if last_run is None:
        return now  # due immediately (still subject to the window gate in the runner)
    return last_run + timedelta(minutes=cadence.minutes)


def next_fire(cadence: Cadence, last_run: datetime | None, now: datetime) -> datetime:
    """The next instant at which `cadence` becomes due, given it last fired at `last_run`
    (None = never). Pure schedule math — ignores weekends/window (see `applies_now`)."""
    if isinstance(cadence, DailyAt):
        return _daily_next(cadence, last_run, now)
    if isinstance(cadence, WeeklyAt):
        return _weekly_next(cadence, last_run, now)
    if isinstance(cadence, Interval):
        return _interval_next(cadence, last_run, now)
    raise TypeError(f"unknown cadence: {cadence!r}")


def is_due(cadence: Cadence, last_run: datetime | None, now: datetime) -> bool:
    """True when `now` has reached the cadence's next scheduled instant."""
    return now >= next_fire(cadence, last_run, now)
