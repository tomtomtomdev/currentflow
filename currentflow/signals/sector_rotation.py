"""Sector Rotation Map (spec §9) — a derived VISUALIZATION over the flow signals.
Rendering, not a new signal.

Flow aggregated by sector, plotted against relative strength on the RS-vs-flow
quadrant the spec names (Leaders / Early Recovery / Distribution Warning / Avoid),
with the foreign/domestic tide as framing:

    - flow (y): net foreign flow (NBSA) summed across the sector's names over the
      window — the tide the design draws around a zero baseline.
    - relative strength (x): the sector's mean price return minus the market's mean
      return over the same window (universe equal-weight as the market proxy).
    - quadrant: the sign pair (rs, flow) → a categorical label, never a buy/sell verb.

RULE B: rs, flow, and the tide are raw **measurements**; the quadrant label is a
categorical observation of where a sector sits, not a ranked recommendation. The
quadrant names are the spec's own (§9). `missing ≠ zero`: a symbol with neither a
return nor a visible net is skipped and logged (never a fabricated zero row), and a
sector missing either axis carries `quadrant = None` rather than a guessed corner.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date as Date
from datetime import datetime
from enum import Enum

from currentflow import config
from currentflow.dal.models import DailyBar, RowStatus
from currentflow.signals import foreign_flow

log = logging.getLogger(__name__)

_UNKNOWN_SECTOR = "UNKNOWN"


class Quadrant(str, Enum):
    """RS-vs-flow quadrant (spec §9). Categorical observation — not a buy/sell verb."""

    LEADERS = "LEADERS"                       # RS ≥ 0, inflow  (top-right)
    EARLY_RECOVERY = "EARLY_RECOVERY"         # RS < 0, inflow  (top-left)
    DISTRIBUTION_WARN = "DISTRIBUTION_WARN"   # RS ≥ 0, outflow (bottom-right)
    AVOID = "AVOID"                           # RS < 0, outflow (bottom-left)


def classify_quadrant(rs: float, flow: float) -> Quadrant:
    """Map a sector's (relative-strength, net-flow) position to its quadrant."""
    strong = rs >= 0
    inflow = flow >= 0
    if inflow:
        return Quadrant.LEADERS if strong else Quadrant.EARLY_RECOVERY
    return Quadrant.DISTRIBUTION_WARN if strong else Quadrant.AVOID


def _tide(flow: float | None) -> str:
    if flow is None or flow == 0:
        return "NEUTRAL"
    return "BUY" if flow > 0 else "SELL"


def _mean(xs: list[float]) -> float | None:
    return sum(xs) / len(xs) if xs else None


@dataclass(frozen=True, slots=True)
class SectorRotation:
    """One sector's rotation observation for the map. No score, no claim."""

    sector: str
    members: tuple[str, ...]           # symbols that contributed at least one axis
    price_return: float | None         # sector mean price return over the window
    market_return: float | None        # universe mean return over the window (proxy)
    relative_strength: float | None    # price_return − market_return (the x-axis)
    net_foreign_flow: float | None     # NBSA summed across the sector (the y-axis, IDR)
    tide: str                          # BUY / SELL / NEUTRAL (sign of net_foreign_flow)
    quadrant: Quadrant | None          # None when either axis is unmeasurable

    @property
    def symbols(self) -> int:
        return len(self.members)


def _symbol_stats(
    bars: list[DailyBar],
) -> tuple[float | None, float | None]:
    """(price_return, net_foreign_flow) for one symbol over the window.

    Return needs ≥2 complete TRADED closes; flow sums only bars that carry a net
    (missing ≠ zero — foreign_flow.daily_net_foreign drops absent nets loudly)."""
    complete = [
        b for b in sorted(bars, key=lambda b: b.date)
        if b.status is RowStatus.TRADED and b.close
    ]
    ret = None
    if len(complete) >= 2 and complete[0].close:
        ret = complete[-1].close / complete[0].close - 1
    daily = foreign_flow.daily_net_foreign(bars)
    flow = sum(daily.values()) if daily else None
    return ret, flow


def build_sector_rotation(
    store,
    symbols: list[str],
    decision_ts: datetime,
    *,
    sector_map: dict[str, str],
    start: Date | None = None,
    end: Date | None = None,
) -> list[SectorRotation]:
    """One row per sector present in `symbols`, look-ahead-safe (the store returns
    only `as_of < decision_ts` rows). Symbols with neither a return nor a visible net
    are skipped and logged; a sector missing an axis carries `quadrant = None`."""
    rets: dict[str, float] = {}
    flows: dict[str, float] = {}
    contributed: set[str] = set()
    skipped = 0
    for sym in symbols:
        bars = store.read_daily_bars(sym, decision_ts, start=start, end=end)
        ret, flow = _symbol_stats(bars)
        if ret is None and flow is None:
            skipped += 1
            continue
        contributed.add(sym)
        if ret is not None:
            rets[sym] = ret
        if flow is not None:
            flows[sym] = flow
    if skipped:
        log.warning(
            "sector_rotation: %d/%d symbol(s) had no visible return or net — skipped, not zeroed",
            skipped, len(symbols),
        )

    market_return = _mean(list(rets.values()))

    by_sector: dict[str, list[str]] = {}
    for sym in sorted(contributed):
        by_sector.setdefault(sector_map.get(sym, _UNKNOWN_SECTOR), []).append(sym)

    out: list[SectorRotation] = []
    for sector, members in by_sector.items():
        m_rets = [rets[s] for s in members if s in rets]
        m_flows = [flows[s] for s in members if s in flows]
        price_return = _mean(m_rets)
        flow = sum(m_flows) if m_flows else None
        rs = (
            price_return - market_return
            if price_return is not None and market_return is not None
            else None
        )
        quadrant = (
            classify_quadrant(rs, flow) if rs is not None and flow is not None else None
        )
        out.append(
            SectorRotation(
                sector=sector,
                members=tuple(members),
                price_return=price_return,
                market_return=market_return,
                relative_strength=rs,
                net_foreign_flow=flow,
                tide=_tide(flow),
                quadrant=quadrant,
            )
        )
    return sorted(out, key=lambda r: r.sector)


def window_start(end: Date, window_days: int = config.SECTOR_WINDOW_DAYS) -> Date:
    """Trailing-window start (calendar days) the caller passes to
    `build_sector_rotation` as `start` — mirrors how the replay/foreign views scope a
    range in `ui/app.py`. The `decision_ts` look-ahead firewall is enforced separately
    by the store; this only bounds which trading days feed the RS/flow measurement."""
    from datetime import timedelta

    return end - timedelta(days=window_days)
