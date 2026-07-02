"""Institutional Accumulation Detector — stealth divergence, accumulator VWAP,
volume dry-up. Pure observation (no score); look-ahead-safe through analyze()."""

from __future__ import annotations

from datetime import date as Date
from datetime import datetime, timedelta

import pytest
from builders import Chart, brow

from currentflow.dal.models import InvestorType, Side
from currentflow.signals import accumulation, broker_flow

TS = datetime(2026, 7, 1, 9, 0)
DAYS = [Date(2026, 6, 23), Date(2026, 6, 24), Date(2026, 6, 25), Date(2026, 6, 26)]


def _acc_broker(symbol="X", vals=(2e9, 3e9, 4e9, 5e9)):
    rows = []
    for d, v in zip(DAYS, vals):
        rows.append(brow("DX", Side.BUY, v, d, symbol=symbol, investor=InvestorType.FOREIGN, avg_price=95))
        rows.append(brow("YP", Side.SELL, v * 0.5, d, symbol=symbol))
    return broker_flow.build_snapshot(symbol, rows, decision_ts=TS)


def _bars(closes, vols, symbol="X"):
    ch = Chart(symbol)
    for c, v in zip(closes, vols):
        ch.add(c, c + 1, c - 1, c, v)
    return ch.bars


def test_stealth_divergence_price_flat_while_accumulation_rises():
    bars = _bars([100, 100, 99, 99, 98, 98], [2000, 2000, 1500, 1500, 1000, 1000])
    snap = accumulation.build_snapshot("X", bars, _acc_broker(), decision_ts=TS)
    assert snap.stealth_divergence is True
    assert snap.accumulator == "DX"
    assert snap.accumulation_rising is True
    assert snap.net_accumulation == pytest.approx(14e9)
    assert snap.accumulator_vwap == 95
    assert snap.price_vs_vwap_pct == pytest.approx((98 - 95) / 95)
    assert snap.volume_dryup_ratio < 1        # volume drying up during consolidation
    assert snap.absorption is None            # no L2 depth — degrades gracefully


def test_no_stealth_when_price_is_rising():
    bars = _bars([100, 103, 106, 108, 110, 112], [1000] * 6)
    snap = accumulation.build_snapshot("X", bars, _acc_broker(), decision_ts=TS)
    assert snap.price_change_pct > 0.02
    assert snap.stealth_divergence is False


def test_analyze_is_look_ahead_safe(store):
    bars = _bars([100, 100, 99, 99, 98, 98], [2000, 2000, 1500, 1500, 1000, 1000])
    store.write_daily_bars(bars)
    last = bars[-1].date
    # before the last bar's 16:15 as_of → last day invisible, window ends a day earlier
    early = accumulation.analyze(store, "X", decision_ts=datetime.combine(last, datetime.min.time()))
    assert early.end == bars[-2].date
    later = accumulation.analyze(store, "X", decision_ts=datetime.combine(last + timedelta(days=1), datetime.min.time()))
    assert later.end == last
