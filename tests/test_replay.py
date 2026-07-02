"""Money Flow Replay — the acceptance-criterion audit test (spec §13):
the replay reconstructs any past signal from stored `as_of` data. Frames must
(a) never see the future, (b) respect revisions' `as_of`, (c) respect broker
conservative availability (LD-5), and (d) reconcile exactly with the live signal
modules evaluated at the same historical decision_ts."""

from __future__ import annotations

from datetime import date as Date
from datetime import datetime, time, timedelta

import pytest

from currentflow.dal.models import (
    BrokerNet,
    DailyBar,
    InvestorType,
    RowStatus,
    Side,
)
from currentflow.dal.timing import broker_as_of, ohlcv_as_of
from currentflow.signals import broker_flow, foreign_flow
from currentflow.signals.replay import build_frame, build_replay, frame_decision_ts
from currentflow.ui.replay_view import PHASE_PLACEHOLDER, playhead_panel, visible_rows

SYM = "TEST"
D = [Date(2026, 6, 22) + timedelta(days=i) for i in range(5)]  # Mon–Fri
CLOSES = [100.0, 110.0, 121.0, 115.0, 120.0]
VOLUMES = [10, 20, 60, 40, 50]
NF = [1e9, -2e9, 3e9, 4e9, -5e9]


def _bar(d: Date, close: float, volume: int, nf: float, as_of: datetime | None = None) -> DailyBar:
    return DailyBar(
        symbol=SYM, date=d, as_of=as_of or ohlcv_as_of(d), status=RowStatus.TRADED,
        open=close, high=close, low=close, close=close, volume=volume,
        value=close * volume, frequency=5, vwap=close, foreign_buy=None,
        foreign_sell=None, net_foreign=nf, change_percentage=None,
    )


def _broker(d: Date, code: str, side: Side, value: float) -> BrokerNet:
    return BrokerNet(
        symbol=SYM, date=d, as_of=broker_as_of(d), broker_code=code, side=side,
        investor_type=InvestorType.LOCAL, avg_price=None, value=value,
        lot=None, frequency=None,
    )


@pytest.fixture
def seeded(store):
    store.write_daily_bars([_bar(*args) for args in zip(D, CLOSES, VOLUMES, NF)])
    # day 3 (D[2]): DX is SMART_MONEY in the default registry; YP is RETAIL
    store.write_broker_net([
        _broker(D[2], "DX", Side.BUY, 5e9),
        _broker(D[2], "DX", Side.SELL, 2e9),
        _broker(D[2], "YP", Side.BUY, 1e9),
    ])
    return store


# --- frame values (hand-checked) -----------------------------------------------------


def test_frame_reconstructs_the_day(seeded):
    f = build_frame(seeded, SYM, D[2])
    assert f.decision_ts == datetime.combine(D[3], time(9, 15))
    assert f.close == 121.0
    assert f.prev_close == 110.0
    assert f.change_pct == pytest.approx(10.0)
    assert f.rvol_20d == pytest.approx(60 / ((10 + 20) / 2))  # 4.0 — priors only
    assert f.net_foreign == 3e9
    assert f.broker_net_total == pytest.approx(5e9 - 2e9 + 1e9)
    assert f.smart_money_net == pytest.approx(3e9)  # DX only; YP is retail
    assert f.phase is None  # RULE A classifier absent — never fabricated


def test_frame_with_no_broker_rows_reports_none_not_zero(seeded):
    f = build_frame(seeded, SYM, D[1])
    assert f.broker_net_total is None
    assert f.smart_money_net is None


# --- (a) the future is invisible ------------------------------------------------------


def test_frames_never_see_future_days(seeded):
    series = build_replay(seeded, SYM, D[0], D[4])
    assert [f.close for f in series.frames] == CLOSES
    # RVOL at D[2] must use only D[0..1] volumes even though D[3..4] are stored
    assert series.frames[2].rvol_20d == pytest.approx(4.0)
    # chart rows at playhead 2 withhold days 3-4 entirely
    rows = visible_rows(series, 2)
    assert [r["date"] for r in rows] == D[:3]


# --- (b) revisions respect as_of -------------------------------------------------------


def test_revision_with_later_as_of_stays_invisible_to_earlier_frames(seeded):
    # D[1]'s bar is revised (close AND volume) on D[3] at 10:00 — after the
    # decision moments of frame(D[1]) (D[2] 09:15) and frame(D[2]) (D[3] 09:15).
    revised = _bar(D[1], 999.0, 200, -2e9, as_of=datetime.combine(D[3], time(10, 0)))
    seeded.write_daily_bars([revised])

    assert build_frame(seeded, SYM, D[1]).close == 110.0            # original
    assert build_frame(seeded, SYM, D[2]).prev_close == 110.0       # still original
    assert build_frame(seeded, SYM, D[2]).rvol_20d == pytest.approx(60 / 15)
    # frame(D[4]) decides at D[5] 09:15 — the revision is now knowable and feeds RVOL
    f4 = build_frame(seeded, SYM, D[4])
    assert f4.rvol_20d == pytest.approx(50 / ((10 + 200 + 60 + 40) / 4))


# --- (c) broker conservative availability (LD-5) ---------------------------------------


def test_broker_rows_respect_conservative_next_day_availability(seeded):
    # Broker summary for D[2] is stamped D[3] 09:00. A decision moment of D[3] 08:00
    # (before publish) must not see it; the default 09:15 moment must.
    early = build_frame(seeded, SYM, D[2], decision_time=time(8, 0))
    assert early.broker_net_total is None
    default = build_frame(seeded, SYM, D[2])
    assert default.broker_net_total is not None


# --- (d) reconciliation: replay == live signal at the same decision_ts -----------------


def test_replay_reconstructs_live_foreign_flow_signal(seeded):
    f = build_frame(seeded, SYM, D[2])
    live = foreign_flow.analyze(seeded, SYM, decision_ts=f.decision_ts, end=f.date)
    assert live.net_last == f.net_foreign
    assert live.end == f.date


def test_replay_reconstructs_live_broker_flow_signal(seeded):
    f = build_frame(seeded, SYM, D[2])
    live = broker_flow.analyze(
        seeded, SYM, decision_ts=f.decision_ts, start=f.date, end=f.date
    )
    assert sum(live.daily_nets[D[2]].values()) == pytest.approx(f.broker_net_total)


# --- gaps stay visible ------------------------------------------------------------------


def test_gap_day_yields_empty_frame_not_dropped(store):
    store.write_daily_bars([
        _bar(D[0], 100.0, 10, 1e9),
        _bar(D[2], 121.0, 60, 3e9),  # D[1] is a gap
    ])
    series = build_replay(store, SYM, D[0], D[2])
    assert [f.date for f in series.frames] == D[:3]
    gap = series.frames[1]
    assert gap.close is None and gap.volume is None and gap.net_foreign is None


# --- view-model (RULE B framing) --------------------------------------------------------


def test_playhead_panel_shows_measurements_and_phase_placeholder(seeded):
    panel = playhead_panel(build_frame(seeded, SYM, D[2]))
    assert panel["close"] == 121.0
    assert panel["net_foreign_bn"] == 3.0
    assert panel["smart_money_net_bn"] == 3.0
    assert panel["phase"] == PHASE_PLACEHOLDER
    assert "slice 4" in panel["phase"]
