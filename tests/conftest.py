"""Shared test fixtures: fake transport/response + sample `exodus` payloads.

No network. The DAL's injected transport is scripted so client behavior (auth,
backoff, parsing) is fully deterministic.
"""

from __future__ import annotations

from typing import Any

import pytest

from currentflow.store.db import Store


class FakeResponse:
    def __init__(self, status_code: int, payload: Any = None) -> None:
        self.status_code = status_code
        self._payload = payload if payload is not None else {}

    def json(self) -> Any:
        return self._payload


def scripted_transport(steps: list, calls: list | None = None):
    """Build an async transport that yields `steps` in order.

    A step is: an int status; a (status, payload) tuple; or an Exception (class or
    instance) to raise (simulating a network failure before any response).
    """
    it = iter(steps)

    async def transport(path: str, params: dict) -> FakeResponse:
        if calls is not None:
            calls.append((path, params))
        step = next(it)
        if isinstance(step, type) and issubclass(step, BaseException):
            raise step("simulated transport failure")
        if isinstance(step, BaseException):
            raise step
        if isinstance(step, tuple):
            return FakeResponse(step[0], step[1])
        return FakeResponse(step)

    return transport


def recording_sleep(delays: list[float]):
    async def _sleep(d: float) -> None:
        delays.append(d)

    return _sleep


# --- sample payloads --------------------------------------------------------------


def ohlcv_payload(rows: list[dict]) -> dict:
    return {"data": rows}


def broker_payload(buys: list[dict], sells: list[dict], data_last_updated: str | None = None) -> dict:
    inner: dict = {"broker_summary": {"brokers_buy": buys, "brokers_sell": sells}}
    if data_last_updated is not None:
        inner["data_last_updated"] = data_last_updated
    return {"data": inner}


@pytest.fixture
def store() -> Store:
    s = Store(":memory:")
    yield s
    s.close()
