"""Index-rebalancing filter (spec §3) — down-weight pure-beta moves, never reject.

A candidate whose move is explained by index/sector beta (rolling β-adjusted return
≈ sector return) with flow concentrated on index-tracker brokers near a rebalance
date gets its SMS **down-weighted by 30%** (multiplier 0.7). It stays in the
universe: "stop paying alpha prices for beta", not exclusion.

The multiplier is consumed by the SMS in slice 4; computing it here keeps the
universe stage the single owner of §3 logic.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as Date
from datetime import timedelta

from currentflow import config

# MSCI free-float / FIF-cut review calendar — event-risk dates to track (spec §3
# names 2026 reviews touching BBCA, GOTO, AMMN…). Operator-maintained.
REBALANCE_DATES_2026: tuple[Date, ...] = (
    Date(2026, 2, 27),   # MSCI Feb quarterly review effective
    Date(2026, 5, 29),   # MSCI May semi-annual review effective
    Date(2026, 8, 31),   # MSCI Aug quarterly review effective
    Date(2026, 11, 30),  # MSCI Nov semi-annual review effective
)
REBALANCE_PROXIMITY_DAYS = 7


@dataclass(frozen=True, slots=True)
class RebalanceCheck:
    beta_explained: bool
    near_rebalance: bool
    tracker_flow_dominant: bool
    sms_multiplier: float  # 0.7 when down-weighted, else 1.0


def near_rebalance(
    day: Date,
    rebalance_dates: tuple[Date, ...] = REBALANCE_DATES_2026,
    proximity_days: int = REBALANCE_PROXIMITY_DAYS,
) -> bool:
    window = timedelta(days=proximity_days)
    return any(abs(day - d) <= window for d in rebalance_dates)


def rebalance_downweight(
    day: Date,
    stock_return: float,
    sector_return: float,
    beta: float,
    tracker_broker_flow_share: float,
    *,
    rebalance_dates: tuple[Date, ...] = REBALANCE_DATES_2026,
) -> RebalanceCheck:
    """Down-weight decision for one candidate on one day.

    "Explained by beta" = the β-adjusted residual (stock return − β × sector return)
    is within `REBALANCE_RESIDUAL_THRESHOLD`, AND ≥ `REBALANCE_TRACKER_SHARE` of the
    net flow sits on index-tracker brokers, AND the day is near a rebalance date.
    All three must hold — a genuine alpha move near a rebalance keeps full weight.
    """
    residual = stock_return - beta * sector_return
    beta_explained = abs(residual) <= config.REBALANCE_RESIDUAL_THRESHOLD
    is_near = near_rebalance(day, rebalance_dates)
    tracker_dominant = tracker_broker_flow_share >= config.REBALANCE_TRACKER_SHARE

    downweight = beta_explained and is_near and tracker_dominant
    return RebalanceCheck(
        beta_explained=beta_explained,
        near_rebalance=is_near,
        tracker_flow_dominant=tracker_dominant,
        sms_multiplier=config.REBALANCE_DOWNWEIGHT if downweight else 1.0,
    )
