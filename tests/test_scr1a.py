"""SCR-1A wiring — template fidelity to screeners.md, ingest-once cache, look-ahead."""

from __future__ import annotations

import json
from datetime import date as Date
from datetime import datetime

from currentflow.dal.client import ExodusClient
from currentflow.screeners.scr1a import SCR1A_TEMPLATE, run_scr1a
from tests.conftest import scripted_transport

DAY = Date(2026, 6, 30)
NOW = datetime(2026, 7, 1, 18, 0)


def scr1a_payload() -> dict:
    return {
        "data": {
            "calcs": [
                {
                    "symbol": "BBRI",
                    "results": [
                        {"item": 3194, "raw": 250e9},
                        {"item": 13540, "raw": 80e9},
                        {"item": 13561, "raw": 4},
                        {"item": 13521, "raw": 60e9},
                    ],
                },
                {"symbol": "TLKM", "results": [{"item": 3194, "raw": 90e9}]},
            ]
        }
    }


# --- template fidelity -------------------------------------------------------------------


def test_scr1a_template_matches_locked_screener_spec():
    assert SCR1A_TEMPLATE["name"] == "scr1a-foreign-accum"
    assert SCR1A_TEMPLATE["sequence"] == "3194,13540,13561,13521"
    assert SCR1A_TEMPLATE["ordertype"] == "DESC"
    assert json.loads(SCR1A_TEMPLATE["universe"]) == {
        "scope": "idx", "scopeID": "550", "name": "LQ45",
    }
    filters = json.loads(SCR1A_TEMPLATE["filters"])
    rules = {f["item1"]: (f["operator"], f["item2"], f["multiplier"], f["type"]) for f in filters}
    assert rules == {
        3194: (">", "13540", "2", "compare"),   # NBSA > 2× its MA20
        13561: (">", "2", "0", "basic"),        # net-buy streak ≥ 3 days
        13521: (">", "0", "0", "basic"),        # flow trend up
        16454: (">", "10000000000", "0", "basic"),  # inherit SCR-0 liquidity
    }


# --- run + cache -----------------------------------------------------------------------------


async def test_run_scr1a_caches_ingest_once(store):
    calls: list = []
    client = ExodusClient(
        scripted_transport([]),
        post_transport=scripted_transport(
            [(200, scr1a_payload()), (200, scr1a_payload())], calls
        ),
    )

    rows = await run_scr1a(client, store, trading_day=DAY, now=NOW)
    assert [r.symbol for r in rows] == ["BBRI", "TLKM"]
    assert rows[0].net_foreign == 250e9
    assert rows[0].net_foreign_ma20 == 80e9
    assert rows[0].buy_streak == 4
    assert rows[1].flow_ma20 is None  # absent fitem stays None, never zero

    # look-ahead: invisible before as_of, visible after
    assert store.read_scr1a(DAY, decision_ts=NOW) == []
    visible = store.read_scr1a(DAY, decision_ts=datetime(2026, 7, 1, 18, 1))
    assert [r.symbol for r in visible] == ["BBRI", "TLKM"]

    # re-run same (symbol, date, as_of) → no duplicate rows (ingest-once)
    await run_scr1a(client, store, trading_day=DAY, now=NOW)
    assert len(store.read_scr1a(DAY, decision_ts=datetime(2026, 7, 1, 18, 1))) == 2
    assert calls[0][0] == "screener/templates"
