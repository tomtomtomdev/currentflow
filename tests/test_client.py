"""Client tests: 401 fail-loud (no retry), refresh path, exponential backoff."""

from __future__ import annotations

from datetime import date

import pytest

from currentflow.dal.client import ExodusClient
from currentflow.dal.errors import AuthError, RateLimitError
from tests.conftest import recording_sleep, scripted_transport

D0, D1 = date(2026, 6, 1), date(2026, 6, 5)


async def test_401_fails_loud_without_retry():
    calls: list = []
    delays: list[float] = []
    client = ExodusClient(
        scripted_transport([401], calls), sleep=recording_sleep(delays)
    )
    with pytest.raises(AuthError):
        await client.broker_summary("BBCA", D0, D1)
    assert len(calls) == 1  # no retry on 401
    assert delays == []     # never slept


async def test_401_then_refresh_then_success():
    refreshed = {"n": 0}

    async def refresh() -> None:
        refreshed["n"] += 1

    calls: list = []
    client = ExodusClient(
        scripted_transport([401, (200, {"data": []})], calls), refresh=refresh
    )
    rows = await client.ohlcv_foreign("BBCA", D0, D1)
    assert rows == []
    assert refreshed["n"] == 1
    assert len(calls) == 2


async def test_401_after_refresh_still_fails_loud():
    async def refresh() -> None:
        return None

    client = ExodusClient(scripted_transport([401, 401]), refresh=refresh)
    with pytest.raises(AuthError):
        await client.ohlcv_foreign("BBCA", D0, D1)


async def test_backoff_retries_then_succeeds():
    delays: list[float] = []
    client = ExodusClient(
        scripted_transport([429, 429, (200, {"data": []})]),
        sleep=recording_sleep(delays),
        backoff_base=2.0,
    )
    rows = await client.ohlcv_foreign("BBCA", D0, D1)
    assert rows == []
    assert delays == [2.0, 4.0]  # exponential


async def test_backoff_exhausts_and_raises():
    delays: list[float] = []
    client = ExodusClient(
        scripted_transport([429, 429, 429, 429, 429]),
        sleep=recording_sleep(delays),
        max_retries=4,
        backoff_base=2.0,
    )
    with pytest.raises(RateLimitError):
        await client.ohlcv_foreign("BBCA", D0, D1)
    assert delays == [2.0, 4.0, 8.0, 16.0]  # 4 retries, then give up


async def test_transport_exception_is_retried():
    delays: list[float] = []
    client = ExodusClient(
        scripted_transport([RateLimitError, (200, {"data": []})]),
        sleep=recording_sleep(delays),
    )
    rows = await client.ohlcv_foreign("BBCA", D0, D1)
    assert rows == []
    assert delays == [2.0]
