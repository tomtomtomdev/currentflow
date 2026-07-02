"""RULE B (LD-9) acceptance test: no unvalidated module displays a number, and the
per-module validation state drives the observation↔claim switch.

The SMS/Rank module computes an internal score and an ARMED state, but pre-validation
it renders only components + a state word — the composite number is withheld (`•••`).
Flipping the module to VALIDATED (as the paper-trade engine will in slice 8) reveals it.
"""

from __future__ import annotations

from datetime import date as Date
from datetime import datetime

from builders import phase_b_bars, strong_phase_c_bars, two_buyer_rows

from currentflow.signals import engine
from currentflow.signals.engine import EngineState
from currentflow.ui import sms_view
from currentflow.ui.sms_view import WITHHELD
from currentflow.validation.state import ModuleState, may_display_number, module_state

TS = datetime(2026, 7, 1, 9, 0)
BDAYS = [Date(2026, 6, 24), Date(2026, 6, 25), Date(2026, 6, 26)]
VALIDATED = {"sms": ModuleState.VALIDATED}


def _armed(store):
    store.write_daily_bars(strong_phase_c_bars("STRONG"))
    store.write_broker_net(two_buyer_rows("STRONG", BDAYS))
    return engine.evaluate(store, "STRONG", TS, track="B")


# --- the gate defaults closed --------------------------------------------------------


def test_gated_modules_start_observation_only():
    for m in ("sms", "ai_ranking", "daily_top"):
        assert module_state(m) is ModuleState.OBSERVATION_ONLY
        assert may_display_number(m) is False


# --- no number pre-validation, even when ARMED --------------------------------------


def test_armed_watchlist_hides_the_score(store):
    res = _armed(store)
    assert res.state is EngineState.ARMED
    assert res.sms.internal_score >= 70            # computed internally …

    view = sms_view.summary(res)                    # … but never shown pre-validation
    assert view["score"] == WITHHELD
    assert view["armed"] is True                    # the STATE drives the watchlist
    assert view["validation_state"] == "OBSERVATION_ONLY"
    # the internal number must appear nowhere in the composite display
    assert str(round(res.sms.internal_score)) != view["score"]
    assert sms_view.score_display(res.sms) == WITHHELD


def test_components_still_ship_as_observation(store):
    res = _armed(store)
    view = sms_view.summary(res)
    keys = {c["component"] for c in view["components"]}
    assert keys == {"divergence", "broker_concentration", "foreign_flow", "rvol", "block_trade", "phase_bonus"}
    # components carry raw observations, not a composite score
    assert all("observation" in c for c in view["components"])


def test_no_buy_sell_verb_in_any_state_label(store):
    for bars, sym in ((strong_phase_c_bars("A"), "A"), (phase_b_bars("B"), "B")):
        store.write_daily_bars(bars)
        store.write_broker_net(two_buyer_rows(sym, BDAYS))
        label = sms_view.state_label(engine.evaluate(store, sym, TS, track="B"))
        low = label.lower()
        for banned in ("buy ", "sell", "target", "probability", "%"):
            assert banned not in low, f"state label leaked '{banned}': {label!r}"


# --- validation flips the switch (observation → claim) ------------------------------


def test_validated_module_may_show_the_number(store):
    res = _armed(store)
    assert may_display_number("sms", VALIDATED) is True
    shown = sms_view.score_display(res.sms, registry=VALIDATED)
    assert shown == f"{res.sms.internal_score:.0f}"
    assert shown != WITHHELD
    assert sms_view.summary(res, registry=VALIDATED)["validation_state"] == "VALIDATED"
