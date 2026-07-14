"""DuckDB schema. Every table keyed on `(symbol, date, as_of)` (+ broker/side).

Column identifiers are always double-quoted in generated SQL because several
(`date`, `open`, `close`, `value`) collide with DuckDB keywords.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as Date
from datetime import datetime
from enum import Enum

from currentflow.dal.models import InvestorType, RowStatus, Side


def _in_list(enum_cls: type[Enum]) -> str:
    """SQL `IN (...)` value list for a CHECK constraint, derived from the enum so it
    never drifts from `dal.models`."""
    return ", ".join(f"'{m.value}'" for m in enum_cls)


# Ordered columns per table (kept in sync with dal.models dataclass fields).
DAILY_BAR_COLUMNS: tuple[str, ...] = (
    "symbol",
    "date",
    "as_of",
    "status",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "value",
    "frequency",
    "vwap",
    "foreign_buy",
    "foreign_sell",
    "net_foreign",
    "change_percentage",
)

BROKER_NET_COLUMNS: tuple[str, ...] = (
    "symbol",
    "date",
    "as_of",
    "broker_code",
    "side",
    "investor_type",
    "avg_price",
    "value",
    "lot",
    "frequency",
)

# SCR-0 eligibility results (screeners.md §4: "cache to DuckDB with as_of").
SCR0_COLUMNS: tuple[str, ...] = (
    "symbol",
    "date",
    "as_of",
    "adv20",
    "price",
    "free_float",
    "market_cap",
)

# SCR-1A foreign-accumulation results (screeners.md; Track A / LQ45).
SCR1A_COLUMNS: tuple[str, ...] = (
    "symbol",
    "date",
    "as_of",
    "net_foreign",
    "net_foreign_ma20",
    "buy_streak",
    "flow_ma20",
)

# SCR-1B bandar-accumulation results (screeners.md; Track B / IDXSMC-LIQ).
SCR1B_COLUMNS: tuple[str, ...] = (
    "symbol",
    "date",
    "as_of",
    "bandar_value",
    "bandar_value_ma20",
    "bandar_accum_dist",
    "adv20",
)

# SCR-1C stealth-divergence-proxy results (screeners.md; IHSG).
SCR1C_COLUMNS: tuple[str, ...] = (
    "symbol",
    "date",
    "as_of",
    "bandar_value",
    "price_return_1m",
    "volume",
    "volume_ma20",
)

# SCR-2 volume/frequency-anomaly (RVOL) results (screeners.md; IHSG).
SCR2_COLUMNS: tuple[str, ...] = (
    "symbol",
    "date",
    "as_of",
    "volume",
    "volume_ma20",
    "frequency",
    "frequency_spike",
)

# SCR-EXIT distribution/mirror results (screeners.md; spec §8 signal-decay).
SCR_EXIT_COLUMNS: tuple[str, ...] = (
    "symbol",
    "date",
    "as_of",
    "bandar_accum_dist",
    "net_foreign_ma20",
    "foreign_sell_streak",
)

# SCR-3 trend-confirmation results (screeners.md; spec Stage 3 / §6 trigger context).
SCR3_COLUMNS: tuple[str, ...] = (
    "symbol",
    "date",
    "as_of",
    "price",
    "price_ma20",
    "price_ma50",
    "vwap",
    "adx14",
    "atr14",
    "rs_3m",
)

# SCR-4 fundamental-tilt reference results (screeners.md; spec §7 — ranking, not a gate).
SCR4_COLUMNS: tuple[str, ...] = (
    "symbol",
    "date",
    "as_of",
    "mf_rank_pct",
    "roc_greenblatt",
    "ev_ebit",
    "rank_roic",
    "roe",
    "market_cap",
)

# Index-membership roster (§3 Track A/B source; `indexes` comma-joined). No trading
# `date` — a live snapshot keyed (symbol, as_of); read-latest gives point-in-time track.
SYMBOL_INDEX_COLUMNS: tuple[str, ...] = (
    "symbol",
    "as_of",
    "indexes",
)

# KSEI monthly ownership slices (foreign-ownership-trend overlay, spec §9).
KSEI_COLUMNS: tuple[str, ...] = (
    "symbol",
    "date",
    "as_of",
    "foreign_pct",
    "local_pct",
)

# Scheduler durable run-state (slice 12). Store-owned metadata, NOT a DAL feed — one
# row per fire, survives restart so the daemon never double-fires or misses a day, and
# doubles as the audit trail. `outcome` is a free VARCHAR (no enum/CHECK) whose values
# are defined by `scheduler.runner` (OK / SKIPPED_EMPTY / ERROR).
SCHEDULER_RUNS_COLUMNS: tuple[str, ...] = (
    "feed",
    "last_fired_at",
    "rows_written",
    "outcome",
)


@dataclass(frozen=True, slots=True)
class SchedulerRunRow:
    """One recorded scheduler fire (slice 12). `last_fired_at` is the run's injected
    `now` (WIB, tz-naive) — the read side takes the latest per feed to decide due-ness."""

    feed: str
    last_fired_at: datetime
    rows_written: int
    outcome: str


# --- Fast Mode (LD-11, slice 15) — the durable paper book + run state ----------------
# Store-owned operational tables (like scheduler_runs), NOT DAL feeds. They record the
# realized/held FACTS of the auto paper-trader, so they carry an `as_of` audit stamp but
# skip the `as_of < decision_ts` look-ahead firewall (a closed trade is a fact, not a
# point-in-time signal read). `exit_reason` is a free VARCHAR (values from
# execution.risk.ExitReason), mirroring scheduler_runs.outcome — no cross-package CHECK.
FAST_POSITION_COLUMNS: tuple[str, ...] = (
    "symbol", "as_of", "track", "sector", "board", "tier", "tilt_kind",
    "entry_date", "entry_price", "stop", "target", "trail_pct", "qty",
    "risk_idr", "entry_fee",
)

FAST_TRADE_COLUMNS: tuple[str, ...] = (
    "symbol", "entry_date", "exit_date", "as_of", "track", "tilt_kind", "qty",
    "entry_price", "exit_price", "entry_fee", "exit_fee", "exit_reason",
    "stop", "risk_idr",
)

FAST_MODE_STATE_COLUMNS: tuple[str, ...] = (
    "key", "enabled", "since_date", "last_run_day",
    "realized_pnl", "prev_equity", "peak_equity",
)


@dataclass(frozen=True, slots=True)
class FastPositionRow:
    """One open Fast-Mode paper position (LD-11). Carries enough to run the §8 exit and
    rebuild the closed PaperTrade on exit (`entry_fee` lets net-of-fee P&L reconcile with
    `validation.trade.from_fills`)."""

    symbol: str
    as_of: datetime
    track: str
    sector: str
    board: str
    tier: str
    tilt_kind: str
    entry_date: Date
    entry_price: float
    stop: float
    target: float | None
    trail_pct: float
    qty: int
    risk_idr: float | None
    entry_fee: float


@dataclass(frozen=True, slots=True)
class FastTradeRow:
    """One closed Fast-Mode paper trade (LD-11) — the durable forward-paper record that
    feeds the `ValidationLedger` (`fast_mode` lane) and the pipeline EXITED verdict."""

    symbol: str
    entry_date: Date
    exit_date: Date
    as_of: datetime
    track: str
    tilt_kind: str
    qty: int
    entry_price: float
    exit_price: float
    entry_fee: float
    exit_fee: float
    exit_reason: str
    stop: float
    risk_idr: float | None


@dataclass(frozen=True, slots=True)
class FastModeStateRow:
    """The Fast-Mode run singleton (LD-11): the operator arm/disarm flag + the carried §6
    circuit state (prev/peak equity) so the daemon's breakers bind across days. `key` is a
    fixed constant added by the store — a single row."""

    enabled: bool
    since_date: Date | None
    last_run_day: Date | None
    realized_pnl: float
    prev_equity: float
    peak_equity: float


DDL = f"""
CREATE TABLE IF NOT EXISTS daily_bar (
    "symbol"            VARCHAR   NOT NULL,
    "date"              DATE      NOT NULL,
    "as_of"             TIMESTAMP NOT NULL,
    "status"            VARCHAR   NOT NULL CHECK ("status" IN ({_in_list(RowStatus)})),
    "open"              DOUBLE,
    "high"              DOUBLE,
    "low"               DOUBLE,
    "close"             DOUBLE,
    "volume"            BIGINT,
    "value"             DOUBLE,
    "frequency"         BIGINT,
    "vwap"              DOUBLE,
    "foreign_buy"       DOUBLE,
    "foreign_sell"      DOUBLE,
    "net_foreign"       DOUBLE,
    "change_percentage" DOUBLE,
    PRIMARY KEY ("symbol", "date", "as_of")
);

