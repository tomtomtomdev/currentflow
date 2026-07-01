"""DAL — thin async client over Stockbit `exodus` (DATA_SOURCES.md §6).

Slice-1 scope (DATA_SOURCES.md §140): `broker_summary` + `ohlcv_foreign` only.
Later feeds (corp_actions, status_flags, fundamentals, orderbook, regime …) land in
their own slices and currently raise NotImplementedError by design.

DAL rules (CLAUDE.md): one method per feed; every returned record carries
`availability_ts`; on 401 fail loud (never emit stale/empty); ingest once and cache.
"""

from currentflow.dal.client import ExodusClient
from currentflow.dal.errors import (
    AuthError,
    ExodusError,
    PaywallError,
    RateLimitError,
    TransportError,
)
from currentflow.dal.models import BrokerNet, DailyBar, RowStatus, Side

__all__ = [
    "ExodusClient",
    "ExodusError",
    "AuthError",
    "PaywallError",
    "RateLimitError",
    "TransportError",
    "BrokerNet",
    "DailyBar",
    "RowStatus",
    "Side",
]
