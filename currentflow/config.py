"""Config constants for the data layer.

All timestamps in CurrentFlow are treated as **Asia/Jakarta (WIB) local, tz-naive**.
IDX has one exchange timezone; we do not mix zones. `as_of` (availability_ts) and
`decision_ts` are compared directly (spec §1 look-ahead rule).
"""

from __future__ import annotations

from datetime import date, time, timedelta
from pathlib import Path

EXODUS_BASE_URL = "https://exodus.stockbit.com"

# --- Logging (local-only; captures dal/netlog.py `net-error` lines) -----------------
# Rotating file under a git-ignored dir on the operator's machine — never republished
# (spec §10). Redaction happens at the seam (netlog.py); this only persists those lines.
LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
LOG_FILE = LOG_DIR / "net.log"
LOG_MAX_BYTES = 5_000_000   # ~5 MB per file
LOG_BACKUP_COUNT = 3        # keep net.log + 3 rotations

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

# Broker-summary conservation: every buy has a matching sell, so per (symbol) the
# gross buy value must equal the gross sell value. A fractional imbalance above this
# tolerance means the feed is TRUNCATED (top-N only), rows were dropped, or a sign
# convention broke — surfaced loudly, never swallowed (CLAUDE.md: no silent caps).
BROKER_CLEARING_TOL = 0.01  # 1% of the larger side

# --- Foreign flow / replay (slice 3) ------------------------------------------------
FF_AVG_WINDOW_DAYS = 20   # trailing window for the vs-avg multiple and z-score (§4 NBSA)
FF_CUM_DAYS = 5           # short cumulative NBSA stat (design: "5-day cumulative")

# Replay frame for trading day D reconstructs the first actionable pre-open moment,
# D+1 09:15 WIB: after D's EOD bar (~16:15, OHLCV_AVAILABLE_TIME) and after the
# conservative broker-summary availability of D+1 09:00 (LD-5). Injectable per call;
# revisit once BROKER_PUBLISH_LATENCY is measured.
REPLAY_DECISION_TIME = time(9, 15)
REPLAY_HISTORY_LOOKBACK_DAYS = 45  # calendar lookback read per frame for RVOL context
# The Wyckoff classifier (slice 4) needs a longer base to see a range form; read this
# many calendar days per frame for the phase lane (still look-ahead-safe at decision_ts).
REPLAY_PHASE_LOOKBACK_DAYS = 150

# --- Retry / backoff --------------------------------------------------------------
MAX_RETRIES = 4
BACKOFF_BASE_SECONDS = 2.0  # 2, 4, 8, 16 …

# --- Feed pagination (slice 13, live-verified) --------------------------------------
# `screener/templates` requires an integer `page` (omitting it → 400 "Screener Page
# can't be empty") and takes `limit` as the page size. One IHSG-sized page normally
# covers the whole universe (~900 names); the client still pages by `totalrows` so a
# larger result is never silently truncated (no silent caps).
SCREENER_PAGE_LIMIT = 900
# `company-price-feed/historical/summary` silently caps an un-paginated call at ~12
# rows regardless of the date range; `limit` beyond 50 is rejected (400). The client
# pages until a short page so a long backfill range is never silently truncated.
OHLCV_PAGE_LIMIT = 50

# --- Live transport (slice 10) ----------------------------------------------------
# The operator's own authenticated Stockbit session Bearer is captured out-of-band
# (own session, at own risk — CLAUDE.md/§15) and stored in the macOS Keychain, never
# on disk in plaintext, never republished. The httpx transport reads it fresh per
# request so a refresh (re-paste) takes effect without rebuilding the client.
HTTP_TIMEOUT_SECONDS = 30.0
KEYCHAIN_SERVICE = "currentflow-exodus"   # `security` generic-password service
KEYCHAIN_ACCOUNT = "bearer"               # pasted raw Bearer (slice-10 fallback)
# Slice 11: the credential-login session (access+refresh+expiry) is one JSON blob
# under a separate account. `access_token()` prefers this session's access token and
# falls back to the pasted Bearer above, so both auth paths coexist.
KEYCHAIN_SESSION_ACCOUNT = "session"
# Slice 12c: the OneSignal-style `player_id` device identifier. Generated ONCE (a
# random UUIDv4) on first login and persisted here, then reused for every subsequent
# login — it is the server's device-trust anchor (a stable, previously-verified
# player_id logs in directly; a fresh one re-triggers the one-time MFA/OTP). It is a
# device id, NOT a secret, but it lives in the Keychain with the session so it
# survives across sign-out (clearing it would force MFA again). See DATA_SOURCES §4.1.
KEYCHAIN_PLAYER_ID_ACCOUNT = "player_id"

