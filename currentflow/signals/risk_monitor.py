"""Portfolio Risk Monitor (spec §9) — OBSERVATION (risk observations, not return
predictions).

Surfaces the Stage-4 gates the spec asks for as risk *measurements* over a supplied
portfolio, and feeds the §6 exposure caps + correlated-pair check:

    - exposure caps        per-name ≤ 10% equity, per-sector ≤ 30% (§6)
    - sector concentration Herfindahl (HHI) over sector weights
    - crowding matrix      "same bandar" broker-overlap correlation between names,
                           and the correlated-pair check (§6) that flags ρ ≥ threshold
    - β vs benchmark       systematic exposure; benchmark returns are injected (no IHSG
                           feed is ingested — None until one is provided, missing ≠ zero)
    - VaR (95% · 1d)       historical simulation over the portfolio's daily returns
    - liquidity / DTE      days-to-exit at a bounded ADV participation rate
    - scenario stress      defined what-if shocks (hypothetical impact, not a forecast)
    - circuit breakers     §6 halt-new-entries (−3% daily) / pause-system (−10% DD)

RULE B: every number here is a **measurement of risk exposure** — VaR, β, HHI, a
correlation, a days-to-exit, a what-if impact — never a confidence, probability of a
return, Smart Money Score, or buy/sell verb. Scenario rows are explicitly hypothetical
("if X, the book moves Y"), not predictions. `missing ≠ zero`: a metric with no visible
input is `None` and logged, never a fabricated zero.

Positions are an **input**: the IDX-aware paper fill engine that produces them lands in
slice 7. Until then the monitor observes any portfolio the operator hands it (e.g. the
ARMED watchlist as a preview) — P&L stays `None` when no entry price exists.
"""

from __future__ import annotations

import logging
import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date as Date
from datetime import datetime
from enum import Enum

from currentflow import config
from currentflow.dal.models import BoardType, DailyBar, RowStatus
from currentflow.signals import broker_flow
from currentflow.signals.broker_flow import BrokerDNA
from currentflow.store.db import Store
from currentflow.universe import bands

log = logging.getLogger(__name__)


# --- portfolio inputs ----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Position:
    """One open paper position. `entry_price` is optional — P&L is only defined once a
    fill exists (slice 7); until then the exposure/crowding/β/VaR observations still
    compute from the mark price and stored flow."""

    symbol: str
    sector: str
    qty: int                          # shares held (lots × 100)
    last_price: float                 # mark price (latest visible close or supplied)
    entry_price: float | None = None
    board: BoardType = BoardType.MAIN  # selects the ARB band for the lock scenario

    @property
    def market_value(self) -> float:
        return self.qty * self.last_price

    @property
    def pnl(self) -> float | None:
        if self.entry_price is None:
            return None
        return (self.last_price - self.entry_price) * self.qty


@dataclass(frozen=True, slots=True)
class Portfolio:
    positions: tuple[Position, ...]
    cash: float = 0.0

    @property
    def invested(self) -> float:
        return sum(p.market_value for p in self.positions)

    @property
    def equity(self) -> float:
        return self.invested + self.cash


# --- exposure caps (§6) --------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Exposure:
    key: str                # symbol or sector
    weight: float           # market value / equity, 0–1
    cap: float              # the §6 cap for this scope
    over_cap: bool          # breaches the hard cap
    warn: bool              # approaching the cap (design amber band)


def _exposure(key: str, value: float, equity: float, cap: float, warn: float) -> Exposure:
    w = value / equity if equity > 0 else 0.0
    return Exposure(key=key, weight=w, cap=cap, over_cap=w > cap, warn=warn < w <= cap)


def name_exposures(pf: Portfolio) -> list[Exposure]:
    eq = pf.equity
    return sorted(
        (
            _exposure(p.symbol, p.market_value, eq, config.EXPOSURE_CAP_NAME, config.EXPOSURE_WARN_NAME)
            for p in pf.positions
        ),
        key=lambda e: e.weight,
        reverse=True,
    )


def sector_values(pf: Portfolio) -> dict[str, float]:
    out: dict[str, float] = defaultdict(float)
    for p in pf.positions:
        out[p.sector] += p.market_value
    return dict(out)