CREATE TABLE IF NOT EXISTS broker_net (
    "symbol"        VARCHAR   NOT NULL,
    "date"          DATE      NOT NULL,
    "as_of"         TIMESTAMP NOT NULL,
    "broker_code"   VARCHAR   NOT NULL,
    "side"          VARCHAR   NOT NULL CHECK ("side" IN ({_in_list(Side)})),
    "investor_type" VARCHAR   NOT NULL CHECK ("investor_type" IN ({_in_list(InvestorType)})),
    "avg_price"     DOUBLE,
    "value"         DOUBLE,
    "lot"           BIGINT,
    "frequency"     BIGINT,
    PRIMARY KEY ("symbol", "date", "as_of", "broker_code", "side")
);

CREATE TABLE IF NOT EXISTS scr0_eligible (
    "symbol"     VARCHAR   NOT NULL,
    "date"       DATE      NOT NULL,
    "as_of"      TIMESTAMP NOT NULL,
    "adv20"      DOUBLE,
    "price"      DOUBLE,
    "free_float" DOUBLE,
    "market_cap" DOUBLE,
    PRIMARY KEY ("symbol", "date", "as_of")
);

CREATE TABLE IF NOT EXISTS scr1a_foreign_accum (
    "symbol"           VARCHAR   NOT NULL,
    "date"             DATE      NOT NULL,
    "as_of"            TIMESTAMP NOT NULL,
    "net_foreign"      DOUBLE,
    "net_foreign_ma20" DOUBLE,
    "buy_streak"       DOUBLE,
    "flow_ma20"        DOUBLE,
    PRIMARY KEY ("symbol", "date", "as_of")
);

