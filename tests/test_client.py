"""Client tests: 401 fail-loud (no retry), refresh path, exponential backoff."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from currentflow import config
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
        await client.broker_summary("BBCA", D0)
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


# --- feed pagination / per-day contracts (slice 13, live-verified) -----------------


def _ohlcv_page(n_rows: int, start_day: int) -> dict:
    """Live response shape: rows under data.result, plus a paginate node."""
    rows = [
        {
            "date": (date(2026, 1, 1) + timedelta(days=start_day + i)).isoformat(),
            "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1,
            "value": 1, "frequency": 1, "average": 100.0,
            "foreign_buy": 0, "foreign_sell": 0, "net_foreign": 0,
            "change_percentage": 0.0,
        }
        for i in range(n_rows)
    ]
    return {"data": {"result": rows, "paginate": {"next_page": "x"}}}


async def test_ohlcv_pages_until_short_page():
    """The endpoint caps un-paginated calls at ~12 rows; the client must walk
    limit-sized pages until a short page so a backfill range is never truncated."""
    calls: list = []
    full = config.OHLCV_PAGE_LIMIT
    client = ExodusClient(
        scripted_transport(
            [(200, _ohlcv_page(full, 0)), (200, _ohlcv_page(3, full))], calls
        )
    )
    rows = await client.ohlcv_foreign("BBCA", D0, D1)
    assert len(rows) == full + 3
    assert [(p["page"], p["limit"]) for _path, p in calls] == [(1, full), (2, full)]


async def test_broker_summary_is_single_day():
    """A multi-day range returns a server-side AGGREGATE stamped at `from`
    (live-verified) — the method must request exactly one day."""
    calls: list = []
    client = ExodusClient(scripted_transport([(200, {"data": {}})], calls))
    rows = await client.broker_summary("BBCA", D0)
    assert rows == []
    (_path, params) = calls[0]
    assert params["from"] == params["to"] == D0.isoformat()
