"""Smart Money Heatmap view-model — pure data shaping, no Streamlit.

A derived visualization (RULE B): direction + intensity (flow-as-%-of-cap) are raw
measurements, never a score. Divergence alerts are categorical, not ranked.
"""

from __future__ import annotations

from currentflow.signals.heatmap import HeatCell, by_sector

_IDR_BN = 1e9


def _bn(v: float | None) -> float | None:
    return None if v is None else round(v / _IDR_BN, 2)


def heatmap_rows(cells: list[HeatCell]) -> list[dict]:
    """Flat table rows for the sector → stock grid."""
    return [
        {
            "sector": c.sector,
            "symbol": c.symbol,
            "direction": c.direction,
            "foreign_net_bn": _bn(c.foreign_net),
            "local_smart_net_bn": _bn(c.local_smart_net),
            "intensity_pct_of_cap": c.intensity_pct_of_cap,
            "divergence": "◆ local buy / foreign sell" if c.divergence_alert else "",
        }
        for c in cells
    ]


def sector_totals(cells: list[HeatCell]) -> list[dict]:
    """Sector roll-up: net foreign flow and how many names carry a divergence alert."""
    rows = []
    for sector, group in by_sector(cells).items():
        foreign = [c.foreign_net for c in group if c.foreign_net is not None]
        rows.append(
            {
                "sector": sector,
                "symbols": len(group),
                "foreign_net_bn": _bn(sum(foreign)) if foreign else None,
                "divergence_alerts": sum(1 for c in group if c.divergence_alert),
            }
        )
    return sorted(rows, key=lambda r: r["sector"])


def divergence_alerts(cells: list[HeatCell]) -> list[str]:
    return [
        f"{c.symbol} ({c.sector}): local smart-money buying while foreign sells"
        for c in cells if c.divergence_alert
    ]
