"""Slice 20 §17.4 — regime boundary clamp acceptance tests."""

from __future__ import annotations

from datetime import date as Date
from datetime import datetime

import pytest

from builders import Chart
from currentflow import config
from currentflow.fundamentals.tilt import classify_tilt
from currentflow.validation import runner
from currentflow.validation.runner import RunConfig, assert_regime_clamped


def _cfg(track: str = "B"):
    return RunConfig(
        track=track,
        tilt=classify_tilt("ACC", sector="CONSUMER", mf_rank_pct=80),
        sector="CONSUMER", equity=1_000_000_000.0, adv20=200e9,
    )


def test_clamped_read_never_returns_a_pre_boundary_row(store):
    # Bars straddling the Track B boundary (2024-07-01).
    ch = Chart("BBRI", start=Date(2024, 6, 1))
    for _ in range(60):
        ch.add(100, 102, 99, 100, v=1000)
    store.write_daily_bars(ch.bars)
    ts = datetime(2026, 1, 1, 9, 15)

    clamped = store.read_daily_bars("BBRI", ts, clamp_regime="B")
    assert clamped, "expected some post-boundary bars"
    assert min(b.date for b in clamped) >= config.REGIME_START_TRACK_B

    # Unclamped read still sees the earlier bars — the clamp is opt-in, not global.
    unclamped = store.read_daily_bars("BBRI", ts)
    assert min(b.date for b in unclamped) < config.REGIME_START_TRACK_B


def test_track_a_clamp_uses_the_earlier_boundary(store):
    # Span from late 2023 across BOTH boundaries into 2024-08 so each clamp has data.
    ch = Chart("BBCA", start=Date(2023, 11, 1))
    for _ in range(200):
        ch.add(100, 102, 99, 100, v=1000)
    store.write_daily_bars(ch.bars)
    ts = datetime(2026, 1, 1, 9, 15)

    a = store.read_daily_bars("BBCA", ts, clamp_regime="A")
    b = store.read_daily_bars("BBCA", ts, clamp_regime="B")
    assert min(x.date for x in a) >= config.REGIME_START_TRACK_A
    assert min(x.date for x in b) >= config.REGIME_START_TRACK_B
    # Track A reaches back further than Track B for the same name.
    assert min(x.date for x in a) < min(x.date for x in b)


def test_backtest_before_the_boundary_raises_naming_it(store):
    early = [Date(2024, 3, 1), Date(2024, 3, 4)]  # before Track B 2024-07-01
    with pytest.raises(ValueError, match="regime boundary"):
        runner.run_backtest(store, "ACC", early, _cfg("B"))


def test_backtest_after_the_boundary_is_allowed(store):
    ok = [Date(2024, 8, 1), Date(2024, 8, 2)]
    # No data seeded → no trades, but crucially it does NOT raise the clamp error.
    assert runner.run_backtest(store, "ACC", ok, _cfg("B")) == []


def test_assert_regime_clamped_message_names_track_and_date():
    with pytest.raises(ValueError) as exc:
        assert_regime_clamped([Date(2024, 1, 5)], "B")
    msg = str(exc.value)
    assert "Track B" in msg and "2024-07-01" in msg
