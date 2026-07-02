"""SCR-0 · Universe Eligibility — the server-side HARD GATE pre-filter
(screeners.md §3). Cuts ~900 names to ~100–150 in one screener call before the
engine spends paywall-counted pulls.

Screener-served: ADV (Value MA 20 > 10 bn), price (> 100), free float (> 15%).
Engine residual (NOT screenable, applied by `universe.gate` per name): suspended /
UMA flags, IPO < 60 trading days, ARA/ARB-pinned close, broker-summary
completeness, corp-action window, Track A/B assignment.
"""

from __future__ import annotations

import json
import logging
from datetime import date as Date
from datetime import datetime

from currentflow.dal.client import ExodusClient
from currentflow.dal.models import Scr0Row
from currentflow.store.db import Store

log = logging.getLogger(__name__)

# fitem ids (screeners.md §2)
FITEM_VALUE_MA20 = 16454
FITEM_PRICE = 2661
FITEM_FREE_FLOAT = 21535
FITEM_MARKET_CAP = 2892

# Template exactly as pinned in screeners.md §3 (SCR-0).
SCR0_TEMPLATE: dict = {
    "name": "scr0-eligibility",
    "type": "TEMPLATE_TYPE_CUSTOM",
    "ordercol": 0,
    "ordertype": "DESC",
    "sequence": "16454,2661,21535,2892",
    "universe": json.dumps({"scope": "IHSG", "scopeID": "0", "name": "IHSG"}),
    "filters": json.dumps(
        [
            {
                "item1": FITEM_VALUE_MA20, "item1_name": "Value MA 20",
                "item2": "10000000000", "item2_name": "",
                "multiplier": "0", "operator": ">", "type": "basic",
            },
            {
                "item1": FITEM_PRICE, "item1_name": "Price",
                "item2": "100", "item2_name": "",
                "multiplier": "0", "operator": ">", "type": "basic",
            },
            {
                "item1": FITEM_FREE_FLOAT, "item1_name": "Free Float",
                "item2": "15", "item2_name": "",
                "multiplier": "0", "operator": ">", "type": "basic",
            },
        ]
    ),
}


async def run_scr0(
    client: ExodusClient,
    store: Store,
    *,
    trading_day: Date,
    now: datetime,
) -> list[Scr0Row]:
    """Run SCR-0, cache survivors to DuckDB with `as_of = now`, return them.

    Re-running for a stored (symbol, date, as_of) is a no-op (ingest-once).
    """
    results = await client.run_screener(SCR0_TEMPLATE)
    rows = [
        Scr0Row(
            symbol=r["symbol"],
            date=trading_day,
            as_of=now,
            adv20=r["values"].get(FITEM_VALUE_MA20),
            price=r["values"].get(FITEM_PRICE),
            free_float=r["values"].get(FITEM_FREE_FLOAT),
            market_cap=r["values"].get(FITEM_MARKET_CAP),
        )
        for r in results
    ]
    inserted = store.write_scr0_eligible(rows)
    log.info(
        "SCR-0 %s: %d eligible name(s), %d newly cached (as_of=%s)",
        trading_day, len(rows), inserted, now,
    )
    return rows
