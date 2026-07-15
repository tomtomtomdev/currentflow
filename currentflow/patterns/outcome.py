"""Pattern outcome resolver (slice 21, PATTERN-CATALOG-SPEC §5.6).

Resolves OPEN instances by reading forward bars only. Terminal outcomes (a name that
suspends / FCA's / delists inside the horizon — detected as bars stopping while the
market keeps trading) are recorded as such and COUNTED, never dropped. Instances with
too little forward history yet stay OPEN (never forced to MISS).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date as Date
from datetime import datetime

from currentflow.dal.models import RowStatus
from currentflow.patterns.catalog import OutcomeSpec, Pattern
from currentflow.store.schema import PatternInstanceRow

TERMINAL_DELIST = "TERMINAL_DELIST"


def resolve_instance(
    traded: list,
    flag_date: Date,
    horizon_days: int,
    spec: OutcomeSpec,
    market_latest: Date | None,
) -> tuple[str, Date | None]:
    """Resolve one instance against a date-sorted TRADED-bar list. Entry is the last
    close strictly before the flag (the signal day); the forward window is the first
    `horizon_days` bars from the flag day onward."""
    prior = [b for b in traded if b.date < flag_date and b.close]
    if not prior:
        return ("OPEN", None)
    entry = prior[-1].close
    fwd = [b for b in traded if b.date >= flag_date and b.close][:horizon_days]

    for b in fwd:
        ret = b.close / entry - 1.0
        if spec.direction == "up" and ret >= spec.target:
            return ("HIT", b.date)
        if spec.direction == "down" and ret <= spec.target:
            return ("HIT", b.date)

    if len(fwd) >= horizon_days:
        return ("MISS", fwd[-1].date)

    # Not enough forward bars: terminal only if the name stopped while the market went on.
    name_last = traded[-1].date if traded else flag_date
    if market_latest is not None and name_last < market_latest:
        return (TERMINAL_DELIST, name_last)
    return ("OPEN", None)


def resolve_open(store, pattern: Pattern, *, now: datetime) -> int:
    """Resolve every OPEN instance of `pattern`, writing back the resolved outcomes.
    Returns the number newly resolved. Deterministic over an unchanged store."""
    open_insts = store.read_pattern_instances(
        pattern.pattern_id, pattern.version, outcome="OPEN"
    )
    if not open_insts:
        return 0

    market_latest = store.latest_bar_date()
    bars_cache: dict[str, list] = {}
    resolved: list[PatternInstanceRow] = []
    for inst in open_insts:
        traded = bars_cache.get(inst.symbol)
        if traded is None:
            raw = store.read_daily_bars(inst.symbol, now)
            traded = sorted(
                (b for b in raw if b.status is RowStatus.TRADED), key=lambda b: b.date
            )
            bars_cache[inst.symbol] = traded
        outcome, resolved_on = resolve_instance(
            traded, inst.flag_date, inst.horizon_days, pattern.outcome, market_latest
        )
        if outcome != "OPEN":
            resolved.append(replace(inst, outcome=outcome, resolved_on=resolved_on, as_of=now))

    store.write_pattern_instances(resolved)
    return len(resolved)
