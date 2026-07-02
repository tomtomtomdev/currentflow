"""Smart Money Heatmap (spec §9) — a derived VISUALIZATION over the flow/foreign/
accumulation signals. Rendering, not a new signal.

Each cell: direction (sign of flow), intensity (|flow| as % of market cap), and a
divergence alert when local smart-money buys while foreign sells (or vice versa) — the
classic bandar-vs-foreign disagreement. Sector → stock → broker drill-down.

RULE B: intensity and net flow are raw measurements (flow-as-%-of-cap), not a score or
probability. `missing ≠ zero`: a symbol with no visible flow is skipped and logged by
the reader, never rendered as a flat-zero cell.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date as Date
from datetime import datetime

from currentflow.signals import broker_flow, foreign_flow
from currentflow.signals.broker_flow import BrokerDNA
from currentflow.store.db import Store

log = logging.getLogger(__name__)

_UNKNOWN_SECTOR = "UNKNOWN"
_LOCAL_DNA = frozenset({BrokerDNA.LOCAL_INST, BrokerDNA.SMART_MONEY})


@dataclass(frozen=True, slots=True)
class HeatCell:
    symbol: str
    sector: str
    foreign_net: float | None            # signed NBSA, latest visible day (IDR)
    local_smart_net: float               # window net of LOCAL_INST + SMART_MONEY brokers (IDR)
    direction: str                       # BUY | SELL | NEUTRAL (sign of foreign flow)
    intensity_pct_of_cap: float | None   # |foreign_net| / market_cap · 100
    divergence_alert: bool               # local smart-money buy WHILE foreign sell (or vice versa)


def _direction(net: float | None) -> str:
    if net is None or net == 0:
        return "NEUTRAL"
    return "BUY" if net > 0 else "SELL"


def build_cell(
    symbol: str,
    *,
    sector: str,
    foreign_net: float | None,
    local_smart_net: float,
    market_cap: float | None,
) -> HeatCell:
    intensity = None
    if foreign_net is not None and market_cap:
        intensity = abs(foreign_net) / market_cap * 100
    divergence = foreign_net is not None and (
        (local_smart_net > 0 and foreign_net < 0) or (local_smart_net < 0 and foreign_net > 0)
    )
    return HeatCell(
        symbol=symbol, sector=sector, foreign_net=foreign_net,
        local_smart_net=local_smart_net, direction=_direction(foreign_net),
        intensity_pct_of_cap=None if intensity is None else round(intensity, 4),
        divergence_alert=divergence,
    )


def heatmap(
    store: Store,
    symbols: list[str],
    decision_ts: datetime,
    *,
    sector_map: dict[str, str] | None = None,
    start: Date | None = None,
    end: Date | None = None,
    registry: dict[str, BrokerDNA] | None = None,
) -> list[HeatCell]:
    """One cell per symbol with visible flow. Symbols with no flow are skipped and
    logged (no silent caps, never a fabricated zero cell)."""
    cells: list[HeatCell] = []
    skipped = 0
    for sym in symbols:
        fsnap = foreign_flow.analyze(store, sym, decision_ts, start=start, end=end)
        bsnap = broker_flow.analyze(store, sym, decision_ts, start=start, end=end, registry=registry)
        local_smart = sum(b.net_value for b in bsnap.brokers if b.dna in _LOCAL_DNA)
        if fsnap.net_last is None and not bsnap.brokers:
            skipped += 1
            continue
        scr0 = store.read_scr0_latest(sym, decision_ts)
        cells.append(
            build_cell(
                sym,
                sector=(sector_map or {}).get(sym, _UNKNOWN_SECTOR),
                foreign_net=fsnap.net_last,
                local_smart_net=local_smart,
                market_cap=scr0.market_cap if scr0 else None,
            )
        )
    if skipped:
        log.warning(
            "heatmap: %d/%d symbol(s) had no visible flow — skipped, not zeroed",
            skipped, len(symbols),
        )
    return cells


def by_sector(cells: list[HeatCell]) -> dict[str, list[HeatCell]]:
    """Group cells for the sector → stock drill-down."""
    out: dict[str, list[HeatCell]] = {}
    for c in cells:
        out.setdefault(c.sector, []).append(c)
    return out
