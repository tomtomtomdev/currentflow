"""Bearer-paste fallback (design State C, §9.1): the pasted token is verified with a
live ping BEFORE it is stored — a rejected token is never written; the raw token is
held only for the attempt.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
from test_dal_auth import fake_keychain

from currentflow.dal import session
from currentflow.dal.errors import AuthError, ExodusError
from currentflow.dal.token_store import KeychainTokenStore
from currentflow.ui import login_view as lv


def _store():
    runner, state = fake_keychain()
    return KeychainTokenStore(runner=runner), state


def _ping(exc=None):
    calls: list[str] = []

    async def ping(token: str) -> None:
        calls.append(token)
        if exc is not None:
            raise exc

    return ping, calls


# --- submit_bearer (view-model) ------------------------------------------------------


def test_empty_token_errors_without_pinging_or_storing():
    store, state = _store()
    ping, calls = _ping()
    view = asyncio.run(lv.submit_bearer("   ", store=store, ping=ping))
    assert view.state == lv.BEARER and view.error
    assert calls == [] and state == {}


def test_bearer_prefix_stripped_and_stored_only_on_success():
    store, _ = _store()
    ping, calls = _ping()
    view = asyncio.run(lv.submit_bearer("Bearer abc.def.ghi", store=store, ping=ping))
    assert view.state == lv.FINISH
    assert calls == ["abc.def.ghi"]        # the ping saw the raw token, prefix stripped
    assert store.get() == "abc.def.ghi"


def test_rejected_token_is_never_stored():
    store, state = _store()
    ping, _ = _ping(AuthError("401 unauthorized"))
    view = asyncio.run(lv.submit_bearer("expired-token-xyz", store=store, ping=ping))
    assert view.state == lv.BEARER and "not stored" in (view.error or "")
    assert store.get() is None and state == {}


def test_connection_error_is_not_a_store_either():
    store, state = _store()
    ping, _ = _ping(ExodusError("connect timeout"))
    view = asyncio.run(lv.submit_bearer("sometoken-abcdef", store=store, ping=ping))
    assert view.state == lv.BEARER and view.error
    assert state == {}


# --- verify_bearer (live ping, mocked transport) --------------------------------------


def test_verify_bearer_sends_the_candidate_token():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("Authorization")
        return httpx.Response(200, json={"data": []})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    asyncio.run(session.verify_bearer("candidate123", client=client))
    assert seen["auth"] == "Bearer candidate123"


def test_verify_bearer_fails_loud_on_401():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "unauthorized"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with pytest.raises(AuthError):
        asyncio.run(session.verify_bearer("badtoken", client=client))
