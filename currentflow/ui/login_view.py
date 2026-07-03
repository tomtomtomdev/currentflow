"""Login flow view-model (slice 11) — a pure state machine over the `dal.auth`
client and the Keychain store.

Kept deliberately free of Streamlit so it is unit-testable (the plan: "login
view-model (pure, Streamlit runtime not exercised)"). `ui/app.py` drives this
controller and renders `LoginView`; this module holds the transitions and the only
copy of the observation that credentials/OTP live transiently in memory (§9.1): the
username/password/OTP/recaptcha are passed into methods and never stored on the
controller, never returned in `LoginView`, never logged. Only the final access +
refresh tokens are persisted, via the store.

State machine (matches the §4.1 flow):
    CREDENTIALS --submit_credentials--> OTP  (or FINISH on trusted-device)
    OTP --verify_otp (loops on CHALLENGE_OTP)--> FINISH
    BEARER --submit_bearer (live-ping verify, design State C fallback)--> FINISH
    any AuthError -> stays on the current step with `error` set, session UNTOUCHED
    sign_out -> CREDENTIALS (store cleared)

The OTP code is **sent immediately** on entering each OTP round (no operator "send"
step): `submit_credentials` and `verify_otp` fire the send for the challenge's default
channel before handing back an OTP view. When the server asks for a second factor
(email → WhatsApp), the next round auto-sends on the new channel, and the returned view
reports the new `otp_target`/`default_channel` so the UI can render a fresh, empty code
field for it. `send_otp` remains for an explicit operator-driven resend.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Awaitable, Callable

from currentflow.dal.auth import AuthClient, OtpChannel
from currentflow.dal.errors import AuthError, ExodusError
from currentflow.dal.session import (
    KeychainTokenStore,
    session_status,
    store_auth_session,
)

CREDENTIALS = "CREDENTIALS"
OTP = "OTP"
BEARER = "BEARER"
FINISH = "FINISH"


@dataclass(frozen=True)
class LoginView:
    """What the UI renders. Carries NO secret — only the step, masked OTP channels/
    targets, an optional error, and (once signed in) the username."""

    state: str
    channels: list[OtpChannel] = field(default_factory=list)
    default_channel: str | None = None
    otp_target: str | None = None
    otp_next_attempt_in: int = 0
    error: str | None = None
    username: str | None = None


class LoginController:
    """Drives the login flow. `verification_token` / `login_token` are transient MFA
    handles held only for the life of one login attempt — never rendered or logged."""

    def __init__(self, auth: AuthClient, store: KeychainTokenStore | None = None) -> None:
        self._auth = auth
        self._store = store or KeychainTokenStore()
        self._verification_token: str | None = None
        self._login_token: str | None = None
        # The channel/target the current OTP was sent to — held so a wrong-code retry
        # can keep showing "code sent to …" instead of dropping the context.
        self._otp_channel: str | None = None
        self._otp_target: str | None = None

    # -- step 1: credentials ------------------------------------------------------

    async def submit_credentials(
        self,
        user: str,
        password: str,
        *,
        player_id: str | None = None,
    ) -> LoginView:
        # No reCAPTCHA token: content is not validated server-side (§4.1), only
        # presence, which AuthClient satisfies with a fixed placeholder. `player_id`
        # is the stable device-trust anchor from the store (generated once) — a
        # previously-verified device returns a session directly (skips OTP).
        pid = player_id or self._store.player_id()
        try:
            start = await self._auth.login_username(user, password, player_id=pid)
        except AuthError as exc:
            return LoginView(CREDENTIALS, error=str(exc))
        except ExodusError as exc:
            return LoginView(CREDENTIALS, error=f"connection error: {exc}")

        if not start.needs_mfa and start.session is not None:
            store_auth_session(self._store, start.session)  # trusted-device: direct session
            return LoginView(FINISH, username=start.session.username)

        self._login_token = start.login_token
        self._verification_token = start.verification_token
        try:
            challenge = await self._auth.challenge_start(self._verification_token or "")
        except ExodusError as exc:
            return LoginView(CREDENTIALS, error=str(exc))
        return await self._enter_otp(challenge)

    # -- step 2/3: send an OTP ----------------------------------------------------

    async def _enter_otp(self, challenge) -> LoginView:
        """Transition into an OTP round, sending the code immediately (no operator
        button) to the challenge's default channel — the server dictates which factor
        is due (email first, then WhatsApp), so we honour `default_channel`. The full
        channel list is preserved on the view for context; `default_channel`/`otp_target`
        report the channel the code actually went to (drives the UI's per-round field)."""
        channel = challenge.default_channel or (
            challenge.channels[0].channel if challenge.channels else None
        )
        if not channel:
            return LoginView(
                OTP,
                channels=challenge.channels,
                default_channel=challenge.default_channel,
                error="server offered no OTP channel",
            )
        try:
            sent = await self._auth.otp_send(self._verification_token or "", channel)
        except ExodusError as exc:
            return LoginView(
                OTP,
                channels=challenge.channels,
                default_channel=challenge.default_channel,
                error=str(exc),
            )
        self._otp_channel, self._otp_target = sent.channel, sent.target
        return LoginView(
            OTP,
            channels=challenge.channels or [OtpChannel(sent.channel, sent.target)],
            default_channel=sent.channel,
            otp_target=sent.target,
            otp_next_attempt_in=sent.next_attempt_in,
        )

    async def send_otp(self, channel: str) -> LoginView:
        """Explicit operator-driven resend of the OTP (the automatic first send happens
        on entering the round). Kept for a resend affordance / cooldown recovery."""
        if not self._verification_token:
            return LoginView(CREDENTIALS, error="session expired — sign in again")
        try:
            sent = await self._auth.otp_send(self._verification_token, channel)
        except ExodusError as exc:
            return LoginView(OTP, error=str(exc))
        self._otp_channel, self._otp_target = sent.channel, sent.target
        return LoginView(
            OTP,
            channels=[OtpChannel(sent.channel, sent.target)],
            default_channel=sent.channel,
            otp_target=sent.target,
            otp_next_attempt_in=sent.next_attempt_in,
        )

    # -- step 4/5: verify (loops) then new-device verify --------------------------

    async def verify_otp(self, otp: str) -> LoginView:
        if not self._verification_token:
            return LoginView(CREDENTIALS, error="session expired — sign in again")
        try:
            challenge = await self._auth.otp_verify(self._verification_token, otp)
        except AuthError as exc:
            # wrong code — stay on OTP so the operator can retry; store nothing. Keep
            # the current channel/target so the "code sent to …" context and field key
            # survive the retry (same channel → field is preserved, not cleared).
            return self._otp_error_view(str(exc))
        except ExodusError as exc:
            return self._otp_error_view(str(exc))

        if not challenge.is_finished:
            # another OTP round (a new channel, e.g. email → WhatsApp): auto-send the
            # next code. The returned view's new otp_target drives a fresh, empty field.
            return await self._enter_otp(challenge)

        try:
            session = await self._auth.new_device_verify(self._login_token or "")
        except ExodusError as exc:
            return LoginView(OTP, error=str(exc))
        store_auth_session(self._store, session)
        self._verification_token = self._login_token = None  # drop handles
        self._otp_channel = self._otp_target = None
        return LoginView(FINISH, username=session.username)

    def _otp_error_view(self, error: str) -> LoginView:
        """An OTP-step error view that keeps the current channel/target so the UI still
        shows where the code went (and holds the field key stable across the retry)."""
        channels = [OtpChannel(self._otp_channel, self._otp_target)] if self._otp_channel else []
        return LoginView(
            OTP,
            channels=channels,
            default_channel=self._otp_channel,
            otp_target=self._otp_target,
            error=error,
        )

    # -- sign out -----------------------------------------------------------------

    def sign_out(self) -> LoginView:
        self._store.clear()
        self._verification_token = self._login_token = None
        self._otp_channel = self._otp_target = None
        return LoginView(CREDENTIALS)


async def submit_bearer(
    token: str,
    *,
    ping: Callable[[str], Awaitable[None]],
    store: KeychainTokenStore | None = None,
) -> LoginView:
    """The Bearer-paste fallback (design State C, §9.1). Strips an optional
    `Bearer ` prefix, verifies the candidate with a live `ping` BEFORE anything is
    written — a rejected token is never stored — and persists it only on success.
    The raw token is held only for this attempt, never rendered back or logged."""
    store = store or KeychainTokenStore()
    raw = (token or "").strip()
    if raw.lower().startswith("bearer "):
        raw = raw[len("bearer "):].strip()
    if not raw:
        return LoginView(BEARER, error="paste a session Bearer token")
    try:
        await ping(raw)
    except AuthError as exc:
        return LoginView(BEARER, error=f"rejected by the live ping — token not stored ({exc})")
    except ExodusError as exc:
        return LoginView(BEARER, error=f"connection error — token not stored ({exc})")
    store.set(raw)
    return LoginView(FINISH, username=None)


def initial_view(store: KeychainTokenStore | None = None) -> LoginView:
    """The view to render on load (no network): FINISH if a valid session is already
    in the Keychain, else the CREDENTIALS form."""
    st = session_status(store)
    if st["has_token"]:
        return LoginView(FINISH, username=st.get("username"))
    return LoginView(CREDENTIALS)
