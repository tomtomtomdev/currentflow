"""ARA/ARB auto-reject band derivation (spec §12; DATA_SOURCES §3.2).

No served flag exists — the band is derived from board type + previous close, and a
close is "pinned" when `|close − prev| / prev ≥ band − ε`. A pinned close has no
fillable band, so the universe gate rejects the signal day (§3) and the paper fill
engine (slice 7) will reject orders into it.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from currentflow import config
from currentflow.dal.models import BoardType


class PinState(str, Enum):
    FREE = "FREE"
    PINNED_ARA = "PINNED_ARA"  # pinned at the upper band (auto-reject atas)
    PINNED_ARB = "PINNED_ARB"  # pinned at the lower band (auto-reject bawah)


@dataclass(frozen=True, slots=True)
class BandCheck:
    band_pct: float
    move_pct: float           # signed (close − prev) / prev
    state: PinState

    @property
    def pinned(self) -> bool:
        return self.state is not PinState.FREE


def band_pct(
    board: BoardType,
    prev_close: float,
    *,
    trading_days_since_ipo: int | None = None,
) -> float:
    """Auto-reject band for the day, per spec §12 (locked numbers).

    First 15 trading days post-IPO override the board band at ±35%. The dev-board
    10–25% range resolves by price tier: prev close ≥ 5000 → 10%, else 25%
    (decision logged in PROGRESS.md).
    """
    if (
        trading_days_since_ipo is not None
        and trading_days_since_ipo <= config.IPO_BAND_TRADING_DAYS
    ):
        return config.BAND_IPO
    if board is BoardType.DEVELOPMENT:
        return (
            config.BAND_DEV_TIGHT
            if prev_close >= config.DEV_TIGHT_PRICE_IDR
            else config.BAND_DEV_WIDE
        )
    # MAIN, and UNKNOWN falls back to the main-board band (tightest → most
    # conservative pin detection; an unknown board never hides a pinned close).
    return config.BAND_MAIN


def check_pinned(
    close: float,
    prev_close: float,
    board: BoardType,
    *,
    trading_days_since_ipo: int | None = None,
    epsilon: float = config.PIN_EPSILON,
) -> BandCheck:
    """`pinned = |close − prev| / prev ≥ band − ε` (DATA_SOURCES §3.2)."""
    if prev_close <= 0:
        raise ValueError(f"prev_close must be positive, got {prev_close}")
    band = band_pct(board, prev_close, trading_days_since_ipo=trading_days_since_ipo)
    move = (close - prev_close) / prev_close
    if abs(move) >= band - epsilon:
        state = PinState.PINNED_ARA if move > 0 else PinState.PINNED_ARB
    else:
        state = PinState.FREE
    return BandCheck(band_pct=band, move_pct=move, state=state)
