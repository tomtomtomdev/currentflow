"""Slice 20 §17.4 — point-in-time roster loader acceptance tests."""

from __future__ import annotations

from datetime import date as Date
from datetime import datetime

import pytest

from currentflow.universe import roster
from currentflow.universe.roster import RosterValidationError

NOW = datetime(2026, 7, 15, 9, 0)

_GOOD = (
    "index_name,symbol,effective_from,effective_to,source\n"
    "LQ45,BBCA,2024-02-01,2024-07-31,IDX-PENG-001/2024\n"
    "LQ45,BBCA,2024-08-01,,IDX-PENG-002/2024\n"
    "LQ45,GOTO,2024-02-01,,IDX-PENG-001/2024\n"
)


def _write(tmp_path, name, text):
    (tmp_path / name).write_text(text)


def test_loads_and_resolves_membership_by_day(tmp_path, store):
    _write(tmp_path, "lq45.csv", _GOOD)
    report = roster.load_rosters(store, tmp_path, now=NOW)
    assert report.rows_written == 3
    assert report.indexes == ("LQ45",)

    # Effective-on-day resolution across the two BBCA periods.
    assert store.read_index_roster_pit("BBCA", Date(2024, 5, 1)) == ("LQ45",)
    assert store.read_index_roster_pit("BBCA", Date(2024, 9, 1)) == ("LQ45",)
    # Before any period → not a member.
    assert store.read_index_roster_pit("BBCA", Date(2024, 1, 1)) == ()


def test_overlapping_periods_reject(tmp_path, store):
    bad = (
        "index_name,symbol,effective_from,effective_to,source\n"
        "LQ45,BBCA,2024-02-01,2024-08-31,IDX-1\n"
        "LQ45,BBCA,2024-08-01,,IDX-2\n"  # overlaps the previous period on 08-01..08-31
    )
    _write(tmp_path, "bad.csv", bad)
    with pytest.raises(RosterValidationError, match="overlapping"):
        roster.load_rosters(store, tmp_path, now=NOW)


def test_missing_source_rejects(tmp_path, store):
    bad = (
        "index_name,symbol,effective_from,effective_to,source\n"
        "LQ45,BBCA,2024-02-01,,\n"
    )
    _write(tmp_path, "bad.csv", bad)
    with pytest.raises(RosterValidationError, match="source"):
        roster.load_rosters(store, tmp_path, now=NOW)


def test_overlap_checked_across_files(tmp_path, store):
    _write(tmp_path, "a.csv",
           "index_name,symbol,effective_from,effective_to,source\n"
           "LQ45,BBCA,2024-02-01,,IDX-1\n")
    _write(tmp_path, "b.csv",
           "index_name,symbol,effective_from,effective_to,source\n"
           "LQ45,BBCA,2024-09-01,,IDX-2\n")  # open period vs open period → overlap
    with pytest.raises(RosterValidationError, match="overlapping"):
        roster.load_rosters(store, tmp_path, now=NOW)


def test_roster_covers_reports_gaps(tmp_path, store):
    _write(tmp_path, "lq45.csv", _GOOD)
    roster.load_rosters(store, tmp_path, now=NOW)
    assert store.roster_covers(Date(2024, 5, 1)) is True
    assert store.roster_covers(Date(2023, 1, 1)) is False  # before any period → gap