def sector_exposures(pf: Portfolio) -> list[Exposure]:
    eq = pf.equity
    return sorted(
        (
            _exposure(sec, val, eq, config.EXPOSURE_CAP_SECTOR, config.EXPOSURE_WARN_SECTOR)
            for sec, val in sector_values(pf).items()
        ),
        key=lambda e: e.weight,
        reverse=True,
    )


def sector_hhi(pf: Portfolio) -> float | None:
    """Herfindahl over sector weights (of invested value), 0–1. 1.0 = one sector."""
    vals = list(sector_values(pf).values())
    total = sum(vals)
    if total <= 0:
        return None
    return sum((v / total) ** 2 for v in vals)


# --- crowding / "same bandar" correlation (§6 correlated-pair check) -----------------


def crowding_correlation(
    net_a: dict[str, float], net_b: dict[str, float]
) -> float | None:
    """Cosine similarity of two names' net-by-broker vectors — how much the *same*
    brokers drive both on the *same* side. ~1 = the same syndicate is behind both
    (crowded); ~0 = disjoint. None when either vector is empty or zero-norm."""
    if not net_a or not net_b:
        return None
    na = math.sqrt(sum(v * v for v in net_a.values()))
    nb = math.sqrt(sum(v * v for v in net_b.values()))
    if na == 0 or nb == 0:
        return None
    dot = sum(net_a.get(k, 0.0) * net_b.get(k, 0.0) for k in set(net_a) | set(net_b))
    return dot / (na * nb)


def _shared_lead_broker(net_a: dict[str, float], net_b: dict[str, float]) -> str | None:
    """The broker whose net most drives both names on the same side (the common
    'bandar'), or None if no broker is a same-side contributor to both."""
    shared = [(k, net_a[k] * net_b[k]) for k in set(net_a) & set(net_b) if net_a[k] * net_b[k] > 0]
    return max(shared, key=lambda kv: kv[1])[0] if shared else None


@dataclass(frozen=True, slots=True)
class CrowdedPair:
    a: str
    b: str
    rho: float
    shared_lead_broker: str | None


def net_by_broker(
    store: Store, symbol: str, decision_ts: datetime, registry: dict[str, BrokerDNA] | None = None
) -> dict[str, float]:
    """{broker_code: window net value} for one name — the crowding vector."""
    snap = broker_flow.analyze(store, symbol, decision_ts, registry=registry)
    return {b.broker_code: b.net_value for b in snap.brokers}


def crowding_matrix_from_nets(
    nets: dict[str, dict[str, float]],
) -> dict[str, dict[str, float | None]]:
    """Symmetric N×N same-bandar correlation from per-name broker vectors. Diagonal is
    1.0 for names that carry broker flow, None for names with none (missing ≠ zero — no
    fabricated self-correlation)."""
    symbols = list(nets)
    matrix: dict[str, dict[str, float | None]] = {s: {} for s in symbols}
    for a in symbols:
        for b in symbols:
            matrix[a][b] = (1.0 if nets[a] else None) if a == b else crowding_correlation(nets[a], nets[b])
    return matrix


def crowding_matrix(
    store: Store,
    symbols: list[str],
    decision_ts: datetime,
    *,
    registry: dict[str, BrokerDNA] | None = None,
) -> dict[str, dict[str, float | None]]:
    """Store convenience: read each name's broker vector and build the matrix."""
    return crowding_matrix_from_nets(
        {s: net_by_broker(store, s, decision_ts, registry) for s in symbols}
    )


def correlated_pairs(
    matrix: dict[str, dict[str, float | None]],
    nets: dict[str, dict[str, float]],
    *,
    threshold: float = config.CROWDING_CORR_THRESHOLD,
) -> list[CrowdedPair]:
    """§6 correlated-pair check: unordered name pairs whose crowding ρ ≥ threshold,
    most-crowded first, annotated with the shared lead broker."""
    seen: set[frozenset[str]] = set()
    out: list[CrowdedPair] = []
    for a in matrix:
        for b, rho in matrix[a].items():
            if a == b or rho is None or rho < threshold:
                continue
            key = frozenset((a, b))
            if key in seen:
                continue
            seen.add(key)
            out.append(CrowdedPair(a=a, b=b, rho=rho, shared_lead_broker=_shared_lead_broker(nets[a], nets[b])))
    return sorted(out, key=lambda p: p.rho, reverse=True)


# --- returns, β, VaR -----------------------------------------------------------------


