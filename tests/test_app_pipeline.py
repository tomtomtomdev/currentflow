"""End-to-end render/routing smoke for the v2 restructure.

Drives the real Streamlit app headless via AppTest against the checked-in DuckDB.
Auth is mocked (the session gate is orthogonal to the analytics — no signal or
RULE A/B behavior depends on login). Asserts the app renders with no exception and
that the routing works both ways: **Framework Lenses is the landing view**
(`app.DEFAULT_VIEW`) with the Signal Pipeline one `‹ Pipeline` click away, and from the
pipeline a row click opens the contextual evidence view with four tabs, back returns.

The landing view is chrome, not gating: the pipeline still owns every verdict, so the
pipeline-side tests navigate there explicitly (`_pipeline_app`) rather than skip.
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
    """The app as it lands — Framework Lenses (`app.DEFAULT_VIEW`)."""
    at = AppTest.from_file(APP, default_timeout=timeout)
    at.run()
    assert not at.exception, at.exception
    return at


def _pipeline_app(timeout: float = 90) -> AppTest:
    """The app on the Signal Pipeline: land on the lens view, then `‹ Pipeline`. The back
    button renders before the lens view's data check, so this navigates on an empty store
    too (no silent skip of the pipeline assertions)."""
    at = _authed_app(timeout)
    next(b for b in at.button if b.key == "cflensback").click()
    at.run()
    assert not at.exception, at.exception
    return at


def test_login_gate_renders_without_session():
    at = AppTest.from_file(APP, default_timeout=30)
    at.run()
    assert not at.exception, at.exception


@patch("currentflow.dal.session.session_status", return_value=_FAKE_SESSION)
def test_framework_lenses_is_the_landing_view(_session):
    """The terminal opens on the lens surface, not the pipeline grid. It is a full-width
    OBSERVATION read — so the ARMED rail is absent and no stage header is on screen."""
    at = _authed_app()
    md = " ".join(m.value for m in at.markdown)
    assert "Framework Lenses" in md
    assert "UNIVERSE GATE" not in md   # the pipeline's stage headers are not the landing
    assert any(b.key == "cflensback" for b in at.button)   # pipeline is one click away
    # no left module nav rail in v2 → no sidebar radio
    assert not at.sidebar.radio


@patch("currentflow.dal.session.session_status", return_value=_FAKE_SESSION)
def test_pipeline_is_one_click_from_the_landing_view(_session):
    at = _pipeline_app()
    md = " ".join(m.value for m in at.markdown)
    assert "Signal Pipeline" in md
    assert "UNIVERSE GATE" in md  # the four locked stage headers render
    assert not at.sidebar.radio


@patch("currentflow.dal.session.session_status", return_value=_FAKE_SESSION)
def test_fast_mode_panel_and_toggle_render(_session):
    """The LD-11 Fast Mode panel renders on the pipeline home with an arm/disarm toggle,
    defaulted OFF (opt-in), and the app raises no exception (wiring is sound)."""
    at = _pipeline_app()
    toggles = {t.key: t for t in at.toggle}
    if "cf_fast_toggle" not in toggles:
        pytest.skip("no data ingested in the checked-in store → panel not reached")
    tog = toggles["cf_fast_toggle"]
    assert tog.value is False                    # off by default (opt-in — never auto-trades)
    assert "Fast Mode" in tog.label              # the panel's arm control rendered


@patch("currentflow.dal.session.session_status", return_value=_FAKE_SESSION)
def test_row_click_opens_evidence_then_back_returns(_session):
    at = _pipeline_app()
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


@patch("currentflow.dal.session.session_status", return_value=_FAKE_SESSION)
def test_framework_lenses_open_switch_sections_and_return(_session):
    """The five frameworks read apart: the lens view still opens from the pipeline (the
    `▸ Framework Lenses` button, not only the landing default), every section switches
    without exception, and `‹ Pipeline` returns. The pipeline itself is untouched by the
    trip — a lens surface decides nothing (RULE A)."""
    at = _pipeline_app()
    opener = [b for b in at.button if b.key == "cfopenlens"]
    if not opener:
        pytest.skip("no candidates ingested in the checked-in store")

    opener[0].click()
    at.run()
    assert not at.exception, at.exception
    md = " ".join(m.value for m in at.markdown)
    assert "Framework Lenses" in md
    assert "UNIVERSE GATE" not in md  # a dedicated surface, not the pipeline grid

    # the redesigned chrome: tab-card switcher with per-lens tallies, the persistent
    # read-state key, column captions, and the state bands (all five, even at zero)
    assert "cf-lenstab" in md and "cf-lenskey" in md and "cf-lenscols" in md
    assert "no name in this state today" in md

    sections = [b.key for b in at.button if b.key and b.key.startswith("cflens-")]
    assert sections == [
        "cflens-wyckoff", "cflens-wyckoff2", "cflens-vpa",
        "cflens-bandar", "cflens-mf", "cflens-confluence",
    ]
    for key in sections:
        next(b for b in at.button if b.key == key).click()
        at.run()
        assert not at.exception, at.exception
        section_md = " ".join(m.value for m in at.markdown)
        # every row carries the engine's own verdict, and the cross-lens strip is always
        # five fixed slots — never a proportion (RULE A / RULE B, at the app level)
        assert "pipeline:" in section_md
        slots = section_md.count('class="cf-lensslot')   # rendered slots, not the sheet
        assert slots and slots % 5 == 0

    next(b for b in at.button if b.key == "cflensback").click()
    at.run()
    assert not at.exception, at.exception
    assert "UNIVERSE GATE" in " ".join(m.value for m in at.markdown)
