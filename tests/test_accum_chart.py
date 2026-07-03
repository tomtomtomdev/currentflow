"""Accumulation chart series (design/README.md §3: price + cumulative smart-money
accumulation + accumulator VWAP). Pure view-model rows; missing broker days are a gap
(None), never a fabricated zero.
"""

from __future__ import annotations

from datetime import date as Date
from datetime import datetime

import pytest
from builders import Chart, brow

from currentflow.dal.models import InvestorType, Side
from currentflow.signals import broker_flow
from currentflow.ui.accumulation_view import chart_rows

TS = datetime(2026, 7, 1, 9, 0)
DAYS = [Date(2026, 6, 23), Date(2026, 6, 24), Date(2026, 6, 25), Date(2026, 6, 26)]


def _bars(symbol="X"):
    ch = Chart(symbol, start=Date(2026, 6, 23))   # Jun 23–26, then 29–30 (weekend skipped)
    for c, v in zip([100, 100, 99, 99, 98, 98], [2000, 2000, 1500, 1500, 1000, 1000]):
        ch.add(c, c + 1, c - 1, c, v)
    return ch.bars


def _broker(symbol="X", vals=(2e9, 3e9, 4e9, 5e9)):
    rows = []
    for d, v in zip(DAYS, vals):
        rows.append(brow("DX", Side.BUY, v, d, symbol=symbol, investor=InvestorType.FOREIGN, avg_price=95))
        rows.append(brow("YP", Side.SELL, v * 0.5, d, symbol=symbol))
    return broker_flow.build_snapshot(symbol, rows, decision_ts=TS)


def test_price_and_cumulative_accumulation_lanes():
    bars = _bars()
    rows = chart_rows(bars, _broker())

    assert [r["date"] for r in rows] == [b.date for b in bars]
    assert [r["close"] for r in rows] == [b.close for b in bars]
    # accumulator = DX; cumulative net walks 2 → 5 → 9 → 14 bn over its traded days
    assert [r["cum_accumulation_bn"] for r in rows[:4]] == pytest.approx([2.0, 5.0, 9.0, 14.0])
    # the accumulator VWAP reference line rides every row
    assert all(r["accumulator_vwap"] == 95 for r in rows)


def test_days_without_broker_rows_are_a_gap_not_zero():
    rows = chart_rows(_bars(), _broker())
    # Jun 29/30 have bars but no broker rows — the accumulation lane goes blank there
    assert [r["cum_accumulation_bn"] for r in rows[4:]] == [None, None]


def test_no_broker_data_yields_no_accumulation_lane():
    empty = broker_flow.build_snapshot("X", [], decision_ts=TS)
    rows = chart_rows(_bars(), empty)
    assert rows                                        # price lane still renders
    assert all(r["cum_accumulation_bn"] is None for r in rows)
    assert all(r["accumulator_vwap"] is None for r in rows)
