"""Operator-assisted reCAPTCHA v3 token capture (slice 11).

`login/v6/username` enforces reCAPTCHA v3 (confirmed 2026-07-03 — an empty token is
rejected 400 "Permintaan tidak valid"; DATA_SOURCES §4.1). v3 is invisible and the
token can't be minted in pure Python — it's produced client-side by Google's
`grecaptcha.execute(siteKey, {action})` on the stockbit.com origin.

Rather than drive a headless browser (a heavy dep against the stdlib-core posture,
§4.1 fork), this module renders a one-line **browser-console snippet** the operator
runs in DevTools on an open stockbit.com tab; it mints a fresh token and copies it to
the clipboard to paste back into the login prompt. Pure string builders — no network,
no secrets — so the login views (CLI + Streamlit) share one capture path.

The token is **single-use and short-lived** (~2 min): mint it immediately before
submitting, don't reuse the one from a HAR.
"""

from __future__ import annotations

from currentflow import config

# Shown when a login is attempted with no token — the two views share this wording.
REQUIRED_MESSAGE = (
    "reCAPTCHA token required — the server enforces reCAPTCHA v3 (an empty token is "
    "rejected). Mint a fresh one with the console snippet, then paste it."
)


def mint_snippet(
    *,
    site_key: str = config.AUTH_RECAPTCHA_SITE_KEY,
    action: str = config.AUTH_RECAPTCHA_ACTION,
) -> str:
    """The DevTools-console one-liner that mints a fresh v3 token and copies it to the
    clipboard. Run it on an open https://stockbit.com tab (grecaptcha is loaded there);
    `copy(...)` is the console's built-in clipboard helper."""
    return (
        f"grecaptcha.ready(()=>grecaptcha.execute('{site_key}',{{action:'{action}'}})"
        ".then(t=>{copy(t);console.log('reCAPTCHA token copied ('+t.length+' chars)');}))"
    )


def capture_instructions(
    *,
    site_key: str = config.AUTH_RECAPTCHA_SITE_KEY,
    action: str = config.AUTH_RECAPTCHA_ACTION,
) -> str:
    """Multi-line operator guidance (numbered steps + the snippet) for a terminal."""
    return (
        "reCAPTCHA v3 is enforced — mint a fresh token to paste:\n"
        "  1. Open https://stockbit.com in a browser (logged out is fine).\n"
        "  2. Open DevTools → Console (Cmd-Opt-J / F12).\n"
        "  3. Paste this, run it, then paste the copied token below:\n\n"
        f"     {mint_snippet(site_key=site_key, action=action)}\n\n"
        "  (Token is single-use and expires in ~2 min — mint it right before signing in.)"
    )