# --- In-app login flow (slice 11; verified wire contract DATA_SOURCES §4.1) --------
# Own-session credential login. Credentials/OTP/recaptcha are transient in-memory
# only — never persisted, rendered back, or logged (§9.1). Only the access+refresh
# tokens reach the Keychain (KEYCHAIN_SESSION_ACCOUNT above).
AUTH_LOGIN_USERNAME_PATH = "login/v6/username"
AUTH_CHALLENGE_START_PATH = "mfa/verification/v1/challenge/start"
AUTH_CHALLENGE_OTP_SEND_PATH = "mfa/verification/v1/challenge/otp/send"
AUTH_CHALLENGE_OTP_VERIFY_PATH = "mfa/verification/v1/challenge/otp/verify"
AUTH_NEW_DEVICE_VERIFY_PATH = "login/v6/new-device/verify"
# Refresh route/shape NOT in the HAR capture — do NOT guess. `AuthClient.refresh`
# raises until an operator captures a real refresh exchange and pins this.
AUTH_REFRESH_PATH: str | None = None
# reCAPTCHA (slice 12c — supersedes the slice-11/12b "enforced, browser-minted"
# conclusion). The server validates only that `recaptcha_token` is PRESENT and
# non-empty — NOT its content or freshness. Confirmed 2026-07-03 by live probe: an
# arbitrary junk string ("not-a-real-recaptcha-token…") logs in 200, a reused stale
# HAR token logs in 200, and only an empty/absent token is rejected 400 "Permintaan
# tidak valid". So there is no Google `siteverify` on the backend to satisfy: no
# browser, console snippet, bookmarklet, or headless engine is needed. We send a fixed
# non-empty placeholder to clear the presence check. See DATA_SOURCES §4.1.
AUTH_RECAPTCHA_VERSION = "RECAPTCHA_VERSION_3"
AUTH_RECAPTCHA_PLACEHOLDER = "currentflow"   # any non-empty string clears the presence check
AUTH_RECAPTCHA_SITE_KEY = "6LeBXZYqAAAAAIAqBYdAV5HuBc6i0YeVziSYrXAZ"  # public v3 key (unused; kept for reference)
AUTH_RECAPTCHA_ACTION = "login"   # advisory only; content is not validated server-side
CHALLENGE_OTP = "CHALLENGE_OTP"
CHALLENGE_FINISH = "CHALLENGE_FINISH"

# --- Universe gate (spec §3, LOCKED) ----------------------------------------------
ADV_FLOOR_IDR = 10_000_000_000.0        # 20-day avg daily value traded ≥ IDR 10 bn
ADV_TRACK_A_IDR = 25_000_000_000.0      # Track A additionally requires ADV ≥ IDR 25 bn
PRICE_FLOOR_IDR = 100.0                 # last price ≥ IDR 100
MIN_HISTORY_TRADING_DAYS = 60           # IPO with < 60 trading days of history → reject
CORP_ACTION_WINDOW_DAYS = 5             # exclude ±5 calendar days around a corp action
ADV_WINDOW_DAYS = 20
TRACK_A_INDEXES = frozenset({"LQ45", "IDX80"})

