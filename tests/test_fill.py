"""IDX paper fill engine (§12) — hand-checked lot / tick / ARA-ARB / fee / slippage /
T+2 cases (acceptance criterion: "fill engine reproduces … against hand-checked cases").
"""

from __future__ import annotations

from datetime import date as Date

from currentflow import config
from currentflow.dal.models import BoardType, Side
from currentflow.paper.fill import (
    FillStatus,
    LiquidityTier,
    compute_fees,
    fill_order,
    round_to_tick,
    settlement_date,
    tick_size,
    tier_for_adv,
)

ORDER_DAY = Date(2026, 7, 1)  # a Wednesday


# --- tick bands ----------------------------------------------------------------------


def test_tick_size_bands():
    assert tick_size(100) == 1.0        # < 200
    assert tick_size(199) == 1.0
    assert tick_size(200) == 2.0        # 200–<500
    assert tick_size(499) == 2.0
    assert tick_size(500) == 5.0        # 500–<2000
    assert tick_size(1995) == 5.0
    assert tick_size(2000) == 10.0      # 2000–<5000
    assert tick_size(5000) == 25.0      # ≥ 5000
    assert tick_size(9975) == 25.0


def test_round_to_tick_modes():
    assert round_to_tick(1003, "down") == 1000.0   # tick 5
    assert round_to_tick(1003, "up") == 1005.0
    assert round_to_tick(1003, "nearest") == 1005.0
    assert round_to_tick(1000, "up") == 1000.0      # already on a tick
    assert round_to_tick(207, "up") == 208.0        # tick 2


# --- slippage tiers ------------------------------------------------------------------


def test_tier_for_adv():
    assert tier_for_adv(200e9) is LiquidityTier.LARGE
    assert tier_for_adv(30e9) is LiquidityTier.MID
    assert tier_for_adv(5e9) is LiquidityTier.SMALL
    assert tier_for_adv(None) is LiquidityTier.SMALL   # unknown → conservative (widest)


# --- fee stack (hand-checked) --------------------------------------------------------


def test_buy_fee_stack_hand_checked():
    # gross 10,000,000: commission 0.15% = 15,000; VAT 11% of that = 1,650;
    # levy 0.043% = 4,300; no sell tax.
    f = compute_fees(10_000_000, Side.BUY)
    assert f.commission == 15_000.0
    assert round(f.vat, 6) == 1_650.0
    assert round(f.levy, 6) == 4_300.0
    assert f.sell_tax == 0.0
    assert round(f.total, 6) == 20_950.0


def test_sell_fee_stack_hand_checked():
    # gross 11,000,000: commission 0.25% = 27,500; VAT = 3,025; levy = 4,730;
    # sell tax 0.1% = 11,000.
    f = compute_fees(11_000_000, Side.SELL)
    assert f.commission == 27_500.0
    assert round(f.vat, 6) == 3_025.0
    assert round(f.levy, 6) == 4_730.0
    assert f.sell_tax == 11_000.0
    assert round(f.total, 6) == 46_255.0


# --- settlement ----------------------------------------------------------------------


def test_settlement_t_plus_2_skips_weekend():
    assert settlement_date(Date(2026, 7, 1)) == Date(2026, 7, 3)   # Wed → Fri
    assert settlement_date(Date(2026, 7, 2)) == Date(2026, 7, 6)   # Thu → Mon (skip weekend)
    assert settlement_date(Date(2026, 7, 3)) == Date(2026, 7, 7)   # Fri → Tue


# --- fills ---------------------------------------------------------------------------


def test_buy_fill_at_open_with_slippage_and_fees():
    # LARGE tier slippage 0.1%: open 1000 → 1001 → tick(5)-nearest → 1000; ≤ limit 1005.
    f = fill_order(
        symbol="BBRI", side=Side.BUY, limit_price=1005, qty=10_000,
        order_date=ORDER_DAY, next_open=1000, prev_close=1000,
        board=BoardType.MAIN, tier=LiquidityTier.LARGE,
    )
    assert f.status is FillStatus.FILLED
    assert f.fill_price == 1000.0
    assert f.gross == 10_000_000.0
    assert round(f.fees.total, 6) == 20_950.0
    assert round(f.cash_flow, 6) == -10_020_950.0     # cash out = gross + fees
    assert f.settlement_date == Date(2026, 7, 3)


