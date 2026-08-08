"""Premise tests for the premise-test harness (research layer).

The harness exists to falsify individual SMS components *before* they are assembled, so
its own correctness matters more than usual: a look-ahead leak here would manufacture the
very edge it is meant to test.
"""

from __future__ import annotations

from datetime import date as Date
from datetime import datetime, timedelta

import pytest

from currentflow.dal.models import BrokerNet, DailyBar, InvestorType, RowStatus, Side
from currentflow.research.premise import (
    CrossSection,
    PremiseReport,
    feature_broker_top2_share,
    feature_divergence,
    feature_foreign_persistence,
    forward_return,
    run_premise,
)

# Data is stamped when ingested; research reads later. The store firewall is a strict
# `as_of < decision_ts`, so a read at exactly the stamp time would see nothing.
AS_OF = datetime(2026, 8, 8, 6, 0)
NOW = datetime(2026, 8, 8, 18, 0)
START = Date(2025, 1, 6)  # a Monday


def _weekdays(n: int, start: Date = START) -> list[Date]:
    out: list[Date] = []
    d = start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _bar(symbol: str, d: Date, close: float, *, volume: int = 1_000, value: float | None = None,
         net_foreign: float | None = None, change: float | None = None,
         status: RowStatus = RowStatus.TRADED) -> DailyBar:
    return DailyBar(
        symbol=symbol, date=d, as_of=AS_OF, status=status,
        open=close, high=close, low=close, close=close,
        volume=volume, value=value if value is not None else close * volume,
        frequency=10, vwap=close, foreign_buy=None, foreign_sell=None,
        net_foreign=net_foreign, change_percentage=change,
    )


def _series(symbol: str, days: list[Date], closes: list[float], **kw) -> list[DailyBar]:
    return [_bar(symbol, d, c, **kw) for d, c in zip(days, closes)]


class FakeStore:
    """Minimal store stand-in: honours the `decision_ts` firewall the real store enforces."""

    def __init__(self, bars: dict[str, list[DailyBar]],
                 broker: dict[str, list[BrokerNet]] | None = None):
        self._bars = bars
        self._broker = broker or {}

    def symbols(self, table: str = "daily_bar") -> list[str]:
        return sorted(self._bars)

    def read_daily_bars(self, symbol, decision_ts, start=None, end=None, *, clamp_regime=None):
        return [b for b in self._bars.get(symbol, [])
                if b.as_of < decision_ts
                and (start is None or b.date >= start)
                and (end is None or b.date <= end)]

    def read_broker_net(self, symbol, decision_ts, start=None, end=None):
        return [r for r in self._broker.get(symbol, [])
                if r.as_of < decision_ts
                and (start is None or r.date >= start)
                and (end is None or r.date <= end)]


# --- forward return -------------------------------------------------------------------


def test_forward_return_enters_after_the_decision_day():
    """Entry is the first close ON/AFTER the decision day — never the prior bar, which is
    not tradable at the 09:15 pre-open decision frame."""
    days = _weekdays(10)
    bars = _series("X", days, [100, 101, 102, 103, 104, 105, 106, 107, 108, 109])
    # decide on days[2] -> enter at close of days[2] (102), exit 3 bars later (105)
    assert forward_return(bars, days[2], horizon_days=3) == pytest.approx(105 / 102 - 1)


def test_forward_return_none_when_horizon_incomplete():
    days = _weekdays(5)
    bars = _series("X", days, [100, 101, 102, 103, 104])
    assert forward_return(bars, days[3], horizon_days=5) is None


def test_forward_return_skips_non_traded_bars():
    days = _weekdays(6)
    bars = _series("X", days, [100, 101, 102, 103, 104, 105])
    bars[3] = _bar("X", days[3], 103, status=RowStatus.NO_TRADES)
    # days[1] entry=101; traded forward bars are 102,104,105 -> horizon 2 lands on 104
    assert forward_return(bars, days[1], horizon_days=2) == pytest.approx(104 / 101 - 1)


# --- look-ahead firewall --------------------------------------------------------------


def test_feature_never_sees_the_decision_day_or_later():
    """The load-bearing invariant: a feature is built only from bars strictly BEFORE the
    decision day. A leak here would fabricate the edge the harness is meant to falsify."""
    days = _weekdays(60)
    seen: list[Date] = []

    def spy_feature(bars, broker, day):
        seen.extend(b.date for b in bars)
        seen.extend(r.date for r in broker)
        return 1.0

    store = FakeStore({s: _series(s, days, [100 + i for i in range(60)]) for s in ("A", "B", "C", "D")})
    run_premise(store, spy_feature, name="spy", horizon_days=5, buckets=2,
                start=days[30], end=days[40], now=NOW, min_history=5)

    assert seen, "feature was never invoked"
    assert max(seen) < days[40], "feature saw a bar on/after its decision day"


# --- bucketing / spread ---------------------------------------------------------------


def test_monotone_feature_produces_positive_spread():
    """A feature that is perfectly rank-correlated with forward return must show a
    positive top-minus-bottom spread."""
    days = _weekdays(40)
    # D rises fastest, A slowest; the feature returns the same ordering.
    slopes = {"A": 0.0, "B": 0.5, "C": 1.0, "D": 1.5}
    bars = {s: _series(s, days, [100 + slope * i for i in range(40)]) for s, slope in slopes.items()}
    store = FakeStore(bars)

    report = run_premise(
        store, lambda b, br, d: slopes[b[0].symbol], name="slope",
        horizon_days=5, buckets=2, start=days[10], end=days[30], now=NOW, min_history=5,
    )
    assert report.mean_spread is not None and report.mean_spread > 0
    assert report.n_days > 0
    assert report.bucket_means[-1] > report.bucket_means[0]


