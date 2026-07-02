"""Universe gate (spec §3, LOCKED) — the hard floor + Track A/B assignment.

Pure evaluation over look-ahead-safe inputs: the caller reads bars/broker rows from
the store with a `decision_ts` (the store enforces `as_of < decision_ts`) and passes
them in. Every rejection carries its reasons — no silent caps; rejects are logged.

RULE A/B note: passing the gate makes a name *eligible for analysis*, nothing more.
No score, no phase, no claim is produced here.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date as Date
from datetime import timedelta
from enum import Enum

from currentflow import config
from currentflow.dal.models import BoardType, CorpAction, DailyBar, RowStatus, SymbolInfo
from currentflow.store.integrity import CoverageReport
from currentflow.universe.bands import BandCheck, check_pinned

log = logging.getLogger(__name__)


class Track(str, Enum):
    A = "A"  # LQ45/IDX80 member AND ADV ≥ IDR 25 bn → foreign-flow-reliable
    B = "B"  # passes hard floor, not Track A → broker-concentration-reliable


class GateFailure(str, Enum):
    INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"      # IPO < 60 trading days (§3)
    DATA_GAP = "DATA_GAP"                              # gap in window — missing ≠ zero
    NO_SIGNAL_DAY_BAR = "NO_SIGNAL_DAY_BAR"            # no bar for the signal day
    LOW_LIQUIDITY = "LOW_LIQUIDITY"                    # ADV20 < IDR 10 bn
    PRICE_FLOOR = "PRICE_FLOOR"                        # close < IDR 100
    SUSPENDED = "SUSPENDED"
    PINNED_CLOSE = "PINNED_CLOSE"                      # closed ARA/ARB-pinned
    BROKER_SUMMARY_INCOMPLETE = "BROKER_SUMMARY_INCOMPLETE"
    CORP_ACTION_WINDOW = "CORP_ACTION_WINDOW"          # corp action within ±5 days
    INCOMPLETE_DATA = "INCOMPLETE_DATA"                # traded bar missing value/close


@dataclass(frozen=True, slots=True)
class GateDecision:
    symbol: str
    day: Date
    passed: bool
    failures: tuple[GateFailure, ...]
    adv20: float | None          # 20-day avg daily value traded (IDR)
    close: float | None
    track: Track | None          # assigned only when passed
    band: BandCheck | None


def _corp_action_dates(actions: list[CorpAction]) -> list[Date]:
    out: list[Date] = []
    for a in actions:
        out.extend(d for d in (a.ex_date, a.recording_date) if d is not None)
    return out


def evaluate_gate(
    symbol: str,
    day: Date,
    bars: list[DailyBar],
    *,
    info: SymbolInfo,
    corp_actions: list[CorpAction],
    board: BoardType,
    coverage: CoverageReport,
    broker_summary_complete: bool,
    trading_days_since_ipo: int | None = None,
) -> GateDecision:
    """Apply the §3 hard floor for `symbol` on signal day `day`.

    `bars` must be look-ahead-safe history up to and including `day`, ordered by date.
    `coverage` classifies the ADV window; a GAP there is a data problem, never zero.
    """
    failures: list[GateFailure] = []
    bars = sorted(bars, key=lambda b: b.date)

    # History floor — also the IPO < 60 trading days rule (§3).
    history = trading_days_since_ipo if trading_days_since_ipo is not None else len(bars)
    if history < config.MIN_HISTORY_TRADING_DAYS:
        failures.append(GateFailure.INSUFFICIENT_HISTORY)

    # Missing data is never zero flow: an unexplained gap in the window is a reject.
    if coverage.has_gaps:
        failures.append(GateFailure.DATA_GAP)

    signal_bar = next((b for b in bars if b.date == day), None)
    close: float | None = None
    adv20: float | None = None
    band: BandCheck | None = None

    if signal_bar is None:
        failures.append(GateFailure.NO_SIGNAL_DAY_BAR)
    else:
        close = signal_bar.close

        # ADV over the last 20 available trading days ending at `day`. A NO_TRADES
        # day is a genuine zero; a TRADED bar with no value is broken data.
        window = [b for b in bars if b.date <= day][-config.ADV_WINDOW_DAYS :]
        values: list[float] = []
        for b in window:
            if b.status is RowStatus.NO_TRADES:
                values.append(0.0)
            elif b.value is None:
                failures.append(GateFailure.INCOMPLETE_DATA)
                break
            else:
                values.append(b.value)
        else:
            adv20 = sum(values) / len(values) if values else None

        if adv20 is not None and adv20 < config.ADV_FLOOR_IDR:
            failures.append(GateFailure.LOW_LIQUIDITY)

        if close is None:
            if GateFailure.INCOMPLETE_DATA not in failures:
                failures.append(GateFailure.INCOMPLETE_DATA)
        elif close < config.PRICE_FLOOR_IDR:
            failures.append(GateFailure.PRICE_FLOOR)

        # ARA/ARB-pinned close → no fillable band on the signal day.
        prev = next((b for b in reversed(bars) if b.date < day and b.close), None)
        if close is not None and prev is not None and prev.close:
            band = check_pinned(
                close, prev.close, board, trading_days_since_ipo=trading_days_since_ipo
            )
            if band.pinned:
                failures.append(GateFailure.PINNED_CLOSE)

    if info.suspended:
        failures.append(GateFailure.SUSPENDED)

    if not broker_summary_complete:
        failures.append(GateFailure.BROKER_SUMMARY_INCOMPLETE)

    window_days = timedelta(days=config.CORP_ACTION_WINDOW_DAYS)
    if any(abs(d - day) <= window_days for d in _corp_action_dates(corp_actions)):
        failures.append(GateFailure.CORP_ACTION_WINDOW)

    passed = not failures
    track: Track | None = None
    if passed:
        is_index_member = bool(set(info.indexes) & config.TRACK_A_INDEXES)
        track = (
            Track.A
            if is_index_member and adv20 is not None and adv20 >= config.ADV_TRACK_A_IDR
            else Track.B
        )
    else:
        log.info(
            "universe gate reject %s @ %s: %s",
            symbol, day, ", ".join(f.value for f in failures),
        )

    return GateDecision(
        symbol=symbol,
        day=day,
        passed=passed,
        failures=tuple(failures),
        adv20=adv20,
        close=close,
        track=track,
        band=band,
    )
