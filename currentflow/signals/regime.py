"""Market-regime read (observation only) — the deferred half of the allocation question.

Whether the *market* is risk-on or risk-off is a natural input to "how much to deploy."
But scaling allocation by regime is a NEW locked decision (it changes sizing behaviour) and
must survive forward paper before it may drive a rupiah — exactly the RULE B / LD-8
discipline the rest of the engine follows. So this module **only observes**: it classifies
a coarse regime from a look-ahead-safe benchmark/proxy return series and reports the
supporting measurements. It exposes **no allocation multiplier** and is wired into nothing
in `execution.order` / `validation.portfolio_runner`.

    RISK_ON   proxy in a rising trend (latest cumulative above its moving average) and
              broad participation
    RISK_OFF  falling trend / narrow breadth
    NEUTRAL   mixed
    UNKNOWN   too little visible data (missing ≠ zero — never a fabricated regime)

To promote this from observation to a sizing input: pin the factor set (see the operator
research note), version-bump `LOCKED_SPEC.md` with a new LD, and gate the multiplier behind
the `ValidationLedger` like SMS/RULE B. Until then, a UI may *show* the regime; the
allocator must ignore it.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date as Date
from datetime import datetime
from enum import Enum

from currentflow.signals.risk_monitor import daily_returns, market_proxy_returns
from currentflow.store.db import Store


class Regime(str, Enum):
    RISK_ON = "RISK_ON"
    NEUTRAL = "NEUTRAL"
    RISK_OFF = "RISK_OFF"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class RegimeRead:
    """A categorical regime + the measurements behind it. No number scales allocation."""

    regime: Regime
    trend_pct: float | None       # cumulative proxy return over the window (the TREND leg)
    above_ma: bool | None         # latest cumulative level above its moving average
    breadth: float | None         # mean fraction of names advancing over the window (BREADTH)
    breadth_latest: float | None  # fraction advancing on the most recent day (A-D confirmation)
    n_obs: int
    note: str


def _cumulative(returns: list[float]) -> list[float]:
    """Cumulative (compounded) level path from a return series, starting at 1.0."""
    level, out = 1.0, []
    for r in returns:
        level *= (1.0 + r)
        out.append(level)
    return out


def classify_regime(
    market_returns: dict[Date, float],
    *,
    ma_window: int = 20,
    min_obs: int = 10,
    trend_band: float = 0.02,
    breadth_series: dict[Date, float] | None = None,
) -> RegimeRead:
    """Classify the regime from an injected look-ahead-safe proxy/benchmark return series.

    `market_returns` is typically `risk_monitor.market_proxy_returns(...)` (equal-weight
    universe) or an injected index series. `breadth_series` optionally supplies a per-day
    breadth reading (e.g. fraction of names advancing); absent, breadth falls back to the
    share of positive proxy days. Everything is a *measurement*, never a prediction."""
    dates = sorted(market_returns)
    n = len(dates)
    if n < min_obs:
        return RegimeRead(
            regime=Regime.UNKNOWN, trend_pct=None, above_ma=None, breadth=None,
            breadth_latest=None, n_obs=n,
            note=f"insufficient data ({n} < {min_obs} obs) — regime withheld",
        )

    rets = [market_returns[d] for d in dates]
    levels = _cumulative(rets)
    trend_pct = levels[-1] - 1.0

    window = levels[-ma_window:] if len(levels) >= ma_window else levels
    ma = sum(window) / len(window)
    above_ma = levels[-1] >= ma

    if breadth_series:
        bdates = sorted(breadth_series)
        bvals = [breadth_series[d] for d in bdates]
        breadth = sum(bvals) / len(bvals) if bvals else None
        breadth_latest = bvals[-1] if bvals else None
    else:
        # No advance-decline series supplied — fall back to the share of up proxy days.
        breadth = sum(1 for r in rets if r > 0) / n
        breadth_latest = None

    # A trend deadband keeps a chop that merely drifts a few bps from reading as a
    # directional regime — only a move beyond ±`trend_band` with the MA agreeing counts.
    if above_ma and trend_pct > trend_band and (breadth is None or breadth >= 0.5):
        regime = Regime.RISK_ON
    elif (not above_ma) and trend_pct < -trend_band and (breadth is None or breadth <= 0.55):
        regime = Regime.RISK_OFF
    else:
        regime = Regime.NEUTRAL

    return RegimeRead(
        regime=regime, trend_pct=trend_pct, above_ma=above_ma, breadth=breadth,
        breadth_latest=breadth_latest, n_obs=n,
        note=(
            f"proxy {trend_pct:+.1%} over {n}d, "
            f"{'above' if above_ma else 'below'} {ma_window}d MA"
            + (f", breadth {breadth:.0%}" if breadth is not None else "")
            + (f" (latest {breadth_latest:.0%})" if breadth_latest is not None else "")
            + " — observation only, does not scale allocation (RULE B)"
        ),
    )


# --- store-level trend + breadth (the market-wide observation) -----------------------


def market_breadth(
    store: Store,
    symbols: list[str],
    decision_ts: datetime,
    *,
    start: Date | None = None,
    end: Date | None = None,
) -> dict[Date, float]:
    """Advance-decline breadth: per day, the fraction of `symbols` that advanced (positive
    close-to-close return). Look-ahead-safe (each name read as-of `decision_ts`). A day is
    present only if at least one name has a visible return — `missing ≠ zero`, an absent
    print is not counted as a decline."""
    adv: dict[Date, int] = defaultdict(int)
    tot: dict[Date, int] = defaultdict(int)
    for sym in symbols:
        bars = store.read_daily_bars(sym, decision_ts, start=start, end=end)
        for d, r in daily_returns(bars).items():
            tot[d] += 1
            if r > 0:
                adv[d] += 1
    return {d: adv[d] / tot[d] for d in tot if tot[d] > 0}


def classify_market_regime(
    store: Store,
    symbols: list[str],
    decision_ts: datetime,
    *,
    ma_window: int = 20,
    min_obs: int = 10,
    trend_band: float = 0.02,
    start: Date | None = None,
    end: Date | None = None,
) -> RegimeRead:
    """The market-wide regime read: TREND (equal-weight universe proxy) confirmed by
    BREADTH (advance-decline), both look-ahead-safe from the store. Convergent with Zweig
    (Four Percent + A-D / breadth) and Murphy (primary trend + breadth confirmation).

    Still OBSERVATION ONLY (RULE B): it reports the regime and its measurements and scales
    no allocation. Promoting it to a gross-exposure input requires a `LOCKED_SPEC.md` LD
    bump and forward-paper validation, like SMS."""
    proxy = market_proxy_returns(store, symbols, decision_ts, start=start, end=end)
    breadth = market_breadth(store, symbols, decision_ts, start=start, end=end)
    return classify_regime(
        proxy, ma_window=ma_window, min_obs=min_obs, trend_band=trend_band,
        breadth_series=breadth,
    )
