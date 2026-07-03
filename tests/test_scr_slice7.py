"""SCR-3 (trend confirm) + SCR-4 (fundamental tilt) — template fidelity to screeners.md,
ingest-once cache, look-ahead-safe reads, missing-≠-zero, and the tilt hand-off."""

from __future__ import annotations

import json
from datetime import date as Date
from datetime import datetime

from currentflow.dal.client import ExodusClient
from currentflow.fundamentals.tilt import TiltKind, classify_tilt
from currentflow.screeners.scr3 import SCR3_TEMPLATE, run_scr3
from currentflow.screeners.scr4 import SCR4_TEMPLATE, run_scr4
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


def test_scr3_template_matches_locked_spec():
    assert SCR3_TEMPLATE["name"] == "scr3-trend-confirm"
    assert SCR3_TEMPLATE["sequence"] == "2661,12458,12460,21552,21562,21559,13373"
    assert SCR3_TEMPLATE["ordercol"] == 4 and SCR3_TEMPLATE["ordertype"] == "DESC"
    assert json.loads(SCR3_TEMPLATE["universe"]) == {"scope": "IHSG", "scopeID": "0", "name": "IHSG"}
    filters = json.loads(SCR3_TEMPLATE["filters"])
    assert len(filters) == 5
    rules = {(f["item1"], f.get("item2")): (f["operator"], f["type"]) for f in filters}
    assert rules[(2661, "12458")] == (">", "compare")   # price > MA20
    assert rules[(12458, "12460")] == (">", "compare")  # MA20 > MA50
    assert rules[(2661, "21552")] == (">", "compare")   # price > VWAP
    assert rules[(21562, "20")] == (">", "basic")       # ADX14 > 20
    assert rules[(13373, "0")] == (">", "basic")        # RS 3M > 0


def test_scr4_template_is_ranking_pull_no_filters():
    assert SCR4_TEMPLATE["name"] == "scr4-fundamental-tilt"
    assert SCR4_TEMPLATE["sequence"] == "13474,13411,2897,15276,1461,2892"
    assert json.loads(SCR4_TEMPLATE["filters"]) == []   # NOT a gate — fundamentals never block entry


# --- SCR-3 run / cache / look-ahead --------------------------------------------------


async def test_run_scr3_caches_ingest_once(store):
    payload = _payload([{
        "symbol": "BBRI",
        "results": [
            {"item": 2661, "raw": 5000.0}, {"item": 12458, "raw": 4800.0},
            {"item": 12460, "raw": 4500.0}, {"item": 21552, "raw": 4700.0},
            {"item": 21562, "raw": 28.0}, {"item": 21559, "raw": 120.0},
            {"item": 13373, "raw": 3.5},
        ],
    }])
    rows = await run_scr3(_client(payload), store, trading_day=DAY, now=NOW)
    assert rows[0].symbol == "BBRI"
    assert rows[0].adx14 == 28.0 and rows[0].atr14 == 120.0

    assert store.read_scr3(DAY, decision_ts=NOW) == []                    # invisible at as_of
    assert [r.symbol for r in store.read_scr3(DAY, decision_ts=LATER)] == ["BBRI"]

    await run_scr3(_client(payload), store, trading_day=DAY, now=NOW)     # re-run
    assert len(store.read_scr3(DAY, decision_ts=LATER)) == 1             # ingest-once


async def test_run_scr3_keeps_missing_none(store):
    payload = _payload([{
        "symbol": "ADRO",
        "results": [{"item": 2661, "raw": 3000.0}, {"item": 12458, "raw": 2900.0}],  # rest absent
    }])
    rows = await run_scr3(_client(payload), store, trading_day=DAY, now=NOW)
    assert rows[0].atr14 is None and rows[0].rs_3m is None   # absent fitems stay None, never zero


# --- SCR-4 run / cache / tilt hand-off -----------------------------------------------


async def test_run_scr4_caches_and_feeds_tilt(store):
    payload = _payload([{
        "symbol": "UNVR",
        "results": [
            {"item": 13474, "raw": 88.0}, {"item": 13411, "raw": 0.35},
            {"item": 2897, "raw": 12.0}, {"item": 15276, "raw": 90.0},
            {"item": 1461, "raw": 1.2}, {"item": 2892, "raw": 3e14},
        ],
    }])
    rows = await run_scr4(_client(payload), store, trading_day=DAY, now=NOW)
    assert rows[0].mf_rank_pct == 88.0 and rows[0].ev_ebit == 12.0

    assert store.read_scr4(DAY, decision_ts=NOW) == []
    row = store.read_scr4(DAY, decision_ts=LATER)[0]

    # A cached SCR-4 row drives the §7 tilt: top-tercile MF rank → COMPOUNDER.
    tilt = classify_tilt(row.symbol, sector="CONSUMER", mf_rank_pct=row.mf_rank_pct, ev_ebit=row.ev_ebit)
    assert tilt.kind is TiltKind.COMPOUNDER


async def test_run_scr4_negative_ev_ebit_flows_to_speculative(store):
    payload = _payload([{
        "symbol": "LOSS",
        "results": [{"item": 13474, "raw": 95.0}, {"item": 2897, "raw": -6.0}],  # neg EBIT
    }])
    rows = await run_scr4(_client(payload), store, trading_day=DAY, now=NOW)
    tilt = classify_tilt("LOSS", sector="CONSUMER", mf_rank_pct=rows[0].mf_rank_pct, ev_ebit=rows[0].ev_ebit)
    assert tilt.kind is TiltKind.SPECULATIVE   # negative EBIT overrides the high rank
