"""Point-in-time index-roster loader (slice 20, §17.2).

Track A/B needs *historical* LQ45/IDX80 membership — the live `symbol_index` snapshot
only knows today's constituents. IDX republishes index membership on a reconstitution
cadence; the operator supplies each effective-period constituent list as a CSV under
`data/rosters/` and this loader validates + ingests it into `index_roster_pit`.

CSV shape (header required), one row per (index, symbol, period):

    index_name,symbol,effective_from,effective_to,source
    LQ45,BBCA,2024-02-01,2024-07-31,IDX-PENG-00123/BEI/2024
    LQ45,BBCA,2024-08-01,,IDX-PENG-00456/BEI/2024      # open period (still current)

Honesty rules (mirrors the store's no-silent-provenance / missing≠zero posture):
  * every row MUST cite a `source` (an IDX announcement ref) — a period with no source
    is rejected, never loaded blind.
  * overlapping effective periods for the same (index, symbol) are rejected — a name
    cannot be "in LQ45" under two conflicting periods at once.
Rejection fails the whole load loud (RosterValidationError); a partial roster would
silently bias every downstream base rate.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date as Date
from datetime import datetime
from pathlib import Path

from currentflow.store.schema import IndexRosterRow

# Default location for the operator's roster CSVs (git-tracked reference data).
ROSTER_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "rosters"

_HEADER = ("index_name", "symbol", "effective_from", "effective_to", "source")


class RosterValidationError(ValueError):
    """A roster CSV is malformed, missing provenance, or has overlapping periods."""


@dataclass(frozen=True, slots=True)
class RosterLoadReport:
    files_read: tuple[str, ...]
    rows_written: int
    periods_seen: int
    indexes: tuple[str, ...]


def _parse_date(raw: str) -> Date | None:
    raw = raw.strip()
    if not raw:
        return None
    try:
        return Date.fromisoformat(raw)
    except ValueError as exc:  # noqa: TRY003 — surface the bad token
        raise RosterValidationError(f"bad date {raw!r}: {exc}") from exc


def parse_rows(text: str, *, now: datetime, filename: str = "<string>") -> list[IndexRosterRow]:
    """Parse one CSV document into roster rows (no validation beyond per-row shape)."""
    reader = csv.DictReader(text.splitlines())
    if reader.fieldnames is None or tuple(f.strip() for f in reader.fieldnames) != _HEADER:
        raise RosterValidationError(
            f"{filename}: header must be {','.join(_HEADER)}, got {reader.fieldnames!r}"
        )
    rows: list[IndexRosterRow] = []
    for i, rec in enumerate(reader, start=2):  # row 1 is the header
        source = (rec.get("source") or "").strip()
        if not source:
            raise RosterValidationError(
                f"{filename} line {i}: missing source (no silent provenance)"
            )
        eff_from = _parse_date(rec["effective_from"] or "")
        if eff_from is None:
            raise RosterValidationError(f"{filename} line {i}: effective_from is required")
        eff_to = _parse_date(rec.get("effective_to") or "")
        if eff_to is not None and eff_to < eff_from:
            raise RosterValidationError(
                f"{filename} line {i}: effective_to {eff_to} precedes effective_from {eff_from}"
            )
        rows.append(
            IndexRosterRow(
                index_name=rec["index_name"].strip().upper(),
                symbol=rec["symbol"].strip().upper(),
                effective_from=eff_from,
                effective_to=eff_to,
                source=source,
                as_of=now,
            )
        )
    return rows


def validate_periods(rows: list[IndexRosterRow]) -> None:
    """Reject overlapping effective periods for the same (index_name, symbol)."""
    by_key: dict[tuple[str, str], list[IndexRosterRow]] = {}
    for r in rows:
        by_key.setdefault((r.index_name, r.symbol), []).append(r)
    for (index_name, symbol), periods in by_key.items():
        periods = sorted(periods, key=lambda r: r.effective_from)
        for prev, nxt in zip(periods, periods[1:]):
            prev_end = prev.effective_to  # None = open → overlaps everything after it
            if prev_end is None or prev_end >= nxt.effective_from:
                raise RosterValidationError(
                    f"overlapping periods for {index_name}/{symbol}: "
                    f"[{prev.effective_from}..{prev.effective_to}] and "
                    f"[{nxt.effective_from}..{nxt.effective_to}]"
                )


def load_rosters(store, csv_dir: Path | str = ROSTER_DIR, *, now: datetime) -> RosterLoadReport:
    """Read every ``*.csv`` under `csv_dir`, validate, and ingest into `index_roster_pit`.

    Validation is all-or-nothing across the directory (overlaps are checked on the union
    so two files cannot each look clean yet conflict). Ingest-once on the store side means
    a re-load is a no-op; a corrected period must use a new `effective_from`.
    """
    csv_dir = Path(csv_dir)
    if not csv_dir.exists():
        raise RosterValidationError(f"roster dir not found: {csv_dir}")
    files = sorted(p for p in csv_dir.glob("*.csv"))
    all_rows: list[IndexRosterRow] = []
    for path in files:
        all_rows.extend(parse_rows(path.read_text(), now=now, filename=path.name))
    validate_periods(all_rows)
    written = store.write_index_roster(all_rows)
    indexes = tuple(sorted({r.index_name for r in all_rows}))
    return RosterLoadReport(
        files_read=tuple(p.name for p in files),
        rows_written=written,
        periods_seen=len(all_rows),
        indexes=indexes,
    )
