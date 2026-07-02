"""Money Flow Replay view-model — pure data shaping, no Streamlit imports.

The replay is the audit surface: every value shown at a playhead position was
computed from store reads at that day's historical `decision_ts`. RULE B: raw
measurements only; the Wyckoff phase box stays a placeholder until the slice-4
classifier exists (RULE A) — never fabricated here.
"""

from __future__ import annotations

from currentflow.signals.replay import ReplayFrame, ReplaySeries

PHASE_PLACEHOLDER = (
    "Wyckoff phase — classifier lands in slice 4 (RULE A hard gate); "
    "not shown until it exists."
)

_IDR_BN = 1e9


def _bn(v: float | None) -> float | None:
    return None if v is None else round(v / _IDR_BN, 2)


def playhead_panel(frame: ReplayFrame) -> dict:
    """The 'At Playhead' panel (design module 4). None = not knowable then, not zero."""
    return {
        "date": frame.date,
        "as_knowable_at": frame.decision_ts,
        "close": frame.close,
        "change_pct": None if frame.change_pct is None else round(frame.change_pct, 2),
        "volume": frame.volume,
        "rvol_20d": None if frame.rvol_20d is None else round(frame.rvol_20d, 2),
        "net_foreign_bn": _bn(frame.net_foreign),
        "broker_net_bn": _bn(frame.broker_net_total),
        "smart_money_net_bn": _bn(frame.smart_money_net),
        "phase": PHASE_PLACEHOLDER,
    }


def visible_rows(series: ReplaySeries, playhead: int) -> list[dict]:
    """Chart rows up to (and including) the playhead index. Each row carries the
    values as they were knowable at *its own* decision moment — the audit framing.
    Rows past the playhead are withheld entirely (the future is not merely dimmed)."""
    rows = []
    for f in series.frames[: playhead + 1]:
        rows.append(
            {
                "date": f.date,
                "close": f.close,
                "volume": f.volume,
                "net_foreign_bn": _bn(f.net_foreign),
                "smart_money_net_bn": _bn(f.smart_money_net),
            }
        )
    return rows
