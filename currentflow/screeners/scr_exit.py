"""SCR-EXIT · Distribution / Mirror warning (screeners.md; spec §8 signal-decay exit).

Runs continuously over open positions + ARMED names: a name in a distribution regime
(Bandar Accum/Dist < 0) with foreign outflow (Net Foreign MA20 < 0) and a persistent
foreign sell streak (> 2 days). Any hit → a signal-decay flag. Cached as an observation
candidate list, never a score (RULE B).

The Stockbit screener evaluates the whole market server-side; `exit_flags_for` then
intersects the survivors with the operator's open + ARMED watchlist so the flag surfaces
only where it matters — logging how many market-wide survivors sat outside the watchlist
(no silent caps).

Engine residual (NOT screenable, computed downstream by `signals.distribution`): UTAD /
no-demand VPA prints, dominant-broker flip to net sell, price-vs-flow bearish divergence
(spec §8: "divergence is the single best exit signal").
"""

from __future__ import annotations

import json
import logging
from datetime import date as Date
from datetime import datetime

from currentflow.dal.client import ExodusClient
from currentflow.dal.models import ScrExitRow
from currentflow.store.db import Store

log = logging.getLogger(__name__)

# fitem ids (screeners.md §2, category 93 Bandarmology)
FITEM_BANDAR_ACCUM_DIST = 14400
FITEM_NET_FOREIGN_MA20 = 13540
FITEM_NET_FOREIGN_SELL_STREAK = 13562

# Template exactly as pinned in screeners.md (SCR-EXIT).
SCR_EXIT_TEMPLATE: dict = {
    "name": "scr-exit-distribution",
    "type": "TEMPLATE_TYPE_CUSTOM",
    "ordercol": 0,
    "ordertype": "ASC",
    "sequence": "14400,13540,13562",
    "universe": json.dumps({"scope": "IHSG", "scopeID": "0", "name": "IHSG"}),
    "filters": json.dumps(
        [
            {
                "item1": FITEM_BANDAR_ACCUM_DIST, "item1_name": "Bandar Accum/Dist",
                "item2": "0", "item2_name": "",
                "multiplier": "0", "operator": "<", "type": "basic",
            },
            {
                "item1": FITEM_NET_FOREIGN_MA20, "item1_name": "Net Foreign Buy / Sell MA20",
                "item2": "0", "item2_name": "",
                "multiplier": "0", "operator": "<", "type": "basic",
            },
            {
                "item1": FITEM_NET_FOREIGN_SELL_STREAK, "item1_name": "Net Foreign Sell Streak",
                "item2": "2", "item2_name": "",
                "multiplier": "0", "operator": ">", "type": "basic",
            },
        ]
    ),
}


async def run_scr_exit(
    client: ExodusClient,
    store: Store,
    *,
    trading_day: Date,
    now: datetime,
) -> list[ScrExitRow]:
    """Run SCR-EXIT, cache survivors to DuckDB with `as_of = now`, return them.

    Re-running for a stored (symbol, date, as_of) is a no-op (ingest-once).
    """
    results = await client.run_screener(SCR_EXIT_TEMPLATE)
    rows = [
        ScrExitRow(
            symbol=r["symbol"],
            date=trading_day,
            as_of=now,
            bandar_accum_dist=r["values"].get(FITEM_BANDAR_ACCUM_DIST),
            net_foreign_ma20=r["values"].get(FITEM_NET_FOREIGN_MA20),
            foreign_sell_streak=r["values"].get(FITEM_NET_FOREIGN_SELL_STREAK),
        )
        for r in results
    ]
    inserted = store.write_scr_exit(rows)
    log.info(
        "SCR-EXIT %s: %d distribution/mirror survivor(s), %d newly cached (as_of=%s)",
        trading_day, len(rows), inserted, now,
    )
    return rows


def exit_flags_for(
    store: Store,
    watchlist: set[str],
    *,
    day: Date,
    decision_ts: datetime,
) -> list[ScrExitRow]:
    """SCR-EXIT survivors that are on the operator's open + ARMED `watchlist`, as
    visible at `decision_ts`. The screener scans the whole market; this narrows it to
    the names the operator actually holds or is watching (spec §8 cadence). Survivors
    outside the watchlist are logged, never silently dropped."""
    survivors = store.read_scr_exit(day, decision_ts)
    on_watch = [r for r in survivors if r.symbol in watchlist]
    off_watch = len(survivors) - len(on_watch)
    if off_watch:
        log.info(
            "SCR-EXIT %s: %d survivor(s) off the open+ARMED watchlist (not flagged here)",
            day, off_watch,
        )
    return on_watch
