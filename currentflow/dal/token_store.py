"""Keychain-backed Bearer token store (slice 10).

The operator captures the Bearer from their OWN authenticated Stockbit session
(own session, at own risk — §15) and pastes it in via `dal.login`. It lives in the
macOS **Keychain**, never in a repo file, never in plaintext on disk, never
republished (CLAUDE.md local-first, nothing leaves the machine).

Implemented over the macOS `security` CLI via subprocess — zero new dependency,
keeping the core stdlib (the same posture as slice 9's numpy-free ML). The
subprocess `runner` is injected so the store is fully testable without touching a
real Keychain.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Callable, Protocol

from currentflow import config


class CompletedProc(Protocol):
    returncode: int
    stdout: str
    stderr: str


Runner = Callable[[Sequence[str]], CompletedProc]


def _default_runner(argv: Sequence[str]) -> CompletedProc:
    return subprocess.run(  # noqa: S603 — fixed `security` argv, no shell
        list(argv), capture_output=True, text=True, check=False
    )


def _strip_bearer(token: str) -> str:
    """Accept a raw token or a full `Bearer <token>` header; store the raw token."""
    token = token.strip()
    if token.lower().startswith("bearer "):
        token = token[len("bearer ") :].strip()
    return token


@dataclass(frozen=True)
class SessionData:
    """The credential-login session (slice 11): access + refresh tokens with their
    ISO-8601 expiries and the account username. Only these tokens ever touch the
    Keychain — credentials, OTP, and recaptcha stay transient in memory (§9.1)."""

    access_token: str
    access_expires: str | None = None
    refresh_token: str | None = None
    refresh_expires: str | None = None
    username: str | None = None


@dataclass
class KeychainTokenStore:
    """macOS Keychain generic-password store for the exodus session.

    Two accounts under one service:
      * `account` (KEYCHAIN_ACCOUNT) — a raw pasted Bearer (slice-10 fallback).
      * `session_account` (KEYCHAIN_SESSION_ACCOUNT) — the credential-login session
        (access+refresh+expiry) as one JSON blob (slice 11).

    `get()` returns the raw pasted Bearer or None. `access_token()` is what the
    transport reads: the session's access token if present, else the pasted Bearer.
    Missing ≠ empty — a caller that needs a token must fail loud, never send a blank
    Authorization header.
    """

    service: str = config.KEYCHAIN_SERVICE
    account: str = config.KEYCHAIN_ACCOUNT
    session_account: str = config.KEYCHAIN_SESSION_ACCOUNT
    runner: Runner = _default_runner

    # -- low-level, account-parametric --------------------------------------------

    def _read(self, account: str) -> str | None:
        proc = self.runner(
            ["security", "find-generic-password", "-s", self.service, "-a", account, "-w"]
        )
        if proc.returncode != 0:
            return None  # not found (44) or any error → absent, never blank
        value = (proc.stdout or "").strip()
        return value or None

    def _write(self, account: str, value: str) -> None:
        # -U updates in place if the item already exists (idempotent re-capture).
        proc = self.runner(
            [
                "security", "add-generic-password",
                "-s", self.service, "-a", account, "-w", value, "-U",
            ]
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"Keychain store failed (rc={proc.returncode}): {proc.stderr.strip()}"
            )

    def _delete(self, account: str) -> None:
        self.runner(
            ["security", "delete-generic-password", "-s", self.service, "-a", account]
        )

    # -- pasted Bearer (slice-10 fallback) ----------------------------------------

    def get(self) -> str | None:
        return self._read(self.account)

    def set(self, token: str) -> None:
        token = _strip_bearer(token)
        if not token:
            raise ValueError("refusing to store an empty Bearer token")
        self._write(self.account, token)

    # -- credential-login session (slice 11) --------------------------------------

    def set_session(self, session: SessionData) -> None:
        if not session.access_token or not session.access_token.strip():
            raise ValueError("refusing to store a session with no access token")
        self._write(self.session_account, json.dumps(asdict(session)))

    def get_session(self) -> SessionData | None:
        blob = self._read(self.session_account)
        if not blob:
            return None
        try:
            data = json.loads(blob)
        except (ValueError, TypeError):
            return None  # corrupt blob → treat as absent (fail loud downstream)
        if not isinstance(data, dict) or not data.get("access_token"):
            return None
        return SessionData(
            access_token=data["access_token"],
            access_expires=data.get("access_expires"),
            refresh_token=data.get("refresh_token"),
            refresh_expires=data.get("refresh_expires"),
            username=data.get("username"),
        )

    def get_access(self) -> str | None:
        session = self.get_session()
        return session.access_token if session else None

    def get_refresh(self) -> str | None:
        session = self.get_session()
        return session.refresh_token if session else None

    def access_token(self) -> str | None:
        """The token the transport carries: the login session's access token if
        present, else the pasted Bearer. Missing → None (never a blank header)."""
        return self.get_access() or self.get()

    def clear(self) -> None:
        """Delete both the pasted Bearer and the login session. Missing items are
        not an error (idempotent)."""
        self._delete(self.account)
        self._delete(self.session_account)