def test_day_skipped_when_too_few_symbols_and_counted():
    """A cross-section that cannot fill the buckets is skipped and COUNTED — never
    silently dropped (no silent caps)."""
    days = _weekdays(40)
    store = FakeStore({s: _series(s, days, [100 + i for i in range(40)]) for s in ("A", "B")})
    report = run_premise(store, lambda b, br, d: 1.0, name="thin",
                         horizon_days=5, buckets=5, start=days[10], end=days[20],
                         now=NOW, min_history=5)
    assert report.n_days == 0
    assert report.skipped_days > 0
    assert report.mean_spread is None


def test_none_feature_excludes_symbol_and_is_counted():
    days = _weekdays(40)
    store = FakeStore({s: _series(s, days, [100 + i for i in range(40)]) for s in ("A", "B", "C", "D")})
    report = run_premise(
        store, lambda b, br, d: None if b[0].symbol == "A" else 1.0, name="partial",
        horizon_days=5, buckets=2, start=days[10], end=days[20], now=NOW, min_history=5,
    )
    assert report.dropped_missing_feature > 0
    assert "A" not in report.symbols_used


def test_default_stride_is_non_overlapping():
    """Overlapping horizons autocorrelate the daily spread series and inflate the t-stat,
    so the default sampling stride equals the horizon."""
    days = _weekdays(60)
    store = FakeStore({s: _series(s, days, [100 + i for i in range(60)]) for s in ("A", "B", "C", "D")})
    report = run_premise(store, lambda b, br, d: 1.0, name="stride",
                         horizon_days=10, buckets=2, start=days[10], end=days[50],
                         now=NOW, min_history=5)
    assert report.stride_days == 10


def test_t_stat_none_below_min_days():
    days = _weekdays(40)
    store = FakeStore({s: _series(s, days, [100 + i for i in range(40)]) for s in ("A", "B", "C", "D")})
    report = run_premise(store, lambda b, br, d: 1.0, name="tiny",
                         horizon_days=5, buckets=2, start=days[10], end=days[12],
                         now=NOW, min_history=5, min_days=10)
    assert report.t_stat is None


# --- the three real features ----------------------------------------------------------


def test_broker_top2_share_uses_latest_visible_day_only():
    days = _weekdays(5)
    rows = []
    for i, d in enumerate(days):
        # last visible day (days[3]) is dominated by one broker; earlier days dispersed
        if d == days[3]:
            spread = {"AA": 900.0, "BB": 50.0, "CC": 50.0}
        else:
            spread = {"AA": 100.0, "BB": 100.0, "CC": 100.0}
        for code, val in spread.items():
            rows.append(BrokerNet(symbol="X", date=d, as_of=AS_OF, broker_code=code,
                                  side=Side.BUY, investor_type=InvestorType.LOCAL,
                                  avg_price=100.0, value=val, lot=1, frequency=1))
    bars = _series("X", days, [100] * 5)
    got = feature_broker_top2_share(bars[:4], [r for r in rows if r.date < days[4]], days[4])
    assert got == pytest.approx((900 + 50) / 1000)


def test_broker_top2_share_none_without_broker_rows():
    days = _weekdays(3)
    assert feature_broker_top2_share(_series("X", days, [100] * 3), [], days[2]) is None


def test_foreign_persistence_normalises_by_traded_value():
    days = _weekdays(25)
    bars = [_bar("X", d, 100, value=1000.0, net_foreign=50.0) for d in days]
    # 20-day window: sum(net_foreign)/sum(value) = 50/1000
    assert feature_foreign_persistence(bars, [], days[-1]) == pytest.approx(0.05)


def test_foreign_persistence_none_when_flow_missing():
    """missing ≠ zero — an absent net_foreign must not read as no-flow."""
    days = _weekdays(25)
    bars = [_bar("X", d, 100, value=1000.0, net_foreign=None) for d in days]
    assert feature_foreign_persistence(bars, [], days[-1]) is None


def test_divergence_high_on_absorption_pattern():
    """High volume with flat price = absorption -> high score; high volume with big price
    moves -> low score."""
    days = _weekdays(45)
    absorb = [_bar("X", d, 100, volume=3000 if i % 2 else 1000, change=0.1 if i % 2 else 0.0)
              for i, d in enumerate(days)]
    trend = [_bar("Y", d, 100, volume=3000 if i % 2 else 1000, change=5.0 if i % 2 else 0.0)
             for i, d in enumerate(days)]
    hi = feature_divergence(absorb, [], days[-1])
    lo = feature_divergence(trend, [], days[-1])
    assert hi is not None and lo is not None and hi > lo


def test_report_names_what_it_does_not_handle():
    """The report must carry its own caveats — a spread number with no context is a claim."""
    days = _weekdays(40)
    store = FakeStore({s: _series(s, days, [100 + i for i in range(40)]) for s in ("A", "B", "C", "D")})
    report = run_premise(store, lambda b, br, d: 1.0, name="caveat",
                         horizon_days=5, buckets=2, start=days[10], end=days[30], now=NOW,
                         min_history=5)
    assert isinstance(report, PremiseReport)
    assert report.caveats, "report must name its unhandled biases"
    assert any("survivor" in c.lower() for c in report.caveats)
