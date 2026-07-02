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


class BoardType(str, Enum):
    """IDX listing board — selects the ARA/ARB band (spec §12)."""

    MAIN = "MAIN"
    DEVELOPMENT = "DEVELOPMENT"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class SymbolInfo:
    """Universe-gate state flags + index membership from emitten/{sym}/info.

    A *live* snapshot — `as_of` is the fetch time; it is not historically replayable.
    """

    symbol: str
    as_of: datetime
    suspended: bool
    tradeable: bool | None      # feed `tradeable`; None if absent
    uma: bool
    notations: tuple[str, ...]
    indexes: tuple[str, ...]    # LQ45 / IDX80 / IDXSMC-LIQ / …


@dataclass(frozen=True, slots=True)
class CorpAction:
    """One corporate action from corpaction/{sym} — drives the ±5d exclusion (§3)."""

    symbol: str
    as_of: datetime
    action_type: str            # dividend / stocksplit / rightissue / …
    ex_date: Date | None        # primary exclusion anchor
    recording_date: Date | None


@dataclass(frozen=True, slots=True)
class Scr0Row:
    """One SCR-0 eligibility survivor (screeners.md) for a trading day."""

    symbol: str
    date: Date
    as_of: datetime
    adv20: float | None         # fitem 16454 Value MA 20
    price: float | None         # fitem 2661
    free_float: float | None    # fitem 21535 (%)
    market_cap: float | None    # fitem 2892


@dataclass(frozen=True, slots=True)
class OwnershipSlice:
    """One monthly KSEI local-vs-foreign ownership point from
    emitten-metadata/shareholders/{sym}/chart.

    KSEI publishes monthly with a lag the feed does not disclose, so `as_of` is the
    fetch time — the only availability we can honestly claim is when we pulled it.
    """

    symbol: str
    date: Date                  # month bucket (feed's period date)
    as_of: datetime
    foreign_pct: float | None
    local_pct: float | None


@dataclass(frozen=True, slots=True)
class Scr1aRow:
    """One SCR-1A foreign-accumulation survivor (screeners.md) for a trading day."""

    symbol: str
    date: Date
    as_of: datetime
    net_foreign: float | None       # fitem 3194 Net Foreign Buy/Sell
    net_foreign_ma20: float | None  # fitem 13540
    buy_streak: float | None        # fitem 13561 Net Foreign Buy Streak
    flow_ma20: float | None         # fitem 13521 Foreign Flow MA 20


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
