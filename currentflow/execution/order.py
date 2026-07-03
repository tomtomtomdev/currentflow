"""Order generation (spec §6, pipeline step [8]) — limit only, sized to 1% risk.

Turns a valid technical trigger into a **limit** order (never a market order, LD-3),
sized so the distance to the invalidation stop risks exactly 1% of equity (§6, the "IDX
manipulation tax"), scaled by the §7 conviction multiplier, then clamped by the §6
exposure caps and blocked by the §6 circuit breakers.

Acceptance invariant (§13): every accepted order is a LIMIT with a defined stop and
R:R ≥ 2:1 — guaranteed because sizing only runs on a `trigger.valid` signal (R:R already
cleared in `execution.trigger`) and the order carries the stop through.

    qty = floor( (equity × 1% × conviction) / (entry − stop) ) rounded down to whole lots
          then reduced to fit ≤ 10% equity / name and ≤ 30% equity / sector (§6)

`missing ≠ zero`: a sub-lot budget, a cap that leaves < 1 lot, or an open circuit breaker
is a typed REJECTED order with a reason — never a silent zero-size fill.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from currentflow import config
from currentflow.dal.models import BoardType, Side
from currentflow.execution.trigger import TriggerSignal
from currentflow.fundamentals.tilt import FundamentalTilt
from currentflow.paper.fill import LiquidityTier, tier_for_adv
from currentflow.signals.risk_monitor import CircuitState, Portfolio, sector_values


class OrderStatus(str, Enum):
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"


@dataclass(frozen=True, slots=True)
class Order:
    """A sized limit order for the paper fill engine. LIMIT only, stop always defined."""

    symbol: str
    decision_ts: datetime
    status: OrderStatus
    side: Side
    order_type: str            # always "LIMIT" (LD-3 — no market orders)
    limit_price: float | None
    qty: int
    stop: float | None
    target: float | None
    rr: float | None
    risk_idr: float | None     # equity × 1% × conviction (risk budgeted to the stop)
    tilt_kind: str | None
    board: BoardType
    tier: LiquidityTier
    reason: str

    @property
    def accepted(self) -> bool:
        return self.status is OrderStatus.ACCEPTED

    @property
    def notional(self) -> float | None:
        return None if self.limit_price is None or self.qty <= 0 else self.limit_price * self.qty


def _lots(qty_shares: float) -> int:
    """Round a raw share count down to whole IDX lots (× 100)."""
    return (int(qty_shares) // config.LOT_SIZE) * config.LOT_SIZE


def generate_order(
    trigger: TriggerSignal,
    *,
    equity: float,
    tilt: FundamentalTilt,
    sector: str,
    board: BoardType = BoardType.MAIN,
    adv20: float | None = None,
    portfolio: Portfolio | None = None,
    circuit: CircuitState = CircuitState.OK,
) -> Order:
    """Size a limit order from a valid trigger, clamped by §6 caps and circuit breakers."""
    tier = tier_for_adv(adv20)

    def reject(reason: str) -> Order:
        return Order(
            symbol=trigger.symbol, decision_ts=trigger.decision_ts, status=OrderStatus.REJECTED,
            side=Side.BUY, order_type="LIMIT", limit_price=None, qty=0, stop=trigger.stop,
            target=trigger.target, rr=trigger.rr, risk_idr=None, tilt_kind=tilt.kind.value,
            board=board, tier=tier, reason=reason,
        )

    # §6 circuit breakers halt NEW entries first.
    if circuit is not CircuitState.OK:
        return reject(f"circuit breaker {circuit.value} — no new entries (§6)")
    if not trigger.valid or trigger.entry_limit is None or trigger.stop is None:
        return reject(f"no valid trigger ({trigger.reason})")

    entry, stop = trigger.entry_limit, trigger.stop
    risk_per_share = entry - stop
    if risk_per_share <= 0:
        return reject("non-positive risk per share (entry ≤ stop)")

    risk_idr = equity * config.RISK_PCT * tilt.multiplier
    qty = _lots(risk_idr / risk_per_share)
    if qty < config.LOT_SIZE:
        return reject(f"1% risk budget ({risk_idr:,.0f}) buys < 1 lot at risk {risk_per_share:.2f}/sh")

    # §6 exposure caps — reduce qty to fit ≤ 10% / name and ≤ 30% / sector of equity.
    name_cap_idr = equity * config.EXPOSURE_CAP_NAME
    if qty * entry > name_cap_idr:
        qty = _lots(name_cap_idr / entry)

    sector_now = sector_values(portfolio).get(sector, 0.0) if portfolio is not None else 0.0
    sector_room = equity * config.EXPOSURE_CAP_SECTOR - sector_now
    if qty * entry > sector_room:
        qty = _lots(max(0.0, sector_room) / entry)

    if qty < config.LOT_SIZE:
        return reject("exposure caps leave < 1 lot (§6 per-name/per-sector limits)")

    return Order(
        symbol=trigger.symbol, decision_ts=trigger.decision_ts, status=OrderStatus.ACCEPTED,
        side=Side.BUY, order_type="LIMIT", limit_price=entry, qty=qty, stop=stop,
        target=trigger.target, rr=trigger.rr, risk_idr=risk_idr, tilt_kind=tilt.kind.value,
        board=board, tier=tier,
        reason=(
            f"LIMIT {qty}@{entry:.2f}, stop {stop:.2f}, R:R {trigger.rr:.2f}, "
            f"{tilt.kind.value} ×{tilt.multiplier} → risk {risk_idr:,.0f} ({config.RISK_PCT:.0%} eq)"
        ),
    )
