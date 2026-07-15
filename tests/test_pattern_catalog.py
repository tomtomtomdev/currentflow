"""Slice 21 §18.3 — pattern catalog scan / outcome / estimate acceptance tests."""

from __future__ import annotations

from datetime import date as Date
from datetime import datetime, time, timedelta

import pytest

from builders import Chart, brow
from currentflow.dal.models import Side
from currentflow.patterns import estimate as estimate_mod
from currentflow.patterns import outcome as outcome_mod
from currentflow.patterns import scan as scan_mod
from currentflow.patterns.catalog import OutcomeSpec, Pattern, SEED_PATTERNS
from currentflow.patterns.outcome import resolve_instance, resolve_open
from currentflow.patterns.scan import scan_pattern
from currentflow.store.schema import PatternCatalogRow, PatternInstanceRow
from currentflow.universe.pit import PitUniverse

NOW = datetime(2026, 7, 1, 9, 0)


def _pat(direction="up", horizons=(5,), target=0.10, predicate=lambda f: True):
    return Pattern(
        pattern_id="t", version=1, track="B", status="DEFINED",
        folk_claim="", source="s", definition_text="",
        outcome=OutcomeSpec(horizons=horizons, target=target, direction=direction),
        predicate=predicate, feature_window=20,
    )


def _uni_provider(symbols):
    def provider(day: Date) -> PitUniverse:
        return PitUniverse(
            day=day, decision_ts=datetime.combine(day, time(9, 15)),
            symbols=tuple(symbols), tracks={s: "B" for s in symbols},
            unchecked_legs=(), known_missing=(), roster_gap_days=0,
        )
    return provider


def _weekdays(start: Date, n: int) -> list[Date]:
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _seed_acc(store, symbol="ACC", start=Date(2024, 8, 1), n=40):
    ch = Chart(symbol, start=start)
    for _ in range(n):
        ch.add(1000, 1010, 990, 1000, v=50_000_000)
    store.write_daily_bars(ch.bars)
    return ch


# --- §18.3 PIT dependency ----------------------------------------------------------

def test_scanner_refuses_non_pit_universe(store):
    _seed_acc(store)
    days = _weekdays(Date(2024, 9, 2), 3)
    bad_provider = lambda day: ["ACC"]  # a plain list, not a PitUniverse
    with pytest.raises(TypeError, match="PitUniverse"):
        scan_pattern(store, _pat(), days, bad_provider, now=NOW)


# --- §18.3 look-ahead --------------------------------------------------------------

def test_features_are_look_ahead_safe(store):
    from currentflow.signals.pattern_features import compute_features
    _seed_acc(store, n=30)
    # A future bar stamped AFTER the decision moment must not leak into features.
    future = Chart("ACC", start=Date(2025, 1, 6))
    future.add(2000, 2010, 1990, 2000, v=50_000_000)
    store.write_daily_bars(future.bars)

    decision_ts = datetime(2024, 10, 1, 9, 15)
    fs = compute_features(store, "ACC", decision_ts)
    assert fs is not None
    assert fs.day < Date(2024, 10, 1)          # newest visible bar is before decision_ts
    assert (fs.price_return or 0) == pytest.approx(0.0, abs=1e-9)  # future 2x not seen


# --- §18.3 overlap collapse --------------------------------------------------------

def test_overlap_collapse_within_one_horizon(store):
    _seed_acc(store, n=60)
    days = _weekdays(Date(2024, 10, 1), 12)   # 12 consecutive qualifying days
    rows = scan_pattern(store, _pat(horizons=(5,)), days, _uni_provider(["ACC"]), now=NOW)
    flags = sorted({r.flag_date for r in rows})
    # horizon 5 → a flag every ≥5 trading days: indices 0, 5, 10 → 3 instances, not 12.
    assert len(flags) == 3
    idx = [days.index(f) for f in flags]
    assert all(b - a >= 5 for a, b in zip(idx, idx[1:]))


# --- §18.3 terminal outcomes -------------------------------------------------------

def test_terminal_outcome_counted_not_dropped(store):
    # XGONE flags, then its bars stop while the market (XLIVE) keeps trading.
    gone = Chart("XGONE", start=Date(2024, 8, 1))
    for _ in range(30):
        gone.add(1000, 1010, 990, 1000, v=50_000_000)
    store.write_daily_bars(gone.bars)
    live = Chart("XLIVE", start=Date(2024, 8, 1))
    for _ in range(60):
        live.add(1000, 1010, 990, 1000, v=50_000_000)
    store.write_daily_bars(live.bars)

    flag = gone.bars[27].date   # near the end → <5 forward bars before XGONE stops
    store.write_pattern_instances([PatternInstanceRow(
        "t", 1, "XGONE", flag, 5, "OPEN", None, "EST", NOW,
    )])
    n = resolve_open(store, _pat(direction="down", horizons=(5,), target=-0.10), now=NOW)
    assert n == 1
    inst = store.read_pattern_instances("t", 1)[0]
    assert inst.outcome.startswith("TERMINAL")   # recorded, not dropped
    assert inst.resolved_on is not None


def test_resolve_instance_hit_and_miss():
    ch = Chart("H", start=Date(2024, 8, 1))
    for _ in range(3):
        ch.add(100, 100, 100, 100, v=1000)      # flat entry context
    flag = ch.last_date
    ch.d = flag + timedelta(days=1)
    ch.add(100, 130, 100, 125, v=1000)          # +25% next day
    for _ in range(6):
        ch.add(125, 126, 124, 125, v=1000)
    traded = sorted(ch.bars, key=lambda b: b.date)
    spec = OutcomeSpec(horizons=(5,), target=0.20, direction="up")
    outcome, day = resolve_instance(traded, flag, 5, spec, traded[-1].date)
    assert outcome == "HIT"

    spec_miss = OutcomeSpec(horizons=(5,), target=0.50, direction="up")
    outcome2, _ = resolve_instance(traded, flag, 5, spec_miss, traded[-1].date)
    assert outcome2 == "MISS"


# --- §18.3 null-attached + determinism ---------------------------------------------

def test_null_attached_rejected_at_write(store):
    import json
    bad = json.dumps({"horizons": {"5": {"n": 3, "rate": 0.5}}})  # no rate_uncond
    row = PatternCatalogRow("t", 1, "B", "ESTIMATED", "{}", bad, NOW)
    with pytest.raises(ValueError, match="rate_uncond"):
        store.upsert_pattern_catalog(row)


def test_estimator_is_deterministic_and_null_attached(store):
    _seed_acc(store, n=60)
    days = _weekdays(Date(2024, 10, 1), 10)
    pat = _pat(horizons=(5,), target=0.10, direction="up")
    scan_pattern(store, pat, days, _uni_provider(["ACC"]), now=NOW)
    resolve_open(store, pat, now=NOW)

    r1 = estimate_mod.estimate(store, pat, days, _uni_provider(["ACC"]), now=NOW)
    r2 = estimate_mod.estimate(store, pat, days, _uni_provider(["ACC"]), now=NOW)
    assert r1.results_json == r2.results_json          # byte-identical re-run
    import json
    horizons = json.loads(r1.results_json)["horizons"]
    assert horizons["5"]["rate_uncond"] is not None    # the null travels with the rate


def test_seed_patterns_serialise_and_carry_stability_label():
    import json
    assert len(SEED_PATTERNS) == 6
    for p in SEED_PATTERNS:
        spec = json.loads(p.spec_json())
        assert spec["stability"] == "UNKNOWN (current regime only)"
        assert p.track in ("A", "B")
