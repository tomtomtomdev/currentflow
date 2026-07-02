"""Foreign Flow Dashboard view-model — pure data shaping, no Streamlit imports.

Everything here is an *observation* (raw flow measurements). Framing rules
(RULE B / spec §15): no probability, no score, no buy/sell advice verb — "net BUY"
below describes the direction of the observed flow, per the design handoff copy.
"""

from __future__ import annotations

from currentflow.dal.models import Side
from currentflow.signals.foreign_flow import ForeignFlowSnapshot, TideRow

_IDR_BN = 1e9


def _bn(v: float | None) -> float | None:
    return None if v is None else round(v / _IDR_BN, 2)


def stats_panel(snapshot: ForeignFlowSnapshot, *, persist_dots: int = 6) -> dict:
    """Right-column Foreign Flow Stats (design module 2)."""
    side = snapshot.persistence_side
    return {
        "net_today_bn": _bn(snapshot.net_last),
        "cum_5d_bn": _bn(snapshot.cum_5d),
        "persistence": f"{min(snapshot.persistence_days, persist_dots)}/{persist_dots}",
        "persistence_side": side.value if side else None,
        "vs_20d_avg": None if snapshot.vs_20d_avg is None else round(snapshot.vs_20d_avg, 1),
        "zscore_20d": None if snapshot.zscore_20d is None else round(snapshot.zscore_20d, 2),
        "avg_window_used": snapshot.avg_window_used,
    }


def reversal_callout(snapshot: ForeignFlowSnapshot) -> str | None:
    """'Foreign flow reversed to net BUY on {date} — N-day persistence.' (design copy)."""
    r = snapshot.reversal
    if r is None:
        return None
    direction = "BUY" if r.side is Side.BUY else "SELL"
    return (
        f"Foreign flow reversed to net {direction} on {r.date.isoformat()} — "
        f"{r.persistence_days}-day persistence."
    )


def split_bar(snapshot: ForeignFlowSnapshot) -> dict:
    """Foreign vs domestic split, most recent day. Net domestic mirrors net foreign
    by construction (two sides to every trade); participation is the turnover share."""
    net = snapshot.net_last
    share = snapshot.foreign_turnover_share
    return {
        "foreign_net_bn": _bn(net),
        "domestic_net_bn": _bn(-net) if net is not None else None,
        "foreign_turnover_share_pct": None if share is None else round(share * 100, 1),
    }


def daily_series(snapshot: ForeignFlowSnapshot) -> list[dict]:
    """Bottom chart lane: daily NBSA bars around a zero baseline."""
    return [
        {"date": d, "net_foreign_bn": _bn(v)} for d, v in sorted(snapshot.daily_net.items())
    ]


def cumulative_series(snapshot: ForeignFlowSnapshot) -> list[dict]:
    """Top chart lane: cumulative NBSA over the window."""
    return [{"date": d, "cumulative_bn": _bn(v)} for d, v in snapshot.cumulative]


def ksei_panel(snapshot: ForeignFlowSnapshot, *, points: int = 6) -> dict:
    """KSEI monthly ownership sparkline + trend label + vs-free-float gauge."""
    slices = [s for s in snapshot.ksei if s.foreign_pct is not None][-points:]
    series = [{"month": s.date, "foreign_pct": s.foreign_pct} for s in slices]
    trend = None
    if len(slices) >= 2:
        delta = slices[-1].foreign_pct - slices[0].foreign_pct
        trend = "rising" if delta > 0.1 else "easing" if delta < -0.1 else "flat"
    return {
        "series": series,
        "trend": trend,
        "foreign_own_pct": slices[-1].foreign_pct if slices else None,
        "nbsa_pct_of_float": (
            None
            if snapshot.nbsa_pct_of_float is None
            else round(snapshot.nbsa_pct_of_float, 2)
        ),
    }


def tide_table(rows: list[TideRow]) -> list[dict]:
    """Market/sector tide rows: aggregate NBSA per scope."""
    return [
        {"scope": r.scope, "net_foreign_bn": _bn(r.net_foreign), "symbols": r.symbols}
        for r in rows
    ]
