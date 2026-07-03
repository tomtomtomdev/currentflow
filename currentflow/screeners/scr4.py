"""SCR-4 · Fundamental Tilt reference (screeners.md; spec §7) — a RANKING pull, NOT a gate.

Sorts survivors by Magic Formula inputs to set the conviction multiplier / hold horizon.
Fundamentals never block entry (LD-6), so this screener carries **no filters** — it is a
sorted attribute pull. Sequence leads with `13474` Rank(Magic Formula)(%), Stockbit's
combined Greenblatt rank, used directly (no manual summing) → tercile → COMPOUNDER /
NEUTRAL / SPECULATIVE via `fundamentals.tilt`.

Engine residual: the FLOW_ONLY dual track (financials/utilities skip MF, use an ROE proxy)
and point-in-time fundamentals for backtest (DATA_SOURCES §3.1). Cached as tilt inputs.
"""

from __future__ import annotations

import json
import logging
from datetime import date as Date
from datetime import datetime

from currentflow.dal.client import ExodusClient
from currentflow.dal.models import Scr4Row
from currentflow.store.db import Store

log = logging.getLogger(__name__)

# fitem ids (screeners.md §2 / SCR-4 sequence)
FITEM_MF_RANK = 13474        # Rank(Magic Formula)(%) — combined Greenblatt rank
FITEM_ROC_GREENBLATT = 13411
FITEM_EV_EBIT = 2897         # EY = 1 / this
FITEM_RANK_ROIC = 15276
FITEM_ROE = 1461             # bank / FLOW_ONLY sector proxy
FITEM_MARKET_CAP = 2892

# Template exactly as pinned in screeners.md (SCR-4) — ranking pull, no filters.
SCR4_TEMPLATE: dict = {
    "name": "scr4-fundamental-tilt",
    "type": "TEMPLATE_TYPE_CUSTOM",
    "ordercol": 0,
    "ordertype": "DESC",
    "sequence": "13474,13411,2897,15276,1461,2892",
    "universe": json.dumps({"scope": "IHSG", "scopeID": "0", "name": "IHSG"}),
    "filters": json.dumps([]),
}


async def run_scr4(
    client: ExodusClient,
    store: Store,
    *,
    trading_day: Date,
    now: datetime,
) -> list[Scr4Row]:
    """Run SCR-4, cache the tilt reference rows to DuckDB with `as_of = now`, return them.

    Re-running for a stored (symbol, date, as_of) is a no-op (ingest-once).
    """
    results = await client.run_screener(SCR4_TEMPLATE)
    rows = [
        Scr4Row(
            symbol=r["symbol"],
            date=trading_day,
            as_of=now,
            mf_rank_pct=r["values"].get(FITEM_MF_RANK),
            roc_greenblatt=r["values"].get(FITEM_ROC_GREENBLATT),
            ev_ebit=r["values"].get(FITEM_EV_EBIT),
            rank_roic=r["values"].get(FITEM_RANK_ROIC),
            roe=r["values"].get(FITEM_ROE),
            market_cap=r["values"].get(FITEM_MARKET_CAP),
        )
        for r in results
    ]
    inserted = store.write_scr4(rows)
    log.info(
        "SCR-4 %s: %d tilt reference row(s), %d newly cached (as_of=%s)",
        trading_day, len(rows), inserted, now,
    )
    return rows
