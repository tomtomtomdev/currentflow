"""Config constants for the data layer.

All timestamps in CurrentFlow are treated as **Asia/Jakarta (WIB) local, tz-naive**.
IDX has one exchange timezone; we do not mix zones. `as_of` (availability_ts) and
`decision_ts` are compared directly (spec §1 look-ahead rule).
"""

from __future__ import annotations

from datetime import time, timedelta

EXODUS_BASE_URL = "https://exodus.stockbit.com"

# --- Publish-latency policy (LD-5) ------------------------------------------------
# The HAR capture cannot reveal WHEN EOD broker summary actually publishes vs the
# next session open. Until measured empirically (see ingest/publish_latency.py),
# broker-summary same-day signals are NOT trusted: availability is stamped
# conservatively so `as_of` lands the morning AFTER the trading day.
#
# BROKER_PUBLISH_LATENCY stays None until an operator measures it and pins a value.
BROKER_PUBLISH_LATENCY: timedelta | None = None

# Conservative fallback used while BROKER_PUBLISH_LATENCY is unmeasured: broker
# summary for trading day D is treated as available at D+1 09:00 WIB (next-session
# open) — never same-day. This keeps look-ahead honest by construction.
BROKER_CONSERVATIVE_AVAILABLE_TIME = time(9, 0)  # next trading morning

# EOD OHLCV bar for day D is published after the ~16:00 WIB close. We stamp
# availability at 16:15 WIB same day (post-close), configurable if measured otherwise.
OHLCV_AVAILABLE_TIME = time(16, 15)

# --- Retry / backoff --------------------------------------------------------------
MAX_RETRIES = 4
BACKOFF_BASE_SECONDS = 2.0  # 2, 4, 8, 16 …

# --- Universe gate (spec §3, LOCKED) ----------------------------------------------
ADV_FLOOR_IDR = 10_000_000_000.0        # 20-day avg daily value traded ≥ IDR 10 bn
ADV_TRACK_A_IDR = 25_000_000_000.0      # Track A additionally requires ADV ≥ IDR 25 bn
PRICE_FLOOR_IDR = 100.0                 # last price ≥ IDR 100
MIN_HISTORY_TRADING_DAYS = 60           # IPO with < 60 trading days of history → reject
CORP_ACTION_WINDOW_DAYS = 5             # exclude ±5 calendar days around a corp action
ADV_WINDOW_DAYS = 20
TRACK_A_INDEXES = frozenset({"LQ45", "IDX80"})

# --- ARA/ARB bands (spec §12; derivation DATA_SOURCES §3.2) ------------------------
# Spec pins: main ±7% / dev board ±10–25% / first 15 trading days post-IPO ±35%.
# The dev-board 10–25% range is resolved by price tier (higher-priced names get the
# tighter band) — implementation choice logged in PROGRESS.md decisions.
BAND_MAIN = 0.07
BAND_DEV_TIGHT = 0.10          # development board, prev close ≥ DEV_TIGHT_PRICE
BAND_DEV_WIDE = 0.25           # development board, prev close <  DEV_TIGHT_PRICE
DEV_TIGHT_PRICE_IDR = 5_000.0
BAND_IPO = 0.35                # first 15 trading days post-IPO
IPO_BAND_TRADING_DAYS = 15
# `pinned = |close − prev| / prev ≥ band − ε` — ε absorbs tick rounding at the band.
PIN_EPSILON = 0.005

# --- Index-rebalancing filter (spec §3) --------------------------------------------
# Pure-beta moves near rebalance dates are down-weighted 30% — never rejected.
REBALANCE_DOWNWEIGHT = 0.7
REBALANCE_RESIDUAL_THRESHOLD = 0.01     # |β-adjusted residual| ≤ 1% ≈ "explained by beta"
REBALANCE_TRACKER_SHARE = 0.5           # ≥ 50% of net flow on index-tracker brokers
