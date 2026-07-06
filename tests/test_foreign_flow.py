"""Foreign Flow Dashboard — hand-checked NBSA math, persistence/reversal,
missing ≠ zero, look-ahead through analyze(), market/sector tide."""

from __future__ import annotations

import statistics
from datetime import date as Date
from datetime import datetime, timedelta

import pytest

from currentflow.dal.models import DailyBar, OwnershipSlice, RowStatus, Scr0Row, Side
from currentflow.dal.timing import ohlcv_as_of
from currentflow.signals import foreign_flow
from currentflow.signals.foreign_flow import (
    current_run,
    daily_net_foreign,
    detect_reversal,
    market_tide,
)
from currentflow.ui.foreign_flow_view import ksei_panel, reversal_callout, stats_panel

D1 = Date(2026, 6, 22)  # Monday


def _days(n: int) -> list[Date]:
    """n consecutive weekdays starting D1."""
    out, d = [], D1
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _bar(
    symbol: str,
    d: Date,
    net_foreign: float | None,
    *,
    foreign_buy: float | None = None,
    foreign_sell: float | None = None,
    value: float | None = None,
) -> DailyBar:
    return DailyBar(
        symbol=symbol, date=d, as_of=ohlcv_as_of(d), status=RowStatus.TRADED,
        open=100, high=110, low=95, close=105, volume=1000, value=value,
        frequency=50, vwap=102, foreign_buy=foreign_buy, foreign_sell=foreign_sell,
        net_foreign=net_foreign, change_percentage=None,
    )


# nets in IDR bn: +1, -3, -2, +4, +6, +8, +10 → BUY run of 4 starting day index 3
NETS = [1e9, -3e9, -2e9, 4e9, 6e9, 8e9, 10e9]


def _bars(symbol: str = "BBRI") -> list[DailyBar]:
    days = _days(len(NETS))
    bars = [_bar(symbol, d, n) for d, n in zip(days[:-1], NETS[:-1])]
    bars.append(
        _bar(symbol, days[-1], NETS[-1],
             foreign_buy=30e9, foreign_sell=20e9, value=100e9)
    )
    return bars


# --- daily series: missing ≠ zero ---------------------------------------------------


def test_missing_net_foreign_dropped_loudly_never_zero(caplog):
    days = _days(3)
    bars = [_bar("BBRI", days[0], 5e9), _bar("BBRI", days[1], None), _bar("BBRI", days[2], 0.0)]
    with caplog.at_level("WARNING"):
        daily = daily_net_foreign(bars)
    assert days[1] not in daily            # absent, not fabricated as 0
    assert daily[days[2]] == 0.0           # a real zero is kept
    assert "missing ≠ zero" in caplog.text


# --- persistence & reversal ----------------------------------------------------------


def test_current_run_counts_consecutive_same_sign_days():
    daily = daily_net_foreign(_bars())
    side, run_len, first = current_run(daily)
    assert side is Side.BUY
    assert run_len == 4
    assert first == _days(7)[3]


def test_reversal_detected_at_sign_flip():
    rev = detect_reversal(daily_net_foreign(_bars()))
    assert rev is not None
    assert rev.side is Side.BUY
    assert rev.date == _days(7)[3]
    assert rev.persistence_days == 4


def test_unbroken_run_is_not_a_reversal():
    days = _days(3)
    daily = daily_net_foreign([_bar("X", d, 1e9) for d in days])
    assert detect_reversal(daily) is None
    assert current_run(daily) == (Side.BUY, 3, days[0])


def test_zero_flow_day_breaks_the_run():
    days = _days(3)
    daily = daily_net_foreign(
        [_bar("X", days[0], 2e9), _bar("X", days[1], 0.0), _bar("X", days[2], 3e9)]
    )
    side, run_len, _ = current_run(daily)
    assert (side, run_len) == (Side.BUY, 1)


# --- snapshot math (hand-checked) ------------------------------------------------------


def test_snapshot_hand_checked_stats(store):
    store.write_daily_bars(_bars())
    snap = foreign_flow.analyze(store, "BBRI", decision_ts=datetime(2026, 7, 1, 9, 0))

    assert snap.net_last == 10e9
    assert snap.cum_window == pytest.approx(sum(NETS))          # 24 bn
    assert snap.cum_5d == pytest.approx(-2e9 + 4e9 + 6e9 + 8e9 + 10e9)  # 26 bn
    assert snap.persistence_days == 4

    prior = NETS[:-1]
    assert snap.vs_20d_avg == pytest.approx(10e9 / statistics.mean(abs(v) for v in prior))
    assert snap.zscore_20d == pytest.approx(
        (10e9 - statistics.mean(prior)) / statistics.pstdev(prior)
    )
    assert snap.avg_window_used == 6

    # foreign turnover share on the last day: (30+20) / (2·100)
    assert snap.foreign_turnover_share == pytest.approx(0.25)


