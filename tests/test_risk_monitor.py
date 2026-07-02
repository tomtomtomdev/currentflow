"""Portfolio Risk Monitor (spec §9 + §6 caps) — exposure caps, sector HHI, same-bandar
crowding + correlated-pair check, β vs an injected benchmark, historical VaR,
days-to-exit, scenario stress, and the §6 circuit breakers. Hand-checked math; the
store-integration path is look-ahead-safe."""

from __future__ import annotations

import math
from datetime import date as Date
from datetime import datetime

import pytest
from builders import Chart, brow

from currentflow.dal.models import BoardType, Side
from currentflow.signals import risk_monitor as rm
from currentflow.signals.risk_monitor import (
    CircuitState,
    Portfolio,
    Position,
    beta,
    build_risk_report,
    circuit_breaker_state,
    correlated_pairs,
    crowding_correlation,
    crowding_matrix_from_nets,
    daily_returns,
    days_to_exit,
    name_exposures,
    scenario_stress,
    sector_exposures,
    sector_hhi,
    var_historical,
)

TS = datetime(2026, 3, 1, 9, 0)


# --- exposure caps (§6) --------------------------------------------------------------


def test_name_and_sector_exposure_caps():
    pf = Portfolio(
        positions=(
            Position("AAA", "TECH", 1, 90e6),    # w 0.09 → warn (approaching 10%)
            Position("BBB", "TECH", 1, 250e6),   # w 0.25 → over the 10% name cap
            Position("CCC", "BANK", 1, 30e6),    # w 0.03 → OK
        ),
        cash=630e6,                              # invested 370e6 → equity 1.0e9
    )
    assert pf.equity == pytest.approx(1e9)

    names = {e.key: e for e in name_exposures(pf)}
    assert names["AAA"].weight == pytest.approx(0.09) and names["AAA"].warn and not names["AAA"].over_cap
    assert names["BBB"].over_cap and names["BBB"].weight == pytest.approx(0.25)
    assert not names["CCC"].warn and not names["CCC"].over_cap

    secs = {e.key: e for e in sector_exposures(pf)}
    assert secs["TECH"].over_cap and secs["TECH"].weight == pytest.approx(0.34)  # 340e6/1e9 > 30%
    assert not secs["BANK"].over_cap


def test_sector_hhi_over_weights():
    pf = Portfolio((Position("A", "X", 1, 60e6), Position("B", "Y", 1, 40e6)))
    # weights 0.6 / 0.4 → HHI = 0.36 + 0.16 = 0.52
    assert sector_hhi(pf) == pytest.approx(0.52)
    assert sector_hhi(Portfolio(())) is None


# --- crowding / same-bandar correlation (§6 correlated-pair check) -------------------


def test_crowding_correlation_shared_vs_disjoint():
    rho = crowding_correlation({"DX": 8, "KI": 2}, {"DX": 6, "CC": 1})
    assert rho == pytest.approx(48 / (math.sqrt(68) * math.sqrt(37)))  # ≈ 0.957
    assert crowding_correlation({"DX": 8}, {"YP": 5}) == 0.0           # disjoint brokers
    assert crowding_correlation({}, {"DX": 1}) is None                 # missing ≠ zero


def test_correlated_pairs_flags_shared_lead_broker():
    nets = {"A": {"DX": 8, "KI": 2}, "B": {"DX": 7, "CC": 1}, "C": {"YP": 5}}
    matrix = crowding_matrix_from_nets(nets)
    assert matrix["A"]["A"] == 1.0                       # self-corr for a name with flow
    assert matrix["A"]["C"] == pytest.approx(0.0)        # disjoint

    pairs = correlated_pairs(matrix, nets, threshold=0.7)
    assert [(p.a, p.b) for p in pairs] == [("A", "B")]   # only the DX-crowded pair
    assert pairs[0].shared_lead_broker == "DX"


def test_crowding_diagonal_none_when_no_flow():
    matrix = crowding_matrix_from_nets({"A": {}})
    assert matrix["A"]["A"] is None                      # no fabricated self-correlation


# --- returns, β, VaR -----------------------------------------------------------------


def test_daily_returns_and_beta():
    bars = Chart("AAA")
    for c in (100, 110, 99):
        bars.add(c, c + 1, c - 1, c, 1000)
    nr = daily_returns(bars.bars)
    assert list(nr.values()) == pytest.approx([0.10, 99 / 110 - 1])

    bench = {d: v / 2 for d, v in nr.items()}            # name moves exactly 2× the benchmark
    assert beta(nr, bench) == pytest.approx(2.0)
    assert beta(nr, {list(nr)[0]: 0.05}) is None         # < 2 overlapping days
    assert beta(nr, {d: 0.01 for d in nr}) is None       # zero-variance benchmark


def test_market_proxy_returns_equal_weight_mean(store):
    # AAA rises then falls; BBB the opposite → proxy is their per-day mean
    for sym, closes in [("AAA", [100, 110, 99]), ("BBB", [100, 90, 108])]:
        ch = Chart(sym)
        for c in closes:
            ch.add(c, c + 1, c - 1, c, 1000)
        store.write_daily_bars(ch.bars)
    proxy = rm.market_proxy_returns(store, ["AAA", "BBB"], TS)
    days = sorted(proxy)
    assert proxy[days[0]] == pytest.approx((0.10 + -0.10) / 2)     # +10% and −10%
    assert proxy[days[1]] == pytest.approx(((99 / 110 - 1) + (108 / 90 - 1)) / 2)


