"""Slice-3 parsers + DAL wiring: KSEI ownership chart (both envelope shapes),
fetch-time `as_of` stamping, and the store round-trip."""

from __future__ import annotations

from datetime import date as Date
from datetime import datetime

from currentflow.dal.client import ExodusClient
from currentflow.dal.parse import parse_ksei_ownership
from tests.conftest import scripted_transport

FETCHED = datetime(2026, 7, 1, 9, 0)


def test_parse_ksei_flat_rows_with_both_percentages():
    payload = {
        "data": [
            {"date": "2026-04-30", "foreign": 42.0, "local": 58.0},
            {"date": "2026-05-31", "foreign_percentage": "43.5", "local_percentage": "56.5"},
        ]
    }
    rows = parse_ksei_ownership("BBRI", payload, fetched_at=FETCHED)
    assert [(r.date, r.foreign_pct, r.local_pct) for r in rows] == [
        (Date(2026, 4, 30), 42.0, 58.0),
        (Date(2026, 5, 31), 43.5, 56.5),
    ]
    assert all(r.as_of == FETCHED for r in rows)


def test_parse_ksei_parallel_series_merged_by_date():
    payload = {
        "data": {
            "foreign": [
                {"date": "2026-04-30", "value": 42.0},
                {"date": "2026-05-31", "value": 43.5},
            ],
            "local": [{"date": "2026-04-30", "value": 58.0}],
        }
    }
    rows = parse_ksei_ownership("BBRI", payload, fetched_at=FETCHED)
    assert [(r.date, r.foreign_pct, r.local_pct) for r in rows] == [
        (Date(2026, 4, 30), 42.0, 58.0),
        (Date(2026, 5, 31), 43.5, None),  # missing local stays None, never zero
    ]


def test_parse_ksei_tolerates_junk():
    assert parse_ksei_ownership("BBRI", {"data": []}, fetched_at=FETCHED) == []
    assert parse_ksei_ownership("BBRI", None, fetched_at=FETCHED) == []


async def test_client_ksei_ownership_stamps_fetch_time():
    calls: list = []
    payload = {"data": [{"date": "2026-05-31", "foreign": 43.5, "local": 56.5}]}
    client = ExodusClient(
        scripted_transport([(200, payload)], calls), now=lambda: FETCHED
    )
    rows = await client.ksei_ownership("BBRI", value_year=2026)
    assert calls[0][0] == "emitten-metadata/shareholders/BBRI/chart"
    assert calls[0][1] == {"value_year": 2026}
    assert rows[0].as_of == FETCHED
    assert rows[0].foreign_pct == 43.5


def test_store_ksei_roundtrip_is_ingest_once(store):
    from currentflow.dal.models import OwnershipSlice

    row = OwnershipSlice("BBRI", Date(2026, 5, 31), FETCHED, 43.5, 56.5)
    assert store.write_ksei_ownership([row]) == 1
    assert store.write_ksei_ownership([row]) == 0  # ingest-once
    got = store.read_ksei_ownership("BBRI", decision_ts=datetime(2026, 7, 1, 9, 1))
    assert [(r.date, r.foreign_pct) for r in got] == [(Date(2026, 5, 31), 43.5)]
