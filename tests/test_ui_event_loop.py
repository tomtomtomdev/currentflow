"""Regression: the login flow must not orphan its httpx client on a closed loop.

`AuthClient` caches one `httpx.AsyncClient` in `st.session_state` across Streamlit
reruns (its connection pool binds to the event loop that first drives it). The old
`_run` used `asyncio.run`, which creates AND closes a fresh loop per call — so the
second submit tried to close pooled connections against a dead loop:

    RuntimeError: Event loop is closed

The fix runs every coroutine on one persistent, still-open session loop. This test
pins that contract: successive `_run` calls share a single loop that stays open.
"""

from __future__ import annotations

import asyncio

from currentflow.ui import app


def test_run_reuses_one_persistent_open_loop(monkeypatch):
    # a throwaway session_state stand-in (Streamlit's is a MutableMapping)
    monkeypatch.setattr(app.st, "session_state", {}, raising=False)

    async def current_loop():
        return asyncio.get_running_loop()

    loop_a = app._run(current_loop())
    loop_b = app._run(current_loop())

    assert loop_a is loop_b, "each _run must reuse the same session loop"
    assert not loop_a.is_closed(), "the session loop must stay open across reruns"