def daily_returns(bars: list[DailyBar]) -> dict[Date, float]:
    """{date: close-to-close return} over consecutive complete TRADED bars."""
    complete = [
        b for b in sorted(bars, key=lambda b: b.date)
        if b.status is RowStatus.TRADED and b.close
    ]
    out: dict[Date, float] = {}
    for prev, cur in zip(complete, complete[1:]):
        if prev.close:
            out[cur.date] = cur.close / prev.close - 1
    return out


def market_proxy_returns(
    store: Store,
    symbols: list[str],
    decision_ts: datetime,
    *,
    start: Date | None = None,
    end: Date | None = None,
) -> dict[Date, float]:
    """Equal-weight universe mean daily return — a market proxy for β when no index
    (IHSG) feed is ingested. Per day it averages only the names with a visible return
    (missing ≠ zero). Honest by construction; the UI labels it a proxy, not IHSG."""
    per_day: dict[Date, list[float]] = defaultdict(list)
    for sym in symbols:
        bars = store.read_daily_bars(sym, decision_ts, start=start, end=end)
        for d, r in daily_returns(bars).items():
            per_day[d].append(r)
    return {d: sum(rs) / len(rs) for d, rs in per_day.items()}


def beta(
    name_returns: dict[Date, float], benchmark_returns: dict[Date, float]
) -> float | None:
    """cov(name, bench) / var(bench) over the dates both share. None if the benchmark
    barely moves or there is too little overlap (missing ≠ zero)."""
    common = sorted(set(name_returns) & set(benchmark_returns))
    if len(common) < 2:
        return None
    r = [name_returns[d] for d in common]
    m = [benchmark_returns[d] for d in common]
    mean_r, mean_m = sum(r) / len(r), sum(m) / len(m)
    var_m = sum((mi - mean_m) ** 2 for mi in m) / len(m)
    if var_m == 0:
        return None
    cov = sum((ri - mean_r) * (mi - mean_m) for ri, mi in zip(r, m)) / len(common)
    return cov / var_m


def _percentile(sorted_vals: list[float], q: float) -> float:
    """Nearest-rank percentile of an ascending series (q in [0,1]). The 1e-9 guard
    keeps float error (e.g. 0.05·20 = 1.0000000000000002) from overshooting the rank."""
    rank = max(1, math.ceil(q * len(sorted_vals) - 1e-9))
    return sorted_vals[rank - 1]


def var_historical(
    portfolio_returns: list[float], confidence: float = config.VAR_CONFIDENCE
) -> float | None:
    """Historical-simulation 1-day VaR as a positive loss fraction of equity: the
    negative of the (1−confidence) empirical return quantile, floored at 0. None when
    the return series is too short to be meaningful."""
    if len(portfolio_returns) < 2:
        return None
    q_return = _percentile(sorted(portfolio_returns), 1 - confidence)
    return max(0.0, -q_return)


def portfolio_returns(
    pf: Portfolio, per_name_returns: dict[str, dict[Date, float]]
) -> list[float]:
    """Equity-weighted daily portfolio return series. Each day sums wᵢ·rᵢ over the
    positions with a return that day; days with no visible return anywhere are absent
    (missing ≠ zero). Weights are by market value of invested capital."""
    invested = pf.invested
    if invested <= 0:
        return []
    weight = {p.symbol: p.market_value / invested for p in pf.positions}
    by_day: dict[Date, float] = defaultdict(float)
    seen: set[Date] = set()
    for p in pf.positions:
        for d, r in per_name_returns.get(p.symbol, {}).items():
            by_day[d] += weight[p.symbol] * r
            seen.add(d)
    return [by_day[d] for d in sorted(seen)]


# --- liquidity / days-to-exit --------------------------------------------------------


def _adv20(bars: list[DailyBar]) -> float | None:
    window = [b for b in sorted(bars, key=lambda b: b.date) if b.status is RowStatus.TRADED][
        -config.ADV_WINDOW_DAYS:
    ]
    vals = [b.value for b in window if b.value is not None]
    return sum(vals) / len(vals) if vals else None


@dataclass(frozen=True, slots=True)
class Liquidity:
    symbol: str
    adv20: float | None
    days_to_exit: float | None   # market value / (participation · ADV)


