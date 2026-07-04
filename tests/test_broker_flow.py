"""Broker Flow Analyzer — netting, concentration (hand-checked), persistence, DNA,
syndicates, matrix, and the look-ahead firewall through the store."""

from __future__ import annotations

from datetime import date as Date
from datetime import datetime

from currentflow.dal.models import BrokerNet, InvestorType, Side
from currentflow.signals.broker_flow import (
    BrokerDNA,
    analyze,
    build_snapshot,
    buyer_seller_matrix,
    classify_dna,
    daily_broker_net,
    herfindahl,
    persistence,
    syndicate_nets,
    top_n_share,
)
from currentflow.signals.veto import Veto, VetoReason, VetoResult
from currentflow.ui.broker_flow_view import (
    broker_table,
    concentration_panel,
    hhi_label,
    stock_header,
    veto_checks,
)
from builders import Chart

D1, D2, D3 = Date(2026, 6, 26), Date(2026, 6, 29), Date(2026, 6, 30)
DECISION = datetime(2026, 7, 1, 9, 30)


def mk_row(
    broker: str,
    side: Side,
    value: float | None,
    day: Date = D3,
    *,
    symbol: str = "BRMS",
    investor: InvestorType = InvestorType.LOCAL,
    as_of: datetime | None = None,
    avg_price: float | None = None,
) -> BrokerNet:
    return BrokerNet(
        symbol=symbol, date=day,
        as_of=as_of or datetime.combine(day, datetime.min.time().replace(hour=17)),
        broker_code=broker, side=side, investor_type=investor,
        avg_price=avg_price, value=value, lot=None, frequency=None,
    )


# --- netting ---------------------------------------------------------------------------


def test_daily_net_is_buy_minus_sell_per_broker():
    rows = [mk_row("KZ", Side.BUY, 100e9), mk_row("KZ", Side.SELL, 40e9)]
    assert daily_broker_net(rows) == {D3: {"KZ": 60e9}}


def test_unknown_value_is_dropped_never_zero():
    rows = [mk_row("KZ", Side.BUY, None), mk_row("YP", Side.BUY, 10e9)]
    assert daily_broker_net(rows) == {D3: {"YP": 10e9}}


# --- concentration (hand-checked) --------------------------------------------------------


def test_top2_share_hand_checked():
    nets = {"A": 60e9, "B": 30e9, "C": 10e9, "D": -50e9}
    # top-2 buyers 90 of 100 total net buying
    assert top_n_share(nets, 2) == 0.9


def test_top_n_share_none_when_no_buyers():
    assert top_n_share({"A": -5e9}) is None


def test_herfindahl_hand_checked():
    assert herfindahl({"A": 50e9, "B": 50e9}) == 0.5
    assert herfindahl({"A": 10e9}) == 1.0
    # 0.6² + 0.3² + 0.1² = 0.46
    got = herfindahl({"A": 60e9, "B": 30e9, "C": 10e9, "D": -99e9})
    assert abs(got - 0.46) < 1e-12
    assert herfindahl({}) is None


def test_hhi_labels_match_design():
    assert hhi_label(0.10) == "dispersed"
    assert hhi_label(0.20) == "concentrated"
    assert hhi_label(0.46) == "highly concentrated"


# --- persistence -------------------------------------------------------------------------


def test_persistence_counts_consecutive_net_buy_days_from_end():
    daily = {
        D1: {"KZ": -1e9},
        D2: {"KZ": 5e9},
        D3: {"KZ": 3e9},
    }
    assert persistence(daily, "KZ") == 2


def test_persistence_breaks_on_absent_day():
    daily = {D1: {"KZ": 5e9}, D2: {}, D3: {"KZ": 3e9}}
    assert persistence(daily, "KZ") == 1
    assert persistence(daily, "YP") == 0


# --- DNA ---------------------------------------------------------------------------------


def test_dna_registry_wins():
    assert classify_dna("YP") is BrokerDNA.RETAIL
    assert classify_dna("kz") is BrokerDNA.FOREIGN_INST  # case-insensitive


def test_dna_falls_back_to_feed_investor_tag():
    assert classify_dna("XX", InvestorType.FOREIGN) is BrokerDNA.FOREIGN_INST
    assert classify_dna("XX", InvestorType.LOCAL) is BrokerDNA.UNKNOWN


def test_dna_custom_registry_overrides_default():
    assert classify_dna("YP", registry={"YP": BrokerDNA.PROP}) is BrokerDNA.PROP


# --- syndicates ---------------------------------------------------------------------------


