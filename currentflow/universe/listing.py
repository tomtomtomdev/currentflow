"""Board-listing history — the `LISTED` pseudo-index (slice 22, BACKTEST_PHASE0 §3.1).

Every `exodus` endpoint is symbol-addressed: you cannot ask Stockbit "what was listed in
2024". So a universe reconstructed from the store alone holds exactly the names the
vendor still serves today, and every delisted, suspended, or force-delisted name is
silently gone — which on IDX are precisely the names a value or high-float screen
surfaces. Uncorrected, that inflates every backtested return invisibly.

The fix is an outside source: the **IDX Statistics annual books** (idx.co.id, free PDF)
list every listed company for their year. The operator transcribes each book to a CSV
under `data/listings/`; this module diffs consecutive snapshots into listing periods and
loads them as the `LISTED` pseudo-index in `index_roster_pit`.

CSV shape (header required; `listing_date` and `market_cap_idr` optional, blank = unknown):

    snapshot_date,symbol,source,listing_date,market_cap_idr
    2024-12-31,BBCA,IDX Statistics 2024 p.42,2000-05-31,1234000000000000
    2024-12-31,GONE,IDX Statistics 2024 p.51,,

**What the annual cadence can and cannot say.** A name in the 2024 book and absent from
the 2025 book delisted *somewhere between those two dates* — the books cannot date it to
the day. The derived period therefore closes at the last snapshot that still listed the
name (the conservative end: the name is treated as gone from the first day it can no
longer be evidenced) and the uncertainty window is written into the period's own `source`
string, so no reader can mistake it for an announcement-dated boundary.

The *start* is the other way round: the books print each company's listing date, so
transcribing `listing_date` gives an exact period start. Without it the period starts at
the first book that lists the name — "first observed", not "listed on", which understates
tenure and hides the name for any day before that book. The derive report counts those
first-observed starts so the weaker basis is never invisible.

`LISTED` is a **listing fact, never index membership**: `assign_track` only ever looks at
`config.TRACK_A_INDEXES`, and `Store.roster_covers` is scoped to those same indexes, so a
loaded book can neither promote a name to Track A nor mask a missing LQ45/IDX80 roster.
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import date as Date
from datetime import datetime, timedelta
from pathlib import Path

from currentflow import config
from currentflow.store.schema import IndexRosterRow, ListingSnapshotRow

# Default location for the operator's transcribed IDX Statistics books.
LISTING_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "listings"

LISTED_INDEX = config.LISTED_INDEX

# Stamped into every derived period's provenance so the annual granularity travels with
# the data (a `LISTED` boundary is never an announcement-dated one).
ANNUAL_PRECISION = "ANNUAL_SNAPSHOT precision"

_REQUIRED = ("snapshot_date", "symbol", "source")
_OPTIONAL = ("listing_date", "market_cap_idr")


class ListingValidationError(ValueError):
    """A listing CSV is malformed, missing provenance, or unreadable."""


@dataclass(frozen=True, slots=True)
class ListingDeriveReport:
    snapshots: tuple[Date, ...]
    symbols: int
    open_periods: int
    closed_periods: int
    relistings: int
    unknown_market_cap: int          # (snapshot, symbol) rows whose book gave no cap
    first_observed_starts: int       # periods with no transcribed listing date
    clamped_relistings: int          # relisting starts pushed after the prior period

    @property
    def start_caveat(self) -> str:
        first = self.snapshots[0] if self.snapshots else None
        return (
            f"{self.first_observed_starts} period(s) start at 'first observed' (the "
            f"earliest book that lists the name, from {first}) rather than a transcribed "
            "listing date — their true tenure is longer, and they are invisible before "
            "that book"
        )


@dataclass(frozen=True, slots=True)
class ListingLoadReport:
    files_read: tuple[str, ...]
    snapshot_rows: int               # rows newly written to `listing_snapshot`
    roster_rows: int                 # LISTED periods newly written to `index_roster_pit`
    derive: ListingDeriveReport

    def line(self) -> str:
        d = self.derive
        return (
            f"listings: +{self.snapshot_rows} snapshot rows, +{self.roster_rows} LISTED "
            f"periods from {len(self.files_read)} file(s) — {d.symbols} names over "
            f"{len(d.snapshots)} book(s) {[str(s) for s in d.snapshots]}; "
            f"{d.open_periods} open, {d.closed_periods} closed, {d.relistings} relisting(s), "
            f"{d.first_observed_starts} first-observed start(s), "
            f"{d.unknown_market_cap} row(s) with no market cap"
        )


def _parse_date(raw: str, *, where: str) -> Date:
    try:
        return Date.fromisoformat(raw.strip())
    except ValueError as exc:  # noqa: TRY003 — surface the bad token
        raise ListingValidationError(f"{where}: bad date {raw!r}: {exc}") from exc


def _parse_cap(raw: str | None, *, where: str) -> float | None:
    """Blank/absent → None (unknown). A non-numeric value fails loud rather than
    degrading to 0 — a zero market cap is a claim, an absent one is not."""
    if raw is None or not raw.strip():
        return None
    try:
        return float(raw.strip().replace(",", "").replace("_", ""))
    except ValueError as exc:
        raise ListingValidationError(f"{where}: bad market_cap_idr {raw!r}") from exc


def parse_snapshots(
    text: str, *, now: datetime, filename: str = "<string>"
) -> list[ListingSnapshotRow]:
    """Parse one transcribed book (or several, if the file carries many snapshot dates)."""
    reader = csv.DictReader(text.splitlines())
    fields = tuple(f.strip() for f in (reader.fieldnames or ()))
    if not set(_REQUIRED).issubset(fields) or not set(fields).issubset(_REQUIRED + _OPTIONAL):
        raise ListingValidationError(
            f"{filename}: header must be {','.join(_REQUIRED)}"
            f"[,{','.join(_OPTIONAL)}], got {list(fields)}"
        )
    rows: list[ListingSnapshotRow] = []
    seen: set[tuple[Date, str]] = set()
    for i, rec in enumerate(reader, start=2):  # row 1 is the header
        where = f"{filename} line {i}"
        source = (rec.get("source") or "").strip()
        if not source:
            raise ListingValidationError(f"{where}: missing source (no silent provenance)")
        symbol = (rec.get("symbol") or "").strip().upper()
        if not symbol:
            raise ListingValidationError(f"{where}: missing symbol")
        snapshot_date = _parse_date(rec.get("snapshot_date") or "", where=where)
        raw_listing = (rec.get("listing_date") or "").strip()
        listing_date = _parse_date(raw_listing, where=where) if raw_listing else None
        if listing_date is not None and listing_date > snapshot_date:
            raise ListingValidationError(
                f"{where}: listing_date {listing_date} is after the book's "
                f"snapshot_date {snapshot_date}"
            )
        key = (snapshot_date, symbol)
        if key in seen:                      # a book lists a company once
            continue
        seen.add(key)
        rows.append(
            ListingSnapshotRow(
                snapshot_date=snapshot_date,
                symbol=symbol,
                listing_date=listing_date,
                market_cap_idr=_parse_cap(rec.get("market_cap_idr"), where=where),
                source=source,
                as_of=now,
            )
        )
    return rows


def derive_periods(
    rows: list[ListingSnapshotRow],
) -> tuple[list[IndexRosterRow], ListingDeriveReport]:
    """Diff consecutive snapshots into `LISTED` periods (one per contiguous run).

    A run that reaches the newest loaded book stays open (`effective_to=None`); one that
    stops earlier closes at its last observation, with the delisting window named in the
    period's `source`. A run starts at the transcribed listing date when the book gave
    one, else at the run's first snapshot ("first observed" — counted in the report).
    Two runs for one name = a relisting → two periods; the later start is clamped to the
    day after the earlier period ends (books print the *original* listing date, which
    would otherwise reach back through the gap), keeping them disjoint for
    `roster.validate_periods`.
    """
    snapshots = tuple(sorted({r.snapshot_date for r in rows}))
    if not snapshots:
        return [], ListingDeriveReport((), 0, 0, 0, 0, 0, 0, 0)
    index_of = {d: i for i, d in enumerate(snapshots)}

    by_symbol: dict[str, dict[Date, ListingSnapshotRow]] = {}
    for r in rows:
        by_symbol.setdefault(r.symbol, {})[r.snapshot_date] = r

    periods: list[IndexRosterRow] = []
    open_n = closed_n = relistings = first_observed = clamped = 0
    unknown_cap = sum(1 for r in rows if r.market_cap_idr is None)

    for symbol in sorted(by_symbol):
        seen = sorted(by_symbol[symbol], key=lambda d: index_of[d])
        runs: list[list[Date]] = []
        for d in seen:
            if runs and index_of[d] == index_of[runs[-1][-1]] + 1:
                runs[-1].append(d)
            else:
                runs.append([d])
        relistings += len(runs) - 1
        prev_end: Date | None = None
        for run in runs:
            first, last = run[0], run[-1]
            row = by_symbol[symbol][last]
            listing_date = by_symbol[symbol][first].listing_date
            notes = ["LISTED derived from annual snapshots"]

            if listing_date is None:
                first_observed += 1
                effective_from = first
                notes.append(f"start = first observed in the {first} book, not a listing date")
            else:
                effective_from = listing_date
            if prev_end is not None and effective_from <= prev_end:
                clamped += 1
                effective_from = prev_end + timedelta(days=1)
                notes.append(
                    f"relisting: start clamped to {effective_from} (the book's listing "
                    f"date precedes the prior period's end {prev_end})"
                )

            still_listed = index_of[last] == len(snapshots) - 1
            if still_listed:
                open_n += 1
                effective_to: Date | None = None
            else:
                closed_n += 1
                next_book = snapshots[index_of[last] + 1]
                notes.append(
                    f"delisted between {last} and {next_book} ({ANNUAL_PRECISION})"
                )
                effective_to = last
            prev_end = effective_to

            periods.append(
                IndexRosterRow(
                    index_name=LISTED_INDEX,
                    symbol=symbol,
                    effective_from=effective_from,
                    effective_to=effective_to,
                    source=" | ".join([row.source, *notes]),
                    as_of=row.as_of,
                )
            )

    report = ListingDeriveReport(
        snapshots=snapshots,
        symbols=len(by_symbol),
        open_periods=open_n,
        closed_periods=closed_n,
        relistings=relistings,
        unknown_market_cap=unknown_cap,
        first_observed_starts=first_observed,
        clamped_relistings=clamped,
    )
    return periods, report


def load_listings(
    store, csv_dir: Path | str = LISTING_DIR, *, now: datetime
) -> ListingLoadReport:
    """Read every ``*.csv`` under `csv_dir`, derive `LISTED` periods, and load both.

    All-or-nothing across the directory: periods are derived from the union of every
    book, so two files can never each look complete yet disagree about a gap year. Both
    writes are ingest-once, so a re-load is a no-op.
    """
    csv_dir = Path(csv_dir)
    if not csv_dir.exists():
        raise ListingValidationError(
            f"listing dir not found: {csv_dir} — transcribe the IDX Statistics annual "
            f"books to CSVs there, header: {','.join(_REQUIRED)}[,{','.join(_OPTIONAL)}]"
        )
    files = sorted(csv_dir.glob("*.csv"))
    rows: list[ListingSnapshotRow] = []
    for path in files:
        rows.extend(parse_snapshots(path.read_text(), now=now, filename=path.name))
    periods, derive = derive_periods(rows)
    snapshot_rows = store.write_listing_snapshot(rows)
    roster_rows = store.write_index_roster(periods)
    return ListingLoadReport(
        files_read=tuple(p.name for p in files),
        snapshot_rows=snapshot_rows,
        roster_rows=roster_rows,
        derive=derive,
    )


# --- CLI -------------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - thin CLI wrapper
    from currentflow.ingest.__main__ import DEFAULT_DB
    from currentflow.store.db import Store
    from currentflow.universe.survivorship import measure_bias, measure_bias_span

    parser = argparse.ArgumentParser(
        prog="currentflow.universe.listing",
        description="Load the LISTED board roster (IDX Statistics books) and report "
                    "the survivorship bias it exposes (slice 22).",
    )
    parser.add_argument("--db", default=DEFAULT_DB, help=f"DuckDB path (default {DEFAULT_DB})")
    parser.add_argument("--dir", default=str(LISTING_DIR),
                        help=f"listing CSV directory (default {LISTING_DIR})")
    parser.add_argument("--bias-on", metavar="YYYY-MM-DD",
                        help="also report the bias measured on this day")
    parser.add_argument("--bias-from", metavar="YYYY-MM-DD",
                        help="report the bias over a span (with --bias-to)")
    parser.add_argument("--bias-to", metavar="YYYY-MM-DD")
    args = parser.parse_args(argv)

    with Store(args.db) as store:
        try:
            report = load_listings(store, args.dir, now=datetime.now())
        except ListingValidationError as exc:
            print(f"LISTING LOAD FAILED: {exc}")
            return 3
        print(report.line())
        if report.derive.first_observed_starts:
            print(f"caveat: {report.derive.start_caveat}")
        if args.bias_on:
            print(measure_bias(store, Date.fromisoformat(args.bias_on)).line())
        if args.bias_from and args.bias_to:
            print(
                measure_bias_span(
                    store,
                    Date.fromisoformat(args.bias_from),
                    Date.fromisoformat(args.bias_to),
                ).line()
            )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
