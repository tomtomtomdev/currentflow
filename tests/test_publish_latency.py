"""Publish-latency measurement (LD-5) — unmeasured until real data accrues."""

from __future__ import annotations

from datetime import date, datetime

from currentflow.dal.models import BrokerNet, InvestorType, Side
from currentflow.ingest.publish_latency import measure_broker_latency


def _row(d: date, as_of: datetime) -> BrokerNet:
    return BrokerNet(
        symbol="BBCA", date=d, as_of=as_of, broker_code="YP", side=Side.BUY,
        investor_type=InvestorType.FOREIGN, avg_price=1.0, value=1.0, lot=1, frequency=1,
    )


def test_unmeasured_when_no_real_stamps():
    m = measure_broker_latency([])
    assert m.measured is False
    assert "UNMEASURED" in m.summary()


def test_measures_observed_latency_from_data_last_updated():
    # close is 16:15; data_last_updated ~17:15 same day → ~1h observed latency
    rows = [
        _row(date(2026, 6, 1), datetime(2026, 6, 1, 17, 15)),
        _row(date(2026, 6, 2), datetime(2026, 6, 2, 17, 45)),
    ]
    m = measure_broker_latency(rows)
    assert m.measured is True
    assert m.n == 2
    assert m.max.total_seconds() == 90 * 60  # 16:15 → 17:45
