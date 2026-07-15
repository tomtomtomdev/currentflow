"""Slice 20 §17.4 — point-in-time universe + track resolution acceptance tests."""

from __future__ import annotations

from datetime import date as Date
from datetime import datetime, timedelta

from builders import Chart, brow
from currentflow.dal.models import Side, SymbolIndexRow
from currentflow.universe.pit import UNCHECKED_GATE_LEGS, pit_universe
from currentflow.universe.roster import load_rosters
from currentflow.universe.track import resolve_track, resolve_track_pit

NOW = datetime(2026, 7, 15, 9, 0)


def _next_weekday(d: Date) -> Date:
    d = d + timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def _flat_chart(symbol: str, n: int, start: Date, close: float = 1000.0,
                vol: float = 50_000_000) -> Chart:
    """A flat, high-value chart: value = 50bn/day (clears the ADV floor + Track A ADV)."""
    ch = Chart(symbol, start=start)
    for _ in range(n):
        ch.add(close, close + 5, close - 5, close, v=vol)
    return ch


def _broker_day(symbol: str, day: Date):
    return [
        brow("DX", Side.BUY, 5e9, day, symbol=symbol),
        brow("KI", Side.BUY, 4e9, day, symbol=symbol),
    ]


def test_pit_selects_eligible_and_drops_when_pinned(store):
    ch = _flat_chart("XPASS", 65, start=Date(2024, 8, 1))
    good_last = ch.last_date
    ch.add(1080, 1085, 1075, 1080, v=50_000_000)  # +8% jump → ARA-pinned (main band 7%)
    pinned_last = ch.last_date
    store.write_daily_bars(ch.bars)
    store.write_broker_net(_broker_day("XPASS", good_last) + _broker_day("XPASS", pinned_last))

    d1 = _next_weekday(good_last)
    d2 = _next_weekday(pinned_last)

    assert "XPASS" in pit_universe(store, d1).symbols          # clean signal day → in
    assert "XPASS" not in pit_universe(store, d2).symbols      # pinned close → out


def test_pit_records_stopped_names_as_known_missing(store):
    # XLIVE keeps trading; XGONE stops mid-window (delist/suspend Stockbit can't serve).
    live = _flat_chart("XLIVE", 65, start=Date(2024, 8, 1))
    store.write_daily_bars(live.bars)
    store.write_broker_net(_broker_day("XLIVE", live.last_date))

    gone = _flat_chart("XGONE", 65, start=Date(2024, 8, 1))
    # truncate XGONE 10 bars earlier than XLIVE
    store.write_daily_bars(gone.bars[:-10])
    store.write_broker_net(_broker_day("XGONE", gone.bars[-11].date))

    uni = pit_universe(store, _next_weekday(live.last_date))
    assert "XLIVE" in uni.symbols
    assert "XGONE" in uni.known_missing       # surfaced, never silently absent
    assert "XGONE" not in uni.symbols


def test_pit_unchecked_legs_are_named_not_faked(store):
    store.write_daily_bars(_flat_chart("ANY", 65, start=Date(2024, 8, 1)).bars)
    uni = pit_universe(store, Date(2024, 12, 1))
    assert uni.unchecked_legs == UNCHECKED_GATE_LEGS
    assert uni.unchecked_legs  # non-empty — the sink-less §3 legs (corp-action, suspend)


def test_pit_track_reconciles_with_live_resolver(tmp_path, store):
    ch = _flat_chart("AGREE", 65, start=Date(2024, 8, 1))  # ADV 50bn ≥ 25bn → Track A if member
    store.write_daily_bars(ch.bars)
    # Live snapshot says LQ45 …
    store.write_symbol_index([SymbolIndexRow("AGREE", datetime(2024, 1, 1), ("LQ45",))])
    # … and so does the reconstructed roster, effective across the window.
    (tmp_path / "r.csv").write_text(
        "index_name,symbol,effective_from,effective_to,source\n"
        "LQ45,AGREE,2024-01-01,,IDX-1\n"
    )
    load_rosters(store, tmp_path, now=NOW)

    day = Date(2024, 12, 1)
    decision_ts = datetime.combine(day, datetime.min.time()).replace(hour=9, minute=15)
    live = resolve_track(store, "AGREE", decision_ts, ch.bars)
    pit = resolve_track_pit(store, "AGREE", day, ch.bars)
    assert live == pit == "A"


def test_roster_gap_day_is_track_b_and_counted(store):
    ch = _flat_chart("NOROSTER", 65, start=Date(2024, 8, 1))
    store.write_daily_bars(ch.bars)
    store.write_broker_net(_broker_day("NOROSTER", ch.last_date))
    # No roster loaded at all → any day is a roster gap.
    day = _next_weekday(ch.last_date)

    assert resolve_track_pit(store, "NOROSTER", day, ch.bars) == "B"
    uni = pit_universe(store, day)
    assert uni.roster_gap_days == 1
    assert uni.tracks["NOROSTER"] == "B"
