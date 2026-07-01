"""ExodusClient — thin async client over Stockbit `exodus` (DATA_SOURCES.md §6).

Transport is injected (`transport(path, params) -> Response`) so the network is
mockable in tests and swappable in prod. Behavior contract:
  * Bearer auth via a `token_provider`; a single `refresh` attempt on the first 401.
  * 401 → AuthError, raised loud, NEVER retried, never emits stale/empty.
  * 429 / 5xx / network → exponential backoff (2,4,8,16s), up to `max_retries`.
  * Paywall counter (402/403) → PaywallError, retried with backoff (throttle intent).

Slice-1 methods: broker_summary, ohlcv_foreign. Later feeds raise NotImplementedError.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import date as Date
from typing import Any, Protocol

from currentflow import config
from currentflow.dal.errors import (
    AuthError,
    PaywallError,
    RateLimitError,
    TransportError,
)
from currentflow.dal.models import BrokerNet, DailyBar
from currentflow.dal.parse import parse_broker_summary, parse_ohlcv

log = logging.getLogger(__name__)


class Response(Protocol):
    status_code: int

    def json(self) -> Any: ...


Transport = Callable[[str, dict], Awaitable[Response]]


class ExodusClient:
    def __init__(
        self,
        transport: Transport,
        *,
        token_provider: Callable[[], str] | None = None,
        refresh: Callable[[], Awaitable[None]] | Callable[[], None] | None = None,
        max_retries: int = config.MAX_RETRIES,
        backoff_base: float = config.BACKOFF_BASE_SECONDS,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._transport = transport
        self._token_provider = token_provider
        self._refresh = refresh
        self._max_retries = max_retries
        self._backoff_base = backoff_base
        self._sleep = sleep

    # --- feeds (Slice 1) ----------------------------------------------------------

    async def broker_summary(
        self, symbol: str, date_from: Date, date_to: Date
    ) -> list[BrokerNet]:
        """marketdetectors/{sym} — broker net buy/sell, history to 2019."""
        params = {
            "from": date_from.isoformat(),
            "to": date_to.isoformat(),
            "transaction_type": "TRANSACTION_TYPE_NET",
            "market_board": "MARKET_BOARD_REGULER",
            "investor_type": "INVESTOR_TYPE_ALL",
        }
        payload = await self._get(f"marketdetectors/{symbol}", params)
        return parse_broker_summary(symbol, payload)

    async def ohlcv_foreign(
        self, symbol: str, date_from: Date, date_to: Date
    ) -> list[DailyBar]:
        """company-price-feed/historical/summary/{sym} — OHLCV + foreign + VWAP."""
        params = {
            "period": "HS_PERIOD_DAILY",
            "start_date": date_from.isoformat(),
            "end_date": date_to.isoformat(),
        }
        payload = await self._get(
            f"company-price-feed/historical/summary/{symbol}", params
        )
        return parse_ohlcv(symbol, payload)

    # --- later slices -------------------------------------------------------------

    async def corp_actions(self, symbol: str) -> Any:  # pragma: no cover - slice 2+
        raise NotImplementedError("corp_actions lands in Slice 2 (universe gate)")

    async def status_flags(self, symbol: str) -> Any:  # pragma: no cover - slice 2+
        raise NotImplementedError("status_flags lands in Slice 2 (universe gate)")

    # --- request core -------------------------------------------------------------

    async def _get(self, path: str, params: dict) -> Any:
        attempt = 0
        refreshed = False
        while True:
            try:
                resp = await self._transport(path, params)
            except (RateLimitError, TransportError) as exc:
                attempt = await self._maybe_backoff(attempt, path, repr(exc))
                continue
            except AuthError:
                raise  # fail loud, no retry

            status = resp.status_code
            if status == 200:
                return resp.json()
            if status == 401:
                # one refresh attempt, then fail loud — never emit stale/empty.
                if self._refresh is not None and not refreshed:
                    refreshed = True
                    await self._do_refresh()
                    continue
                raise AuthError(f"401 on {path}: token expired/invalid — re-capture required")
            if status in (402, 403):
                attempt = await self._maybe_backoff(
                    attempt, path, f"paywall/forbidden {status}", PaywallError
                )
                continue
            if status == 429:
                attempt = await self._maybe_backoff(
                    attempt, path, "429 rate limited", RateLimitError
                )
                continue
            if 500 <= status < 600:
                attempt = await self._maybe_backoff(
                    attempt, path, f"server {status}", TransportError
                )
                continue
            raise TransportError(f"unexpected status {status} on {path}")

    async def _maybe_backoff(
        self, attempt: int, path: str, why: str, exc_type: type[Exception] = TransportError
    ) -> int:
        """Sleep with exponential backoff, or raise once retries are exhausted."""
        if attempt >= self._max_retries:
            raise exc_type(f"{why} on {path}: exhausted {self._max_retries} retries")
        delay = self._backoff_base * (2**attempt)
        log.warning("retry %d/%d on %s (%s) after %.0fs", attempt + 1, self._max_retries, path, why, delay)
        await self._sleep(delay)
        return attempt + 1

    async def _do_refresh(self) -> None:
        assert self._refresh is not None
        result = self._refresh()
        if asyncio.iscoroutine(result):
            await result
