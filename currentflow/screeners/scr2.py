"""SCR-2 · Volume / Frequency Anomaly — RVOL (screeners.md; spec §4 volume anomaly,
SMS wt 10–15).

Feeds the volume-anomaly component: relative volume ≥ 3× its 20-day average with a
frequency spike. Run over the SCR-0 survivors. Cached as SMS *component inputs*, never
a score.

Engine residual (NOT screenable, computed downstream): block-trade footprint
(> IDR 1B / > 1% ADV), avg-ticket expansion, retail-FOMO veto (needs broker split).
"""

from __future__ import annotations

import json
import logging
from datetime import date as Date
from datetime import datetime

from currentflow.dal.client import ExodusClient
from currentflow.dal.models import Scr2Row
from currentflow.screeners.scr1c import FITEM_VOLUME, FITEM_VOLUME_MA20
from currentflow.store.db import Store

log = logging.getLogger(__name__)

# fitem ids (screeners.md §2)
FITEM_FREQUENCY = 3229
FITEM_FREQUENCY_SPIKE = 15396

# Template exactly as pinned in screeners.md (SCR-2).
SCR2_TEMPLATE: dict = {
    "name": "scr2-volume-anomaly",
    "type": "TEMPLATE_TYPE_CUSTOM",
    "ordercol": 0,
    "ordertype": "DESC",
    "sequence": "12469,12464,3229,15396",
    "universe": json.dumps({"scope": "IHSG", "scopeID": "0", "name": "IHSG"}),
    "filters": json.dumps(
        [
            {
                "item1": FITEM_VOLUME, "item1_name": "Volume",
                "item2": str(FITEM_VOLUME_MA20), "item2_name": "Volume MA 20",
                "multiplier": "3", "operator": ">", "type": "compare",
            },
            {
                "item1": FITEM_FREQUENCY_SPIKE, "item1_name": "Frequency Spike",
                "item2": "0", "item2_name": "",
                "multiplier": "0", "operator": ">", "type": "basic",
            },
        ]
    ),
}


async def run_scr2(
    client: ExodusClient,
    store: Store,
    *,
    trading_day: Date,
    now: datetime,
) -> list[Scr2Row]:
    """Run SCR-2, cache survivors to DuckDB with `as_of = now`, return them.

    Re-running for a stored (symbol, date, as_of) is a no-op (ingest-once).
    """
    results = await client.run_screener(SCR2_TEMPLATE)
    rows = [
        Scr2Row(
            symbol=r["symbol"],
            date=trading_day,
            as_of=now,
            volume=r["values"].get(FITEM_VOLUME),
            volume_ma20=r["values"].get(FITEM_VOLUME_MA20),
            frequency=r["values"].get(FITEM_FREQUENCY),
            frequency_spike=r["values"].get(FITEM_FREQUENCY_SPIKE),
        )
        for r in results
    ]
    inserted = store.write_scr2(rows)
    log.info(
        "SCR-2 %s: %d volume-anomaly candidate(s), %d newly cached (as_of=%s)",
        trading_day, len(rows), inserted, now,
    )
    return rows