CREATE TABLE IF NOT EXISTS scr1b_bandar_accum (
    "symbol"            VARCHAR   NOT NULL,
    "date"              DATE      NOT NULL,
    "as_of"             TIMESTAMP NOT NULL,
    "bandar_value"      DOUBLE,
    "bandar_value_ma20" DOUBLE,
    "bandar_accum_dist" DOUBLE,
    "adv20"             DOUBLE,
    PRIMARY KEY ("symbol", "date", "as_of")
);

CREATE TABLE IF NOT EXISTS scr1c_stealth_divergence (
    "symbol"          VARCHAR   NOT NULL,
    "date"            DATE      NOT NULL,
    "as_of"           TIMESTAMP NOT NULL,
    "bandar_value"    DOUBLE,
    "price_return_1m" DOUBLE,
    "volume"          DOUBLE,
    "volume_ma20"     DOUBLE,
    PRIMARY KEY ("symbol", "date", "as_of")
);

CREATE TABLE IF NOT EXISTS scr2_volume_anomaly (
    "symbol"          VARCHAR   NOT NULL,
    "date"            DATE      NOT NULL,
    "as_of"           TIMESTAMP NOT NULL,
    "volume"          DOUBLE,
    "volume_ma20"     DOUBLE,
    "frequency"       DOUBLE,
    "frequency_spike" DOUBLE,
    PRIMARY KEY ("symbol", "date", "as_of")
);

CREATE TABLE IF NOT EXISTS scr_exit_distribution (
    "symbol"              VARCHAR   NOT NULL,
    "date"                DATE      NOT NULL,
    "as_of"               TIMESTAMP NOT NULL,
    "bandar_accum_dist"   DOUBLE,
    "net_foreign_ma20"    DOUBLE,
    "foreign_sell_streak" DOUBLE,
    PRIMARY KEY ("symbol", "date", "as_of")
);

