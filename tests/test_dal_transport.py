"""Slice 10 — live DAL transport: Keychain token store, httpx adapter, and the
session factory that wires them into `ExodusClient`.

No real network, no real Keychain: the `security` subprocess is faked and httpx is
driven by `httpx.MockTransport`.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx
import pytest

from currentflow.dal.errors import AuthError, TransportError
from currentflow.dal.session import build_live_client, session_status
from currentflow.dal.token_store import KeychainTokenStore
from currentflow.dal.transport import HttpxTransport


# --- fake `security` CLI ---------------------------------------------------------


@dataclass
class _Proc:
    returncode: int
    stdout: str = ""
    stderr: str = ""


def fake_keychain():
    """Return (runner, state) — an in-memory stand-in for the `security` CLI."""
    state: dict[str, str] = {}

    def runner(argv):
        cmd = argv[1]
        if cmd == "find-generic-password":
            tok = state.get("token")
            return _Proc(0, stdout=tok + "\n") if tok is not None else _Proc(44)
        if cmd == "add-generic-password":
            state["token"] = argv[argv.index("-w") + 1]
            return _Proc(0)
        if cmd == "delete-generic-password":
            existed = state.pop("token", None) is not None
            return _Proc(0) if existed else _Proc(44)
        return _Proc(1, stderr="unknown")

    return runner, state


# --- token store -----------------------------------------------------------------


def test_store_set_get_clear_roundtrip():
    runner, _ = fake_keychain()
    store = KeychainTokenStore(runner=runner)
    assert store.get() is None  # missing → None (never blank)
    store.set("abc123")
    assert store.get() == "abc123"
    store.clear()
    assert store.get() is None


def test_store_strips_bearer_prefix_and_whitespace():
    runner, _ = fake_keychain()
    store = KeychainTokenStore(runner=runner)
    store.set("  Bearer   xyz789  ")
    assert store.get() == "xyz789"


def test_store_refuses_empty_token():
    runner, _ = fake_keychain()
    store = KeychainTokenStore(runner=runner)
    with pytest.raises(ValueError):
        store.set("   ")


def test_store_set_failure_raises():
    def runner(argv):
        return _Proc(1, stderr="keychain locked")

    store = KeychainTokenStore(runner=runner)
    with pytest.raises(RuntimeError, match="keychain locked"):
        store.set("abc")


def test_session_status_masks_token():
    runner, _ = fake_keychain()
    store = KeychainTokenStore(runner=runner)
    empty = session_status(store)
    assert empty["has_token"] is False and empty["preview"] is None and empty["length"] == 0
    store.set("abcdefghijkl")
    st = session_status(store)
    assert st["has_token"] and st["length"] == 12
    assert st["source"] == "paste"  # slice 11: pasted-Bearer path
    assert st["preview"] == "abcd…ijkl"
    assert "efgh" not in st["preview"]  # middle never leaked


# --- httpx transport -------------------------------------------------------------


def _mock_client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_transport_injects_bearer_and_base_url():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("Authorization")
        return httpx.Response(200, json={"data": []})

    t = HttpxTransport(token_provider=lambda: "tok42", client=_mock_client(handler))
    resp = await t.get("marketdetectors/BBCA", {"from": "2026-06-01"})
    assert resp.status_code == 200 and resp.json() == {"data": []}
    assert seen["url"].startswith("https://exodus.stockbit.com/marketdetectors/BBCA")
    assert "from=2026-06-01" in seen["url"]
    assert seen["auth"] == "Bearer tok42"
    await t.aclose()


async def test_transport_post_sends_json_body():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["body"] = request.content
        return httpx.Response(200, json={"data": {"results": []}})

    t = HttpxTransport(token_provider=lambda: "tok", client=_mock_client(handler))
    await t.post("screener/templates", {"a": 1})
    assert seen["method"] == "POST"
    assert b'"a": 1' in seen["body"] or b'"a":1' in seen["body"]
    await t.aclose()


async def test_transport_reads_token_fresh_each_request():
    box = {"tok": "old"}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"seen": request.headers["Authorization"]})

    t = HttpxTransport(token_provider=lambda: box["tok"], client=_mock_client(handler))
    r1 = await t.get("p", {})
    box["tok"] = "new"  # a refresh happened out of band
    r2 = await t.get("p", {})
    assert r1.json()["seen"] == "Bearer old"
    assert r2.json()["seen"] == "Bearer new"  # not cached from construction
    await t.aclose()


async def test_transport_missing_token_fails_loud():
    t = HttpxTransport(token_provider=lambda: None, client=_mock_client(lambda r: None))
    with pytest.raises(AuthError):
        await t.get("p", {})  # never sends a blank Authorization header
    await t.aclose()


async def test_transport_network_error_becomes_transport_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    t = HttpxTransport(token_provider=lambda: "tok", client=_mock_client(handler))
    with pytest.raises(TransportError):
        await t.get("p", {})
    await t.aclose()


async def test_transport_passes_status_through_for_client_to_map():
    # 401/429/5xx are NOT raised by the transport — the client maps them.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429)

    t = HttpxTransport(token_provider=lambda: "tok", client=_mock_client(handler))
    resp = await t.get("p", {})
    assert resp.status_code == 429
    await t.aclose()


# --- session factory (end-to-end through ExodusClient) ---------------------------


async def test_build_live_client_authenticates_and_parses():
    runner, _ = fake_keychain()
    store = KeychainTokenStore(runner=runner)
    store.set("livetoken")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer livetoken"
        return httpx.Response(200, json={"data": []})

    client, transport = build_live_client(store=store, client=_mock_client(handler))
    from datetime import date

    rows = await client.ohlcv_foreign("BBCA", date(2026, 6, 1), date(2026, 6, 5))
    assert rows == []
    await transport.aclose()


async def test_build_live_client_401_without_prompt_fails_loud():
    runner, _ = fake_keychain()
    store = KeychainTokenStore(runner=runner)
    store.set("expired")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401)

    client, transport = build_live_client(store=store, client=_mock_client(handler))
    from datetime import date

    with pytest.raises(AuthError):
        await client.ohlcv_foreign("BBCA", date(2026, 6, 1), date(2026, 6, 5))
    await transport.aclose()


async def test_build_live_client_401_then_prompt_refresh_succeeds():
    runner, _ = fake_keychain()
    store = KeychainTokenStore(runner=runner)
    store.set("expired")
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        # first request carries the expired token → 401; after refresh, the new one.
        if request.headers["Authorization"] == "Bearer fresh":
            return httpx.Response(200, json={"data": []})
        return httpx.Response(401)

    client, transport = build_live_client(
        store=store, prompt=lambda: "fresh", client=_mock_client(handler)
    )
    from datetime import date

    rows = await client.ohlcv_foreign("BBCA", date(2026, 6, 1), date(2026, 6, 5))
    assert rows == []
    assert calls["n"] == 2  # 401, then success after re-paste
    assert store.get() == "fresh"  # refresh persisted the new token
    await transport.aclose()
