"""Time Machine — rewind the read path to a past decision moment (ui/timemachine.py).

Two layers:
  * the pure logic — the decision-moment convention, the fail-loud regime/future clamps,
    and the named non-rewindable caveats;
  * the wired terminal, driven headless via AppTest — the rewound moment actually reaches
    the store reads (a name ingested only after the rewound day is absent, and its bars
    are invisible), the banner announces it, and nothing is written at the rewound date.

RULE A/B are untouched by construction (a `decision_ts` is a read parameter, not a gate or
a presentation state) — `test_rule_b.py` / `test_phase.py` remain the authority on those.
"""

from __future__ import annotations

from datetime import date as Date
from datetime import datetime, timedelta
import re
from pathlib import Path
from unittest.mock import patch

import pytest

from streamlit.testing.v1 import AppTest

from currentflow import config
from currentflow.store.db import Store
from currentflow.ui import timemachine as tm
from tests.builders import Chart

APP = str(Path(__file__).resolve().parents[1] / "currentflow" / "ui" / "app.py")
_FAKE_SESSION = {"has_token": True, "username": "operator",
                 "preview": "····a1f9", "source": "keychain"}


# --- the pure logic ---------------------------------------------------------------


def test_decision_moment_is_the_days_pre_open():
    """A rewind to D reads at D 09:15 WIB — the "acting on D" convention shared with
    `validation.runner._decision_ts` and `universe.pit`, NOT replay's D+1 framing."""
    day = Date(2026, 3, 2)
    assert tm.decision_ts_for(day) == datetime.combine(day, config.REPLAY_DECISION_TIME)

    from currentflow.validation.runner import _decision_ts as runner_ts

    assert tm.decision_ts_for(day) == runner_ts(day)


def test_resolve_is_live_now_when_no_day_is_set():
    now = datetime(2026, 8, 8, 14, 30)
    assert tm.resolve(None, now=now) == now
    assert tm.resolve(Date(2026, 3, 2), now=now) == tm.decision_ts_for(Date(2026, 3, 2))


def test_rewound_moment_hides_data_published_after_it():
    """The whole point: at D's pre-open the store returns D-1's bar and nothing later.
    `as_of` is derived from the trading day (dal/timing.ohlcv_as_of), so a backfilled
    history rewinds honestly rather than vanishing wholesale."""
    chart = Chart(symbol="AAAA", start=Date(2026, 3, 2))   # Mon → five weekday bars
    for _ in range(5):
        chart.add(100, 101, 99, 100)
    store = Store(":memory:")
    store.write_daily_bars(chart.bars)
    days = [b.date for b in chart.bars]

    rewound = tm.decision_ts_for(days[2])          # 2026-03-04 09:15
    visible = store.read_daily_bars("AAAA", rewound)
    assert [b.date for b in visible] == days[:2]   # 03-02, 03-03 — never 03-04 onward

    live = store.read_daily_bars("AAAA", datetime(2026, 8, 8, 12, 0))
    assert [b.date for b in live] == days          # unrewound sees all five


def test_regime_floor_is_refused_not_clamped():
    """REGIME.md §1: reaching before the regime start is a bug, not a bigger sample —
    so it fails loud instead of silently widening to the boundary."""
    assert tm.EARLIEST_DAY == min(config.regime_start("A"), config.regime_start("B"))
    why = tm.rejection(tm.EARLIEST_DAY - timedelta(days=1), today=Date(2026, 8, 8))
    assert why is not None and "regime start" in why
    assert tm.rejection(tm.EARLIEST_DAY, today=Date(2026, 8, 8)) is None


def test_future_day_is_refused():
    today = Date(2026, 8, 8)
    assert tm.rejection(today + timedelta(days=1), today=today) is not None
    assert tm.rejection(today, today=today) is None   # this morning's pre-open is legal


