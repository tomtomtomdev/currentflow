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

import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
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


@dataclass
class KeychainTokenStore:
    """macOS Keychain generic-password store for the exodus Bearer.

    `get()` returns the stored token or None (missing ≠ empty — a caller that
    needs it must fail loud, never proceed with a blank Authorization header).
    """

    service: str = config.KEYCHAIN_SERVICE
    account: str = config.KEYCHAIN_ACCOUNT
    runner: Runner = _default_runner

    def get(self) -> str | None:
        proc = self.runner(
            [
                "security",
                "find-generic-password",
                "-s",
                self.service,
                "-a",
                self.account,
                "-w",  # print only the password (the token) to stdout
            ]
        )
        if proc.returncode != 0:
            return None  # not found (44) or any error → treat as absent, never blank
        token = (proc.stdout or "").strip()
        return token or None

    def set(self, token: str) -> None:
        token = _strip_bearer(token)
        if not token:
            raise ValueError("refusing to store an empty Bearer token")
        # -U updates in place if the item already exists (idempotent re-capture).
        proc = self.runner(
            [
                "security",
                "add-generic-password",
                "-s",
                self.service,
                "-a",
                self.account,
                "-w",
                token,
                "-U",
            ]
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"Keychain store failed (rc={proc.returncode}): {proc.stderr.strip()}"
            )

    def clear(self) -> None:
        """Delete the stored token. A missing item is not an error (idempotent)."""
        self.runner(
            [
                "security",
                "delete-generic-password",
                "-s",
                self.service,
                "-a",
                self.account,
            ]
        )
