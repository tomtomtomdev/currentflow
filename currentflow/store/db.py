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

import logging
from collections.abc import Iterable, Sequence
from datetime import date as Date
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, TypeVar

import duckdb

if TYPE_CHECKING:
    from currentflow.store.integrity import EnumIntegrityReport

from currentflow.dal.models import (
    BrokerNet,
    DailyBar,
    InvestorType,
    OwnershipSlice,
    RowStatus,
    Scr0Row,
    Scr1aRow,
    Scr1bRow,
    Scr1cRow,
    Scr2Row,
    Scr3Row,
    Scr4Row,
    ScrExitRow,
    Side,
    SymbolIndexRow,
)
from currentflow.store.schema import (
    BROKER_NET_COLUMNS,
    DAILY_BAR_COLUMNS,
    DDL,
    KSEI_COLUMNS,
    SCHEDULER_RUNS_COLUMNS,
    SCR0_COLUMNS,
    SCR1A_COLUMNS,
    SCR1B_COLUMNS,
    SCR1C_COLUMNS,
    SCR2_COLUMNS,
    SCR3_COLUMNS,
    SCR4_COLUMNS,
    SCR_EXIT_COLUMNS,
    SYMBOL_INDEX_COLUMNS,
    SchedulerRunRow,
)


log = logging.getLogger(__name__)

_E = TypeVar("_E", bound=Enum)


def _cols(names: Sequence[str]) -> str:
    return ", ".join(f'"{n}"' for n in names)


def _row_arity_ok(r: tuple, expected: int, *, table: str, symbol: str) -> bool:
    """Guard a fetched row against having fewer columns than the schema expects.

    A fixed-column SELECT should always return `expected` values, but a DB left in a
    partial/corrupt state (mid-ingest, a pre-schema-constraint file, a truncated write)
    can surface a short row. Unpacking it would raise IndexError and take down the whole
    terminal — so we skip it and log loudly instead (no silent caps, CLAUDE.md), matching
    the corrupt-enum guard below."""
    if len(r) >= expected:
        return True
    log.warning(
        "dropping malformed %s row for %s: got %d columns, expected %d: %r",
        table, symbol, len(r), expected, r,
    )
    return False


def _coerce_enum(cls: type[_E], raw: object, *, table: str, symbol: str) -> _E | None:
    """Parse a stored VARCHAR into `cls`, or return None for a corrupt value.

    Status/side/type columns are plain VARCHAR (no CHECK constraint), so a
    column-misaligned or cross-table insert can leave a stray value there — e.g. a
    broker code like 'GR' landing in `daily_bar.status`. One bad row must not crash
    the whole terminal, so we skip it and log loudly (no silent caps, CLAUDE.md).
    """
    try:
        return cls(raw)
    except ValueError:
        log.warning(
            "dropping corrupt %s row for %s: %r is not a valid %s",
            table, symbol, raw, cls.__name__,
        )
        return None


