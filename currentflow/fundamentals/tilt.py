"""Fundamental tilt (spec §7, LD-6/7) — conviction & hold-horizon multiplier, NOT a gate.

The core thesis: flow leads, technical structure confirms timing, **fundamental quality
sizes conviction & hold horizon**. So fundamentals never block an entry (LD-6) — they
only set the position multiplier and how the exit manager trails the stop.

Two tracks (LD-7):
  - **Non-financials/non-utilities:** Magic Formula combined rank (Greenblatt EY + ROC,
    served as fitem 13474 Rank(Magic Formula)%). Top tercile → COMPOUNDER (×1.0, hold
    through markup, wide trail); mid → NEUTRAL (×0.75, standard trail); bottom tercile or
    negative EBIT → SPECULATIVE (×0.5, tight trail, exit at first target).
  - **Financials + utilities (`FLOW_ONLY`):** skip MF (ROE/PE/PB distort on leverage —
    fatal on a bank-heavy index). Sector proxy sanity check only (banks: ROE > 12%).
    Default ×0.75, shorter hold, tighter trail; a healthy proxy can promote to ×1.0 but
    **never to COMPOUNDER hold rules**.

RULE B: the tilt is a *sizing input*, not a displayed prediction — it emits a category,
a multiplier, and a hold/trail profile, never a probability or buy/sell verb. `missing ≠
zero`: with no MF rank visible the name is NEUTRAL (the un-tilted default), never assumed
top- or bottom-tercile. The live-fundamentals DAL feed (`fundamentals_live`) is a future
wire; today the tilt is pure over supplied values (SCR-4 rows or injected fundamentals).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from currentflow import config


class TiltKind(str, Enum):
    COMPOUNDER = "COMPOUNDER"      # top MF tercile — full size, hold through markup
    NEUTRAL = "NEUTRAL"           # mid tercile / unknown — standard
    SPECULATIVE = "SPECULATIVE"   # bottom tercile / negative EBIT — tight, exit early
    FLOW_ONLY = "FLOW_ONLY"       # financials/utilities — MF skipped, sector proxy


class HoldHorizon(str, Enum):
    THROUGH_MARKUP = "THROUGH_MARKUP"   # ride the markup (compounder)
    STANDARD = "STANDARD"
    FIRST_TARGET = "FIRST_TARGET"       # exit at first structural target (speculative)
    SHORT = "SHORT"                     # FLOW_ONLY shorter hold


class TrailProfile(str, Enum):
    WIDE = "WIDE"
    STANDARD = "STANDARD"
    TIGHT = "TIGHT"


_TRAIL_PCT = {
    TrailProfile.WIDE: config.TRAIL_WIDE,
    TrailProfile.STANDARD: config.TRAIL_STANDARD,
    TrailProfile.TIGHT: config.TRAIL_TIGHT,
}


def trail_pct(profile: TrailProfile) -> float:
    return _TRAIL_PCT[profile]


@dataclass(frozen=True, slots=True)
class FundamentalTilt:
    """Conviction & hold horizon for one candidate. Sizing input, not a claim."""

    symbol: str
    kind: TiltKind
    multiplier: float           # scales the §6 1%-risk position size
    hold: HoldHorizon
    trail: TrailProfile
    reason: str

    @property
    def trail_pct(self) -> float:
        return trail_pct(self.trail)


def is_flow_only(sector: str | None) -> bool:
    """Financials + utilities run flow+technical only (LD-7)."""
    return sector is not None and sector.strip().upper() in config.FLOW_ONLY_SECTORS


def classify_tilt(
    symbol: str,
    *,
    sector: str | None = None,
    mf_rank_pct: float | None = None,
    ev_ebit: float | None = None,
    roe: float | None = None,
) -> FundamentalTilt:
    """Assign the §7 conviction tilt.

    - `mf_rank_pct` — Magic Formula combined rank percentile (0–100, fitem 13474);
      higher is better. `None` → NEUTRAL (the un-tilted default; missing ≠ zero).
    - `ev_ebit` — EV/EBIT (fitem 2897). A negative value implies negative EBIT →
      SPECULATIVE regardless of rank (spec §7).
    - `roe` — return on equity, the FLOW_ONLY bank/utility sector proxy (§7).
    """
    if is_flow_only(sector):
        promoted = roe is not None and roe > config.BANK_ROE_PROXY_MIN
        mult = config.CONVICTION_COMPOUNDER if promoted else config.CONVICTION_FLOW_ONLY
        reason = (
            f"FLOW_ONLY ({sector}); ROE {roe:.1%} > {config.BANK_ROE_PROXY_MIN:.0%} proxy → ×{mult}"
            if promoted
            else f"FLOW_ONLY ({sector}); MF skipped, sector-proxy default ×{mult}"
        )
        # A promoted FLOW_ONLY name sizes up but NEVER earns COMPOUNDER hold rules (§7).
        return FundamentalTilt(
            symbol=symbol, kind=TiltKind.FLOW_ONLY, multiplier=mult,
            hold=HoldHorizon.SHORT, trail=TrailProfile.TIGHT, reason=reason,
        )

    # Negative EBIT is speculative regardless of the rank (§7 "bottom tercile / negative EBIT").
    if ev_ebit is not None and ev_ebit < 0:
        return FundamentalTilt(
            symbol=symbol, kind=TiltKind.SPECULATIVE, multiplier=config.CONVICTION_SPECULATIVE,
            hold=HoldHorizon.FIRST_TARGET, trail=TrailProfile.TIGHT,
            reason=f"negative EBIT (EV/EBIT {ev_ebit:.1f}) → speculative",
        )

    if mf_rank_pct is None:
        return FundamentalTilt(
            symbol=symbol, kind=TiltKind.NEUTRAL, multiplier=config.CONVICTION_NEUTRAL,
            hold=HoldHorizon.STANDARD, trail=TrailProfile.STANDARD,
            reason="no Magic Formula rank visible → neutral (missing ≠ zero)",
        )

    if mf_rank_pct >= config.MF_TOP_TERCILE_PCT:
        return FundamentalTilt(
            symbol=symbol, kind=TiltKind.COMPOUNDER, multiplier=config.CONVICTION_COMPOUNDER,
            hold=HoldHorizon.THROUGH_MARKUP, trail=TrailProfile.WIDE,
            reason=f"MF rank {mf_rank_pct:.0f}% (top tercile) → compounder, hold through markup",
        )
    if mf_rank_pct < config.MF_BOTTOM_TERCILE_PCT:
        return FundamentalTilt(
            symbol=symbol, kind=TiltKind.SPECULATIVE, multiplier=config.CONVICTION_SPECULATIVE,
            hold=HoldHorizon.FIRST_TARGET, trail=TrailProfile.TIGHT,
            reason=f"MF rank {mf_rank_pct:.0f}% (bottom tercile) → speculative, exit at first target",
        )
    return FundamentalTilt(
        symbol=symbol, kind=TiltKind.NEUTRAL, multiplier=config.CONVICTION_NEUTRAL,
        hold=HoldHorizon.STANDARD, trail=TrailProfile.STANDARD,
        reason=f"MF rank {mf_rank_pct:.0f}% (mid tercile) → neutral",
    )
