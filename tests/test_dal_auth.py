"""Slice 11 — in-app credential + MFA login.

Exercises the verified `login/v6` + `mfa/verification/v1` wire contract (DATA_SOURCES
§4.1) with an injected `httpx.MockTransport` (no live network, mirroring the slice-10
transport tests) and a per-account fake `security` CLI. Covers: the full flow, the
multi-round OTP loop, error mapping, the token store's access+refresh session, the
fail-loud session-refresh seam, the pure login view-model transitions, and the
guarantee that secrets never reach the logs.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

import httpx
import pytest

from currentflow import config
from currentflow.dal.auth import AuthClient
from currentflow.dal.errors import AuthError, TransportError
from currentflow.dal.session import (
    build_live_client,
    build_session_refresh,
    session_status,
    store_auth_session,
)
from currentflow.dal.token_store import KeychainTokenStore, SessionData
from currentflow.ui import login_view as lv


# --- per-account fake `security` CLI --------------------------------------------


@dataclass
class _Proc:
    returncode: int
    stdout: str = ""
    stderr: str = ""


def fake_keychain():
    """In-memory `security` stand-in that honors the `-a <account>` slot (unlike the
    slice-10 single-slot fake — slice 11 uses two accounts)."""
    state: dict[str, str] = {}

    def runner(argv):
        cmd = argv[1]
        account = argv[argv.index("-a") + 1]
        if cmd == "find-generic-password":
            val = state.get(account)
            return _Proc(0, stdout=val + "\n") if val is not None else _Proc(44)
        if cmd == "add-generic-password":
            state[account] = argv[argv.index("-w") + 1]
            return _Proc(0)
        if cmd == "delete-generic-password":
            existed = state.pop(account, None) is not None
            return _Proc(0) if existed else _Proc(44)
        return _Proc(1, stderr="unknown")

    return runner, state


def _store():
    runner, state = fake_keychain()
    return KeychainTokenStore(runner=runner), state


def _mock_client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# --- §4.1 recorded response shapes (field-shape only) ----------------------------

_USERNAME_RESP = {
    "data": {"new_device": {"multi_factor": {"login_token": "L-TOK", "verification_token": "V-TOK"}}}
}
_START_RESP = {
    "data": {
        "next_challenge": config.CHALLENGE_OTP,
        "supporting_data": {
            "otp": {
                "channels": [
                    {"channel": "CHANNEL_EMAIL", "target": "tom****@gmail.com"},
                    {"channel": "CHANNEL_WHATSAPP", "target": "+62****789"},
                ],
                "default_channel": "CHANNEL_EMAIL",
            }
        },
    }
}
_SEND_RESP = {"data": {"channel": "CHANNEL_EMAIL", "target": "tom****@gmail.com", "next_attempt_in": 60}}
_VERIFY_AGAIN = {
    "data": {
        "next_challenge": config.CHALLENGE_OTP,
        "supporting_data": {"otp": {"channels": [{"channel": "CHANNEL_WHATSAPP", "target": "+62****789"}]}},
    }
}
_VERIFY_FINISH = {"data": {"next_challenge": config.CHALLENGE_FINISH}}
_NEW_DEVICE_RESP = {
    "data": {
        "access": {"token": "ACCESS-JWT", "expired_at": "2026-07-04T09:00:00+07:00"},
        "refresh": {"token": "REFRESH-JWT", "expired_at": "2026-07-10T09:00:00+07:00"},
        "user": {"id": 42, "username": "tommy", "email": "t@x.io"},
    }
}
# The real trusted-device (previously-verified player_id) shape — tokens nested under
# `login.token_data`, user under `login.user` (confirmed 2026-07-03 by live probe).
_TRUSTED_LOGIN_RESP = {
    "data": {
        "login": {
            "user": {"id": 42, "username": "tommy", "email": "t@x.io"},
            "token_data": {
                "access": {"token": "ACCESS-JWT", "expired_at": "2026-07-04T08:39:44Z"},
                "refresh": {"token": "REFRESH-JWT", "expired_at": "2026-07-10T08:39:44Z"},
            },
            "support": {"id": "zY-Az-x"},
        }
    }
}


def _flow_handler(rounds: int = 1):
    """A MockTransport handler for the whole flow. `rounds` = how many OTP verify
    rounds before CHALLENGE_FINISH (§4.1: the loop can span channels)."""
    seen = {"verify": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path.lstrip("/")
        if path == config.AUTH_LOGIN_USERNAME_PATH:
            return httpx.Response(200, json=_USERNAME_RESP)
        if path == config.AUTH_CHALLENGE_START_PATH:
            return httpx.Response(200, json=_START_RESP)
        if path == config.AUTH_CHALLENGE_OTP_SEND_PATH:
            return httpx.Response(200, json=_SEND_RESP)
        if path == config.AUTH_CHALLENGE_OTP_VERIFY_PATH:
            seen["verify"] += 1
            done = seen["verify"] >= rounds
            return httpx.Response(200, json=_VERIFY_FINISH if done else _VERIFY_AGAIN)
        if path == config.AUTH_NEW_DEVICE_VERIFY_PATH:
            return httpx.Response(200, json=_NEW_DEVICE_RESP)
        return httpx.Response(404)

    return handler


# --- auth client: happy path -----------------------------------------------------


async def test_login_username_returns_mfa_handles():
    auth = AuthClient(client=_mock_client(_flow_handler()))
    start = await auth.login_username("tommy", "pw", recaptcha_token="")
    assert start.needs_mfa
    assert start.login_token == "L-TOK" and start.verification_token == "V-TOK"
    await auth.aclose()


async def test_challenge_start_lists_channels():
    auth = AuthClient(client=_mock_client(_flow_handler()))
    ch = await auth.challenge_start("V-TOK")
    assert not ch.is_finished
    assert [c.channel for c in ch.channels] == ["CHANNEL_EMAIL", "CHANNEL_WHATSAPP"]
    assert ch.default_channel == "CHANNEL_EMAIL"
    await auth.aclose()


async def test_otp_verify_loops_then_finishes_and_new_device_verify():
    # Two OTP rounds (email → whatsapp) before FINISH, per the captured flow.
    auth = AuthClient(client=_mock_client(_flow_handler(rounds=2)))
    await auth.otp_send("V-TOK", "CHANNEL_EMAIL")
    c1 = await auth.otp_verify("V-TOK", "111111")
    assert not c1.is_finished  # server asks for a second channel
    assert c1.channels[0].channel == "CHANNEL_WHATSAPP"
    c2 = await auth.otp_verify("V-TOK", "222222")
    assert c2.is_finished
    session = await auth.new_device_verify("L-TOK")
    assert session.access_token == "ACCESS-JWT"
    assert session.refresh_token == "REFRESH-JWT"
    assert session.username == "tommy"
    await auth.aclose()


async def test_trusted_device_returns_direct_session_nested_shape():
    """Trusted-device (stable player_id) branch: session nested under
    `data.login.token_data` — no MFA. This is the shape a repeat login actually gets."""
    def handler(request):
        return httpx.Response(200, json=_TRUSTED_LOGIN_RESP)

    auth = AuthClient(client=_mock_client(handler))
    start = await auth.login_username("tommy", "pw", player_id="STABLE-PID")
    assert not start.needs_mfa and start.session is not None
    assert start.session.access_token == "ACCESS-JWT"
    assert start.session.refresh_token == "REFRESH-JWT"
    assert start.session.access_expires == "2026-07-04T08:39:44Z"
    assert start.session.username == "tommy"
    await auth.aclose()


async def test_login_username_defaults_to_placeholder_recaptcha():
    """No token is minted: the body carries the fixed placeholder, and the server
    (which validates presence only, §4.1) accepts it. player_id is passed through."""
    seen = {}

    def handler(request):
        seen.update(json.loads(request.content))
        return httpx.Response(200, json=_USERNAME_RESP)

    auth = AuthClient(client=_mock_client(handler))
    await auth.login_username("tommy", "pw", player_id="PID-123")
    assert seen["recaptcha_token"] == config.AUTH_RECAPTCHA_PLACEHOLDER
    assert seen["recaptcha_token"]  # non-empty (clears the presence check)
    assert seen["player_id"] == "PID-123"
    await auth.aclose()


# --- auth client: error mapping --------------------------------------------------


async def test_bad_credentials_raise_auth_error():
    def handler(request):
        return httpx.Response(401, json={"message": "invalid credentials"})

    auth = AuthClient(client=_mock_client(handler))
    with pytest.raises(AuthError):
        await auth.login_username("tommy", "wrong")
    await auth.aclose()


async def test_failed_otp_raises_auth_error():
    def handler(request):
        return httpx.Response(400, json={"message": "wrong otp"})

    auth = AuthClient(client=_mock_client(handler))
    with pytest.raises(AuthError):
        await auth.otp_verify("V-TOK", "000000")
    await auth.aclose()


async def test_server_5xx_maps_to_transport_error():
    def handler(request):
        return httpx.Response(503)

    auth = AuthClient(client=_mock_client(handler))
    with pytest.raises(TransportError):
        await auth.challenge_start("V-TOK")
    await auth.aclose()


async def test_network_failure_maps_to_transport_error():
    def handler(request):
        raise httpx.ConnectError("boom", request=request)

    auth = AuthClient(client=_mock_client(handler))
    with pytest.raises(TransportError):
        await auth.login_username("tommy", "pw")
    await auth.aclose()


async def test_refresh_fails_loud_until_route_confirmed():
    auth = AuthClient(client=_mock_client(_flow_handler()))
    assert config.AUTH_REFRESH_PATH is None  # §4.1 open item — not guessed
    with pytest.raises(AuthError):
        await auth.refresh("REFRESH-JWT")
    await auth.aclose()


# --- secrets never logged --------------------------------------------------------


async def test_secrets_never_appear_in_logs(caplog):
    caplog.set_level(logging.DEBUG)
    auth = AuthClient(client=_mock_client(_flow_handler(rounds=1)))
    await auth.login_username("tommy", "s3cr3t-pw", recaptcha_token="RECAP-XYZ", player_id="PID")
    await auth.otp_send("V-TOK", "CHANNEL_EMAIL")
    await auth.otp_verify("V-TOK", "424242")
    await auth.new_device_verify("L-TOK")
    await auth.aclose()
    blob = caplog.text
    for secret in ("s3cr3t-pw", "RECAP-XYZ", "424242", "ACCESS-JWT", "REFRESH-JWT", "V-TOK", "L-TOK"):
        assert secret not in blob


# --- token store: access+refresh session -----------------------------------------


def test_session_roundtrip_and_access_prefers_session():
    store, _ = _store()
    assert store.get_session() is None
    store.set_session(SessionData("ACC", "e1", "REF", "e2", "tommy"))
    got = store.get_session()
    assert got.access_token == "ACC" and got.refresh_token == "REF" and got.username == "tommy"
    assert store.get_access() == "ACC" and store.get_refresh() == "REF"
    # transport reads access_token(): the session wins over any pasted Bearer.
    store.set("PASTED")
    assert store.access_token() == "ACC"


def test_access_token_falls_back_to_pasted_bearer():
    store, _ = _store()
    store.set("PASTED")
    assert store.get_session() is None
    assert store.access_token() == "PASTED"


def test_set_session_refuses_empty_access():
    store, _ = _store()
    with pytest.raises(ValueError):
        store.set_session(SessionData(""))


def test_clear_removes_both_session_and_bearer():
    store, _ = _store()
    store.set("PASTED")
    store.set_session(SessionData("ACC"))
    store.clear()
    assert store.get_session() is None and store.get() is None
    assert store.access_token() is None


def test_session_status_reports_login_source_masked():
    store, _ = _store()
    store.set_session(SessionData("abcdefghijkl", "2026-07-04T09:00", "REF", None, "tommy"))
    st = session_status(store)
    assert st["has_token"] and st["source"] == "login" and st["username"] == "tommy"
    assert st["preview"] == "abcd…ijkl" and "efgh" not in st["preview"]  # middle never leaked


# --- session factory: 401 → refresh fails loud (route unconfirmed) ---------------


async def test_build_live_client_uses_session_access_token():
    store, _ = _store()
    store.set_session(SessionData("SESSTOK", None, "REF"))

    def handler(request):
        assert request.headers["Authorization"] == "Bearer SESSTOK"
        return httpx.Response(200, json={"data": []})

    from datetime import date

    client, transport = build_live_client(store=store, client=_mock_client(handler))
    rows = await client.ohlcv_foreign("BBCA", date(2026, 6, 1), date(2026, 6, 5))
    assert rows == []
    await transport.aclose()


async def test_401_then_refresh_fails_loud():
    store, _ = _store()
    store.set_session(SessionData("EXPIRED", None, "REF-TOK"))

    def handler(request):
        return httpx.Response(401)

    from datetime import date

    refresher = build_session_refresh(store)
    client, transport = build_live_client(store=store, refresher=refresher, client=_mock_client(handler))
    with pytest.raises(AuthError):  # refresh route unconfirmed → fail loud, never stale
        await client.ohlcv_foreign("BBCA", date(2026, 6, 1), date(2026, 6, 5))
    await transport.aclose()


# --- login view-model (pure; Streamlit runtime not exercised) --------------------


async def test_view_credentials_to_otp_to_finish():
    store, _ = _store()
    ctl = lv.LoginController(AuthClient(client=_mock_client(_flow_handler(rounds=1))), store)
    v1 = await ctl.submit_credentials("tommy", "pw")
    assert v1.state == lv.OTP and [c.channel for c in v1.channels] == ["CHANNEL_EMAIL", "CHANNEL_WHATSAPP"]
    await ctl.send_otp("CHANNEL_EMAIL")
    v2 = await ctl.verify_otp("123456")
    assert v2.state == lv.FINISH and v2.username == "tommy"
    assert store.get_access() == "ACCESS-JWT"  # session persisted only on FINISH


async def test_view_otp_loop_over_two_channels():
    store, _ = _store()
    ctl = lv.LoginController(AuthClient(client=_mock_client(_flow_handler(rounds=2))), store)
    await ctl.submit_credentials("tommy", "pw")
    await ctl.send_otp("CHANNEL_EMAIL")
    mid = await ctl.verify_otp("111111")
    assert mid.state == lv.OTP and mid.error is None  # second round requested
    assert store.get_session() is None  # nothing stored mid-loop
    await ctl.send_otp("CHANNEL_WHATSAPP")
    done = await ctl.verify_otp("222222")
    assert done.state == lv.FINISH and store.get_access() == "ACCESS-JWT"


async def test_view_rejected_login_stores_nothing_and_surfaces_error():
    store, _ = _store()

    def handler(request):
        return httpx.Response(401, json={"message": "invalid credentials"})

    ctl = lv.LoginController(AuthClient(client=_mock_client(handler)), store)
    v = await ctl.submit_credentials("tommy", "wrong")
    assert v.state == lv.CREDENTIALS and v.error
    assert store.get_session() is None  # store untouched on rejection


async def test_view_wrong_otp_stays_on_otp_for_retry():
    store, _ = _store()

    def handler(request):
        path = request.url.path.lstrip("/")
        if path == config.AUTH_LOGIN_USERNAME_PATH:
            return httpx.Response(200, json=_USERNAME_RESP)
        if path == config.AUTH_CHALLENGE_START_PATH:
            return httpx.Response(200, json=_START_RESP)
        if path == config.AUTH_CHALLENGE_OTP_VERIFY_PATH:
            return httpx.Response(400, json={"message": "wrong otp"})
        return httpx.Response(404)

    ctl = lv.LoginController(AuthClient(client=_mock_client(handler)), store)
    await ctl.submit_credentials("tommy", "pw")
    v = await ctl.verify_otp("000000")
    assert v.state == lv.OTP and v.error  # retryable, not kicked to credentials
    assert store.get_session() is None


async def test_view_submit_needs_no_recaptcha_and_uses_stable_player_id():
    """No reCAPTCHA token is required (§4.1 — presence-only, satisfied by the
    placeholder). submit_credentials posts the store's stable player_id and reaches
    the server (here: trusted-device → direct session)."""
    store, _ = _store()
    pid = store.player_id()
    seen = {}

    def handler(request):
        seen.update(json.loads(request.content))
        return httpx.Response(200, json=_TRUSTED_LOGIN_RESP)

    ctl = lv.LoginController(AuthClient(client=_mock_client(handler)), store)
    v = await ctl.submit_credentials("tommy", "pw")
    assert v.state == lv.FINISH
    assert seen["player_id"] == pid  # stable device id from the store
    assert seen["recaptcha_token"] == config.AUTH_RECAPTCHA_PLACEHOLDER


def test_player_id_generated_once_persisted_and_survives_clear():
    """The device player_id is a UUID minted on first read, then stable forever —
    identical across reads and across a session `clear()` (sign-out keeps the device
    trusted). `clear_player_id()` forgets it so the next login re-triggers MFA."""
    import uuid as _uuid

    store, state = _store()
    first = store.player_id()
    uuid_obj = _uuid.UUID(first)  # well-formed UUID
    assert uuid_obj.version == 4
    assert store.player_id() == first                      # stable across reads
    store.set_session(SessionData("ACC"))
    store.clear()                                          # sign-out
    assert store.player_id() == first                      # device identity survives
    store.clear_player_id()
    assert store.player_id() != first                      # forgotten → new device


def test_view_sign_out_clears_and_returns_to_credentials():
    store, _ = _store()
    store.set_session(SessionData("ACC"))
    ctl = lv.LoginController(AuthClient(), store)
    v = ctl.sign_out()
    assert v.state == lv.CREDENTIALS and store.get_session() is None


def test_initial_view_is_finish_when_session_present():
    store, _ = _store()
    assert lv.initial_view(store).state == lv.CREDENTIALS
    store.set_session(SessionData("ACC", username="tommy"))
    v = lv.initial_view(store)
    assert v.state == lv.FINISH and v.username == "tommy"
