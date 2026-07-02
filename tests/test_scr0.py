"""SCR-0 wiring — template fidelity to screeners.md, POST path, ingest-once cache."""

from __future__ import annotations

import json
from datetime import date as Date
from datetime import datetime

import pytest

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