def days_to_exit(market_value: float, adv20: float | None) -> float | None:
    """Trading days to liquidate at ≤ `DTE_PARTICIPATION` of ADV per day. None when
    ADV is unknown (missing ≠ zero — never a fabricated 'instant' exit)."""
    if not adv20 or adv20 <= 0:
        return None
    return market_value / (config.DTE_PARTICIPATION * adv20)


# --- scenario stress (defined what-ifs, not predictions) -----------------------------


@dataclass(frozen=True, slots=True)
class ScenarioImpact:
    name: str
    detail: str
    impact_idr: float | None            # signed IDR change to the book under the shock
    impact_pct_of_equity: float | None  # impact_idr / equity


def _scenario(name: str, detail: str, impact_idr: float | None, equity: float) -> ScenarioImpact:
    pct = None if impact_idr is None or equity <= 0 else impact_idr / equity
    return ScenarioImpact(name=name, detail=detail, impact_idr=impact_idr, impact_pct_of_equity=pct)


def scenario_stress(
    pf: Portfolio,
    *,
    portfolio_beta: float | None,
    foreign_window_flow: dict[str, float | None],
) -> list[ScenarioImpact]:
    """The design's four what-if rows. Each is a hypothetical mark impact under a
    defined shock (config-pinned), never a forecast. Uncomputable rows carry None."""
    eq = pf.equity
    invested = pf.invested

    # IHSG −5% gap transmitted through portfolio β.
    ihsg = (
        None if portfolio_beta is None
        else portfolio_beta * config.STRESS_IHSG_GAP * invested
    )

    # Foreign exodus: shock the exposure that foreign inflow has been holding up —
    # positions whose window NBSA is net positive are the ones an exodus would reverse.
    foreign_base = sum(
        p.market_value for p in pf.positions
        if (foreign_window_flow.get(p.symbol) or 0.0) > 0
    )
    has_foreign = any(foreign_window_flow.get(p.symbol) is not None for p in pf.positions)
    exodus = config.STRESS_FOREIGN_EXODUS * foreign_base if has_foreign else None

    # Single-name ARB-lock: the largest position gaps to its lower band.
    arb = None
    arb_detail = "no positions"
    if pf.positions:
        worst = max(pf.positions, key=lambda p: p.market_value)
        band = bands.band_pct(worst.board, worst.last_price)
        arb = -worst.market_value * band
        arb_detail = f"{worst.symbol} locks ARB (−{band:.0%})"

    # Rupiah shock: a broad, uniform hit across the whole book.
    rupiah = config.STRESS_RUPIAH_SHOCK * invested

    return [
        _scenario("IHSG −5% gap", f"β {portfolio_beta:.2f} × −5%" if portfolio_beta is not None else "β unknown", ihsg, eq),
        _scenario("Foreign exodus", f"−3% on foreign-held exposure ({foreign_base / 1e9:.1f} bn)" if has_foreign else "no foreign flow visible", exodus, eq),
        _scenario("Single-name ARB-lock", arb_detail, arb, eq),
        _scenario("Rupiah shock", "−4% across the book", rupiah, eq),
    ]


# --- circuit breakers (§6) -----------------------------------------------------------


class CircuitState(str, Enum):
    OK = "OK"
    HALT_NEW_ENTRIES = "HALT_NEW_ENTRIES"   # −3% daily P&L (§6)
    PAUSE_SYSTEM = "PAUSE_SYSTEM"           # −10% peak-to-trough drawdown (§6)


def circuit_breaker_state(
    daily_pnl_pct: float | None, drawdown_pct: float | None
) -> CircuitState:
    """Evaluate the §6 breakers on injected P&L / drawdown (the fill engine supplies
    these in slice 7). Drawdown pause dominates a daily halt."""
    if drawdown_pct is not None and drawdown_pct <= config.CIRCUIT_PAUSE_DRAWDOWN:
        return CircuitState.PAUSE_SYSTEM
    if daily_pnl_pct is not None and daily_pnl_pct <= config.CIRCUIT_HALT_DAILY_PNL:
        return CircuitState.HALT_NEW_ENTRIES
    return CircuitState.OK


