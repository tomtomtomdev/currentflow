"""Centralized network-error logging for the DAL transport seams (slice 12).

One formatter so every seam (`HttpxTransport`, `AuthClient._post`,
`ExodusClient._request`) records failures identically and redaction is guaranteed in
exactly one place. A `net-error` line carries only:

    method + path + (status=NNN | err=<ExceptionClassName>) + outcome

It NEVER receives a request/response body, token, password, OTP, recaptcha, or an
exception *message* — callers pass the exception **class name** (`type(exc).__name__`),
never the exception or its `repr`, which can echo a URL with query params. This keeps
the DAL's existing "paths and coarse outcomes only" posture (see `dal/auth.py`).

Level policy: WARNING for retryable/transient failures (network blips, 5xx, 429, an
in-progress retry); ERROR for fail-loud/terminal ones (401 after refresh, unexpected
status, retries exhausted).
"""

from __future__ import annotations

import logging


def log_net_error(
    logger: logging.Logger,
    *,
    method: str,
    path: str,
    outcome: str,
    retryable: bool,
    status: int | None = None,
    error_class: str | None = None,
) -> None:
    """Emit one `net-error` line. Pass EITHER `status` (HTTP status mapped) OR
    `error_class` (the exception's class name for a network-level failure) — never a
    body, token, or exception message. `retryable` drives the level (WARNING vs ERROR).
    """
    detail = f"status={status}" if status is not None else f"err={error_class}"
    level = logging.WARNING if retryable else logging.ERROR
    logger.log(level, "net-error %s %s %s outcome=%s", method, path, detail, outcome)
