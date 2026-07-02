"""Foreign Flow Dashboard (spec §9) — PURE OBSERVATION.

NBSA (net foreign buy/sell) magnitude & persistence, flow-reversal detection,
foreign-vs-domestic split, and the KSEI monthly ownership overlay — at stock level
plus market/sector aggregates.

RULE B: this module renders observations only. The statistics here (multiples,
z-scores, turnover shares, %-of-float) are **measurements of the flow**, not
predictions — no score, no probability, no buy/sell claim.

Identity note: on a single stock every foreign net buy is, by construction, a
domestic net sell (two sides to every trade), so the *net* split always mirrors.
The informative split is participation: foreign share of turnover.

`missing ≠ zero`: bars whose foreign fields are absent are dropped loudly from the
flow series, never read as zero flow. A NO_TRADES day's genuine 0 is kept.
"""

from __future__ import annotations

import logging
import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import date as Date
from datetime import datetime

from currentflow import config
from currentflow.dal.models import DailyBar, OwnershipSlice, Side
from currentflow.store.db import Store

log = logging.getLogger(__name__)

_UNKNOWN_SECTOR = "UNKNOWN"


# --- daily series ----------------------------------------------------------------


def daily_net_foreign(bars: list[DailyBar]) -> dict[Date, float]:
    """{date: net_foreign IDR}. Bars with an absent net are dropped and logged."""
    dropped = 0
    out: dict[Date, float] = {}
    for b in bars:
        if b.net_foreign is None:
            dropped += 1
            continue
        out[b.date] = b.net_foreign
    if dropped:
        log.warning(
            "foreign_flow: dropped %d bar(s) with unknown net_foreign (missing ≠ zero)",
            dropped,
        )
    return out


# --- persistence & reversal --------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FlowReversal:
    """The most recent sign flip of daily NBSA inside the window."""

    side: Side              # direction flow reversed TO (BUY = net buying)
    date: Date              # first day of the current same-sign run
    persistence_days: int   # length of that run, ending at the window end


def current_run(daily: dict[Date, float]) -> tuple[Side | None, int, Date | None]:
    """(side, length, first_day) of the same-sign run ending at the most recent day.

    Zero-flow days break the run (a real 0 is neither buying nor selling).
    """
    days = sorted(daily, reverse=True)
    if not days or daily[days[0]] == 0:
        return None, 0, None
    side = Side.BUY if daily[days[0]] > 0 else Side.SELL
    run_len, first = 0, None
    for d in days:
        v = daily[d]
        if (v > 0) != (side is Side.BUY) or v == 0:
            break
        run_len += 1
        first = d
    return side, run_len, first


def detect_reversal(daily: dict[Date, float]) -> FlowReversal | None:
    """The current run, reported as a reversal only if an opposite-sign (or zero)
    day precedes it inside the window — an unbroken run is persistence, not a flip."""
    side, run_len, first = current_run(daily)
    if side is None or run_len == len(daily):
        return None
    return FlowReversal(side=side, date=first, persistence_days=run_len)


# --- snapshot ------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ForeignFlowSnapshot:
    """Window observation of foreign flow for one symbol. No score, no claim."""

    symbol: str
    start: Date
    end: Date
    decision_ts: datetime
    daily_net: dict[Date, float]                 # NBSA per day, IDR
    net_last: float | None                       # most recent observed day
    cum_window: float                            # NBSA summed over the whole window
    cum_5d: float | None                         # last FF_CUM_DAYS observed days
    persistence_days: int                        # current same-sign run length
    persistence_side: Side | None
    reversal: FlowReversal | None
    vs_20d_avg: float | None                     # |net_last| / mean|net| of prior days
    zscore_20d: float | None                     # (net_last − mean) / pstdev, prior days
    avg_window_used: int                         # prior days actually available (≤ 20)
    foreign_turnover_share: float | None         # (fbuy+fsell)/(2·value), last day
    nbsa_pct_of_float: float | None              # cum_window / (free_float%·mcap) · 100
    ksei: tuple[OwnershipSlice, ...]             # monthly overlay, look-ahead-safe

    @property
    def cumulative(self) -> tuple[tuple[Date, float], ...]:
        total, out = 0.0, []
        for d in sorted(self.daily_net):
            total += self.daily_net[d]
            out.append((d, total))
        return tuple(out)


