"""Track A/B assignment (spec §3 / LD-1) — the single source of truth for the rule.

    Track A — LQ45/IDX80 member AND ADV ≥ IDR 25 bn → foreign-flow-reliable
    Track B — everything else that clears the hard floor → broker-concentration-reliable

`assign_track` is the pure rule, shared with `universe.gate` (so the gate and the
watchlist can never drift). `resolve_track` applies it for the offline ARMED watchlist:
index membership comes from the local roster (`store.read_symbol_index_latest`,
look-ahead-safe), and ADV is derived from the very bars the engine is about to score,
so it reconciles with the gate's own ADV. **Missing membership → Track B**: we never
invent Track A from absent data (missing ≠ zero).
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from currentflow import config
from currentflow.dal.models import DailyBar, RowStatus


class Track(str, Enum):
    A = "A"  # LQ45/IDX80 member AND ADV ≥ IDR 25 bn → foreign-flow-reliable
    B = "B"  # passes hard floor, not Track A → broker-concentration-reliable


def assign_track(indexes: tuple[str, ...], adv20: float | None) -> Track:
    """The §3 rule. Track A needs *both* index membership and the higher ADV floor;
    an absent/low ADV or a non-member is Track B."""
    is_index_member = bool(set(indexes) & config.TRACK_A_INDEXES)
    if is_index_member and adv20 is not None and adv20 >= config.ADV_TRACK_A_IDR:
        return Track.A
    return Track.B


def _adv20(bars: list[DailyBar]) -> float | None:
    """20-day avg value traded over the TRADED bars (same computation as the gate and
    `engine._adv20`), so the watchlist track reconciles with `gate.evaluate_gate`."""
    window = [b for b in bars if b.status is RowStatus.TRADED][-config.ADV_WINDOW_DAYS:]
    vals = [b.value for b in window if b.value is not None]
    return sum(vals) / len(vals) if vals else None


def resolve_track(
    store, symbol: str, decision_ts: datetime, bars: list[DailyBar]
) -> str:
    """The name's spec track ("A"/"B") for the offline watchlist, from the stored roster
    + bar-derived ADV. Returns the enum *value* (engine.evaluate takes a `str` track)."""
    row = store.read_symbol_index_latest(symbol, decision_ts)
    indexes = row.indexes if row is not None else ()
    return assign_track(indexes, _adv20(bars)).value
