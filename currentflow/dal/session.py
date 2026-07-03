"""Live-session factory: wire the Keychain token + httpx transport into an
`ExodusClient` (slice 10).

This is the one production construction site for the client — everywhere else the
client is transport-injected for tests. The Bearer comes from the operator's own
authenticated session (own risk, §15); nothing here reaches out except the exodus
calls the client makes.
"""

from __future__ import annotations

from typing import Callable

from currentflow.dal.client import ExodusClient
from currentflow.dal.token_store import KeychainTokenStore
from currentflow.dal.transport import HttpxTransport


def build_live_client(
    *,
    store: KeychainTokenStore | None = None,
    prompt: Callable[[], str] | None = None,
    client=None,
) -> tuple[ExodusClient, HttpxTransport]:
    """Return `(client, transport)`. Close the transport (or use it as a context
    manager) when done to release the underlying httpx connection pool.

    `prompt` (optional) supplies a fresh Bearer when the token expires: on a 401 the
    client calls `refresh` once, which re-captures via `prompt` and re-stores in the
    Keychain. Without a `prompt`, a 401 fails loud immediately (AuthError) — the
    operator must re-run `dal.login paste`. `client` is an injectable
    `httpx.AsyncClient` (tests pass one backed by `httpx.MockTransport`).
    """
    store = store or KeychainTokenStore()
    transport = HttpxTransport(token_provider=store.get, client=client)

    refresh: Callable[[], None] | None = None
    if prompt is not None:

        def refresh() -> None:
            new = prompt()
            if new and new.strip():
                store.set(new)

    exodus = ExodusClient(
        transport.get,
        post_transport=transport.post,
        token_provider=store.get,
        refresh=refresh,
    )
    return exodus, transport


def session_status(store: KeychainTokenStore | None = None) -> dict:
    """Non-network health of the local session: is a Bearer captured, and a masked
    preview so the operator can confirm which token is live without leaking it."""
    store = store or KeychainTokenStore()
    token = store.get()
    if not token:
        return {"has_token": False, "preview": None, "length": 0}
    preview = f"{token[:4]}…{token[-4:]}" if len(token) > 8 else "…"
    return {"has_token": True, "preview": preview, "length": len(token)}
