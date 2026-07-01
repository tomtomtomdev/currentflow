"""Integrity / gap checks — distinguish 'no trades' vs 'not yet published' vs 'gap'.

CLAUDE.md: *missing data is never zero flow*; *no silent caps — log what was dropped*.
For a symbol over a date range this classifies every expected trading day so downstream
signals can NEVER silently read a gap as zero.

Trading-calendar note: without a wired IDX holiday feed we use a weekday proxy and take
an injected `holidays` set. Any weekday with no row that is not a known holiday and is
past its publish horizon is a GAP — surfaced, never swallowed. The holiday-feed gap is
logged (not silently assumed) per the 'no silent caps' rule.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date as Date
from datetime import datetime, timedelta

from currentflow.dal.models import DailyBar, RowStatus
from currentflow.dal.timing import ohlcv_as_of

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CoverageReport:
    symbol: str
    start: Date
    end: Date
    status_by_date: dict[Date, RowStatus]
    holiday_proxy: bool = field(default=True)  # True while no real IDX calendar wired

    @property
    def traded(self) -> list[Date]:
        return sorted(d for d, s in self.status_by_date.items() if s is RowStatus.TRADED)

    @property
    def no_trades(self) -> list[Date]:
        return sorted(d for d, s in self.status_by_date.items() if s is RowStatus.NO_TRADES)

    @property
    def not_published(self) -> list[Date]:
        return sorted(
            d for d, s in self.status_by_date.items() if s is RowStatus.NOT_PUBLISHED
        )

    @property
    def gaps(self) -> list[Date]:
        return sorted(d for d, s in self.status_by_date.items() if s is RowStatus.GAP)

    @property
    def has_gaps(self) -> bool:
        return bool(self.gaps)


def _weekdays(start: Date, end: Date) -> list[Date]:
    days, d = [], start
    while d <= end:
        if d.weekday() < 5:  # Mon–Fri
            days.append(d)
        d += timedelta(days=1)
    return days


def classify_coverage(
    symbol: str,
    start: Date,
    end: Date,
    bars: Iterable[DailyBar],
    *,
    now: datetime,
    holidays: frozenset[Date] = frozenset(),
) -> CoverageReport:
    """Classify every expected trading day in [start, end].

    * present row, activity            → TRADED
    * present row, all-zero            → NO_TRADES   (illiquid; NOT a gap, NOT zero-flow noise)
    * absent, publish horizon not met  → NOT_PUBLISHED (as_of > now)
    * absent, past horizon, not holiday → GAP        (surfaced loudly)
    """
    present = {b.date: b.status for b in bars}
    status_by_date: dict[Date, RowStatus] = {}

    for d in _weekdays(start, end):
        if d in holidays:
            continue  # known non-trading day: not expected, not a gap
        if d in present:
            status_by_date[d] = present[d]
        elif ohlcv_as_of(d) > now:
            status_by_date[d] = RowStatus.NOT_PUBLISHED
        else:
            status_by_date[d] = RowStatus.GAP

    report = CoverageReport(symbol, start, end, status_by_date)
    if report.gaps:
        log.warning(
            "coverage gap: %s has %d unexplained missing trading day(s) in [%s..%s]: %s",
            symbol, len(report.gaps), start, end,
            ", ".join(d.isoformat() for d in report.gaps[:10])
            + (" …" if len(report.gaps) > 10 else ""),
        )
    return report
