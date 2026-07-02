"""Money Flow Replay view-model — pure data shaping, no Streamlit imports.

The replay is the audit surface: every value shown at a playhead position was
computed from store reads at that day's historical `decision_ts`. RULE B: raw
measurements only. The Wyckoff phase box carries the slice-4 classifier's *label*
(a RULE A gate verdict, not a number) reconstructed at that frame's decision moment.
"""

from __future__ import annotations

from currentflow.signals.replay import ReplayFrame, ReplaySeries

# Human labels for the phase lane (RULE A gate verdict; C/D are the tradeable window).
_PHASE_LABEL = {
    "UNKNOWN": "Wyckoff phase — insufficient history to confirm",
    "DOWNTREND": "Wyckoff — downtrend (no stopping action)",
    "A": "Wyckoff Phase A — stopping action",
    "B": "Wyckoff Phase B — building cause",
    "C": "Wyckoff Phase C — spring/test (tradeable)",
    "D": "Wyckoff Phase D — SOS/LPS markup (tradeable)",
    "E": "Wyckoff Phase E — markup (too late)",
    "DISTRIBUTION": "Wyckoff — distribution (avoid)",
}

_IDR_BN = 1e9


def _bn(v: float | None) -> float | None:
    return None if v is None else round(v / _IDR_BN, 2)


def phase_label(phase: str | None) -> str:
    return _PHASE_LABEL.get(phase or "UNKNOWN", "Wyckoff phase — unavailable")


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
        "phase": phase_label(frame.phase),
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
