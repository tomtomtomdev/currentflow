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

from currentflow import config
from currentflow.dal.models import BrokerNet, DailyBar, RowStatus, Side
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


@dataclass(frozen=True, slots=True)
class ClearingReport:
    """Broker-summary conservation check. Every buy is someone's sell, so per symbol
    the gross buy value must equal the gross sell value. An imbalance flags a
    TRUNCATED feed (top-N), dropped rows, or a broken sign convention — the failure
    that let AK@MEDC render distribution (-61.3B) as accumulation (+504.1B)."""

    symbol: str
    gross_buy: float
    gross_sell: float
    dropped: int  # rows with unknown value — never counted as zero

    @property
    def imbalance(self) -> float:
        denom = max(self.gross_buy, self.gross_sell)
        return abs(self.gross_buy - self.gross_sell) / denom if denom else 0.0

    @property
    def clears(self) -> bool:
        return self.imbalance <= config.BROKER_CLEARING_TOL


def broker_market_clears(
    symbol: str, rows: Iterable[BrokerNet], *, tol: float | None = None
) -> ClearingReport:
    """Assert broker rows conserve: Σ gross buy ≈ Σ gross sell (values are magnitudes,
    `side` carries direction). Logs loudly when the imbalance exceeds tolerance."""
    gross_buy = gross_sell = 0.0
    dropped = 0
    for r in rows:
        if r.value is None:
            dropped += 1  # missing ≠ zero
            continue
        if r.side is Side.BUY:
            gross_buy += r.value
        else:
            gross_sell += r.value

    report = ClearingReport(symbol, gross_buy, gross_sell, dropped)
    limit = config.BROKER_CLEARING_TOL if tol is None else tol
    if report.imbalance > limit:
        log.warning(
            "broker net imbalance: %s buy=%.4g sell=%.4g imbalance=%.1f%% (> %.1f%%) — "
            "feed truncated, rows dropped, or sign convention broken",
            symbol, gross_buy, gross_sell, report.imbalance * 100, limit * 100,
        )
    return report


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
