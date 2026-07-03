"""Institutional Accumulation Detector view-model — pure data shaping, no Streamlit.

Observation only (RULE B): every field is a raw measurement; no score, no probability,
no buy/sell verb in any string produced here.
"""

from __future__ import annotations

from currentflow.dal.models import DailyBar, RowStatus
from currentflow.signals.accumulation import AccumulationSnapshot
from currentflow.signals.broker_flow import BrokerFlowSnapshot

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


def chart_rows(bars: list[DailyBar], broker: BrokerFlowSnapshot) -> list[dict]:
    """Series for the design's accumulation chart: price lane + the accumulator's
    cumulative net (IDR bn) + its VWAP as a reference line. A traded day with no
    broker rows renders as a gap (`None`) in the accumulation lane — missing ≠ zero."""
    usable = [
        b for b in sorted(bars, key=lambda b: b.date)
        if b.status is RowStatus.TRADED and None not in (b.high, b.low, b.close, b.volume)
    ]
    buyers = broker.top_buyers
    code = buyers[0].broker_code if buyers else None
    vwap = buyers[0].avg_price if buyers else None

    cum = 0.0
    out: list[dict] = []
    for b in usable:
        cum_bn = None
        if code is not None and b.date in broker.daily_nets:
            cum += broker.daily_nets[b.date].get(code, 0.0)
            cum_bn = round(cum / _IDR_BN, 2)
        out.append(
            {
                "date": b.date,
                "close": b.close,
                "accumulator_vwap": vwap,
                "cum_accumulation_bn": cum_bn,
            }
        )
    return out


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