# --- Regime boundaries (REGIME.md; slice 20) --------------------------------------
# All engine constants above are pinned to the CURRENT IDX regime. Any historical
# computation over data generated under earlier rules (COVID unwind, pre-FCA) is
# invalid unless clamped to these boundaries. There is no era-versioned constant
# system — the repo is current-regime-scoped (REGIME.md §4). Two ⚠ VERIFY items on
# the exact IDX announcement dates remain operator actions (REGIME.md §2).
REGIME_START_TRACK_A = date(2024, 1, 1)   # post COVID-rule unwind (LQ45/IDX80 large caps)
REGIME_START_TRACK_B = date(2024, 7, 1)   # post-FCA settle (lapis-2 / IDXSMC-LIQ)
CATALOG_HOLDOUT_START = date(2026, 1, 1)  # pattern-catalog estimation/OOS seam (REGIME.md §3)


def regime_start(track: str) -> date:
    """The per-name regime clamp (REGIME.md §1) — the single definition. A historical
    read for a name is bounded below by this; a backtest reaching earlier is a bug, not
    a bigger sample. Portfolio runs spanning both tracks clamp at the Track B boundary
    (the stricter/later of the two)."""
    return REGIME_START_TRACK_A if track == "A" else REGIME_START_TRACK_B


# --- Regime-scoped historical backfill (slice 20) ---------------------------------
# The backfill seed universe is the current SCR-0 pull (seed only — point-in-time
# correctness comes from pit_universe, not the seed). Pacing is a courtesy pause between
# backfilled names, ON TOP of the client's reactive per-call backoff (paywall intent —
# within a name the client's backoff already paces the day-by-day broker calls).
BACKFILL_UNIVERSE_SCOPE = "SCR0"           # candidate list source for the backfill seed
BACKFILL_BATCH_PAUSE_S = 1.0               # paywall pacing between backfilled names

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

# --- Wyckoff phase classifier (spec §2 step [3], RULE A HARD GATE) ------------------
# Threshold detectors FEED the classifier; they never bypass it. Only Accumulation
# Phase C (spring/test) or D (SOS + LPS) is tradeable.
PHASE_MIN_BARS = 40                    # min history to trust a range has formed
PHASE_RANGE_LOOKBACK = 60             # bars scanned when locating the trading range
PHASE_RANGE_MIN_BARS = 10            # a trading range must span ≥ this many bars
PHASE_RANGE_MAX_WIDTH = 0.35         # support→resistance span ≤ 35% of support (a range, not a trend)
PHASE_TOUCH_TOLERANCE = 0.03         # within 3% of a level counts as a "touch"
PHASE_SC_VOLUME_MULT = 2.0           # selling-climax bar volume ≥ 2× prior avg
PHASE_SOS_VOLUME_MULT = 1.5          # sign-of-strength rally volume ≥ 1.5× range avg
PHASE_SPRING_PENETRATION = 0.02      # spring low dips ≤ 2% below support …
PHASE_SPRING_MAX_VOLUME_MULT = 1.5   # … on non-climactic (≤1.5× avg) volume, then recovers
PHASE_MARKUP_EXTENSION = 0.10        # ≥10% above resistance → Phase E (markup — too late to arm)

# --- Smart Money Score (spec §4) — LOCKED weights, the ONLY tunable surface --------
# CLAUDE.md: never hand-edit live; the walk-forward Sharpe optimizer is the sole
# writer. Keyed by track ("A"/"B" — universe.gate.Track values) to avoid an import
# cycle. Weights sum to 100 per track.
SMS_WEIGHTS: dict[str, dict[str, int]] = {
    "A": {  # large-cap: NBSA foreign flow co-leads
        "divergence": 30, "broker_concentration": 20, "foreign_flow": 25,
        "rvol": 10, "block_trade": 5, "phase_bonus": 10,
    },
    "B": {  # lapis-2: broker concentration leads, foreign flow excluded (LD-1)
        "divergence": 30, "broker_concentration": 35, "foreign_flow": 0,
        "rvol": 15, "block_trade": 10, "phase_bonus": 10,
    },
}
SMS_ARMED_THRESHOLD = 70.0            # SMS ≥ 70 AND phase∈{C,D} AND no veto → ARMED (LOCKED)

# RULE B (LD-9): a module may show a number only after this many months of
# fill-realistic forward paper trading. Slice 8 wires the paper-trade engine to
# promote OBSERVATION_ONLY → VALIDATED; until then every gated module shows components.
PAPER_VALIDATION_MONTHS = 3

