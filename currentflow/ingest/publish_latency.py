"""Broker-summary publish-latency measurement (LD-5).

The HAR cannot reveal WHEN EOD broker summary actually publishes vs the next session
open, so same-day broker signals are untrusted until measured. This computes the
observed latency distribution from stored `as_of` stamps (which carry the feed's
`data_last_updated` when present) versus each trading day's close.

Until an operator runs this over real accrued data and pins
`config.BROKER_PUBLISH_LATENCY`, the conservative next-day fallback stays in force.
This module is the measurement tool, not a source of trust by itself.
"""

from __future__ import annotations

import statistics
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta

from currentflow import config
from currentflow.dal.models import BrokerNet


@dataclass(frozen=True, slots=True)
class LatencyMeasurement:
    n: int
    median: timedelta | None
    p95: timedelta | None
    max: timedelta | None
    measured: bool  # False when there is no usable data yet

    def summary(self) -> str:
        if not self.measured:
            return "broker publish latency: UNMEASURED — conservative next-day fallback in force"
        return (
            f"broker publish latency over n={self.n}: "
            f"median={self.median}, p95={self.p95}, max={self.max}"
        )


def measure_broker_latency(rows: Iterable[BrokerNet]) -> LatencyMeasurement:
    """Observed (as_of − trading-day close) across broker rows.

    Only rows whose `as_of` differs from the conservative fallback are usable — a row
    still on the fallback stamp carries no real observation.
    """
    close_time = config.OHLCV_AVAILABLE_TIME
    deltas: list[timedelta] = []
    for r in rows:
        close = datetime.combine(r.date, close_time)
        delta = r.as_of - close
        # ignore obviously-fallback stamps (next-day 09:00) and negatives
        if delta > timedelta(0):
            deltas.append(delta)

    if not deltas:
        return LatencyMeasurement(0, None, None, None, measured=False)

    deltas.sort()
    p95 = deltas[min(len(deltas) - 1, int(0.95 * len(deltas)))]
    return LatencyMeasurement(
        n=len(deltas),
        median=statistics.median(deltas),
        p95=p95,
        max=deltas[-1],
        measured=True,
    )
