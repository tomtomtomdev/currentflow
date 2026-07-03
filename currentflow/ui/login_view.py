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
    OTP --send_otp/verify_otp (loops on CHALLENGE_OTP)--> FINISH
    any AuthError -> stays on the current step with `error` set, session UNTOUCHED
    sign_out -> CREDENTIALS (store cleared)
"""

from __future__ import annotations

from dataclasses import dataclass, field

from currentflow.dal import recaptcha
from currentflow.dal.auth import AuthClient, OtpChannel
from currentflow.dal.errors import AuthError, ExodusError
from currentflow.dal.session import (
    KeychainTokenStore,
    session_status,
    store_auth_session,
)

CREDENTIALS = "CREDENTIALS"
OTP = "OTP"
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

    # -- step 1: credentials ------------------------------------------------------

    async def submit_credentials(
        self,
        user: str,
        password: str,
        *,
        recaptcha_token: str = "",
        player_id: str = "",
    ) -> LoginView:
        # reCAPTCHA v3 is server-enforced (§4.1) — refuse an empty token here rather
        # than fire a request that comes back 400. Stay on CREDENTIALS with guidance.
        if not recaptcha_token.strip():
            return LoginView(CREDENTIALS, error=recaptcha.REQUIRED_MESSAGE)
        try:
            start = await self._auth.login_username(
                user, password, recaptcha_token=recaptcha_token, player_id=player_id
            )
        except AuthError as exc:
            return LoginView(CREDENTIALS, error=str(exc))
        except ExodusError as exc:
            return LoginView(CREDENTIALS, error=f"connection error: {exc}")

        if not start.needs_mfa and start.session is not None:
            store_auth_session(self._store, start.session)  # trusted-device (unconfirmed)
            return LoginView(FINISH, username=start.session.username)

        self._login_token = start.login_token
        self._verification_token = start.verification_token
        try:
            challenge = await self._auth.challenge_start(self._verification_token or "")
        except ExodusError as exc:
            return LoginView(CREDENTIALS, error=str(exc))
        return LoginView(
            OTP,
            channels=challenge.channels,
            default_channel=challenge.default_channel,
        )

    # -- step 2/3: send an OTP ----------------------------------------------------

    async def send_otp(self, channel: str) -> LoginView:
        if not self._verification_token:
            return LoginView(CREDENTIALS, error="session expired — sign in again")
        try:
            sent = await self._auth.otp_send(self._verification_token, channel)
        except ExodusError as exc:
            return LoginView(OTP, error=str(exc))
        return LoginView(
            OTP,
            channels=[OtpChannel(sent.channel, sent.target)],
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
            # wrong code — stay on OTP so the operator can retry; store nothing.
            return LoginView(OTP, error=str(exc))
        except ExodusError as exc:
            return LoginView(OTP, error=str(exc))

        if not challenge.is_finished:
            # another OTP round (a new channel). Remain on OTP with the new channels.
            return LoginView(
                OTP,
                channels=challenge.channels,
                default_channel=challenge.default_channel,
            )

        try:
            session = await self._auth.new_device_verify(self._login_token or "")
        except ExodusError as exc:
            return LoginView(OTP, error=str(exc))
        store_auth_session(self._store, session)
        self._verification_token = self._login_token = None  # drop handles
        return LoginView(FINISH, username=session.username)

    # -- sign out -----------------------------------------------------------------

    def sign_out(self) -> LoginView:
        self._store.clear()
        self._verification_token = self._login_token = None
        return LoginView(CREDENTIALS)


def initial_view(store: KeychainTokenStore | None = None) -> LoginView:
    """The view to render on load (no network): FINISH if a valid session is already
    in the Keychain, else the CREDENTIALS form."""
    st = session_status(store)
    if st["has_token"]:
        return LoginView(FINISH, username=st.get("username"))
    return LoginView(CREDENTIALS)
