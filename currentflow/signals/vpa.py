"""VPA bar-character (spec §4.1, LD-13 v1.6; slice 18) — PURE OBSERVATION.

Coulling's Volume Price Analysis turns on one reading the system does not have today:
**where the bar closed inside its own spread**, against the effort (volume) behind it.
The §4 divergence spine (`sms._divergence`) sees volume vs `|Δclose|` only — a bar that
opened at its high, sold off all session and closed on its low is, to the spine,
indistinguishable from a quiet flat bar at the same close. That close position *is* the
Wyckoff effort-vs-result read:

    narrow up bar, low volume, after a rally      → NO_DEMAND      (no appetite to support)
    narrow down bar, low volume, after a decline  → NO_SUPPLY      (nobody left to sell)
    high volume down bar closing off its low      → ABSORPTION     (demand met the supply)
    …and it makes a new low                       → STOPPING_VOLUME (the selling is being stopped)
    high volume up bar closing in its lower third → SUPPLY_PRESENT (effort up, no result)
    high volume, narrow spread, mid close         → CHURN          (effort, no result at all)
    high volume up bar closing on its high        → DEMAND_CONFIRMED (effort WITH result)

**Everything is calibrated relative to the recent bars** (`VPA_CONTEXT_BARS`, Coulling's
10–20), never to an absolute volume or an absolute rupiah spread: the same shape must read
the same on a thin lapis-2 name and on a large-cap. `missing ≠ zero`: a bar with no spread
(a locked ARA/ARB print) carries **no** positional information — it is UNREADABLE, never
"closed mid-range"; too little context is UNREADABLE, never NEUTRAL.

**Relationship to the §8 decay layer.** `distribution._no_demand` (slice 5) already flags
a no-demand bar on the *latest* day as an exit/decay warning, using a pairwise volume test
(below both prior bars). That stays exactly as it is — it is a §8 flag on an open/ARMED
name, with its own calibrated threshold and its own acceptance tests. This module is the
per-bar *character ribbon* across a window, calibrated against the recent average, feeding
§4.1 and the phase events. They agree in spirit and are deliberately not merged: changing
`_no_demand`'s rule would change §8 decay behaviour, which is outside this slice.

RULE A is untouched. This module hands the Spring / SOS / LPS / UTAD detectors a
*corroboration note* (`corroboration()`, attached by `phase._corroborate` **after** the
verdict is decided) — the C/D tradeability decision rule is not altered, by construction.

RULE B (LD-9): a bar's reading is a categorical `character` + ordinal `severity`
(INFO/WATCH/WARN salience) with digit-free copy. The ratios (close position, spread,
volume) stay on the dataclass as *measurements* for audit; the view renders the words.

§4.1: this also feeds the SMS `bar_character` candidate component — a refinement of the
divergence spine, **pinned at weight 0** (`config.SMS_WEIGHTS`), so the running score is
unchanged until the walk-forward optimizer earns it a weight under RULE B.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date as Date
from datetime import datetime
from enum import Enum

from currentflow import config
from currentflow.dal.models import DailyBar, RowStatus
from currentflow.store.db import Store

log = logging.getLogger(__name__)


class VpaSeverity(str, Enum):
    """Ordinal salience — a word, never a number (RULE B)."""

    INFO = "INFO"     # context only (neutral bar, or nothing readable)
    WATCH = "WATCH"   # a character worth reading alongside the flow
    WARN = "WARN"     # effort without result / no demand — the weakness side


_SEVERITY_RANK = {VpaSeverity.INFO: 0, VpaSeverity.WATCH: 1, VpaSeverity.WARN: 2}


class BarCharacter(str, Enum):
    NO_DEMAND = "NO_DEMAND"                  # narrow up bar, low volume, after a rally
    NO_SUPPLY = "NO_SUPPLY"                  # narrow down bar, low volume, after a decline
    ABSORPTION = "ABSORPTION"                # high volume, down/level bar closing off its low
    STOPPING_VOLUME = "STOPPING_VOLUME"      # absorption on a new low — the selling being stopped
    SUPPLY_PRESENT = "SUPPLY_PRESENT"        # high volume up bar closing in its lower third
    CHURN = "CHURN"                          # high volume, narrow spread — effort, no result
    DEMAND_CONFIRMED = "DEMAND_CONFIRMED"    # high volume wide up bar closing on its high
    NEUTRAL = "NEUTRAL"                      # nothing the framework names
    UNREADABLE = "UNREADABLE"                # no spread / no context (missing ≠ zero)


_SEVERITY = {
    BarCharacter.NO_DEMAND: VpaSeverity.WARN,
    BarCharacter.SUPPLY_PRESENT: VpaSeverity.WARN,
    BarCharacter.CHURN: VpaSeverity.WARN,
    BarCharacter.NO_SUPPLY: VpaSeverity.WATCH,
    BarCharacter.ABSORPTION: VpaSeverity.WATCH,
    BarCharacter.STOPPING_VOLUME: VpaSeverity.WATCH,
    BarCharacter.DEMAND_CONFIRMED: VpaSeverity.WATCH,
    BarCharacter.NEUTRAL: VpaSeverity.INFO,
    BarCharacter.UNREADABLE: VpaSeverity.INFO,
}

# The accumulation-side characters: supply being absorbed or exhausted. These are what
# the §4.1 candidate grades and what corroborates a Phase C test (never a gate).
DEMAND_SIDE = frozenset({
    BarCharacter.NO_SUPPLY, BarCharacter.ABSORPTION, BarCharacter.STOPPING_VOLUME,
})
SUPPLY_SIDE = frozenset({
    BarCharacter.NO_DEMAND, BarCharacter.SUPPLY_PRESENT, BarCharacter.CHURN,
})


@dataclass(frozen=True, slots=True)
class VpaBar:
    """One bar's VPA character. Categorical; the ratios are audit measurements."""

    date: Date
    character: BarCharacter
    severity: VpaSeverity
    detail: str
    up: bool | None                 # close above the prior close (VSA's up/down bar)
    close_position: float | None    # 0 = closed at the low, 1 = closed at the high
    spread_ratio: float | None      # spread vs the context average spread
    volume_ratio: float | None      # volume vs the context average volume
    effort_without_result: bool     # high volume, no/contrary price result
    result_without_effort: bool     # a wide move with no volume behind it
    available: bool                 # False = nothing readable (never a silent NEUTRAL)

    @property
    def rank(self) -> int:
        """Ordinal severity for display ordering — never a magnitude to render."""
        return _SEVERITY_RANK[self.severity]


