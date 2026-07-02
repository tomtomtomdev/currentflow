"""DuckDB schema. Every table keyed on `(symbol, date, as_of)` (+ broker/side).

Column identifiers are always double-quoted in generated SQL because several
(`date`, `open`, `close`, `value`) collide with DuckDB keywords.
"""

from __future__ import annotations

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

# KSEI monthly ownership slices (foreign-ownership-trend overlay, spec §9).
KSEI_COLUMNS: tuple[str, ...] = (
    "symbol",
    "date",
    "as_of",
    "foreign_pct",
    "local_pct",
)

DDL = """
CREATE TABLE IF NOT EXISTS daily_bar (
    "symbol"            VARCHAR   NOT NULL,
    "date"              DATE      NOT NULL,
    "as_of"             TIMESTAMP NOT NULL,
    "status"            VARCHAR   NOT NULL,
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
    "side"          VARCHAR   NOT NULL,
    "investor_type" VARCHAR   NOT NULL,
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

CREATE TABLE IF NOT EXISTS ksei_ownership (
    "symbol"      VARCHAR   NOT NULL,
    "date"        DATE      NOT NULL,
    "as_of"       TIMESTAMP NOT NULL,
    "foreign_pct" DOUBLE,
    "local_pct"   DOUBLE,
    PRIMARY KEY ("symbol", "date", "as_of")
);
"""
