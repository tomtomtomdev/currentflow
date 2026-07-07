"""Parser tests: as_of stamping, empty≠zero, investor tagging."""

from __future__ import annotations

from datetime import date, datetime, time

from currentflow import config
from currentflow.dal.models import InvestorType, RowStatus, Side
from currentflow.dal.parse import parse_broker_summary, parse_ohlcv
from tests.conftest import broker_payload, ohlcv_payload


def test_ohlcv_traded_vs_no_trades_and_as_of():
    payload = ohlcv_payload([
        {
            "date": "2026-06-01", "open": 100, "high": 110, "low": 99, "close": 105,
            "volume": 5000, "value": 525000, "frequency": 42, "average": 104.3,
            "foreign_buy": 300000, "foreign_sell": 100000, "net_foreign": 200000,
            "change_percentage": 1.2,
        },
        # illiquid no-trade day: present row, all-zero (observed on XBIG)
        {
            "date": "2026-06-02", "open": 0, "high": 0, "low": 0, "close": 0,
            "volume": 0, "value": 0, "frequency": 0, "average": 0,
        },
    ])
    bars = parse_ohlcv("BBCA", payload)
    assert len(bars) == 2

    traded, flat = bars
    assert traded.status is RowStatus.TRADED
    assert traded.net_foreign == 200000
    # as_of is post-close same day (16:15 WIB)
    assert traded.as_of == datetime.combine(date(2026, 6, 1), config.OHLCV_AVAILABLE_TIME)

    # empty ≠ zero at the status level: a real no-trade day is flagged, not dropped
    assert flat.status is RowStatus.NO_TRADES


def test_ohlcv_missing_field_is_none_not_zero():
    # net_foreign absent entirely — must be None, never coerced to 0
    payload = ohlcv_payload([
        {"date": "2026-06-01", "close": 105, "volume": 10, "frequency": 3}
    ])
    (bar,) = parse_ohlcv("BBRI", payload)
    assert bar.net_foreign is None
    assert bar.foreign_buy is None
    assert bar.value is None
    assert bar.status is RowStatus.TRADED  # had volume/freq


def test_broker_summary_as_of_from_data_last_updated():
    payload = broker_payload(
        buys=[{
            "netbs_broker_code": "YP", "type": "Asing", "netbs_buy_avg_price": 105.0,
            "bval": 1_000_000, "blot": 100, "freq": 12, "netbs_date": "2026-06-01",
        }],
        sells=[{
            "netbs_broker_code": "CC", "type": "Lokal", "netbs_sell_avg_price": 104.0,
            "sval": 400_000, "slot": 40, "freq": 8, "netbs_date": "2026-06-01",
        }],
        data_last_updated="2026-06-01T17:30:00",
    )
    rows = parse_broker_summary("BBCA", payload)
    assert len(rows) == 2

    buy = next(r for r in rows if r.side is Side.BUY)
    assert buy.broker_code == "YP"
    assert buy.investor_type is InvestorType.FOREIGN
    assert buy.avg_price == 105.0
    assert buy.value == 1_000_000
    # as_of taken from the feed's observed data_last_updated
    assert buy.as_of == datetime(2026, 6, 1, 17, 30, 0)

    sell = next(r for r in rows if r.side is Side.SELL)
    assert sell.side is Side.SELL
    assert sell.investor_type is InvestorType.LOCAL
    assert sell.value == 400_000


def test_broker_summary_normalizes_signed_sell_value_to_magnitude():
    # LIVE FEED CONVENTION (verified against marketdetectors NET, MEDC 2026-Q2):
    # a net-seller's `sval` arrives NEGATIVE (the feed signs the net). We must store
    # a MAGNITUDE with `side` carrying direction — otherwise the aggregation layer's
    # `buy_val - sell_val` double-flips the sign and reports distribution as
    # accumulation (AK@MEDC: true -61.3B rendered as +504.1B).
    payload = broker_payload(
        buys=[{
            "netbs_broker_code": "AK", "type": "Asing", "bval": 221_380,
            "blot": 100, "netbs_date": "2026-06-01",
        }],
        sells=[{
            "netbs_broker_code": "AK", "type": "Asing", "sval": -282_720,
            "slot": 200, "netbs_date": "2026-06-01",
        }],
    )
    rows = parse_broker_summary("MEDC", payload)
    buy = next(r for r in rows if r.side is Side.BUY)
    sell = next(r for r in rows if r.side is Side.SELL)
    assert buy.value == 221_380
    assert sell.value == 282_720  # magnitude, NOT the raw signed -282_720


def test_broker_summary_conservative_as_of_when_no_timestamp():
    # LD-5: without data_last_updated, availability falls to next-day 09:00 WIB.
    payload = broker_payload(
        buys=[{
            "netbs_broker_code": "YP", "type": "F", "bval": 1, "blot": 1,
            "netbs_date": "2026-06-01",
        }],
        sells=[],
    )
    (row,) = parse_broker_summary("BBCA", payload)
    assert row.as_of == datetime.combine(
        date(2026, 6, 2), config.BROKER_CONSERVATIVE_AVAILABLE_TIME
    )
    assert row.as_of.time() == time(9, 0)
