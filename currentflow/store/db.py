"""DuckDB-backed feature store.

Guarantees:
  * **Ingest-once** — writes use `ON CONFLICT DO NOTHING`; re-writing a stored
    `(symbol, date, as_of)` is a no-op (DATA_SOURCES §4). `write_*` returns the count
    of rows *actually* inserted so callers can log what was new.
  * **Look-ahead-safe reads** — `read_*` require a `decision_ts` and return only rows
    with `as_of < decision_ts`, collapsing to the latest visible `as_of` per date
    (point-in-time correct; spec §1).
  * **empty ≠ zero** — reads return only rows that exist; absent dates are simply
    absent (never fabricated as zero). Use `classify_coverage` to reason about gaps.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import date as Date
from datetime import datetime

import duckdb

from currentflow.dal.models import (
    BrokerNet,
    DailyBar,
    InvestorType,
    OwnershipSlice,
    RowStatus,
    Scr0Row,
    Scr1aRow,
    Side,
)
from currentflow.store.schema import (
    BROKER_NET_COLUMNS,
    DAILY_BAR_COLUMNS,
    DDL,
    KSEI_COLUMNS,
    SCR0_COLUMNS,
    SCR1A_COLUMNS,
)


def _cols(names: Sequence[str]) -> str:
    return ", ".join(f'"{n}"' for n in names)


class Store:
    def __init__(self, path: str = ":memory:") -> None:
        self._con = duckdb.connect(path)
        self._con.execute(DDL)

    def close(self) -> None:
        self._con.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # --- writes (ingest-once) -----------------------------------------------------

    def write_daily_bars(self, bars: Iterable[DailyBar]) -> int:
        rows = [
            (
                b.symbol, b.date, b.as_of, b.status.value,
                b.open, b.high, b.low, b.close, b.volume, b.value,
                b.frequency, b.vwap, b.foreign_buy, b.foreign_sell,
                b.net_foreign, b.change_percentage,
            )
            for b in bars
        ]
        return self._insert("daily_bar", DAILY_BAR_COLUMNS, rows)

    def write_broker_net(self, rows_in: Iterable[BrokerNet]) -> int:
        rows = [
            (
                r.symbol, r.date, r.as_of, r.broker_code, r.side.value,
                r.investor_type.value, r.avg_price, r.value, r.lot, r.frequency,
            )
            for r in rows_in
        ]
        return self._insert("broker_net", BROKER_NET_COLUMNS, rows)

    def write_scr0_eligible(self, rows_in: Iterable[Scr0Row]) -> int:
        rows = [
            (r.symbol, r.date, r.as_of, r.adv20, r.price, r.free_float, r.market_cap)
            for r in rows_in
        ]
        return self._insert("scr0_eligible", SCR0_COLUMNS, rows)

    def write_scr1a(self, rows_in: Iterable[Scr1aRow]) -> int:
        rows = [
            (
                r.symbol, r.date, r.as_of, r.net_foreign,
                r.net_foreign_ma20, r.buy_streak, r.flow_ma20,
            )
            for r in rows_in
        ]
        return self._insert("scr1a_foreign_accum", SCR1A_COLUMNS, rows)

    def write_ksei_ownership(self, rows_in: Iterable[OwnershipSlice]) -> int:
        rows = [
            (r.symbol, r.date, r.as_of, r.foreign_pct, r.local_pct) for r in rows_in
        ]
        return self._insert("ksei_ownership", KSEI_COLUMNS, rows)

    def _insert(self, table: str, columns: Sequence[str], rows: list[tuple]) -> int:
        if not rows:
            return 0
        before = self._count(table)
        placeholders = ", ".join("?" for _ in columns)
        sql = (
            f'INSERT INTO {table} ({_cols(columns)}) VALUES ({placeholders}) '
            f"ON CONFLICT DO NOTHING"
        )
        self._con.executemany(sql, rows)
        return self._count(table) - before

    def _count(self, table: str) -> int:
        return self._con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]

    # --- ingest-once helpers ------------------------------------------------------

    def ingested_dates(self, symbol: str, table: str = "daily_bar") -> set[Date]:
        """Dates already stored for `symbol` — the pipeline fetches only the rest."""
        rows = self._con.execute(
            f'SELECT DISTINCT "date" FROM {table} WHERE "symbol" = ?', [symbol]
        ).fetchall()
        return {r[0] for r in rows}

    # --- look-ahead-safe reads ----------------------------------------------------

    def read_daily_bars(
        self,
        symbol: str,
        decision_ts: datetime,
        start: Date | None = None,
        end: Date | None = None,
    ) -> list[DailyBar]:
        rows = self._read("daily_bar", DAILY_BAR_COLUMNS, symbol, decision_ts, start, end)
        return [
            DailyBar(
                symbol=r[0], date=r[1], as_of=r[2], status=RowStatus(r[3]),
                open=r[4], high=r[5], low=r[6], close=r[7], volume=r[8], value=r[9],
                frequency=r[10], vwap=r[11], foreign_buy=r[12], foreign_sell=r[13],
                net_foreign=r[14], change_percentage=r[15],
            )
            for r in rows
        ]

    def read_broker_net(
        self,
        symbol: str,
        decision_ts: datetime,
        start: Date | None = None,
        end: Date | None = None,
    ) -> list[BrokerNet]:
        # collapse to latest visible as_of per (date, broker, side)
        rows = self._read(
            "broker_net", BROKER_NET_COLUMNS, symbol, decision_ts, start, end,
            partition=("date", "broker_code", "side"),
        )
        return [
            BrokerNet(
                symbol=r[0], date=r[1], as_of=r[2], broker_code=r[3], side=Side(r[4]),
                investor_type=InvestorType(r[5]), avg_price=r[6], value=r[7],
                lot=r[8], frequency=r[9],
            )
            for r in rows
        ]

    def read_scr0_eligible(self, day: Date, decision_ts: datetime) -> list[Scr0Row]:
        """Eligible set for `day` as visible at `decision_ts` (latest as_of per symbol)."""
        sql = (
            f"SELECT {_cols(SCR0_COLUMNS)} FROM scr0_eligible "
            'WHERE "date" = ? AND "as_of" < ? '
            'QUALIFY row_number() OVER (PARTITION BY "symbol" ORDER BY "as_of" DESC) = 1 '
            'ORDER BY "symbol"'
        )
        rows = self._con.execute(sql, [day, decision_ts]).fetchall()
        return [
            Scr0Row(
                symbol=r[0], date=r[1], as_of=r[2], adv20=r[3],
                price=r[4], free_float=r[5], market_cap=r[6],
            )
            for r in rows
        ]

    def read_scr1a(self, day: Date, decision_ts: datetime) -> list[Scr1aRow]:
        """SCR-1A survivors for `day` as visible at `decision_ts` (latest as_of per symbol)."""
        sql = (
            f"SELECT {_cols(SCR1A_COLUMNS)} FROM scr1a_foreign_accum "
            'WHERE "date" = ? AND "as_of" < ? '
            'QUALIFY row_number() OVER (PARTITION BY "symbol" ORDER BY "as_of" DESC) = 1 '
            'ORDER BY "symbol"'
        )
        rows = self._con.execute(sql, [day, decision_ts]).fetchall()
        return [
            Scr1aRow(
                symbol=r[0], date=r[1], as_of=r[2], net_foreign=r[3],
                net_foreign_ma20=r[4], buy_streak=r[5], flow_ma20=r[6],
            )
            for r in rows
        ]

    def read_ksei_ownership(
        self,
        symbol: str,
        decision_ts: datetime,
        start: Date | None = None,
        end: Date | None = None,
    ) -> list[OwnershipSlice]:
        rows = self._read("ksei_ownership", KSEI_COLUMNS, symbol, decision_ts, start, end)
        return [
            OwnershipSlice(symbol=r[0], date=r[1], as_of=r[2], foreign_pct=r[3], local_pct=r[4])
            for r in rows
        ]

    def read_scr0_latest(self, symbol: str, decision_ts: datetime) -> Scr0Row | None:
        """Most recent SCR-0 row for `symbol` visible at `decision_ts` (float/mcap context)."""
        sql = (
            f"SELECT {_cols(SCR0_COLUMNS)} FROM scr0_eligible "
            'WHERE "symbol" = ? AND "as_of" < ? '
            'ORDER BY "date" DESC, "as_of" DESC LIMIT 1'
        )
        r = self._con.execute(sql, [symbol, decision_ts]).fetchone()
        if r is None:
            return None
        return Scr0Row(
            symbol=r[0], date=r[1], as_of=r[2], adv20=r[3],
            price=r[4], free_float=r[5], market_cap=r[6],
        )

    def _read(
        self,
        table: str,
        columns: Sequence[str],
        symbol: str,
        decision_ts: datetime,
        start: Date | None,
        end: Date | None,
        partition: Sequence[str] = ("date",),
    ) -> list[tuple]:
        params: list = [symbol, decision_ts]
        where = ['"symbol" = ?', '"as_of" < ?']  # <-- the look-ahead firewall
        if start is not None:
            where.append('"date" >= ?')
            params.append(start)
        if end is not None:
            where.append('"date" <= ?')
            params.append(end)
        part = ", ".join(f'"{p}"' for p in partition)
        sql = (
            f"SELECT {_cols(columns)} FROM {table} "
            f"WHERE {' AND '.join(where)} "
            f'QUALIFY row_number() OVER (PARTITION BY {part} ORDER BY "as_of" DESC) = 1 '
            f'ORDER BY "date"'
        )
        return self._con.execute(sql, params).fetchall()
