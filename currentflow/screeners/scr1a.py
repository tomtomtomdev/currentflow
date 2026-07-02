"""SCR-1A · Foreign Accumulation — Track A lead (screeners.md; spec §4 NBSA, SMS wt 25).

Server-side pre-filter scoped to LQ45: foreign net buy spiking above 2× its 20-day
average, with a ≥3-day net-buy streak and a positive flow trend. Results are cached
as SMS *component inputs* (screeners.md §4), never as a score.

Engine residual (NOT screenable, computed by `signals.foreign_flow`): foreign
net-buy z-score, KSEI ownership Δ overlay, Track-A-only weighting.
"""

from __future__ import annotations

import json
import logging
from datetime import date as Date
from datetime import datetime

from currentflow.dal.client import ExodusClient
from currentflow.dal.models import Scr1aRow
from currentflow.screeners.scr0 import FITEM_VALUE_MA20
from currentflow.store.db import Store

log = logging.getLogger(__name__)

# fitem ids (screeners.md §2)
FITEM_NET_FOREIGN = 3194     # Net Foreign Buy / Sell
FITEM_NBSA_MA20 = 13540      # Net Foreign Buy / Sell MA20
FITEM_BUY_STREAK = 13561     # Net Foreign Buy Streak
FITEM_FLOW_MA20 = 13521      # Foreign Flow MA 20

# Widen Track A by swapping the universe scopeID (screeners.md SCR-1A note).
LQ45_SCOPE_ID = "550"
IDX80_SCOPE_ID = "1000003288"

# Template exactly as pinned in screeners.md (SCR-1A).
SCR1A_TEMPLATE: dict = {
    "name": "scr1a-foreign-accum",
    "type": "TEMPLATE_TYPE_CUSTOM",
    "ordercol": 0,
    "ordertype": "DESC",
    "sequence": "3194,13540,13561,13521",
    "universe": json.dumps({"scope": "idx", "scopeID": LQ45_SCOPE_ID, "name": "LQ45"}),
    "filters": json.dumps(
        [
            {
                "item1": FITEM_NET_FOREIGN, "item1_name": "Net Foreign Buy / Sell",
                "item2": str(FITEM_NBSA_MA20), "item2_name": "Net Foreign Buy / Sell MA20",
                "multiplier": "2", "operator": ">", "type": "compare",
            },
            {
                "item1": FITEM_BUY_STREAK, "item1_name": "Net Foreign Buy Streak",
                "item2": "2", "item2_name": "",
                "multiplier": "0", "operator": ">", "type": "basic",
            },
            {
                "item1": FITEM_FLOW_MA20, "item1_name": "Foreign Flow MA 20",
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


async def run_scr1a(
    client: ExodusClient,
    store: Store,
    *,
    trading_day: Date,
    now: datetime,
) -> list[Scr1aRow]:
    """Run SCR-1A, cache survivors to DuckDB with `as_of = now`, return them.

    Re-running for a stored (symbol, date, as_of) is a no-op (ingest-once).
    """
    results = await client.run_screener(SCR1A_TEMPLATE)
    rows = [
        Scr1aRow(
            symbol=r["symbol"],
            date=trading_day,
            as_of=now,
            net_foreign=r["values"].get(FITEM_NET_FOREIGN),
            net_foreign_ma20=r["values"].get(FITEM_NBSA_MA20),
            buy_streak=r["values"].get(FITEM_BUY_STREAK),
            flow_ma20=r["values"].get(FITEM_FLOW_MA20),
        )
        for r in results
    ]
    inserted = store.write_scr1a(rows)
    log.info(
        "SCR-1A %s: %d foreign-accumulation candidate(s), %d newly cached (as_of=%s)",
        trading_day, len(rows), inserted, now,
    )
    return rows
