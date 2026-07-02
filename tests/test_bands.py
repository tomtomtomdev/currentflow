"""ARA/ARB band derivation — hand-checked cases (spec §12; DATA_SOURCES §3.2)."""

from __future__ import annotations

import pytest

from currentflow.dal.models import BoardType
from currentflow.universe.bands import PinState, band_pct, check_pinned


# --- band selection -----------------------------------------------------------------


def test_main_board_band_is_7pct():
    assert band_pct(BoardType.MAIN, 1000) == 0.07


def test_dev_board_band_tiers_by_price():
    assert band_pct(BoardType.DEVELOPMENT, 200) == 0.25   # < 5000 → wide
    assert band_pct(BoardType.DEVELOPMENT, 6000) == 0.10  # ≥ 5000 → tight


def test_ipo_band_overrides_board_for_first_15_trading_days():
    assert band_pct(BoardType.MAIN, 1000, trading_days_since_ipo=1) == 0.35
    assert band_pct(BoardType.DEVELOPMENT, 200, trading_days_since_ipo=15) == 0.35
    # day 16 → back to the board band
    assert band_pct(BoardType.MAIN, 1000, trading_days_since_ipo=16) == 0.07


def test_unknown_board_falls_back_to_main_band():
    assert band_pct(BoardType.UNKNOWN, 1000) == 0.07


# --- pinned math (hand-checked) --------------------------------------------------------


def test_exact_ara_close_is_pinned():
    # main board: prev 1000, close 1070 → +7.0% ≥ 7% − 0.5%
    chk = check_pinned(1070, 1000, BoardType.MAIN)
    assert chk.state is PinState.PINNED_ARA
    assert chk.pinned
    assert chk.move_pct == pytest.approx(0.07)


def test_exact_arb_close_is_pinned():
    chk = check_pinned(930, 1000, BoardType.MAIN)
    assert chk.state is PinState.PINNED_ARB


def test_epsilon_catches_tick_rounding_at_the_band():
    # +6.6% is within ε=0.5% of the 7% band → still pinned
    assert check_pinned(1066, 1000, BoardType.MAIN).pinned
    # +6.0% is clearly inside the band → free
    assert check_pinned(1060, 1000, BoardType.MAIN).state is PinState.FREE


def test_dev_board_wide_band():
    # prev 200 (< 5000) → ±25%: close 250 pinned, close 240 free
    assert check_pinned(250, 200, BoardType.DEVELOPMENT).pinned
    assert not check_pinned(240, 200, BoardType.DEVELOPMENT).pinned


def test_ipo_band_applies_to_pin_check():
    # ±35% for the first 15 trading days: +25% is NOT pinned during IPO window
    chk = check_pinned(250, 200, BoardType.DEVELOPMENT, trading_days_since_ipo=5)
    assert chk.band_pct == 0.35
    assert not chk.pinned


def test_nonpositive_prev_close_raises():
    with pytest.raises(ValueError):
        check_pinned(100, 0, BoardType.MAIN)
