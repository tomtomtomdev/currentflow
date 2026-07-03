"""Slice-8 observation↔claim switch across ALL gated modules (spec §13, RULE B).

Extends the slice-4 SMS-only RULE-B test: the SMS, AI Buy/Sell Ranking, and Daily Top
Opportunities modules all withhold their number/rank (`•••`) until the ledger — driven by
the paper-trade engine — promotes them to VALIDATED, at which point the number appears."""

from __future__ import annotations

from datetime import date as Date
from datetime import datetime

from tests.builders import phase_b_bars, strong_phase_c_bars, two_buyer_rows

from currentflow import config
from currentflow.execution.risk import ExitReason
from currentflow.signals import engine
from currentflow.ui import daily_top_view, ranking_view, sms_view
from currentflow.validation.promotion import ValidationLedger
from currentflow.validation.state import WITHHELD, ModuleState, gated_display
from currentflow.validation.trade import PaperTrade

TS = datetime(2026, 7, 1, 9, 0)
BDAYS = [Date(2026, 6, 24), Date(2026, 6, 25), Date(2026, 6, 26)]


def _results(store):
    store.write_daily_bars(strong_phase_c_bars("STRONG"))
    store.write_broker_net(two_buyer_rows("STRONG", BDAYS))
    store.write_daily_bars(phase_b_bars("QUIET"))
    store.write_broker_net(two_buyer_rows("QUIET", BDAYS))
    return [
        engine.evaluate(store, "STRONG", TS, track="B"),
        engine.evaluate(store, "QUIET", TS, track="B"),
    ]


def _trade(move: float, day: int) -> PaperTrade:
    return PaperTrade(
        symbol="X", track="B", tilt_kind="NEUTRAL",
        entry_date=Date(2026, 1, day), exit_date=Date(2026, 1, day + 1), qty=1000,
        entry_price=100.0, exit_price=100.0 + move, entry_fee=500.0, exit_fee=500.0,
        exit_reason=ExitReason.TARGET, stop=95.0, risk_idr=5000.0,
    )


def _validate_all(led: ValidationLedger):
    rec = [_trade(8 + (i % 3) * 4, i) for i in range(1, 13)]
    for m in ("sms", "ai_ranking", "daily_top"):
        led.record_forward_paper(m, trades=rec, months_accrued=config.PAPER_VALIDATION_MONTHS)
    return led.states()


# --- the shared switch ---------------------------------------------------------------


def test_gated_display_withholds_until_validated():
    assert gated_display("sms", 87) == WITHHELD                       # default OBSERVATION_ONLY
    assert gated_display("sms", 87, registry={"sms": ModuleState.VALIDATING}) == WITHHELD
    assert gated_display("sms", 87, registry={"sms": ModuleState.VALIDATED}) == "87"
    assert gated_display("sms", None, registry={"sms": ModuleState.VALIDATED}) == WITHHELD


# --- pre-validation: every gated module hides its number -----------------------------


def test_all_three_modules_hide_numbers_pre_validation(store):
    results = _results(store)

    assert sms_view.score_display(results[0].sms) == WITHHELD

    for row in ranking_view.ranking(results):
        assert row["score"] == WITHHELD and row["position"] == WITHHELD
    assert "not a recommendation" in ranking_view.framing()

    dig = daily_top_view.digest(results)
    assert dig["names"], "an ARMED name should surface as observation"
    for row in dig["names"]:
        assert row["score"] == WITHHELD
        assert row["components"]                # components ship as observation
    assert "not a recommendation" in dig["framing"]


# --- post-validation: the paper-trade engine flips the switch ------------------------


def test_promotion_reveals_numbers_across_modules(store):
    results = _results(store)
    reg = _validate_all(ValidationLedger())

    assert sms_view.score_display(results[0].sms, registry=reg) != WITHHELD

    ranked = ranking_view.ranking(results, registry=reg)
    assert ranked[0]["position"] == "1"
    assert ranked[0]["score"] != WITHHELD
    assert "validated" in ranking_view.framing(registry=reg).lower()

    dig = daily_top_view.digest(results, registry=reg)
    assert all(row["score"] != WITHHELD for row in dig["names"])


def test_no_buy_sell_verb_leaks_in_any_gated_view(store):
    results = _results(store)
    reg = _validate_all(ValidationLedger())
    blobs = [ranking_view.framing(registry=reg), daily_top_view.digest(results, registry=reg)["framing"]]
    blobs += [r["state"] for r in ranking_view.ranking(results, registry=reg)]
    for text in blobs:
        low = text.lower()
        for banned in ("buy ", "sell ", "target price"):
            assert banned not in low, f"leaked {banned!r} in {text!r}"