CREATE TABLE IF NOT EXISTS scr3_trend_confirm (
    "symbol"     VARCHAR   NOT NULL,
    "date"       DATE      NOT NULL,
    "as_of"      TIMESTAMP NOT NULL,
    "price"      DOUBLE,
    "price_ma20" DOUBLE,
    "price_ma50" DOUBLE,
    "vwap"       DOUBLE,
    "adx14"      DOUBLE,
    "atr14"      DOUBLE,
    "rs_3m"      DOUBLE,
    PRIMARY KEY ("symbol", "date", "as_of")
);

CREATE TABLE IF NOT EXISTS scr4_fundamental_tilt (
    "symbol"         VARCHAR   NOT NULL,
    "date"           DATE      NOT NULL,
    "as_of"          TIMESTAMP NOT NULL,
    "mf_rank_pct"    DOUBLE,
    "roc_greenblatt" DOUBLE,
    "ev_ebit"        DOUBLE,
    "rank_roic"      DOUBLE,
    "roe"            DOUBLE,
    "market_cap"     DOUBLE,
    PRIMARY KEY ("symbol", "date", "as_of")
);

CREATE TABLE IF NOT EXISTS symbol_index (
    "symbol"  VARCHAR   NOT NULL,
    "as_of"   TIMESTAMP NOT NULL,
    "indexes" VARCHAR   NOT NULL,
    PRIMARY KEY ("symbol", "as_of")
);

CREATE TABLE IF NOT EXISTS ksei_ownership (
    "symbol"      VARCHAR   NOT NULL,
    "date"        DATE      NOT NULL,
    "as_of"       TIMESTAMP NOT NULL,
    "foreign_pct" DOUBLE,
    "local_pct"   DOUBLE,
    PRIMARY KEY ("symbol", "date", "as_of")
);

CREATE TABLE IF NOT EXISTS scheduler_runs (
    "feed"          VARCHAR   NOT NULL,
    "last_fired_at" TIMESTAMP NOT NULL,
    "rows_written"  BIGINT    NOT NULL,
    "outcome"       VARCHAR   NOT NULL,
    PRIMARY KEY ("feed", "last_fired_at")
);

CREATE TABLE IF NOT EXISTS paper_position (
    "symbol"      VARCHAR   NOT NULL,
    "as_of"       TIMESTAMP NOT NULL,
    "track"       VARCHAR   NOT NULL,
    "sector"      VARCHAR   NOT NULL,
    "board"       VARCHAR   NOT NULL,
    "tier"        VARCHAR   NOT NULL,
    "tilt_kind"   VARCHAR   NOT NULL,
    "entry_date"  DATE      NOT NULL,
    "entry_price" DOUBLE    NOT NULL,
    "stop"        DOUBLE    NOT NULL,
    "target"      DOUBLE,
    "trail_pct"   DOUBLE    NOT NULL,
    "qty"         BIGINT    NOT NULL,
    "risk_idr"    DOUBLE,
    "entry_fee"   DOUBLE    NOT NULL,
    PRIMARY KEY ("symbol")
);

CREATE TABLE IF NOT EXISTS paper_trade (
    "symbol"      VARCHAR   NOT NULL,
    "entry_date"  DATE      NOT NULL,
    "exit_date"   DATE      NOT NULL,
    "as_of"       TIMESTAMP NOT NULL,
    "track"       VARCHAR   NOT NULL,
    "tilt_kind"   VARCHAR   NOT NULL,
    "qty"         BIGINT    NOT NULL,
    "entry_price" DOUBLE    NOT NULL,
    "exit_price"  DOUBLE    NOT NULL,
    "entry_fee"   DOUBLE    NOT NULL,
    "exit_fee"    DOUBLE    NOT NULL,
    "exit_reason" VARCHAR   NOT NULL,
    "stop"        DOUBLE    NOT NULL,
    "risk_idr"    DOUBLE,
    PRIMARY KEY ("symbol", "entry_date", "exit_date")
);

CREATE TABLE IF NOT EXISTS fast_mode_state (
    "key"          VARCHAR   NOT NULL,
    "enabled"      BOOLEAN   NOT NULL,
    "since_date"   DATE,
    "last_run_day" DATE,
    "realized_pnl" DOUBLE    NOT NULL,
    "prev_equity"  DOUBLE    NOT NULL,
    "peak_equity"  DOUBLE    NOT NULL,
    PRIMARY KEY ("key")
);
"""
