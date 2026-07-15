"""Slice 21 — pattern-catalog scheduler feed (MonthlyAt cadence + accrual action)."""

from __future__ import annotations

from datetime import date as Date
from datetime import datetime, time

import pytest

from currentflow.patterns.accrual import accrue_oos, seed_catalog
from currentflow.scheduler import calendar as cal
from currentflow.scheduler import runner
from currentflow.scheduler.schedule import (
    FEED_PATTERN_OOS_ACCRUAL,
    FEED_SCHEDULES,
    MonthlyAt,
    Scope,
)

NOW = datetime(2026, 7, 1, 9, 0)


def test_monthly_cadence_due_math():
    cadence = MonthlyAt(1, time(9, 0))
    # Never fired: due once now has reached this month's occurrence.
    assert cal.is_due(cadence, None, datetime(2026, 7, 1, 9, 0)) is True
    assert cal.is_due(cadence, None, datetime(2026, 7, 1, 8, 0)) is False
    # Fired this month → next fire is the 1st of next month.
    last = datetime(2026, 7, 1, 9, 0)
    assert cal.is_due(cadence, last, datetime(2026, 7, 20, 9, 0)) is False
    assert cal.next_fire(cadence, last, datetime(2026, 7, 20, 9, 0)) == datetime(2026, 8, 1, 9, 0)
    assert cal.is_due(cadence, last, datetime(2026, 8, 1, 9, 0)) is True


def test_monthly_day_clamped_to_month_length():
    cadence = MonthlyAt(31, time(9, 0))  # clamps to 28/29/30 as needed
    assert cal.next_fire(cadence, None, datetime(2026, 2, 10, 9, 0)) == datetime(2026, 2, 28, 9, 0)


def test_feed_registered_and_dispatchable():
    feeds = {f.feed for f in FEED_SCHEDULES}
    assert FEED_PATTERN_OOS_ACCRUAL in feeds
    sched = next(f for f in FEED_SCHEDULES if f.feed == FEED_PATTERN_OOS_ACCRUAL)
    assert isinstance(sched.cadence, MonthlyAt)
    assert sched.scope is Scope.NONE
    assert FEED_PATTERN_OOS_ACCRUAL in runner._ACTIONS


async def test_accrual_action_is_cache_only_and_empty_without_data(store):
    # No bars → nothing estimable, but the seeds are still established (cache-only).
    rows, outcome, detail = await runner._act_pattern_oos(None, store, [], now=NOW)
    assert outcome == runner.OUTCOME_EMPTY
    assert rows == 0
    assert len(store.read_pattern_catalog()) == 6  # six DEFINED seeds seeded


def test_seed_catalog_is_idempotent(store):
    assert seed_catalog(store, now=NOW) == 6
    seed_catalog(store, now=NOW)
    ids = {r.pattern_id for r in store.read_pattern_catalog()}
    assert len(ids) == 6  # no duplicate versions
