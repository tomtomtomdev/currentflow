"""Exodus credential + MFA login client (slice 11).

Implements the **verified** own-session login wire contract pinned in
`DATA_SOURCES.md §4.1` (from `login-stockbit.har`, 2026-07-03). This is the auth
plumbing that lets the operator sign in with a username/password + OTP instead of
hand-pasting a Bearer (slice 10). It establishes the operator's OWN session, at own
risk (§15); nothing is republished, and only the resulting access/refresh tokens are
persisted (to the Keychain, by the caller) — credentials, OTP, and any recaptcha
token stay transient in memory (§9.1).

The 5-step flow (all `POST … application/json` to `EXODUS_BASE_URL`):
  1. login/v6/username           -> new_device.multi_factor.{login_token, verification_token}
  2. mfa/…/challenge/start        -> next_challenge + otp channels
  3. mfa/…/challenge/otp/send     -> resend cooldown (next_attempt_in)
  4. mfa/…/challenge/otp/verify   -> next_challenge  (LOOPS: repeat send→verify
                                     until next_challenge == CHALLENGE_FINISH)
  5. login/v6/new-device/verify   -> {access, refresh, user}

No Bearer is carried here (there is none yet) — this client posts unauthenticated
JSON. Error mapping mirrors the DAL taxonomy: bad creds / failed OTP → `AuthError`
(fail loud, no retry); network / 5xx → `TransportError`.

SECURITY: this module NEVER logs a password, OTP, recaptcha token, or any token body.
Only endpoint paths and coarse outcomes are ever emitted.

`recaptcha_token` (reCAPTCHA v3, invisible) is **required** — the server rejects an
empty token 400 (confirmed 2026-07-03, §4.1). This client only carries it through; it
never mints one. The operator mints a fresh token in the browser (see `dal.recaptcha`)
and the login views supply it; an empty token is refused before this client is reached.

Two items in §4.1 remain unresolved by the HAR and MUST NOT be guessed in code:
  * `player_id` (OneSignal UUID) — required-vs-optional unconfirmed; carried through.
  * refresh route/shape — not captured. `refresh()` raises until pinned in config.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import httpx

from currentflow import config
from currentflow.dal.errors import AuthError, TransportError
from currentflow.dal.netlog import log_net_error

log = logging.getLogger(__name__)


# --- returned shapes (field-shape only; no secrets logged) -----------------------


@dataclass(frozen=True)
class OtpChannel:
    channel: str          # CHANNEL_EMAIL / CHANNEL_WHATSAPP / CHANNEL_SMS
    target: str           # masked, e.g. "tom****@gmail.com"


@dataclass(frozen=True)
class Challenge:
    """State of the MFA challenge loop. Drive send→verify until `is_finished`."""

    next_challenge: str            # CHALLENGE_OTP … CHALLENGE_FINISH
    channels: list[OtpChannel] = field(default_factory=list)
    default_channel: str | None = None

    @property
    def is_finished(self) -> bool:
        return self.next_challenge == config.CHALLENGE_FINISH


@dataclass(frozen=True)
class OtpSend:
    channel: str
    target: str
    next_attempt_in: int           # resend cooldown, seconds


@dataclass(frozen=True)
class Session:
    """The tokens the rest of the DAL needs. `access_token` is the `Bearer …`."""

    access_token: str
    access_expires: str | None
    refresh_token: str | None
    refresh_expires: str | None
    username: str | None = None
    user_id: str | None = None


@dataclass(frozen=True)
class LoginStart:
    """Result of step 1. New-device branch carries the MFA handles; the trusted-device
    branch (unconfirmed in the HAR) would carry a ready `session` and skip MFA."""

    login_token: str | None
    verification_token: str | None
    session: Session | None = None      # trusted-device shortcut (guarded, unconfirmed)

    @property
    def needs_mfa(self) -> bool:
        return self.session is None


def _session_from_data(data: dict) -> Session:
    access = data.get("access") or {}
    refresh = data.get("refresh") or {}
    user = data.get("user") or {}
    return Session(
        access_token=access.get("token", ""),
        access_expires=access.get("expired_at"),
        refresh_token=refresh.get("token"),
        refresh_expires=refresh.get("expired_at"),
        username=user.get("username"),
        user_id=str(user.get("id")) if user.get("id") is not None else None,
    )


class AuthClient:
    """Async auth client over the exodus login/MFA endpoints. Transport is injectable
    (tests pass an `httpx.AsyncClient` backed by `httpx.MockTransport`); prod uses a
    real client. Use as an async context manager to own the client lifecycle."""

    def __init__(
        self,
        *,
        base_url: str = config.EXODUS_BASE_URL,
        client: httpx.AsyncClient | None = None,
        timeout: float = config.HTTP_TIMEOUT_SECONDS,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=timeout)

    # -- transport ----------------------------------------------------------------

    def _url(self, path: str) -> str:
        return f"{self._base_url}/{path.lstrip('/')}"

    async def _post(self, path: str, body: dict) -> dict:
        """POST JSON; return the response's `data` object. Maps errors per the DAL
        taxonomy. NEVER logs `body` (it carries password/OTP/recaptcha/tokens)."""
        try:
            resp = await self._client.post(self._url(path), json=body)
        except httpx.TransportError as exc:  # connect/read/timeout — retryable class
            log_net_error(log, method="POST", path=path, error_class=type(exc).__name__,
                          outcome="raised-to-caller", retryable=True)
            raise TransportError(f"POST {path}: network failure: {exc!r}") from exc

        if resp.status_code >= 500 or resp.status_code == 429:
            log_net_error(log, method="POST", path=path, status=resp.status_code,
                          outcome="raised-to-caller", retryable=True)
            raise TransportError(f"POST {path}: server status {resp.status_code}")
        if resp.status_code >= 400:
            # bad creds / failed OTP / invalid token → fail loud, do not retry.
            # Persist the server's own reason (netlog's auth-4xx carve-out) so a 400
            # is diagnosable from logs/net.log without re-running the login.
            server_msg = _msg(resp)
            log_net_error(log, method="POST", path=path, status=resp.status_code,
                          outcome="fail-loud", retryable=False, server_message=server_msg)
            raise AuthError(f"POST {path}: rejected ({resp.status_code}): {server_msg}")
        payload = resp.json()
        data = payload.get("data") if isinstance(payload, dict) else None
        if data is None:
            raise AuthError(f"POST {path}: no `data` in response")
        return data

    # -- 1 · username/password ----------------------------------------------------

    async def login_username(
        self,
        user: str,
        password: str,
        *,
        recaptcha_token: str = "",
        player_id: str = "",
    ) -> LoginStart:
        """Step 1. Returns the new-device MFA handles, or (trusted-device, unconfirmed)
        a ready session. `recaptcha_token` is carried through verbatim — this client
        does not mint one (§4.1 probe decides whether a real token is required)."""
        data = await self._post(
            config.AUTH_LOGIN_USERNAME_PATH,
            {
                "user": user,
                "password": password,
                "recaptcha_token": recaptcha_token,
                "recaptcha_version": config.AUTH_RECAPTCHA_VERSION,
                "player_id": player_id,
            },
        )
        # trusted-device shortcut (HAR-unconfirmed): guard for a direct session.
        if data.get("access"):
            return LoginStart(None, None, session=_session_from_data(data))
        mf = ((data.get("new_device") or {}).get("multi_factor")) or {}
        login_token = mf.get("login_token")
        verification_token = mf.get("verification_token")
        if not login_token or not verification_token:
            raise AuthError("login/v6/username: no MFA handles in response")
        log.info("login step 1 ok — MFA required (new device)")
        return LoginStart(login_token, verification_token)

    # -- 2 · challenge start ------------------------------------------------------

    async def challenge_start(self, verification_token: str) -> Challenge:
        data = await self._post(
            config.AUTH_CHALLENGE_START_PATH,
            {"verification_token": verification_token},
        )
        return _challenge_from_data(data)

    # -- 3 · otp send -------------------------------------------------------------

    async def otp_send(self, verification_token: str, channel: str) -> OtpSend:
        data = await self._post(
            config.AUTH_CHALLENGE_OTP_SEND_PATH,
            {"verification_token": verification_token, "channel": channel},
        )
        return OtpSend(
            channel=data.get("channel", channel),
            target=data.get("target", ""),
            next_attempt_in=int(data.get("next_attempt_in", 0) or 0),
        )

    # -- 4 · otp verify (caller LOOPS until finished) -----------------------------

    async def otp_verify(self, verification_token: str, otp: str) -> Challenge:
        """Verify one OTP. Returns the next challenge — which may be ANOTHER
        `CHALLENGE_OTP` on a new channel. Caller repeats send→verify until
        `Challenge.is_finished`."""
        data = await self._post(
            config.AUTH_CHALLENGE_OTP_VERIFY_PATH,
            {"verification_token": verification_token, "otp": otp},
        )
        return _challenge_from_data(data)

    # -- 5 · new-device verify (only after CHALLENGE_FINISH) ----------------------

    async def new_device_verify(self, login_token: str) -> Session:
        data = await self._post(
            config.AUTH_NEW_DEVICE_VERIFY_PATH,
            {"multi_factor": {"login_token": login_token}},
        )
        session = _session_from_data(data)
        if not session.access_token:
            raise AuthError("new-device/verify: no access token in response")
        log.info("login complete — session established for %s", session.username or "?")
        return session

    # -- refresh (route unconfirmed) ----------------------------------------------

    async def refresh(self, refresh_token: str) -> Session:  # noqa: ARG002
        """NOT wired: the refresh route/shape was not exercised in the HAR capture
        (§4.1 open item). Fails loud rather than guess an endpoint. Pin
        `config.AUTH_REFRESH_PATH` from a real capture, then implement."""
        if not config.AUTH_REFRESH_PATH:
            raise AuthError(
                "token refresh route not confirmed (DATA_SOURCES §4.1) — "
                "re-login required. Capture a refresh exchange before wiring this."
            )
        raise NotImplementedError("refresh route pinned but not yet implemented")

    # -- lifecycle ----------------------------------------------------------------

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> AuthClient:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.aclose()


def _challenge_from_data(data: dict) -> Challenge:
    otp = (data.get("supporting_data") or {}).get("otp") or {}
    channels = [
        OtpChannel(channel=c.get("channel", ""), target=c.get("target", ""))
        for c in (otp.get("channels") or [])
    ]
    return Challenge(
        next_challenge=data.get("next_challenge", ""),
        channels=channels,
        default_channel=otp.get("default_channel"),
    )


def _msg(resp: httpx.Response) -> str:
    """Best-effort server message for an AuthError — a status/message field, never a
    secret (request bodies are never echoed here)."""
    try:
        body = resp.json()
    except (ValueError, httpx.DecodingError):
        return "<no message>"
    if isinstance(body, dict):
        return str(body.get("message") or body.get("error") or "<no message>")
    return "<no message>"
