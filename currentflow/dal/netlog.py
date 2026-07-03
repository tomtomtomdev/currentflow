"""Centralized network-error logging for the DAL transport seams (slice 12).

One formatter so every seam (`HttpxTransport`, `AuthClient._post`,
`ExodusClient._request`) records failures identically and redaction is guaranteed in
exactly one place. A `net-error` line carries only:

    method + path + (status=NNN | err=<ExceptionClassName>) + outcome

It NEVER receives a request/response body, token, password, OTP, recaptcha, or an
exception *message* — callers pass the exception **class name** (`type(exc).__name__`),
never the exception or its `repr`, which can echo a URL with query params. This keeps
the DAL's existing "paths and coarse outcomes only" posture (see `dal/auth.py`).

ONE narrow carve-out: `server_message`. On a fail-loud auth **4xx**, the server's own
rejection *reason* — the response `message`/`error` field already extracted by
`dal/auth._msg` — is the single datum that disambiguates the failure (recaptcha
enforcement vs bad creds vs a missing `player_id`), so a 400 can be diagnosed from
`logs/net.log` alone without re-running the login. It is a *server response reason*,
NOT an exception message and NOT a request body: `_msg` reads only the response's
`message`/`error` field, so a password/OTP/recaptcha/token cannot reach it. Still
sanitized here (the single redaction point): collapsed to one line and length-capped
so the greppable one-line-per-error invariant holds. Callers other than the auth 4xx
seam must NOT pass it.

Level policy: WARNING for retryable/transient failures (network blips, 5xx, 429, an
in-progress retry); ERROR for fail-loud/terminal ones (401 after refresh, unexpected
status, retries exhausted).
"""

from __future__ import annotations

import logging

_MSG_MAX = 200  # cap the server reason so one net-error stays one greppable line


def _one_line(text: str) -> str:
    """Collapse whitespace/newlines and length-cap a server reason so it can't break
    the single-line net-error invariant."""
    flattened = " ".join(text.split())
    return flattened[:_MSG_MAX] + "…" if len(flattened) > _MSG_MAX else flattened


def log_net_error(
    logger: logging.Logger,
    *,
    method: str,
    path: str,
    outcome: str,
    retryable: bool,
    status: int | None = None,
    error_class: str | None = None,
    server_message: str | None = None,
) -> None:
    """Emit one `net-error` line. Pass EITHER `status` (HTTP status mapped) OR
    `error_class` (the exception's class name for a network-level failure) — never a
    body, token, or exception message. `retryable` drives the level (WARNING vs ERROR).

    `server_message` is the auth-4xx-only carve-out (see module docstring): the server's
    sanitized rejection reason, appended as `msg="…"`. Omit it everywhere else.
    """
    detail = f"status={status}" if status is not None else f"err={error_class}"
    if server_message:
        detail += f' msg="{_one_line(server_message)}"'
    level = logging.WARNING if retryable else logging.ERROR
    logger.log(level, "net-error %s %s %s outcome=%s", method, path, detail, outcome)