def test_caveats_name_what_does_not_rewind():
    """Non-rewindable surfaces are named, never faked — the `pit.unchecked_legs` posture."""
    from currentflow.universe.pit import UNCHECKED_GATE_LEGS

    gapped = tm.caveats(Date(2026, 3, 2), roster_covers=False)
    covered = tm.caveats(Date(2026, 3, 2), roster_covers=True)
    joined = " ".join(gapped)

    assert any(leg in joined for leg in UNCHECKED_GATE_LEGS)   # the sink-less §3 legs
    assert "Fast-Mode" in joined and "Survivorship" in joined
    assert "Track B" in " ".join(gapped)          # no roster period → ADV leg alone
    assert "index_roster_pit" in " ".join(covered)


def test_weekend_rewind_is_flagged_not_treated_as_missing():
    saturday = Date(2026, 3, 7)
    assert tm.is_weekend(saturday)
    assert any("weekend" in c for c in tm.caveats(saturday, roster_covers=True))
    assert not any("weekend" in c for c in tm.caveats(Date(2026, 3, 4), roster_covers=True))


def test_label_never_confuses_the_two_modes():
    assert tm.label(None) == "LIVE"
    assert "TIME MACHINE" in tm.label(Date(2026, 3, 2))


# --- the wired terminal -----------------------------------------------------------


def _authed_app(timeout: float = 120) -> AppTest:
    at = AppTest.from_file(APP, default_timeout=timeout)
    at.run()
    assert not at.exception, at.exception
    return at


def _committed_day(at: AppTest) -> Date | None:
    """The Time Machine day the app currently holds (SafeSessionState has no `.get`)."""
    return at.session_state["cf_asof_day"] if "cf_asof_day" in at.session_state else None


@patch("currentflow.dal.session.session_status", return_value=_FAKE_SESSION)
def test_terminal_starts_live_with_the_control_and_no_banner(_session):
    at = _authed_app()
    assert "cf_asof_pick" in {d.key for d in at.date_input}   # the control renders
    md = " ".join(m.value for m in at.markdown)
    assert "TIME MACHINE —" not in md                         # no banner while live
    assert "Mode: **LIVE**" in " ".join(c.value for c in at.caption)


@patch("currentflow.dal.session.session_status", return_value=_FAKE_SESSION)
def test_rewinding_banners_the_moment_and_names_its_caveats(_session):
    at = _authed_app()
    day = Date(2026, 3, 2)
    at.session_state["cf_asof_day"] = day
    at.run()
    assert not at.exception, at.exception

    md = " ".join(m.value for m in at.markdown)
    assert f"TIME MACHINE — reading as of {day} pre-open" in md
    assert "2026-03-02 09:15" in md                # the exact moment, spelled out
    assert "REWOUND to" in md                      # top bar can't be read as live
    assert "Survivorship" in md                    # caveats are on screen, not implied


@patch("currentflow.dal.session.session_status", return_value=_FAKE_SESSION)
def test_picking_a_valid_past_day_commits_it(_session):
    """The picker's on_change path (`_apply_asof`) is what actually rewinds the terminal."""
    at = _authed_app()
    day = max(tm.EARLIEST_DAY, Date(2026, 3, 2))
    at.date_input(key="cf_asof_pick").set_value(day).run()
    assert not at.exception, at.exception
    assert _committed_day(at) == day
    assert f"TIME MACHINE — reading as of {day} pre-open" in " ".join(
        m.value for m in at.markdown
    )


@patch("currentflow.dal.session.session_status", return_value=_FAKE_SESSION)
def test_pre_regime_day_is_neither_accepted_nor_clamped(_session):
    """REGIME.md §1 is enforced at the widget bound (`min_value` = the regime floor): a
    pre-regime pick is not taken at all. What must never happen is the silent clamp — the
    date quietly becoming the floor and the terminal reading as if that was the ask."""
    at = _authed_app()
    at.date_input(key="cf_asof_pick").set_value(tm.EARLIEST_DAY - timedelta(days=30)).run()
    assert not at.exception, at.exception
    assert _committed_day(at) is None                                  # nothing committed
    assert at.date_input(key="cf_asof_pick").value != tm.EARLIEST_DAY  # nor clamped to it
    assert "TIME MACHINE —" not in " ".join(m.value for m in at.markdown)


