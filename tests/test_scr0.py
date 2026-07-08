"""SCR-0 wiring — template fidelity to screeners.md, POST path, ingest-once cache."""

from __future__ import annotations

import json
from datetime import date as Date
from datetime import datetime

import pytest

from currentflow import config
from currentflow.dal.client import ExodusClient
from currentflow.dal.errors import TransportError
from currentflow.dal.parse import parse_screener_results
from currentflow.screeners.scr0 import SCR0_TEMPLATE, run_scr0
from tests.conftest import scripted_transport

DAY = Date(2026, 6, 30)
NOW = datetime(2026, 7, 1, 18, 0)


def scr0_payload() -> dict:
    return {
        "data": {
            "calcs": [
                {
                    "symbol": "BBRI",
                    "results": [
                        {"item": 16454, "raw": 5.0e11},
                        {"item": 2661, "raw": 4500},
                        {"item": 21535, "raw": 43.0},
                        {"item": 2892, "raw": 6.8e14},
                    ],
                },
                {"symbol": "BRMS", "results": [{"item": 16454, "raw": 6.1e10}]},
            ]
        }
    }


# --- template fidelity -------------------------------------------------------------------


def test_scr0_template_matches_locked_screener_spec():
    assert SCR0_TEMPLATE["name"] == "scr0-eligibility"
    assert SCR0_TEMPLATE["sequence"] == "16454,2661,21535,2892"
    assert json.loads(SCR0_TEMPLATE["universe"]) == {
        "scope": "IHSG", "scopeID": "0", "name": "IHSG",
    }
    filters = json.loads(SCR0_TEMPLATE["filters"])
    rules = {f["item1"]: (f["operator"], f["item2"]) for f in filters}
    assert rules == {
        16454: (">", "10000000000"),  # ADV ≥ Rp 10 bn
        2661: (">", "100"),           # price floor
        21535: (">", "15"),           # free float %
    }
    assert all(f["type"] == "basic" for f in filters)


# --- parse ---------------------------------------------------------------------------------


def test_parse_screener_results_extracts_symbols_and_values():
    rows = parse_screener_results(scr0_payload())
    assert [r["symbol"] for r in rows] == ["BBRI", "BRMS"]
    assert rows[0]["values"][2661] == 4500


def test_parse_screener_results_tolerates_flat_list():
    rows = parse_screener_results([{"symbol": "PTRO", "results": []}])
    assert rows == [{"symbol": "PTRO", "values": {}}]


# --- client POST path -------------------------------------------------------------------------


async def test_post_without_transport_fails_loud():
    client = ExodusClient(scripted_transport([]))
    with pytest.raises(TransportError):
        await client.run_screener(SCR0_TEMPLATE)


async def test_run_screener_sends_required_pagination_fields():
    """Live-verified (slice 13): omitting the integer `page` is a 400. The client
    must send page + limit alongside every template."""
    calls: list = []
    client = ExodusClient(
        scripted_transport([]),
        post_transport=scripted_transport([(200, scr0_payload())], calls),
    )
    await client.run_screener(SCR0_TEMPLATE)
    _path, body = calls[0]
    assert body["page"] == 1
    assert body["limit"] == config.SCREENER_PAGE_LIMIT
    assert body["name"] == "scr0-eligibility"  # template fields still carried


async def test_run_screener_pages_until_totalrows():
    """A survivor set larger than one page is fetched completely (no silent caps)."""

    def page_payload(symbols: list[str], totalrows: int) -> dict:
        return {
            "data": {
                "totalrows": totalrows,
                "calcs": [{"symbol": s, "results": []} for s in symbols],
            }
        }

    calls: list = []
    client = ExodusClient(
        scripted_transport([]),
        post_transport=scripted_transport(
            [
                (200, page_payload(["AAAA", "BBBB"], totalrows=3)),
                (200, page_payload(["CCCC"], totalrows=3)),
            ],
            calls,
        ),
    )
    rows = await client.run_screener(SCR0_TEMPLATE)
    assert [r["symbol"] for r in rows] == ["AAAA", "BBBB", "CCCC"]
    assert [body["page"] for _p, body in calls] == [1, 2]


async def test_run_screener_pages_when_totalrows_absent(monkeypatch):
    """No `totalrows` in the envelope must NOT cap at page 1 — the client pages until
    a short page, else a multi-page universe is silently truncated (no silent caps)."""
    monkeypatch.setattr(config, "SCREENER_PAGE_LIMIT", 2)

    def page(symbols: list[str]) -> dict:  # note: no `totalrows` key
        return {"data": {"calcs": [{"symbol": s, "results": []} for s in symbols]}}

    calls: list = []
    client = ExodusClient(
        scripted_transport([]),
        post_transport=scripted_transport(
            [(200, page(["AAAA", "BBBB"])), (200, page(["CCCC"]))], calls
        ),
    )
    rows = await client.run_screener(SCR0_TEMPLATE)
    assert [r["symbol"] for r in rows] == ["AAAA", "BBBB", "CCCC"]
    assert [body["page"] for _p, body in calls] == [1, 2]


async def test_run_screener_stops_on_empty_page_despite_totalrows():
    """A lying server (totalrows > what it will ever send) must not loop forever."""
    client = ExodusClient(
        scripted_transport([]),
        post_transport=scripted_transport(
            [(200, {"data": {"totalrows": 99, "calcs": []}})]
        ),
    )
    rows = await client.run_screener(SCR0_TEMPLATE)
    assert rows == []


async def test_run_scr0_caches_ingest_once(store):
    calls: list = []
    client = ExodusClient(
        scripted_transport([]),
        post_transport=scripted_transport(
            [(200, scr0_payload()), (200, scr0_payload())], calls
        ),
    )

    rows = await run_scr0(client, store, trading_day=DAY, now=NOW)
    assert [r.symbol for r in rows] == ["BBRI", "BRMS"]
    assert rows[0].adv20 == 5.0e11 and rows[0].price == 4500

    # look-ahead: invisible before as_of, visible after
    assert store.read_scr0_eligible(DAY, decision_ts=NOW) == []
    visible = store.read_scr0_eligible(DAY, decision_ts=datetime(2026, 7, 1, 18, 1))
    assert [r.symbol for r in visible] == ["BBRI", "BRMS"]

    # re-run same (symbol, date, as_of) → no duplicate rows (ingest-once)
    await run_scr0(client, store, trading_day=DAY, now=NOW)
    again = store.read_scr0_eligible(DAY, decision_ts=datetime(2026, 7, 1, 18, 1))
    assert len(again) == 2
    assert calls[0][0] == "screener/templates"
