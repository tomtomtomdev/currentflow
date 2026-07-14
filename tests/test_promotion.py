"""Slice-8 promotion engine (RULE B / LD-9): the paper-trade engine is the sole authority
that advances a module OBSERVATION_ONLY → VALIDATING → VALIDATED, and only on ≥
PAPER_VALIDATION_MONTHS of forward paper with a positive walk-forward Sharpe."""

from __future__ import annotations

from datetime import date as Date

from currentflow import config
from currentflow.execution.risk import ExitReason
from currentflow.validation import metrics
from currentflow.validation.promotion import ValidationLedger, resolve_state
from currentflow.validation.state import GATED_MODULES, ModuleState
from currentflow.validation.trade import PaperTrade


def _trade(move: float, day: int) -> PaperTrade:
    return PaperTrade(
        symbol="X", track="B", tilt_kind="NEUTRAL",
        entry_date=Date(2026, 1, day), exit_date=Date(2026, 1, day + 1), qty=1000,
        entry_price=100.0, exit_price=100.0 + move, entry_fee=500.0, exit_fee=500.0,
        exit_reason=ExitReason.TARGET, stop=95.0, risk_idr=5000.0,
    )


def _winning_record(n=12):
    return [_trade(8 + (i % 3) * 4, i) for i in range(1, n + 1)]


# --- the pure gate -------------------------------------------------------------------


def test_resolve_state_gate():
    M = config.PAPER_VALIDATION_MONTHS
    assert resolve_state(0, 1.0) is ModuleState.OBSERVATION_ONLY
    assert resolve_state(1, 1.0) is ModuleState.VALIDATING           # not enough months
    assert resolve_state(M, None) is ModuleState.VALIDATING          # no walk-forward yet
    assert resolve_state(M, -0.2) is ModuleState.VALIDATING          # negative walk-forward
    assert resolve_state(M, 0.5) is ModuleState.VALIDATED            # earned


# --- the ledger is the sole, server-authoritative writer -----------------------------


def test_ledger_starts_all_gated_modules_observation_only():
    led = ValidationLedger()
    # Every gated module (incl. the LD-11 `fast_mode` lane) starts observation-only.
    assert "fast_mode" in GATED_MODULES
    for m in GATED_MODULES:
        assert led.state(m) is ModuleState.OBSERVATION_ONLY
    assert led.states() == {m: ModuleState.OBSERVATION_ONLY for m in GATED_MODULES}


def test_promotes_only_after_months_and_positive_walk_forward():
    led = ValidationLedger()
    rec = _winning_record()
    assert metrics.walk_forward_sharpe(rec) > 0    # sanity: this record has positive WF

    # enough trades but not enough months → still VALIDATING (no number)
    led.record_forward_paper("sms", trades=rec, months_accrued=config.PAPER_VALIDATION_MONTHS - 1)
    assert led.state("sms") is ModuleState.VALIDATING

    # months cleared AND positive walk-forward → VALIDATED (may show its number)
    r = led.record_forward_paper("sms", trades=rec, months_accrued=config.PAPER_VALIDATION_MONTHS)
    assert r.state is ModuleState.VALIDATED
    assert led.state("sms") is ModuleState.VALIDATED


def test_enough_months_but_losing_record_is_not_promoted():
    led = ValidationLedger()
    losers = [_trade(-8 - (i % 3) * 4, i) for i in range(1, 13)]
    led.record_forward_paper("sms", trades=losers, months_accrued=config.PAPER_VALIDATION_MONTHS)
    assert led.state("sms") is ModuleState.VALIDATING     # months alone never earn the number


def test_promotion_is_per_module():
    led = ValidationLedger()
    led.record_forward_paper("sms", trades=_winning_record(), months_accrued=config.PAPER_VALIDATION_MONTHS)
    assert led.state("sms") is ModuleState.VALIDATED
    # the other gated modules are untouched — promotion never leaks across modules
    assert led.state("ai_ranking") is ModuleState.OBSERVATION_ONLY
    assert led.state("daily_top") is ModuleState.OBSERVATION_ONLY
