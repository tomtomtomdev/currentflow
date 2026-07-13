"""Veto filters (§5) — each trap fires on its labeled case, and a clean
accumulation passes untouched. RULE B: vetoes are categorical reasons, not numbers.
"""

from __future__ import annotations

from datetime import date as Date
from datetime import datetime

from builders import (
    Chart,
    brow,
    concentrated_buyer_rows,
    distribution_bars,
    phase_b_bars,
    phase_c_bars,
)

from currentflow.dal.models import InvestorType, Side
from currentflow.signals import broker_flow, phase
from currentflow.signals.veto import VetoReason, evaluate_vetoes

TS = datetime(2026, 7, 1, 9, 0)
D1, D2, D3 = Date(2026, 6, 24), Date(2026, 6, 25), Date(2026, 6, 26)


def _snap(rows):
    return broker_flow.build_snapshot("X", rows, decision_ts=TS)


def _veto(rows, bars=None, **kw):
    bars = bars if bars is not None else phase_c_bars()
    return evaluate_vetoes("X", broker=_snap(rows), bars=bars,
                           phase_cls=phase.classify("X", bars, TS), decision_ts=TS, **kw)


# --- clean accumulation passes -------------------------------------------------------


def test_clean_accumulation_has_no_veto():
    res = _veto(concentrated_buyer_rows("X", [D1, D2, D3]))
    assert res.rejected is False
    assert res.vetoes == ()


# --- manipulation / trap detectors ---------------------------------------------------


def test_single_bandar_monopoly():
    rows = [brow("DX", Side.BUY, 7e9, D3), brow("KI", Side.BUY, 3e9, D3), brow("YP", Side.SELL, 2e9, D3)]
    assert VetoReason.SINGLE_BANDAR_MONOPOLY in _veto(rows).reasons   # top-1 = 70%


def test_distribution_phase_is_vetoed():
    bars = distribution_bars()
    res = _veto(concentrated_buyer_rows("X", [D1, D2, D3]), bars=bars)
    assert VetoReason.DISTRIBUTION_DRESSED in res.reasons


def test_sustained_dominant_buyer_flip_to_net_sell():
    rows = [
        brow("DX", Side.BUY, 40e9, D1),                     # accumulates → stays window-dominant
        brow("DX", Side.SELL, 12e9, D2), brow("DX", Side.SELL, 12e9, D3),   # net-sells 2 days running
        brow("KI", Side.BUY, 2e9, D3), brow("CC", Side.BUY, 1e9, D3),
    ]
    assert VetoReason.DISTRIBUTION_DRESSED in _veto(rows).reasons


def test_single_day_flip_is_not_distribution():
    """One red day for the dominant buyer is noise, not a flip — must not veto."""
    rows = [
        brow("DX", Side.BUY, 10e9, D1), brow("DX", Side.BUY, 10e9, D2),
        brow("DX", Side.SELL, 12e9, D3),                    # only the latest day is negative
        brow("KI", Side.BUY, 2e9, D3), brow("CC", Side.BUY, 1e9, D3),
    ]
    assert VetoReason.DISTRIBUTION_DRESSED not in _veto(rows).reasons


def test_markup_on_thin_volume():
    ch = Chart("X")
    for _ in range(20):
        ch.add(100, 101, 99, 100, 1000)
    ch.add(100, 106, 100, 105, 900)                          # +5% on 0.9× volume
    assert VetoReason.MARKUP_ON_THIN_VOLUME in _veto([], bars=ch.bars).reasons


def test_wash_churn():
    rows = [brow("BQ", Side.BUY, 5e9, D3), brow("BQ", Side.SELL, 4.5e9, D3), brow("KI", Side.BUY, 1e9, D3)]
    assert VetoReason.WASH_CHURN in _veto(rows).reasons


def test_broker_rotation():
    rows = [
        brow("AX", Side.BUY, 10e9, D1), brow("ZZ", Side.SELL, 1e9, D1),
        brow("BX", Side.BUY, 10e9, D2), brow("ZZ", Side.SELL, 1e9, D2),
        brow("CX", Side.BUY, 10e9, D3), brow("ZZ", Side.SELL, 1e9, D3),
    ]
    assert VetoReason.BROKER_ROTATION in _veto(rows).reasons     # top buyer changes daily


# --- noise / context filters ---------------------------------------------------------


def test_retail_fomo():
    rows = [
        brow("YP", Side.BUY, 4e9, D3, investor=InvestorType.LOCAL),
        brow("PD", Side.BUY, 4e9, D3),
        brow("GR", Side.BUY, 4e9, D3),
        brow("KI", Side.BUY, 1e9, D3),                          # only smart-money buyer
    ]
    assert VetoReason.RETAIL_FOMO in _veto(rows).reasons          # retail = 92% of buying


def test_event_driven_flag():
    res = _veto(concentrated_buyer_rows("X", [D1, D2, D3]), has_material_news=True)
    assert VetoReason.EVENT_DRIVEN in res.reasons


def test_phase_mismatch_restated():
    res = _veto(concentrated_buyer_rows("X", [D1, D2, D3]), bars=phase_b_bars())
    assert VetoReason.PHASE_MISMATCH in res.reasons
    assert res.rejected is True
