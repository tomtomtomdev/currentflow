"""Survivorship-bias measurement (slice 22, BACKTEST_PHASE0 §3.1/§6).

The store can only hold names Stockbit still serves. The `LISTED` roster
(`universe.listing`) knows which names were on the board on a past day. The gap between
those two sets is the survivorship bias — and the whole point of this module is that the
gap is **reported as a number of names (and their market-cap share) rather than silently
disappearing**: a backtest that says "8% of the 2024 universe is unrecoverable" is
usable, one that never mentions it is not (CLAUDE.md — no silent caps).

Honesty rules this module enforces:
  * **No roster → UNMEASURED, never 0%.** With no book loaded the bias is unknown; every
    share stays `None` and `line()` says so (missing ≠ zero).
  * **Unknown market cap → `cap_share=None`, never 0.** A book that carried no caps gives
    a count share only, and the caveat names the limitation.
  * **Look-ahead-safe.** Recoverability on day D is judged from bars visible at D's
    decision frame (`REPLAY_DECISION_TIME`), the same firewall as `universe.pit`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as Date
from datetime import datetime, timedelta

from currentflow import config
from currentflow.dal.models import DailyBar

# Calendar days between sampled days when measuring a span (roughly monthly — the
# listing books' own cadence is annual, so a finer sample buys nothing).
SPAN_SAMPLE_STRIDE_DAYS = 21

# How many unrecoverable names `line()` prints before summarising the remainder. The
# count is always exact; only the enumeration is abbreviated, and it says by how much.
_NAME_PREVIEW = 8

UNMEASURED_REASON = (
    "UNMEASURED — no LISTED roster loaded (see data/listings + BACKTEST_PHASE0 §3.1); "
    "the store's own names are not a universe"
)
CAP_UNKNOWN_CAVEAT = (
    "market cap unknown for every listed name in the books — count share only, "
    "no cap-weighted share"
)


@dataclass(frozen=True, slots=True)
class SurvivorshipBias:
    """What fraction of the point-in-time board the store cannot serve.

    `measured=False` means "no roster to compare against" — every share is then `None`,
    never 0.0. A single-day measurement has `start == end`.
    """

    start: Date
    end: Date
    days_sampled: int
    measured: bool
    listed: int                              # names on the board (union over sampled days)
    recoverable: int                         # ... the store can serve on every sampled day
    unrecoverable: tuple[str, ...]           # ... it cannot, on at least one sampled day
    count_share: float | None                # unrecoverable / listed
    cap_known: int                           # listed names whose book carried a cap
    unrecoverable_cap_idr: float | None      # summed over known caps only
    cap_share: float | None                  # unrecoverable cap / known-cap total
    caveat: str | None                       # what limits this measurement (never silent)

    def line(self) -> str:
        """One-line disclosure for a report header. Always states the count."""
        span = f"{self.start}" if self.start == self.end else f"{self.start}..{self.end}"
        if not self.measured:
            return f"survivorship {span}: {UNMEASURED_REASON}"
        names = ", ".join(self.unrecoverable[:_NAME_PREVIEW])
        extra = len(self.unrecoverable) - _NAME_PREVIEW
        if extra > 0:
            names += f", +{extra} more"
        cap = (
            f", {self.cap_share * 100:.1f}% of known market cap"
            if self.cap_share is not None
            else ", cap share unavailable"
        )
        return (
            f"survivorship {span}: {len(self.unrecoverable)} of {self.listed} listed "
            f"names unrecoverable ({(self.count_share or 0.0) * 100:.1f}%{cap})"
            + (f" — {names}" if names else "")
        )


def _sample_days(start: Date, end: Date) -> list[Date]:
    """Sampled measurement days: `start`, then every stride, always including `end`."""
    if end < start:
        raise ValueError(f"span end {end} precedes start {start}")
    days: list[Date] = []
    d = start
    while d < end:
        days.append(d)
        d += timedelta(days=SPAN_SAMPLE_STRIDE_DAYS)
    days.append(end)
    return days


def _visible_last_date(bars: list[DailyBar], decision_ts: datetime) -> Date | None:
    """Newest bar date published before `decision_ts` (the per-day as_of firewall)."""
    dates = [b.date for b in bars if b.as_of < decision_ts]
    return max(dates) if dates else None


def measure_bias(
    store,
    day: Date,
    *,
    bars_by_symbol: dict[str, list[DailyBar]] | None = None,
) -> SurvivorshipBias:
    """Bias on a single day. `bars_by_symbol` lets a caller that has already read the
    store (e.g. `pit_universe`) avoid a second pass; it must be read at or after `day`'s
    decision frame — the as_of filter is re-applied here regardless."""
    return measure_bias_span(store, day, day, bars_by_symbol=bars_by_symbol)


def measure_bias_span(
    store,
    start: Date,
    end: Date,
    *,
    bars_by_symbol: dict[str, list[DailyBar]] | None = None,
) -> SurvivorshipBias:
    """Bias over a span: a name unrecoverable on ANY sampled day counts as unrecoverable
    (the union — a backtest that could not have traded a name on one of its days did not
    have a complete universe on that day)."""
    days = _sample_days(start, end)
    listed: set[str] = set()
    unrecoverable: set[str] = set()

    for day in days:
        decision_ts = datetime.combine(day, config.REPLAY_DECISION_TIME)
        members = store.read_roster_members(config.LISTED_INDEX, day)
        listed.update(members)
        floor = day - timedelta(days=config.SURVIVORSHIP_RECOVERY_WINDOW_DAYS)
        for symbol in members:
            bars = (
                bars_by_symbol.get(symbol, [])
                if bars_by_symbol is not None
                else store.read_daily_bars(symbol, decision_ts)
            )
            last = _visible_last_date(bars, decision_ts)
            if last is None or last < floor:
                unrecoverable.add(symbol)

    if not listed:
        return SurvivorshipBias(
            start=start, end=end, days_sampled=len(days), measured=False,
            listed=0, recoverable=0, unrecoverable=(), count_share=None,
            cap_known=0, unrecoverable_cap_idr=None, cap_share=None,
            caveat=UNMEASURED_REASON,
        )

    caps = store.read_listing_caps(end)
    known = {s: caps[s] for s in listed if caps.get(s) is not None}
    total_cap = sum(known.values())
    missing_cap = len(listed) - len(known)
    if known and total_cap > 0:
        lost_cap = sum(v for s, v in known.items() if s in unrecoverable)
        cap_share: float | None = lost_cap / total_cap
        unrecoverable_cap: float | None = lost_cap
        caveat = (
            f"market cap unknown for {missing_cap} of {len(listed)} listed names — "
            "the cap share covers the rest only"
            if missing_cap
            else None
        )
    else:
        cap_share = unrecoverable_cap = None
        caveat = CAP_UNKNOWN_CAVEAT

    return SurvivorshipBias(
        start=start,
        end=end,
        days_sampled=len(days),
        measured=True,
        listed=len(listed),
        recoverable=len(listed) - len(unrecoverable),
        unrecoverable=tuple(sorted(unrecoverable)),
        count_share=len(unrecoverable) / len(listed),
        cap_known=len(known),
        unrecoverable_cap_idr=unrecoverable_cap,
        cap_share=cap_share,
        caveat=caveat,
    )
