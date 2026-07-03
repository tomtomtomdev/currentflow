"""Operator session management CLI (slice 10) — the 'view' of the live-transport
vertical.

    python -m currentflow.dal.login paste     # capture the Bearer into the Keychain
    python -m currentflow.dal.login status     # is a token present? (masked, no network)
    python -m currentflow.dal.login check       # live ping — confirm the token works
    python -m currentflow.dal.login clear       # remove the stored token

The Bearer is captured from the operator's OWN authenticated Stockbit session
(own risk, §15). Paste it here; it is stored in the macOS Keychain and never
written to disk in plaintext, never logged, never republished.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import date, timedelta
from getpass import getpass

from currentflow.dal.errors import AuthError, ExodusError
from currentflow.dal.session import build_live_client, session_status
from currentflow.dal.token_store import KeychainTokenStore

# A liquid, always-trading name for the live ping — a short window is enough to
# prove the token authenticates without pulling meaningful data.
_PING_SYMBOL = "BBCA"


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
        print("no token captured — run `python -m currentflow.dal.login paste`")
        return 1
    print(f"token present — {st['preview']} ({st['length']} chars)")
    return 0


def _cmd_clear() -> int:
    KeychainTokenStore().clear()
    print("token cleared")
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
            print(f"AUTH FAILED — re-capture the Bearer: {exc}", file=sys.stderr)
            return 1
        except ExodusError as exc:
            print(f"transport/exodus error: {exc}", file=sys.stderr)
            return 2
        finally:
            await transport.aclose()

    return asyncio.run(_ping())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="currentflow.dal.login")
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("paste", "status", "check", "clear"):
        sub.add_parser(name)
    args = parser.parse_args(argv)
    return {
        "paste": _cmd_paste,
        "status": _cmd_status,
        "check": _cmd_check,
        "clear": _cmd_clear,
    }[args.cmd]()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