@patch("currentflow.dal.session.session_status", return_value=_FAKE_SESSION)
def test_a_refused_day_is_reported_on_screen(_session):
    """When `_apply_asof` does refuse a day it records the reason and the next render says it
    out loud — the operator is never left to infer a refusal from a date that didn't move."""
    at = _authed_app()
    at.session_state["cf_asof_error"] = "sentinel-refusal-reason"
    at.run()
    assert not at.exception, at.exception
    assert any("sentinel-refusal-reason" in e.value for e in at.error)


@patch("currentflow.dal.session.session_status", return_value=_FAKE_SESSION)
def test_rewound_asof_stamp_moves_back_to_the_last_visible_day(_session):
    """End-to-end proof that the rewound moment reaches the store: the top-bar as-of stamp
    is the newest day visible AT that moment, never today's. (A raw `max(date)` would leak
    the future straight into the chrome.)"""
    at = _authed_app()
    live_stamp = _asof_stamp(at)
    if live_stamp is None:
        pytest.skip("no data ingested in the checked-in store")

    day = Date(2026, 6, 1)
    at.session_state["cf_asof_day"] = day
    at.run()
    assert not at.exception, at.exception
    rewound_stamp = _asof_stamp(at)
    assert rewound_stamp is not None and rewound_stamp < day   # only D-1 and earlier
    assert rewound_stamp < live_stamp


@patch("currentflow.dal.session.session_status", return_value=_FAKE_SESSION)
def test_rewound_terminal_lists_only_names_visible_then(_session):
    """The candidate list is drawn from names with a row already visible at the rewound
    moment — never a name first ingested later, listed and then rendered empty."""
    at = _authed_app()
    live_syms = set(_app_symbols(at))
    if not live_syms:
        pytest.skip("no data ingested in the checked-in store")

    at.session_state["cf_asof_day"] = Date(2026, 6, 1)
    at.run()
    assert not at.exception, at.exception
    assert set(_app_symbols(at)) <= live_syms          # never invents a name

    at.session_state["cf_asof_day"] = tm.EARLIEST_DAY  # before anything was ingested
    at.run()
    assert not at.exception, at.exception
    assert not _app_symbols(at)
    # "nothing published yet" is a different fact from "no data" — it must say so
    assert any("had been published before" in w.value for w in at.warning)


def _app_symbols(at: AppTest) -> list[str]:
    """The names the app currently offers as pipeline row selectors."""
    return [b.key.removeprefix("cfpipeopen-") for b in at.button
            if b.key and b.key.startswith("cfpipeopen-")]


def _asof_stamp(at: AppTest) -> Date | None:
    """The top bar's as-of stamp, parsed back out of the shell HTML."""
    m = re.search(
        r'as-of <span class="cf-mono">(\d{4}-\d{2}-\d{2})</span>',
        " ".join(x.value for x in at.markdown),
    )
    return Date.fromisoformat(m.group(1)) if m else None


@patch("currentflow.dal.session.session_status", return_value=_FAKE_SESSION)
def test_arming_fast_mode_is_disabled_while_rewound(_session):
    """A rewound view must not drive live state: arming is a WRITE and the paper book is
    a live record, so the toggle is disabled rather than silently acting at today's date."""
    at = _authed_app()
    at.session_state["cf_asof_day"] = Date(2026, 6, 1)  # inside the ingested range
    at.run()
    assert not at.exception, at.exception
    toggles = {t.key: t for t in at.toggle}
    if "cf_fast_toggle" not in toggles:
        pytest.skip("no data ingested in the checked-in store → panel not reached")
    assert toggles["cf_fast_toggle"].disabled is True


@patch("currentflow.dal.session.session_status", return_value=_FAKE_SESSION)
def test_live_button_returns_to_now(_session):
    at = _authed_app()
    at.session_state["cf_asof_day"] = Date(2026, 3, 2)
    at.run()
    at.button(key="cf_asof_live").click().run()
    assert not at.exception, at.exception
    assert _committed_day(at) is None
    assert "TIME MACHINE —" not in " ".join(m.value for m in at.markdown)
