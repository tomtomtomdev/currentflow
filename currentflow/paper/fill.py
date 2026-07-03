"""IDX-aware paper fill engine (spec §12) — the ONE fill engine.

`LOCKED_SPEC.md` §11/§13: backtest and forward-paper are separate code paths that
**share this single fill engine**; every reported return is net of the full fee stack
it charges here. The engine reproduces the IDX microstructure the operator actually
trades into:

    - lots of 100 shares                          (LOT_SIZE)
    - price ticks (fraksi harga) by price band    (TICK_BANDS)
    - ARA/ARB auto-reject: an order into a locked band on its adverse side cannot fill
    - next-open fills with liquidity-tiered slippage (buys slip up, sells slip down)
    - limit discipline: a buy above its limit / a sell below it does NOT fill
    - full fee stack: broker commission + levy + VAT (on commission) + 0.1% sell tax
    - T+2 settlement

RULE-adjacent discipline (§6 / LD-3): the engine only *fills* limit orders — it never
invents a market fill. A gap through the band or past the limit is an honest NO_FILL /
REJECTED_BAND, not a fabricated execution. `missing ≠ zero`: an un-fillable order is a
typed non-fill, never a silent zero-cost trade.

Fee math is auditable line-by-line so the §13 hand-checked cases pin every component.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as Date
from datetime import timedelta
from enum import Enum

from currentflow import config
from currentflow.dal.models import BoardType, Side
from currentflow.universe import bands
from currentflow.universe.bands import PinState


# --- liquidity tier (slippage, §12) --------------------------------------------------


class LiquidityTier(str, Enum):
    LARGE = "LARGE"   # LQ45 / large-cap — 0.05–0.15% slippage
    MID = "MID"       # mid-cap — 0.2–0.5%
    SMALL = "SMALL"   # small-cap — >1%


_TIER_SLIPPAGE = {
    LiquidityTier.LARGE: config.SLIPPAGE_LARGE,
    LiquidityTier.MID: config.SLIPPAGE_MID,
    LiquidityTier.SMALL: config.SLIPPAGE_SMALL,
}


def tier_for_adv(adv20: float | None) -> LiquidityTier:
    """Assign the slippage tier from 20-day ADV. Unknown ADV is treated as SMALL —
    the most conservative (widest slippage), never optimistically LARGE."""
    if adv20 is None:
        return LiquidityTier.SMALL
    if adv20 >= config.SLIPPAGE_LARGE_ADV_IDR:
        return LiquidityTier.LARGE
    if adv20 >= config.SLIPPAGE_MID_ADV_IDR:
        return LiquidityTier.MID
    return LiquidityTier.SMALL


def slippage_for(tier: LiquidityTier) -> float:
    return _TIER_SLIPPAGE[tier]


# --- tick sizing (fraksi harga, §12) -------------------------------------------------


def tick_size(price: float) -> float:
    """IDX tick for a price — the last band whose lower bound the price meets."""
    tick = config.TICK_BANDS[0][1]
    for lower, t in config.TICK_BANDS:
        if price >= lower:
            tick = t
        else:
            break
    return tick


def round_to_tick(price: float, mode: str = "nearest") -> float:
    """Round `price` to a valid IDX tick. mode ∈ {'nearest','up','down'}."""
    tick = tick_size(price)
    q = price / tick
    if mode == "up":
        n = int(q) if abs(q - round(q)) < 1e-9 else int(q) + 1
    elif mode == "down":
        n = int(q)
    else:
        n = int(q + 0.5)
    return round(n * tick, 6)


# --- fee stack (§12) -----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FeeBreakdown:
    """Every line of the §12 fee stack, so a fill's cost is auditable."""

    commission: float
    vat: float          # PPN 11% on the broker commission
    levy: float         # IDX + KPEI + KSEI transaction levy
    sell_tax: float     # 0.1% final sales tax (sell side only)

    @property
    def total(self) -> float:
        return self.commission + self.vat + self.levy + self.sell_tax


def compute_fees(notional: float, side: Side) -> FeeBreakdown:
    """The full fee stack on a `notional` (price × qty) for one side."""
    rate = config.FEE_COMMISSION_BUY if side is Side.BUY else config.FEE_COMMISSION_SELL
    commission = notional * rate
    vat = commission * config.FEE_VAT
    levy = notional * config.FEE_LEVY
    sell_tax = notional * config.FEE_SELL_TAX if side is Side.SELL else 0.0
    return FeeBreakdown(commission=commission, vat=vat, levy=levy, sell_tax=sell_tax)


# --- settlement (T+2) ----------------------------------------------------------------


