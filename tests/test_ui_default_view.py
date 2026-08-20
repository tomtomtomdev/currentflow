"""Landing view: the terminal opens on Framework Lenses, and the operator can still leave.

`_active_view` is the whole routing rule — pure over the session-state mapping, so the
default can be pinned without a Streamlit session. RULE A/B are untouched by it: a lens
is a read over an already-computed `EngineResult`, so which surface paints first cannot
arm, un-reject or re-gate a name.
"""

from __future__ import annotations

from currentflow.ui import app


def test_first_paint_lands_on_framework_lenses():
    assert app._active_view({}) == "lenses" == app.DEFAULT_VIEW


def test_back_button_leaves_lenses_for_the_pipeline():
    # `_close_lenses` writes an explicit None — present-but-None must NOT re-default
    state = {}
    state["cf_view"] = None          # what _close_lenses/_close_catalog set
    assert app._active_view(state) is None   # None = Signal Pipeline

    state["cf_view"] = "catalog"
    assert app._active_view(state) == "catalog"
