"""Sector Rotation Map (spec §9) — RS-vs-flow quadrant classification, sector flow +
relative-strength aggregation, look-ahead safety, and missing-≠-zero handling."""

from __future__ import annotations

from datetime import datetime

import pytest
from builders import Chart

from currentflow.dal.timing import ohlcv_as_of
from currentflow.signals.sector_rotation import (
    Quadrant,
    build_sector_rotation,
    classify_quadrant,
)

TS = datetime(2026, 3, 1, 9, 0)   # well after every bar below is knowable


def _sym(store, symbol, closes, nfs):
    ch = Chart(symbol)
    for c, nf in zip(closes, nfs):
        ch.add(c, c + 1, c - 1, c, 1000, nf=nf)
    store.write_daily_bars(ch.bars)
    return ch


# --- quadrant math -------------------------------------------------------------------


def test_classify_quadrant_all_four_corners():
    assert classify_quadrant(0.05, 5e9) is Quadrant.LEADERS            # strong + inflow
    assert classify_quadrant(-0.05, 5e9) is Quadrant.EARLY_RECOVERY    # weak + inflow
    assert classify_quadrant(0.05, -5e9) is Quadrant.DISTRIBUTION_WARN # strong + outflow
    assert classify_quadrant(-0.05, -5e9) is Quadrant.AVOID            # weak + outflow


def test_classify_quadrant_boundaries_count_as_strong_inflow():
    # zero on either axis resolves to the ≥0 side (deterministic, no dead zone)
    assert classify_quadrant(0.0, 0.0) is Quadrant.LEADERS


# --- aggregation over the store ------------------------------------------------------


def test_relative_strength_flow_and_quadrant(store):
    # TECH: AAA +10% (+5bn), BBB +6% (+3bn); BANK: CCC −5% (−4bn)
    _sym(store, "AAA", [100, 110], [2e9, 3e9])
    _sym(store, "BBB", [100, 106], [1e9, 2e9])
    _sym(store, "CCC", [100, 95], [-2e9, -2e9])
    smap = {"AAA": "TECH", "BBB": "TECH", "CCC": "BANK"}

    rows = {r.sector: r for r in build_sector_rotation(store, ["AAA", "BBB", "CCC"], TS, sector_map=smap)}
    market = (0.10 + 0.06 - 0.05) / 3

    tech = rows["TECH"]
    assert tech.market_return == pytest.approx(market)
    assert tech.price_return == pytest.approx(0.08)
    assert tech.relative_strength == pytest.approx(0.08 - market)
    assert tech.net_foreign_flow == pytest.approx(8e9)
    assert tech.tide == "BUY"
    assert tech.quadrant is Quadrant.LEADERS

    bank = rows["BANK"]
    assert bank.price_return == pytest.approx(-0.05)
    assert bank.net_foreign_flow == pytest.approx(-4e9)
    assert bank.tide == "SELL"
    assert bank.quadrant is Quadrant.AVOID


# --- look-ahead: the future is invisible ---------------------------------------------


def test_lookahead_last_bar_excluded_until_knowable(store):
    ch = _sym(store, "AAA", [100, 105, 110], [2e9, 3e9, 5e9])
    smap = {"AAA": "TECH"}

    full = build_sector_rotation(store, ["AAA"], TS, sector_map=smap)[0]
    assert full.net_foreign_flow == pytest.approx(10e9)     # all three days

    before_last = ohlcv_as_of(ch.bars[-1].date)             # last bar not yet knowable
    blind = build_sector_rotation(store, ["AAA"], before_last, sector_map=smap)[0]
    assert blind.net_foreign_flow == pytest.approx(5e9)     # last +5bn excluded


# --- missing ≠ zero ------------------------------------------------------------------


def test_symbol_with_no_data_is_skipped_and_logged(store, caplog):
    _sym(store, "AAA", [100, 110], [2e9, 3e9])
    with caplog.at_level("WARNING"):
        rows = build_sector_rotation(
            store, ["AAA", "ZZZ"], TS, sector_map={"AAA": "TECH", "ZZZ": "BANK"}
        )
    sectors = {r.sector for r in rows}
    assert sectors == {"TECH"}          # ZZZ had no data → no BANK row, not a zero row
    assert "skipped" in caplog.text


def test_sector_without_visible_flow_has_no_quadrant(store):
    _sym(store, "AAA", [100, 110], [None, None])   # a return, but no foreign net
    r = build_sector_rotation(store, ["AAA"], TS, sector_map={"AAA": "TECH"})[0]
    assert r.price_return == pytest.approx(0.10)
    assert r.net_foreign_flow is None
    assert r.quadrant is None                       # missing axis → no guessed corner
