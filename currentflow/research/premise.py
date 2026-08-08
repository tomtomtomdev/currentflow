"""Univariate premise tests — does a single SMS component predict anything at all?

`LOCKED_SPEC` §4 assembles ~48 hand-chosen constants into a score, then asks a 6-trade
minimum whether the *whole thing* works. That ordering cannot falsify a component: a
dead signal carrying 35 weight is indistinguishable from a live one once it is buried in
a conjunction of gates.

This module inverts it. One feature, one number, one question: sorted into buckets each
day, does the top bucket out-return the bottom over the next H days? A component that
shows nothing here cannot be rescued by Wyckoff gating downstream, and should not be
funded with weight.

Method (Fama-MacBeth in shape, deliberately not a backtest):

  * Per decision day, rank the cross-section on the feature, split into equal buckets,
    and measure each bucket's mean forward return. The day's **spread** = top − bottom.
  * Average the *daily* spreads and t-stat across days. Doing it per-day rather than
    pooling is what keeps cross-sectional correlation (every IDX name loading on the
    same rupiah/commodity factor) from inflating the sample to n = observations.
  * Sample days `stride_days` apart, defaulting to the horizon, so the spread series is
    non-overlapping. Overlapping windows autocorrelate and inflate the t-stat.

Look-ahead firewall (the load-bearing invariant): a feature is built ONLY from bars
dated strictly before its decision day; entry is the first close ON/AFTER that day.
`test_premise.py` asserts both — a leak here would manufacture the edge under test.

What this deliberately does NOT do: fees, slippage, ARA/ARB fillability, position
sizing. A spread here is an upper bound on a tradable edge, never an expected return.
Every report carries its own caveats (`PremiseReport.caveats`) — a bare spread number
with no context is a claim, and claims are RULE B's business, not research's.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date as Date
from datetime import datetime, timedelta
from typing import Callable, Sequence

from currentflow import config
from currentflow.dal.models import BrokerNet, DailyBar, RowStatus
from currentflow.signals.broker_flow import daily_broker_net, top_n_share

# (bars strictly before the decision day, broker rows likewise, decision day) -> score
FeatureFn = Callable[[list[DailyBar], list[BrokerNet], Date], float | None]

DEFAULT_MIN_DAYS = 12        # fewer cross-sections than this → no t-stat (can't honestly test)
DEFAULT_MIN_HISTORY = 60     # visible trading bars a name needs before it may be ranked
FOREIGN_WINDOW_DAYS = 20

CAVEATS: tuple[str, ...] = (
    "Survivorship: the store holds names Stockbit still serves. Delisted/suspended names "
    "are absent, biasing every bucket upward (see pit.known_missing).",
    "Gross of everything: no fees, slippage, tick rounding, or ARA/ARB fillability. "
    "Treat any spread as an upper bound on a tradable edge.",
    "Overlapping-horizon autocorrelation is handled by striding, NOT by Newey-West; "
    "a stride below the horizon invalidates the t-stat.",
    "Single-factor: no sector, size, or beta control. A spread may be a size or "
    "commodity-beta proxy rather than the feature.",
    "Regime-scoped: reads are clamped per REGIME.md. Results carry no cross-regime "
    "stability claim.",
)


@dataclass(frozen=True, slots=True)
class CrossSection:
    """One decision day's ranked cross-section."""

    day: Date
    n_symbols: int
    bucket_means: tuple[float, ...]     # mean forward return per bucket, low→high feature
    spread: float                        # top bucket − bottom bucket


@dataclass(frozen=True, slots=True)
class PremiseReport:
    """A measurement, not a verdict. `mean_spread`/`t_stat` are None when the sample is
    too thin to say anything — never 0.0 (missing ≠ zero)."""

    name: str
    horizon_days: int
    buckets: int
    stride_days: int
    n_days: int                          # cross-sections actually used
    n_obs: int                           # (symbol, day) observations behind them
    mean_spread: float | None            # avg daily top-minus-bottom forward return
    t_stat: float | None                 # across days, so cross-sectional corr can't inflate n
    bucket_means: tuple[float, ...]      # pooled per-bucket means (monotonicity read)
    skipped_days: int                    # cross-sections too thin to bucket (counted, not dropped)
    dropped_missing_feature: int         # (symbol, day) pairs with no computable feature
    dropped_missing_return: int          # ... with no complete forward window
    symbols_used: tuple[str, ...]
    caveats: tuple[str, ...] = CAVEATS

    def verdict_line(self) -> str:
        """One-line human summary. Deliberately hedged — this layer never says 'buy'."""
        if self.mean_spread is None:
            return f"{self.name}: INCONCLUSIVE (n_days={self.n_days}, skipped={self.skipped_days})"
        t = f"t={self.t_stat:+.2f}" if self.t_stat is not None else "t=n/a"
        return (
            f"{self.name}: spread={self.mean_spread * 100:+.2f}%/{self.horizon_days}d "
            f"{t} over {self.n_days} non-overlapping cross-sections (n_obs={self.n_obs})"
        )


