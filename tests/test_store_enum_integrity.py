"""Corrupt enum-column defenses.

Regression for the crash where a broker code ('GR') leaked into `daily_bar.status`
(broker_code and status share column index 3 across the two tables) and
`RowStatus('GR')` took down the whole terminal on read. Three layers:

  1. the reader skips a corrupt row instead of raising (terminal survives);
  2. the schema CHECK constraint blocks a bad value at write time (fresh DBs);
  3. `check_enum_integrity` surfaces corruption in DBs created before the constraint.
"""

from __future__ import annotations

from datetime import date, datetime

import duckdb
import pytest

from currentflow.store.db import Store
from currentflow.store.integrity import scan_enum_integrity
from currentflow.store.schema import DAILY_BAR_COLUMNS

_COLS = ", ".join(f'"{c}"' for c in DAILY_BAR_COLUMNS)
_PH = ", ".join("?" for _ in DAILY_BAR_COLUMNS)


def _row(sym: str, d: date, status: str) -> tuple:
    return (sym, d, datetime(d.year, d.month, d.day, 16, 15), status,
            1.0, 2.0, 0.5, 1.5, 100, 1000.0, 10, 1.2, None, None, None, None)


def test_reader_skips_corrupt_status_row():
    # simulate a pre-CHECK legacy DB: a constraint-free twin holding a corrupt row
    con = duckdb.connect(":memory:")
    con.execute(f'CREATE TABLE daily_bar ("{DAILY_BAR_COLUMNS[0]}" VARCHAR, '
                '"date" DATE, "as_of" TIMESTAMP, "status" VARCHAR, '
                '"open" DOUBLE, "high" DOUBLE, "low" DOUBLE, "close" DOUBLE, '
                '"volume" BIGINT, "value" DOUBLE, "frequency" BIGINT, "vwap" DOUBLE, '
                '"foreign_buy" DOUBLE, "foreign_sell" DOUBLE, "net_foreign" DOUBLE, '
                '"change_percentage" DOUBLE)')
    con.executemany(f"INSERT INTO daily_bar ({_COLS}) VALUES ({_PH})", [
        _row("BBCA", date(2026, 7, 1), "TRADED"),
        _row("BBCA", date(2026, 7, 2), "GR"),  # broker code leaked into status
    ])
    s = Store.__new__(Store)
    s._con = con
    bars = s.read_daily_bars("BBCA", decision_ts=datetime(2030, 1, 1))
    assert [b.date.day for b in bars] == [1]  # corrupt row dropped, terminal survives


def test_check_constraint_rejects_bad_status(store):
    with pytest.raises(duckdb.ConstraintException):
        store._con.execute(
            f"INSERT INTO daily_bar ({_COLS}) VALUES ({_PH})",
            _row("BBCA", date(2026, 7, 2), "GR"),
        )
    # a valid status still writes fine
    store._con.execute(
        f"INSERT INTO daily_bar ({_COLS}) VALUES ({_PH})",
        _row("BBCA", date(2026, 7, 1), "TRADED"),
    )


def test_scan_surfaces_corruption_and_passes_clean():
    con = duckdb.connect(":memory:")
    con.execute('CREATE TABLE daily_bar ("symbol" VARCHAR, "date" DATE, '
                '"as_of" TIMESTAMP, "status" VARCHAR)')
    con.execute('CREATE TABLE broker_net ("symbol" VARCHAR, "date" DATE, '
                '"as_of" TIMESTAMP, "broker_code" VARCHAR, "side" VARCHAR, '
                '"investor_type" VARCHAR)')
    assert scan_enum_integrity(con).clean  # empty tables are clean

    con.execute("INSERT INTO daily_bar VALUES "
                "('BBCA','2026-07-01','2026-07-01 16:15','TRADED'),"
                "('BBCA','2026-07-02','2026-07-02 16:15','GR')")
    con.execute("INSERT INTO broker_net VALUES "
                "('BBCA','2026-07-01','2026-07-01 16:15','GR','BUY','FOREIGN')")
    report = scan_enum_integrity(con)
    assert not report.clean
    assert report.total == 1  # one bad daily_bar.status; broker_net is clean
    assert report.invalid[("daily_bar", "status")] == {"GR": 1}


def test_reader_skips_short_row_instead_of_indexerror(monkeypatch):
    """A partial/corrupt DB can surface a row with fewer columns than the schema.
    Unpacking it raised IndexError (`r[3]`) and took down the whole ranking module;
    the reader must drop it and keep going."""
    good = _row("BBCA", date(2026, 7, 1), "TRADED")
    short = ("BBCA", date(2026, 7, 2), datetime(2026, 7, 2, 16, 15))  # truncated: 3 cols

    s = Store.__new__(Store)
    s._con = duckdb.connect(":memory:")
    monkeypatch.setattr(s, "_read", lambda *a, **k: [good, short])

    bars = s.read_daily_bars("BBCA", decision_ts=datetime(2030, 1, 1))
    assert [b.date.day for b in bars] == [1]  # short row dropped, terminal survives