# SMS component detector thresholds (§4 parentheticals).
SMS_DIVERGENCE_FLAT_PCT = 0.005       # price move ≤ ±0.5% on the high-vol bar
SMS_DIVERGENCE_CORR_MAX = 0.30        # corr(volume, |Δprice|) reference point (graduated, §4)
SMS_DIVERGENCE_HIVOL_MULT = 1.5       # "high-vol bar" = volume ≥ 1.5× window avg
# Divergence is a CURRENT-state signal (a recent absorption window), not a year-long
# average — measured over the last N complete bars, aligned with a Wyckoff range's span.
# (§4 pins the ±0.5%/corr conditions + weight, not the lookback — see PROGRESS decisions.)
SMS_DIVERGENCE_WINDOW_DAYS = 40
SMS_BROKER_PERSIST_DAYS = 3           # top-2 concentration credited from ≥ 3 consecutive days
SMS_FOREIGN_SPIKE_MULT = 2.0          # foreign net buy > 2× 20d avg (Track A)
SMS_RVOL_MULT = 3.0                   # volume anomaly > 3× 20d avg
SMS_BLOCK_VALUE_IDR = 1_000_000_000.0  # block footprint > IDR 1B …
SMS_BLOCK_ADV_PCT = 0.01              # … or > 1% ADV

# --- Veto filters (spec §5, hard reject regardless of SMS) -------------------------
VETO_MONOPOLY_SHARE = 0.60            # one broker > 60% of net-buy concentration
VETO_RETAIL_FOMO_SHARE = 0.60         # retail buy ratio > 60% of volume
VETO_MARKUP_PRICE_PCT = 0.03          # a "spike" = last-bar |Δprice| ≥ 3% …
VETO_MARKUP_THIN_RVOL = 1.0           # … on ≤ 1× RVOL (no real demand behind it)
VETO_WASH_RATIO = 0.70                # broker min(buy,sell)/max(buy,sell) ≥ 0.7 = churn
VETO_DIST_CLOSE_POSITION = 0.5        # high-vol up bar closing in the lower half of its range
VETO_ROTATION_MIN_DAYS = 3            # ≥3 concentrated days with a rotating top buyer = disguise
VETO_FLIP_MIN_DAYS = 2                # dominant accumulator must net-sell ≥2 consecutive latest days

# --- Stage-2 distribution / decay layer (spec §8 signal-decay; slice 5) -------------
# The credibility/exit layer. These are OBSERVATION flags (categorical severities, not
# numbers — RULE B), surfaced across every view. `missing ≠ zero`: a detector that
# needs foreign flow stays silent when net_foreign is absent, never inventing outflow.
DECAY_WINDOW_DAYS = 10                # window over which price-rise / divergence is read
DECAY_DIVERGENCE_MIN_PRICE_RISE = 0.03  # price up ≥ 3% over the window while flow falls
DECAY_FOREIGN_SELL_STREAK_DAYS = 3   # ≥ this many trailing days of net foreign sell = outflow
DECAY_NO_DEMAND_SPREAD_MULT = 1.0    # "narrow" = spread ≤ this × recent avg spread (no-demand)

# --- Sector Rotation Map (spec §9; slice 6) — DERIVED VIEW ---------------------------
# Flow-by-sector + RS-vs-flow quadrant. The quadrant is a categorical observation of a
# sector's (relative-strength, net-flow) position — never a buy/sell verb (RULE B). RS
# is measured relative to the universe (equal-weight mean return) as a market proxy;
# never IHSG-as-benchmark for returns (§8) — here it only frames the RS axis.
SECTOR_WINDOW_DAYS = 20              # trailing window for sector flow + relative strength