def _vs_avg_and_zscore(
    daily: dict[Date, float], window: int
) -> tuple[float | None, float | None, int]:
    """Magnitude multiple + z-score of the last day vs the prior ≤`window` days."""
    days = sorted(daily)
    if len(days) < 2:
        return None, None, 0
    prior = [daily[d] for d in days[-(window + 1):-1]]
    last = daily[days[-1]]
    mean_abs = statistics.mean(abs(v) for v in prior)
    vs_avg = None if mean_abs == 0 else abs(last) / mean_abs
    z = None
    if len(prior) >= 2:
        spread = statistics.pstdev(prior)
        if spread > 0:
            z = (last - statistics.mean(prior)) / spread
    return vs_avg, z, len(prior)


def build_snapshot(
    symbol: str,
    bars: list[DailyBar],
    *,
    decision_ts: datetime,
    ksei: tuple[OwnershipSlice, ...] = (),
    free_float_pct: float | None = None,
    market_cap: float | None = None,
) -> ForeignFlowSnapshot:
    daily = daily_net_foreign(bars)
    days = sorted(daily)
    start, end = (days[0], days[-1]) if days else (Date.min, Date.min)

    side, run_len, _ = current_run(daily)
    vs_avg, zscore, avg_used = _vs_avg_and_zscore(daily, config.FF_AVG_WINDOW_DAYS)

    last_bar = next((b for b in reversed(sorted(bars, key=lambda b: b.date))
                     if b.date == end), None)
    turnover_share = None
    if (
        last_bar is not None
        and last_bar.foreign_buy is not None
        and last_bar.foreign_sell is not None
        and last_bar.value
    ):
        turnover_share = (last_bar.foreign_buy + last_bar.foreign_sell) / (2 * last_bar.value)

    cum_window = sum(daily.values())
    pct_of_float = None
    if free_float_pct and market_cap:
        float_value = free_float_pct / 100 * market_cap
        if float_value > 0:
            pct_of_float = cum_window / float_value * 100

    return ForeignFlowSnapshot(
        symbol=symbol,
        start=start,
        end=end,
        decision_ts=decision_ts,
        daily_net=daily,
        net_last=daily[days[-1]] if days else None,
        cum_window=cum_window,
        cum_5d=sum(daily[d] for d in days[-config.FF_CUM_DAYS:]) if days else None,
        persistence_days=run_len,
        persistence_side=side,
        reversal=detect_reversal(daily),
        vs_20d_avg=vs_avg,
        zscore_20d=zscore,
        avg_window_used=avg_used,
        foreign_turnover_share=turnover_share,
        nbsa_pct_of_float=pct_of_float,
        ksei=ksei,
    )


def analyze(
    store: Store,
    symbol: str,
    decision_ts: datetime,
    *,
    start: Date | None = None,
    end: Date | None = None,
) -> ForeignFlowSnapshot:
    """Read look-ahead-safe bars + KSEI overlay + float context, build the snapshot."""
    bars = store.read_daily_bars(symbol, decision_ts, start=start, end=end)
    ksei = tuple(store.read_ksei_ownership(symbol, decision_ts))
    scr0 = store.read_scr0_latest(symbol, decision_ts)
    if scr0 is None:
        log.info("foreign_flow %s: no SCR-0 row visible — %%-of-float unavailable", symbol)
    return build_snapshot(
        symbol,
        bars,
        decision_ts=decision_ts,
        ksei=ksei,
        free_float_pct=scr0.free_float if scr0 else None,
        market_cap=scr0.market_cap if scr0 else None,
    )


# --- market / sector tide -------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TideRow:
    """Aggregate NBSA for one scope (MARKET, a sector, or UNKNOWN) on one day."""

    scope: str
    net_foreign: float
    symbols: int


def market_tide(
    store: Store,
    symbols: list[str],
    decision_ts: datetime,
    *,
    day: Date,
    sector_map: dict[str, str] | None = None,
) -> list[TideRow]:
    """NBSA summed across `symbols` for `day`: one MARKET row, plus per-sector rows
    when a sector map is provided. Aggregates only what is ingested — symbols with
    no visible net for the day are skipped and logged (no silent caps)."""
    by_scope: dict[str, list[float]] = defaultdict(list)
    skipped = 0
    for sym in symbols:
        bars = store.read_daily_bars(sym, decision_ts, start=day, end=day)
        net = daily_net_foreign(bars).get(day)
        if net is None:
            skipped += 1
            continue
        by_scope["MARKET"].append(net)
        if sector_map is not None:
            by_scope[sector_map.get(sym, _UNKNOWN_SECTOR)].append(net)
    if skipped:
        log.warning(
            "market_tide %s: %d/%d symbol(s) had no visible net_foreign — skipped, not zeroed",
            day, skipped, len(symbols),
        )
    return [
        TideRow(scope=scope, net_foreign=sum(vals), symbols=len(vals))
        for scope, vals in sorted(by_scope.items(), key=lambda kv: kv[0] != "MARKET")
    ]
