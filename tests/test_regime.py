"""Market-regime read (`signals.regime`) — OBSERVATION only. It classifies a coarse
regime from an injected look-ahead-safe return series and, per RULE B / the operator
decision (2026-07-08), exposes NO allocation multiplier and drives no sizing."""

from __future__ import annotations

from datetime import date as Date, datetime, timedelta

from tests.builders import Chart

from currentflow.signals.regime import (
    Regime,
    RegimeRead,
    classify_market_regime,
    classify_regime,
    market_breadth,
)

_DTS = datetime(2100, 1, 1)   # far-future decision_ts → all stored bars are visible


def _series(daily: list[float]) -> dict[Date, float]:
    start = Date(2026, 1, 5)
    return {start + timedelta(days=i): r for i, r in enumerate(daily)}


def _rising(symbol: str, n: int = 16, step: float = 1.0):
    ch = Chart(symbol)
    for i in range(n):
        c = 100 + step * i
        ch.add(c, c + 0.5, c - 0.5, c)
    return ch.bars


def _falling(symbol: str, n: int = 16, step: float = 0.5):
    ch = Chart(symbol)
    for i in range(n):
        c = 100 - step * i
        ch.add(c, c + 0.5, c - 0.5, c)
    return ch.bars


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


def test_market_breadth_counts_advancers(store):
    for s in ("A", "B", "C"):
        store.write_daily_bars(_rising(s))
    breadth = market_breadth(store, ["A", "B", "C"], _DTS)
    assert breadth and all(v == 1.0 for v in breadth.values())   # every name advances daily


def test_trend_plus_breadth_reads_risk_on(store):
    for s in ("A", "B", "C", "D", "E"):
        store.write_daily_bars(_rising(s))
    read = classify_market_regime(store, ["A", "B", "C", "D", "E"], _DTS)
    assert read.regime is Regime.RISK_ON
    assert read.trend_pct > 0 and read.above_ma
    assert read.breadth == 1.0 and read.breadth_latest == 1.0


def test_breadth_divergence_downgrades_to_neutral(store):
    """One big winner lifts the equal-weight proxy (trend up), but the broad universe is
    declining — weak breadth blocks a RISK_ON read (A-D confirmation, Zweig/Murphy)."""
    store.write_daily_bars(_rising("WIN", step=4.0))
    for s in ("L1", "L2", "L3", "L4"):
        store.write_daily_bars(_falling(s))
    read = classify_market_regime(store, ["WIN", "L1", "L2", "L3", "L4"], _DTS)
    assert read.trend_pct > 0.02 and read.above_ma            # trend leg is up…
    assert read.breadth_latest == 0.2                          # …but only 1/5 advancing
    assert read.regime is Regime.NEUTRAL                       # breadth vetoes RISK_ON


def test_regime_exposes_no_allocation_multiplier():
    """RULE B guard: the observation must not carry a sizing knob. Scaling allocation by
    regime is a deferred, spec-bump-gated decision — the read has no such field."""
    read = classify_regime(_series([0.01] * 30))
    for forbidden in ("allocation_multiplier", "size_multiplier", "weight", "deploy_pct"):
        assert not hasattr(read, forbidden)
    assert isinstance(read, RegimeRead)
