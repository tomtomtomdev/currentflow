"""DAL error taxonomy.

401 → AuthError, raised loud and NEVER retried (CLAUDE.md: "on 401 fail loud, never
emit stale/empty"). Rate-limit / paywall-counter / transient transport errors are
retryable with exponential backoff.
"""

from __future__ import annotations


class ExodusError(Exception):
    """Base for all DAL errors."""


class AuthError(ExodusError):
    """401 Unauthorized — token expired/invalid. Fail loud, do not retry, do not
    emit stale or empty data. Caller must re-capture the Bearer token."""


class PaywallError(ExodusError):
    """Paywall counter exhausted / Pro gate (402/403). Retryable with backoff, but
    ultimately an operational limit — throttle and ingest-once to avoid hitting it."""


class RateLimitError(ExodusError):
    """429 Too Many Requests. Retryable with exponential backoff."""


class TransportError(ExodusError):
    """Network failure or 5xx. Retryable with exponential backoff."""
