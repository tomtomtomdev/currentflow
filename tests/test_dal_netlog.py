"""Slice 12 — centralized DAL network-error logging.

Every network failure at the DAL transport seams must emit exactly one greppable
`net-error` line carrying method + path + status|err-class + coarse outcome — and
NEVER a body, token, password, OTP, recaptcha, or an exception message (which can
echo a URL with query params). Level policy: WARNING for retryable/transient,
ERROR for fail-loud/exhausted.

No network, no Keychain: httpx is driven by `MockTransport`; the ExodusClient path
uses the scripted transport + recording sleep from conftest.
"""

from __future__ import annotations

import logging

import httpx
import pytest

from currentflow.dal.auth import AuthClient
from currentflow.dal.client import ExodusClient
from currentflow.dal.errors import AuthError, TransportError
from currentflow.dal.netlog import log_net_error
from currentflow.dal.transport import HttpxTransport
from tests.conftest import recording_sleep, scripted_transport


def _mock_client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _net_lines(caplog):
    return [r for r in caplog.records if r.getMessage().startswith("net-error")]


# --- helper contract -------------------------------------------------------------


def test_helper_status_line_is_warning_when_retryable(caplog):
    caplog.set_level(logging.DEBUG)
    log = logging.getLogger("t.net.a")
    log_net_error(log, method="POST", path="screener/templates", status=503,
                  outcome="retry 2/4", retryable=True)
    (rec,) = _net_lines(caplog)
    msg = rec.getMessage()
    assert rec.levelno == logging.WARNING
    assert "POST" in msg and "screener/templates" in msg
    assert "status=503" in msg and "outcome=retry 2/4" in msg
    assert "err=" not in msg  # status path never also prints an err class


def test_helper_err_line_is_error_when_fail_loud(caplog):
    caplog.set_level(logging.DEBUG)
    log = logging.getLogger("t.net.b")
    log_net_error(log, method="GET", path="marketdetectors/BBCA",
                  error_class="ConnectTimeout", outcome="fail-loud", retryable=False)
    (rec,) = _net_lines(caplog)
    msg = rec.getMessage()
    assert rec.levelno == logging.ERROR
    assert "err=ConnectTimeout" in msg and "outcome=fail-loud" in msg
    assert "status=" not in msg


def test_helper_appends_server_message_when_provided(caplog):
    caplog.set_level(logging.DEBUG)
    log = logging.getLogger("t.net.c")
    log_net_error(log, method="POST", path="login/v6/username", status=400,
                  outcome="fail-loud", retryable=False, server_message="bad creds")
    (rec,) = _net_lines(caplog)
    msg = rec.getMessage()
    assert "status=400" in msg and 'msg="bad creds"' in msg


def test_helper_omits_msg_field_when_no_server_message(caplog):
    caplog.set_level(logging.DEBUG)
    log = logging.getLogger("t.net.d")
    log_net_error(log, method="GET", path="p", error_class="ConnectError",
                  outcome="fail-loud", retryable=False)
    (rec,) = _net_lines(caplog)
    assert "msg=" not in rec.getMessage()  # class-name-only path unchanged


# --- transport seam (HttpxTransport) ---------------------------------------------


async def test_transport_network_error_logs_class_name_not_url(caplog):
    caplog.set_level(logging.DEBUG)
    secret_url = "https://exodus.stockbit.com/marketdetectors/BBCA?apikey=SECRET123"

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(secret_url, request=request)

    t = HttpxTransport(token_provider=lambda: "tok", client=_mock_client(handler))
    with pytest.raises(TransportError):
        await t.get("marketdetectors/BBCA", {})
    await t.aclose()

    (rec,) = _net_lines(caplog)
    assert rec.levelno == logging.WARNING  # network failure is retryable
    assert "err=ConnectError" in rec.getMessage()
    assert "SECRET123" not in caplog.text  # the exception message never reaches logs


async def test_transport_success_logs_no_net_error(caplog):
    caplog.set_level(logging.DEBUG)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": []})

    t = HttpxTransport(token_provider=lambda: "tok", client=_mock_client(handler))
    await t.get("p", {})
    await t.aclose()
    assert _net_lines(caplog) == []


# --- auth seam (AuthClient._post) ------------------------------------------------


async def test_auth_network_error_logged_warning(caplog):
    caplog.set_level(logging.DEBUG)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    auth = AuthClient(client=_mock_client(handler))
    with pytest.raises(TransportError):
        await auth.login_username("tommy", "s3cr3t-pw")
    await auth.aclose()

    (rec,) = _net_lines(caplog)
    assert rec.levelno == logging.WARNING
    assert "err=ReadTimeout" in rec.getMessage()