def test_nbsa_pct_of_float_uses_latest_visible_scr0(store):
    store.write_daily_bars(_bars())
    store.write_scr0_eligible([
        Scr0Row(symbol="BBRI", date=D1, as_of=datetime(2026, 6, 22, 18, 0),
                adv20=5e11, price=4500, free_float=40.0, market_cap=1e12),
    ])
    snap = foreign_flow.analyze(store, "BBRI", decision_ts=datetime(2026, 7, 1, 9, 0))
    # float value = 40% · 1e12 = 4e11; window NBSA = 24e9 → 6.0%
    assert snap.nbsa_pct_of_float == pytest.approx(6.0)

    # SCR-0 row not yet visible → no %-of-float (never guessed)
    early = foreign_flow.analyze(store, "BBRI", decision_ts=datetime(2026, 6, 22, 17, 0))
    assert early.nbsa_pct_of_float is None


# --- look-ahead through analyze -------------------------------------------------------


def test_analyze_is_look_ahead_safe(store):
    store.write_daily_bars(_bars())
    days = _days(7)
    # decision before the last bar publishes (16:15) → last day invisible
    snap = foreign_flow.analyze(
        store, "BBRI", decision_ts=datetime.combine(days[-1], datetime.min.time())
    )
    assert snap.end == days[-2]
    assert snap.net_last == NETS[-2]


def test_ksei_overlay_is_look_ahead_safe(store):
    store.write_daily_bars(_bars())
    fetched = datetime(2026, 6, 30, 20, 0)
    store.write_ksei_ownership([
        OwnershipSlice("BBRI", Date(2026, 4, 30), fetched, 42.0, 58.0),
        OwnershipSlice("BBRI", Date(2026, 5, 31), fetched, 43.5, 56.5),
    ])
    before = foreign_flow.analyze(store, "BBRI", decision_ts=datetime(2026, 6, 30, 19, 0))
    assert before.ksei == ()
    after = foreign_flow.analyze(store, "BBRI", decision_ts=datetime(2026, 7, 1, 9, 0))
    assert [s.foreign_pct for s in after.ksei] == [42.0, 43.5]
    panel = ksei_panel(after)
    assert panel["trend"] == "rising"
    assert panel["foreign_own_pct"] == 43.5


def test_ksei_panel_reports_foreign_own_vs_free_float(store):
    """Design 04 'FOREIGN OWN vs FREE-FLOAT': the bar fills to foreign's share of the
    free-float. free_float_pct comes from SCR-0; own% from the latest KSEI slice."""
    store.write_daily_bars(_bars())
    store.write_scr0_eligible([
        Scr0Row(symbol="BBRI", date=D1, as_of=datetime(2026, 6, 22, 18, 0),
                adv20=5e11, price=4500, free_float=38.0, market_cap=1e12),
    ])
    store.write_ksei_ownership([
        OwnershipSlice("BBRI", Date(2026, 5, 31), datetime(2026, 6, 30, 20, 0), 35.0, 65.0),
    ])
    panel = ksei_panel(foreign_flow.analyze(store, "BBRI", decision_ts=datetime(2026, 7, 1, 9, 0)))
    assert panel["foreign_own_pct"] == 35.0
    assert panel["free_float_pct"] == 38.0
    # 35 of 38 free-float → bar fills to ~92.1%
    assert panel["own_of_float_pct"] == pytest.approx(92.1, abs=0.1)


def test_ksei_panel_free_float_absent_when_no_scr0(store):
    """No SCR-0 on file → no free-float denominator (never fabricated as a number)."""
    store.write_daily_bars(_bars())
    store.write_ksei_ownership([
        OwnershipSlice("BBRI", Date(2026, 5, 31), datetime(2026, 6, 30, 20, 0), 35.0, 65.0),
    ])
    panel = ksei_panel(foreign_flow.analyze(store, "BBRI", decision_ts=datetime(2026, 7, 1, 9, 0)))
    assert panel["foreign_own_pct"] == 35.0
    assert panel["free_float_pct"] is None
    assert panel["own_of_float_pct"] is None


# --- market / sector tide ---------------------------------------------------------------


def test_market_tide_aggregates_and_skips_missing_loudly(store, caplog):
    days = _days(1)
    store.write_daily_bars([
        _bar("BBRI", days[0], 10e9),
        _bar("ASII", days[0], -4e9),
        _bar("GOTO", days[0], None),   # unknown net — must be skipped, not zeroed
    ])
    with caplog.at_level("WARNING"):
        rows = market_tide(
            store, ["BBRI", "ASII", "GOTO"], datetime(2026, 7, 1), day=days[0],
            sector_map={"BBRI": "BANKS"},
        )
    by_scope = {r.scope: r for r in rows}
    assert by_scope["MARKET"].net_foreign == pytest.approx(6e9)
    assert by_scope["MARKET"].symbols == 2
    assert by_scope["BANKS"].net_foreign == pytest.approx(10e9)
    assert by_scope["UNKNOWN"].net_foreign == pytest.approx(-4e9)
    assert "skipped" in caplog.text


# --- view-model framing (RULE B) -----------------------------------------------------


def test_view_model_strings_carry_no_advice(store):
    store.write_daily_bars(_bars())
    snap = foreign_flow.analyze(store, "BBRI", decision_ts=datetime(2026, 7, 1, 9, 0))
    callout = reversal_callout(snap)
    assert callout == (
        f"Foreign flow reversed to net BUY on {_days(7)[3].isoformat()} — "
        "4-day persistence."
    )
    stats = stats_panel(snap)
    assert stats["persistence"] == "4/6"
    for banned in ("score", "probability", "recommend", "should"):
        assert banned not in callout.lower()