class Store:
    def __init__(self, path: str = ":memory:") -> None:
        self._con = duckdb.connect(path)
        self._con.execute(DDL)

    def close(self) -> None:
        self._con.close()

    def check_enum_integrity(self) -> "EnumIntegrityReport":
        """Scan enum-typed columns for corrupt values (e.g. a broker code leaked into
        `daily_bar.status`). Logs loudly and returns a report. Cheap; run after ingest
        or on startup for DBs created before the schema CHECK constraints existed."""
        from currentflow.store.integrity import scan_enum_integrity

        return scan_enum_integrity(self._con)

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

    def write_scr1b(self, rows_in: Iterable[Scr1bRow]) -> int:
        rows = [
            (
                r.symbol, r.date, r.as_of, r.bandar_value,
                r.bandar_value_ma20, r.bandar_accum_dist, r.adv20,
            )
            for r in rows_in
        ]
        return self._insert("scr1b_bandar_accum", SCR1B_COLUMNS, rows)

    def write_scr1c(self, rows_in: Iterable[Scr1cRow]) -> int:
        rows = [
            (
                r.symbol, r.date, r.as_of, r.bandar_value,
                r.price_return_1m, r.volume, r.volume_ma20,
            )
            for r in rows_in
        ]
        return self._insert("scr1c_stealth_divergence", SCR1C_COLUMNS, rows)

    def write_scr2(self, rows_in: Iterable[Scr2Row]) -> int:
        rows = [
            (
                r.symbol, r.date, r.as_of, r.volume,
                r.volume_ma20, r.frequency, r.frequency_spike,
            )
            for r in rows_in
        ]
        return self._insert("scr2_volume_anomaly", SCR2_COLUMNS, rows)

    def write_scr_exit(self, rows_in: Iterable[ScrExitRow]) -> int:
        rows = [
            (
                r.symbol, r.date, r.as_of, r.bandar_accum_dist,
                r.net_foreign_ma20, r.foreign_sell_streak,
            )
            for r in rows_in
        ]
        return self._insert("scr_exit_distribution", SCR_EXIT_COLUMNS, rows)

    def write_scr3(self, rows_in: Iterable[Scr3Row]) -> int:
        rows = [
            (
                r.symbol, r.date, r.as_of, r.price, r.price_ma20, r.price_ma50,
                r.vwap, r.adx14, r.atr14, r.rs_3m,
            )
            for r in rows_in
        ]
        return self._insert("scr3_trend_confirm", SCR3_COLUMNS, rows)

    def write_scr4(self, rows_in: Iterable[Scr4Row]) -> int:
        rows = [
            (
                r.symbol, r.date, r.as_of, r.mf_rank_pct, r.roc_greenblatt,
                r.ev_ebit, r.rank_roic, r.roe, r.market_cap,
            )
            for r in rows_in
        ]
        return self._insert("scr4_fundamental_tilt", SCR4_COLUMNS, rows)

    def write_ksei_ownership(self, rows_in: Iterable[OwnershipSlice]) -> int:
        rows = [
            (r.symbol, r.date, r.as_of, r.foreign_pct, r.local_pct) for r in rows_in
        ]
        return self._insert("ksei_ownership", KSEI_COLUMNS, rows)

    def write_symbol_index(self, rows_in: Iterable[SymbolIndexRow]) -> int:
        """Index-membership snapshots (§3 Track source). `indexes` is stored comma-joined
        and split back on read; unlike OHLCV this is *not* ingest-once — membership drifts
        at index reconstitution, so each refresh writes a fresh `as_of` and read-latest wins."""
        rows = [(r.symbol, r.as_of, ",".join(r.indexes)) for r in rows_in]
        return self._insert("symbol_index", SYMBOL_INDEX_COLUMNS, rows)

    def write_scheduler_run(self, rows_in: Iterable[SchedulerRunRow]) -> int:
        """Record scheduler fires (slice 12). One row per fire; latest per feed drives
        due-ness (read_scheduler_run_latest). Not ingest-once — each fire is a distinct
        `last_fired_at`; a same-`now` re-write is an exact-key no-op (ON CONFLICT)."""
        rows = [
            (r.feed, r.last_fired_at, r.rows_written, r.outcome) for r in rows_in
        ]
        return self._insert("scheduler_runs", SCHEDULER_RUNS_COLUMNS, rows)

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
        out: list[DailyBar] = []
        for r in rows:
            if not _row_arity_ok(r, len(DAILY_BAR_COLUMNS), table="daily_bar", symbol=symbol):
                continue
            status = _coerce_enum(RowStatus, r[3], table="daily_bar", symbol=symbol)
            if status is None:
                continue
            out.append(
                DailyBar(
                    symbol=r[0], date=r[1], as_of=r[2], status=status,
                    open=r[4], high=r[5], low=r[6], close=r[7], volume=r[8], value=r[9],
                    frequency=r[10], vwap=r[11], foreign_buy=r[12], foreign_sell=r[13],
                    net_foreign=r[14], change_percentage=r[15],
                )
            )
        return out

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
        out: list[BrokerNet] = []
        for r in rows:
            if not _row_arity_ok(r, len(BROKER_NET_COLUMNS), table="broker_net", symbol=symbol):
                continue
            side = _coerce_enum(Side, r[4], table="broker_net", symbol=symbol)
            investor_type = _coerce_enum(InvestorType, r[5], table="broker_net", symbol=symbol)
            if side is None or investor_type is None:
                continue
            out.append(
                BrokerNet(
                    symbol=r[0], date=r[1], as_of=r[2], broker_code=r[3], side=side,
                    investor_type=investor_type, avg_price=r[6], value=r[7],
                    lot=r[8], frequency=r[9],
                )
            )
        return out

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

    def read_scr1b(self, day: Date, decision_ts: datetime) -> list[Scr1bRow]:
        """SCR-1B survivors for `day` as visible at `decision_ts` (latest as_of/symbol)."""
        rows = self._read_screener("scr1b_bandar_accum", SCR1B_COLUMNS, day, decision_ts)
        return [
            Scr1bRow(
                symbol=r[0], date=r[1], as_of=r[2], bandar_value=r[3],
                bandar_value_ma20=r[4], bandar_accum_dist=r[5], adv20=r[6],
            )
            for r in rows
        ]

    def read_scr1c(self, day: Date, decision_ts: datetime) -> list[Scr1cRow]:
        """SCR-1C survivors for `day` as visible at `decision_ts` (latest as_of/symbol)."""
        rows = self._read_screener("scr1c_stealth_divergence", SCR1C_COLUMNS, day, decision_ts)
        return [
            Scr1cRow(
                symbol=r[0], date=r[1], as_of=r[2], bandar_value=r[3],
                price_return_1m=r[4], volume=r[5], volume_ma20=r[6],
            )
            for r in rows
        ]

    def read_scr2(self, day: Date, decision_ts: datetime) -> list[Scr2Row]:
        """SCR-2 survivors for `day` as visible at `decision_ts` (latest as_of/symbol)."""
        rows = self._read_screener("scr2_volume_anomaly", SCR2_COLUMNS, day, decision_ts)
        return [
            Scr2Row(
                symbol=r[0], date=r[1], as_of=r[2], volume=r[3],
                volume_ma20=r[4], frequency=r[5], frequency_spike=r[6],
            )
            for r in rows
        ]

    def read_scr_exit(self, day: Date, decision_ts: datetime) -> list[ScrExitRow]:
        """SCR-EXIT survivors for `day` as visible at `decision_ts` (latest as_of/symbol)."""
        rows = self._read_screener("scr_exit_distribution", SCR_EXIT_COLUMNS, day, decision_ts)
        return [
            ScrExitRow(
                symbol=r[0], date=r[1], as_of=r[2], bandar_accum_dist=r[3],
                net_foreign_ma20=r[4], foreign_sell_streak=r[5],
            )
            for r in rows
        ]

    def read_scr3(self, day: Date, decision_ts: datetime) -> list[Scr3Row]:
        """SCR-3 survivors for `day` as visible at `decision_ts` (latest as_of/symbol)."""
        rows = self._read_screener("scr3_trend_confirm", SCR3_COLUMNS, day, decision_ts)
        return [
            Scr3Row(
                symbol=r[0], date=r[1], as_of=r[2], price=r[3], price_ma20=r[4],
                price_ma50=r[5], vwap=r[6], adx14=r[7], atr14=r[8], rs_3m=r[9],
            )
            for r in rows
        ]

    def read_scr4(self, day: Date, decision_ts: datetime) -> list[Scr4Row]:
        """SCR-4 tilt rows for `day` as visible at `decision_ts` (latest as_of/symbol)."""
        rows = self._read_screener("scr4_fundamental_tilt", SCR4_COLUMNS, day, decision_ts)
        return [
            Scr4Row(
                symbol=r[0], date=r[1], as_of=r[2], mf_rank_pct=r[3], roc_greenblatt=r[4],
                ev_ebit=r[5], rank_roic=r[6], roe=r[7], market_cap=r[8],
            )
            for r in rows
        ]

    def _read_screener(
        self, table: str, columns: Sequence[str], day: Date, decision_ts: datetime
    ) -> list[tuple]:
        sql = (
            f"SELECT {_cols(columns)} FROM {table} "
            'WHERE "date" = ? AND "as_of" < ? '
            'QUALIFY row_number() OVER (PARTITION BY "symbol" ORDER BY "as_of" DESC) = 1 '
            'ORDER BY "symbol"'
        )
        return self._con.execute(sql, [day, decision_ts]).fetchall()

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

    def read_symbol_index_latest(
        self, symbol: str, decision_ts: datetime
    ) -> SymbolIndexRow | None:
        """Most recent index-membership snapshot for `symbol` visible at `decision_ts`
        (`as_of < decision_ts` — the look-ahead firewall). None when nothing is stored."""
        sql = (
            f"SELECT {_cols(SYMBOL_INDEX_COLUMNS)} FROM symbol_index "
            'WHERE "symbol" = ? AND "as_of" < ? '
            'ORDER BY "as_of" DESC LIMIT 1'
        )
        r = self._con.execute(sql, [symbol, decision_ts]).fetchone()
        if r is None:
            return None
        indexes = tuple(i for i in r[2].split(",") if i)  # "" → () (no membership known)
        return SymbolIndexRow(symbol=r[0], as_of=r[1], indexes=indexes)

    # --- scheduler run-state (slice 12) -------------------------------------------

    def read_scheduler_run_latest(self, feed: str) -> SchedulerRunRow | None:
        """The most recent recorded fire of `feed` (any outcome) — the scheduler's
        due-math anchor. None when the feed has never fired (a fresh install)."""
        sql = (
            f"SELECT {_cols(SCHEDULER_RUNS_COLUMNS)} FROM scheduler_runs "
            'WHERE "feed" = ? ORDER BY "last_fired_at" DESC LIMIT 1'
        )
        r = self._con.execute(sql, [feed]).fetchone()
        if r is None:
            return None
        return SchedulerRunRow(
            feed=r[0], last_fired_at=r[1], rows_written=r[2], outcome=r[3]
        )

    def symbols(self, table: str = "daily_bar") -> list[str]:
        """Distinct symbols present in `table` (the ingested-universe roster the
        scheduler's per-symbol feeds iterate). Not look-ahead-gated — it is an
        operational roster, not a signal read."""
        return [
            r[0]
            for r in self._con.execute(
                f'SELECT DISTINCT "symbol" FROM {table} ORDER BY "symbol"'
            ).fetchall()
        ]

    def scr0_universe(self, decision_ts: datetime) -> list[str]:
        """The latest cached SCR-0 survivor set visible at `decision_ts` — the
        scheduler's UNIVERSE scope. Takes the most recent `date` whose rows are visible
        (`as_of < decision_ts`, the look-ahead firewall); empty when no screener has run
        yet (missing ≠ zero — the caller logs and ingests nothing rather than inventing a
        universe)."""
        sql = (
            'SELECT DISTINCT "symbol" FROM scr0_eligible '
            'WHERE "as_of" < ? AND "date" = ('
            '  SELECT max("date") FROM scr0_eligible WHERE "as_of" < ?'
            ') ORDER BY "symbol"'
        )
        return [
            r[0]
            for r in self._con.execute(sql, [decision_ts, decision_ts]).fetchall()
        ]

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
