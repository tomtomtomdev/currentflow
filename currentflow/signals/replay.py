"""Money Flow Replay (spec §9) — THE AUDIT TOOL. PURE OBSERVATION.

Each frame reconstructs what was knowable at that day's decision moment by
re-reading the store with the *historical* `decision_ts` — the exact same
look-ahead-safe read path every live signal uses. Nothing is precomputed or
carried across frames, so a frame can never leak the future: revisions with a
later `as_of` stay invisible until a frame whose decision moment passes them.

Decision moment: the frame for trading day D uses `decision_ts` = D+1 09:15 WIB
(config.REPLAY_DECISION_TIME) — the first actionable pre-open moment at which both
D's EOD bar (~16:15, OHLCV_AVAILABLE_TIME) and D's broker summary (conservative
availability D+1 09:00, LD-5) are knowable. Injectable per call; revisit once
BROKER_PUBLISH_LATENCY is measured.

RULE B: frames carry raw measurements (close, volume, RVOL multiple, net flows).
No score, no probability. The Wyckoff phase lane carries the classifier's *label*
(a gate verdict, not a number, RULE A) reconstructed at each frame's own decision_ts;
`UNKNOWN` when there is not yet enough history — never fabricated.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as Date
from datetime import datetime, time, timedelta

from currentflow import config
from currentflow.dal.models import InvestorType
from currentflow.signals import phase as phase_mod
from currentflow.signals.broker_flow import BrokerDNA, classify_dna, daily_broker_net
from currentflow.store.db import Store


def frame_decision_ts(day: Date, decision_time: time | None = None) -> datetime:
    """The decision moment a frame for trading day `day` reconstructs."""
    t = config.REPLAY_DECISION_TIME if decision_time is None else decision_time
    return datetime.combine(day + timedelta(days=1), t)


@dataclass(frozen=True, slots=True)
class ReplayFrame:
    """One playhead position: trading day `date` as knowable at `decision_ts`.

    None fields mean *not knowable then* (not published / gap) — never zero.
    """

    date: Date
    decision_ts: datetime
    close: float | None
    prev_close: float | None
    change_pct: float | None
    volume: int | None
    rvol_20d: float | None          # volume / mean volume of the prior ≤20 visible bars
    net_foreign: float | None
    broker_net_total: float | None  # sum of listed brokers' net that day (feed resolution)
    smart_money_net: float | None   # SMART_MONEY-DNA brokers' net within the listed set
    # Wyckoff phase label at this frame's decision_ts (RULE A gate verdict, not a number);
    # "UNKNOWN" until enough history — never fabricated.
    phase: str | None = None


@dataclass(frozen=True, slots=True)
class ReplaySeries:
    symbol: str
    start: Date
    end: Date
    frames: tuple[ReplayFrame, ...]


def build_frame(
    store: Store,
    symbol: str,
    day: Date,
    *,
    decision_time: time | None = None,
    registry: dict[str, BrokerDNA] | None = None,
) -> ReplayFrame:
    """Reconstruct trading day `day` from the store at its historical decision moment."""
    ts = frame_decision_ts(day, decision_time)
    history_start = day - timedelta(days=config.REPLAY_HISTORY_LOOKBACK_DAYS)

    bars = store.read_daily_bars(symbol, ts, start=history_start, end=day)
    bar = bars[-1] if bars and bars[-1].date == day else None
    prior = [b for b in bars if b.date < day]
    prev_close = prior[-1].close if prior else None

    close = bar.close if bar else None
    change_pct = None
    if close is not None and prev_close:
        change_pct = (close - prev_close) / prev_close * 100

    rvol = None
    if bar is not None and bar.volume is not None:
        prior_vols = [b.volume for b in prior[-config.FF_AVG_WINDOW_DAYS:] if b.volume is not None]
        if prior_vols:
            mean_vol = sum(prior_vols) / len(prior_vols)
            if mean_vol > 0:
                rvol = bar.volume / mean_vol

    broker_rows = store.read_broker_net(symbol, ts, start=day, end=day)
    nets = daily_broker_net(broker_rows).get(day)
    broker_net_total = sum(nets.values()) if nets else None
    smart_money_net = None
    if nets:
        inv = {r.broker_code: r.investor_type for r in broker_rows}
        smart_money_net = sum(
            v for code, v in nets.items()
            if classify_dna(code, inv.get(code, InvestorType.UNKNOWN), registry)
            is BrokerDNA.SMART_MONEY
        )

    # Wyckoff phase lane (RULE A) — reconstructed from a longer look-ahead-safe base.
    phase_bars = store.read_daily_bars(
        symbol, ts, start=day - timedelta(days=config.REPLAY_PHASE_LOOKBACK_DAYS), end=day
    )
    phase_label = phase_mod.classify(symbol, phase_bars, ts).phase.value

    return ReplayFrame(
        date=day,
        decision_ts=ts,
        close=close,
        prev_close=prev_close,
        change_pct=change_pct,
        volume=bar.volume if bar else None,
        rvol_20d=rvol,
        net_foreign=bar.net_foreign if bar else None,
        broker_net_total=broker_net_total,
        smart_money_net=smart_money_net,
        phase=phase_label,
    )


def build_replay(
    store: Store,
    symbol: str,
    start: Date,
    end: Date,
    *,
    decision_time: time | None = None,
    registry: dict[str, BrokerDNA] | None = None,
) -> ReplaySeries:
    """One frame per weekday in [start, end]. Days with nothing visible produce a
    frame of Nones — an honest gap in the timeline, never dropped silently."""
    frames = []
    d = start
    while d <= end:
        if d.weekday() < 5:
            frames.append(
                build_frame(store, symbol, d, decision_time=decision_time, registry=registry)
            )
        d += timedelta(days=1)
    return ReplaySeries(symbol=symbol, start=start, end=end, frames=tuple(frames))
