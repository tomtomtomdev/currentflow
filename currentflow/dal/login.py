"""Operator session management CLI (slice 10 paste; slice 11 credential login) — the
'view' of the live-transport / auth vertical.

    python -m currentflow.dal.login login      # sign in: username + password + OTP → session
    python -m currentflow.dal.login paste       # fallback: paste a Bearer into the Keychain
    python -m currentflow.dal.login status       # is a session present? (masked, no network)
    python -m currentflow.dal.login check         # live ping — confirm the token works
    python -m currentflow.dal.login clear         # remove the stored session/token

`login` establishes the operator's OWN authenticated Stockbit session (own risk, §15)
from the verified `login/v6` + `mfa/verification/v1` contract (DATA_SOURCES §4.1).
Credentials and OTP are held only for the duration of the prompt — never written to
disk, never logged. Only the resulting access+refresh tokens reach the Keychain.
`paste` remains as the out-of-band fallback (and the reCAPTCHA-enforced escape hatch).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import date, timedelta
from getpass import getpass

from currentflow import config
from currentflow.logging_setup import configure_logging
from currentflow.dal.auth import AuthClient
from currentflow.dal.errors import AuthError, ExodusError
from currentflow.dal.session import build_live_client, session_status, store_auth_session
from currentflow.dal.token_store import KeychainTokenStore

# A liquid, always-trading name for the live ping — a short window is enough to
# prove the token authenticates without pulling meaningful data.
_PING_SYMBOL = "BBCA"


def _store_session(session) -> None:
    store_auth_session(KeychainTokenStore(), session)


async def _run_login() -> int:
    user = input("Stockbit username / email: ").strip()
    password = getpass("Password (hidden): ")
    if not user or not password:
        print("username and password required — nothing stored", file=sys.stderr)
        return 1
    # reCAPTCHA v3 enforcement is unconfirmed (§4.1). Empty token = the pure-Python
    # attempt; if the server rejects it, paste an operator-minted token here.
    recaptcha = getpass("reCAPTCHA token (blank to try without): ").strip()

    auth = AuthClient()
    try:
        start = await auth.login_username(user, password, recaptcha_token=recaptcha)
        if not start.needs_mfa and start.session is not None:
            _store_session(start.session)  # trusted-device shortcut (unconfirmed)
            print(f"signed in (trusted device) — {session_status()['preview']}")
            return 0

        vtok = start.verification_token or ""
        challenge = await auth.challenge_start(vtok)
        # Drive send→verify until CHALLENGE_FINISH (the loop can span channels).
        while not challenge.is_finished:
            channel = _pick_channel(challenge)
            sent = await auth.otp_send(vtok, channel)
            code = input(f"OTP sent to {sent.target} via {sent.channel} — enter code: ").strip()
            challenge = await auth.otp_verify(vtok, code)

        session = await auth.new_device_verify(start.login_token or "")
        _store_session(session)
        print(f"signed in — {session_status()['preview']} (user: {session.username})")
        return 0
    except AuthError as exc:
        print(f"login failed: {exc}", file=sys.stderr)
        return 1
    except ExodusError as exc:
        print(f"transport/exodus error: {exc}", file=sys.stderr)
        return 2
    finally:
        await auth.aclose()


def _pick_channel(challenge) -> str:
    if not challenge.channels:
        return challenge.default_channel or config.CHALLENGE_OTP
    if len(challenge.channels) == 1:
        return challenge.channels[0].channel
    print("Choose an OTP channel:")
    for i, ch in enumerate(challenge.channels):
        print(f"  [{i}] {ch.channel} → {ch.target}")
    raw = input(f"channel [0-{len(challenge.channels) - 1}]: ").strip() or "0"
    idx = max(0, min(len(challenge.channels) - 1, int(raw) if raw.isdigit() else 0))
    return challenge.channels[idx].channel


def _cmd_login() -> int:
    return asyncio.run(_run_login())


def _cmd_paste() -> int:
    token = getpass("Paste exodus Bearer (input hidden): ")
    if not token.strip():
        print("no token entered — nothing stored", file=sys.stderr)
        return 1
    KeychainTokenStore().set(token)
    st = session_status()
    print(f"stored in Keychain — {st['preview']} ({st['length']} chars)")
    return 0


def _cmd_status() -> int:
    st = session_status()
    if not st["has_token"]:
        print("no session — run `python -m currentflow.dal.login login`")
        return 1
    who = f", user: {st['username']}" if st.get("username") else ""
    exp = f", access expires {st['access_expires']}" if st.get("access_expires") else ""
    print(f"session present [{st['source']}] — {st['preview']} ({st['length']} chars{who}{exp})")
    return 0


def _cmd_clear() -> int:
    KeychainTokenStore().clear()
    print("session cleared")
    return 0


def _cmd_check() -> int:
    async def _ping() -> int:
        client, transport = build_live_client()
        try:
            to = date.today()
            frm = to - timedelta(days=7)
            rows = await client.ohlcv_foreign(_PING_SYMBOL, frm, to)
            print(f"OK — token authenticates ({_PING_SYMBOL}: {len(rows)} bars)")
            return 0
        except AuthError as exc:
            print(f"AUTH FAILED — re-login: {exc}", file=sys.stderr)
            return 1
        except ExodusError as exc:
            print(f"transport/exodus error: {exc}", file=sys.stderr)
            return 2
        finally:
            await transport.aclose()

    return asyncio.run(_ping())


def main(argv: list[str] | None = None) -> int:
    configure_logging()  # persist dal `net-error` lines to logs/net.log
    parser = argparse.ArgumentParser(prog="currentflow.dal.login")
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("login", "paste", "status", "check", "clear"):
        sub.add_parser(name)
    args = parser.parse_args(argv)
    return {
        "login": _cmd_login,
        "paste": _cmd_paste,
        "status": _cmd_status,
        "check": _cmd_check,
        "clear": _cmd_clear,
    }[args.cmd]()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
