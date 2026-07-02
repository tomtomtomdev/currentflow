"""Portfolio Risk Monitor view-model — pure data shaping, no Streamlit.

RULE B: these are risk *observations* (VaR, β, HHI, crowding ρ, days-to-exit, what-if
impacts) — measurements of exposure, never a return prediction, probability, score, or
buy/sell verb. Scenario rows are hypothetical shocks, not forecasts.
"""

from __future__ import annotations

from currentflow.signals.risk_monitor import Exposure, RiskReport

_IDR_BN = 1e9

FRAMING = "risk observations, not return predictions"


def _bn(v: float | None) -> float | None:
    return None if v is None else round(v / _IDR_BN, 2)


def _pct(v: float | None) -> float | None:
    return None if v is None else round(v * 100, 2)


def _cap_status(e: Exposure) -> str:
    return "OVER CAP" if e.over_cap else ("WARN" if e.warn else "OK")


def metric_cards(report: RiskReport) -> dict:
    """The four top cards: portfolio β, VaR (95%·1d), sector HHI, invested/cash."""
    hhi = report.sector_hhi
    return {
        "portfolio_beta": None if report.portfolio_beta is None else round(report.portfolio_beta, 2),
        "var_1d_pct": _pct(report.var_1d),
        "var_1d_bn": _bn(report.var_1d_idr),
        "sector_hhi": None if hhi is None else round(hhi, 2),
        "sector_hhi_label": _hhi_label(hhi),
        "invested_bn": _bn(report.invested),
        "cash_bn": _bn(report.cash),
        "equity_bn": _bn(report.equity),
        "total_pnl_bn": _bn(report.total_pnl),
    }


def _hhi_label(hhi: float | None) -> str:
    if hhi is None:
        return "—"
    if hhi >= 0.5:
        return "highly concentrated"
    if hhi >= 0.25:
        return "concentrated"
    return "diversified"


def _exposure_rows(exposures: tuple[Exposure, ...]) -> list[dict]:
    return [
        {
            "key": e.key,
            "weight_pct": _pct(e.weight),
            "cap_pct": _pct(e.cap),
            "status": _cap_status(e),
        }
        for e in exposures
    ]


def name_exposure_rows(report: RiskReport) -> list[dict]:
    return _exposure_rows(report.name_exposures)


def sector_exposure_rows(report: RiskReport) -> list[dict]:
    return _exposure_rows(report.sector_exposures)


def crowding_rows(report: RiskReport) -> list[dict]:
    """Correlated-pair check (§6): flagged same-bandar pairs, most-crowded first."""
    return [
        {
            "pair": f"{p.a} & {p.b}",
            "rho": round(p.rho, 2),
            "shared_lead_broker": p.shared_lead_broker or "—",
        }
        for p in report.crowded_pairs
    ]


def liquidity_rows(report: RiskReport) -> list[dict]:
    return [
        {
            "symbol": lq.symbol,
            "adv20_bn": _bn(lq.adv20),
            "days_to_exit": None if lq.days_to_exit is None else round(lq.days_to_exit, 1),
        }
        for lq in report.liquidity
    ]


def scenario_rows(report: RiskReport) -> list[dict]:
    return [
        {
            "scenario": s.name,
            "detail": s.detail,
            "impact_bn": _bn(s.impact_idr),
            "impact_pct_of_equity": _pct(s.impact_pct_of_equity),
        }
        for s in report.scenarios
    ]
