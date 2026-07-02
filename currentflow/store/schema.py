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
"""
