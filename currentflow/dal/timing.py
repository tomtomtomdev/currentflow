"""`as_of` (availability_ts) derivation — the look-ahead firewall (spec §1, LD-5).

The single place that decides *when* a datum became knowable. Signals must never
consume a record whose `as_of >= decision_ts`; getting this stamp right is what makes
that guarantee real rather than aspirational.
"""

from __future__ import annotations

from datetime import date as Date
from datetime import datetime, timedelta

from currentflow import config


def ohlcv_as_of(trading_day: Date) -> datetime:
    """EOD bar for `trading_day` is available after the ~16:00 WIB close."""
    return datetime.combine(trading_day, config.OHLCV_AVAILABLE_TIME)


def broker_as_of(trading_day: Date, data_last_updated: datetime | None = None) -> datetime:
    """Availability of broker summary for `trading_day`.

    Priority:
      1. `data_last_updated` from the feed, when present — the real observed stamp.
      2. A measured `config.BROKER_PUBLISH_LATENCY` added to the close, once pinned.
      3. Conservative fallback (LD-5): next-day 09:00 WIB. Same-day broker signals
         stay untrusted until latency is measured empirically.
    """
    if data_last_updated is not None:
        return data_last_updated
    if config.BROKER_PUBLISH_LATENCY is not None:
        close = datetime.combine(trading_day, config.OHLCV_AVAILABLE_TIME)
        return close + config.BROKER_PUBLISH_LATENCY
    return datetime.combine(
        trading_day + timedelta(days=1), config.BROKER_CONSERVATIVE_AVAILABLE_TIME
    )
