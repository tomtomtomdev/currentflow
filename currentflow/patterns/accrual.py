"""Catalog seeding + OOS accrual (slice 21, PATTERN-CATALOG-SPEC §2/§7).

`seed_catalog` writes the six seed entries (their definitions exist in code → DEFINED,
no results yet). `run_estimation` takes one pattern end-to-end (scan → resolve → estimate),
promoting it to ESTIMATED/OOS_CHECKED — this is how `dominant-broker-flip` and
`stealth-divergence` reach ESTIMATED (their definitions already exist, so they exercise the
pipeline end-to-end while measuring the system's own current beliefs). `accrue_oos` is the
monthly scheduler job: cache-only, it extends instances forward of the seam and re-resolves.

Cache-only + evidence-only: nothing here scores, gates, arms, or writes SMS weights (P1/P2).
"""

from __future__ import annotations

from datetime import date as Date
from datetime import datetime, timedelta

from currentflow import config
from currentflow.patterns.catalog import SEED_PATTERNS, Pattern
from currentflow.patterns.estimate import estimate
from currentflow.patterns.outcome import resolve_open
from currentflow.patterns.scan import scan_pattern, store_universe_provider
from currentflow.store.schema import PatternCatalogRow

# The seeds whose definitions already exist in code and are taken to ESTIMATED first.
PATTERNS_TO_ESTIMATE: tuple[Pattern, ...] = tuple(
    p for p in SEED_PATTERNS if p.status == "ESTIMATED"
)


def seed_catalog(store, *, now: datetime) -> int:
    """Write/refresh the six seed catalog entries at DEFINED (definition exists, no results
    yet). Idempotent — a re-seed is an upsert no-op. Returns the count seeded."""
    for p in SEED_PATTERNS:
        existing = {r.version: r for r in store.read_pattern_catalog(p.pattern_id)}
        # Don't clobber a version already promoted to ESTIMATED/OOS_CHECKED with its results.
        if p.version in existing and existing[p.version].status in ("ESTIMATED", "OOS_CHECKED"):
            continue
        store.upsert_pattern_catalog(PatternCatalogRow(
            pattern_id=p.pattern_id, version=p.version, track=p.track,
            status="DEFINED", spec_json=p.spec_json(), results_json=None, as_of=now,
        ))
    return len(SEED_PATTERNS)


def _trading_days(start: Date, end: Date) -> list[Date]:
    out, d = [], start
    while d <= end:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def run_estimation(store, pattern: Pattern, *, now: datetime, universe=None) -> PatternCatalogRow | None:
    """Full pipeline for one pattern over its regime-clamped window: scan (EST + OOS days)
    → resolve outcomes → estimate. Returns the updated catalog row, or None if the store has
    no bars. The scan/estimate window is floored at `regime_start(track)` (REGIME.md)."""
    universe = universe or store_universe_provider(store)
    latest = store.latest_bar_date()
    if latest is None:
        return None
    days = _trading_days(config.regime_start(pattern.track), latest)
    scan_pattern(store, pattern, days, universe, now=now)
    resolve_open(store, pattern, now=now)
    return estimate(store, pattern, days, universe, now=now)


def accrue_oos(store, *, now: datetime, universe=None) -> tuple[int, list[str]]:
    """Monthly OOS accrual (cache-only). Seeds if needed, then for each estimable pattern
    extends + resolves instances and re-estimates (EST is stable by construction — its window
    is closed — so only OOS grows). Returns (OOS-instance count touched, pattern ids)."""
    seed_catalog(store, now=now)
    universe = universe or store_universe_provider(store)
    if store.latest_bar_date() is None:
        return 0, []
    touched, ids = 0, []
    for p in PATTERNS_TO_ESTIMATE:
        row = run_estimation(store, p, now=now, universe=universe)
        if row is None:
            continue
        ids.append(p.pattern_id)
        touched += len(store.read_pattern_instances(p.pattern_id, p.version, window="OOS"))
    return touched, ids