# --- Portfolio Risk Monitor (spec §9 + §6 caps; slice 6) — OBSERVATION ----------------
# Risk *observations*, not return predictions (RULE B): VaR/β/HHI are measurements, the
# crowding matrix is a broker-overlap correlation, the caps/breakers are the §6 limits.
EXPOSURE_CAP_NAME = 0.10            # ≤ 10% equity per name (§6)
EXPOSURE_CAP_SECTOR = 0.30         # ≤ 30% per sector (§6)
EXPOSURE_WARN_NAME = 0.085         # design: amber as a name approaches the 10% cap
EXPOSURE_WARN_SECTOR = 0.25        # design: amber as a sector approaches the 30% cap
CROWDING_CORR_THRESHOLD = 0.70     # correlated-pair flag: broker-overlap ρ ≥ this (§6 crowding check)
RISK_RETURN_WINDOW_DAYS = 60       # trailing window of daily returns for β and VaR
VAR_CONFIDENCE = 0.95              # historical 1-day Value-at-Risk confidence level
DTE_PARTICIPATION = 0.20           # can liquidate ≤ 20% of ADV per day → days-to-exit
CIRCUIT_HALT_DAILY_PNL = -0.03     # halt NEW entries at −3% daily P&L (§6)
CIRCUIT_PAUSE_DRAWDOWN = -0.10     # pause the system at −10% peak-to-trough drawdown (§6)
# Scenario stress = defined what-if shocks (hypothetical impact, not a prediction, §9).
STRESS_IHSG_GAP = -0.05            # IHSG −5% gap-down, transmitted through portfolio β
STRESS_FOREIGN_EXODUS = -0.03      # foreign exodus: shock to foreign-crowded exposure
STRESS_RUPIAH_SHOCK = -0.04        # rupiah shock: broad shock across the book

# --- Execution: technical trigger (spec §6, LD-3; slice 7) ---------------------------
# Grimes discipline: a passing score sets ARMED, not ENTER. Entry needs a confirmation
# trigger (Spring-test close OR LPS pullback) via a LIMIT order, with R:R ≥ 2:1 or skip.
RR_MIN = 2.0                       # first structural target R:R ≥ 2:1 or no trade (§6)
STOP_BUFFER = 0.005               # stop sits this far below the spring/swing low (invalidation)
LIMIT_UNDERCUT = 0.0              # limit placed at trigger; >0 shaves it below (never chase)
# Phase D measured-move target: resistance + this × range span (a Wyckoff count). Phase C
# targets the automatic-rally high (range resistance) directly.
TARGET_MEASURED_MOVE_MULT = 1.0

# --- Fast Mode (spec §6, LD-11; slice 15) --------------------------------------------
# Operator-armed auto paper-trader that buys every ARMED name AT ONCE, without waiting for
# the Spring/LPS trigger and WITHOUT the R:R≥2:1 gate (R:R is still computed, as an
# observation). Paper only — never a live order (§15). Off by default (opt-in), matching
# the ML/regime "gated shut" posture.
FAST_MODE_ENABLED = False          # global default; the durable fast_mode_state overrides it
# The fast-mode entry is a *marketable* limit: reference (last visible close) × (1 + premium),
# so the next-open fill lands at/under the limit (honest under ARA/ARB, §12). Small by design.
FAST_MODE_LIMIT_PREMIUM = 0.005    # 0.5% above the reference close

# --- Execution: sizing / order gen (spec §6; slice 7) --------------------------------
RISK_PCT = 0.01                    # position risk locked at 1% of equity (IDX manipulation tax)
LOT_SIZE = 100                     # IDX board lot = 100 shares (§12)
# Conviction multipliers from the fundamental tilt (§7) — scale the 1% risk.
CONVICTION_COMPOUNDER = 1.0
CONVICTION_NEUTRAL = 0.75
CONVICTION_SPECULATIVE = 0.5
CONVICTION_FLOW_ONLY = 0.75        # financials/utilities default (§7); proxy can lift to 1.0

# --- Execution: risk / exit manager (spec §8; slice 7) -------------------------------
# Trailing-stop width by hold profile (§7): compounder rides wide, speculative trails tight.
TRAIL_WIDE = 0.15                  # COMPOUNDER — hold through markup
TRAIL_STANDARD = 0.10              # NEUTRAL
TRAIL_TIGHT = 0.06                 # SPECULATIVE / FLOW_ONLY — exit at first target, tight trail