# --- unified report ------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RiskReport:
    """The whole risk-observation picture for a portfolio at one decision moment."""

    decision_ts: datetime
    equity: float
    invested: float
    cash: float
    name_exposures: tuple[Exposure, ...]
    sector_exposures: tuple[Exposure, ...]
    sector_hhi: float | None
    portfolio_beta: float | None
    var_1d: float | None                       # loss fraction of equity (95% · 1d)
    var_1d_idr: float | None                   # var_1d · equity
    liquidity: tuple[Liquidity, ...]
    crowded_pairs: tuple[CrowdedPair, ...]
    scenarios: tuple[ScenarioImpact, ...]
    total_pnl: float | None                    # None until any position has an entry price
    crowding: dict[str, dict[str, float | None]] = field(default_factory=dict)

    @property
    def cap_breaches(self) -> tuple[Exposure, ...]:
        return tuple(e for e in (*self.name_exposures, *self.sector_exposures) if e.over_cap)


def build_risk_report(
    store: Store,
    portfolio: Portfolio,
    decision_ts: datetime,
    *,
    benchmark_returns: dict[Date, float] | None = None,
    start: Date | None = None,
    end: Date | None = None,
    registry: dict[str, BrokerDNA] | None = None,
) -> RiskReport:
    """Read look-ahead-safe bars + broker flow per position and assemble every risk
    observation. `benchmark_returns` (e.g. IHSG or a market proxy) is injected — β and
    the IHSG scenario stay None without it (no index feed is ingested; missing ≠ zero)."""
    symbols = [p.symbol for p in portfolio.positions]
    bars_by = {
        p.symbol: store.read_daily_bars(p.symbol, decision_ts, start=start, end=end)
        for p in portfolio.positions
    }
    per_name_returns = {s: daily_returns(b) for s, b in bars_by.items()}

    # β vs the injected benchmark, market-value weighted across names that have one.
    pf_beta = None
    if not benchmark_returns:
        log.info(
            "risk_monitor: no benchmark returns supplied — portfolio β withheld "
            "(no IHSG feed ingested; missing ≠ zero)"
        )
    else:
        weighted, wsum = 0.0, 0.0
        invested = portfolio.invested
        for p in portfolio.positions:
            b = beta(per_name_returns.get(p.symbol, {}), benchmark_returns)
            if b is not None and invested > 0:
                w = p.market_value / invested
                weighted += w * b
                wsum += w
        pf_beta = weighted if wsum > 0 else None

    var_frac = var_historical(portfolio_returns(portfolio, per_name_returns))
    var_idr = None if var_frac is None else var_frac * portfolio.equity

    liq: list[Liquidity] = []
    for p in portfolio.positions:
        adv = _adv20(bars_by[p.symbol])
        liq.append(Liquidity(symbol=p.symbol, adv20=adv, days_to_exit=days_to_exit(p.market_value, adv)))

    nets = {s: net_by_broker(store, s, decision_ts, registry) for s in symbols}
    no_flow = [s for s in symbols if not nets[s]]
    if no_flow:
        log.info(
            "risk_monitor: %d/%d position(s) carry no visible broker flow — crowding shown "
            "as None, not zeroed (%s)", len(no_flow), len(symbols), ", ".join(no_flow),
        )
    matrix = crowding_matrix_from_nets(nets)
    pairs = correlated_pairs(matrix, nets)

    foreign_flow_window = {
        p.symbol: _window_net_foreign(bars_by[p.symbol]) for p in portfolio.positions
    }
    scenarios = scenario_stress(
        portfolio, portfolio_beta=pf_beta, foreign_window_flow=foreign_flow_window
    )

    pnls = [p.pnl for p in portfolio.positions if p.pnl is not None]
    total_pnl = sum(pnls) if pnls else None

    return RiskReport(
        decision_ts=decision_ts,
        equity=portfolio.equity,
        invested=portfolio.invested,
        cash=portfolio.cash,
        name_exposures=tuple(name_exposures(portfolio)),
        sector_exposures=tuple(sector_exposures(portfolio)),
        sector_hhi=sector_hhi(portfolio),
        portfolio_beta=pf_beta,
        var_1d=var_frac,
        var_1d_idr=var_idr,
        liquidity=tuple(liq),
        crowded_pairs=tuple(pairs),
        scenarios=tuple(scenarios),
        total_pnl=total_pnl,
        crowding=matrix,
    )


def _window_net_foreign(bars: list[DailyBar]) -> float | None:
    nf = [b.net_foreign for b in bars if b.net_foreign is not None]
    return sum(nf) if nf else None
