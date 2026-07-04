"""Broker Flow Analyzer view-model — pure data shaping, no Streamlit imports.

Everything here is an *observation* (raw flow measurements). Framing rules
(RULE B / spec §15): no probability, no score, no buy/sell verb anywhere in the
strings this module produces.
"""

from __future__ import annotations

from currentflow.dal.models import DailyBar, RowStatus
from currentflow.signals.broker_flow import BrokerFlowSnapshot
from currentflow.signals.veto import VetoReason, VetoResult

OBSERVATION_BADGE = "OBSERVATION · ships now"
DISCLAIMER = (
    "Private personal-use analytics tool — observation, not a recommendation. "
    "Not investment advice. Paper only. Own-session data, used at own risk, "
    "nothing republished."
)

_IDR_BN = 1e9


def _bn(v: float) -> float:
    return round(v / _IDR_BN, 2)


def broker_table(snapshot: BrokerFlowSnapshot, *, persist_dots: int = 7) -> list[dict]:
    """Rows for the Broker Net Flow table: # | BROKER·DNA | NET (bn) | PERSIST."""
    return [
        {
            "#": i + 1,
            "broker": s.broker_code,
            "dna": s.dna.value,
            "net_idr_bn": _bn(s.net_value),
            "buy_idr_bn": _bn(s.buy_value),
            "sell_idr_bn": _bn(s.sell_value),
            "persist": "●" * min(s.persistence_days, persist_dots)
            + "○" * max(persist_dots - s.persistence_days, 0),
            "accum_vwap": s.avg_price,
        }
        for i, s in enumerate(snapshot.brokers)
    ]


def hhi_label(hhi: float) -> str:
    if hhi < 0.15:
        return "dispersed"
    if hhi <= 0.25:
        return "concentrated"
    return "highly concentrated"


def concentration_panel(snapshot: BrokerFlowSnapshot) -> dict:
    """Top-2 net-buy share + Herfindahl, with the design's qualitative label."""
    top2 = snapshot.top_buyers[:2]
    return {
        "top2_share_pct": None if snapshot.top2_share is None else round(snapshot.top2_share * 100, 1),
        "hhi": None if snapshot.hhi is None else round(snapshot.hhi, 2),
        "hhi_label": None if snapshot.hhi is None else hhi_label(snapshot.hhi),
        "top2_names": ", ".join(b.broker_code for b in top2) if top2 else None,
    }


# §5 taxonomy → design panel labels (full v1.1 list — no filter is silently hidden).
VETO_LABELS: dict[VetoReason, str] = {
    VetoReason.SINGLE_BANDAR_MONOPOLY: "Single-bandar monopoly (>60% net-buy)",
    VetoReason.DISTRIBUTION_DRESSED: "Distribution-dressed-as-accumulation",
    VetoReason.MARKUP_ON_THIN_VOLUME: "Markup on thin volume",
    VetoReason.WASH_CHURN: "Wash / churn (manufactured volume)",
    VetoReason.BROKER_ROTATION: "Broker rotation (baton passing)",
    VetoReason.RETAIL_FOMO: "Retail-FOMO (buy ratio >60%)",
    VetoReason.EVENT_DRIVEN: "Event-driven (material news in window)",
    VetoReason.PHASE_MISMATCH: "Phase mismatch (RULE A: only C/D)",
}


def veto_checks(result: VetoResult) -> list[dict]:
    """Rows for the Veto Checks panel: every §5 filter, fired-or-clear, with the
    observation that tripped it. Fired checks sort first (most relevant on top)."""
    fired = {v.reason: v.detail for v in result.vetoes}
    rows = [
        {"check": reason.value, "label": label, "fired": reason in fired,
         "detail": fired.get(reason)}
        for reason, label in VETO_LABELS.items()
    ]
    rows.sort(key=lambda r: not r["fired"])
    return rows


def stock_header(bars: list[DailyBar], *, adv_window: int = 20) -> dict:
    """Price / Δ% / 20-day ADV for the design's stock-header row. Every field is
    None when unknowable (missing ≠ zero; shown as absent, never faked)."""
    traded = [b for b in sorted(bars, key=lambda b: b.date)
              if b.status is RowStatus.TRADED and b.close is not None]
    if not traded:
        return {"price": None, "change_pct": None, "adv_bn": None}
    last = traded[-1]
    change = last.change_percentage
    if change is None and len(traded) >= 2 and traded[-2].close:
        change = (last.close - traded[-2].close) / traded[-2].close * 100
    values = [b.value for b in traded[-adv_window:] if b.value is not None]
    return {
        "price": last.close,
        "change_pct": None if change is None else round(change, 2),
        "adv_bn": round(sum(values) / len(values) / _IDR_BN, 1) if values else None,
    }


def matrix_table(
    matrix: dict[str, dict[str, float]], symbols: list[str]
) -> list[dict]:
    """Broker × Stock matrix rows (net value in IDR bn; None = not a top participant)."""
    return [
        {
            "broker": code,
            **{
                sym: (None if sym not in by_sym else _bn(by_sym[sym]))
                for sym in symbols
            },
        }
        for code, by_sym in matrix.items()
    ]
