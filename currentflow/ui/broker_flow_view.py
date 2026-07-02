"""Broker Flow Analyzer view-model — pure data shaping, no Streamlit imports.

Everything here is an *observation* (raw flow measurements). Framing rules
(RULE B / spec §15): no probability, no score, no buy/sell verb anywhere in the
strings this module produces.
"""

from __future__ import annotations

from currentflow.signals.broker_flow import BrokerFlowSnapshot

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
