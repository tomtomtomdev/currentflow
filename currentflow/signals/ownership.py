"""Institutional-ownership delta (spec §4.1, LD-13 v1.6; slice 17) — PURE OBSERVATION.

The KSEI shareholder-composition feed has been fetched and stored since slice 3 but was
consumed only by the UI. This module wires it into detection as the frameworks read it
(Bandarmology §2/§10 — institutions are the real, slow bandar; Wyckoff's Composite Man):

    rising foreign/institutional share across the range   ↔ slow-money accumulation
    share FALLING while price is marked flat/up           ↔ distribution dressed as strength

**Data cadence is the constraint.** KSEI publishes monthly with an undisclosed lag, so
`as_of` is the fetch time (conservative by construction) and the composition describes a
*range*, never a day. Everything here is therefore a slow confirmation:

  - `missing ≠ zero` — no composition, one lone slice, or an absent price context is
    UNAVAILABLE (`available=False`), never a flat/neutral reading scored as 0-strength.
  - **Stale degrades to neutral.** A composition older than `OWNERSHIP_STALE_DAYS`
    cannot flag distribution — a stale falling series says nothing about today's markup.
  - It **never hard-rejects**: the §5 distribution veto takes this only as a corroborator
    (`corroborates_distribution`), which strengthens a veto that already fired and can
    never fire one alone (`veto._distribution`).

RULE B (LD-9): the reading is a categorical `kind` + ordinal `severity` (INFO/WATCH/WARN)
— no score, no probability, no buy/sell verb. `severity` is *salience*, not direction: the
kind says which way the composition moved, the severity says how loudly it speaks. The
raw pp measurement is kept on the report for audit (a measurement, like a z-score); the
view renders the categorical reading only (`ui.foreign_flow_view.ownership_panel`).

§4.1: this also feeds the SMS `ownership_delta` candidate component — **pinned at weight
0** (`config.SMS_WEIGHTS`), so the running score is unchanged until the walk-forward
optimizer earns it a weight under RULE B.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date as Date
from datetime import datetime
from enum import Enum

from currentflow import config
from currentflow.dal.models import DailyBar, OwnershipSlice, RowStatus
from currentflow.store.db import Store

log = logging.getLogger(__name__)


class OwnershipSeverity(str, Enum):
    """Ordinal salience — a word, never a number (RULE B)."""

    INFO = "INFO"     # context only (flat, stale, or no composition on file)
    WATCH = "WATCH"   # a material composition move worth reading alongside the flow
    WARN = "WARN"     # composition falling while price is marked flat/up (distribution tell)


_SEVERITY_RANK = {OwnershipSeverity.INFO: 0, OwnershipSeverity.WATCH: 1, OwnershipSeverity.WARN: 2}


class OwnershipKind(str, Enum):
    ACCUMULATION_CONFIRMED = "ACCUMULATION_CONFIRMED"  # share rising across the range
    DISTRIBUTION_TELL = "DISTRIBUTION_TELL"            # share falling while marked flat/up
    OWNERSHIP_EASING = "OWNERSHIP_EASING"              # share falling, price falling too (exit, not dressing)
    OWNERSHIP_FLAT = "OWNERSHIP_FLAT"                  # no material change
    STALE_COMPOSITION = "STALE_COMPOSITION"            # latest slice too old to describe this range
    NO_COMPOSITION = "NO_COMPOSITION"                  # nothing visible at decision_ts (missing ≠ zero)


@dataclass(frozen=True, slots=True)
class OwnershipDelta:
    """One symbol's KSEI composition reading at one decision moment. Categorical."""

    symbol: str
    decision_ts: datetime
    kind: OwnershipKind
    severity: OwnershipSeverity
    detail: str
    slices_used: int
    first_date: Date | None
    last_date: Date | None
    delta_pp: float | None        # Δ foreign share, percentage points (measurement, audit)
    latest_pct: float | None      # latest visible foreign share
    age_days: int | None          # composition age at decision_ts
    stale: bool
    price_change: float | None    # price change across the composition span (None = no context)
    available: bool               # False = composition missing/stale — never scored as 0-strength

    @property
    def corroborates_distribution(self) -> bool:
        """Feeds the §5 distribution-dressed veto as a *corroborator* only — it
        strengthens a veto that already fired and never fires one on its own (the
        monthly cadence is far too coarse to hard-reject a name)."""
        return self.kind is OwnershipKind.DISTRIBUTION_TELL and not self.stale

    @property
    def confirms_accumulation(self) -> bool:
        return self.kind is OwnershipKind.ACCUMULATION_CONFIRMED

    @property
    def rank(self) -> int:
        """Ordinal severity for display ordering — never a magnitude to render."""
        return _SEVERITY_RANK[self.severity]


