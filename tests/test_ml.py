"""Slice 9 — Scale / ML layer (gated, LD-8). Tests the admission gate, purged/embargoed CV,
engineered features, the walk-forward weight optimizer (sole writer of weights, never
degrades, respects locked §4 structure), the no-hand-edit weight store, and the doubly-gated
ML ranker (LD-8 admission + RULE B display)."""

from __future__ import annotations

from datetime import date as Date
from datetime import datetime

import pytest

from currentflow import config
from currentflow.execution.risk import ExitReason
from currentflow.ml import optimizer as opt
from currentflow.ml import ranker as rk
from currentflow.ml.admission import MLNotAdmittedError, check_admission, require_admission
from currentflow.ml.cv import (
    InsufficientSamplesError,
    Sample,
    purged_walk_forward,
)
from currentflow.ml.features import FEATURE_KEYS, features_from_sms
from currentflow.ml.weights_store import WeightStore
from currentflow.signals.sms import COMPONENT_KEYS, SmsComponent, SmsResult
from currentflow.ui import ml_view
from currentflow.validation.promotion import ValidationLedger
from currentflow.validation.state import ModuleState


# --- helpers -------------------------------------------------------------------------


def _trade(r: float, day: int):
    """A PaperTrade whose net_return is exactly `r` (entry 100, qty 1000, zero fees)."""
    from currentflow.validation.trade import PaperTrade

    return PaperTrade(
        symbol="X", track="B", tilt_kind="NEUTRAL",
        entry_date=Date(2026, 1, day), exit_date=Date(2026, 1, day), qty=1000,
        entry_price=100.0, exit_price=100.0 * (1.0 + r), entry_fee=0.0, exit_fee=0.0,
        exit_reason=ExitReason.TARGET, stop=95.0, risk_idr=5000.0,
    )


def _winning_record(n=12):
    return [_trade((8 + (i % 3) * 4) / 100.0, i) for i in range(1, n + 1)]


def _validated_ledger(*modules: str) -> ValidationLedger:
    led = ValidationLedger()
    for m in modules or ("sms",):
        led.record_forward_paper(m, trades=_winning_record(), months_accrued=config.PAPER_VALIDATION_MONTHS)
    return led


# --- admission (LD-8) ----------------------------------------------------------------


def test_admission_closed_until_rules_validated():
    led = ValidationLedger()  # everything OBSERVATION_ONLY
    d = check_admission(led)
    assert d.admitted is False
    assert d.state is ModuleState.OBSERVATION_ONLY
    with pytest.raises(MLNotAdmittedError):
        require_admission(led)


def test_admission_opens_only_when_sms_validated():
    led = _validated_ledger("sms")
    d = check_admission(led)
    assert d.admitted is True
    assert require_admission(led).admitted is True


def test_admission_validating_is_not_admitted():
    led = ValidationLedger()
    # enough months but no walk-forward → VALIDATING, not VALIDATED → still locked
    led.record_forward_paper("sms", trades=[_trade(0.05, 1)], months_accrued=config.PAPER_VALIDATION_MONTHS)
    assert led.state("sms") is ModuleState.VALIDATING
    assert check_admission(led).admitted is False


# --- purged + embargoed CV -----------------------------------------------------------


def test_purged_walk_forward_is_forward_only():
    samples = [Sample(Date(2026, 1, d), Date(2026, 1, d)) for d in range(1, 17)]  # resolved, non-overlapping
    folds = purged_walk_forward(samples, folds=3, embargo_frac=0.0)
    assert len(folds) == 3
    for f in folds:
        # train is strictly before test (walk-forward / out-of-sample)
        assert max(f.train) < min(f.test)


def test_purge_drops_label_overlap():
    # 4 samples; fold: warm-up train {0,1}, test {2,3}. Sample 1's label spills into the test.
    samples = [
        Sample(Date(2026, 1, 1), Date(2026, 1, 2)),
        Sample(Date(2026, 1, 3), Date(2026, 1, 20)),   # label runs past the test start
        Sample(Date(2026, 1, 5), Date(2026, 1, 6)),
        Sample(Date(2026, 1, 7), Date(2026, 1, 8)),
    ]
    [fold] = purged_walk_forward(samples, folds=1, embargo_frac=0.0)
    assert fold.test == (2, 3)
    assert fold.train == (0,)          # sample 1 purged (label overlaps the test window)