# --- forward return -------------------------------------------------------------------


def forward_return(
    bars: Sequence[DailyBar], day: Date, horizon_days: int
) -> float | None:
    """Return over `horizon_days` TRADED bars, entering at the first close ON/AFTER `day`.

    Entry is deliberately not the prior bar's close: the decision frame is D 09:15
    pre-open (`config.REPLAY_DECISION_TIME`), so yesterday's close is no longer
    obtainable. `patterns.outcome.resolve_instance` uses the prior close because it
    measures a pattern's move; here we measure what a decision could have captured.
    """
    traded = sorted(
        (b for b in bars if b.status is RowStatus.TRADED and b.close), key=lambda b: b.date
    )
    entry_idx = next((i for i, b in enumerate(traded) if b.date >= day), None)
    if entry_idx is None:
        return None
    exit_idx = entry_idx + horizon_days
    if exit_idx >= len(traded):
        return None
    entry = traded[entry_idx].close
    if not entry:
        return None
    return traded[exit_idx].close / entry - 1.0


# --- the harness ----------------------------------------------------------------------


def _bucket_of(rank: int, n: int, buckets: int) -> int:
    """Equal-count bucket index for `rank` among `n` sorted items."""
    return min(rank * buckets // n, buckets - 1)


def _bucketize(
    scored: list[tuple[str, float, float]], buckets: int
) -> tuple[list[float], list[int]] | None:
    """Sum and count of forward returns per bucket, low→high feature.

    `scored` = [(symbol, feature, forward_return)]. None when the cross-section cannot
    fill every bucket with at least two names — a bucket of one is a single stock, not
    a portfolio, and its "mean" is noise.
    """
    if len(scored) < buckets * 2:
        return None
    ordered = sorted(scored, key=lambda t: t[1])
    sums = [0.0] * buckets
    counts = [0] * buckets
    for rank, (_, _, fwd) in enumerate(ordered):
        b = _bucket_of(rank, len(ordered), buckets)
        sums[b] += fwd
        counts[b] += 1
    if any(c == 0 for c in counts):
        return None
    return sums, counts


def run_premise(
    store,
    feature: FeatureFn,
    *,
    name: str,
    horizon_days: int,
    buckets: int,
    start: Date,
    end: Date,
    now: datetime,
    stride_days: int | None = None,
    min_history: int = DEFAULT_MIN_HISTORY,
    min_days: int = DEFAULT_MIN_DAYS,
    symbols: Sequence[str] | None = None,
) -> PremiseReport:
    """Rank the cross-section on `feature` each sampled day; measure the forward spread.

    `stride_days` defaults to `horizon_days` so sampled days do not overlap.
    """
    stride = stride_days if stride_days is not None else horizon_days
    universe = list(symbols) if symbols is not None else store.symbols()

    # One read per symbol at `now`; the firewall below is by DATE, applied per decision
    # day, so a single read cannot leak (the feature never sees a bar dated >= day).
    bars_by_symbol: dict[str, list[DailyBar]] = {}
    for sym in universe:
        rows = store.read_daily_bars(sym, now)
        if rows:
            bars_by_symbol[sym] = sorted(rows, key=lambda b: b.date)

    days = _sample_days(bars_by_symbol, start, end, stride)

    sections: list[CrossSection] = []
    skipped = dropped_feat = dropped_ret = 0
    used_symbols: set[str] = set()
    pooled_sums: list[float] = [0.0] * buckets
    pooled_counts: list[int] = [0] * buckets
    n_obs = 0

    for day in days:
        scored: list[tuple[str, float, float]] = []
        for sym, bars in bars_by_symbol.items():
            prior = [b for b in bars if b.date < day]
            if len(prior) < min_history:
                continue
            broker = store.read_broker_net(sym, now, end=_prev_day(day))
            broker = [r for r in broker if r.date < day]
            score = feature(prior, broker, day)
            if score is None:
                dropped_feat += 1
                continue
            used_symbols.add(sym)
            fwd = forward_return(bars, day, horizon_days)
            if fwd is None:
                dropped_ret += 1
                continue
            scored.append((sym, score, fwd))

        bucketed = _bucketize(scored, buckets)
        if bucketed is None:
            skipped += 1
            continue
        sums, counts = bucketed
        means = tuple(s / c for s, c in zip(sums, counts))
        sections.append(
            CrossSection(day=day, n_symbols=len(scored),
                         bucket_means=means, spread=means[-1] - means[0])
        )
        n_obs += len(scored)
        for b in range(buckets):
            pooled_sums[b] += sums[b]
            pooled_counts[b] += counts[b]

    spreads = [s.spread for s in sections]
    mean_spread = sum(spreads) / len(spreads) if spreads else None
    t_stat = _t_stat(spreads, min_days)
    bucket_means = tuple(
        (s / c if c else 0.0) for s, c in zip(pooled_sums, pooled_counts)
    )

    return PremiseReport(
        name=name,
        horizon_days=horizon_days,
        buckets=buckets,
        stride_days=stride,
        n_days=len(sections),
        n_obs=n_obs,
        mean_spread=mean_spread,
        t_stat=t_stat,
        bucket_means=bucket_means,
        skipped_days=skipped,
        dropped_missing_feature=dropped_feat,
        dropped_missing_return=dropped_ret,
        symbols_used=tuple(sorted(used_symbols)),
    )


def _prev_day(day: Date) -> Date:
    return day - timedelta(days=1)


def _sample_days(
    bars_by_symbol: dict[str, list[DailyBar]], start: Date, end: Date, stride: int
) -> list[Date]:
    """Trading days present in the store, within [start, end], every `stride`-th."""
    all_days = sorted({b.date for bars in bars_by_symbol.values() for b in bars})
    window = [d for d in all_days if start <= d <= end]
    return window[::stride] if stride > 1 else window


def _t_stat(spreads: list[float], min_days: int) -> float | None:
    """t over the DAILY spread series. None below `min_days` or with no dispersion —
    a t-stat on 3 points is noise wearing a decimal point."""
    n = len(spreads)
    if n < min_days or n < 2:
        return None
    mean = sum(spreads) / n
    var = sum((s - mean) ** 2 for s in spreads) / (n - 1)
    if var <= 0:
        return None
    return mean / math.sqrt(var / n)


# --- the three features under test ----------------------------------------------------


def feature_broker_top2_share(
    bars: list[DailyBar], broker: list[BrokerNet], day: Date
) -> float | None:
    """Top-2 brokers' share of net buying on the latest visible day (SMS
    `broker_concentration`, weight 20/A and **35/B** — the largest single Track B bet).

    The premise being tested is the identification assumption underneath bandarmologi:
    that broker concentration proxies one informed actor. A broker aggregates thousands
    of unrelated clients and one operator can split across brokers, so the mapping is
    not identified from this feed — which is exactly why it needs an empirical answer.
    """
    rows = [r for r in broker if r.date < day]
    if not rows:
        return None
    by_day = daily_broker_net(rows)
    if not by_day:
        return None
    return top_n_share(by_day[max(by_day)], 2)


def feature_foreign_persistence(
    bars: list[DailyBar], broker: list[BrokerNet], day: Date
) -> float | None:
    """Net foreign flow over the last 20 visible bars, normalised by traded value
    (SMS `foreign_flow`, weight 25/A; structurally zero on Track B per LD-1).

    Normalising matters: raw net foreign is a size proxy, so an un-normalised spread
    would mostly re-discover market cap.
    """
    prior = [b for b in bars if b.date < day and b.status is RowStatus.TRADED]
    window = prior[-FOREIGN_WINDOW_DAYS:]
    if len(window) < FOREIGN_WINDOW_DAYS:
        return None
    if any(b.net_foreign is None or b.value is None for b in window):
        return None      # missing ≠ zero: absent flow is not no-flow
    total_value = sum(b.value for b in window)
    if total_value <= 0:
        return None
    return sum(b.net_foreign for b in window) / total_value


def feature_divergence(
    bars: list[DailyBar], broker: list[BrokerNet], day: Date
) -> float | None:
    """Absorption proxy: the fraction of high-volume bars that closed flat (SMS
    `divergence`, weight **30 on BOTH tracks** — the single largest shared bet).

    High volume with no price move is the Wyckoff absorption claim: supply being taken
    without markup. Thresholds mirror §4 (`SMS_DIVERGENCE_*`) so the test measures the
    shipped detector, not a re-specified one.
    """
    prior = [b for b in bars if b.date < day and b.status is RowStatus.TRADED]
    window = prior[-config.SMS_DIVERGENCE_WINDOW_DAYS:]
    if len(window) < config.SMS_DIVERGENCE_WINDOW_DAYS:
        return None
    if any(b.volume is None or b.change_percentage is None for b in window):
        return None
    avg_vol = sum(b.volume for b in window) / len(window)
    if avg_vol <= 0:
        return None
    hivol = [b for b in window if b.volume >= config.SMS_DIVERGENCE_HIVOL_MULT * avg_vol]
    if not hivol:
        return None
    flat_pct = config.SMS_DIVERGENCE_FLAT_PCT * 100.0   # change_percentage is in percent
    flat = sum(1 for b in hivol if abs(b.change_percentage) <= flat_pct)
    return flat / len(hivol)


FEATURES: dict[str, FeatureFn] = {
    "broker_top2_share": feature_broker_top2_share,
    "foreign_persistence": feature_foreign_persistence,
    "divergence": feature_divergence,
}
