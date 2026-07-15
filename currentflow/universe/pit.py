"""Point-in-time universe (slice 20, §17.2) — survivorship-honest per-day membership.

`pit_universe(store, day)` returns the set of names whose *stored* data passes the
reconstructable §3 gate legs as of `day` at that day's decision_ts (D 09:15 WIB, the
replay pre-open frame — so the newest visible bar is the prior trading day's, exactly
what the engine would see acting on `day`).

Honesty (CLAUDE.md — missing ≠ zero, no silent caps, name what you can't check):
  * **Reconstructable legs** (checked here): ≥60 trading-day history, ADV floor, price
    floor, ARA/ARB-pinned close, broker-summary presence.
  * **Unchecked legs** (no historical store sink today — the slice-12 deferred feeds):
    the corp-action ±5d window and suspend/UMA/notation flags. They are enumerated in
    `PitUniverse.unchecked_legs`, never silently assumed to pass.
  * **Delisted-name bias**: names Stockbit can no longer serve stop having bars. When a
    name present earlier in the store stops, it is recorded per day in `known_missing`,
    never just silently absent (an acknowledged, surfaced bias).
  * **Roster gaps**: a day no loaded roster period covers resolves every name via the
    ADV leg only (→ Track B) and is counted in `roster_gap_days`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as Date
from datetime import datetime

from currentflow import config
from currentflow.dal.models import BoardType, DailyBar, RowStatus
from currentflow.universe.bands import check_pinned
from currentflow.universe.track import resolve_track_pit

# The §3 gate legs with no historical store sink — reconstruction cannot check them, so
# they are named rather than faked. Exactly the sink-less legs (GateFailure values).
UNCHECKED_GATE_LEGS: tuple[str, ...] = ("CORP_ACTION_WINDOW", "SUSPENDED")


@dataclass(frozen=True, slots=True)
class PitUniverse:
    day: Date
    decision_ts: datetime
    symbols: tuple[str, ...]                 # names passing the reconstructable legs
    tracks: dict[str, str]                   # symbol -> "A"/"B" (point-in-time)
    unchecked_legs: tuple[str, ...]          # §3 legs with no historical sink (named, not faked)
    known_missing: tuple[str, ...]           # had bars earlier, stopped (delist bias, surfaced)
    roster_gap_days: int                     # 1 if this day's roster is missing, else 0


def _adv20(bars: list[DailyBar]) -> float | None:
    """ADV over the last 20 visible trading days (same rule as the gate: NO_TRADES is a
    genuine zero; a TRADED bar with no value makes ADV unreconstructable → None)."""
    window = bars[-config.ADV_WINDOW_DAYS:]
    values: list[float] = []
    for b in window:
        if b.status is RowStatus.NO_TRADES:
            values.append(0.0)
        elif b.value is None:
            return None
        else:
            values.append(b.value)
    return sum(values) / len(values) if values else None


def _passes_reconstructable_legs(
    store, symbol: str, bars: list[DailyBar], decision_ts: datetime
) -> bool:
    """The offline-checkable subset of §3 (evaluate_gate legs sans the sink-less two)."""
    traded = [b for b in bars if b.status is RowStatus.TRADED]
    if len(traded) < config.MIN_HISTORY_TRADING_DAYS:
        return False

    adv20 = _adv20(bars)
    if adv20 is None or adv20 < config.ADV_FLOOR_IDR:
        return False

    last = bars[-1]
    if last.close is None or last.close < config.PRICE_FLOOR_IDR:
        return False

    # ARA/ARB-pinned close → no fillable band. Board is unknown historically; MAIN gives
    # the tightest band (most conservative pin detection — never hides a pinned close).
    prev = next((b for b in reversed(bars[:-1]) if b.close), None)
    if prev is not None and prev.close:
        if check_pinned(last.close, prev.close, BoardType.MAIN).pinned:
            return False

    # Broker-summary presence for the newest visible day (completeness — missing ≠ zero).
    # Read at the universe decision_ts: the broker summary for `last.date` publishes the
    # next morning (T+1 09:00), so it is visible at this frame though `last.date` is not.
    broker = store.read_broker_net(symbol, decision_ts, start=last.date, end=last.date)
    if not broker:
        return False

    return True


def pit_universe(store, day: Date) -> PitUniverse:
    """Reconstruct the eligible universe as-of `day` from the store's own data."""
    decision_ts = datetime.combine(day, config.REPLAY_DECISION_TIME)
    roster_gap_days = 0 if store.roster_covers(day) else 1

    # Latest visible bar date per ingested name (the market's shape as-of decision_ts).
    latest: dict[str, list[DailyBar]] = {}
    for symbol in store.symbols():
        bars = store.read_daily_bars(symbol, decision_ts)
        if bars:
            latest[symbol] = bars
    if not latest:
        return PitUniverse(day, decision_ts, (), {}, UNCHECKED_GATE_LEGS, (), roster_gap_days)

    market_latest = max(bars[-1].date for bars in latest.values())

    passing: list[str] = []
    tracks: dict[str, str] = {}
    known_missing: list[str] = []
    for symbol, bars in latest.items():
        if bars[-1].date < market_latest:
            # Had bars, then stopped: delisted/suspended, Stockbit can't serve → surfaced.
            known_missing.append(symbol)
            continue
        if _passes_reconstructable_legs(store, symbol, bars, decision_ts):
            passing.append(symbol)
            tracks[symbol] = resolve_track_pit(store, symbol, day, bars)

    return PitUniverse(
        day=day,
        decision_ts=decision_ts,
        symbols=tuple(sorted(passing)),
        tracks=tracks,
        unchecked_legs=UNCHECKED_GATE_LEGS,
        known_missing=tuple(sorted(known_missing)),
        roster_gap_days=roster_gap_days,
    )
