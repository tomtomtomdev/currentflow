"""The closed paper-trade atom (spec §8/§11) — the unit the validation engine counts.

A `PaperTrade` is one entry `Fill` + one exit `Fill`, both produced by the ONE shared
IDX fill engine (`paper.fill`). Its P&L is therefore **net of the full fee stack by
construction**: the fills already carry signed `cash_flow` (buy = −(gross+fees); sell =
+(gross−fees)), so `net_pnl = entry.cash_flow + exit.cash_flow`. No fee math is redone
here — the engine is the sole authority, so backtest and forward-paper (which both build
`PaperTrade`s from the same engine) reconcile exactly (§13).

RULE B posture: a `PaperTrade` is a realised paper result, not a displayed prediction.
The metrics computed over a *validated* track record are what the promotion engine reads;
until a module is promoted, none of these numbers surface on that module (LD-9).

`missing ≠ zero`: a trade is only ever constructed from two FILLED fills — an un-fillable
order never becomes a zero-P&L trade, it is simply no trade.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as Date

from currentflow.dal.models import Side
from currentflow.execution.risk import ExitReason
from currentflow.paper.fill import Fill, FillStatus


@dataclass(frozen=True, slots=True)
class PaperTrade:
    """One round-trip paper position, closed. All P&L is net of the §12 fee stack."""

    symbol: str
    track: str
    tilt_kind: str
    entry_date: Date
    exit_date: Date
    qty: int
    entry_price: float           # fill price (post-slippage, tick-rounded)
    exit_price: float
    entry_fee: float             # full fee stack on the entry fill
    exit_fee: float              # full fee stack on the exit fill
    exit_reason: ExitReason
    stop: float
    risk_idr: float | None       # 1%-risk budget sized to the stop (for R-multiple)

    # --- P&L, all net of the full fee stack (engine cash flows) ----------------------
    @property
    def entry_notional(self) -> float:
        return self.entry_price * self.qty

    @property
    def fee_total(self) -> float:
        return self.entry_fee + self.exit_fee

    @property
    def gross_pnl(self) -> float:
        """Price move only — before any fee (a diagnostic, not the reported number)."""
        return (self.exit_price - self.entry_price) * self.qty

    @property
    def net_pnl(self) -> float:
        """The only number that counts (§8): price move net of the full fee stack."""
        return self.gross_pnl - self.fee_total

    @property
    def net_return(self) -> float:
        """Net P&L as a fraction of the entry notional."""
        return self.net_pnl / self.entry_notional if self.entry_notional else 0.0

    @property
    def r_multiple(self) -> float | None:
        """Net P&L in units of the 1%-risk budget — `None` if risk is unknown."""
        if not self.risk_idr:
            return None
        return self.net_pnl / self.risk_idr

    @property
    def holding_days(self) -> int:
        return (self.exit_date - self.entry_date).days

    @property
    def won(self) -> bool:
        return self.net_pnl > 0


def from_fills(
    *,
    symbol: str,
    track: str,
    tilt_kind: str,
    entry: Fill,
    exit: Fill,
    exit_reason: ExitReason,
    stop: float,
    risk_idr: float | None,
) -> PaperTrade:
    """Assemble a closed trade from an entry (BUY) and exit (SELL) fill.

    Both must be FILLED — a `PaperTrade` never exists for an un-fillable leg (missing ≠
    zero). P&L flows straight from the fills' cash flows, so it is net of the full fee
    stack the shared engine charged.
    """
    if entry.status is not FillStatus.FILLED or exit.status is not FillStatus.FILLED:
        raise ValueError("a PaperTrade requires two FILLED fills (missing ≠ zero)")
    if entry.side is not Side.BUY or exit.side is not Side.SELL:
        raise ValueError("expected a BUY entry and a SELL exit")
    if entry.fill_date is None or exit.fill_date is None:
        raise ValueError("both fills must carry a fill_date")

    return PaperTrade(
        symbol=symbol, track=track, tilt_kind=tilt_kind,
        entry_date=entry.fill_date, exit_date=exit.fill_date, qty=entry.qty,
        entry_price=entry.fill_price, exit_price=exit.fill_price,
        entry_fee=entry.fees.total, exit_fee=exit.fees.total,
        exit_reason=exit_reason, stop=stop, risk_idr=risk_idr,
    )