# --- Fundamental tilt (spec §7, LD-6/7; slice 7) -------------------------------------
# Magic Formula combined-rank percentile (fitem 13474) tercile → conviction & horizon.
# Higher rank% = better (top tercile = COMPOUNDER). Financials + utilities skip MF and
# run FLOW_ONLY with a sector proxy (banks: ROE > 12%). Fundamentals never block entry.
MF_TOP_TERCILE_PCT = 66.667        # rank% ≥ this → top tercile (COMPOUNDER)
MF_BOTTOM_TERCILE_PCT = 33.333     # rank% < this → bottom tercile (SPECULATIVE)
FLOW_ONLY_SECTORS = frozenset({"FINANCIALS", "FINANCE", "BANK", "UTILITIES", "INFRASTRUCTURE"})
BANK_ROE_PROXY_MIN = 0.12          # FLOW_ONLY quality proxy: ROE > 12% may promote ×0.75 → ×1.0

# --- Paper fill engine (IDX-aware, spec §12; slice 7) --------------------------------
# Lots of 100 · tick bands · ARA/ARB reject · next-open + slippage · FULL fee stack
# (broker + levy + VAT + 0.1% sell tax) · T+2. The ONE fill engine shared by backtest
# and forward-paper (§11/§13); every reported return is net of this stack.
#
# Tick sizes (fraksi harga) by price band — the current IDX regime. `(lower_inclusive,
# tick)`; the band a price falls in is the last whose lower bound it meets.
TICK_BANDS: tuple[tuple[float, float], ...] = (
    (0.0, 1.0),        # < 200      → tick 1
    (200.0, 2.0),      # 200–<500   → tick 2
    (500.0, 5.0),      # 500–<2000  → tick 5
    (2000.0, 10.0),    # 2000–<5000 → tick 10
    (5000.0, 25.0),    # ≥ 5000     → tick 25
)

# Fee stack (§12). Each component is modelled explicitly so the "full fee stack" is
# auditable and the hand-checked acceptance cases can pin every line. Commission is
# side-specific to honour §12's "~0.15–0.25%" range (buy low end, sell high end);
# VAT (PPN 11%) applies to the broker commission; the 0.1% sell tax (PPh final) hits
# the sell notional only; the levy bundles IDX/KPEI/KSEI (~0.043%) on both sides.
FEE_COMMISSION_BUY = 0.0015        # 0.15% broker commission (buy)
FEE_COMMISSION_SELL = 0.0025       # 0.25% broker commission (sell)
FEE_LEVY = 0.00043                 # IDX + KPEI + KSEI transaction levy (both sides)
FEE_VAT = 0.11                     # PPN 11% on the broker commission
FEE_SELL_TAX = 0.001               # 0.1% final sales tax on sell notional (§12)

# Next-open slippage by liquidity tier (§12): LQ45 0.05–0.15% / mid-cap 0.2–0.5% /
# small-cap >1%. Midpoints taken; buys slip up (worse), sells slip down (worse).
SLIPPAGE_LARGE = 0.001             # LQ45 / large-cap (mid of 0.05–0.15%)
SLIPPAGE_MID = 0.0035              # mid-cap (mid of 0.2–0.5%)
SLIPPAGE_SMALL = 0.012             # small-cap (>1%)
# ADV thresholds that assign the slippage tier (IDR). ≥ large → LARGE; ≥ mid → MID; else SMALL.
SLIPPAGE_LARGE_ADV_IDR = 100_000_000_000.0   # ≥ IDR 100 bn ADV → large/LQ45-like
SLIPPAGE_MID_ADV_IDR = 25_000_000_000.0      # ≥ IDR 25 bn ADV → mid-cap
SETTLEMENT_DAYS = 2                # T+2 settlement (§12)

