"""Typed DAL records. Every record carries `as_of` (availability_ts).

`missing data is never zero flow` (CLAUDE.md): fields that were absent from the feed
are `None`, never coerced to 0. A genuine zero (illiquid no-trade day) is 0 with
`status == NO_TRADES` — distinct from absence.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as Date
from datetime import datetime
from enum import Enum


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class InvestorType(str, Enum):
    """Broker `type` tag from marketdetectors (Asing/Lokal/Pemerintah)."""

    FOREIGN = "FOREIGN"      # Asing
    LOCAL = "LOCAL"          # Lokal
    GOVERNMENT = "GOVERNMENT"  # Pemerintah
    UNKNOWN = "UNKNOWN"


class RowStatus(str, Enum):
    """Coverage status for a (symbol, date). 'empty ≠ zero' (DATA_SOURCES §4)."""

    TRADED = "TRADED"                # row present, real activity
    NO_TRADES = "NO_TRADES"          # row present, all-zero (illiquid, e.g. XBIG)
    NOT_PUBLISHED = "NOT_PUBLISHED"  # date not yet available (as_of > now)
    GAP = "GAP"                      # expected trading day, no row, not a calendar holiday


@dataclass(frozen=True, slots=True)
class DailyBar:
    """One EOD bar from company-price-feed/historical/summary/{sym}."""

    symbol: str
    date: Date
    as_of: datetime            # availability_ts (WIB, tz-naive)
    status: RowStatus
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    volume: int | None
    value: float | None
    frequency: int | None
    vwap: float | None          # feed `average`
    foreign_buy: float | None
    foreign_sell: float | None
    net_foreign: float | None
    change_percentage: float | None


@dataclass(frozen=True, slots=True)
class BrokerNet:
    """One broker's buy or sell side for a (symbol, date) from marketdetectors."""

    symbol: str
    date: Date
    as_of: datetime            # availability_ts (WIB, tz-naive)
    broker_code: str
    side: Side
    investor_type: InvestorType
    avg_price: float | None     # netbs_buy_avg_price — accumulator VWAP
    value: float | None         # bval / sval
    lot: int | None             # blot / slot
    frequency: int | None       # freq
