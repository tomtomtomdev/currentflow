"""DuckDB schema. Every table keyed on `(symbol, date, as_of)` (+ broker/side).

Column identifiers are always double-quoted in generated SQL because several
(`date`, `open`, `close`, `value`) collide with DuckDB keywords.
"""

from __future__ import annotations

from dataclasses import dataclass
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
"""