def _usable(slices: tuple[OwnershipSlice, ...]) -> list[OwnershipSlice]:
    """Chronological slices carrying a foreign share. A slice with no percentage is
    dropped and logged — an unpublished composition is not a 0% holding."""
    dropped = sum(1 for s in slices if s.foreign_pct is None)
    if dropped:
        log.info(
            "ownership: dropped %d KSEI slice(s) with no foreign_pct (missing ≠ zero)", dropped
        )
    return sorted((s for s in slices if s.foreign_pct is not None), key=lambda s: s.date)


def _price_change(bars: list[DailyBar], since: Date) -> float | None:
    """Close-to-close change from the first complete bar on/after `since` to the last.
    `None` when the window holds fewer than two complete bars — no price context, so no
    distribution tell can be claimed (missing ≠ zero)."""
    complete = [
        b for b in sorted(bars, key=lambda b: b.date)
        if b.status is RowStatus.TRADED and b.close
    ]
    window = [b for b in complete if b.date >= since]
    if len(window) < 2:
        return None
    return window[-1].close / window[0].close - 1


def _unavailable(
    symbol: str, decision_ts: datetime, kind: OwnershipKind, detail: str, **extra
) -> OwnershipDelta:
    base = dict(
        slices_used=0, first_date=None, last_date=None, delta_pp=None, latest_pct=None,
        age_days=None, stale=False, price_change=None,
    )
    base.update(extra)
    return OwnershipDelta(
        symbol=symbol, decision_ts=decision_ts, kind=kind,
        severity=OwnershipSeverity.INFO, detail=detail, available=False, **base,
    )