def test_sell_fill_slips_down_and_nets_fees():
    # LARGE slippage 0.1%: open 1100 → 1098.9 → tick(5)-nearest → 1100; ≥ limit 1095.
    f = fill_order(
        symbol="BBRI", side=Side.SELL, limit_price=1095, qty=10_000,
        order_date=ORDER_DAY, next_open=1100, prev_close=1050,
        board=BoardType.MAIN, tier=LiquidityTier.LARGE,
    )
    assert f.status is FillStatus.FILLED
    assert f.fill_price == 1100.0
    assert f.gross == 11_000_000.0
    assert round(f.cash_flow, 6) == 11_000_000.0 - 46_255.0    # cash in = gross − fees


def test_buy_above_limit_does_not_fill():
    # open 1010 above the 1000 buy limit → limit discipline, NO_FILL.
    f = fill_order(
        symbol="X", side=Side.BUY, limit_price=1000, qty=1_000,
        order_date=ORDER_DAY, next_open=1010, prev_close=1005, tier=LiquidityTier.LARGE,
    )
    assert f.status is FillStatus.NO_FILL
    assert f.fill_price is None and f.cash_flow is None


def test_sell_below_limit_does_not_fill():
    f = fill_order(
        symbol="X", side=Side.SELL, limit_price=1000, qty=1_000,
        order_date=ORDER_DAY, next_open=990, prev_close=1005, tier=LiquidityTier.LARGE,
    )
    assert f.status is FillStatus.NO_FILL


def test_buy_slippage_capped_at_limit():
    # SMALL slippage 1.2%: open 1000 → 1012 → tick-nearest 1010, but limit is 1005 →
    # cannot fill above limit → cap at round_down(1005) = 1005.
    f = fill_order(
        symbol="X", side=Side.BUY, limit_price=1005, qty=1_000,
        order_date=ORDER_DAY, next_open=1000, prev_close=1000, tier=LiquidityTier.SMALL,
    )
    assert f.status is FillStatus.FILLED
    assert f.fill_price == 1005.0                       # capped at the limit, never above


def test_buy_into_ara_lock_rejected():
    # next open +7% on a main-board name = ARA locked; a buy has no offers → reject.
    f = fill_order(
        symbol="X", side=Side.BUY, limit_price=1075, qty=1_000,
        order_date=ORDER_DAY, next_open=1070, prev_close=1000, board=BoardType.MAIN,
        tier=LiquidityTier.LARGE,
    )
    assert f.status is FillStatus.REJECTED_BAND


def test_sell_into_arb_lock_rejected():
    f = fill_order(
        symbol="X", side=Side.SELL, limit_price=925, qty=1_000,
        order_date=ORDER_DAY, next_open=930, prev_close=1000, board=BoardType.MAIN,
        tier=LiquidityTier.LARGE,
    )
    assert f.status is FillStatus.REJECTED_BAND


def test_buy_into_arb_lock_is_allowed():
    # A buy into a limit-DOWN open is fine (panic sellers) — only the adverse lock rejects.
    f = fill_order(
        symbol="X", side=Side.BUY, limit_price=935, qty=1_000,
        order_date=ORDER_DAY, next_open=930, prev_close=1000, board=BoardType.MAIN,
        tier=LiquidityTier.LARGE,
    )
    assert f.status is FillStatus.FILLED


def test_odd_lot_rejected():
    f = fill_order(
        symbol="X", side=Side.BUY, limit_price=1000, qty=150,   # not a multiple of 100
        order_date=ORDER_DAY, next_open=1000, prev_close=1000, tier=LiquidityTier.LARGE,
    )
    assert f.status is FillStatus.REJECTED_LOT


def test_config_locks_full_fee_stack():
    # §12 posture: the four fee components exist and the sell tax is exactly 0.1%.
    assert config.FEE_SELL_TAX == 0.001
    assert config.LOT_SIZE == 100
    assert config.SETTLEMENT_DAYS == 2
