"""httpx-backed live transport for `ExodusClient` (slice 10).

Closes the seam every prior slice deferred: turns the injected `Transport` /
`PostTransport` contract into real calls against `EXODUS_BASE_URL`, carrying the
operator's own-session Bearer.

Contract honored (see `ExodusClient._request`):
  * Return the raw `Response` — the CLIENT maps status codes (200/401/402/429/5xx).
    The transport must NOT swallow an HTTP status.
  * Raise `TransportError` on a network-level failure (connect/read/timeout) so the
    client's exponential backoff engages.
  * Raise `AuthError` (fail loud) rather than send a blank `Authorization` header —
    "never emit stale/empty" (CLAUDE.md) extends to never sending no-auth.
The token is read FRESH per request from `token_provider`, so a refresh (re-paste)
takes effect on the next call without rebuilding the client.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

import httpx

from currentflow import config
from currentflow.dal.errors import AuthError, TransportError
from currentflow.dal.netlog import log_net_error

log = logging.getLogger(__name__)


class HttpxTransport:
    """Async GET/POST adapter. Use as an async context manager to own the client
    lifecycle, or pass an existing `httpx.AsyncClient` (tests inject a MockTransport).

    `transport` / `post_transport` bind straight onto `ExodusClient`:
        t = HttpxTransport(token_provider=store.get)
        client = ExodusClient(t.get, post_transport=t.post, refresh=...)
    """

    def __init__(
        self,
        *,
        token_provider: Callable[[], str | None],
        base_url: str = config.EXODUS_BASE_URL,
        client: httpx.AsyncClient | None = None,
        timeout: float = config.HTTP_TIMEOUT_SECONDS,
    ) -> None:
        self._token_provider = token_provider
        self._base_url = base_url.rstrip("/")
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=timeout)

    def _auth_headers(self) -> dict[str, str]:
        token = self._token_provider()
        if not token:
            raise AuthError(
                "no Bearer token captured — run `python -m currentflow.dal.login paste`"
            )
        return {"Authorization": f"Bearer {token}"}

    def _url(self, path: str) -> str:
        return f"{self._base_url}/{path.lstrip('/')}"

    async def get(self, path: str, params: dict) -> httpx.Response:
        try:
            return await self._client.get(
                self._url(path), params=params, headers=self._auth_headers()
            )
        except httpx.TransportError as exc:  # connect/read/timeout — retryable
            log_net_error(log, method="GET", path=path, error_class=type(exc).__name__,
                          outcome="raised-to-client", retryable=True)
            raise TransportError(f"GET {path}: network failure: {exc!r}") from exc

    async def post(self, path: str, body: dict) -> httpx.Response:
        try:
            return await self._client.post(
                self._url(path), json=body, headers=self._auth_headers()
            )
        except httpx.TransportError as exc:
            log_net_error(log, method="POST", path=path, error_class=type(exc).__name__,
                          outcome="raised-to-client", retryable=True)
            raise TransportError(f"POST {path}: network failure: {exc!r}") from exc

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> HttpxTransport:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.aclose()