def test_embargo_drops_boundary_sample():
    samples = [Sample(Date(2026, 1, d), Date(2026, 1, d)) for d in range(1, 5)]
    [no_embargo] = purged_walk_forward(samples, folds=1, embargo_frac=0.0)
    assert no_embargo.train == (0, 1)
    [embargoed] = purged_walk_forward(samples, folds=1, embargo_frac=0.5)  # ceil(0.5*4)=2 dropped
    assert embargoed.train == ()       # both prior samples embargoed at the boundary


def test_cv_raises_when_too_few_samples():
    with pytest.raises(InsufficientSamplesError):
        purged_walk_forward([Sample(Date(2026, 1, 1))], folds=3)


# --- engineered features (LD-8: engineered features only) -----------------------------


def _sms(track="A", **subs) -> SmsResult:
    comps = tuple(
        SmsComponent(k, 0, subs.get(k, 0.0), {}, available=(k in subs))
        for k in COMPONENT_KEYS
    )
    return SmsResult(
        symbol="BBRI", decision_ts=datetime(2026, 3, 2, 9, 15), track=track,
        components=comps, rebalance_multiplier=1.0, internal_score=0.0,
    )


def test_features_are_the_engineered_components():
    row = features_from_sms(_sms(divergence=0.8, rvol=0.4), t1=Date(2026, 3, 10))
    assert set(FEATURE_KEYS) == set(COMPONENT_KEYS)
    assert row.features == {"divergence": 0.8, "rvol": 0.4}   # unavailable components excluded
    assert row.t0 == Date(2026, 3, 2)                          # defaults to the decision date
    assert row.t1 == Date(2026, 3, 10)
    assert row.as_sample() == Sample(Date(2026, 3, 2), Date(2026, 3, 10))


# --- optimizer (sole writer; walk-forward; locked structure; never degrade) -----------


def _make_eval(target: str, sign: float = 1.0):
    """A backtest stub whose trades' returns grow with `weights[target]` — so coordinate
    ascent has a gradient toward `target`. 16 dated trades → enough for 3 folds."""
    jitter = [0.0, 0.5, -0.3, 0.2] * 4  # constant, non-zero-dispersion pattern

    def evaluate(weights: dict[str, int]):
        lvl = weights[target] / 100.0
        return [_trade(sign * (0.02 * lvl + 0.001 * jitter[i]), i + 1) for i in range(16)]

    return evaluate


def test_optimizer_requires_admission():
    with pytest.raises(MLNotAdmittedError):
        opt.optimize_weights("A", evaluate=_make_eval("rvol"), ledger=ValidationLedger())


def test_optimizer_climbs_toward_the_paying_component():
    led = _validated_ledger("sms")
    p = opt.optimize_weights("A", evaluate=_make_eval("rvol"), ledger=led)
    # coordinate ascent shifts weight into the component that pays
    assert p.weights["rvol"] > config.SMS_WEIGHTS["A"]["rvol"]
    assert sum(p.weights.values()) == config.ML_WEIGHT_SUM      # simplex preserved
    assert p.improved is True                                    # positive OOS, non-degrading
    assert p.oos_walk_forward_sharpe is not None and p.oos_walk_forward_sharpe > 0


def test_optimizer_preserves_track_b_locked_zero():
    led = _validated_ledger("sms")
    p = opt.optimize_weights("B", evaluate=_make_eval("rvol"), ledger=led)
    assert p.weights["foreign_flow"] == 0                        # LD-1 locked zero never funded
    assert sum(p.weights.values()) == config.ML_WEIGHT_SUM


def test_optimizer_does_not_propose_a_degrading_change():
    led = _validated_ledger("sms")
    # every candidate loses money → OOS Sharpe ≤ 0 → never flagged improved
    p = opt.optimize_weights("A", evaluate=_make_eval("rvol", sign=-1.0), ledger=led)
    assert p.improved is False