def build_delta(
    symbol: str,
    slices: tuple[OwnershipSlice, ...],
    *,
    decision_ts: datetime,
    bars: list[DailyBar] | None = None,
) -> OwnershipDelta:
    """Read the composition change across the accumulation window. `slices` must already
    be look-ahead-safe (`store.read_ksei_ownership(symbol, decision_ts)`)."""
    usable = _usable(slices)
    if len(usable) < 2:
        return _unavailable(
            symbol, decision_ts, OwnershipKind.NO_COMPOSITION,
            "fewer than two KSEI composition slices visible — no delta to read "
            "(not yet published ≠ no change)",
            slices_used=len(usable),
            latest_pct=usable[-1].foreign_pct if usable else None,
            last_date=usable[-1].date if usable else None,
        )

    window = usable[-config.OWNERSHIP_WINDOW_SLICES:]
    first, last = window[0], window[-1]
    age_days = (decision_ts.date() - last.date).days
    stale = age_days > config.OWNERSHIP_STALE_DAYS
    if stale:
        return _unavailable(
            symbol, decision_ts, OwnershipKind.STALE_COMPOSITION,
            f"latest KSEI composition is {age_days}d old (> {config.OWNERSHIP_STALE_DAYS}d) — "
            "too stale to describe this range; reads neutral",
            slices_used=len(window), first_date=first.date, last_date=last.date,
            delta_pp=round(last.foreign_pct - first.foreign_pct, 2),
            latest_pct=last.foreign_pct, age_days=age_days, stale=True,
        )

    delta_pp = round(last.foreign_pct - first.foreign_pct, 2)
    change = _price_change(bars or [], first.date)
    span = f"{first.date.isoformat()}→{last.date.isoformat()}"
    common = dict(
        slices_used=len(window), first_date=first.date, last_date=last.date,
        delta_pp=delta_pp, latest_pct=last.foreign_pct, age_days=age_days, stale=False,
        price_change=None if change is None else round(change, 4),
    )

    if abs(delta_pp) < config.OWNERSHIP_MATERIAL_PP:
        return OwnershipDelta(
            symbol=symbol, decision_ts=decision_ts, kind=OwnershipKind.OWNERSHIP_FLAT,
            severity=OwnershipSeverity.INFO,
            detail=(f"foreign/institutional share unchanged within noise "
                    f"({delta_pp:+.2f}pp, {span})"),
            available=True, **common,
        )

    if delta_pp > 0:
        return OwnershipDelta(
            symbol=symbol, decision_ts=decision_ts,
            kind=OwnershipKind.ACCUMULATION_CONFIRMED, severity=OwnershipSeverity.WATCH,
            detail=(f"foreign/institutional share rising {delta_pp:+.2f}pp across the range "
                    f"({span}) — slow-money accumulation confirmation"),
            available=True, **common,
        )

    # Falling. The distribution tell needs price context: shares leaving while the price
    # is held flat or marked up. Without a price window we cannot claim it (missing ≠ zero).
    if change is None:
        return OwnershipDelta(
            symbol=symbol, decision_ts=decision_ts, kind=OwnershipKind.OWNERSHIP_EASING,
            severity=OwnershipSeverity.WATCH,
            detail=(f"foreign/institutional share falling {delta_pp:+.2f}pp ({span}) — "
                    "no price context across the span, so no distribution read"),
            available=True, **common,
        )
    if change >= config.OWNERSHIP_MARKUP_PCT:
        return OwnershipDelta(
            symbol=symbol, decision_ts=decision_ts, kind=OwnershipKind.DISTRIBUTION_TELL,
            severity=OwnershipSeverity.WARN,
            detail=(f"foreign/institutional share falling {delta_pp:+.2f}pp while price is "
                    f"marked {change:+.1%} ({span}) — supply leaving into strength"),
            available=True, **common,
        )
    return OwnershipDelta(
        symbol=symbol, decision_ts=decision_ts, kind=OwnershipKind.OWNERSHIP_EASING,
        severity=OwnershipSeverity.WATCH,
        detail=(f"foreign/institutional share falling {delta_pp:+.2f}pp with price down "
                f"{change:+.1%} ({span}) — holders leaving a falling market, not a markup"),
        available=True, **common,
    )


def analyze(
    store: Store,
    symbol: str,
    decision_ts: datetime,
    *,
    start: Date | None = None,
    end: Date | None = None,
) -> OwnershipDelta:
    """Read the look-ahead-safe composition + price context and build the reading."""
    slices = tuple(store.read_ksei_ownership(symbol, decision_ts))
    bars = store.read_daily_bars(symbol, decision_ts, start=start, end=end)
    return build_delta(symbol, slices, decision_ts=decision_ts, bars=bars)


def subscore(delta: OwnershipDelta | None) -> tuple[float, bool]:
    """(strength, available) for the §4.1 `ownership_delta` candidate component.

    Graded on the accumulation side only — a rising institutional share is the signal the
    frameworks credit; the falling side is a *warning*, handled by the veto corroborator
    and the observation panel, and is never negative strength inside the simplex. Missing
    or stale composition → `available=False` (never a silent 0-strength).
    """
    if delta is None or not delta.available or delta.delta_pp is None:
        return 0.0, False
    strength = delta.delta_pp / config.OWNERSHIP_FULL_CREDIT_PP
    return (0.0 if strength < 0 else 1.0 if strength > 1 else strength), True
