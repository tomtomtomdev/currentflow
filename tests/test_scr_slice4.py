"""SCR-1B / SCR-1C / SCR-2 wiring — template fidelity to screeners.md, ingest-once
cache, and look-ahead-safe reads. Screeners emit component-input lists, never scores.
"""

from __future__ import annotations

import json
from datetime import date as Date
from datetime import datetime

from currentflow.dal.client import ExodusClient
from currentflow.screeners.scr1b import SCR1B_TEMPLATE, run_scr1b
from currentflow.screeners.scr1c import SCR1C_TEMPLATE, run_scr1c
from currentflow.screeners.scr2 import SCR2_TEMPLATE, run_scr2
from tests.conftest import scripted_transport

DAY = Date(2026, 6, 30)
NOW = datetime(2026, 7, 1, 18, 0)
LATER = datetime(2026, 7, 1, 18, 1)


def _payload(calcs: list[dict]) -> dict:
    return {"data": {"calcs": calcs}}


def _client(payload):
    return ExodusClient(
        scripted_transport([]),
        post_transport=scripted_transport([(200, payload), (200, payload)]),
    )


# --- template fidelity ---------------------------------------------------------------


def test_scr1b_template_matches_locked_spec():
    assert SCR1B_TEMPLATE["name"] == "scr1b-bandar-accum"
    assert SCR1B_TEMPLATE["sequence"] == "14399,14426,14400,16454"
    assert json.loads(SCR1B_TEMPLATE["universe"]) == {
        "scope": "idx", "scopeID": "1000003583", "name": "IDXSMC-LIQ",
    }
    # two filters share item1=14399 (a compare and a basic), so match the list itself
    triples = {(f["item1"], f["operator"], f["item2"], f["multiplier"], f["type"])
               for f in json.loads(SCR1B_TEMPLATE["filters"])}
    assert triples == {
        (14399, ">", "14426", "1", "compare"),      # bandar value > its MA20
        (14399, ">", "0", "0", "basic"),            # net accumulation positive
        (14400, ">", "0", "0", "basic"),            # accumulation regime
        (16454, ">", "10000000000", "0", "basic"),  # inherit SCR-0 liquidity
    }


def test_scr1c_template_matches_locked_spec():
    assert SCR1C_TEMPLATE["name"] == "scr1c-stealth-divergence"
    assert SCR1C_TEMPLATE["sequence"] == "14399,1564,12469,12464"
    assert SCR1C_TEMPLATE["ordertype"] == "ASC"
    assert json.loads(SCR1C_TEMPLATE["universe"])["scope"] == "IHSG"
    filters = json.loads(SCR1C_TEMPLATE["filters"])
    assert {"item1": 1564, "item1_name": "1 Month Price Returns", "item2": "3",
            "item2_name": "", "multiplier": "0", "operator": "<", "type": "basic"} in filters
    assert any(f["item1"] == 12469 and f["multiplier"] == "1.5" and f["operator"] == ">" for f in filters)


def test_scr2_template_matches_locked_spec():
    assert SCR2_TEMPLATE["sequence"] == "12469,12464,3229,15396"
    rules = {f["item1"]: (f["operator"], f["item2"], f["multiplier"], f["type"])
             for f in json.loads(SCR2_TEMPLATE["filters"])}
    assert rules == {
        12469: (">", "12464", "3", "compare"),   # RVOL ≥ 3×
        15396: (">", "0", "0", "basic"),         # frequency spike
    }


# --- run + cache + look-ahead --------------------------------------------------------


async def test_run_scr1b_caches_ingest_once(store):
    payload = _payload([{
        "symbol": "BRMS",
        "results": [{"item": 14399, "raw": 12e9}, {"item": 14426, "raw": 8e9},
                    {"item": 14400, "raw": 1.0}, {"item": 16454, "raw": 15e9}],
    }])
    rows = await run_scr1b(_client(payload), store, trading_day=DAY, now=NOW)
    assert rows[0].symbol == "BRMS"
    assert rows[0].bandar_value == 12e9 and rows[0].bandar_value_ma20 == 8e9

    assert store.read_scr1b(DAY, decision_ts=NOW) == []                 # invisible at as_of
    assert [r.symbol for r in store.read_scr1b(DAY, decision_ts=LATER)] == ["BRMS"]

    await run_scr1b(_client(payload), store, trading_day=DAY, now=NOW)  # re-run
    assert len(store.read_scr1b(DAY, decision_ts=LATER)) == 1           # ingest-once


async def test_run_scr1c_caches_and_keeps_missing_none(store):
    payload = _payload([{
        "symbol": "ADRO",
        "results": [{"item": 14399, "raw": 9e9}, {"item": 1564, "raw": 1.2},
                    {"item": 12469, "raw": 5e6}],   # 12464 absent
    }])
    rows = await run_scr1c(_client(payload), store, trading_day=DAY, now=NOW)
    assert rows[0].price_return_1m == 1.2
    assert rows[0].volume_ma20 is None              # absent fitem stays None, never zero
    assert [r.symbol for r in store.read_scr1c(DAY, decision_ts=LATER)] == ["ADRO"]


async def test_run_scr2_caches(store):
    payload = _payload([{
        "symbol": "GOTO",
        "results": [{"item": 12469, "raw": 30e9}, {"item": 12464, "raw": 8e9},
                    {"item": 3229, "raw": 5000}, {"item": 15396, "raw": 2.0}],
    }])
    rows = await run_scr2(_client(payload), store, trading_day=DAY, now=NOW)
    assert rows[0].frequency_spike == 2.0
    assert [r.symbol for r in store.read_scr2(DAY, decision_ts=LATER)] == ["GOTO"]