def test_var_historical_nearest_rank():
    returns = [-0.08] + [0.0] * 19                       # n=20, 5% → worst day
    assert var_historical(returns, 0.95) == pytest.approx(0.08)
    assert var_historical([0.01, 0.02, 0.03], 0.95) == 0.0   # no loss tail → floored at 0
    assert var_historical([0.01], 0.95) is None              # too short


# --- liquidity / days-to-exit --------------------------------------------------------


def test_days_to_exit():
    assert days_to_exit(100e9, 50e9) == pytest.approx(10.0)  # 100e9 / (0.2 · 50e9)
    assert days_to_exit(100e9, None) is None
    assert days_to_exit(100e9, 0.0) is None


# --- scenario stress (defined what-ifs) ----------------------------------------------


def test_scenario_stress_impacts():
    pf = Portfolio((Position("AAA", "TECH", 1, 100e9, board=BoardType.MAIN),), cash=100e9)
    scen = {s.name: s for s in scenario_stress(pf, portfolio_beta=1.5, foreign_window_flow={"AAA": 20e9})}
    assert scen["IHSG −5% gap"].impact_idr == pytest.approx(1.5 * -0.05 * 100e9)      # β transmits −5%
    assert scen["Foreign exodus"].impact_idr == pytest.approx(-0.03 * 100e9)          # foreign-held base
    assert scen["Single-name ARB-lock"].impact_idr == pytest.approx(-100e9 * 0.07)   # main band 7%
    assert scen["Rupiah shock"].impact_idr == pytest.approx(-0.04 * 100e9)
    assert scen["Rupiah shock"].impact_pct_of_equity == pytest.approx(-4e9 / 200e9)


def test_scenario_stress_none_when_inputs_missing():
    pf = Portfolio((Position("AAA", "TECH", 1, 100e9),), cash=0.0)
    scen = {s.name: s for s in scenario_stress(pf, portfolio_beta=None, foreign_window_flow={"AAA": None})}
    assert scen["IHSG −5% gap"].impact_idr is None       # no β
    assert scen["Foreign exodus"].impact_idr is None     # no foreign flow visible


# --- circuit breakers (§6) -----------------------------------------------------------


def test_circuit_breaker_states():
    assert circuit_breaker_state(-0.01, -0.02) is CircuitState.OK
    assert circuit_breaker_state(-0.04, -0.02) is CircuitState.HALT_NEW_ENTRIES   # −3% daily
    assert circuit_breaker_state(-0.01, -0.12) is CircuitState.PAUSE_SYSTEM       # −10% DD
    assert circuit_breaker_state(-0.05, -0.15) is CircuitState.PAUSE_SYSTEM       # pause dominates
    assert circuit_breaker_state(None, None) is CircuitState.OK


# --- store integration: end-to-end + look-ahead --------------------------------------


def _seed_bars(store):
    # AAA's closes fall then recover so the benchmark built from them carries variance
    for sym, closes in [("AAA", [100, 110, 99]), ("BBB", [50, 55, 60]), ("CCC", [200, 190, 180])]:
        ch = Chart(sym)
        for c in closes:
            ch.add(c, c + 1, c - 1, c, 1000, nf=1e9)
        store.write_daily_bars(ch.bars)


def _seed_brokers(store):
    rows = []
    for d in (Date(2026, 1, 5), Date(2026, 1, 6), Date(2026, 1, 7)):
        rows += [brow("DX", Side.BUY, 8e9, d, symbol="AAA"), brow("YP", Side.SELL, 2e9, d, symbol="AAA")]
        rows += [brow("DX", Side.BUY, 7e9, d, symbol="BBB"), brow("YP", Side.SELL, 1e9, d, symbol="BBB")]
        rows += [brow("YP", Side.BUY, 5e9, d, symbol="CCC")]
    store.write_broker_net(rows)


def test_build_risk_report_end_to_end(store):
    _seed_bars(store)
    _seed_brokers(store)
    pf = Portfolio(
        positions=(
            Position("AAA", "MATERIALS", 100, 121, entry_price=100),
            Position("BBB", "MATERIALS", 100, 60),
            Position("CCC", "ENERGY", 100, 180),
        ),
        cash=0.0,
    )
    bench = daily_returns(store.read_daily_bars("AAA", TS))
    report = build_risk_report(store, pf, TS, benchmark_returns=bench)

    assert report.sector_hhi is not None
    assert report.portfolio_beta is not None                 # benchmark supplied
    assert report.var_1d is not None
    # AAA & BBB are both DX-led → crowded; CCC (retail) is not
    crowded = {frozenset((p.a, p.b)) for p in report.crowded_pairs}
    assert frozenset(("AAA", "BBB")) in crowded
    assert all(frozenset(("CCC",)) != {p.a} for p in report.crowded_pairs)
    assert report.total_pnl == pytest.approx((121 - 100) * 100)   # only AAA carries an entry


def test_build_risk_report_lookahead_hides_broker_flow(store):
    _seed_bars(store)
    _seed_brokers(store)
    pf = Portfolio((Position("AAA", "MATERIALS", 100, 121), Position("BBB", "MATERIALS", 100, 60)))

    blind_ts = datetime(2026, 1, 6, 8, 0)   # before any broker summary (as_of D+1 09:00) is knowable
    report = build_risk_report(store, pf, blind_ts)
    assert report.crowded_pairs == ()        # no broker flow visible yet → no crowding
    assert report.portfolio_beta is None     # no benchmark provided
