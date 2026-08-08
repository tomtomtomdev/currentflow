"""Time Machine — rewind the whole terminal to a past decision moment (read path only).

Every store read already takes a `decision_ts` and returns only rows with
`as_of < decision_ts` (`store/db.py` — the look-ahead firewall). This module turns that
parameter into an operator control: pick a day D and every view re-reads at
`combine(D, REPLAY_DECISION_TIME)` — D's pre-open moment, the same convention
`universe/pit.py` and `validation/runner.py` use for *acting on* day D. So the newest
visible bar is D-1's EOD and the newest visible broker summary is D-1's (published
D 09:00, LD-5). Anything stamped later stays invisible: the terminal shows what was
knowable then, not what is known now.

`as_of` is derived from the trading day, not from ingest wall-clock (`dal/timing.py`:
OHLCV D 16:15, broker D+1 09:00), so a backfilled history rewinds honestly.

**Read-path parameter only.** No signal behavior changes, so RULE A and RULE B are
untouched — the phase gate still hard-gates before scoring, and an unvalidated module
still withholds its number. Writes never rewind either: ingest/bootstrap, the Fast-Mode
paper book, the catalog seed, and the login session all run at real wall-clock now.

Fail loud, never silently widen (CLAUDE.md — no silent caps, REGIME.md §1):
  * a day before the regime start is REFUSED, not clamped to the boundary;
  * a day after today is REFUSED (there is no future to read).

What cannot be rewound is named, never faked (`caveats`) — same posture as
`pit.PitUniverse.unchecked_legs`.

Pure module: no Streamlit import. The session-state layer and the widgets live in
`ui/app.py`; everything here is a function of its arguments so it is directly testable.
"""

from __future__ import annotations

from datetime import date as Date
from datetime import datetime, timedelta

from currentflow import config
from currentflow.universe.pit import UNCHECKED_GATE_LEGS

# The rewind floor: the earlier of the two per-track regime starts (REGIME.md §1). A read
# before this is outside the current IDX regime — a bug, not a bigger sample, so it fails
# loud here rather than quietly returning pre-regime rows.
EARLIEST_DAY: Date = min(config.regime_start("A"), config.regime_start("B"))


def decision_ts_for(day: Date) -> datetime:
    """The decision moment for a rewound `day`: its pre-open (D 09:15 WIB).

    Same convention as `validation.runner._decision_ts` and `universe.pit` — "what the
    engine would see acting on `day`", i.e. D-1's bar and D-1's broker summary. (Note the
    deliberate contrast with `replay.frame_decision_ts`, which answers the other question:
    when day X's own data became knowable, D+1 09:15.)
    """
    return datetime.combine(day, config.REPLAY_DECISION_TIME)


def resolve(day: Date | None, *, now: datetime) -> datetime:
    """The `decision_ts` every view should read at: real `now` when live, the rewound
    pre-open moment when a day is set. The single definition — callers never branch."""
    return now if day is None else decision_ts_for(day)


def rejection(day: Date, *, today: Date) -> str | None:
    """Why `day` cannot be used, or None when it is usable. Fail-loud message text —
    the caller refuses the day and keeps the previous view, never substitutes a
    silently clamped one."""
    if day < EARLIEST_DAY:
        return (
            f"{day} is before the regime start ({EARLIEST_DAY}) — outside the current IDX "
            "regime (REGIME.md §1). Reaching further back is a bug, not a bigger sample."
        )
    if day > today:
        return f"{day} is in the future (today is {today}) — there is nothing to read yet."
    return None


def last_visible_day(day: Date) -> Date:
    """The newest trading day whose data is fully knowable at `day`'s pre-open — the day
    before. Calendar-based (weekends/holidays simply have no bars); it bounds what the
    replay scrub and the as-of stamp may show."""
    return day - timedelta(days=1)


def is_weekend(day: Date) -> bool:
    """True for Sat/Sun. Not an error — a weekend rewind just shows Friday's data — but
    worth saying out loud so an empty-looking terminal is never mistaken for missing data.
    (IDX holidays remain an acknowledged calendar gap, see scheduler/calendar.py.)"""
    return day.weekday() >= 5


def caveats(day: Date, *, roster_covers: bool) -> tuple[str, ...]:
    """The surfaces that do NOT rewind with the read path, named rather than faked.

    `roster_covers` — whether `store.roster_covers(day)` finds a point-in-time index
    roster period covering `day`; without one, Track A membership is unknowable for that
    day and every name resolves on the ADV leg alone (→ Track B, missing ≠ zero).
    """
    out = [
        "Index membership: the live `symbol_index` snapshot is stamped at ingest, so it "
        "cannot rewind. "
        + (
            f"Using the point-in-time roster effective on {day} (`index_roster_pit`)."
            if roster_covers
            else f"No roster period covers {day} — every name resolves on the ADV leg "
            "alone, i.e. Track B. Load rosters with `./run.sh backfill --rosters`."
        ),
        "§3 gate legs with no historical sink are unchecked, not assumed to pass: "
        + ", ".join(UNCHECKED_GATE_LEGS)
        + " (same legs `universe.pit` names).",
        "Live records do not rewind: the Fast-Mode paper book, module validation state "
        "(RULE B), and pattern-catalog base rates are as of now, not as of this day.",
        "Survivorship: names Stockbit no longer serves were never ingested, so they are "
        "absent from this view however far back it is set.",
    ]
    if is_weekend(day):
        out.append(
            f"{day} is a weekend — the newest visible bar is the preceding Friday's. "
            "Sparse panels here are the calendar, not a gap."
        )
    return tuple(out)


def label(day: Date | None) -> str:
    """The mode word for the top bar / banner: never ambiguous about which one is live."""
    return "LIVE" if day is None else f"TIME MACHINE · {day}"
