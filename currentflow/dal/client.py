"""ExodusClient — thin async client over Stockbit `exodus` (DATA_SOURCES.md §6).

Transport is injected (`transport(path, params) -> Response`) so the network is
mockable in tests and swappable in prod. Behavior contract:
  * Bearer auth via a `token_provider`; a single `refresh` attempt on the first 401.
  * 401 → AuthError, raised loud, NEVER retried, never emits stale/empty.
  * 429 / 5xx / network → exponential backoff (2,4,8,16s), up to `max_retries`.
  * Paywall counter (402/403) → PaywallError, retried with backoff (throttle intent).

Slice-1 methods: broker_summary, ohlcv_foreign.
Slice-2 methods: symbol_info, corp_actions, special_board, run_screener (POST).
Slice-3 methods: ksei_ownership.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import date as Date
from datetime import datetime
from typing import Any, Protocol

from currentflow import config
from currentflow.dal.errors import (
    AuthError,
    PaywallError,
    RateLimitError,
    TransportError,
)
from currentflow.dal.models import (
    BoardType,
    BrokerNet,
    CorpAction,
    DailyBar,
    OwnershipSlice,
    SymbolInfo,
)
from currentflow.dal.netlog import log_net_error
from currentflow.dal.parse import (
    ohlcv_page_rowcount,
    parse_broker_summary,
    parse_corp_actions,
    parse_ksei_ownership,
    parse_ohlcv,
    parse_screener_results,
    parse_screener_totalrows,
    parse_special_board,
    parse_symbol_info,
)

log = logging.getLogger(__name__)


class Response(Protocol):
    status_code: int

    def json(self) -> Any: ...


Transport = Callable[[str, dict], Awaitable[Response]]
# POST transport: (path, json_body) -> Response. Injected separately so slice-1
# GET-only transports keep working unchanged.
PostTransport = Callable[[str, dict], Awaitable[Response]]


class ExodusClient:
    def __init__(
        self,
        transport: Transport,
        *,
        post_transport: PostTransport | None = None,
        token_provider: Callable[[], str] | None = None,
        refresh: Callable[[], Awaitable[None]] | Callable[[], None] | None = None,
        max_retries: int = config.MAX_RETRIES,
        backoff_base: float = config.BACKOFF_BASE_SECONDS,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        now: Callable[[], datetime] = datetime.now,
    ) -> None:
        self._transport = transport
        self._post_transport = post_transport
        self._token_provider = token_provider
        self._refresh = refresh
        self._max_retries = max_retries
        self._backoff_base = backoff_base
        self._sleep = sleep
        # injectable clock for live-snapshot `as_of` stamps (tests pin it)
        self._now = now

    # --- feeds (Slice 1) ----------------------------------------------------------

    async def broker_summary(self, symbol: str, day: Date) -> list[BrokerNet]:
        """marketdetectors/{sym} for ONE trading day (`from = to = day`).

        Live-verified (slice 13): a multi-day range returns a single range
        AGGREGATE with every row stamped `netbs_date = from` — per-day broker
        rows exist only day-by-day, so this method takes one day and callers
        loop (the per-day cost model DATA_SOURCES §7 always budgeted). History
        reaches to 2019; each call is paywall-counted.
        """
        params = {
            "from": day.isoformat(),
            "to": day.isoformat(),
            "transaction_type": "TRANSACTION_TYPE_NET",
            "market_board": "MARKET_BOARD_REGULER",
            "investor_type": "INVESTOR_TYPE_ALL",
        }
        payload = await self._get(f"marketdetectors/{symbol}", params)
        return parse_broker_summary(symbol, payload)

    async def ohlcv_foreign(
        self, symbol: str, date_from: Date, date_to: Date
    ) -> list[DailyBar]:
        """company-price-feed/historical/summary/{sym} — OHLCV + foreign + VWAP.

        Paginated (live-verified, slice 13): without `limit`/`page` the server
        returns only ~12 most-recent rows regardless of the range, and `limit`
        beyond `OHLCV_PAGE_LIMIT` (50) is a 400. Pages are walked (newest-first)
        until a short page, so a backfill range is never silently truncated.
        """
        params = {
            "period": "HS_PERIOD_DAILY",
            "start_date": date_from.isoformat(),
            "end_date": date_to.isoformat(),
            "limit": config.OHLCV_PAGE_LIMIT,
        }
        bars: list[DailyBar] = []
        page = 1
        while True:
            payload = await self._get(
                f"company-price-feed/historical/summary/{symbol}",
                {**params, "page": page},
            )
            bars.extend(parse_ohlcv(symbol, payload))
            # Terminate on the RAW page size, not the parsed count: a malformed row
            # would shrink the parse yield and end a backfill early (silent truncation).
            if ohlcv_page_rowcount(payload) < config.OHLCV_PAGE_LIMIT:
                return bars
            page += 1

    # --- feeds (Slice 2: universe gate + screeners) ---------------------------------

    async def symbol_info(self, symbol: str) -> SymbolInfo:
        """emitten/{sym}/info — suspend/UMA/notation flags + index membership (§3).

        Live snapshot: `as_of` = fetch time; not historically replayable.
        """
        payload = await self._get(f"emitten/{symbol}/info", {})
        return parse_symbol_info(symbol, payload, fetched_at=self._now())

    async def corp_actions(self, symbol: str) -> list[CorpAction]:
        """corpaction/{sym} — drives the ±5-day exclusion window (§3)."""
        payload = await self._get(f"corpaction/{symbol}", {})
        return parse_corp_actions(symbol, payload, fetched_at=self._now())

    async def special_board(self) -> dict[str, BoardType]:
        """emitten/indexes/special-board — dev-board membership for ARA/ARB bands."""
        payload = await self._get("emitten/indexes/special-board", {})
        return parse_special_board(payload)

    # --- feeds (Slice 3: foreign flow + replay) --------------------------------------

    async def ksei_ownership(
        self, symbol: str, *, value_year: int | None = None, shareholder_type: str = ""
    ) -> list[OwnershipSlice]:
        """emitten-metadata/shareholders/{sym}/chart — monthly Local vs Foreign %
        (KSEI, lagged). `as_of` = fetch time: KSEI's publish lag is undisclosed, so
        the only availability we can honestly claim is when we pulled it.
        """
        params: dict = {}
        if value_year is not None:
            params["value_year"] = value_year
        if shareholder_type:
            params["shareholder_type"] = shareholder_type
        payload = await self._get(f"emitten-metadata/shareholders/{symbol}/chart", params)
        return parse_ksei_ownership(symbol, payload, fetched_at=self._now())

    async def run_screener(self, template: dict) -> list[dict[str, Any]]:
        """POST screener/templates — server-side pre-filter (screeners.md §1).

        Returns [{symbol, values: {fitem_id: raw}}] per surviving company.

        The endpoint is paginated (live-verified, slice 13): an integer `page` is
        REQUIRED (omitting it → 400 "Screener Page can't be empty") and `limit` is
        the page size. One `SCREENER_PAGE_LIMIT`-sized page normally covers the
        whole universe; if `totalrows` says more survived, keep paging — a screener
        result is never silently truncated (no silent caps).
        """
        rows: list[dict[str, Any]] = []
        page = 1
        while True:
            payload = await self._post(
                "screener/templates",
                {**template, "page": page, "limit": config.SCREENER_PAGE_LIMIT},
            )
            batch = parse_screener_results(payload)
            rows.extend(batch)
            total = parse_screener_totalrows(payload)
            if total is not None and len(rows) >= total:
                return rows
            if not batch:  # empty page — natural end, or a server claiming more
                if total is not None:
                    log.warning(
                        "run_screener: server reports %d total rows but page %d was "
                        "empty — returning the %d received (incomplete)",
                        total, page, len(rows),
                    )
                return rows
            # No `totalrows` to bound us: a short page is the natural end of results.
            # Without this the loop would cap at page 1 and silently truncate a
            # multi-page universe (no silent caps).
            if total is None and len(batch) < config.SCREENER_PAGE_LIMIT:
                return rows
            page += 1

    # --- request core -------------------------------------------------------------

    async def _get(self, path: str, params: dict) -> Any:
        return await self._request(lambda: self._transport(path, params), path, "GET")

    async def _post(self, path: str, body: dict) -> Any:
        if self._post_transport is None:
            raise TransportError(f"POST {path}: no post_transport configured")
        return await self._request(
            lambda: self._post_transport(path, body), path, "POST"
        )

    async def _request(
        self, send: Callable[[], Awaitable[Response]], path: str, method: str
    ) -> Any:
        attempt = 0
        refreshed = False
        while True:
            try:
                resp = await send()
            except (RateLimitError, TransportError) as exc:
                # network/rate-limit already surfaced from the transport (logged there
                # if httpx-level); record the retry decision at this altitude.
                attempt = await self._maybe_backoff(
                    attempt, path, method, type(exc).__name__, exc_type=type(exc)
                )
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
                log_net_error(log, method=method, path=path, status=401,
                              outcome="fail-loud", retryable=False)
                raise AuthError(f"401 on {path}: token expired/invalid — re-capture required")
            if status in (402, 403):
                attempt = await self._maybe_backoff(
                    attempt, path, method, status=status, exc_type=PaywallError
                )
                continue
            if status == 429:
                attempt = await self._maybe_backoff(
                    attempt, path, method, status=429, exc_type=RateLimitError
                )
                continue
            if 500 <= status < 600:
                attempt = await self._maybe_backoff(
                    attempt, path, method, status=status, exc_type=TransportError
                )
                continue
            log_net_error(log, method=method, path=path, status=status,
                          outcome="fail-loud", retryable=False)
            raise TransportError(f"unexpected status {status} on {path}")

    async def _maybe_backoff(
        self,
        attempt: int,
        path: str,
        method: str,
        error_class: str | None = None,
        *,
        status: int | None = None,
        exc_type: type[Exception] = TransportError,
    ) -> int:
        """Sleep with exponential backoff, or raise once retries are exhausted. Logs
        one `net-error` line per retry (WARNING) and one on exhaustion (ERROR)."""
        why = f"status {status}" if status is not None else error_class
        if attempt >= self._max_retries:
            log_net_error(log, method=method, path=path, status=status,
                          error_class=error_class,
                          outcome=f"exhausted after {self._max_retries} retries",
                          retryable=False)
            raise exc_type(f"{why} on {path}: exhausted {self._max_retries} retries")
        delay = self._backoff_base * (2**attempt)
        log_net_error(log, method=method, path=path, status=status,
                      error_class=error_class,
                      outcome=f"retry {attempt + 1}/{self._max_retries} in {delay:.0f}s",
                      retryable=True)
        await self._sleep(delay)
        return attempt + 1

    async def _do_refresh(self) -> None:
        assert self._refresh is not None
        result = self._refresh()
        if asyncio.iscoroutine(result):
            await result