@dataclass(frozen=True, slots=True)
class VpaReading:
    """One symbol's bar-character ribbon at one decision moment."""

    symbol: str
    decision_ts: datetime
    bars: tuple[VpaBar, ...]        # chronological; the last entry is the latest bar
    available: bool                 # False = nothing in the window was readable

    @property
    def latest(self) -> VpaBar | None:
        return self.bars[-1] if self.bars else None

    @property
    def by_date(self) -> dict[Date, VpaBar]:
        return {b.date: b for b in self.bars}

    def count(self, characters) -> int:
        wanted = frozenset(characters)
        return sum(1 for b in self.bars if b.character in wanted)

    @property
    def demand_side_bars(self) -> int:
        """Absorption / stopping / no-supply prints in the window — the accumulation tells."""
        return self.count(DEMAND_SIDE)

    @property
    def confirms_absorption(self) -> bool:
        return self.demand_side_bars > 0

    @property
    def shows_weakness(self) -> bool:
        return self.count(SUPPLY_SIDE) > 0


# --- bar hygiene ---------------------------------------------------------------------


def _complete(bars: list[DailyBar]) -> list[DailyBar]:
    """Only TRADED bars carrying full OHLC + volume. Incomplete/absent bars are dropped
    loudly — a suspended day is not a zero-volume, zero-spread bar."""
    out, dropped = [], 0
    for b in sorted(bars, key=lambda b: b.date):
        if b.status is RowStatus.TRADED and None not in (b.open, b.high, b.low, b.close, b.volume):
            out.append(b)
        else:
            dropped += 1
    if dropped:
        log.info("vpa: dropped %d incomplete/non-TRADED bar(s) (missing ≠ zero)", dropped)
    return out


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _unreadable(day: Date, detail: str) -> VpaBar:
    return VpaBar(
        date=day, character=BarCharacter.UNREADABLE, severity=VpaSeverity.INFO,
        detail=detail, up=None, close_position=None, spread_ratio=None,
        volume_ratio=None, effort_without_result=False, result_without_effort=False,
        available=False,
    )


# --- per-bar classification ----------------------------------------------------------


