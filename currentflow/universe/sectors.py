"""Operator sector map (spec §3 — "tag sector").

A single source of truth for symbol → sector, shared by the UI (Sector Rotation, Risk
Monitor, pipeline metadata) and the engine-side Fast-Mode auto-trader (the §6 30%/sector
exposure cap + the §7 FLOW_ONLY tilt). Like the broker-DNA registry it is **operator
knowledge** to verify and extend; an unmapped symbol falls back to `UNKNOWN` — never
silently grouped (missing ≠ zero).
"""

from __future__ import annotations

OPERATOR_SECTOR_MAP: dict[str, str] = {
    "BRMS": "Basic Materials", "NCKL": "Basic Materials", "MBMA": "Basic Materials",
    "PTRO": "Energy", "RAJA": "Energy", "CUAN": "Energy", "DEWA": "Energy",
}

UNKNOWN_SECTOR = "UNKNOWN"


def sector_for(symbol: str) -> str:
    """The operator's sector for `symbol`, or `UNKNOWN` when unmapped (never guessed)."""
    return OPERATOR_SECTOR_MAP.get(symbol, UNKNOWN_SECTOR)
