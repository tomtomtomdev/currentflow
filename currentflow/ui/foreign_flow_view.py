"""Foreign Flow Dashboard view-model — pure data shaping, no Streamlit imports.

Everything here is an *observation* (raw flow measurements). Framing rules
(RULE B / spec §15): no probability, no score, no buy/sell advice verb — "net BUY"
below describes the direction of the observed flow, per the design handoff copy.
"""

from __future__ import annotations

from currentflow.dal.models import Side
from currentflow.signals.foreign_flow import ForeignFlowSnapshot, TideRow
from currentflow.signals.ownership import OwnershipDelta, OwnershipKind

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
    own_pct = slices[-1].foreign_pct if slices else None
    float_pct = snapshot.free_float_pct
    # Bar fills to foreign's share of the *free-float* (design: "35% of 38% free-float").
    own_of_float_pct = (
        round(min(own_pct / float_pct * 100, 100), 1)
        if own_pct is not None and float_pct
        else None
    )
    return {
        "series": series,
        "trend": trend,
        "foreign_own_pct": own_pct,
        "free_float_pct": None if float_pct is None else round(float_pct, 1),
        "own_of_float_pct": own_of_float_pct,
        "nbsa_pct_of_float": (
            None
            if snapshot.nbsa_pct_of_float is None
            else round(snapshot.nbsa_pct_of_float, 2)
        ),
    }


# --- institutional ownership (LD-13, slice 17) — CATEGORICAL observation --------------
#
# The KSEI overlay was a display sparkline; this promotes it to a reading. Every string
# below is digit-free on purpose (RULE B / §4.1): the composition speaks as a *category*
# and a severity word, never as a magnitude the operator could mistake for a score. The
# raw pp measurement stays on the signal's `detail` for audit, not in this panel's copy.
_OWNERSHIP_COPY = {
    OwnershipKind.ACCUMULATION_CONFIRMED: (
        "institutional share rising across the range",
        "Slow money is still coming in — the composition confirms accumulation over the "
        "range. Monthly cadence: a confirmation, never a daily driver.",
    ),
    OwnershipKind.DISTRIBUTION_TELL: (
        "institutional share falling while price is marked up",
        "Shares are leaving the institutional book while the price is held flat or marked "
        "higher — the classic distribution dressing. Corroborates the distribution veto "
        "filter; it never rejects a name on its own.",
    ),
    OwnershipKind.OWNERSHIP_EASING: (
        "institutional share easing with the price",
        "Holders are stepping back while the price falls too — an exit, not a markup being "
        "dressed. Read it as context.",
    ),
    OwnershipKind.OWNERSHIP_FLAT: (
        "institutional share unchanged",
        "The composition has not moved beyond feed noise across the observed slices.",
    ),
    OwnershipKind.STALE_COMPOSITION: (
        "composition too old to read",
        "The latest KSEI slice predates this range, so it says nothing about the current "
        "markup. Reads neutral — a stale series never flags distribution.",
    ),
    OwnershipKind.NO_COMPOSITION: (
        "no composition on file",
        "Fewer than two KSEI slices are visible at this moment. Not yet published is not "
        "no change — nothing is inferred.",
    ),
}


def ownership_panel(delta: OwnershipDelta) -> dict:
    """KSEI composition as a categorical observation (LD-13 §4.1).

    Categorical only: `kind` + `severity` + digit-free copy. No score, no probability,
    no buy/sell verb — and no magnitude, so nothing here can read as a claim."""
    headline, detail = _OWNERSHIP_COPY[delta.kind]
    return {
        "kind": delta.kind.value,
        "severity": delta.severity.value,
        "headline": headline,
        "detail": detail,
        "available": delta.available,
        "stale": delta.stale,
        "slices_used": delta.slices_used,       # data-availability count, not a measurement
        "corroborates_distribution": delta.corroborates_distribution,
    }


def tide_table(rows: list[TideRow]) -> list[dict]:
    """Market/sector tide rows: aggregate NBSA per scope."""
    return [
        {"scope": r.scope, "net_foreign_bn": _bn(r.net_foreign), "symbols": r.symbols}
        for r in rows
    ]
