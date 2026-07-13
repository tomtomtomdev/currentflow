"""End-to-end render/routing smoke for the v2 restructure (Signal Pipeline home).

Drives the real Streamlit app headless via AppTest against the checked-in DuckDB.
Auth is mocked (the session gate is orthogonal to the analytics — no signal or
RULE A/B behavior depends on login). Asserts the app renders with no exception and
that the pipeline ⇄ evidence routing works: row click opens the contextual evidence
view with four tabs; the back button returns to the pipeline.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parents[1] / "currentflow" / "ui" / "app.py")
_FAKE_SESSION = {"has_token": True, "username": "operator",
                 "preview": "····a1f9", "source": "keychain"}


def _authed_app(timeout: float = 90) -> AppTest:
    at = AppTest.from_file(APP, default_timeout=timeout)
    at.run()
    assert not at.exception, at.exception
    return at


def test_login_gate_renders_without_session():
    at = AppTest.from_file(APP, default_timeout=30)
    at.run()
    assert not at.exception, at.exception


@patch("currentflow.dal.session.session_status", return_value=_FAKE_SESSION)
def test_pipeline_is_the_home_view(_session):
    at = _authed_app()
    md = " ".join(m.value for m in at.markdown)
    assert "Signal Pipeline" in md
    assert "UNIVERSE GATE" in md  # the four locked stage headers render
    # no left module nav rail in v2 → no sidebar radio
    assert not at.sidebar.radio


@patch("currentflow.dal.session.session_status", return_value=_FAKE_SESSION)
def test_row_click_opens_evidence_then_back_returns(_session):
    at = _authed_app()
    opens = [b for b in at.button if b.key and b.key.startswith("cfpipeopen-")]
    if not opens:
        pytest.skip("no candidates ingested in the checked-in store")
    ticker = opens[0].key.removeprefix("cfpipeopen-")

    opens[0].click()
    at.run()
    assert not at.exception, at.exception
    md = " ".join(m.value for m in at.markdown)
    assert f"Why {ticker}" in md  # contextual evidence header
    tabs = {b.key for b in at.button if b.key and b.key.startswith("cftab-")}
    assert tabs == {"cftab-broker", "cftab-foreign", "cftab-accum", "cftab-replay"}

    # switch to Money Replay — no exception
    next(b for b in at.button if b.key == "cftab-replay").click()
    at.run()
    assert not at.exception, at.exception

    # back to the pipeline
    next(b for b in at.button if b.key == "cfbackbtn").click()
    at.run()
    assert not at.exception, at.exception
    md2 = " ".join(m.value for m in at.markdown)
    assert "UNIVERSE GATE" in md2 and "Why " not in md2
