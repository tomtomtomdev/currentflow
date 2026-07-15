"""Pattern scanner (slice 21, PATTERN-CATALOG-SPEC §5).

Walks `pit_universe(day)` per day over a window, evaluates a pattern's DEFINED predicate,
and writes OPEN instances. Look-ahead-safe by construction: features are read at the
universe's replay `decision_ts` (§5.2). Instances of the same pattern on the same name
within the outcome horizon collapse to one (§5.3), else persistence patterns self-replicate
and inflate n.

PIT is a hard requirement (§5.1): the scanner takes a `pit_universe` *provider* and refuses
any source that does not yield a `PitUniverse` — a base rate over a present-day survivor
universe is invalid and must never be stored.
"""

from __future__ import annotations

from datetime import date as Date
from typing import Callable

from currentflow import config
from currentflow.patterns.catalog import Pattern
from currentflow.signals.pattern_features import compute_features
from currentflow.store.schema import PatternInstanceRow
from currentflow.universe.pit import PitUniverse, pit_universe

UniverseProvider = Callable[[Date], PitUniverse]


def store_universe_provider(store) -> UniverseProvider:
    """The production provider: the slice-17 point-in-time universe."""
    return lambda day: pit_universe(store, day)


def _window_for(flag_date: Date) -> str:
    """EST before the holdout seam, OOS on/after it (REGIME.md §3)."""
    return "OOS" if flag_date >= config.CATALOG_HOLDOUT_START else "EST"


def scan_pattern(
    store,
    pattern: Pattern,
    days: list[Date],
    universe: UniverseProvider,
    *,
    now,
) -> list[PatternInstanceRow]:
    """Evaluate `pattern` across `days`; return (and persist) the flagged OPEN instances.

    `universe(day)` MUST return a `PitUniverse` (§5.1) — anything else raises TypeError.
    Overlap collapse is per (pattern, symbol): a fresh flag is suppressed while a prior
    flag's largest horizon is still open (measured in trading days over `days`)."""
    max_h = max(pattern.outcome.horizons)
    last_flag_idx: dict[str, int] = {}
    rows: list[PatternInstanceRow] = []

    for i, day in enumerate(sorted(days)):
        uni = universe(day)
        if not isinstance(uni, PitUniverse):
            raise TypeError(
                "scan_pattern requires a PitUniverse source (§5.1 point-in-time) — "
                f"got {type(uni).__name__}"
            )
        for symbol in uni.symbols:
            if uni.tracks.get(symbol) != pattern.track:
                continue  # a pattern is defined per track, never blended (LD-1)
            prev = last_flag_idx.get(symbol)
            if prev is not None and (i - prev) < max_h:
                continue  # overlap collapse — within the prior flag's horizon
            fs = compute_features(store, symbol, uni.decision_ts, window=pattern.feature_window)
            if fs is None or not pattern.predicate(fs):
                continue
            last_flag_idx[symbol] = i
            window = _window_for(day)
            for horizon in pattern.outcome.horizons:
                rows.append(PatternInstanceRow(
                    pattern_id=pattern.pattern_id, version=pattern.version, symbol=symbol,
                    flag_date=day, horizon_days=horizon, outcome="OPEN",
                    resolved_on=None, window=window, as_of=now,
                ))

    store.write_pattern_instances(rows)
    return rows
