"""Every exodus request must look like the browser session it impersonates.

From 2026-08-08: `login/v6/username` began returning a bodyless 403 to the CLI while the
same account logged in from Chrome 15 seconds later. An empty-bodied 403 (application
rejections carry a `message`) points at an edge/bot filter, and the CLI's request was
trivially bot-shaped — `python-httpx/x.y` with no Origin or Referer.

These tests pin the headers so a future refactor cannot quietly drop them and resurrect
the 403 as a mystery.
"""

from __future__ import annotations

import httpx
import pytest

from currentflow import config
from currentflow.dal.auth import AuthClient
from currentflow.dal.transport import HttpxTransport

REQUIRED = ("user-agent", "origin", "referer", "accept")


def _capture() -> tuple[httpx.AsyncClient, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"data": {"ok": True}})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler)), seen


# --- the constant itself --------------------------------------------------------------


def test_browser_headers_are_not_httpx_default():
    ua = config.BROWSER_HEADERS["User-Agent"]
    assert "httpx" not in ua.lower(), "a python-httpx UA is what the filter rejects"
    assert "Mozilla" in ua


def test_browser_headers_carry_origin_and_referer():
    assert config.BROWSER_HEADERS["Origin"] == "https://stockbit.com"
    assert config.BROWSER_HEADERS["Referer"].startswith("https://stockbit.com")


def test_browser_headers_request_json():
    assert config.BROWSER_HEADERS["Accept"] == "application/json"


# --- AuthClient (the endpoint that was 403ing) ----------------------------------------


@pytest.mark.asyncio
async def test_auth_client_sends_browser_headers():
    client, seen = _capture()
    auth = AuthClient(client=client)
    try:
        await auth.challenge_start("v-token")
    finally:
        await auth.aclose()

    assert seen, "no request captured"
    sent = {k.lower(): v for k, v in seen[0].headers.items()}
    for h in REQUIRED:
        assert h in sent, f"AuthClient dropped {h}"
    assert "httpx" not in sent["user-agent"].lower()


@pytest.mark.asyncio
async def test_auth_client_still_sends_the_json_body():
    """Headers must not displace the payload."""
    client, seen = _capture()
    auth = AuthClient(client=client)
    try:
        await auth.challenge_start("v-token")
    finally:
        await auth.aclose()
    assert b"v-token" in seen[0].content
    assert seen[0].headers["content-type"] == "application/json"


# --- HttpxTransport (every data feed) -------------------------------------------------


@pytest.mark.asyncio
async def test_transport_get_sends_browser_headers_and_bearer():
    client, seen = _capture()
    t = HttpxTransport(token_provider=lambda: "tok-123", client=client)
    await t.get("marketdetectors/BBCA", {"from": "2026-08-07"})

    sent = {k.lower(): v for k, v in seen[0].headers.items()}
    for h in REQUIRED:
        assert h in sent, f"transport GET dropped {h}"
    assert sent["authorization"] == "Bearer tok-123", "Bearer must survive the merge"


@pytest.mark.asyncio
async def test_transport_post_sends_browser_headers_and_bearer():
    client, seen = _capture()
    t = HttpxTransport(token_provider=lambda: "tok-123", client=client)
    await t.post("screener/templates", {"q": 1})

    sent = {k.lower(): v for k, v in seen[0].headers.items()}
    for h in REQUIRED:
        assert h in sent, f"transport POST dropped {h}"
    assert sent["authorization"] == "Bearer tok-123"


@pytest.mark.asyncio
async def test_bearer_is_never_overridden_by_the_header_block():
    """Authorization is per-request; the shared block must never contain one."""
    assert not any(k.lower() == "authorization" for k in config.BROWSER_HEADERS)
