"""SCR-3 · Trend Confirmation (screeners.md; spec Stage 3 / §6 trigger context).

Close > MA20 > MA50, above VWAP, ADX > 20, positive 3-month RS — run over ARMED-candidate
names to confirm structure server-side *before* the in-engine technical trigger. VWAP and
ADX are now screened server-side; ATR14 is pulled in `sequence` to seed stop sizing.

Engine residual (NOT screenable, computed downstream by `execution.trigger`): Spring-test /
LPS detection, exact stop placement, R:R ≥ 2:1, the 9-day exhaustion cap (spec §6). Cached
as trigger-context inputs, never a score (RULE B).
"""

from __future__ import annotations

import json
import logging
from datetime import date as Date
from datetime import datetime

from currentflow.dal.client import ExodusClient
from currentflow.dal.models import Scr3Row
from currentflow.store.db import Store

log = logging.getLogger(__name__)

# fitem ids (screeners.md §2)
FITEM_PRICE = 2661
FITEM_PRICE_MA20 = 12458
FITEM_PRICE_MA50 = 12460
FITEM_VWAP = 21552
FITEM_ADX14 = 21562
FITEM_ATR14 = 21559
FITEM_RS_3M = 13373

# Template exactly as pinned in screeners.md (SCR-3).
SCR3_TEMPLATE: dict = {
    "name": "scr3-trend-confirm",
    "type": "TEMPLATE_TYPE_CUSTOM",
    "ordercol": 4,
    "ordertype": "DESC",
    "sequence": "2661,12458,12460,21552,21562,21559,13373",
    "universe": json.dumps({"scope": "IHSG", "scopeID": "0", "name": "IHSG"}),
    "filters": json.dumps(
        [
            {
                "item1": FITEM_PRICE, "item1_name": "Price",
                "item2": str(FITEM_PRICE_MA20), "item2_name": "Price MA 20",
                "multiplier": "1", "operator": ">", "type": "compare",
            },
            {
                "item1": FITEM_PRICE_MA20, "item1_name": "Price MA 20",
                "item2": str(FITEM_PRICE_MA50), "item2_name": "Price MA 50",
                "multiplier": "1", "operator": ">", "type": "compare",
            },
            {
                "item1": FITEM_PRICE, "item1_name": "Price",
                "item2": str(FITEM_VWAP), "item2_name": "VWAP",
                "multiplier": "1", "operator": ">", "type": "compare",
            },
            {
                "item1": FITEM_ADX14, "item1_name": "Average Directional Index 14",
                "item2": "20", "item2_name": "",
                "multiplier": "0", "operator": ">", "type": "basic",
            },
            {
                "item1": FITEM_RS_3M, "item1_name": "3 Month RS Line",
                "item2": "0", "item2_name": "",
                "multiplier": "0", "operator": ">", "type": "basic",
            },
        ]
    ),
}


async def run_scr3(
    client: ExodusClient,
    store: Store,
    *,
    trading_day: Date,
    now: datetime,
) -> list[Scr3Row]:
    """Run SCR-3, cache survivors to DuckDB with `as_of = now`, return them.

    Re-running for a stored (symbol, date, as_of) is a no-op (ingest-once).
    """
    results = await client.run_screener(SCR3_TEMPLATE)
    rows = [
        Scr3Row(
            symbol=r["symbol"],
            date=trading_day,
            as_of=now,
            price=r["values"].get(FITEM_PRICE),
            price_ma20=r["values"].get(FITEM_PRICE_MA20),
            price_ma50=r["values"].get(FITEM_PRICE_MA50),
            vwap=r["values"].get(FITEM_VWAP),
            adx14=r["values"].get(FITEM_ADX14),
            atr14=r["values"].get(FITEM_ATR14),
            rs_3m=r["values"].get(FITEM_RS_3M),
        )
        for r in results
    ]
    inserted = store.write_scr3(rows)
    log.info(
        "SCR-3 %s: %d trend-confirmed name(s), %d newly cached (as_of=%s)",
        trading_day, len(rows), inserted, now,
    )
    return rows