def classify_bar(history: list[DailyBar]) -> VpaBar:
    """Read the character of `history[-1]` from the bars before it — never after it, so
    the ribbon is look-ahead-safe bar by bar as well as at the store boundary.

    `history` must already be complete/TRADED (`_complete`)."""
    if not history:
        raise ValueError("classify_bar needs at least the bar to classify")

    bar = history[-1]
    context = history[-config.VPA_CONTEXT_BARS - 1:-1]
    if len(context) < config.VPA_MIN_CONTEXT_BARS:
        return _unreadable(
            bar.date,
            "too few prior bars to calibrate spread and volume — no character read "
            "(no base ≠ a neutral bar)",
        )

    spread = bar.high - bar.low
    if spread <= 0:
        return _unreadable(
            bar.date,
            "the bar has no spread (a locked print) — the close carries no position "
            "inside a range that does not exist",
        )

    avg_spread = _mean([b.high - b.low for b in context])
    avg_vol = _mean([float(b.volume) for b in context])
    if avg_spread <= 0 or avg_vol <= 0:
        return _unreadable(
            bar.date,
            "the recent bars carry no spread or no volume to calibrate against — "
            "nothing to read this bar as relative to",
        )

    close_pos = (bar.close - bar.low) / spread
    spread_ratio = spread / avg_spread
    vol_ratio = bar.volume / avg_vol
    prev_close = context[-1].close
    up = bar.close > prev_close
    down = bar.close < prev_close

    trend_base = history[-config.VPA_TREND_BARS - 2:-1]
    prior_move = None
    if len(trend_base) >= 2 and trend_base[0].close:
        prior_move = trend_base[-1].close / trend_base[0].close - 1
    after_rally = prior_move is not None and prior_move >= config.VPA_TREND_PCT
    after_decline = prior_move is not None and prior_move <= -config.VPA_TREND_PCT

    high_vol = vol_ratio >= config.VPA_HIGH_VOL_MULT
    low_vol = vol_ratio <= config.VPA_LOW_VOL_MULT
    narrow = spread_ratio <= config.VPA_NARROW_SPREAD_MULT
    wide = spread_ratio >= config.VPA_WIDE_SPREAD_MULT
    closed_high = close_pos >= config.VPA_CLOSE_HIGH
    closed_low = close_pos <= config.VPA_CLOSE_LOW
    makes_new_low = bar.low <= min(b.low for b in context)

    def read(character: BarCharacter, detail: str, *, effort=False, result=False) -> VpaBar:
        return VpaBar(
            date=bar.date, character=character, severity=_SEVERITY[character],
            detail=detail, up=up, close_position=round(close_pos, 3),
            spread_ratio=round(spread_ratio, 2), volume_ratio=round(vol_ratio, 2),
            effort_without_result=effort, result_without_effort=result, available=True,
        )

    if high_vol:
        if narrow:
            return read(
                BarCharacter.CHURN,
                "heavy volume on a narrow spread — effort with no price result at all "
                "(churn: stock changing hands without moving)",
                effort=True,
            )
        if closed_high and not up:
            if makes_new_low:
                return read(
                    BarCharacter.STOPPING_VOLUME,
                    "heavy volume made a new low and the bar closed near its high — "
                    "the selling is being stopped, demand met the supply",
                )
            return read(
                BarCharacter.ABSORPTION,
                "heavy volume, no higher close than the day before, yet the bar finished "
                "near its high — supply absorbed inside the range",
            )
        if closed_low and up:
            return read(
                BarCharacter.SUPPLY_PRESENT,
                "heavy volume lifted the close yet the bar finished in its lower third — "
                "effort up, no result: supply is present",
                effort=True,
            )
        if closed_high and up and wide:
            return read(
                BarCharacter.DEMAND_CONFIRMED,
                "heavy volume, a wide spread and a close on the high — "
                "effort with result: demand carried the bar",
            )
        return read(
            BarCharacter.NEUTRAL,
            "heavy volume, but the close sits where the framework reads no tell",
        )

    if low_vol:
        if up and narrow and after_rally:
            return read(
                BarCharacter.NO_DEMAND,
                "an up bar on a narrow spread and volume below the recent average, "
                "after a rally — no appetite behind the move",
            )
        if down and narrow and after_decline:
            return read(
                BarCharacter.NO_SUPPLY,
                "a down bar on a narrow spread and volume below the recent average, "
                "after a decline — the selling has dried up",
            )
        if wide:
            return read(
                BarCharacter.NEUTRAL,
                "a wide spread on volume below the recent average — "
                "a move with no effort behind it",
                result=True,
            )
    return read(
        BarCharacter.NEUTRAL,
        "no character the framework names in this context — spread, volume and close "
        "position read as ordinary here",
    )


def build_reading(
    symbol: str,
    bars: list[DailyBar],
    *,
    decision_ts: datetime,
    window: int | None = None,
) -> VpaReading:
    """Classify the last `window` (default `VPA_RIBBON_BARS`) bars. `bars` must already
    be look-ahead-safe (`store.read_daily_bars(symbol, decision_ts)`)."""
    usable = _complete(bars)
    span = config.VPA_RIBBON_BARS if window is None else window
    out: list[VpaBar] = []
    for i in range(max(1, len(usable) - span + 1), len(usable) + 1):
        out.append(classify_bar(usable[:i]))
    return VpaReading(
        symbol=symbol, decision_ts=decision_ts, bars=tuple(out),
        available=any(b.available for b in out),
    )


