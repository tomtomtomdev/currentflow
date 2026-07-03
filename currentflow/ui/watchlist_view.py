"""ARMED watchlist rail view-model (design/SCREENS_terminal.md right rail) — pure data shaping,
no Streamlit.

RULE B: a row carries a state WORD (ARMED/WATCH) and the five design spark-bar
component strengths (DIV BRK FF RVOL BLK) — raw observation. The composite SMS number,
a rank number, and buy/sell verbs are never emitted here; only the SMS/Rank module may
reveal the number, and only once VALIDATED. Ordering within a state is flow-derived
(same posture as `ranking_view`): the ordering is observation, the number is a claim.

Gate-rejected (RULE A) and vetoed names never reach the rail — there is nothing to
watch. `rows` caps the list for the sidebar but reports what the cap hid (`dropped`,
`total`) so the cap is never silent.
"""

from __future__ import annotations

from currentflow.signals.engine import EngineResult, EngineState
from currentflow.ui.sms_view import WATCHLIST_FRAMING

FRAMING = WATCHLIST_FRAMING

# design spark-bar labels, in design order (design/SCREENS_terminal.md: `DIV BRK FF RVOL BLK`)
SPARK_LABELS = {
    "divergence": "DIV",
    "broker_concentration": "BRK",
    "foreign_flow": "FF",
    "rvol": "RVOL",
    "block_trade": "BLK",
}

_STATE_WORD = {EngineState.ARMED: "ARMED", EngineState.WATCH: "WATCH"}
_ORDER = {EngineState.ARMED: 0, EngineState.WATCH: 1}


def rows(results: list[EngineResult], *, limit: int = 8) -> dict:
    """ARMED first, then WATCH; strongest flow first within a state. Returns
    `{rows, total, dropped, framing}` — `dropped` reports what `limit` hid."""
    kept = sorted(
        (r for r in results if r.state in _ORDER),
        key=lambda r: (_ORDER[r.state], -r.sms.internal_score),
    )
    shown = [
        {
            "symbol": r.symbol,
            "track": r.track,
            "state": _STATE_WORD[r.state],
            "components": {
                SPARK_LABELS[c.key]: (round(c.subscore * 100) if c.available else None)
                for c in r.sms.components
                if c.key in SPARK_LABELS
            },
        }
        for r in kept[:limit]
    ]
    return {
        "rows": shown,
        "total": len(kept),
        "dropped": max(len(kept) - limit, 0),
        "framing": FRAMING,
    }


def spark_line(row: dict) -> str:
    """One compact mono line of component strengths; `—` when a component is
    unavailable (missing ≠ zero)."""
    return " · ".join(
        f"{label} {value if value is not None else '—'}"
        for label, value in row["components"].items()
    )
