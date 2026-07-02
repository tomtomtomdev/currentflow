"""Institutional Accumulation Detector view-model — pure data shaping, no Streamlit.

Observation only (RULE B): every field is a raw measurement; no score, no probability,
no buy/sell verb in any string produced here.
"""

from __future__ import annotations

from currentflow.signals.accumulation import AccumulationSnapshot

_IDR_BN = 1e9


def _bn(v: float | None) -> float | None:
    return None if v is None else round(v / _IDR_BN, 2)


def _pct(v: float | None) -> float | None:
    return None if v is None else round(v * 100, 2)


def accumulation_panel(snap: AccumulationSnapshot) -> dict:
    return {
        "window": f"{snap.start} → {snap.end}",
        "price_change_pct": _pct(snap.price_change_pct),
        "accumulator": snap.accumulator,
        "net_accumulation_bn": _bn(snap.net_accumulation),
        "accumulation_rising": snap.accumulation_rising,
        "accumulator_vwap": snap.accumulator_vwap,
        "price_vs_vwap_pct": _pct(snap.price_vs_vwap_pct),
        "volume_dryup_ratio": None if snap.volume_dryup_ratio is None else round(snap.volume_dryup_ratio, 2),
        "price_tightness_pct": _pct(snap.price_tightness),
        "absorption": "unavailable (needs L2 depth)" if snap.absorption is None else snap.absorption,
    }


def stealth_callout(snap: AccumulationSnapshot) -> str | None:
    """Neutral observation of a stealth-divergence read — never advice."""
    if not snap.stealth_divergence:
        return None
    pc = _pct(snap.price_change_pct)
    who = snap.accumulator or "top broker"
    return (
        f"Stealth divergence: price {pc:+}% over the window while {who} kept "
        "accumulating (rising net) — observation, not a recommendation."
    )