async def test_auth_5xx_logged_warning(caplog):
    caplog.set_level(logging.DEBUG)
    auth = AuthClient(client=_mock_client(lambda r: httpx.Response(503)))
    with pytest.raises(TransportError):
        await auth.login_username("tommy", "s3cr3t-pw")
    await auth.aclose()
    (rec,) = _net_lines(caplog)
    assert rec.levelno == logging.WARNING and "status=503" in rec.getMessage()


async def test_auth_4xx_reject_logged_error(caplog):
    caplog.set_level(logging.DEBUG)
    auth = AuthClient(client=_mock_client(lambda r: httpx.Response(401, json={"message": "bad creds"})))
    with pytest.raises(AuthError):
        await auth.login_username("tommy", "s3cr3t-pw")
    await auth.aclose()
    (rec,) = _net_lines(caplog)
    assert rec.levelno == logging.ERROR and "status=401" in rec.getMessage()


async def test_auth_4xx_reject_carries_server_message(caplog):
    """The single fail-loud carve-out: an auth 4xx persists the server's own reason
    (`message`/`error` field) so a 400 can be diagnosed from the log alone — recaptcha
    enforcement vs bad creds vs player_id — without re-running the login."""
    caplog.set_level(logging.DEBUG)
    auth = AuthClient(
        client=_mock_client(
            lambda r: httpx.Response(400, json={"message": "recaptcha token required"})
        )
    )
    with pytest.raises(AuthError):
        await auth.login_username("tommy", "s3cr3t-pw")
    await auth.aclose()
    (rec,) = _net_lines(caplog)
    msg = rec.getMessage()
    assert "status=400" in msg
    assert 'msg="recaptcha token required"' in msg


async def test_auth_4xx_server_message_stays_one_greppable_line(caplog):
    """A hostile/multiline server body must not break the single-line net-error
    invariant: newlines collapse and the reason is length-capped."""
    caplog.set_level(logging.DEBUG)
    noisy = "invalid\nrequest\r\n" + "x" * 500
    auth = AuthClient(client=_mock_client(lambda r: httpx.Response(400, json={"error": noisy})))
    with pytest.raises(AuthError):
        await auth.login_username("tommy", "s3cr3t-pw")
    await auth.aclose()
    (rec,) = _net_lines(caplog)
    msg = rec.getMessage()
    assert "\n" not in msg and "\r" not in msg
    assert len(msg) < 400  # capped, not the full 500-char body


async def test_auth_secrets_never_appear_in_netlog(caplog):
    caplog.set_level(logging.DEBUG)
    auth = AuthClient(client=_mock_client(lambda r: httpx.Response(503)))
    with pytest.raises(TransportError):
        await auth.login_username(
            "tommy", "s3cr3t-pw", recaptcha_token="RECAP-XYZ"
        )
    await auth.aclose()
    for secret in ("s3cr3t-pw", "RECAP-XYZ"):
        assert secret not in caplog.text


# --- client seam (ExodusClient._request / _maybe_backoff) ------------------------

from datetime import date  # noqa: E402

D0, D1 = date(2026, 6, 1), date(2026, 6, 5)


async def test_client_401_fail_loud_logged_error(caplog):
    caplog.set_level(logging.DEBUG)
    client = ExodusClient(scripted_transport([401]))
    with pytest.raises(AuthError):
        await client.broker_summary("BBCA", D0)
    (rec,) = _net_lines(caplog)
    assert rec.levelno == logging.ERROR
    assert "status=401" in rec.getMessage() and "outcome=fail-loud" in rec.getMessage()


async def test_client_unexpected_status_logged_error(caplog):
    caplog.set_level(logging.DEBUG)
    client = ExodusClient(scripted_transport([418]))
    with pytest.raises(TransportError):
        await client.broker_summary("BBCA", D0)
    (rec,) = _net_lines(caplog)
    assert rec.levelno == logging.ERROR and "status=418" in rec.getMessage()


async def test_client_retries_then_exhausts_warns_then_errors(caplog):
    caplog.set_level(logging.DEBUG)
    client = ExodusClient(
        scripted_transport([503, 503, 503, 503, 503]),
        sleep=recording_sleep([]),
        max_retries=4,
    )
    with pytest.raises(TransportError):
        await client.ohlcv_foreign("BBCA", D0, D1)
    lines = _net_lines(caplog)
    warns = [r for r in lines if r.levelno == logging.WARNING]
    errs = [r for r in lines if r.levelno == logging.ERROR]
    assert len(warns) == 4  # one per retry
    assert len(errs) == 1  # terminal exhaustion
    assert "outcome=exhausted" in errs[0].getMessage()


async def test_client_success_logs_no_net_error(caplog):
    caplog.set_level(logging.DEBUG)
    client = ExodusClient(scripted_transport([(200, {"data": []})]))
    await client.ohlcv_foreign("BBCA", D0, D1)
    assert _net_lines(caplog) == []
