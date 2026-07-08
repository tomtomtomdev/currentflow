"""Market-regime read (`signals.regime`) — OBSERVATION only. It classifies a coarse
regime from an injected look-ahead-safe return series and, per RULE B / the operator
decision (2026-07-08), exposes NO allocation multiplier and drives no sizing."""

from __future__ import annotations

from datetime import date as Date, timedelta

from currentflow.signals.regime import Regime, RegimeRead, classify_regime


def _series(daily: list[float]) -> dict[Date, float]:
    start = Date(2026, 1, 5)
    return {start + timedelta(days=i): r for i, r in enumerate(daily)}


def test_rising_trend_reads_risk_on():
    read = classify_regime(_series([0.01] * 30))
    assert read.regime is Regime.RISK_ON
    assert read.above_ma and read.trend_pct > 0 and read.breadth == 1.0


def test_falling_trend_reads_risk_off():
    read = classify_regime(_series([-0.01] * 30))
    assert read.regime is Regime.RISK_OFF
    assert read.above_ma is False and read.trend_pct < 0


def test_choppy_reads_neutral():
    read = classify_regime(_series([0.02, -0.02] * 15))
    assert read.regime is Regime.NEUTRAL


def test_insufficient_data_is_unknown_not_zero():
    read = classify_regime(_series([0.01] * 3))
    assert read.regime is Regime.UNKNOWN
    assert read.trend_pct is None and read.breadth is None   # missing ≠ zero


def test_regime_exposes_no_allocation_multiplier():
    """RULE B guard: the observation must not carry a sizing knob. Scaling allocation by
    regime is a deferred, spec-bump-gated decision — the read has no such field."""
    read = classify_regime(_series([0.01] * 30))
    for forbidden in ("allocation_multiplier", "size_multiplier", "weight", "deploy_pct"):
        assert not hasattr(read, forbidden)
    assert isinstance(read, RegimeRead)
