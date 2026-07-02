"""SCR-EXIT distribution/mirror screener — template fidelity to screeners.md, ingest-
once cache, look-ahead-safe reads, and the open+ARMED watchlist intersection. Emits a
signal-decay candidate list, never a score (RULE B).
"""

from __future__ import annotations

import json
from datetime import date as Date
from datetime import datetime

from currentflow.dal.client import ExodusClient
from currentflow.screeners.scr_exit import SCR_EXIT_TEMPLATE, exit_flags_for, run_scr_exit
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


def test_scr_exit_template_matches_locked_spec():
    assert SCR_EXIT_TEMPLATE["name"] == "scr-exit-distribution"
    assert SCR_EXIT_TEMPLATE["sequence"] == "14400,13540,13562"
    assert SCR_EXIT_TEMPLATE["ordertype"] == "ASC"
    assert json.loads(SCR_EXIT_TEMPLATE["universe"]) == {
        "scope": "IHSG", "scopeID": "0", "name": "IHSG",
    }
    rules = {f["item1"]: (f["operator"], f["item2"], f["multiplier"], f["type"])
             for f in json.loads(SCR_EXIT_TEMPLATE["filters"])}
    assert rules == {
        14400: ("<", "0", "0", "basic"),   # distribution regime
        13540: ("<", "0", "0", "basic"),   # foreign outflow
        13562: (">", "2", "0", "basic"),   # foreign sell streak ≥ 3 days
    }


# --- run + cache + look-ahead --------------------------------------------------------


async def test_run_scr_exit_caches_ingest_once(store):
    payload = _payload([{
        "symbol": "BUMI",
        "results": [{"item": 14400, "raw": -1.5}, {"item": 13540, "raw": -3e9},
                    {"item": 13562, "raw": 4.0}],
    }])
    rows = await run_scr_exit(_client(payload), store, trading_day=DAY, now=NOW)
    assert rows[0].symbol == "BUMI"
    assert rows[0].bandar_accum_dist == -1.5
    assert rows[0].net_foreign_ma20 == -3e9
    assert rows[0].foreign_sell_streak == 4.0

    assert store.read_scr_exit(DAY, decision_ts=NOW) == []                 # invisible at as_of
    assert [r.symbol for r in store.read_scr_exit(DAY, decision_ts=LATER)] == ["BUMI"]

    await run_scr_exit(_client(payload), store, trading_day=DAY, now=NOW)  # re-run
    assert len(store.read_scr_exit(DAY, decision_ts=LATER)) == 1           # ingest-once


async def test_run_scr_exit_keeps_missing_none(store):
    payload = _payload([{
        "symbol": "ADRO",
        "results": [{"item": 14400, "raw": -0.8}, {"item": 13540, "raw": -1e9}],  # streak absent
    }])
    rows = await run_scr_exit(_client(payload), store, trading_day=DAY, now=NOW)
    assert rows[0].foreign_sell_streak is None    # absent fitem stays None, never zero


# --- open + ARMED watchlist intersection ---------------------------------------------


async def test_exit_flags_for_intersects_watchlist(store):
    payload = _payload([
        {"symbol": "BUMI", "results": [{"item": 14400, "raw": -1.5}, {"item": 13540, "raw": -3e9},
                                       {"item": 13562, "raw": 4.0}]},
        {"symbol": "ANTM", "results": [{"item": 14400, "raw": -0.5}, {"item": 13540, "raw": -1e9},
                                       {"item": 13562, "raw": 3.0}]},
    ])
    await run_scr_exit(_client(payload), store, trading_day=DAY, now=NOW)

    # Only names the operator holds / is watching get flagged; the rest are logged, not dropped.
    flagged = exit_flags_for(store, {"BUMI"}, day=DAY, decision_ts=LATER)
    assert [r.symbol for r in flagged] == ["BUMI"]

    none = exit_flags_for(store, {"TLKM"}, day=DAY, decision_ts=LATER)
    assert none == []
