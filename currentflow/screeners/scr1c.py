"""SCR-1C · Stealth Divergence proxy — highest-value Stage 1 signal (screeners.md;
spec §4 P-V Divergence, SMS wt 30).

Coarse pre-filter for "accumulation up while price flat/down": bandar value rising
above its 20-day trend, 1-month price return roughly flat (< 3%), volume present
(> 1.5× its MA20). True divergence (vol/price corr < 0.3 on high-vol bars) is
engine-computed. Cached as SMS *component inputs*, never a score.

Engine residual (NOT screenable, computed by `signals.sms._divergence` /
`signals.phase`): true price-volume correlation on high-vol bars, absorption (needs
depth), VSA effort-vs-result, Wyckoff phase classification (RULE A gate).
"""

from __future__ import annotations

import json
import logging
from datetime import date as Date
from datetime import datetime

from currentflow.dal.client import ExodusClient
from currentflow.dal.models import Scr1cRow
from currentflow.screeners.scr0 import FITEM_VALUE_MA20
from currentflow.screeners.scr1b import FITEM_BANDAR_VALUE, FITEM_BANDAR_VALUE_MA20
from currentflow.store.db import Store

log = logging.getLogger(__name__)

# fitem ids (screeners.md §2)
FITEM_PRICE_RETURN_1M = 1564   # 1 Month Price Returns (%)
FITEM_VOLUME = 12469
FITEM_VOLUME_MA20 = 12464

# Template exactly as pinned in screeners.md (SCR-1C).
SCR1C_TEMPLATE: dict = {
    "name": "scr1c-stealth-divergence",
    "type": "TEMPLATE_TYPE_CUSTOM",
    "ordercol": 0,
    "ordertype": "ASC",
    "sequence": "14399,1564,12469,12464",
    "universe": json.dumps({"scope": "IHSG", "scopeID": "0", "name": "IHSG"}),
    "filters": json.dumps(
        [
            {
                "item1": FITEM_BANDAR_VALUE, "item1_name": "Bandar Value",
                "item2": str(FITEM_BANDAR_VALUE_MA20), "item2_name": "Bandar Value MA 20",
                "multiplier": "1", "operator": ">", "type": "compare",
            },
            {
                "item1": FITEM_PRICE_RETURN_1M, "item1_name": "1 Month Price Returns",
                "item2": "3", "item2_name": "",
                "multiplier": "0", "operator": "<", "type": "basic",
            },
            {
                "item1": FITEM_VOLUME, "item1_name": "Volume",
                "item2": str(FITEM_VOLUME_MA20), "item2_name": "Volume MA 20",
                "multiplier": "1.5", "operator": ">", "type": "compare",
            },
            {
                "item1": FITEM_VALUE_MA20, "item1_name": "Value MA 20",
                "item2": "10000000000", "item2_name": "",
                "multiplier": "0", "operator": ">", "type": "basic",
            },
        ]
    ),
}


async def run_scr1c(
    client: ExodusClient,
    store: Store,
    *,
    trading_day: Date,
    now: datetime,
) -> list[Scr1cRow]:
    """Run SCR-1C, cache survivors to DuckDB with `as_of = now`, return them.

    Re-running for a stored (symbol, date, as_of) is a no-op (ingest-once).
    """
    results = await client.run_screener(SCR1C_TEMPLATE)
    rows = [
        Scr1cRow(
            symbol=r["symbol"],
            date=trading_day,
            as_of=now,
            bandar_value=r["values"].get(FITEM_BANDAR_VALUE),
            price_return_1m=r["values"].get(FITEM_PRICE_RETURN_1M),
            volume=r["values"].get(FITEM_VOLUME),
            volume_ma20=r["values"].get(FITEM_VOLUME_MA20),
        )
        for r in results
    ]
    inserted = store.write_scr1c(rows)
    log.info(
        "SCR-1C %s: %d stealth-divergence candidate(s), %d newly cached (as_of=%s)",
        trading_day, len(rows), inserted, now,
    )
    return rows
