"""Sector Rotation Map view-model — pure data shaping, no Streamlit.

A derived visualization (RULE B): relative strength and net flow are raw measurements;
the quadrant is a categorical observation of a sector's (RS, flow) position (the spec's
own §9 labels), never a ranked buy/sell claim.
"""

from __future__ import annotations

from currentflow.signals.sector_rotation import Quadrant, SectorRotation

_IDR_BN = 1e9

# Observation-framed note per quadrant (§9). Descriptive, not advice.
QUADRANT_NOTE = {
    Quadrant.LEADERS: "strong relative strength on net inflow",
    Quadrant.EARLY_RECOVERY: "net inflow ahead of relative strength (early)",
    Quadrant.DISTRIBUTION_WARN: "strength persisting while flow leaves (watch)",
    Quadrant.AVOID: "weak relative strength on net outflow",
}


def _bn(v: float | None) -> float | None:
    return None if v is None else round(v / _IDR_BN, 2)


def _pct(v: float | None) -> float | None:
    return None if v is None else round(v * 100, 2)


def quadrant_label(rot: SectorRotation) -> str:
    return rot.quadrant.value if rot.quadrant else "—"


def sector_rows(rotations: list[SectorRotation]) -> list[dict]:
    """One row per sector for the right-column cards (flow / RS / tide line)."""
    return [
        {
            "sector": r.sector,
            "quadrant": quadrant_label(r),
            "note": QUADRANT_NOTE.get(r.quadrant, "insufficient data") if r.quadrant else "insufficient data",
            "symbols": r.symbols,
            "net_foreign_flow_bn": _bn(r.net_foreign_flow),
            "relative_strength_pct": _pct(r.relative_strength),
            "tide": r.tide,
        }
        for r in rotations
    ]


def scatter_points(rotations: list[SectorRotation]) -> list[dict]:
    """Quadrant-scatter points: x = relative strength, y = net flow, radius = |flow|.
    Sectors missing an axis are skipped (nothing to place — never at a fake origin)."""
    return [
        {
            "sector": r.sector,
            "x_relative_strength_pct": _pct(r.relative_strength),
            "y_net_flow_bn": _bn(r.net_foreign_flow),
            "radius_flow_bn": abs(_bn(r.net_foreign_flow)),
            "quadrant": r.quadrant.value,
        }
        for r in rotations
        if r.quadrant is not None and r.net_foreign_flow is not None
    ]