# --- Scale / ML layer (spec §11 step 9, LD-8) — GATED --------------------------------
# ML is deferred and gated (LD-8): before ANY optimizer or ranker may run, the rules system
# must FIRST have earned its number — ≥ PAPER_VALIDATION_MONTHS of forward paper with a
# positive walk-forward Sharpe (the `sms` module VALIDATED in the ValidationLedger). Reflexive,
# non-stationary, small-sample IDX flow overfits trivially, so ML is admitted only once the
# non-ML rules have demonstrably survived fill-realistic forward paper.
#
# ML is confined to a signal-weight OPTIMIZER / RANKER over ENGINEERED features only, under
# mandatory purged + embargoed cross-validation. Weights are never hand-edited live — the
# optimizer is the sole writer of the weight surface (CLAUDE.md / §4).
ML_ADMISSION_MODULE = "sms"        # the rules-system module whose VALIDATED state admits ML (LD-8)
ML_CV_FOLDS = 3                    # sequential out-of-sample walk-forward test folds
ML_EMBARGO_FRAC = 0.02            # embargo = this fraction of samples dropped at each train↔test boundary (López de Prado)
ML_WEIGHT_STEP = 5                # optimizer coordinate-search granularity on the integer weight simplex (§4)
ML_WEIGHT_SUM = 100               # weights sum to 100 per track (locked §4 structure — optimizer preserves it)
# Structurally-locked zero weights the optimizer must never fund (LD-1): Track B excludes
# foreign flow (unreliable on lapis-2). Keyed by track → set of components pinned to 0.
ML_LOCKED_ZEROS: dict[str, frozenset[str]] = {"A": frozenset(), "B": frozenset({"foreign_flow"})}

# --- Pattern catalog (LD-14, v1.7; slice 21, PATTERN-CATALOG-SPEC.md) --------------
# Event-cadence base rates confined to the catalog view under P1–P4. A base rate is a
# measurement (like a z-score), never a forward claim: it never renders on a live
# candidate/pipeline/rail surface, never carries a buy/sell verb, and never multiplies
# into SMS. Attaching a pattern's stats to a live name is a claim → the standard RULE B
# path (a dedicated ValidationLedger lane), not the catalog.
CATALOG_MIN_N = 20              # n < this → the rate cell renders the interval only (P4)
CATALOG_CI_CONFIDENCE = 0.90    # Wilson interval confidence (two-sided 90%)
CATALOG_CI_Z = 1.6448536269514722  # z for the two-sided 90% Wilson interval

# --- Automated ingestion scheduler (slice 12) -------------------------------------
# Infra, not a spec slice: fires the already-implemented feeds on their own cadence
# during Mon–Fri trading hours and writes cache only (never scores, never touches
# RULE A/B; `as_of` stamping unchanged, so look-ahead safety is untouched). All times
# are WIB, tz-naive (module docstring). Cadences are configurable — this is the default.
#
# The trading-hours window. EOD signal feeds publish ~16:15 (OHLCV_AVAILABLE_TIME),
# AFTER this window closes, so a fire inside the window necessarily fetches the PRIOR
# completed trading day — which is exactly the conservative look-ahead stamp already in
# force (BROKER_CONSERVATIVE_AVAILABLE_TIME). Window bounds are inclusive.
SCHEDULER_WINDOW_OPEN = time(9, 0)     # 09:00 WIB
SCHEDULER_WINDOW_CLOSE = time(16, 0)   # 16:00 WIB
# EOD per-symbol feeds (broker + OHLCV) + weekly rosters fetch the prior completed
# trading day at the window open — matches BROKER_CONSERVATIVE_AVAILABLE_TIME (09:00).
SCHEDULER_EOD_TIME = time(9, 0)
# Universe screener refresh a beat later so the day's fresh survivor set lands after
# the morning EOD ingest (steady state: per-symbol feeds use the prior refresh).
SCHEDULER_SCREENER_TIME = time(9, 5)
# Fast Mode (LD-11) auto-trade step fires a beat AFTER the EOD ingest + screener refresh so
# it reads the day's freshly-cached bars/broker + universe. Prior completed trading day.
SCHEDULER_FAST_MODE_TIME = time(9, 10)
# The loop wakes this often to ask each feed "due?" against durable run-state. A tick
# that finds nothing due (or is outside the window) is a cheap no-op.
SCHEDULER_TICK_SECONDS = 60
