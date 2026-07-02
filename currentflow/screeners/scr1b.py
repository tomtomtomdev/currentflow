"""SCR-1B · Bandar Accumulation — Track B lead (screeners.md; spec §4 broker
concentration, SMS wt 35).

Server-side pre-filter scoped to IDXSMC-LIQ (the small-mid-cap liquid index, the
natural lapis-2 universe): big-broker net value rising above its own 20-day trend, net
positive, in an accumulation regime. Cached as SMS *component inputs*, never a score.

Engine residual (NOT screenable, computed by `signals.broker_flow` / `signals.veto`):
top-2 net-buy share + Herfindahl, persistence on flat/down bars, single-bandar-monopoly
veto (>60%), accumulator VWAP vs price.
"""

from __future__ import annotations

import json
import logging
from datetime import date as Date
from datetime import datetime

from currentflow.dal.client import ExodusClient
from currentflow.dal.models import Scr1bRow
from currentflow.screeners.scr0 import FITEM_VALUE_MA20
from currentflow.store.db import Store

log = logging.getLogger(__name__)

# fitem ids (screeners.md §2, category 93 Bandarmology)
FITEM_BANDAR_VALUE = 14399
FITEM_BANDAR_VALUE_MA20 = 14426
FITEM_BANDAR_ACCUM_DIST = 14400

IDXSMC_LIQ_SCOPE_ID = "1000003583"   # small-mid liquid → Track B

# Template exactly as pinned in screeners.md (SCR-1B).
SCR1B_TEMPLATE: dict = {
    "name": "scr1b-bandar-accum",
    "type": "TEMPLATE_TYPE_CUSTOM",
    "ordercol": 0,
    "ordertype": "DESC",
    "sequence": "14399,14426,14400,16454",
    "universe": json.dumps({"scope": "idx", "scopeID": IDXSMC_LIQ_SCOPE_ID, "name": "IDXSMC-LIQ"}),
    "filters": json.dumps(
        [
            {
                "item1": FITEM_BANDAR_VALUE, "item1_name": "Bandar Value",
                "item2": str(FITEM_BANDAR_VALUE_MA20), "item2_name": "Bandar Value MA 20",
                "multiplier": "1", "operator": ">", "type": "compare",
            },
            {
                "item1": FITEM_BANDAR_VALUE, "item1_name": "Bandar Value",
                "item2": "0", "item2_name": "",
                "multiplier": "0", "operator": ">", "type": "basic",
            },
            {
                "item1": FITEM_BANDAR_ACCUM_DIST, "item1_name": "Bandar Accum/Dist",
                "item2": "0", "item2_name": "",
                "multiplier": "0", "operator": ">", "type": "basic",
            },
            {
                "item1": FITEM_VALUE_MA20, "item1_name": "Value MA 20",
                "item2": "10000000000", "item2_name": "",
                "multiplier": "0", "operator": ">", "type": "basic",
            },
        ]
    ),
}


async def run_scr1b(
    client: ExodusClient,
    store: Store,
    *,
    trading_day: Date,
    now: datetime,
) -> list[Scr1bRow]:
    """Run SCR-1B, cache survivors to DuckDB with `as_of = now`, return them.

    Re-running for a stored (symbol, date, as_of) is a no-op (ingest-once).
    """
    results = await client.run_screener(SCR1B_TEMPLATE)
    rows = [
        Scr1bRow(
            symbol=r["symbol"],
            date=trading_day,
            as_of=now,
            bandar_value=r["values"].get(FITEM_BANDAR_VALUE),
            bandar_value_ma20=r["values"].get(FITEM_BANDAR_VALUE_MA20),
            bandar_accum_dist=r["values"].get(FITEM_BANDAR_ACCUM_DIST),
            adv20=r["values"].get(FITEM_VALUE_MA20),
        )
        for r in results
    ]
    inserted = store.write_scr1b(rows)
    log.info(
        "SCR-1B %s: %d bandar-accumulation candidate(s), %d newly cached (as_of=%s)",
        trading_day, len(rows), inserted, now,
    )
    return rows
