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
