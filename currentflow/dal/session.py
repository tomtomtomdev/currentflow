"""Live-session factory: wire the Keychain token + httpx transport into an
`ExodusClient` (slice 10; extended slice 11 for the credential-login session).

This is the one production construction site for the client — everywhere else the
client is transport-injected for tests. The Bearer comes from the operator's own
authenticated session (own risk, §15): either the credential-login session (slice 11,
access+refresh in the Keychain) or a hand-pasted Bearer (slice 10 fallback).
`store.access_token()` prefers the login session and falls back to the paste, so the
transport is agnostic to which auth path established the session.
"""

from __future__ import annotations

from typing import Awaitable, Callable

from currentflow.dal.client import ExodusClient
from currentflow.dal.token_store import KeychainTokenStore
from currentflow.dal.transport import HttpxTransport


def build_live_client(
    *,
    store: KeychainTokenStore | None = None,
    prompt: Callable[[], str] | None = None,
    refresher: Callable[[], Awaitable[None]] | Callable[[], None] | None = None,
    client=None,
) -> tuple[ExodusClient, HttpxTransport]:
    """Return `(client, transport)`. Close the transport (or use it as a context
    manager) when done to release the underlying httpx connection pool.

    On a 401 the client calls a refresh seam once, then fails loud:
      * `refresher` (slice 11) — a real token refresh using the stored refresh token
        (see `build_session_refresh`). Takes precedence when supplied.
      * `prompt` (slice 10) — re-capture a pasted Bearer.
    Without either, a 401 fails loud immediately (AuthError) — the operator must
    re-login. `client` is an injectable `httpx.AsyncClient` (tests back it with
    `httpx.MockTransport`).
    """
    store = store or KeychainTokenStore()
    transport = HttpxTransport(token_provider=store.access_token, client=client)

    refresh: Callable[[], Awaitable[None]] | Callable[[], None] | None = None
    if refresher is not None:
        refresh = refresher
    elif prompt is not None:

        def refresh() -> None:
            new = prompt()
            if new and new.strip():
                store.set(new)

    exodus = ExodusClient(
        transport.get,
        post_transport=transport.post,
        token_provider=store.access_token,
        refresh=refresh,
    )
    return exodus, transport


def build_session_refresh(
    store: KeychainTokenStore,
    *,
    client=None,
) -> Callable[[], Awaitable[None]]:
    """A 401-refresh seam that swaps the stored refresh token for a fresh session via
    `dal.auth.AuthClient.refresh`. Fails LOUD (AuthError) on any failure — including
    the current reality that the refresh route is unconfirmed (§4.1), so this always
    raises until captured, sending the UI back to the login form rather than serving
    stale/empty. Wire this into `build_live_client(refresher=…)`."""
    from currentflow.dal.auth import AuthClient
    from currentflow.dal.errors import AuthError

    async def refresh() -> None:
        token = store.get_refresh()
        if not token:
            raise AuthError("no refresh token stored — re-login required")
        auth = AuthClient(client=client)
        try:
            session = await auth.refresh(token)  # raises until §4.1 route pinned
        finally:
            await auth.aclose()
        from currentflow.dal.token_store import SessionData

        store.set_session(
            SessionData(
                access_token=session.access_token,
                access_expires=session.access_expires,
                refresh_token=session.refresh_token,
                refresh_expires=session.refresh_expires,
                username=session.username,
            )
        )

    return refresh


def store_auth_session(store: KeychainTokenStore, session) -> None:
    """Persist a `dal.auth.Session` into the Keychain as the credential-login session.
    The one place the auth-client shape is mapped onto the store shape (used by the
    CLI and the login view)."""
    from currentflow.dal.token_store import SessionData

    store.set_session(
        SessionData(
            access_token=session.access_token,
            access_expires=session.access_expires,
            refresh_token=session.refresh_token,
            refresh_expires=session.refresh_expires,
            username=session.username,
        )
    )


def session_status(store: KeychainTokenStore | None = None) -> dict:
    """Non-network health of the local session: is a token captured, by which path,
    and a masked preview so the operator can confirm which session is live without
    leaking it."""
    store = store or KeychainTokenStore()
    session = store.get_session()
    if session is not None:
        token = session.access_token
        return {
            "has_token": True,
            "source": "login",
            "username": session.username,
            "access_expires": session.access_expires,
            "preview": _mask(token),
            "length": len(token),
        }
    token = store.get()
    if not token:
        return {"has_token": False, "source": None, "preview": None, "length": 0}
    return {
        "has_token": True,
        "source": "paste",
        "username": None,
        "access_expires": None,
        "preview": _mask(token),
        "length": len(token),
    }


def _mask(token: str) -> str:
    return f"{token[:4]}…{token[-4:]}" if len(token) > 8 else "…"