def test_validate_weights_enforces_structure():
    with pytest.raises(ValueError):
        opt.validate_weights("A", {k: 0 for k in COMPONENT_KEYS})           # sum != 100
    bad_b = dict(config.SMS_WEIGHTS["B"]); bad_b["foreign_flow"] = 10; bad_b["rvol"] -= 10
    with pytest.raises(ValueError):
        opt.validate_weights("B", bad_b)                                     # funds a locked zero


# --- weight store (no hand-edit; re-gated; never degrade) -----------------------------


def test_weight_store_has_no_hand_edit_path():
    store = WeightStore()
    # the only mutator is apply_proposal(WeightProposal, ledger) — no raw setter exists
    assert not hasattr(store, "set_weights")
    assert store.live("A") == config.SMS_WEIGHTS["A"]


def test_weight_store_applies_only_improving_proposals():
    led = _validated_ledger("sms")
    store = WeightStore()
    good = opt.optimize_weights("A", evaluate=_make_eval("rvol"), ledger=led)
    assert store.apply_proposal(good, led) is True
    assert store.live("A")["rvol"] == good.weights["rvol"]
    assert len(store.history()) == 1

    bad = opt.optimize_weights("A", evaluate=_make_eval("rvol", sign=-1.0), ledger=led)
    assert store.apply_proposal(bad, led) is False              # degrading → refused
    assert len(store.history()) == 1


def test_weight_store_apply_is_re_gated():
    led = _validated_ledger("sms")
    good = opt.optimize_weights("A", evaluate=_make_eval("rvol"), ledger=led)
    with pytest.raises(MLNotAdmittedError):
        WeightStore().apply_proposal(good, ValidationLedger())  # gate closed at apply time


# --- ML ranker (LD-8 admission + RULE B display) -------------------------------------


def _rows():
    return [
        features_from_sms(_sms(divergence=0.2, rvol=0.9), t0=Date(2026, 3, 2)),
        features_from_sms(_sms(divergence=0.9, rvol=0.1), t0=Date(2026, 3, 2)),
    ]


def test_ranker_requires_admission():
    with pytest.raises(MLNotAdmittedError):
        rk.rank([], weights=config.SMS_WEIGHTS["A"], ledger=ValidationLedger())


def test_ranker_orders_by_weighted_feature_score():
    led = _validated_ledger("sms")
    # weight rvol heavily → the rvol-strong name ranks first
    w = dict(config.SMS_WEIGHTS["A"]);
    out = rk.rank(_rows(), weights={**{k: 0 for k in COMPONENT_KEYS}, "rvol": 100}, ledger=led)
    assert [r["symbol"] for r in out] == ["BBRI", "BBRI"]        # both same symbol here
    # ordering follows score: first row (rvol 0.9) beats second (rvol 0.1)
    ranked_scores = [rk.score_row(r, {**{k: 0 for k in COMPONENT_KEYS}, "rvol": 100}) for r in _rows()]
    assert ranked_scores[0] > ranked_scores[1]


def test_ranker_withholds_number_until_ai_ranking_validated():
    led = _validated_ledger("sms")                              # ML admitted, ai_ranking NOT validated
    out = rk.rank(_rows(), weights=config.SMS_WEIGHTS["A"], ledger=led)
    assert all(r["score"] == "•••" and r["position"] == "•••" for r in out)  # RULE B

    led2 = _validated_ledger("sms", "ai_ranking")              # now ai_ranking validated too
    out2 = rk.rank(_rows(), weights=config.SMS_WEIGHTS["A"], ledger=led2)
    assert all(r["score"] != "•••" and r["position"] != "•••" for r in out2)


# --- UI status view ------------------------------------------------------------------


def test_ml_view_reports_locked_then_open():
    locked = ml_view.status(ValidationLedger())
    assert locked["admitted"] is False
    assert "LOCKED" in locked["banner"]

    opened = ml_view.status(_validated_ledger("sms"))
    assert opened["admitted"] is True
    assert "ADMITTED" in opened["banner"]
