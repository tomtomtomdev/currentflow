"""Smart Money Heatmap — direction/intensity cells, the local-buy/foreign-sell
divergence alert, sector grouping, and missing-≠-zero skipping through the store."""

from __future__ import annotations

from datetime import date as Date
from datetime import datetime

import pytest
from builders import Chart, brow

from currentflow.dal.models import InvestorType, Scr0Row, Side
from currentflow.signals import heatmap
from currentflow.signals.heatmap import build_cell, by_sector

TS = datetime(2026, 7, 1, 9, 0)
D = Date(2026, 6, 26)


# --- cell math -----------------------------------------------------------------------


def test_cell_direction_intensity_and_divergence_alert():
    # local smart-money buying while foreign sells → the bandar-vs-foreign alert
    cell = build_cell("BBRI", sector="BANKS", foreign_net=-5e9, local_smart_net=8e9, market_cap=1e12)
    assert cell.direction == "SELL"
    assert cell.intensity_pct_of_cap == pytest.approx(0.5)   # 5e9 / 1e12 · 100
    assert cell.divergence_alert is True


def test_no_divergence_when_both_sides_agree():
    cell = build_cell("BBRI", sector="BANKS", foreign_net=5e9, local_smart_net=8e9, market_cap=1e12)
    assert cell.direction == "BUY"
    assert cell.divergence_alert is False


def test_intensity_none_without_market_cap():
    cell = build_cell("X", sector="UNKNOWN", foreign_net=5e9, local_smart_net=0, market_cap=None)
    assert cell.intensity_pct_of_cap is None


# --- store integration ---------------------------------------------------------------


def _bars(symbol, net_foreign):
    return Chart(symbol).add(100, 101, 99, 100, 1000, nf=net_foreign).bars


def test_heatmap_reads_flow_groups_by_sector_and_skips_missing(store, caplog):
    store.write_daily_bars(_bars("BBRI", -5e9))
    store.write_broker_net([brow("DX", Side.BUY, 8e9, D, symbol="BBRI", investor=InvestorType.LOCAL)])
    store.write_daily_bars(_bars("ASII", 4e9))
    store.write_scr0_eligible([
        Scr0Row("BBRI", D, datetime(2026, 6, 27, 9, 0), 5e11, 4500, 40.0, 1e12),
    ])
    with caplog.at_level("WARNING"):
        cells = heatmap.heatmap(
            store, ["BBRI", "ASII", "GOTO"], TS, sector_map={"BBRI": "BANKS", "ASII": "AUTO"}
        )
    by_sym = {c.symbol: c for c in cells}
    assert set(by_sym) == {"BBRI", "ASII"}          # GOTO had no flow → skipped
    assert by_sym["BBRI"].divergence_alert is True   # DX (local) buys, foreign sells
    assert by_sym["BBRI"].intensity_pct_of_cap == pytest.approx(0.5)
    assert by_sym["ASII"].intensity_pct_of_cap is None   # no SCR-0 market cap visible
    assert "skipped" in caplog.text

    groups = by_sector(cells)
    assert set(groups) == {"BANKS", "AUTO"}