def analyze(
    store: Store,
    symbol: str,
    decision_ts: datetime,
    *,
    start: Date | None = None,
    end: Date | None = None,
) -> VpaReading:
    """Read look-ahead-safe bars and build the bar-character ribbon."""
    bars = store.read_daily_bars(symbol, decision_ts, start=start, end=end)
    return build_reading(symbol, bars, decision_ts=decision_ts)


# --- phase-detector corroboration (RULE A decision rule unchanged) --------------------

# Which characters speak FOR and AGAINST each Wyckoff event. Corroboration only: the
# note is attached after the classifier has already decided the phase, so it can never
# promote or demote a candidate (`phase._corroborate`).
_CONFIRMS: dict[str, frozenset[BarCharacter]] = {
    "SPRING": frozenset({BarCharacter.NO_SUPPLY, BarCharacter.STOPPING_VOLUME,
                         BarCharacter.ABSORPTION}),
    "LPS": frozenset({BarCharacter.NO_SUPPLY, BarCharacter.ABSORPTION}),
    "SOS": frozenset({BarCharacter.DEMAND_CONFIRMED}),
    "UTAD": frozenset({BarCharacter.SUPPLY_PRESENT, BarCharacter.NO_DEMAND,
                       BarCharacter.CHURN}),
    "SELLING_CLIMAX": frozenset({BarCharacter.STOPPING_VOLUME, BarCharacter.ABSORPTION}),
}
_CONTRADICTS: dict[str, frozenset[BarCharacter]] = {
    "SPRING": frozenset({BarCharacter.SUPPLY_PRESENT, BarCharacter.CHURN}),
    "LPS": frozenset({BarCharacter.SUPPLY_PRESENT, BarCharacter.NO_DEMAND}),
    "SOS": frozenset({BarCharacter.SUPPLY_PRESENT, BarCharacter.CHURN,
                      BarCharacter.NO_DEMAND}),
    "UTAD": frozenset({BarCharacter.DEMAND_CONFIRMED}),
    "SELLING_CLIMAX": frozenset({BarCharacter.NO_DEMAND}),
}

_CHARACTER_PHRASE = {
    BarCharacter.NO_DEMAND: "no demand",
    BarCharacter.NO_SUPPLY: "no supply",
    BarCharacter.ABSORPTION: "absorption",
    BarCharacter.STOPPING_VOLUME: "stopping volume",
    BarCharacter.SUPPLY_PRESENT: "supply present",
    BarCharacter.CHURN: "churn",
    BarCharacter.DEMAND_CONFIRMED: "demand confirmed",
}


def corroboration(reading: VpaReading | None, kind: str, day: Date) -> str | None:
    """The effort-vs-result note for a phase event of `kind` printed on `day`, or None.

    `None in → None out`: no reading, no readable bar on that day, or a character the
    framework does not tie to the event yields nothing to attach. Digit-free by design —
    a phase event is a gate verdict, and RULE B keeps magnitudes off it."""
    if reading is None:
        return None
    bar = reading.by_date.get(day)
    if bar is None or not bar.available:
        return None
    phrase = _CHARACTER_PHRASE.get(bar.character)
    if phrase is None:
        return None
    if bar.character in _CONFIRMS.get(kind, frozenset()):
        return f"VPA confirms: {phrase} on the bar (close position within its spread)"
    if bar.character in _CONTRADICTS.get(kind, frozenset()):
        return f"VPA does not confirm: {phrase} on the bar (close position within its spread)"
    return None


# --- §4.1 candidate component ---------------------------------------------------------


def subscore(reading: VpaReading | None) -> tuple[float, bool]:
    """(strength, available) for the §4.1 `bar_character` candidate — a refinement of the
    divergence spine, which today reads effort (volume) against `|Δclose|` but never
    against the close's position in the spread.

    Graded on the accumulation side only: absorption / stopping volume / no-supply prints
    in the window are the frameworks' "supply is being taken" tells. The weakness side is
    a *warning*, surfaced by the observation ribbon, and is never negative strength inside
    the simplex. No readable bar → `available=False` (never a silent 0-strength)."""
    if reading is None or not reading.available:
        return 0.0, False
    strength = reading.demand_side_bars / config.VPA_FULL_CREDIT_BARS
    return (0.0 if strength < 0 else 1.0 if strength > 1 else strength), True