def test_syndicate_grouping_preserves_total():
    nets = {"KZ": 60e9, "AK": 20e9, "YP": -30e9}
    grouped = syndicate_nets(nets, {"foreign-pair": ("KZ", "AK")})
    assert grouped == {"foreign-pair": 80e9, "YP": -30e9}
    assert sum(grouped.values()) == sum(nets.values())


# --- snapshot + view ------------------------------------------------------------------------


def test_snapshot_ranks_brokers_and_measures_latest_day():
    rows = [
        mk_row("KZ", Side.BUY, 60e9, D2, investor=InvestorType.FOREIGN, avg_price=101.0),
        mk_row("KZ", Side.BUY, 60e9, D3, investor=InvestorType.FOREIGN, avg_price=102.0),
        mk_row("CC", Side.BUY, 30e9, D3),
        mk_row("YP", Side.SELL, 40e9, D3),
    ]
    snap = build_snapshot("BRMS", rows, decision_ts=DECISION)
    assert [b.broker_code for b in snap.brokers] == ["KZ", "CC", "YP"]
    kz = snap.brokers[0]
    assert kz.net_value == 120e9
    assert kz.persistence_days == 2
    assert kz.avg_price == 102.0  # latest accumulator VWAP
    assert snap.top2_share == 1.0  # KZ+CC are all of the latest day's buying
    assert snap.top_sellers[0].broker_code == "YP"

    table = broker_table(snap)
    assert table[0]["broker"] == "KZ" and table[0]["net_idr_bn"] == 120.0
    panel = concentration_panel(snap)
    assert panel["top2_share_pct"] == 100.0
    assert panel["top2_names"] == "KZ, CC"


def test_matrix_unions_top_buyers_and_sellers():
    snap_a = build_snapshot(
        "BRMS", [mk_row("KZ", Side.BUY, 10e9), mk_row("YP", Side.SELL, 5e9)],
        decision_ts=DECISION,
    )
    snap_b = build_snapshot(
        "PTRO", [mk_row("CC", Side.BUY, 8e9, symbol="PTRO")], decision_ts=DECISION
    )
    matrix = buyer_seller_matrix({"BRMS": snap_a, "PTRO": snap_b})
    assert set(matrix) == {"KZ", "YP", "CC"}
    assert matrix["KZ"] == {"BRMS": 10e9}
    assert matrix["CC"] == {"PTRO": 8e9}


# --- look-ahead firewall ----------------------------------------------------------------------


def test_analyze_excludes_rows_not_yet_visible(store):
    visible = mk_row("KZ", Side.BUY, 10e9, D2, as_of=datetime(2026, 6, 30, 9, 0))
    future = mk_row("CC", Side.BUY, 99e9, D3, as_of=datetime(2026, 7, 2, 9, 0))
    store.write_broker_net([visible, future])

    snap = analyze(store, "BRMS", decision_ts=DECISION)
    codes = [b.broker_code for b in snap.brokers]
    assert codes == ["KZ"]  # CC's as_of >= decision_ts → invisible

    later = analyze(store, "BRMS", decision_ts=datetime(2026, 7, 3))
    assert {b.broker_code for b in later.brokers} == {"KZ", "CC"}


# --- veto-checks panel + stock header (design module 1) -----------------------------------------


def test_veto_checks_cover_the_full_taxonomy_fired_first():
    res = VetoResult(
        "BRMS", DECISION,
        vetoes=(Veto(VetoReason.RETAIL_FOMO, "retail brokers are 70% of buying"),),
    )
    rows = veto_checks(res)
    assert len(rows) == len(VetoReason)  # no filter silently hidden
    assert rows[0]["check"] == "RETAIL_FOMO" and rows[0]["fired"]
    assert rows[0]["detail"].startswith("retail")
    assert all(not r["fired"] and r["detail"] is None for r in rows[1:])


def test_veto_checks_all_clear():
    rows = veto_checks(VetoResult("BRMS", DECISION))
    assert not any(r["fired"] for r in rows)


def test_stock_header_price_change_and_adv():
    ch = Chart("BRMS")
    ch.add(o=100, h=102, l=98, c=100, v=1000)   # value = 100_000
    ch.add(o=100, h=104, l=99, c=102, v=1000)   # value = 102_000
    head = stock_header(ch.bars)
    assert head["price"] == 102
    assert head["change_pct"] == 2.0            # derived from prior close (feed Δ absent)
    assert head["adv_bn"] == 0.0                # 101_000 IDR ≪ 1 bn → rounds to 0.0


def test_stock_header_empty_is_absent_not_zero():
    assert stock_header([]) == {"price": None, "change_pct": None, "adv_bn": None}
