"""Universe gate (§3) — every hard-floor rule, Track A/B, no silent caps."""

from __future__ import annotations

from datetime import date as Date
from datetime import datetime, timedelta

from currentflow.dal.models import (
    BoardType,
    CorpAction,
    DailyBar,
    RowStatus,
    SymbolInfo,
)
from currentflow.store.integrity import CoverageReport
from currentflow.universe.gate import GateFailure, Track, evaluate_gate

DAY = Date(2026, 6, 30)
NOW = datetime(2026, 7, 1, 9, 0)


def mk_bar(
    day: Date,
    *,
    close: float | None = 500.0,
    value: float | None = 12e9,
    status: RowStatus = RowStatus.TRADED,
    symbol: str = "TEST",
) -> DailyBar:
    return DailyBar(
        symbol=symbol, date=day, as_of=datetime.combine(day, datetime.min.time()),
        status=status, open=close, high=close, low=close, close=close,
        volume=1000, value=value, frequency=100, vwap=close,
        foreign_buy=None, foreign_sell=None, net_foreign=None, change_percentage=None,
    )


def mk_history(n: int = 70, *, close: float = 500.0, value: float = 12e9) -> list[DailyBar]:
    """n weekday bars ending at DAY."""
    bars, d = [], DAY
    while len(bars) < n:
        if d.weekday() < 5:
            bars.append(mk_bar(d, close=close, value=value))
        d -= timedelta(days=1)
    return list(reversed(bars))


def mk_info(*, suspended: bool = False, indexes: tuple[str, ...] = ()) -> SymbolInfo:
    return SymbolInfo(
        symbol="TEST", as_of=NOW, suspended=suspended, tradeable=True,
        uma=False, notations=(), indexes=indexes,
    )


def mk_coverage(bars: list[DailyBar], *, gaps: tuple[Date, ...] = ()) -> CoverageReport:
    status = {b.date: b.status for b in bars[-20:]}
    status.update({d: RowStatus.GAP for d in gaps})
    return CoverageReport("TEST", bars[-20].date, DAY, status)


def run_gate(
    bars=None, *, info=None, corp_actions=(), board=BoardType.MAIN,
    coverage=None, broker_complete=True, days_since_ipo=None,
):
    bars = mk_history() if bars is None else bars
    return evaluate_gate(
        "TEST", DAY, bars,
        info=mk_info() if info is None else info,
        corp_actions=list(corp_actions),
        board=board,
        coverage=mk_coverage(bars) if coverage is None else coverage,
        broker_summary_complete=broker_complete,
        trading_days_since_ipo=days_since_ipo,
    )


# --- passing case + track assignment ---------------------------------------------------


def test_clean_candidate_passes_as_track_b():
    d = run_gate()
    assert d.passed
    assert d.failures == ()
    assert d.track is Track.B
    assert d.adv20 == 12e9


def test_track_a_requires_index_membership_and_25bn_adv():
    bars = mk_history(value=30e9)
    d = run_gate(bars, info=mk_info(indexes=("LQ45",)))
    assert d.passed and d.track is Track.A


def test_index_member_below_25bn_adv_is_track_b():
    d = run_gate(info=mk_info(indexes=("LQ45",)))  # ADV 12bn
    assert d.passed and d.track is Track.B


def test_liquid_nonmember_is_track_b():
    d = run_gate(mk_history(value=30e9))
    assert d.passed and d.track is Track.B


# --- hard-floor rejections ------------------------------------------------------------


def test_low_adv_rejected():
    d = run_gate(mk_history(value=8e9))
    assert not d.passed and GateFailure.LOW_LIQUIDITY in d.failures


def test_no_trades_days_drag_adv_as_true_zero():
    bars = mk_history(value=12e9)
    # half the window genuinely traded nothing → ADV 6bn < 10bn
    for i in range(-20, 0, 2):
        b = bars[i]
        bars[i] = mk_bar(b.date, close=b.close, value=0.0, status=RowStatus.NO_TRADES)
    d = run_gate(bars)
    assert GateFailure.LOW_LIQUIDITY in d.failures


def test_price_floor_rejected():
    d = run_gate(mk_history(close=90.0))
    assert not d.passed and GateFailure.PRICE_FLOOR in d.failures


def test_suspended_rejected():
    d = run_gate(info=mk_info(suspended=True))
    assert not d.passed and GateFailure.SUSPENDED in d.failures


def test_insufficient_history_rejected():
    d = run_gate(mk_history(40))
    assert not d.passed and GateFailure.INSUFFICIENT_HISTORY in d.failures


def test_ipo_age_overrides_bar_count():
    # plenty of bars stored, but the listing is younger than 60 trading days
    d = run_gate(days_since_ipo=30)
    assert GateFailure.INSUFFICIENT_HISTORY in d.failures


def test_ara_pinned_close_rejected():
    bars = mk_history()
    prev = bars[-2].close
    bars[-1] = mk_bar(DAY, close=prev * 1.07, value=12e9)
    d = run_gate(bars)
    assert not d.passed and GateFailure.PINNED_CLOSE in d.failures
    assert d.band is not None and d.band.pinned


def test_incomplete_broker_summary_rejected():
    d = run_gate(broker_complete=False)
    assert not d.passed and GateFailure.BROKER_SUMMARY_INCOMPLETE in d.failures


def test_corp_action_within_5_days_rejected():
    ca = CorpAction(
        symbol="TEST", as_of=NOW, action_type="rightissue",
        ex_date=DAY + timedelta(days=3), recording_date=None,
    )
    d = run_gate(corp_actions=[ca])
    assert not d.passed and GateFailure.CORP_ACTION_WINDOW in d.failures


def test_corp_action_outside_window_ok():
    ca = CorpAction(
        symbol="TEST", as_of=NOW, action_type="dividend",
        ex_date=DAY + timedelta(days=6), recording_date=None,
    )
    assert run_gate(corp_actions=[ca]).passed


def test_gap_in_window_is_never_read_as_zero():
    bars = mk_history()
    d = run_gate(bars, coverage=mk_coverage(bars, gaps=(DAY - timedelta(days=1),)))
    assert not d.passed and GateFailure.DATA_GAP in d.failures


def test_traded_bar_without_value_is_incomplete_data():
    bars = mk_history()
    b = bars[-3]
    bars[-3] = mk_bar(b.date, close=b.close, value=None)
    d = run_gate(bars)
    assert not d.passed and GateFailure.INCOMPLETE_DATA in d.failures


def test_all_failures_reported_no_silent_caps():
    bars = mk_history(40, close=90.0, value=8e9)
    d = run_gate(bars, info=mk_info(suspended=True), broker_complete=False)
    got = set(d.failures)
    assert {
        GateFailure.INSUFFICIENT_HISTORY,
        GateFailure.LOW_LIQUIDITY,
        GateFailure.PRICE_FLOOR,
        GateFailure.SUSPENDED,
        GateFailure.BROKER_SUMMARY_INCOMPLETE,
    } <= got
    assert d.track is None