def settlement_date(fill_date: Date, days: int = config.SETTLEMENT_DAYS) -> Date:
    """T+`days` settlement counting business days (weekends skipped). IDX holidays are
    not modelled here — a conservative business-day count, refined when a calendar lands."""
    d = fill_date
    added = 0
    while added < days:
        d += timedelta(days=1)
        if d.weekday() < 5:
            added += 1
    return d


# --- fill result ---------------------------------------------------------------------


class FillStatus(str, Enum):
    FILLED = "FILLED"
    NO_FILL = "NO_FILL"              # limit not reached at the open (limit discipline)
    REJECTED_BAND = "REJECTED_BAND"  # locked ARA/ARB on the order's adverse side
    REJECTED_LOT = "REJECTED_LOT"    # qty not a positive whole-lot multiple


@dataclass(frozen=True, slots=True)
class Fill:
    symbol: str
    side: Side
    status: FillStatus
    order_date: Date
    fill_date: Date | None
    requested_limit: float
    qty: int
    tier: LiquidityTier
    fill_price: float | None
    slippage_pct: float
    gross: float | None                 # fill_price × qty
    fees: FeeBreakdown | None
    cash_flow: float | None             # signed: buy = −(gross+fees); sell = +(gross−fees)
    settlement_date: Date | None
    reason: str

    @property
    def filled(self) -> bool:
        return self.status is FillStatus.FILLED


def fill_order(
    *,
    symbol: str,
    side: Side,
    limit_price: float,
    qty: int,
    order_date: Date,
    next_open: float,
    prev_close: float,
    board: BoardType = BoardType.MAIN,
    tier: LiquidityTier = LiquidityTier.SMALL,
    settle_days: int = config.SETTLEMENT_DAYS,
) -> Fill:
    """Attempt to fill one limit order at the next session open (spec §12).

    Order of checks: lot integrity → ARA/ARB band (adverse side) → next-open + slippage
    + tick rounding → limit discipline → fee stack → T+2 settlement.
    """
    slip = slippage_for(tier)

    def result(status: FillStatus, reason: str, *, fill_date=None, fill_price=None,
               gross=None, fees=None, cash_flow=None, settle=None) -> Fill:
        return Fill(
            symbol=symbol, side=side, status=status, order_date=order_date,
            fill_date=fill_date, requested_limit=limit_price, qty=qty, tier=tier,
            fill_price=fill_price, slippage_pct=slip, gross=gross, fees=fees,
            cash_flow=cash_flow, settlement_date=settle, reason=reason,
        )

    # [1] lot integrity — IDX trades whole lots of 100 (§12).
    if qty <= 0 or qty % config.LOT_SIZE != 0:
        return result(FillStatus.REJECTED_LOT, f"qty {qty} is not a positive multiple of {config.LOT_SIZE}")

    # [2] ARA/ARB auto-reject on the adverse side: a locked limit-up has no sellers to
    # buy from; a locked limit-down has no buyers to sell to (no fillable band → reject).
    if prev_close > 0:
        band = bands.check_pinned(next_open, prev_close, board)
        if side is Side.BUY and band.state is PinState.PINNED_ARA:
            return result(FillStatus.REJECTED_BAND, f"open locked ARA (+{band.move_pct:.1%}) — no offers to buy")
        if side is Side.SELL and band.state is PinState.PINNED_ARB:
            return result(FillStatus.REJECTED_BAND, f"open locked ARB ({band.move_pct:.1%}) — no bids to sell")

    # [3] next-open + slippage (adverse), rounded to a valid tick.
    if side is Side.BUY:
        raw = next_open * (1 + slip)
        candidate = round_to_tick(raw)
        # [4] limit discipline: a buy cannot fill above its limit.
        if next_open > limit_price:
            return result(FillStatus.NO_FILL, f"open {next_open:.2f} above buy limit {limit_price:.2f}")
        fill_price = candidate if candidate <= limit_price else round_to_tick(limit_price, "down")
    else:
        raw = next_open * (1 - slip)
        candidate = round_to_tick(raw)
        if next_open < limit_price:
            return result(FillStatus.NO_FILL, f"open {next_open:.2f} below sell limit {limit_price:.2f}")
        fill_price = candidate if candidate >= limit_price else round_to_tick(limit_price, "up")

    gross = fill_price * qty
    fees = compute_fees(gross, side)
    cash_flow = -(gross + fees.total) if side is Side.BUY else (gross - fees.total)
    settle = settlement_date(order_date, settle_days)
    return result(
        FillStatus.FILLED, f"filled at {fill_price:.2f} (open {next_open:.2f}, slip {slip:.2%})",
        fill_date=order_date, fill_price=fill_price, gross=gross, fees=fees,
        cash_flow=cash_flow, settle=settle,
    )
